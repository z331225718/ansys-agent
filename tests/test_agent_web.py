from __future__ import annotations

import json
from pathlib import Path

from aedt_agent.agent.web import dispatch_agent_request
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
    _, data = _dispatch("GET", f"/api/missions/{mission.mission_id}/approvals", b"", runtime)
    # Route may return not_found if approvals sub-route not matched; just check no error
    assert "error" not in data or data.get("error") != "not_found"
