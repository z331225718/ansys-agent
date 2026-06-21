from __future__ import annotations

import json
from pathlib import Path

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.brd_progress_report import (
    BRD_OPTIMIZATION_PROGRESS_CAPABILITY,
    BRD_OPTIMIZATION_REPORT_CAPABILITY,
    run_brd_optimization_progress_worker,
    run_brd_optimization_report_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext


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
            "plot_artifacts": {},
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
        "status": "failed",
        "score": {"status": "fail"},
        "evidence_summary": {"raw_sparameters": "artifact_only", "raw_tdr": "artifact_only"},
        "evidence_artifact": evidence_path,
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [evidence_path],
        },
    }

    output = run_brd_optimization_progress_worker(
        _job(BRD_OPTIMIZATION_PROGRESS_CAPABILITY, payload),
        WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
    )

    assert output["status"] == "failed"
    assert Path(output["optimization_history_csv"]).is_file()
    assert Path(output["report_json"]).is_file()
    assert Path(output["report_html"]).is_file()
    assert output["optimization_history_rows"][0]["round_index"] == 1
    assert output["loop_context"]["optimization_history_csv"] == output["optimization_history_csv"]
    assert output["evidence_summary"]["optimization_report_html"] == output["report_html"]


def test_final_report_worker_returns_scorecard_report(tmp_path):
    evidence_path = _score_evidence(tmp_path / "score" / "evidence.json")
    payload = {
        "decision": "complete",
        "reason": "max rounds reached",
        "loop_context": {
            "round_index": 1,
            "report_dir": str(tmp_path / "progress"),
            "score_evidence_paths": [evidence_path],
        },
    }

    output = run_brd_optimization_report_worker(
        _job(BRD_OPTIMIZATION_REPORT_CAPABILITY, payload),
        WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
    )

    assert output["status"] == "passed"
    assert output["checks"][0]["id"] == "raw_trace_policy"
    assert Path(output["optimization_history_csv"]).is_file()
    assert output["optimization_history_rows"][0]["score_status"] == "fail"
    assert output["artifact_refs"] == [
        output["optimization_history_csv"],
        output["report_json"],
        output["report_html"],
    ]
