from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from aedt_agent.agent.mission import (
    ErrorClass,
    JobError,
    JobRecord,
    JobStatus,
    utc_now_iso,
)
from aedt_agent.infrastructure.harness import (
    HarnessRequest,
    HarnessStatus,
    LocalProcessHarness,
)


WorkerFn = Callable[[JobRecord, "WorkerContext"], dict[str, Any]]


@dataclass(frozen=True)
class WorkerContext:
    worker_id: str
    workspace: str | None = None
    artifacts_dir: str | None = None


@dataclass(frozen=True)
class WorkerExecutionResult:
    job_id: str
    status: JobStatus
    output_payload: dict[str, Any]
    artifact_refs: list[str]
    error: JobError | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerRegistration:
    capability: str
    execution_mode: str
    handler: WorkerFn | None = None
    entrypoint: str = ""
    resource_classes: tuple[str, ...] = ("cpu",)
    allowed_env: tuple[str, ...] = ()

    def validate(self) -> "WorkerRegistration":
        if not self.capability.strip():
            raise ValueError("worker capability is required")
        if self.execution_mode not in {"in_process", "local_process"}:
            raise ValueError(f"unsupported worker execution_mode: {self.execution_mode}")
        if not self.resource_classes:
            raise ValueError("worker resource_classes must not be empty")
        unsupported = [
            resource_class
            for resource_class in self.resource_classes
            if resource_class not in {"cpu", "aedt", "license"}
        ]
        if unsupported:
            raise ValueError(
                f"unsupported worker resource_class: {unsupported[0]}"
            )
        if self.execution_mode == "in_process" and self.handler is None:
            raise ValueError("in_process worker requires handler")
        if self.execution_mode == "local_process" and not self.entrypoint.strip():
            raise ValueError("local_process worker requires entrypoint")
        return self


class InMemoryWorkerRegistry:
    def __init__(
        self,
        *,
        harness: LocalProcessHarness | None = None,
        heartbeat_interval_seconds: int = 5,
        default_allowed_env: tuple[str, ...] = (),
    ) -> None:
        self._registrations: dict[str, WorkerRegistration] = {}
        self.harness = harness
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.default_allowed_env = tuple(default_allowed_env)

    def register(self, capability: str, worker: WorkerFn) -> None:
        self._register(
            WorkerRegistration(
                capability=capability,
                execution_mode="in_process",
                handler=worker,
            )
        )

    def register_process(
        self,
        capability: str,
        entrypoint: str,
        *,
        resource_classes: tuple[str, ...] | list[str] | None = None,
        resource_class: str | None = None,
        allowed_env: tuple[str, ...] | None = None,
    ) -> None:
        if resource_classes is not None and resource_class is not None:
            raise ValueError(
                "provide resource_classes or resource_class, not both"
            )
        selected_resources = (
            (resource_class,)
            if resource_class is not None
            else tuple(resource_classes or ("cpu",))
        )
        self._register(
            WorkerRegistration(
                capability=capability,
                execution_mode="local_process",
                entrypoint=entrypoint,
                resource_classes=selected_resources,
                allowed_env=(
                    self.default_allowed_env
                    if allowed_env is None
                    else tuple(allowed_env)
                ),
            )
        )

    def _register(self, registration: WorkerRegistration) -> None:
        registration.validate()
        if registration.capability in self._registrations:
            raise ValueError(
                f"worker already registered for capability: {registration.capability}"
            )
        self._registrations[registration.capability] = registration

    def execute(
        self,
        job: JobRecord,
        context: WorkerContext,
        *,
        attempt_id: str | None = None,
        cancel_requested=None,
    ) -> WorkerExecutionResult:
        registration = self._registrations.get(job.capability)
        if registration is None:
            return WorkerExecutionResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                output_payload={},
                artifact_refs=[],
                error=JobError(
                    ErrorClass.INVALID_INPUT,
                    f"No worker registered for capability: {job.capability}",
                    False,
                ),
            )
        if registration.execution_mode == "local_process":
            return self._execute_process(
                registration,
                job,
                context,
                attempt_id,
                cancel_requested,
            )
        assert registration.handler is not None
        try:
            output = dict(registration.handler(job, context))
            artifact_refs = list(output.pop("artifact_refs", []))
            return WorkerExecutionResult(
                job.job_id,
                JobStatus.SUCCEEDED,
                output,
                artifact_refs,
                None,
                {"execution_mode": "in_process"},
            )
        except Exception as exc:
            return WorkerExecutionResult(
                job.job_id,
                JobStatus.FAILED,
                {},
                [],
                classify_worker_error(exc),
                {"execution_mode": "in_process"},
            )

    def _execute_process(
        self,
        registration: WorkerRegistration,
        job: JobRecord,
        context: WorkerContext,
        attempt_id: str | None,
        cancel_requested,
    ) -> WorkerExecutionResult:
        if self.harness is None:
            return _configuration_failure(
                job.job_id,
                "local_process worker requires a configured process harness",
            )
        if not attempt_id:
            return _configuration_failure(
                job.job_id,
                "local_process worker requires attempt_id",
            )
        harness_run_id = str(uuid4())
        workspace = self.harness.workspace_policy.create_attempt(
            job.mission_id,
            job.job_id,
            attempt_id,
        )
        request = HarnessRequest.create(
            harness_run_id=harness_run_id,
            mission_id=job.mission_id,
            job_id=job.job_id,
            attempt_id=attempt_id,
            worker_id=context.worker_id,
            capability=job.capability,
            entrypoint=registration.entrypoint,
            timeout_seconds=job.timeout_seconds,
            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
            input_payload=dict(job.input_payload),
            workspace=str(workspace.root),
        )
        result = self.harness.execute(
            request,
            allowed_env=registration.allowed_env,
            resource_classes=registration.resource_classes,
            cancel_requested=cancel_requested,
        )
        metadata = {
            **result.metadata,
            "execution_mode": "local_process",
            "harness_run_id": result.harness_run_id,
            "harness_status": result.status.value,
            "exit_code": result.exit_code,
            "termination_reason": result.termination_reason,
        }
        if result.status == HarnessStatus.SUCCEEDED:
            return WorkerExecutionResult(
                job.job_id,
                JobStatus.SUCCEEDED,
                result.output_payload,
                result.artifact_refs,
                None,
                metadata,
            )
        error = result.error
        job_error = JobError(
            _error_class(error.error_class if error else "worker_crash"),
            error.message if error else f"harness failed: {result.status.value}",
            error.retryable if error else True,
            {} if error is None else dict(error.details),
        )
        return WorkerExecutionResult(
            job.job_id,
            (
                JobStatus.CANCELED
                if result.status == HarnessStatus.CANCELED
                else JobStatus.FAILED
            ),
            {},
            result.artifact_refs,
            job_error,
            metadata,
        )


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


def _configuration_failure(job_id: str, message: str) -> WorkerExecutionResult:
    return WorkerExecutionResult(
        job_id,
        JobStatus.FAILED,
        {},
        [],
        JobError(ErrorClass.INVALID_INPUT, message, False),
    )


def _error_class(value: str) -> ErrorClass:
    try:
        return ErrorClass(value)
    except ValueError:
        return ErrorClass.UNKNOWN
