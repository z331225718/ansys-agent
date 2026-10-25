from __future__ import annotations

import json
from pathlib import Path

from aedt_agent.infrastructure.harness import HarnessRequest, HarnessResult, HarnessStatus
from aedt_agent.infrastructure.harness import child_main


def _request(tmp_path: Path, entrypoint: str, input_payload: dict | None = None) -> HarnessRequest:
    return HarnessRequest.create(
        harness_run_id="run-1",
        mission_id="mission-1",
        job_id="job-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        capability="fake.echo",
        entrypoint=entrypoint,
        timeout_seconds=10,
        heartbeat_interval_seconds=1,
        input_payload=input_payload or {"value": 2},
        workspace=str(tmp_path),
    )


def _write_request(tmp_path: Path, request: HarnessRequest) -> Path:
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(request.to_json_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    return request_path


def _read_result(tmp_path: Path) -> HarnessResult:
    return HarnessResult.from_json_dict(
        json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    )


def test_child_main_executes_entrypoint_and_writes_atomic_result(tmp_path):
    request = _request(tmp_path, "tests.fixtures.process_workers:echo_worker")

    exit_code = child_main.run(_write_request(tmp_path, request))

    result = _read_result(tmp_path)
    assert exit_code == 0
    assert result.status == HarnessStatus.SUCCEEDED
    assert result.output_payload == {"value": 3}
    assert not (tmp_path / "result.json.tmp").exists()


def test_child_main_normalizes_worker_artifact_refs(tmp_path):
    artifact = tmp_path / "artifacts/output.txt"
    artifact.parent.mkdir()
    request = _request(
        tmp_path,
        "tests.fixtures.process_workers:artifact_worker",
        {"artifact_path": str(artifact)},
    )

    child_main.run(_write_request(tmp_path, request))

    result = _read_result(tmp_path)
    assert result.output_payload == {"value": 1}
    assert result.artifact_refs == [str(artifact)]


def test_child_main_injects_verified_workspace_into_worker_context(tmp_path):
    request = _request(
        tmp_path,
        "tests.fixtures.process_workers:workspace_worker",
    )

    child_main.run(_write_request(tmp_path, request))

    result = _read_result(tmp_path)
    assert result.output_payload["workspace"] == str(tmp_path.resolve())
    assert result.output_payload["artifacts_dir"] == str(
        (tmp_path / "artifacts").resolve()
    )
    assert result.artifact_refs == [
        str((tmp_path / "artifacts/workspace.json").resolve())
    ]


def test_child_main_writes_structured_failure_for_worker_exception(tmp_path):
    request = _request(tmp_path, "tests.fixtures.process_workers:failing_worker")

    exit_code = child_main.run(_write_request(tmp_path, request))

    result = _read_result(tmp_path)
    assert exit_code == 1
    assert result.status == HarnessStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == "worker_crash"
    assert result.error.retryable is True
    assert "fixture worker failed" in result.error.message


def test_child_main_preserves_worker_reported_error(tmp_path):
    request = _request(
        tmp_path,
        "tests.fixtures.process_workers:reported_error_worker",
    )

    child_main.run(_write_request(tmp_path, request))

    error = _read_result(tmp_path).error
    assert error is not None
    assert error.error_class == "artifact_missing"
    assert error.retryable is False
    assert error.details["stage"] == "touchstone"


def test_child_main_writes_structured_failure_for_missing_entrypoint(tmp_path):
    request = _request(tmp_path, "tests.fixtures.process_workers:missing_worker")

    exit_code = child_main.run(_write_request(tmp_path, request))

    result = _read_result(tmp_path)
    assert exit_code == 1
    assert result.status == HarnessStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == "invalid_input"


def test_child_main_writes_heartbeat_with_process_identity(tmp_path):
    request = _request(tmp_path, "tests.fixtures.process_workers:echo_worker")

    child_main.run(_write_request(tmp_path, request))

    heartbeat = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["protocol_version"] == 1
    assert heartbeat["harness_run_id"] == "run-1"
    assert heartbeat["job_id"] == "job-1"
    assert isinstance(heartbeat["pid"], int)
    assert heartbeat["updated_at"]


def test_child_main_preserves_report_worker_source_integrity_failure(tmp_path):
    evidence = tmp_path / "score" / "evidence.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps({
        "score": {
            "status": "fail",
            "touchstone_kind": "s4p",
            "sparameter_mode": "differential",
            "return_loss_trace": "SDD11",
            "insertion_loss_trace": "SDD21",
            "rl_worst_db": -14.2,
            "rl_worst_frequency_ghz": 18.0,
            "insertion_worst_db_in_band": -2.1,
            "tdr_observation_port": "Diff1",
            "tdr_peak_deviation_ohm": 11.0,
            "tdr_peak_time_ps": 20.0,
            "optimization_objective": {"total_cost": 14.5},
            "plot_artifacts": {"tdr": "tdr.svg", "sdd11": "sdd11.svg", "sdd21": "sdd21.svg"},
        },
        "evidence_summary": {"status": "fail", "raw_sparameters": "artifact_only", "raw_tdr": "artifact_only"},
    }), encoding="utf-8")
    source = tmp_path / "reviewed.aedt"
    source.write_text("original", encoding="utf-8")
    import hashlib
    payload = {
        "decision": "complete",
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [str(evidence)],
            "source_project_path": str(source),
            "source_project_sha256": hashlib.sha256(b"original").hexdigest(),
        },
    }
    source.write_text("modified", encoding="utf-8")
    request = _request(
        tmp_path,
        "aedt_agent.agent.workers.brd_progress_report:run_brd_optimization_report_worker",
        payload,
    )

    exit_code = child_main.run(_write_request(tmp_path, request))

    result = _read_result(tmp_path)
    assert exit_code == 1
    assert result.status == HarnessStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == "invalid_model"
    assert result.error.retryable is False
    assert result.error.details["code"] == "source_project_modified"
    assert Path(result.error.details["artifact_refs"][0]).is_file()
