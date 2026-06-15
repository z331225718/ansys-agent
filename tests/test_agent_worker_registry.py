from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.mission import ErrorClass, JobRecord, JobStatus
from aedt_agent.agent.workers import (
    InMemoryWorkerRegistry,
    WorkerContext,
    WorkerRegistration,
    classify_worker_error,
)
from aedt_agent.infrastructure.harness import (
    HarnessResult,
    HarnessStatus,
    HarnessWorkspacePolicy,
    LocalProcessHarness,
    ResourceGate,
)


def _job(capability: str = "fake.echo") -> JobRecord:
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=capability,
        idempotency_key="k1",
        input_payload={"value": 3},
        timeout_seconds=30,
        retry_limit=1,
    )


def test_worker_registry_executes_registered_worker():
    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", lambda job, context: {"value": job.input_payload["value"], "worker": context.worker_id})

    result = registry.execute(_job(), WorkerContext(worker_id="worker-1"))

    assert result.status == JobStatus.SUCCEEDED
    assert result.output_payload == {"value": 3, "worker": "worker-1"}
    assert result.error is None


def test_in_process_worker_context_has_no_workspace():
    seen = {}

    def worker(job, context):
        seen["workspace"] = context.workspace
        seen["artifacts_dir"] = context.artifacts_dir
        return {}

    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", worker)
    registry.execute(_job(), WorkerContext("worker-1"))

    assert seen == {"workspace": None, "artifacts_dir": None}


def test_worker_registry_rejects_unknown_capability():
    registry = InMemoryWorkerRegistry()

    result = registry.execute(_job("missing.capability"), WorkerContext(worker_id="worker-1"))

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == ErrorClass.INVALID_INPUT


def test_worker_errors_are_classified_without_llm_authority():
    assert classify_worker_error(TimeoutError("solver timed out")).error_class == ErrorClass.TIMEOUT
    assert classify_worker_error(RuntimeError("license unavailable")).error_class == ErrorClass.LICENSE_UNAVAILABLE
    assert classify_worker_error(ValueError("bad input")).error_class == ErrorClass.INVALID_INPUT
    assert classify_worker_error(Exception("boom")).error_class == ErrorClass.WORKER_CRASH


def test_duplicate_registration_is_rejected():
    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", lambda job, context: {})

    with pytest.raises(ValueError):
        registry.register("fake.echo", lambda job, context: {})


def test_registry_routes_local_process_registration_to_harness(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    harness = LocalProcessHarness(
        HarnessWorkspacePolicy(tmp_path / "runs"),
        resource_gate=ResourceGate(
            max_concurrent_cpu=1,
            max_concurrent_aedt=1,
            max_concurrent_license_jobs=1,
        ),
    )
    registry = InMemoryWorkerRegistry(harness=harness, heartbeat_interval_seconds=1)
    registry.register_process(
        "fake.echo",
        "tests.fixtures.process_workers:echo_worker",
        resource_class="cpu",
        allowed_env=("PYTHONPATH",),
    )

    result = registry.execute(
        _job(),
        WorkerContext(worker_id="worker-1"),
        attempt_id="attempt-1",
    )

    assert result.status == JobStatus.SUCCEEDED
    assert result.output_payload == {"value": 4}
    assert result.metadata["harness_run_id"]
    assert result.metadata["workspace"]


def test_registry_uses_default_allowed_environment_for_process_worker():
    class RecordingHarness:
        def __init__(self):
            self.allowed_env = None
            self.workspace_policy = type(
                "WorkspacePolicy",
                (),
                {
                    "create_attempt": staticmethod(
                        lambda mission_id, job_id, attempt_id: type(
                            "Workspace",
                            (),
                            {"root": Path("workspace")},
                        )()
                    )
                },
            )()

        def execute(
            self,
            request,
            *,
            allowed_env,
            resource_classes,
            cancel_requested,
        ):
            self.allowed_env = tuple(allowed_env)
            return HarnessResult.create(
                harness_run_id=request.harness_run_id,
                job_id=request.job_id,
                status=HarnessStatus.SUCCEEDED,
            )

    harness = RecordingHarness()
    registry = InMemoryWorkerRegistry(
        harness=harness,
        default_allowed_env=("PYTHONPATH", "AWP_ROOT261"),
    )
    registry.register_process(
        "fake.echo",
        "tests.fixtures.process_workers:echo_worker",
    )

    result = registry.execute(
        _job(),
        WorkerContext(worker_id="worker-1"),
        attempt_id="attempt-default-env",
    )

    assert result.status == JobStatus.SUCCEEDED
    assert harness.allowed_env == ("PYTHONPATH", "AWP_ROOT261")


def test_process_registration_accepts_composite_resources():
    registration = WorkerRegistration(
        capability="brd.local_cut.solve",
        execution_mode="local_process",
        entrypoint=(
            "aedt_agent.agent.workers.brd_real_solve:"
            "run_brd_real_solve_worker"
        ),
        resource_classes=("license", "aedt"),
    )

    assert registration.validate().resource_classes == ("license", "aedt")


@pytest.mark.parametrize(
    "registration",
    [
        WorkerRegistration("fake", "in_process"),
        WorkerRegistration("fake", "local_process"),
        WorkerRegistration("fake", "unknown", handler=lambda job, context: {}),
        WorkerRegistration(
            "fake",
            "in_process",
            handler=lambda job, context: {},
            resource_classes=("gpu",),
        ),
    ],
)
def test_worker_registration_rejects_incomplete_or_unsafe_modes(registration):
    with pytest.raises(ValueError):
        registration.validate()
