from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aedt_agent.agent.graph_runner import graph_status
from aedt_agent.agent.orchestrator import AgentRuntime


def workbench_root() -> Path:
    return Path(__file__).resolve().parents[3] / "integrations" / "si-workbench"


def workbench_static(path: str) -> tuple[int, dict[str, str], bytes]:
    name = "index.html" if path in {"", "/"} else path.lstrip("/")
    if name not in {"index.html", "styles.css", "app.js"}:
        return 404, {"content-type": "text/plain; charset=utf-8"}, b"not found"
    file_path = workbench_root() / name
    if not file_path.is_file():
        return 404, {"content-type": "text/plain; charset=utf-8"}, b"not found"
    content_type = {"index.html": "text/html; charset=utf-8", "styles.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8"}[name]
    return 200, {"content-type": content_type}, file_path.read_bytes()


def workbench_missions(runtime: AgentRuntime) -> dict[str, Any]:
    records = []
    for mission in runtime.store.list_missions():
        runs = runtime.store.list_graph_runs(mission.mission_id)
        records.append({"mission_id": mission.mission_id, "goal": mission.user_goal, "state": mission.state.value, "created_at": mission.created_at, "graph_run_id": runs[-1].graph_run_id if runs else "", "graph_status": runs[-1].status.value if runs else ""})
    return {"missions": records, "mode": "mission_readonly"}


def workbench_snapshot(runtime: AgentRuntime, graph_run_id: str) -> dict[str, Any]:
    runtime.store.get_graph_run(graph_run_id)
    status = graph_status(runtime, graph_run_id)
    mission_id = str(status["mission_id"])
    mission = runtime.get_mission(mission_id)
    graph_run = status["graph_run"]
    node_runs = status.get("node_runs") or []
    payload = graph_run.get("initial_payload") or {}
    context = _latest_loop_context(node_runs, payload)
    rounds = _load_rounds(context)
    latest = rounds[-1] if rounds else _latest_metrics(node_runs)
    return {
        "schema": "si-forge-workbench/v1", "mode": "mission_readonly", "read_only": True, "demo": False,
        "mission": {"mission_id": mission_id, "goal": mission.user_goal, "state": mission.state.value},
        "graph": {"graph_run_id": graph_run_id, "template_id": status.get("template_id"), "status": status.get("status"), "current_node_id": graph_run.get("current_node_id"), "step_count": graph_run.get("step_count"), "max_steps": graph_run.get("max_steps")},
        "source": {"source_integrity": "reported_not_reverified", "source_sha256_verified": False, "source_read_only": None, "source_read_only_verified": False, "source_project_path": context.get("source_project_path") or context.get("project_path"), "working_project_path": context.get("working_project_path") or context.get("latest_project_path"), "source_sha256": context.get("source_project_sha256") or context.get("source_sha256"), "source_read_only": True, "working_isolated": bool(context.get("working_project_path")), "report_dir": context.get("report_dir"), "run_root": context.get("run_root"), "setup_name": context.get("setup_name"), "sweep_name": context.get("sweep_name")},
        "metrics": _metrics(latest),
        "rounds": [_round_summary(row) for row in rounds],
        "model": _model(context, latest),
        "constraints": _constraints(context, latest),
        "artifacts": _safe_artifacts(_artifacts(runtime, mission_id)),
        "nodes": [{"node_id": r.get("node_id"), "status": r.get("status"), "edge_decision": r.get("edge_decision"), "error": r.get("error")} for r in node_runs],
        "capabilities": {"geometry_preview": False, "workflow_start": False, "workflow_advance": False, "human_gate_approve": False, "artifact_read": True, "production_write": False},
    }


def _latest_loop_context(node_runs: list[dict[str, Any]], initial: dict[str, Any]) -> dict[str, Any]:
    result = dict(initial)
    result.update(dict(initial.get("loop_context") or {}))
    for run in node_runs:
        context = (run.get("output_payload") or {}).get("loop_context")
        if isinstance(context, dict) and context:
            result.update(context)
    return result


