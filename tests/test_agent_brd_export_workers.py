from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers import (
    BRD_TDR_EXPORT_CAPABILITY,
    BRD_TOUCHSTONE_EXPORT_CAPABILITY,
    WorkerContext,
    build_brd_tdr_export_job_input,
    build_brd_touchstone_export_job_input,
    run_brd_tdr_export_worker,
    run_brd_touchstone_export_worker,
)


def _write_touchstone(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# GHz S MA R 50",
                "0.00 0.05 0 0.90 0 0.90 0 0.05 0",
                "18.00 0.45 0 0.80 0 0.80 0 0.05 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_tdr(path: Path) -> None:
    path.write_text(
        "time_ps,impedance_ohm\n0,90\n10,94\n20,89\n",
        encoding="utf-8",
    )


def _job(capability: str, payload: dict) -> JobRecord:
    return JobRecord.create(
        "job-1",
        "mission-1",
        capability,
        "export",
        payload,
        300,
        1,
    )


def test_touchstone_export_worker_registers_existing_solve_artifact(tmp_path):
    touchstone = tmp_path / "channel.s2p"
    _write_touchstone(touchstone)
    payload = build_brd_touchstone_export_job_input(
        touchstone_path=touchstone,
        artifact_dir=tmp_path / "solve-artifacts",
        sparameter_mode="auto",
        loop_context={"round_index": 1},
    )
    context = WorkerContext(
        "worker-1",
        artifacts_dir=str(tmp_path / "export-artifacts"),
    )

    output = run_brd_touchstone_export_worker(
        _job(BRD_TOUCHSTONE_EXPORT_CAPABILITY, payload),
        context,
    )

    manifest = Path(output["touchstone_export_manifest"])
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert output["status"] == "succeeded"
    assert output["touchstone_kind"] == "s2p"
    assert output["return_loss_trace"] == "S11"
    assert output["insertion_loss_trace"] == "S21"
    assert output["evidence_summary"]["raw_sparameters"] == "artifact_only"
    assert manifest_payload["outputs"]["touchstone"]["path"] == str(touchstone)
    assert str(manifest) in output["artifact_refs"]
    assert output["loop_context"]["last_touchstone_path"] == str(touchstone)


def test_tdr_export_worker_registers_existing_solve_artifact(tmp_path):
    tdr = tmp_path / "ChannelTDR.csv"
    _write_tdr(tdr)
    payload = build_brd_tdr_export_job_input(
        tdr_path=tdr,
        touchstone_path=tmp_path / "channel.s4p",
        tdr_expression="TDRZt(Diff1)",
        tdr_observation_port="Diff1",
        tdr_report_name="ChannelTDR",
        loop_context={"round_index": 1},
    )
    context = WorkerContext(
        "worker-1",
        artifacts_dir=str(tmp_path / "tdr-export-artifacts"),
    )

    output = run_brd_tdr_export_worker(
        _job(BRD_TDR_EXPORT_CAPABILITY, payload),
        context,
    )

    manifest = Path(output["tdr_export_manifest"])
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert output["status"] == "succeeded"
    assert output["tdr_path"] == str(tdr)
    assert output["evidence_summary"]["raw_tdr"] == "artifact_only"
    assert output["evidence_summary"]["tdr_observation_port"] == "Diff1"
    assert manifest_payload["summary"]["tdr_sample_count"] == 3
    assert str(manifest) in output["artifact_refs"]
    assert output["loop_context"]["last_tdr_path"] == str(tdr)


def test_tdr_export_worker_rejects_missing_artifact(tmp_path):
    payload = build_brd_tdr_export_job_input(
        tdr_path=tmp_path / "missing.csv",
    )

    with pytest.raises(ValueError, match="tdr_path does not exist"):
        run_brd_tdr_export_worker(
            _job(BRD_TDR_EXPORT_CAPABILITY, payload),
            WorkerContext("worker-1", artifacts_dir=str(tmp_path / "artifacts")),
        )
