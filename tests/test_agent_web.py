from __future__ import annotations

import json
import time
from pathlib import Path

from aedt_agent.agent.web import dispatch_agent_request
from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_runner import graph_status
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


def test_web_list_missions_empty(tmp_path):
    runtime = _runtime(tmp_path)
    _, data = _dispatch("GET", "/api/missions", b"", runtime)
    assert data["missions"] == []


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
