from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers import (
    BRD_REAL_SOLVE_CAPABILITY,
    WorkerContext,
    build_brd_real_solve_job_input,
    run_brd_real_solve_worker,
)
from aedt_agent.infrastructure import BrdRealSolveResult


def _payload(tmp_path: Path) -> dict:
    project = tmp_path / "approved.aedt"
    project.write_text("approved", encoding="utf-8")
    return build_brd_real_solve_job_input(
        project_path=project,
        setup_name="Setup1",
        sweep_name="Sweep1",
        tdr_expression="TDRZt(P1,P1)",
        expected_port_count=2,
        frequency_start_ghz=0.0,
        frequency_stop_ghz=67.0,
        rl_target_db=-20.0,
        tdr_target_ohm=100.0,
        tdr_observation_port="P1",
        aedt={"version": "2026.1", "non_graphical": True},
    )


def _job(tmp_path: Path) -> JobRecord:
    return JobRecord.create(
        "job-1",
        "mission-1",
        BRD_REAL_SOLVE_CAPABILITY,
        "real-solve",
        _payload(tmp_path),
        7200,
        1,
    )


class FakeSolveAdapter:
    request = None

    def run(self, request):
        self.request = request
        paths = {
            "project_checkpoint": request.artifact_dir
            / "checkpoint.aedt",
            "solved_project": request.artifact_dir / "solved.aedt",
            "touchstone_path": request.artifact_dir / "channel.s2p",
            "tdr_path": request.artifact_dir / "ChannelTDR.csv",
            "solve_manifest_path": request.artifact_dir
            / "solve_manifest.json",
        }
        request.artifact_dir.mkdir(parents=True, exist_ok=True)
        for path in paths.values():
            path.write_text("artifact", encoding="utf-8")
        return BrdRealSolveResult(
            **{
                key: str(path)
                for key, path in paths.items()
            },
            summary={
                "touchstone_sample_count": 1341,
                "tdr_sample_count": 10001,
            },
        )


def test_real_solve_worker_requires_harness_artifact_directory(
    tmp_path,
):
    with pytest.raises(
        ValueError,
        match="requires process harness artifacts_dir",
    ):
        run_brd_real_solve_worker(
            _job(tmp_path),
            WorkerContext("worker-1"),
        )


def test_real_solve_worker_uses_context_artifacts_and_returns_only_refs(
    tmp_path,
):
    context = WorkerContext(
        "worker-1",
        workspace=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    adapter = FakeSolveAdapter()

    output = run_brd_real_solve_worker(
        _job(tmp_path),
        context,
        solve_adapter=adapter,
    )

    assert adapter.request.artifact_dir == tmp_path / "artifacts"
    assert adapter.request.tdr_observation_port == "P1"
    assert adapter.request.tdr_reference_impedance_ohm == 100.0
    assert adapter.request.project_copy_mode == "checkpoint_copy"
    assert output["status"] == "succeeded"
    assert output["solve_summary"]["raw_sparameters"] == "artifact_only"
    assert output["solve_summary"]["raw_tdr"] == "artifact_only"
    assert "frequency_ghz" not in json.dumps(output)
    assert len(output["artifact_refs"]) == 5


def test_real_solve_job_input_contains_no_output_directory(tmp_path):
    payload = _payload(tmp_path)

    assert "artifact_dir" not in payload
    assert payload["solution_name"] == "Setup1 : Sweep1"
    assert payload["run_analyze"] is True
    assert payload["tdr_observation_port"] == "P1"
    assert payload["tdr_reference_impedance_ohm"] == 100.0
    assert payload["sparameter_mode"] == "auto"
    assert payload["project_copy_mode"] == "checkpoint_copy"
    assert payload["approval_reason"] == "approve_real_brd_solve"
    assert payload["approval_options"][0]["id"] == "approve"
