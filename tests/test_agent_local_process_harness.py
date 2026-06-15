from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.infrastructure.harness import (
    HarnessRequest,
    HarnessStatus,
    HarnessWorkspacePolicy,
    LocalProcessHarness,
    ResourceGate,
)


@pytest.fixture(autouse=True)
def _subprocess_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))


def _execute(
    tmp_path: Path,
    entrypoint: str,
    *,
    input_payload: dict | None = None,
):
    policy = HarnessWorkspacePolicy(tmp_path / "runs")
    workspace = policy.create_attempt("mission-1", "job-1", "attempt-1")
    request = HarnessRequest.create(
        harness_run_id="run-1",
        mission_id="mission-1",
        job_id="job-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        capability="fake.worker",
        entrypoint=entrypoint,
        timeout_seconds=10,
        heartbeat_interval_seconds=1,
        input_payload=input_payload or {},
        workspace=str(workspace.root),
    )
    harness = LocalProcessHarness(
        policy,
        resource_gate=ResourceGate(
            max_concurrent_cpu=2,
            max_concurrent_aedt=1,
            max_concurrent_license_jobs=1,
        ),
    )
    return harness.execute(request, allowed_env=(), resource_class="cpu")


def test_local_process_harness_captures_logs_and_result(tmp_path):
    result = _execute(
        tmp_path,
        "tests.fixtures.process_workers:logging_worker",
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.output_payload == {"worker_id": "worker-1"}
    assert "worker stdout" in Path(result.metadata["stdout_path"]).read_text(encoding="utf-8")
    assert "worker stderr" in Path(result.metadata["stderr_path"]).read_text(encoding="utf-8")


def test_local_process_harness_registers_protocol_files_as_artifacts(tmp_path):
    result = _execute(
        tmp_path,
        "tests.fixtures.process_workers:echo_worker",
        input_payload={"value": 2},
    )

    artifact_names = {Path(path).name for path in result.artifact_refs}
    assert {"request.json", "result.json", "stdout.log", "stderr.log"} <= artifact_names
    assert result.metadata["resource_class"] == "cpu"
    assert result.metadata["resource_wait_seconds"] >= 0


def test_local_process_harness_preserves_structured_worker_failure(tmp_path):
    result = _execute(
        tmp_path,
        "tests.fixtures.process_workers:failing_worker",
    )

    assert result.status == HarnessStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == "worker_crash"
    assert result.exit_code == 1


def test_local_process_harness_fails_closed_when_child_exits_without_result(tmp_path):
    result = _execute(
        tmp_path,
        "tests.fixtures.process_workers:abrupt_exit_worker",
    )

    assert result.status == HarnessStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == "worker_crash"
    assert result.exit_code == 7
    assert result.termination_reason == "missing_result"


def test_local_process_harness_rejects_corrupt_result(tmp_path):
    result = _execute(
        tmp_path,
        "tests.fixtures.process_workers:corrupt_result_worker",
    )

    assert result.status == HarnessStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == "worker_crash"
    assert result.termination_reason == "invalid_result"


def test_local_process_harness_rejects_wrong_result_identity(tmp_path):
    result = _execute(
        tmp_path,
        "tests.fixtures.process_workers:wrong_identity_worker",
    )

    assert result.status == HarnessStatus.FAILED
    assert result.error is not None
    assert "harness_run_id mismatch" in result.error.message
    assert result.termination_reason == "invalid_result"
