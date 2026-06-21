from __future__ import annotations

from pathlib import Path
from typing import Any

from aedt_agent.agent.graph_runner import graph_status
from aedt_agent.agent.optimization_handlers import read_history_csv
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.pi_agent.case_config import PiAgentCase


METRIC_KEYS = (
    "round_index",
    "round_status",
    "score_status",
    "touchstone_kind",
    "return_loss_trace",
    "insertion_loss_trace",
    "rl_worst_db",
    "sdd11_worst_db",
    "insertion_worst_db_in_band",
    "sdd21_worst_db_in_band",
    "tdr_observation_port",
    "tdr_peak_deviation_ohm",
    "tdr_proximity_rmse_ohm",
    "tdr_flatness_rms_step_ohm",
    "objective_total_cost",
    "pass_fail_reason",
    "continue_recommendation",
)


def build_case_status(
    case: PiAgentCase,
    *,
    runtime: AgentRuntime | None = None,
    graph_run_id: str = "",
) -> dict[str, Any]:
    runtime = runtime or AgentRuntime(SQLiteMissionStore(case.db_path))
    selected_graph_id = graph_run_id or case.graph_run_id or latest_graph_run_id(runtime)
    if not selected_graph_id:
        return {
            "case_id": case.case_id,
            "status": "not_started",
            "mission_id": case.mission_id,
            "graph_run_id": "",
            "active_node": "",
            "latest_round": "",
            "latest_action": "",
            "metrics": _empty_metrics(),
            "next_safe_action": "preflight",
            "artifacts": _expected_report_artifacts_from_config(case.loop_config),
        }

    report = graph_status(runtime, selected_graph_id)
    return summarize_graph_report(case, report)


def summarize_graph_report(case: PiAgentCase, report: dict[str, Any]) -> dict[str, Any]:
    graph_run = dict(report.get("graph_run") or {})
    mission_id = str(graph_run.get("mission_id") or "")
    node_runs = list(report.get("node_runs") or [])
    progress = _progress_from_node_runs(node_runs)
    history_rows = progress.get("history_rows") or []
    latest_row = history_rows[-1] if history_rows else {}
    latest_node = _latest_relevant_node(node_runs)
    metrics = _metrics_from_row_or_nodes(latest_row, node_runs)
    status = str(report.get("status") or graph_run.get("status") or "unknown")
    return {
        "case_id": case.case_id,
        "status": status,
        "mission_id": mission_id,
        "graph_run_id": str(graph_run.get("graph_run_id") or ""),
        "active_node": str(graph_run.get("current_node_id") or latest_node.get("node_id") or ""),
        "latest_round": metrics.get("round_index") or "",
        "latest_action": latest_row.get("action_type") or _find_nested_value(
            latest_node.get("output_payload") or {},
            "action_type",
        ) or "",
        "metrics": metrics,
        "next_safe_action": _next_safe_action(status),
        "artifacts": {
            **_expected_report_artifacts_from_config(case.loop_config),
            **{key: value for key, value in progress.items() if key != "history_rows"},
        },
        "approval": _latest_approval(node_runs),
        "error": graph_run.get("error") or {},
    }


def latest_graph_run_id(runtime: AgentRuntime) -> str:
    latest: tuple[str, str] | None = None
    for mission in runtime.store.list_missions(limit=50):
        for graph_run in runtime.store.list_graph_runs(mission.mission_id):
            key = (graph_run.created_at, graph_run.graph_run_id)
            if latest is None or key > latest:
                latest = key
    return "" if latest is None else latest[1]


def _progress_from_node_runs(node_runs: list[dict[str, Any]]) -> dict[str, Any]:
    history_csv = ""
    report_html = ""
    report_json = ""
    for node_run in node_runs:
        payload = dict(node_run.get("output_payload") or {})
        loop_context = payload.get("loop_context")
        if isinstance(loop_context, dict):
            history_csv = str(loop_context.get("optimization_history_csv") or history_csv)
            report_html = str(loop_context.get("report_html") or report_html)
            report_json = str(loop_context.get("report_json") or report_json)
        history_csv = str(payload.get("optimization_history_csv") or history_csv)
        report_html = str(payload.get("report_html") or report_html)
        report_json = str(payload.get("report_json") or report_json)
    rows = read_history_csv(history_csv, limit=20) if history_csv else []
    return {
        "optimization_history_csv": history_csv,
        "report_html": report_html,
        "report_json": report_json,
        "history_rows": rows,
    }


def _expected_report_artifacts_from_config(loop_config_path: Path) -> dict[str, str]:
    try:
        import json

        payload = json.loads(loop_config_path.read_text(encoding="utf-8"))
        report_dir = Path(str(payload.get("report_dir") or ""))
    except Exception:
        report_dir = Path("")
    if not str(report_dir):
        return {
            "optimization_history_csv": "",
            "report_html": "",
            "report_json": "",
        }
    return {
        "optimization_history_csv": str(report_dir / "optimization_history.csv"),
        "report_html": str(report_dir / "optimization_progress.html"),
        "report_json": str(report_dir / "optimization_progress.json"),
    }


def _metrics_from_row_or_nodes(
    row: dict[str, Any],
    node_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    if row:
        return {key: row.get(key, "") for key in METRIC_KEYS}
    for node_run in reversed(node_runs):
        payload = node_run.get("output_payload") or {}
        values = {
            key: _find_nested_value(payload, key)
            for key in METRIC_KEYS
        }
        if any(value not in (None, "", []) for value in values.values()):
            return {
                key: "" if value is None else value
                for key, value in values.items()
            }
    return _empty_metrics()


def _empty_metrics() -> dict[str, str]:
    return {key: "" for key in METRIC_KEYS}


def _latest_relevant_node(node_runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not node_runs:
        return {}
    return sorted(node_runs, key=lambda item: int(item.get("sequence") or 0))[-1]


def _latest_approval(node_runs: list[dict[str, Any]]) -> dict[str, Any]:
    for node_run in reversed(node_runs):
        if node_run.get("status") != "waiting_approval":
            continue
        payload = dict(node_run.get("output_payload") or {})
        return {
            "node_id": node_run.get("node_id"),
            "approval_id": payload.get("approval_id", ""),
            "approval_reason": payload.get("approval_reason")
            or payload.get("reason")
            or "",
        }
    return {}


def _next_safe_action(status: str) -> str:
    return {
        "not_started": "preflight",
        "running": "wait",
        "waiting_approval": "ask_user",
        "failed": "inspect_failure",
        "canceled": "inspect_cancellation",
        "succeeded": "report",
    }.get(status, "inspect")


def _find_nested_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_nested_value(child, key)
            if found not in (None, "", []):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_nested_value(child, key)
            if found not in (None, "", []):
                return found
    return None
