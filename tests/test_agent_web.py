from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote

from aedt_agent.agent.web import AGENT_PAGE, _dashboard_startup_message, dispatch_agent_request
from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_runner import graph_status
from aedt_agent.agent.mission import GraphRunRecord, NodeRunRecord, NodeRunStatus
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    InMemoryWorkerRegistry,
    run_brd_local_cut_worker,
)
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "m.db"), registry=registry)


def _dispatch(method, path, body, runtime):
    status, headers, resp = dispatch_agent_request(method, path, body, runtime)
    ct = headers.get("content-type", "")
    if isinstance(ct, bytes):
        ct = ct.decode()
    return status, json.loads(resp) if "json" in ct else resp.decode()


def test_web_root_returns_html(tmp_path):
    runtime = _runtime(tmp_path)
    status, headers, resp = dispatch_agent_request("GET", "/", b"", runtime)
    assert status == 200


def test_web_dashboard_startup_message_is_ascii_safe():
    message = _dashboard_startup_message("本机", 8766)

    message.encode("ascii")
    assert message == "[ansys-agent] dashboard: http://??:8766"


def test_web_dashboard_startup_message_can_include_db_path(tmp_path):
    message = _dashboard_startup_message("127.0.0.1", 8766, tmp_path / "missions.db")

    assert "http://127.0.0.1:8766" in message
    assert "db=" in message


def test_web_list_missions_empty(tmp_path):
    runtime = _runtime(tmp_path)
    _, data = _dispatch("GET", "/api/missions", b"", runtime)
    assert data["missions"] == []


