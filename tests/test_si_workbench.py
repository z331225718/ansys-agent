from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent.graph_runner import graph_status
from aedt_agent.agent.mission import GraphRunRecord, NodeRunRecord, NodeRunStatus
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.web import dispatch_agent_request
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path) -> AgentRuntime:
    return AgentRuntime(SQLiteMissionStore(tmp_path / "missions.db"))


def _dispatch(method, path, runtime):
    status, headers, body = dispatch_agent_request(method, path, b"", runtime)
    ct = headers.get("content-type", "")
    return status, ct, json.loads(body) if "json" in ct else body.decode()


def _fixture(tmp_path: Path, inventory: dict | None = None):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("TX0 to RX0 reviewed differential via", [], [])
    graph = runtime.store.create_graph_run(GraphRunRecord.create(
        "graph-si-1", mission.mission_id, "brd_reviewed_model_optimize_loop", 1, mission.plan_version,
        initial_payload={
            "source_project_path": str(tmp_path / "reviewed.aedt"),
            "working_project_path": str(tmp_path / "working.aedt"),
            "reference_impedance_ohm": 90.0,
            "frequency_start_ghz": 0.0,
            "frequency_stop_ghz": 28.0,
            "tdr_observation_port": "Diff1",
            "geometry_constraints": {"anti_pad": {"max_radius_mil": 22}, "non_functional_pad": {"min_radius_mil": 7.875, "max_radius_mil": 10}},
            **({"model_inventory": inventory} if inventory else {}),
        },
        template_snapshot={"id": "brd_reviewed_model_optimize_loop", "version": 1, "nodes": [], "edges": [], "handoffs": {}},
    ))
    report_html = tmp_path / "optimization_progress.html"
    report_html.write_text("<html><body>real report</body></html>", encoding="utf-8")
    report_json = tmp_path / "optimization_progress.json"
    report_json.write_text(json.dumps({"rounds": [{"round_index": 1, "status": "scored", "solve_status": "succeeded", "score_status": "fail", "return_loss_trace": "SDD11", "rl_worst_db": -16.8, "rl_worst_frequency_ghz": 18.0, "insertion_loss_trace": "SDD21", "insertion_worst_db_in_band": -1.2, "tdr_observation_port": "Diff1", "tdr_peak_deviation_ohm": 10.5, "tdr_peak_time_ps": 20.0, "objective_total_cost": 42.0, "plot_artifacts": {}}]}), encoding="utf-8")
    history = tmp_path / "optimization_history.csv"
    history.write_text("round_index,round_status,score_status,rl_worst_db,objective_total_cost\n1,scored,fail,-16.8,42.0\n", encoding="utf-8")
    source_project = tmp_path / "source.aedt"
    source_project.write_text("source", encoding="utf-8")
    node = runtime.store.create_node_run(NodeRunRecord.create("node-si-1", graph.graph_run_id, mission.mission_id, "channel_score", "worker", "worker", 1, {}))
    runtime.store.complete_node_run(node.node_run_id, NodeRunStatus.SUCCEEDED, {"loop_context": {"source_project_path": str(tmp_path / "reviewed.aedt"), "working_project_path": str(tmp_path / "working.aedt"), "source_project_sha256": "source-hash", "optimization_history_csv": str(history), "report_json": str(report_json), "report_html": str(report_html), "report_dir": str(tmp_path)}, "score": {"status": "fail"}}, [str(report_html), str(report_json), str(history), str(source_project)], edge_decision="succeeded")
    return runtime, mission, graph


def test_workbench_page_and_assets_are_served(tmp_path):
    runtime = _runtime(tmp_path)
    status, headers, _ = dispatch_agent_request("GET", "/workbench", b"", runtime)
    assert status == 302 and headers["location"] == "/workbench/"
    status, ct, body = _dispatch("GET", "/workbench/", runtime)
    assert status == 200 and "text/html" in ct and "SI Forge" in body
    status, ct, _ = _dispatch("GET", "/workbench/app.js", runtime)
    assert status == 200 and "javascript" in ct


