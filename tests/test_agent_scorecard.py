from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.scorecard import score_mission
from aedt_agent.agent.workers import BRD_LOCAL_CUT_BUILD_CAPABILITY, InMemoryWorkerRegistry, build_brd_local_cut_job_input, run_brd_local_cut_worker
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def _payload(tmp_path: Path) -> dict:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    return build_brd_local_cut_job_input(
        layout_file=layout_file,
        signal_nets=["56G_TX0_P", "56G_TX0_N"],
        reference_nets=["GND"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        artifact_dir=tmp_path / "artifacts",
        target_metrics=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
    )


def test_scorecard_passes_completed_brd_build_mission(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("构建 local cut", [], [])
    runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))
    runtime.execute_next_job(mission.mission_id, "worker-1")

    report = score_mission(runtime, mission.mission_id, template_id="brd_local_cut_build")

    assert report["status"] == "passed"
    assert {check["id"] for check in report["checks"]} >= {
        "mission_exists",
        "event_stream_present",
        "job_created",
        "succeeded_jobs_have_artifacts",
        "succeeded_jobs_have_evidence_summary",
    }
    assert all(check["passed"] for check in report["checks"])


def test_scorecard_fails_when_mission_has_no_jobs(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("空 mission", [], [])

    report = score_mission(runtime, mission.mission_id, template_id="brd_local_cut_build")

    assert report["status"] == "failed"
    failed = [check["id"] for check in report["checks"] if not check["passed"]]
    assert "job_created" in failed
