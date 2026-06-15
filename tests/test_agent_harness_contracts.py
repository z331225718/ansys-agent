from __future__ import annotations

import pytest

from aedt_agent.infrastructure.harness import (
    HARNESS_PROTOCOL_VERSION,
    HarnessError,
    HarnessProtocolError,
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
)


def _request_payload() -> dict:
    return {
        "protocol_version": HARNESS_PROTOCOL_VERSION,
        "harness_run_id": "run-1",
        "mission_id": "mission-1",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "worker_id": "worker-1",
        "capability": "fake.echo",
        "entrypoint": "tests.fixtures.process_workers:echo_worker",
        "timeout_seconds": 10,
        "heartbeat_interval_seconds": 1,
        "input_payload": {"value": 2},
        "workspace": "C:/tmp/attempt-1",
    }


def test_harness_request_round_trips_json():
    request = HarnessRequest.from_json_dict(_request_payload())

    assert HarnessRequest.from_json_dict(request.to_json_dict()) == request


def test_harness_result_round_trips_structured_error_and_metadata():
    result = HarnessResult.create(
        harness_run_id="run-1",
        job_id="job-1",
        status=HarnessStatus.FAILED,
        error=HarnessError(
            error_class="worker_crash",
            message="worker exited",
            retryable=True,
            details={"exit_code": 3},
        ),
        exit_code=3,
        termination_reason="child_exit",
        metadata={"workspace": "C:/tmp/attempt-1"},
    )

    assert HarnessResult.from_json_dict(result.to_json_dict()) == result


def test_harness_request_rejects_wrong_protocol_version():
    payload = _request_payload()
    payload["protocol_version"] = 99

    with pytest.raises(HarnessProtocolError, match="protocol_version"):
        HarnessRequest.from_json_dict(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("entrypoint", "", "entrypoint"),
        ("timeout_seconds", 0, "timeout_seconds"),
        ("heartbeat_interval_seconds", -1, "heartbeat_interval_seconds"),
    ],
)
def test_harness_request_rejects_invalid_required_values(field, value, message):
    payload = _request_payload()
    payload[field] = value

    with pytest.raises(HarnessProtocolError, match=message):
        HarnessRequest.from_json_dict(payload)


def test_harness_result_rejects_invalid_status():
    payload = HarnessResult.create(
        harness_run_id="run-1",
        job_id="job-1",
        status=HarnessStatus.SUCCEEDED,
    ).to_json_dict()
    payload["status"] = "maybe"

    with pytest.raises(HarnessProtocolError, match="status"):
        HarnessResult.from_json_dict(payload)


def test_harness_result_assert_identity_rejects_wrong_run_or_job():
    result = HarnessResult.create(
        harness_run_id="run-1",
        job_id="job-1",
        status=HarnessStatus.SUCCEEDED,
    )

    with pytest.raises(HarnessProtocolError, match="harness_run_id"):
        result.assert_identity("run-2", "job-1")
    with pytest.raises(HarnessProtocolError, match="job_id"):
        result.assert_identity("run-1", "job-2")
