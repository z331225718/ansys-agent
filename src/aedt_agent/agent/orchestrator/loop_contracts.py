from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from aedt_agent.agent.mission import utc_now_iso
from aedt_agent.agent.policies.execution_profile import ExecutionProfile


class MissionLoopStatus(StrEnum):
    ACTIVE = "active"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class LoopDecisionType(StrEnum):
    EXECUTE_JOB = "execute_job"
    RETRY_JOB = "retry_job"
    WAITING_APPROVAL = "waiting_approval"
    CONTINUE = "continue"
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STOPPED_NO_IMPROVEMENT = "stopped_no_improvement"
    STOPPED_DUPLICATE_ACTION = "stopped_duplicate_action"
    IDLE = "idle"


@dataclass(frozen=True)
class LoopDecision:
    decision: LoopDecisionType
    reason: str
    usage: dict[str, Any]
    limits: dict[str, Any]
    job_id: str | None = None
    retry_after_seconds: int | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "usage": self.usage,
            "limits": self.limits,
            "job_id": self.job_id,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass(frozen=True)
class MissionLoopRecord:
    loop_id: str
    mission_id: str
    profile: ExecutionProfile
    status: MissionLoopStatus
    iteration_count: int
    job_attempt_count: int
    evidence_query_calls: int
    evidence_tokens: int
    duplicate_action_count: int
    consecutive_no_improvement: int
    started_at: str
    updated_at: str
    completed_at: str | None
    last_decision: LoopDecisionType | None
    last_reason: str | None
    last_job_id: str | None
    retry_not_before: str | None = None

    @classmethod
    def create(
        cls,
        loop_id: str,
        mission_id: str,
        profile: ExecutionProfile,
    ) -> "MissionLoopRecord":
        now = utc_now_iso()
        return cls(
            loop_id=loop_id,
            mission_id=mission_id,
            profile=profile,
            status=MissionLoopStatus.ACTIVE,
            iteration_count=0,
            job_attempt_count=0,
            evidence_query_calls=0,
            evidence_tokens=0,
            duplicate_action_count=0,
            consecutive_no_improvement=0,
            started_at=now,
            updated_at=now,
            completed_at=None,
            last_decision=None,
            last_reason=None,
            last_job_id=None,
        )

    def with_decision(
        self,
        decision: LoopDecision,
        *,
        status: MissionLoopStatus | None = None,
        iteration_increment: int = 0,
        job_attempt_increment: int = 0,
        evidence_query_increment: int = 0,
        evidence_token_increment: int = 0,
        duplicate_action_increment: int = 0,
        consecutive_no_improvement: int | None = None,
        retry_not_before: str | None = None,
    ) -> "MissionLoopRecord":
        target_status = status or self.status
        now = utc_now_iso()
        terminal = target_status in {
            MissionLoopStatus.COMPLETED,
            MissionLoopStatus.FAILED,
            MissionLoopStatus.CANCELED,
        }
        return replace(
            self,
            status=target_status,
            iteration_count=self.iteration_count + iteration_increment,
            job_attempt_count=self.job_attempt_count + job_attempt_increment,
            evidence_query_calls=self.evidence_query_calls + evidence_query_increment,
            evidence_tokens=self.evidence_tokens + evidence_token_increment,
            duplicate_action_count=self.duplicate_action_count + duplicate_action_increment,
            consecutive_no_improvement=(
                self.consecutive_no_improvement
                if consecutive_no_improvement is None
                else consecutive_no_improvement
            ),
            updated_at=now,
            completed_at=now if terminal else None,
            last_decision=decision.decision,
            last_reason=decision.reason,
            last_job_id=decision.job_id,
            retry_not_before=retry_not_before,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "loop_id": self.loop_id,
            "mission_id": self.mission_id,
            "profile": self.profile.to_json_dict(),
            "status": self.status.value,
            "iteration_count": self.iteration_count,
            "job_attempt_count": self.job_attempt_count,
            "evidence_query_calls": self.evidence_query_calls,
            "evidence_tokens": self.evidence_tokens,
            "duplicate_action_count": self.duplicate_action_count,
            "consecutive_no_improvement": self.consecutive_no_improvement,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "last_decision": None if self.last_decision is None else self.last_decision.value,
            "last_reason": self.last_reason,
            "last_job_id": self.last_job_id,
            "retry_not_before": self.retry_not_before,
        }