def test_web_system_status_reports_connected_db_and_counts(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.create_mission("visible in dashboard", [], [])

    status, data = _dispatch("GET", "/api/system", b"", runtime)

    assert status == 200
    assert data["db_exists"] is True
    assert data["db_path"].endswith("m.db")
    assert data["counts"]["missions"] == 1
    assert data["counts"]["graph_runs"] == 0


def test_web_monitor_all_page_contains_dag_node_renderer():
    assert "Graph Monitor" in AGENT_PAGE
    assert "monitor-board" in AGENT_PAGE
    assert "monitor-counts" in AGENT_PAGE
    assert "node-flow" in AGENT_PAGE
    assert "flow-arrow" in AGENT_PAGE
    assert "nodeSheen" in AGENT_PAGE
    assert "renderMonitorNodes" in AGENT_PAGE
    assert "graph_nodes" in AGENT_PAGE
    assert "DB: checking" in AGENT_PAGE


def test_web_list_templates(tmp_path):
    runtime = _runtime(tmp_path)
    _, data = _dispatch("GET", "/api/templates", b"", runtime)
    assert isinstance(data["templates"], list)


def test_web_create_mission_and_get(tmp_path):
    runtime = _runtime(tmp_path)
    _, data = _dispatch("POST", "/api/missions", json.dumps({
        "goal": "test", "template_id": "brd_local_cut_build",
        "signal_nets": ["CLK0"], "bbox": "0,0,10,10",
    }).encode(), runtime)
    assert "mission_id" in data

    mission_id = data["mission_id"]
    _, data = _dispatch("GET", f"/api/missions/{mission_id}", b"", runtime)
    assert data["mission"]["user_goal"] == "test"


def test_web_approvals_list(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("test", [], [])
    approval = ApprovalService(runtime.store).request_approval(
        mission.mission_id,
        "需要审批",
        [{"id": "approve", "label": "批准"}],
    )

    status, data = _dispatch("GET", f"/api/missions/{mission.mission_id}/approvals", b"", runtime)

    assert status == 200
    assert data["approvals"][0]["approval_id"] == approval.approval_id


def test_web_decide_uses_approval_service_and_updates_mission_state(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("test", [], [])
    approval = ApprovalService(runtime.store).request_approval(
        mission.mission_id,
        "需要审批",
        [{"id": "approve", "label": "批准"}],
    )

    status, data = _dispatch(
        "POST",
        f"/api/approvals/{approval.approval_id}/decide",
        json.dumps({"decision": "approved", "option_id": "approve"}).encode(),
        runtime,
    )

    assert status == 200
    assert data["decision"] == "approved"
    assert runtime.store.get_approval(approval.approval_id).selected_option_id == "approve"
    assert runtime.get_mission(mission.mission_id).state.value == "waiting_worker"


def test_web_dashboard_summarizes_brd_loop_progress(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("optimize reviewed BRD model", [], [])
    graph_run = runtime.store.create_graph_run(
        GraphRunRecord.create(
            "graph-1",
            mission.mission_id,
            "brd_reviewed_model_optimize_loop",
            1,
            mission.plan_version,
            template_snapshot={
                "id": "brd_reviewed_model_optimize_loop",
                "version": 1,
                "nodes": [],
                "edges": [],
                "handoffs": {},
            },
        )
    )
    report_html = tmp_path / "optimization_progress.html"
    report_html.write_text("<html><body>report</body></html>", encoding="utf-8")
    tdr_plot = tmp_path / "tdr_plot.svg"
    tdr_plot.write_text("<svg></svg>", encoding="utf-8")
    history_csv = tmp_path / "optimization_history.csv"
    history_csv.write_text(
        "\n".join(
            [
                "round_index,round_status,score_status,rl_worst_db,insertion_worst_db_in_band,tdr_observation_port,tdr_peak_deviation_ohm,objective_total_cost,artifact_refs",
                "1,scored,fail,-16.8,-1.2,Diff1,10.5,42.0,",
            ]
        ),
        encoding="utf-8",
    )
    node_run = runtime.store.create_node_run(
        NodeRunRecord.create(
            "node-1",
            graph_run.graph_run_id,
            mission.mission_id,
            "score_channel",
            "worker",
            "worker",
            1,
            {},
        )
    )
    runtime.store.complete_node_run(
        node_run.node_run_id,
        NodeRunStatus.SUCCEEDED,
        {
            "loop_context": {
                "optimization_history_csv": str(history_csv),
                "report_html": str(report_html),
            },
            "score": {
                "touchstone_kind": "s4p",
                "return_loss_trace": "SDD11",
                "insertion_loss_trace": "SDD21",
                "plot_artifacts": {"tdr": str(tdr_plot)},
            },
        },
        [str(tdr_plot)],
        edge_decision="succeeded",
    )

    status, data = _dispatch("GET", f"/api/missions/{mission.mission_id}/dashboard", b"", runtime)

    assert status == 200
    assert data["node_runs"][0]["node_id"] == "score_channel"
    assert data["latest_metrics"]["rl_worst_db"] == "-16.8"
    assert data["latest_metrics"]["tdr_observation_port"] == "Diff1"
    paths = {artifact["path"] for artifact in data["artifacts"]}
    assert str(history_csv) in paths
    assert str(report_html) in paths
    assert str(tdr_plot) in paths
    assert any(artifact["view_url"] for artifact in data["artifacts"])


def test_web_dashboard_lists_template_nodes_before_node_runs(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("reviewed loop started by external orchestrator", [], [])
    runtime.store.create_graph_run(
        GraphRunRecord.create(
            "graph-1",
            mission.mission_id,
            "brd_reviewed_model_optimize_loop",
            1,
            mission.plan_version,
            template_snapshot={
                "id": "brd_reviewed_model_optimize_loop",
                "version": 1,
                "nodes": [
                    {
                        "id": "prepare_working_project",
                        "role": "prepare",
                        "kind": "program",
                    },
                    {
                        "id": "real_solve_worker",
                        "role": "worker",
                        "kind": "worker",
                        "capability": "brd.local_cut.solve",
                    },
                ],
                "edges": [],
                "handoffs": {},
            },
        )
    )

    status, data = _dispatch("GET", f"/api/missions/{mission.mission_id}/dashboard", b"", runtime)

    assert status == 200
    assert data["node_runs"] == []
    assert [node["node_id"] for node in data["graph_nodes"]] == [
        "prepare_working_project",
        "real_solve_worker",
    ]
    assert [node["status"] for node in data["graph_nodes"]] == ["pending", "pending"]
    assert data["graph_nodes"][1]["capability"] == "brd.local_cut.solve"


def test_web_serves_registered_artifact_file(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("artifact view", [], [])
    graph_run = runtime.store.create_graph_run(
        GraphRunRecord.create(
            "graph-1",
            mission.mission_id,
            "brd_reviewed_model_optimize_loop",
            1,
            mission.plan_version,
            template_snapshot={
                "id": "brd_reviewed_model_optimize_loop",
                "version": 1,
                "nodes": [],
                "edges": [],
                "handoffs": {},
            },
        )
    )
    report_html = tmp_path / "optimization_progress.html"
    report_html.write_text("<html><body>report</body></html>", encoding="utf-8")
    node_run = runtime.store.create_node_run(
        NodeRunRecord.create(
            "node-1",
            graph_run.graph_run_id,
            mission.mission_id,
            "progress_report",
            "worker",
            "worker",
            1,
            {},
        )
    )
    runtime.store.complete_node_run(
        node_run.node_run_id,
        NodeRunStatus.SUCCEEDED,
        {"report_html": str(report_html)},
        [str(report_html)],
    )

    status, headers, body = dispatch_agent_request(
        "GET",
        f"/api/artifacts/file?mission_id={mission.mission_id}&path={quote(str(report_html), safe='')}",
        b"",
        runtime,
    )

    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert b"report" in body


def test_web_orchestrator_stops_at_approval_without_auto_approving(tmp_path):
    runtime = _runtime(tmp_path)
    template_path = tmp_path / "approval_graph.yaml"
    template_path.write_text(
        """
id: approval_graph
version: 1
nodes:
  - id: planner
    role: planner
    kind: llm
  - id: approval_gate
    role: approval_gate
    kind: human_gate
edges:
  - id: planner-approval
    from: planner
    to: approval_gate
    on: succeeded
handoffs: {}
""",
        encoding="utf-8",
    )

    status, data = _dispatch(
        "POST",
        "/api/orchestrate",
        json.dumps(
            {
                "goal": "review BRD local cut",
                "template_id": str(template_path),
                "layout_file": str(tmp_path / "case.brd"),
                "signal_nets": ["CLK0"],
            }
        ).encode(),
        runtime,
    )

    assert status == 201
    session_id = data["session_id"]
    for _ in range(30):
        time.sleep(0.05)
        _, session = _dispatch(
            "GET",
            f"/api/orchestrate-status/{session_id}",
            b"",
            runtime,
        )
        if session["current_status"] == "waiting_approval":
            break
    else:
        raise AssertionError(session)

    approvals = runtime.store.list_approvals(session["mission_id"])
    assert approvals
    assert approvals[-1].decision.value == "pending"
    assert session["running"] is False
    assert graph_status(runtime, session["graph_run_id"])["status"] == "waiting_approval"


def test_web_orchestrator_stops_on_failure_without_silent_takeover(tmp_path):
    runtime = _runtime(tmp_path)
    template_path = tmp_path / "failing_graph.yaml"
    template_path.write_text(
        """
id: failing_graph
version: 1
nodes:
  - id: validator
    role: validator
    kind: program
    input_schema: required_input
    output_schema: required_input
edges: []
handoffs:
  required_input:
    required_fields: [missing]
""",
        encoding="utf-8",
    )

    status, data = _dispatch(
        "POST",
        "/api/orchestrate",
        json.dumps(
            {
                "goal": "must fail",
                "template_id": str(template_path),
            }
        ).encode(),
        runtime,
    )

    assert status == 201
    session_id = data["session_id"]
    for _ in range(30):
        time.sleep(0.05)
        _, session = _dispatch(
            "GET",
            f"/api/orchestrate-status/{session_id}",
            b"",
            runtime,
        )
        if session["current_status"] == "failed":
            break
    else:
        raise AssertionError(session)

    assert session["running"] is False
    assert len(runtime.store.list_graph_runs(session["mission_id"])) == 1
