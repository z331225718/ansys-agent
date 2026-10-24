from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.brd_progress_report import (
    BRD_OPTIMIZATION_PROGRESS_CAPABILITY,
    BRD_OPTIMIZATION_REPORT_CAPABILITY,
    run_brd_optimization_progress_worker,
    run_brd_optimization_report_worker,
)
from aedt_agent.agent.mission import JobStatus
from aedt_agent.agent.workers.registry import InMemoryWorkerRegistry, WorkerContext, WorkerReportedError


def _job(capability: str, input_payload: dict) -> JobRecord:
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=capability,
        idempotency_key=capability,
        input_payload=input_payload,
        timeout_seconds=30,
        retry_limit=1,
    )


def _score_evidence(path: Path) -> str:
    payload = {
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
            "tdr_proximity_mse_ohm2": 8.0,
            "tdr_flatness_msd_ohm2": 2.0,
            "rl_violation_sum_db": 4.5,
            "optimization_objective": {"total_cost": 14.5},
            "plot_artifacts": {
                "tdr": "tdr.svg",
                "sdd11": "sdd11.svg",
                "sdd21": "sdd21.svg",
            },
            "samples": {"sparameter_count": 3, "tdr_count": 4},
        },
        "evidence_summary": {
            "status": "fail",
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def test_progress_worker_writes_history_and_report_artifacts(tmp_path):
    evidence_path = _score_evidence(tmp_path / "score" / "evidence.json")
    payload = {
        "status": "approval_required",
        "edge_outcome": "approval_required",
        "approval_reason": "upstream qualifier review",
        "approval_required": {"reason": "upstream qualifier review"},
        "score": {"status": "fail"},
        "evidence_summary": {"raw_sparameters": "artifact_only", "raw_tdr": "artifact_only"},
        "evidence_artifact": evidence_path,
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [evidence_path],
            "best_project_preservation_status": "updated",
            "best_round_index": 1,
            "best_objective_total_cost": 14.5,
            "best_project_path": str(tmp_path / "best" / "case.best.aedt"),
            "best_project_manifest_path": str(
                tmp_path / "best" / "best_project_manifest.json"
            ),
            "best_score_evidence_path": evidence_path,
            "best_project_artifact_refs": [
                str(tmp_path / "best" / "case.best.aedt"),
                str(tmp_path / "best" / "best_project_manifest.json"),
            ],
        },
    }

    output = run_brd_optimization_progress_worker(
        _job(BRD_OPTIMIZATION_PROGRESS_CAPABILITY, payload),
        WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
    )

    assert output["status"] == "succeeded"
    assert "approval_required" not in output
    assert "edge_outcome" not in output
    assert Path(output["optimization_history_csv"]).is_file()
    assert Path(output["report_json"]).is_file()
    assert Path(output["report_html"]).is_file()
    assert output["optimization_history_rows"][0]["round_index"] == 1
    assert output["best_project"]["project_path"].endswith("case.best.aedt")
    assert output["loop_context"]["optimization_history_csv"] == output["optimization_history_csv"]
    assert output["evidence_summary"]["upstream_status"] == "approval_required"
    assert output["evidence_summary"]["optimization_report_html"] == output["report_html"]
    assert "Best-so-far 工程文件" in Path(output["report_html"]).read_text(
        encoding="utf-8"
    )


def test_final_report_worker_returns_scorecard_report(tmp_path):
    evidence_path = _score_evidence(tmp_path / "score" / "evidence.json")
    source = tmp_path / "source" / "reviewed.aedt"
    source.parent.mkdir(parents=True)
    source.write_text("reviewed project", encoding="utf-8")
    payload = {
        "decision": "complete",
        "reason": "max rounds reached",
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [evidence_path],
            "source_project_path": str(source),
            "source_project_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
    }

    output = run_brd_optimization_report_worker(
        _job(BRD_OPTIMIZATION_REPORT_CAPABILITY, payload),
        WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
    )

    assert output["status"] == "passed"
    assert next(check for check in output["checks"] if check["id"] == "source_project_integrity")["status"] == "passed"
    assert next(check for check in output["checks"] if check["id"] == "raw_trace_policy")["status"] == "passed"
    assert next(check for check in output["checks"] if check["id"] == "required_plots")[
        "status"
    ] == "passed"
    assert Path(output["optimization_history_csv"]).is_file()
    assert output["optimization_history_rows"][0]["score_status"] == "fail"
    assert output["artifact_refs"] == [
        output["optimization_history_csv"],
        output["report_json"],
        output["report_html"],
    ]



def test_final_report_worker_fails_closed_when_source_changed(tmp_path):
    evidence_path = _score_evidence(tmp_path / "score" / "evidence.json")
    source = tmp_path / "source" / "reviewed.aedt"
    source.parent.mkdir(parents=True)
    source.write_text("original", encoding="utf-8")
    payload = {
        "decision": "complete",
        "reason": "max rounds reached",
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [evidence_path],
            "source_project_path": str(source),
            "source_project_sha256": hashlib.sha256(b"original").hexdigest(),
        },
    }
    source.write_text("modified during loop", encoding="utf-8")
    with pytest.raises(WorkerReportedError) as exc:
        run_brd_optimization_report_worker(
            _job(BRD_OPTIMIZATION_REPORT_CAPABILITY, payload),
            WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
        )
    assert exc.value.error_class == "invalid_model"
    assert exc.value.retryable is False
    assert exc.value.details["code"] == "source_project_modified"
    assert Path(exc.value.details["artifact_refs"][0]).is_file()


def test_final_report_worker_fails_closed_without_source_baseline(tmp_path):
    evidence_path = _score_evidence(tmp_path / "score" / "evidence.json")
    payload = {
        "decision": "complete",
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [evidence_path],
        },
    }
    with pytest.raises(WorkerReportedError) as exc:
        run_brd_optimization_report_worker(
            _job(BRD_OPTIMIZATION_REPORT_CAPABILITY, payload),
            WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
        )
    assert exc.value.details["code"] == "source_integrity_unavailable"


def test_report_worker_error_becomes_failed_job_with_diagnostic_details(tmp_path):
    evidence_path = _score_evidence(tmp_path / "score" / "evidence.json")
    source = tmp_path / "source" / "reviewed.aedt"
    source.parent.mkdir(parents=True)
    source.write_text("changed", encoding="utf-8")
    payload = {
        "decision": "complete",
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [evidence_path],
            "source_project_path": str(source),
            "source_project_sha256": hashlib.sha256(b"original").hexdigest(),
        },
    }
    registry = InMemoryWorkerRegistry()
    registry.register(BRD_OPTIMIZATION_REPORT_CAPABILITY, run_brd_optimization_report_worker)
    result = registry.execute(
        _job(BRD_OPTIMIZATION_REPORT_CAPABILITY, payload),
        WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
    )
    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert result.error.details["code"] == "source_project_modified"
    assert Path(result.error.details["artifact_refs"][0]).is_file()
