from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.graph_runner import run_graph_once
from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path
from aedt_agent.agent.orchestrator import AgentRuntime
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
    )


def test_run_graph_once_executes_full_template_and_stops_at_approval(tmp_path):
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("构建 local cut", [], [])
    job = runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))

    report = run_graph_once(runtime, mission.mission_id, template, worker_id="graph")

    assert report["status"] == "waiting_approval"
    assert report["executed_node"]["id"] == "real_build_worker"
    assert report["executed_job"]["job_id"] == job.job_id
    assert report["executed_job"]["status"] == "succeeded"
    assert report["scorecard"]["status"] == "passed"
    assert [node_run["node_id"] for node_run in report["node_runs"]] == [
        "planner",
        "input_validator",
        "real_build_worker",
        "model_review_scorecard",
        "approval_gate",
    ]
    assert report["node_runs"][-1]["status"] == "waiting_approval"


def test_run_graph_once_rejects_job_outside_template(tmp_path):
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("未知 job", [], [])
    runtime.create_job(mission.mission_id, "unknown.capability", "unknown", {})

    with pytest.raises(ValueError, match="not allowed by graph template"):
        run_graph_once(runtime, mission.mission_id, template, worker_id="graph")
