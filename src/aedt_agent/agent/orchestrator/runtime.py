from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from aedt_agent.agent.mission import EngineeringConstraint, EventRecord, JobRecord, JobStatus, MissionRecord
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
        lease = self.store.acquire_job_lease(job.job_id, worker_id, self.default_lease_seconds)
        leased_job = self.store.get_job(job.job_id)
        result = self.registry.execute(leased_job, WorkerContext(worker_id))
        if result.status == JobStatus.SUCCEEDED:
            self.store.complete_job(job.job_id, result.output_payload, result.artifact_refs)
            self.store.create_checkpoint(mission_id, job.job_id, result.artifact_refs, {"output": result.output_payload})
        else:
            assert result.error is not None
            self.store.fail_job(job.job_id, result.error)
        self.store.release_job_lease(lease.lease_id)
        return result

    def recover_expired_leases(self, now: datetime | None = None) -> list[str]:
        return self.store.recover_expired_leases(now or datetime.now(UTC))
