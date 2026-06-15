from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from aedt_agent.agent.mission import JobStatus, MissionState
from aedt_agent.agent.orchestrator.loop_contracts import (
    LoopDecision,
    LoopDecisionType,
    MissionLoopRecord,
    MissionLoopStatus,
)
from aedt_agent.agent.policies import ExecutionProfile
from aedt_agent.agent.policies.mission_budget import evaluate_mission_budget, mission_budget_limits


class MissionLoopController:
    def __init__(self, runtime, profile: ExecutionProfile | None = None):
        self.runtime = runtime
        self.store = runtime.store
        self.profile = profile or ExecutionProfile.safe_recorded()

    def get_or_create_loop(self, mission_id: str) -> MissionLoopRecord:
        self.runtime.get_mission(mission_id)
        existing = self.store.get_mission_loop(mission_id)
        if existing is not None:
            if existing.profile != self.profile and self.profile != ExecutionProfile.safe_recorded():
                raise ValueError("mission loop already exists with a different execution profile")
            return existing
        return self.store.create_mission_loop(
            MissionLoopRecord.create(str(uuid4()), mission_id, self.profile)
        )

    def status(self, mission_id: str) -> dict[str, Any]:
        loop = self.get_or_create_loop(mission_id)
        loop = self._sync_usage(loop)
        return {
            "mission": self.runtime.get_mission(mission_id).to_json_dict(),
            "loop": loop.to_json_dict(),
            "usage": self._usage(loop),
            "limits": mission_budget_limits(loop),
            "jobs": [job.to_json_dict() for job in self.runtime.list_jobs(mission_id)],
        }

    def record_duplicate_action(self, mission_id: str, *, digest: str) -> MissionLoopRecord:
        loop = self._sync_usage(self.get_or_create_loop(mission_id))
        counted = replace(loop, duplicate_action_count=loop.duplicate_action_count + 1)
        decision = LoopDecision(
            LoopDecisionType.CONTINUE,
            f"duplicate action recorded: {digest}",
            self._usage(counted),
            mission_budget_limits(counted),
        )
        return self.store.update_mission_loop(
            counted.with_decision(decision, status=MissionLoopStatus.ACTIVE)
        )

    def advance(self, mission_id: str, *, worker_id: str = "mission-loop") -> LoopDecision:
        mission = self.runtime.get_mission(mission_id)
        loop = self._sync_usage(self.get_or_create_loop(mission_id))

        if mission.state in {MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELED}:
            return self._terminal_mission_decision(mission, loop)
        if mission.state == MissionState.WAITING_APPROVAL:
            decision = LoopDecision(
                LoopDecisionType.WAITING_APPROVAL,
                "mission is waiting for approval",
                self._usage(loop),
                mission_budget_limits(loop),
            )
            self.store.update_mission_loop(
                loop.with_decision(decision, status=MissionLoopStatus.WAITING_APPROVAL)
            )
            return decision
        if self._retry_backoff_active(loop):
            decision = LoopDecision(
                LoopDecisionType.IDLE,
                "retry backoff is active",
                self._usage(loop),
                mission_budget_limits(loop),
                job_id=loop.last_job_id,
            )
            self.store.update_mission_loop(loop.with_decision(decision))
            return decision

        budget_decision = evaluate_mission_budget(loop, self._usage(loop))
        if budget_decision is not None:
            return self._terminate(loop, budget_decision)

        jobs = self.runtime.list_jobs(mission_id)
        queued = [job for job in jobs if job.status == JobStatus.QUEUED]
        if not queued:
            if jobs and all(job.status == JobStatus.SUCCEEDED for job in jobs):
                return self._complete(loop, "all jobs succeeded")
            if any(job.status == JobStatus.FAILED for job in jobs):
                decision = LoopDecision(
                    LoopDecisionType.FAILED,
                    "mission has a failed job with no retry available",
                    self._usage(loop),
                    mission_budget_limits(loop),
                )
                return self._terminate(loop, decision, code="job_failed")
            reason = "mission has no jobs" if not jobs else "mission has no ready jobs"
            decision = LoopDecision(
                LoopDecisionType.IDLE,
                reason,
                self._usage(loop),
                mission_budget_limits(loop),
            )
            self.store.update_mission_loop(loop.with_decision(decision, status=MissionLoopStatus.ACTIVE))
            return decision

        job = queued[0]
        if self._requires_real_aedt(job) and not loop.profile.allow_real_aedt:
            decision = LoopDecision(
                LoopDecisionType.FAILED,
                "real AEDT execution is disabled by the execution profile",
                self._usage(loop),
                mission_budget_limits(loop),
                job_id=job.job_id,
            )
            return self._terminate(loop, decision, code="real_aedt_disabled")

        result = self.runtime.execute_next_job(mission_id, worker_id)
        loop = self._sync_usage(loop)

        if result.status == JobStatus.FAILED:
            current_job = self.runtime.get_job(job.job_id)
            if current_job.status == JobStatus.QUEUED:
                post_budget_decision = evaluate_mission_budget(loop, self._usage(loop))
                if post_budget_decision is not None:
                    return self._terminate(
                        loop,
                        replace(post_budget_decision, job_id=job.job_id),
                        iteration_increment=1,
                    )
                attempts = self.store.list_job_attempts(job.job_id)
                delay = self._retry_delay(loop.profile, len(attempts))
                retry_not_before = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
                decision = LoopDecision(
                    LoopDecisionType.RETRY_JOB,
                    result.error.message if result.error is not None else "job retry available",
                    self._usage(loop),
                    mission_budget_limits(loop),
                    job_id=job.job_id,
                    retry_after_seconds=delay,
                )
                self.store.update_mission_loop(
                    loop.with_decision(
                        decision,
                        status=MissionLoopStatus.ACTIVE,
                        iteration_increment=1,
                        retry_not_before=retry_not_before,
                    )
                )
                return decision
            decision = LoopDecision(
                LoopDecisionType.FAILED,
                result.error.message if result.error is not None else "job failed",
                self._usage(loop),
                mission_budget_limits(loop),
                job_id=job.job_id,
            )
            return self._terminate(loop, decision, code="job_failed", iteration_increment=1)

        mission = self.runtime.get_mission(mission_id)
        if mission.state == MissionState.WAITING_APPROVAL:
            decision = LoopDecision(
                LoopDecisionType.WAITING_APPROVAL,
                "worker requested approval",
                self._usage(loop),
                mission_budget_limits(loop),
                job_id=job.job_id,
            )
            self.store.update_mission_loop(
                loop.with_decision(
                    decision,
                    status=MissionLoopStatus.WAITING_APPROVAL,
                    iteration_increment=1,
                )
            )
            return decision

        remaining = [item for item in self.runtime.list_jobs(mission_id) if item.status == JobStatus.QUEUED]
        if remaining:
            post_budget_decision = evaluate_mission_budget(loop, self._usage(loop))
            if post_budget_decision is not None:
                return self._terminate(
                    loop,
                    replace(post_budget_decision, job_id=job.job_id),
                    iteration_increment=1,
                )
            self._move_to_waiting_worker(mission_id)
            decision = LoopDecision(
                LoopDecisionType.CONTINUE,
                "job succeeded and queued work remains",
                self._usage(loop),
                mission_budget_limits(loop),
                job_id=job.job_id,
            )
            self.store.update_mission_loop(
                loop.with_decision(decision, status=MissionLoopStatus.ACTIVE, iteration_increment=1)
            )
            return decision
        return self._complete(loop, "all jobs succeeded", job_id=job.job_id, iteration_increment=1)

    def _sync_usage(self, loop: MissionLoopRecord) -> MissionLoopRecord:
        jobs = self.runtime.list_jobs(loop.mission_id)
        attempt_count = sum(len(self.store.list_job_attempts(job.job_id)) for job in jobs)
        evidence = self.store.list_evidence_packages(loop.mission_id)
        evidence_query_calls = sum(
            int(package.metadata.get("query_calls", 0))
            for package in evidence
            if isinstance(package.metadata.get("query_calls", 0), int)
        )
        evidence_tokens = sum(
            int(package.token_budget.get("summary_tokens", package.token_budget.get("used_tokens", 0)))
            for package in evidence
            if isinstance(package.token_budget.get("summary_tokens", package.token_budget.get("used_tokens", 0)), int)
        )
        no_improvement = self._consecutive_no_improvement(loop.mission_id)
        synced = replace(
            loop,
            job_attempt_count=attempt_count,
            evidence_query_calls=evidence_query_calls,
            evidence_tokens=evidence_tokens,
            consecutive_no_improvement=no_improvement,
        )
        if synced != loop:
            return self.store.update_mission_loop(synced)
        return loop

    def _consecutive_no_improvement(self, mission_id: str) -> int:
        count = 0
        for action in reversed(self.store.list_actions(mission_id)):
            status = None if action.comparison is None else action.comparison.get("status")
            if status == "improved":
                break
            if status in {"regressed", "unchanged", "mixed"}:
                count += 1
        return count

    @staticmethod
    def _usage(loop: MissionLoopRecord) -> dict[str, Any]:
        return {
            "iterations": loop.iteration_count,
            "job_attempts": loop.job_attempt_count,
            "evidence_query_calls": loop.evidence_query_calls,
            "evidence_tokens": loop.evidence_tokens,
            "consecutive_no_improvement": loop.consecutive_no_improvement,
            "duplicate_actions": loop.duplicate_action_count,
        }

    @staticmethod
    def _retry_delay(profile: ExecutionProfile, attempt_number: int) -> int:
        index = min(max(attempt_number - 1, 0), len(profile.retry_backoff_seconds) - 1)
        return profile.retry_backoff_seconds[index]

    @staticmethod
    def _retry_backoff_active(loop: MissionLoopRecord) -> bool:
        if loop.retry_not_before is None:
            return False
        return datetime.now(UTC) < datetime.fromisoformat(loop.retry_not_before)

    @staticmethod
    def _requires_real_aedt(job) -> bool:
        adapter_mode = str(job.input_payload.get("adapter_mode", "")).lower()
        return (
            adapter_mode in {"real_build", "real_aedt"}
            or job.capability == "brd.local_cut.solve"
            or ".real" in job.capability
        )

    def _move_to_waiting_worker(self, mission_id: str) -> None:
        mission = self.runtime.get_mission(mission_id)
        if mission.state == MissionState.CREATED:
            self.store.update_mission_state(mission_id, MissionState.PLANNING)
            self.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)
        elif mission.state in {MissionState.PLANNING, MissionState.EVALUATING}:
            self.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)

    def _complete(
        self,
        loop: MissionLoopRecord,
        reason: str,
        *,
        job_id: str | None = None,
        iteration_increment: int = 0,
    ) -> LoopDecision:
        mission = self.runtime.get_mission(loop.mission_id)
        if mission.state == MissionState.CREATED:
            self.store.update_mission_state(loop.mission_id, MissionState.PLANNING)
            self.store.update_mission_state(loop.mission_id, MissionState.WAITING_WORKER)
            self.store.update_mission_state(loop.mission_id, MissionState.EVALUATING)
        elif mission.state == MissionState.WAITING_WORKER:
            self.store.update_mission_state(loop.mission_id, MissionState.EVALUATING)
        self.store.update_mission_state(loop.mission_id, MissionState.COMPLETED)
        decision = LoopDecision(
            LoopDecisionType.COMPLETED,
            reason,
            self._usage(loop),
            mission_budget_limits(loop),
            job_id=job_id,
        )
        updated_loop = self.store.update_mission_loop(
            loop.with_decision(
                decision,
                status=MissionLoopStatus.COMPLETED,
                iteration_increment=iteration_increment,
            )
        )
        self.store.set_mission_final_outcome(
            loop.mission_id,
            self._outcome("completed", decision, updated_loop),
        )
        return decision

    def _terminate(
        self,
        loop: MissionLoopRecord,
        decision: LoopDecision,
        *,
        code: str | None = None,
        iteration_increment: int = 0,
    ) -> LoopDecision:
        mission = self.runtime.get_mission(loop.mission_id)
        if mission.state == MissionState.CREATED:
            self.store.update_mission_state(loop.mission_id, MissionState.PLANNING)
        self.store.update_mission_state(loop.mission_id, MissionState.FAILED)
        updated_loop = self.store.update_mission_loop(
            loop.with_decision(
                decision,
                status=MissionLoopStatus.FAILED,
                iteration_increment=iteration_increment,
            )
        )
        self.store.set_mission_final_outcome(
            loop.mission_id,
            self._outcome(code or decision.decision.value, decision, updated_loop),
        )
        return decision

    @staticmethod
    def _outcome(code: str, decision: LoopDecision, loop: MissionLoopRecord) -> dict[str, Any]:
        return {
            "code": code,
            "reason": decision.reason,
            "decision": decision.decision.value,
            "usage": MissionLoopController._usage(loop),
            "limits": mission_budget_limits(loop),
            "last_job_id": decision.job_id,
        }

    @staticmethod
    def _terminal_mission_decision(mission, loop: MissionLoopRecord) -> LoopDecision:
        if mission.state == MissionState.COMPLETED:
            decision_type = LoopDecisionType.COMPLETED
        else:
            decision_type = LoopDecisionType.FAILED
        outcome = mission.final_outcome or {}
        return LoopDecision(
            decision_type,
            str(outcome.get("reason") or f"mission is {mission.state.value}"),
            MissionLoopController._usage(loop),
            mission_budget_limits(loop),
            job_id=outcome.get("last_job_id"),
        )