def _load_rounds(context: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("report_json", "optimization_history_csv"):
        value = context.get(key)
        if not value:
            continue
        path = Path(str(value))
        if not path.is_file():
            continue
        if path.suffix.casefold() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rounds = payload.get("rounds")
            if isinstance(rounds, list):
                return [row for row in rounds if isinstance(row, dict)]
        if path.suffix.casefold() == ".csv":
            from aedt_agent.agent.optimization_handlers import read_history_csv
            try:
                return read_history_csv(path, limit=50)
            except (OSError, ValueError):
                continue
    return []


def _latest_metrics(node_runs: list[dict[str, Any]]) -> dict[str, Any]:
    for run in reversed(node_runs):
        score = (run.get("output_payload") or {}).get("score")
        if isinstance(score, dict) and score:
            return dict(score)
    return {}


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    objective = row.get("optimization_objective") or {}
    if not isinstance(objective, dict):
        objective = {}
    return {"round_index": row.get("round_index"), "round_status": row.get("round_status") or row.get("status"), "score_status": row.get("score_status"), "return_loss_trace": row.get("return_loss_trace"), "rl_worst_db": row.get("rl_worst_db"), "rl_worst_frequency_ghz": row.get("rl_worst_frequency_ghz"), "insertion_loss_trace": row.get("insertion_loss_trace"), "insertion_worst_db_in_band": row.get("insertion_worst_db_in_band"), "insertion_worst_frequency_ghz": row.get("insertion_worst_frequency_ghz"), "tdr_observation_port": row.get("tdr_observation_port"), "tdr_peak_deviation_ohm": row.get("tdr_peak_deviation_ohm"), "tdr_peak_time_ps": row.get("tdr_peak_time_ps"), "objective_total_cost": row.get("objective_total_cost") or objective.get("total_cost"), "pass_fail_reason": row.get("pass_fail_reason"), "continue_recommendation": row.get("continue_recommendation"), "plot_artifacts": row.get("plot_artifacts") or {}, "touchstone_kind": row.get("touchstone_kind")}


def _constraints(context: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    geometry = context.get("geometry_constraints") or {}
    anti = geometry.get("anti_pad") or {}
    nfp = geometry.get("non_functional_pad") or {}
    return {"anti_pad_max_radius_mil": anti.get("max_radius_mil"), "nfp_min_radius_mil": nfp.get("min_radius_mil"), "nfp_max_radius_mil": nfp.get("max_radius_mil"), "reference_impedance_ohm": context.get("reference_impedance_ohm") or context.get("tdr_reference_impedance_ohm"), "frequency_start_ghz": context.get("frequency_start_ghz"), "frequency_stop_ghz": context.get("frequency_stop_ghz"), "tdr_observation_port": context.get("tdr_observation_port") or metrics.get("tdr_observation_port"), "tdr_orientation_evidence": context.get("tdr_orientation_evidence") or context.get("orientation_evidence")}


def _model(context: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    inventory = context.get("model_inventory") or context.get("geometry_inventory") or {}
    return {"available": bool(inventory), "provenance": "aedb_inventory" if inventory else "unavailable", "notice": "" if inventory else "当前 graph/report 没有模型几何 inventory。仅显示指标与报告，不伪造物理结构。", "inventory": inventory, "selected_via": context.get("selected_via") or {}, "changes": metrics.get("changes") or []}


def _round_summary(row: dict[str, Any]) -> dict[str, Any]:
    edit = row.get("edit_digest") or {}
    return {"round_index": row.get("round_index"), "status": row.get("status") or row.get("round_status"), "action_type": row.get("action_type") or edit.get("action_type"), "layers": row.get("layers") or edit.get("layers"), "target_radius": row.get("target_radius") or edit.get("target_radius"), "objective_total_cost": row.get("objective_total_cost"), "rl_worst_db": row.get("rl_worst_db"), "tdr_peak_deviation_ohm": row.get("tdr_peak_deviation_ohm"), "solve_status": row.get("solve_status"), "score_status": row.get("score_status"), "continue_recommendation": row.get("continue_recommendation"), "evidence_path": row.get("evidence_path") or row.get("score_evidence_path")}


_SAFE_ARTIFACT_KINDS = {
    "report_html",
    "history_csv",
    "score_evidence",
    "plot",
    "plot_tdr",
    "plot_sdd11",
    "plot_sdd21",
    # The workbench's own smoke solve exports a raw touchstone; it is a
    # read-only artifact, never a project file, so it stays visible.
    "touchstone",
}
_DENIED_SUFFIXES = {".aedt", ".aedb", ".edb", ".brd", ".aedtz", ".tgz", ".zip"}


def _safe_artifacts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe = []
    for record in records:
        kind = str(record.get("kind") or "")
        path = Path(str(record.get("path") or ""))
        if kind not in _SAFE_ARTIFACT_KINDS:
            continue
        if path.suffix.casefold() in _DENIED_SUFFIXES:
            continue
        suffix = path.suffix.casefold()
        allowed = {".html", ".csv", ".json", ".svg", ".png", ".jpg", ".jpeg", ".txt"}
        # Touchstones are read-only artifacts too (.s1p from a single-point
        # smoke run, .s2p..sNp from a channel export).
        touchstone = bool(re.fullmatch(r"\.s[1-9]\d*p", suffix))
        if suffix not in allowed and not touchstone:
            continue
        safe.append(record)
    return safe


def _artifacts(runtime: AgentRuntime, mission_id: str) -> list[dict[str, Any]]:
    from aedt_agent.agent.web import _collect_mission_artifacts
    return _collect_mission_artifacts(runtime, mission_id)
