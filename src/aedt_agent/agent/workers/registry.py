from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aedt_agent.agent.mission import ErrorClass, JobError, JobRecord, JobStatus, utc_now_iso


WorkerFn = Callable[[JobRecord, "WorkerContext"], dict[str, Any]]


@dataclass(frozen=True)
class WorkerContext:
    worker_id: str


@dataclass(frozen=True)
class WorkerExecutionResult:
    job_id: str
    status: JobStatus
    output_payload: dict[str, Any]
    artifact_refs: list[str]
    error: JobError | None


class InMemoryWorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerFn] = {}

    def register(self, capability: str, worker: WorkerFn) -> None:
        if capability in self._workers:
            raise ValueError(f"worker already registered for capability: {capability}")
        self._workers[capability] = worker

    def execute(self, job: JobRecord, context: WorkerContext) -> WorkerExecutionResult:
        worker = self._workers.get(job.capability)
        if worker is None:
            return WorkerExecutionResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                output_payload={},
                artifact_refs=[],
                error=JobError(ErrorClass.INVALID_INPUT, f"No worker registered for capability: {job.capability}", False),
            )
        try:
            output = worker(job, context)
            artifact_refs = list(output.pop("artifact_refs", [])) if "artifact_refs" in output else []
            return WorkerExecutionResult(job.job_id, JobStatus.SUCCEEDED, output, artifact_refs, None)
        except Exception as exc:
            return WorkerExecutionResult(job.job_id, JobStatus.FAILED, {}, [], classify_worker_error(exc))


def classify_worker_error(error: Exception) -> JobError:
    message = str(error)
    lowered = message.lower()
    if isinstance(error, TimeoutError):
        return JobError(ErrorClass.TIMEOUT, message, retryable=True)
    if "license" in lowered and ("unavailable" in lowered or "denied" in lowered):
        return JobError(ErrorClass.LICENSE_UNAVAILABLE, message, retryable=True)
    if isinstance(error, ValueError):
        return JobError(ErrorClass.INVALID_INPUT, message, retryable=False)
    return JobError(
        ErrorClass.WORKER_CRASH,
        message,
        retryable=True,
        details={"error_type": type(error).__name__, "observed_at": utc_now_iso()},
    )
