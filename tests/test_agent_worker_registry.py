from __future__ import annotations

import pytest

from aedt_agent.agent.mission import ErrorClass, JobRecord, JobStatus
from aedt_agent.agent.workers import InMemoryWorkerRegistry, WorkerContext, classify_worker_error


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
