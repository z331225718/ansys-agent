from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers import BRD_CHANNEL_SCORE_CAPABILITY, WorkerContext, build_brd_channel_score_job_input, run_brd_channel_score_worker


def _write_touchstone(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# GHz S MA R 50",
                "0.00 0.05 0 0.90 0 0.90 0 0.05 0",
                "18.00 0.45 0 0.80 0 0.80 0 0.05 0",
                "67.00 0.04 0 0.70 0 0.70 0 0.04 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_tdr(path: Path) -> None:
    path.write_text("time_ps,impedance_ohm\n0,100\n10,104\n20,111\n30,101\n", encoding="utf-8")


def test_brd_channel_score_worker_outputs_bounded_evidence(tmp_path):
    touchstone = tmp_path / "channel.s2p"
    tdr = tmp_path / "channel_tdr.csv"
    _write_touchstone(touchstone)
    _write_tdr(tdr)
    payload = build_brd_channel_score_job_input(
        touchstone_path=touchstone,
        tdr_path=tdr,
        artifact_dir=tmp_path / "artifacts",
        frequency_stop_ghz=67.0,
        rl_target_db=-20.0,
        tdr_plot_time_stop_ps=20.0,
    )
    job = JobRecord.create("job-1", "mission-1", BRD_CHANNEL_SCORE_CAPABILITY, "score", payload, 300, 1)

    output = run_brd_channel_score_worker(job, WorkerContext("worker-1"))

    assert output["status"] == "failed"
    assert output["score"]["rl_worst_frequency_ghz"] == 18.0
    assert output["evidence_summary"]["touchstone_kind"] == "s2p"
    assert output["evidence_summary"]["raw_sparameters"] == "artifact_only"
    assert output["evidence_summary"]["raw_tdr"] == "artifact_only"
    assert output["evidence_summary"]["tdr_flatness_msd_ohm2"] == 55.0
    assert output["evidence_summary"]["optimization_objective"]["strategy"] == "rl_violation_plus_tdr_proximity_flatness"
    assert output["sparameter_evidence"]["raw_trace_policy"] == "artifact_only"
    assert str(touchstone) in output["artifact_refs"]
    assert str(tdr) in output["artifact_refs"]
    assert output["evidence_artifact"].endswith("brd_channel_score_evidence.json")
    assert Path(output["score"]["plot_artifacts"]["tdr"]).is_file()
    assert Path(output["score"]["plot_artifacts"]["s11"]).is_file()
    assert Path(output["score"]["plot_artifacts"]["s21"]).is_file()
    tdr_svg = Path(output["score"]["plot_artifacts"]["tdr"]).read_text(
        encoding="utf-8"
    )
    s11_svg = Path(output["score"]["plot_artifacts"]["s11"]).read_text(
        encoding="utf-8"
    )
    s21_svg = Path(output["score"]["plot_artifacts"]["s21"]).read_text(
        encoding="utf-8"
    )
    assert 'data-x-max="20"' in tdr_svg
    assert 'data-y-min="80"' in tdr_svg
    assert 'data-y-max="120"' in tdr_svg
    assert "target 100 ohm" in tdr_svg
    assert 'data-y-min="-40"' in s11_svg
    assert 'data-y-max="0"' in s11_svg
    assert "target -20 dB" in s11_svg
    assert 'data-y-min="-5"' in s21_svg
    assert 'data-y-max="1"' in s21_svg
    assert "0.00 0.05" not in str(output["evidence_summary"])


def test_brd_channel_score_worker_rejects_missing_artifacts(tmp_path):
    payload = build_brd_channel_score_job_input(
        touchstone_path=tmp_path / "missing.s2p",
        tdr_path=tmp_path / "missing.csv",
        artifact_dir=tmp_path / "artifacts",
    )
    job = JobRecord.create("job-1", "mission-1", BRD_CHANNEL_SCORE_CAPABILITY, "score", payload, 300, 1)

    with pytest.raises(ValueError, match="touchstone_path does not exist"):
        run_brd_channel_score_worker(job, WorkerContext("worker-1"))
