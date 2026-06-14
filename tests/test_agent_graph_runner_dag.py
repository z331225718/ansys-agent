from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.graph_runner import run_graph_sequential
from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path
from aedt_agent.agent.mission import GraphRunStatus, NodeRunStatus
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


def test_run_graph_sequential_records_graph_node_and_evidence(tmp_path):
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("构建 local cut", [], [])
    job = runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))

    report = run_graph_sequential(runtime, mission.mission_id, template, worker_id="graph")

    graph_runs = runtime.store.list_graph_runs(mission.mission_id)
    node_runs = runtime.store.list_node_runs(graph_runs[0].graph_run_id)
    evidence = runtime.store.list_evidence_packages(mission.mission_id)
    assert report["status"] == "passed"
    assert graph_runs[0].status == GraphRunStatus.SUCCEEDED
    assert graph_runs[0].template_id == "brd_local_cut_build"
    assert node_runs[0].status == NodeRunStatus.SUCCEEDED
    assert node_runs[0].node_id == "real_build_worker"
    assert node_runs[0].output_payload["evidence_summary"]["adapter"] == "agent_brd_local_cut"
    assert node_runs[0].artifact_refs
    assert node_runs[0].evidence_package_id == evidence[0].evidence_package_id
    assert node_runs[0].edge_decision == "succeeded"
    assert evidence[0].summary["scorecard"]["status"] == "passed"
    assert report["executed_job"]["job_id"] == job.job_id


def test_run_graph_sequential_rejects_job_outside_template(tmp_path):
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("未知 job", [], [])
    runtime.create_job(mission.mission_id, "unknown.capability", "unknown", {})

    with pytest.raises(ValueError, match="not allowed by graph template"):
        run_graph_sequential(runtime, mission.mission_id, template, worker_id="graph")


def test_run_graph_sequential_records_failed_graph_when_no_queued_job(tmp_path):
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("空 mission", [], [])

    report = run_graph_sequential(runtime, mission.mission_id, template, worker_id="graph")

    graph_runs = runtime.store.list_graph_runs(mission.mission_id)
    assert report["status"] == "failed"
    assert report["error"]["message"] == "no queued job"
    assert graph_runs[0].status == GraphRunStatus.FAILED
    assert graph_runs[0].error == {"message": "no queued job"}