def test_workbench_snapshot_merges_real_metrics_and_initial_constraints(tmp_path):
    runtime, mission, graph = _fixture(tmp_path)
    status, _, data = _dispatch("GET", "/api/workbench/graphs/graph-si-1", runtime)
    assert status == 200
    assert data["mode"] == "mission_readonly" and data["read_only"] is True and data["demo"] is False
    assert data["metrics"]["rl_worst_db"] == -16.8
    assert data["metrics"]["insertion_worst_db_in_band"] == -1.2
    assert data["metrics"]["tdr_peak_deviation_ohm"] == 10.5
    assert data["metrics"]["objective_total_cost"] == 42.0
    assert data["rounds"][0]["status"] == "scored"
    assert data["constraints"]["anti_pad_max_radius_mil"] == 22
    assert data["constraints"]["reference_impedance_ohm"] == 90.0
    assert data["source"]["source_sha256"] == "source-hash"
    assert data["model"]["available"] is False and "不伪造物理结构" in data["model"]["notice"]
    assert data["capabilities"]["workflow_start"] is False
    assert data["capabilities"]["human_gate_approve"] is False
    assert data["capabilities"]["production_write"] is False


def test_workbench_artifacts_keep_dashboard_permissions(tmp_path):
    runtime, mission, graph = _fixture(tmp_path)
    _, _, data = _dispatch("GET", "/api/workbench/graphs/graph-si-1", runtime)
    kinds = {a["kind"] for a in data["artifacts"]}
    assert {"report_html", "history_csv"} <= kinds
    report = tmp_path / "optimization_progress.html"
    status, _, body = dispatch_agent_request("GET", f"/api/artifacts/file?mission_id={mission.mission_id}&path={report}", b"", runtime)
    assert status == 200 and b"real report" in body
    status, _, _ = dispatch_agent_request("GET", f"/api/artifacts/file?mission_id={mission.mission_id}&path={tmp_path / 'unregistered.txt'}", b"", runtime)
    assert status == 403


def test_workbench_is_read_only(tmp_path):
    runtime, mission, graph = _fixture(tmp_path)
    before = graph_status(runtime, "graph-si-1")
    _dispatch("GET", "/api/workbench/graphs/graph-si-1", runtime)
    after = graph_status(runtime, "graph-si-1")
    assert after["graph_run"]["step_count"] == before["graph_run"]["step_count"]
    assert len(after["node_runs"]) == len(before["node_runs"])
    assert len(after["handoffs"]) == len(before["handoffs"])


def test_workbench_mission_list_and_missing_graph(tmp_path):
    runtime, mission, graph = _fixture(tmp_path)
    _, _, data = _dispatch("GET", "/api/workbench/missions", runtime)
    assert data["missions"][0]["graph_run_id"] == "graph-si-1"
    status, _, data = _dispatch("GET", "/api/workbench/graphs/not-a-graph", runtime)
    assert status == 400 and data["error"] == "KeyError"


def test_workbench_never_exposes_source_project_artifacts(tmp_path):
    runtime, mission, graph = _fixture(tmp_path)
    _, _, data = _dispatch("GET", "/api/workbench/graphs/graph-si-1", runtime)
    paths = {a["path"] for a in data["artifacts"]}
    assert not any(path.casefold().endswith((".aedt", ".aedb", ".edb", ".brd")) for path in paths)
    assert "approvals" not in data


def test_workbench_model_inventory_is_projected_as_real_data(tmp_path):
    runtime, mission, graph = _fixture(tmp_path, inventory={"vias": [{"name": "via_1", "layer": "L2_GND"}], "provenance": "aedb_read"})
    _, _, data = _dispatch("GET", "/api/workbench/graphs/graph-si-1", runtime)
    assert data["model"]["available"] is True
    assert data["model"]["inventory"]["vias"][0]["name"] == "via_1"


def test_workbench_source_integrity_is_reported_not_verified(tmp_path):
    runtime, mission, graph = _fixture(tmp_path)
    _, _, data = _dispatch("GET", "/api/workbench/graphs/graph-si-1", runtime)
    assert data["source"]["source_integrity"] == "reported_not_reverified"
    assert data["source"]["source_sha256_verified"] is False
