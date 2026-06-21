from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aedt_agent.agent.mission import (
    ArtifactManifest,
    EngineeringConstraint,
    ErrorClass,
    EventRecord,
    JobAttemptRecord,
    JobAttemptStatus,
    JobError,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
)
from aedt_agent.agent.workers import InMemoryWorkerRegistry, WorkerContext, WorkerExecutionResult


class AgentRuntime:
    def __init__(
        self,
        store,
        registry: InMemoryWorkerRegistry | None = None,
        default_lease_seconds: int = 60,
        default_job_timeout_seconds: int = 300,
    ):
        self.store = store
        self.registry = registry or InMemoryWorkerRegistry()
        self.default_lease_seconds = default_lease_seconds
        self.default_job_timeout_seconds = default_job_timeout_seconds

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
        timeout_seconds: int | None = None,
        retry_limit: int = 1,
    ) -> JobRecord:
        if timeout_seconds is None:
            timeout_seconds = self.default_job_timeout_seconds
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
            result = self.registry.execute(
                leased_job,
                WorkerContext(worker_id),
                attempt_id=attempt.attempt_id,
                cancel_requested=lambda: (
                    self.get_mission(mission_id).state == MissionState.CANCELED
                ),
            )
            if (
                result.status == JobStatus.SUCCEEDED
                and self.get_mission(mission_id).state == MissionState.CANCELED
            ):
                result = WorkerExecutionResult(
                    job_id=result.job_id,
                    status=JobStatus.CANCELED,
                    output_payload={},
                    artifact_refs=result.artifact_refs,
                    error=JobError(
                        ErrorClass.CANCELED,
                        "mission was canceled before worker result was committed",
                        retryable=False,
                    ),
                    metadata={
                        **result.metadata,
                        "termination_reason": "mission_canceled_before_commit",
                    },
                )
            self._register_artifact_manifests(
                mission_id,
                job.job_id,
                result.artifact_refs,
            )
            if result.status == JobStatus.SUCCEEDED:
                self.store.complete_job(job.job_id, result.output_payload, result.artifact_refs)
                self.store.complete_job_attempt(
                    attempt.attempt_id,
                    JobAttemptStatus.SUCCEEDED,
                    retry_decision="none",
                    metadata=result.metadata,
                )
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
            elif result.status == JobStatus.CANCELED:
                assert result.error is not None
                self.store.cancel_job(job.job_id, result.error)
                self.store.complete_job_attempt(
                    attempt.attempt_id,
                    JobAttemptStatus.CANCELED,
                    result.error.to_json_dict(),
                    "canceled",
                    metadata=result.metadata,
                )
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
                    metadata=result.metadata,
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

    def recover_harness_attempts(
        self,
        mission_id: str,
        *,
        terminate_stale: bool = False,
        process_controller=None,
    ) -> dict:
        from aedt_agent.infrastructure.harness import (
            HarnessRecoveryClassification,
            HarnessRecoveryScanner,
        )

        harness = getattr(self.registry, "harness", None)
        if harness is None:
            raise ValueError("process harness is not configured")
        controller = process_controller or harness.process_controller
        scanner = HarnessRecoveryScanner(
            harness.workspace_policy.root,
            process_controller=controller,
            heartbeat_timeout_seconds=harness.heartbeat_timeout_seconds,
        )
        records = scanner.scan(mission_id)
        report = {
            "mission_id": mission_id,
            "records": [record.to_json_dict() for record in records],
            "completed_attempt_ids": [],
            "adopted_completed_attempt_ids": [],
            "active_attempt_ids": [],
            "stale_attempt_ids": [],
            "interrupted_attempt_ids": [],
            "invalid_workspaces": [],
            "terminated_pids": [],
            "requeued_job_ids": [],
        }
        for record in records:
            if record.classification == HarnessRecoveryClassification.COMPLETED:
                report["completed_attempt_ids"].append(record.attempt_id)
                self._recover_completed_attempt(record, report)
                continue
            if record.classification == HarnessRecoveryClassification.ACTIVE:
                report["active_attempt_ids"].append(record.attempt_id)
                continue
            if record.classification == HarnessRecoveryClassification.INVALID:
                report["invalid_workspaces"].append(record.workspace)
                continue
            if record.classification == HarnessRecoveryClassification.STALE:
                report["stale_attempt_ids"].append(record.attempt_id)
                if not terminate_stale or record.pid is None:
                    continue
                controller.terminate_pid_tree(
                    record.pid,
                    harness.termination_grace_seconds,
                )
                report["terminated_pids"].append(record.pid)
            self._recover_interrupted_attempt(record, report)
        return report

    def _recover_completed_attempt(self, record, report: dict) -> None:
        from aedt_agent.infrastructure.harness import HarnessResult, HarnessStatus

        try:
            attempt = self.store.get_job_attempt(record.attempt_id)
        except KeyError:
            return
        if attempt.status != JobAttemptStatus.RUNNING:
            return
        job = self.get_job(attempt.job_id)
        workspace = Path(record.workspace)
        result = HarnessResult.from_json_dict(
            json.loads((workspace / "result.json").read_text(encoding="utf-8"))
        )
        result.assert_identity(record.harness_run_id, job.job_id)
        protocol_artifacts = [
            str(path)
            for path in (
                workspace / "request.json",
                workspace / "result.json",
                workspace / "stdout.log",
                workspace / "stderr.log",
            )
            if path.exists()
        ]
        artifact_refs = list(dict.fromkeys([*result.artifact_refs, *protocol_artifacts]))
        metadata = {
            **result.metadata,
            "execution_mode": "local_process",
            "harness_run_id": result.harness_run_id,
            "harness_status": result.status.value,
            "workspace": record.workspace,
            "exit_code": result.exit_code,
            "termination_reason": result.termination_reason,
            "recovery_classification": "completed",
        }
        self.store.release_active_job_leases(job.job_id)
        mission_canceled = (
            self.get_mission(job.mission_id).state == MissionState.CANCELED
        )
        if result.status == HarnessStatus.SUCCEEDED and not mission_canceled:
            self._ensure_mission_ready_for_worker(job.mission_id)
            self.store.complete_job(job.job_id, result.output_payload, artifact_refs)
            self.store.complete_job_attempt(
                attempt.attempt_id,
                JobAttemptStatus.SUCCEEDED,
                retry_decision="none",
                metadata=metadata,
            )
            self._register_artifact_manifests(job.mission_id, job.job_id, artifact_refs)
            self.store.create_checkpoint(
                job.mission_id,
                job.job_id,
                artifact_refs,
                {"output": result.output_payload},
            )
            approval_required = result.output_payload.get("approval_required")
            if isinstance(approval_required, dict):
                from aedt_agent.agent.approvals import ApprovalService

                ApprovalService(self.store).request_approval(
                    job.mission_id,
                    str(approval_required.get("reason") or "approval_required"),
                    list(approval_required.get("options") or []),
                )
            else:
                self.store.update_mission_state(job.mission_id, MissionState.EVALUATING)
        else:
            harness_error = result.error
            if mission_canceled:
                error = JobError(
                    ErrorClass.CANCELED,
                    "mission was canceled before recovered worker result was committed",
                    retryable=False,
                )
                metadata["termination_reason"] = "mission_canceled_before_commit"
            else:
                error = JobError(
                    _harness_error_class(
                        harness_error.error_class if harness_error else "worker_crash"
                    ),
                    (
                        harness_error.message
                        if harness_error
                        else f"harness failed: {result.status.value}"
                    ),
                    harness_error.retryable if harness_error else True,
                    {} if harness_error is None else dict(harness_error.details),
                )
            if result.status == HarnessStatus.CANCELED or mission_canceled:
                self.store.cancel_job(job.job_id, error)
                self.store.complete_job_attempt(
                    attempt.attempt_id,
                    JobAttemptStatus.CANCELED,
                    error.to_json_dict(),
                    "canceled",
                    metadata=metadata,
                )
            else:
                self.store.fail_job(job.job_id, error)
                retry_available = (
                    error.retryable and attempt.attempt_number <= job.retry_limit
                )
                self.store.complete_job_attempt(
                    attempt.attempt_id,
                    JobAttemptStatus.FAILED,
                    error.to_json_dict(),
                    "retry_available" if retry_available else "no_retry",
                    metadata=metadata,
                )
                if retry_available:
                    self.store.requeue_failed_job(job.job_id)
                    report["requeued_job_ids"].append(job.job_id)
        report["adopted_completed_attempt_ids"].append(attempt.attempt_id)

    def _recover_interrupted_attempt(self, record, report: dict) -> None:
        from aedt_agent.infrastructure.harness import (
            HarnessError,
            HarnessResult,
            HarnessStatus,
        )

        try:
            attempt = self.store.get_job_attempt(record.attempt_id)
        except KeyError:
            return
        if attempt.status != JobAttemptStatus.RUNNING:
            return
        job = self.get_job(attempt.job_id)
        error = JobError(
            ErrorClass.WORKER_CRASH,
            "process harness attempt was interrupted before writing a result",
            retryable=True,
            details={
                "harness_run_id": record.harness_run_id,
                "workspace": record.workspace,
            },
        )
        workspace = Path(record.workspace)
        protocol_artifacts = [
            str(path)
            for path in (
                workspace / "request.json",
                workspace / "stdout.log",
                workspace / "stderr.log",
            )
            if path.exists()
        ]
        result = HarnessResult.create(
            harness_run_id=record.harness_run_id,
            job_id=job.job_id,
            status=HarnessStatus.INTERRUPTED,
            artifact_refs=protocol_artifacts,
            error=HarnessError(
                error_class=ErrorClass.WORKER_CRASH.value,
                message=error.message,
                retryable=True,
                details=dict(error.details),
            ),
            termination_reason="recovered_interrupted",
            metadata={
                "workspace": record.workspace,
                "recovery_classification": "interrupted",
            },
        )
        result_path = workspace / "result.json"
        _atomic_write_json(result_path, result.to_json_dict())
        protocol_artifacts.append(str(result_path))
        self._register_artifact_manifests(
            job.mission_id,
            job.job_id,
            protocol_artifacts,
        )
        self.store.release_active_job_leases(job.job_id)
        self.store.fail_job(job.job_id, error)
        retry_available = attempt.attempt_number <= job.retry_limit
        self.store.complete_job_attempt(
            attempt.attempt_id,
            JobAttemptStatus.FAILED,
            error.to_json_dict(),
            "retry_available" if retry_available else "no_retry",
            metadata={
                "harness_run_id": record.harness_run_id,
                "workspace": record.workspace,
                "recovery_classification": "interrupted",
            },
        )
        report["interrupted_attempt_ids"].append(attempt.attempt_id)
        if retry_available:
            self.store.requeue_failed_job(job.job_id)
            report["requeued_job_ids"].append(job.job_id)

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


def _atomic_write_json(path: Path, payload: dict) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


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


def _harness_error_class(value: str) -> ErrorClass:
    try:
        return ErrorClass(value)
    except ValueError:
        return ErrorClass.UNKNOWN
