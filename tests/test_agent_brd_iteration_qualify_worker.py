from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.brd_iteration_qualify import (
    BRD_ITERATION_QUALIFY_CAPABILITY,
    build_brd_iteration_qualify_job_input,
    run_brd_iteration_qualify_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext, WorkerReportedError


def _job(input_payload: dict) -> JobRecord:
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=BRD_ITERATION_QUALIFY_CAPABILITY,
        idempotency_key="iteration-qualify",
        input_payload=input_payload,
        timeout_seconds=30,
        retry_limit=1,
    )


def _artifact(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("artifact", encoding="utf-8")
    return str(path)


def _valid_payload(tmp_path: Path) -> dict:
    touchstone = _artifact(tmp_path / "channel.s4p")
    tdr = _artifact(tmp_path / "tdr.csv")
    evidence_artifact = _artifact(tmp_path / "brd_channel_score_evidence.json")
    sdd11_plot = _artifact(tmp_path / "sdd11.png")
    sdd21_plot = _artifact(tmp_path / "sdd21.png")
    tdr_plot = _artifact(tmp_path / "tdr.png")
    score = {
        "status": "fail",
        "touchstone_kind": "s4p",
        "sparameter_mode": "differential",
        "return_loss_trace": "SDD11",
        "insertion_loss_trace": "SDD21",
        "rl_worst_db": -14.2,
        "insertion_worst_db_in_band": -2.1,
        "tdr_observation_port": "Diff1",
        "tdr_peak_deviation_ohm": 11.0,
        "tdr_proximity_mse_ohm2": 8.0,
        "tdr_flatness_msd_ohm2": 2.0,
        "rl_violation_sum_db": 4.5,
        "optimization_objective": {"total_cost": 14.5},
        "plot_artifacts": {
            "sdd11": sdd11_plot,
            "sdd21": sdd21_plot,
            "tdr": tdr_plot,
        },
    }
    evidence = {
        "status": "fail",
        "touchstone_kind": "s4p",
        "sparameter_mode": "differential",
        "return_loss_trace": "SDD11",
        "insertion_loss_trace": "SDD21",
        "tdr_observation_port": "Diff1",
        "raw_sparameters": "artifact_only",
        "raw_tdr": "artifact_only",
        "artifact_refs": [touchstone, tdr, evidence_artifact],
    }
    return build_brd_iteration_qualify_job_input(
        score=score,
        evidence_summary=evidence,
        touchstone_path=touchstone,
        tdr_path=tdr,
        evidence_artifact=evidence_artifact,
        artifact_refs=[touchstone, tdr, evidence_artifact],
        loop_context={"round_index": 2},
    )


def test_iteration_qualifier_passes_bounded_differential_score(tmp_path):
    payload = _valid_payload(tmp_path)

    output = run_brd_iteration_qualify_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
    )

    assert output["status"] == "succeeded"
    assert output["evidence_summary"]["iteration_qualification_status"] == "succeeded"
    assert output["iteration_qualification"]["blocking_count"] == 0
    assert Path(output["iteration_qualification_manifest"]).is_file()
    assert output["loop_context"]["last_iteration_qualification_status"] == "succeeded"


def test_iteration_qualifier_requires_approval_for_bad_contract(tmp_path):
    payload = _valid_payload(tmp_path)
    payload["score"]["touchstone_kind"] = "s2p"
    payload["score"]["return_loss_trace"] = "S11"
    payload["score"]["insertion_loss_trace"] = "S21"
    payload["score"]["tdr_observation_port"] = "Port1"
    payload["evidence_summary"]["raw_sparameters"] = "inline"
    payload["tdr_path"] = str(tmp_path / "missing.csv")

    output = run_brd_iteration_qualify_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
    )

    assert output["status"] == "approval_required"
    assert output["edge_outcome"] == "approval_required"
    assert output["approval_reason"].startswith("iteration_qualification:")
    issue_ids = {
        issue["id"]
        for issue in output["iteration_qualification"]["checks"]
        if issue["status"] != "passed"
    }
    assert "raw_trace_policy" in issue_ids
    assert "differential_touchstone_contract" in issue_ids
    assert "tdr_observation_port" in issue_ids
    assert "score_artifacts_exist" in issue_ids


def test_iteration_qualifier_reports_missing_score_input():
    with pytest.raises(WorkerReportedError) as exc:
        run_brd_iteration_qualify_worker(
            _job({"score": {}, "evidence_summary": {}}),
            WorkerContext(worker_id="worker-1"),
        )

    assert exc.value.error_class == "invalid_input"
    assert exc.value.retryable is False
