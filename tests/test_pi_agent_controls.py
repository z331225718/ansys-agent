from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.cli import _runtime_with_workers
from aedt_agent.agent.graph_runner import create_graph_run, run_graph
from aedt_agent.agent.graph_template import graph_template_from_mapping, load_graph_template
from aedt_agent.agent.mission import GraphRunStatus
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.pi_agent.case_config import PiAgentCase, PiAgentCaseError
from aedt_agent.pi_agent.supervisor import PiAgentSupervisor


def _case(tmp_path: Path) -> PiAgentCase:
    return PiAgentCase(
        case_id="control-case",
        db_path=tmp_path / "missions.db",
        loop_config=Path("config/optimization_loops/reviewed_brd_remote.example.json"),
        execution_profile=Path("config/execution_profiles/local_real_aedt.example.json"),
        max_workers=1,
        poll_interval_seconds=30,
        check_paths=False,
    )


def test_pi_agent_approve_and_resume_finishes_waiting_graph(tmp_path: Path):
    runtime = _runtime_with_workers(tmp_path / "missions.db")
    mission = runtime.create_mission("review local cut", [], [])
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    report = run_graph(
        runtime,
        mission.mission_id,
        load_graph_template("brd_local_cut_build"),
        initial_payload={
            "layout_file": str(layout_file),
            "signal_nets": ["P", "N"],
            "reference_nets": ["GND"],
            "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
            "artifact_dir": str(tmp_path / "artifacts"),
            "adapter_mode": "deterministic",
        },
    )
    approval_id = next(
        run["output_payload"]["approval_id"]
        for run in report["node_runs"]
        if "approval_id" in run["output_payload"]
    )
    supervisor = PiAgentSupervisor(_case(tmp_path))

    approved = supervisor.approve(approval_id=approval_id, comment="ok")
    resumed = supervisor.resume(graph_run_id=report["graph_run"]["graph_run_id"])

    assert approved["status"] == "approved"
    assert resumed["status"] == "succeeded"
    assert resumed["pi_status"]["next_safe_action"] == "report"


def test_pi_agent_stop_cancels_running_graph_and_mission(tmp_path: Path):
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "missions.db"))
    mission = runtime.create_mission("stop me", [], [])
    template = graph_template_from_mapping(
        {
            "id": "stop_graph",
            "version": 1,
            "nodes": [{"id": "source", "role": "planner", "kind": "llm"}],
            "edges": [],
            "handoffs": {},
        }
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template)
    supervisor = PiAgentSupervisor(_case(tmp_path))

    stopped = supervisor.stop(graph_run_id=graph_run.graph_run_id, reason="test stop")

    assert stopped["status"] == "canceled"
    assert stopped["pi_status"]["status"] == "canceled"
    assert runtime.store.get_graph_run(graph_run.graph_run_id).status == GraphRunStatus.CANCELED


def test_pi_agent_resume_rejects_ssh_profile_by_default(tmp_path: Path):
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "missions.db"))
    mission = runtime.create_mission("ssh resume blocked", [], [])
    template = graph_template_from_mapping(
        {
            "id": "blocked_graph",
            "version": 1,
            "nodes": [{"id": "source", "role": "planner", "kind": "llm"}],
            "edges": [],
            "handoffs": {},
        }
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template)
    case = PiAgentCase(
        case_id="ssh-blocked",
        db_path=tmp_path / "missions.db",
        loop_config=Path("config/optimization_loops/reviewed_brd_remote.example.json"),
        execution_profile=Path("config/execution_profiles/ssh_remote.example.json"),
        max_workers=1,
        poll_interval_seconds=30,
        check_paths=False,
    )

    with pytest.raises(PiAgentCaseError, match="profile_local_cli"):
        PiAgentSupervisor(case).resume(graph_run_id=graph_run.graph_run_id)
