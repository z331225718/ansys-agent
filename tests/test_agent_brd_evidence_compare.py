from __future__ import annotations

import json
from pathlib import Path

from aedt_agent.agent.workers.brd_evidence_compare import (
    build_evidence_compare_job_input,
    run_evidence_compare_worker,
)
from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext


def _make_job(payload: dict) -> JobRecord:
    return JobRecord.create(
        job_id="j1", mission_id="m1", capability="brd.evidence.compare",
        idempotency_key="k1", input_payload=payload,
        timeout_seconds=300, retry_limit=1,
    )


def _before_score() -> dict:
    return {"score": {"rl_worst_db": -14.0, "tdr_peak_deviation_ohm": 9.0}}


def _after_score() -> dict:
    return {"score": {"rl_worst_db": -21.0, "tdr_peak_deviation_ohm": 4.0}}


def test_evidence_compare_classifies_improvement(tmp_path: Path):
    before_path = tmp_path / "before_score.json"
    after_path = tmp_path / "after_score.json"
    before_path.write_text(json.dumps(_before_score()))
    after_path.write_text(json.dumps(_after_score()))

    job = _make_job(build_evidence_compare_job_input(
        before_score_path=before_path,
        after_score_path=after_path,
        artifact_dir=tmp_path / "artifacts",
    ))
    result = run_evidence_compare_worker(job, WorkerContext("w1"))

    assert result["status"] == "improved"
    assert result["comparison"]["status"] == "improved"
    assert result["comparison"]["rl_worst_delta_db"] == -7.0
    assert "改善" in result["comparison"]["summary"]


def test_evidence_compare_detects_regression(tmp_path: Path):
    before_path = tmp_path / "before_score.json"
    after_path = tmp_path / "after_score.json"
    before_path.write_text(json.dumps({"score": {"rl_worst_db": -21.0, "tdr_peak_deviation_ohm": 4.0}}))
    after_path.write_text(json.dumps({"score": {"rl_worst_db": -14.0, "tdr_peak_deviation_ohm": 9.0}}))

    job = _make_job(build_evidence_compare_job_input(
        before_score_path=before_path,
        after_score_path=after_path,
        artifact_dir=tmp_path / "artifacts",
    ))
    result = run_evidence_compare_worker(job, WorkerContext("w1"))

    assert result["status"] == "not_improved"
    assert result["comparison"]["status"] in ("regressed", "mixed")


def test_evidence_compare_rejects_missing_file(tmp_path: Path):
    job = _make_job(build_evidence_compare_job_input(
        before_score_path=tmp_path / "nonexistent.json",
        after_score_path=tmp_path / "nonexistent2.json",
        artifact_dir=tmp_path / "artifacts",
    ))
    try:
        run_evidence_compare_worker(job, WorkerContext("w1"))
        assert False, "should raise"
    except FileNotFoundError:
        pass
