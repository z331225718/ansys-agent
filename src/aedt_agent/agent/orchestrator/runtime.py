from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aedt_agent.agent.mission import (
    ArtifactManifest,
    EngineeringConstraint,
    EventRecord,
    JobAttemptRecord,
    JobAttemptStatus,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
)
from aedt_agent.agent.workers import InMemoryWorkerRegistry, WorkerContext, WorkerExecutionResult


class AgentRuntime:
    def __init__(self, store, registry: InMemoryWorkerRegistry | None = None, default_lease_seconds: int = 60):
        self.store = store
        self.registry = registry or InMemoryWorkerRegistry()
        self.default_lease_seconds = default_lease_seconds

    def create_mission(
        self,
        user_goal: str,
        acceptance_criteria: list[dict],
        constraints: list[EngineeringConstraint],
        vision_required: bool = False,
    ) -> MissionRecord:
        mission = MissionRecord.create(str(uuid4()), user_goal, acceptance_criteria, constraints, vision_required)
        return self.store.create_mission(mission)

    def get_mission(self, mission_id: str) -> MissionRecord:
        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        return mission

    def create_job(
        self,
        mission_id: str,
        capability: str,
        idempotency_key: str,
        input_payload: dict,
        timeout_seconds: int = 300,
        retry_limit: int = 1,
    ) -> JobRecord:
        return self.store.create_job(mission_id, capability, idempotency_key, input_payload, timeout_seconds, retry_limit)

    def list_jobs(self, mission_id: str) -> list[JobRecord]:
        return self.store.list_jobs(mission_id)

    def get_job(self, job_id: str) -> JobRecord:
        return self.store.get_job(job_id)

    def list_events(self, mission_id: str) -> list[EventRecord]:
        return self.store.list_events(mission_id)

    def execute_next_job(self, mission_id: str, worker_id: str) -> WorkerExecutionResult:
        job = self.store.next_queued_job(mission_id)
        if job is None:
            raise ValueError(f"no queued job for mission: {mission_id}")
        return self.execute_job(job.job_id, worker_id)

    def execute_job(self, job_id: str, worker_id: str) -> WorkerExecutionResult:
        job = self.get_job(job_id)
        if job.status != JobStatus.QUEUED:
            raise ValueError(f"job is not queued: {job_id} ({job.status.value})")
        mission_id = job.mission_id
        self._ensure_mission_ready_for_worker(mission_id)
        lease = self.store.acquire_job_lease(job.job_id, worker_id, self.default_lease_seconds)
        leased_job = self.store.get_job(job.job_id)
        attempt_number = len(self.store.list_job_attempts(job.job_id)) + 1
        attempt = self.store.create_job_attempt(
            JobAttemptRecord.create(
                attempt_id=str(uuid4()),
                mission_id=mission_id,
                job_id=job.job_id,
                attempt_number=attempt_number,
                worker_id=worker_id,
            )
        )
        try:
            result = self.registry.execute(leased_job, WorkerContext(worker_id))
            if result.status == JobStatus.SUCCEEDED:
                self.store.complete_job(job.job_id, result.output_payload, result.artifact_refs)
                self.store.complete_job_attempt(attempt.attempt_id, JobAttemptStatus.SUCCEEDED, retry_decision="none")
                self._register_artifact_manifests(mission_id, job.job_id, result.artifact_refs)
                self.store.create_checkpoint(mission_id, job.job_id, result.artifact_refs, {"output": result.output_payload})
                approval_required = result.output_payload.get("approval_required")
                if isinstance(approval_required, dict):
                    from aedt_agent.agent.approvals import ApprovalService

                    ApprovalService(self.store).request_approval(
                        mission_id,
                        str(approval_required.get("reason") or "approval_required"),
                        list(approval_required.get("options") or []),
                    )
                else:
                    self.store.update_mission_state(mission_id, MissionState.EVALUATING)
            else:
                assert result.error is not None
                self.store.fail_job(job.job_id, result.error)
                retry_available = result.error.retryable and attempt_number <= leased_job.retry_limit
                retry_decision = "retry_available" if retry_available else "no_retry"
                self.store.complete_job_attempt(
                    attempt.attempt_id,
                    JobAttemptStatus.FAILED,
                    result.error.to_json_dict(),
                    retry_decision,
                )
                if retry_available:
                    self.store.requeue_failed_job(job.job_id)
        finally:
            self.store.release_job_lease(lease.lease_id)
        return result

    def _ensure_mission_ready_for_worker(self, mission_id: str) -> None:
        mission = self.get_mission(mission_id)
        if mission.state == MissionState.CREATED:
            self.store.update_mission_state(mission_id, MissionState.PLANNING)
            self.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)
        elif mission.state in {MissionState.PLANNING, MissionState.EVALUATING}:
            self.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)
        elif mission.state != MissionState.WAITING_WORKER:
            raise ValueError(f"mission is not ready for worker execution: {mission.state.value}")

    def recover_expired_leases(self, now: datetime | None = None) -> list[str]:
        return self.store.recover_expired_leases(now or datetime.now(UTC))

    def _register_artifact_manifests(self, mission_id: str, job_id: str, artifact_refs: list[str]) -> None:
        for artifact_ref in artifact_refs:
            path = Path(artifact_ref)
            if path.exists() and path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                size_bytes = path.stat().st_size
                metadata = {"exists": True}
            else:
                digest = ""
                size_bytes = 0
                metadata = {"exists": False}
            self.store.create_artifact_manifest(
                ArtifactManifest.create(
                    artifact_id=str(uuid4()),
                    mission_id=mission_id,
                    producer_kind="job",
                    producer_id=job_id,
                    path=artifact_ref,
                    kind=_artifact_kind(path),
                    sha256=digest,
                    size_bytes=size_bytes,
                    metadata=metadata,
                )
            )


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".aedt":
        return "aedt_project"
    if suffix in {".s1p", ".s2p", ".s3p", ".s4p", ".snp", ".ts"}:
        return "touchstone"
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    return "artifact"
