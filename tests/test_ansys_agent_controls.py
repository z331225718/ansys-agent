from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.cli import _runtime_with_workers
from aedt_agent.agent.graph_runner import create_graph_run, run_graph
from aedt_agent.agent.graph_template import graph_template_from_mapping, load_graph_template
from aedt_agent.agent.mission import ApprovalDecision, GraphRunStatus
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.ansys_agent.case_config import AnsysAgentCase, AnsysAgentCaseError
from aedt_agent.ansys_agent.supervisor import AnsysAgentSupervisor


def _case(tmp_path: Path) -> AnsysAgentCase:
    return AnsysAgentCase(
        case_id="control-case",
        db_path=tmp_path / "missions.db",
        loop_config=Path("config/optimization_loops/reviewed_brd_remote.example.json"),
        execution_profile=Path("config/execution_profiles/local_real_aedt.example.json"),
        max_workers=1,
        poll_interval_seconds=30,
        check_paths=False,
    )


def _waiting_local_cut_graph(tmp_path: Path):
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
            "local_cut_region": {
                "type": "bbox",
                "unit": "mil",
                "x_min": 0,
                "y_min": 0,
                "x_max": 1,
                "y_max": 1,
            },
            "artifact_dir": str(tmp_path / "artifacts"),
            "adapter_mode": "deterministic",
        },
    )
    approval_id = next(
        run["output_payload"]["approval_id"]
        for run in report["node_runs"]
        if "approval_id" in run["output_payload"]
    )
    return runtime, report, approval_id


def test_ansys_agent_resume_stops_at_pending_approval_without_advancing(tmp_path: Path):
    runtime, report, approval_id = _waiting_local_cut_graph(tmp_path)
    supervisor = AnsysAgentSupervisor(_case(tmp_path))
    node_count_before = len(runtime.store.list_node_runs(report["graph_run"]["graph_run_id"]))

    blocked = supervisor.resume(graph_run_id=report["graph_run"]["graph_run_id"])

    assert blocked["status"] == "waiting_approval"
    assert blocked["pending_approvals"][0]["approval_id"] == approval_id
    assert blocked["agent_status"]["available_commands"]["approve_and_resume"].endswith(
        "--option-id approve --resume --graph-run-id "
        + report["graph_run"]["graph_run_id"]
    )
    assert len(runtime.store.list_node_runs(report["graph_run"]["graph_run_id"])) == node_count_before


def test_ansys_agent_status_without_db_includes_operator_fields(tmp_path: Path):
    status = AnsysAgentSupervisor(_case(tmp_path)).status()

    assert status["status"] == "not_started"
    assert status["reason"].startswith("mission db does not exist")
    assert status["available_commands"]["preflight"].startswith(
        "python -m aedt_agent.ansys_agent preflight"
    )
    assert status["recommended_command"].startswith("python -m aedt_agent.ansys_agent preflight")
    assert status["pending_approvals"] == []


def test_ansys_agent_approve_with_resume_finishes_waiting_graph(tmp_path: Path):
    _, report, approval_id = _waiting_local_cut_graph(tmp_path)
    supervisor = AnsysAgentSupervisor(_case(tmp_path))

    resumed = supervisor.approve(
        approval_id=approval_id,
        comment="ok",
        resume=True,
        graph_run_id=report["graph_run"]["graph_run_id"],
    )

    assert resumed["status"] == "succeeded"
    assert resumed["approval"]["selected_option_id"] == "approve"
    assert resumed["graph_run_id"] == report["graph_run"]["graph_run_id"]
    assert resumed["agent_status"]["next_safe_action"] == "report"


def test_ansys_agent_approve_resume_rejects_mismatched_graph_run(tmp_path: Path):
    runtime, _, approval_id = _waiting_local_cut_graph(tmp_path)
    other_mission = runtime.create_mission("other mission", [], [])
    other_template = graph_template_from_mapping(
        {
            "id": "other_graph",
            "version": 1,
            "nodes": [{"id": "source", "role": "planner", "kind": "llm"}],
            "edges": [],
            "handoffs": {},
        }
    )
    other_graph = create_graph_run(runtime, other_mission.mission_id, other_template)
    supervisor = AnsysAgentSupervisor(_case(tmp_path))

    with pytest.raises(AnsysAgentCaseError, match="approval mission does not match"):
        supervisor.approve(
            approval_id=approval_id,
            resume=True,
            graph_run_id=other_graph.graph_run_id,
        )

    assert runtime.store.get_approval(approval_id).decision == ApprovalDecision.PENDING


def test_ansys_agent_stop_cancels_running_graph_and_mission(tmp_path: Path):
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
    supervisor = AnsysAgentSupervisor(_case(tmp_path))

    stopped = supervisor.stop(graph_run_id=graph_run.graph_run_id, reason="test stop")

    assert stopped["status"] == "canceled"
    assert stopped["agent_status"]["status"] == "canceled"
    assert runtime.store.get_graph_run(graph_run.graph_run_id).status == GraphRunStatus.CANCELED


def test_ansys_agent_resume_rejects_ssh_profile_by_default(tmp_path: Path):
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
    case = AnsysAgentCase(
        case_id="ssh-blocked",
        db_path=tmp_path / "missions.db",
        loop_config=Path("config/optimization_loops/reviewed_brd_remote.example.json"),
        execution_profile=Path("config/execution_profiles/ssh_remote.example.json"),
        max_workers=1,
        poll_interval_seconds=30,
        check_paths=False,
    )

    with pytest.raises(AnsysAgentCaseError, match="profile_local_cli"):
        AnsysAgentSupervisor(case).resume(graph_run_id=graph_run.graph_run_id)
