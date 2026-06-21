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
        return not_started_case_status(case)

    report = graph_status(runtime, selected_graph_id)
    mission_id = str((report.get("graph_run") or {}).get("mission_id") or "")
    return summarize_graph_report(
        case,
        report,
        pending_approvals=_pending_approvals(runtime, mission_id),
    )


def not_started_case_status(case: PiAgentCase) -> dict[str, Any]:
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
        "recommended_command": _recommended_command(case, "not_started", ""),
        "available_commands": _available_commands(case, "not_started", ""),
        "dashboard_url": _dashboard_url(case),
        "artifacts": _expected_report_artifacts_from_config(case.loop_config),
        "latest_artifacts": [],
        "pending_approvals": [],
        "failure": {},
        "graph": {},
        "error": {},
        "approval": {},
    }


def summarize_graph_report(
    case: PiAgentCase,
    report: dict[str, Any],
    *,
    pending_approvals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    graph_run = dict(report.get("graph_run") or {})
    mission_id = str(graph_run.get("mission_id") or "")
    node_runs = list(report.get("node_runs") or [])
    progress = _progress_from_node_runs(node_runs)
    history_rows = progress.get("history_rows") or []
    latest_row = history_rows[-1] if history_rows else {}
    latest_node = _latest_relevant_node(node_runs)
    metrics = _metrics_from_row_or_nodes(latest_row, node_runs)
    status = str(report.get("status") or graph_run.get("status") or "unknown")
    graph_run_id = str(graph_run.get("graph_run_id") or "")
    approvals = list(pending_approvals or [])
    if not approvals:
        node_approval = _latest_approval(node_runs)
        approvals = [node_approval] if node_approval else []
    artifacts = {
        **_expected_report_artifacts_from_config(case.loop_config),
        **{key: value for key, value in progress.items() if key != "history_rows"},
    }
    latest_artifacts = _latest_artifacts(node_runs, artifacts)
    failure = _failure_summary(graph_run, node_runs)
    return {
        "case_id": case.case_id,
        "status": status,
        "mission_id": mission_id,
        "graph_run_id": graph_run_id,
        "active_node": str(graph_run.get("current_node_id") or latest_node.get("node_id") or ""),
        "latest_round": metrics.get("round_index") or "",
        "latest_action": latest_row.get("action_type") or _find_nested_value(
            latest_node.get("output_payload") or {},
            "action_type",
        ) or "",
        "metrics": metrics,
        "next_safe_action": _next_safe_action(status),
        "recommended_command": _recommended_command(
            case,
            status,
            graph_run_id,
            approvals=approvals,
        ),
        "available_commands": _available_commands(
            case,
            status,
            graph_run_id,
            approvals=approvals,
        ),
        "dashboard_url": _dashboard_url(case),
        "artifacts": artifacts,
        "latest_artifacts": latest_artifacts,
        "pending_approvals": approvals,
        "approval": approvals[0] if approvals else {},
        "failure": failure,
        "error": graph_run.get("error") or {},
        "graph": {
            "step_count": graph_run.get("step_count"),
            "max_steps": graph_run.get("max_steps"),
            "template_id": graph_run.get("template_id"),
        },
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


def _pending_approvals(runtime: AgentRuntime, mission_id: str) -> list[dict[str, Any]]:
    if not mission_id:
        return []
    approvals = []
    for approval in runtime.store.list_approvals(mission_id):
        payload = approval.to_json_dict()
        if payload.get("decision") == "pending":
            approvals.append(
                {
                    "approval_id": payload.get("approval_id", ""),
                    "mission_id": payload.get("mission_id", ""),
                    "reason": payload.get("reason", ""),
                    "options": payload.get("options", []),
                    "created_at": payload.get("created_at", ""),
                }
            )
    return approvals


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
            "reason": payload.get("approval_reason")
            or payload.get("reason")
            or "",
            "options": payload.get("options", []),
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


def _recommended_command(
    case: PiAgentCase,
    status: str,
    graph_run_id: str,
    *,
    approvals: list[dict[str, Any]] | None = None,
) -> str:
    case_arg = _case_arg(case)
    if status == "not_started":
        return f"python -m aedt_agent.pi_agent preflight --case {case_arg}"
    if status == "running":
        return f"python -m aedt_agent.pi_agent status --case {case_arg}"
    if status == "waiting_approval":
        return f"python -m aedt_agent.pi_agent status --case {case_arg}"
    if status == "failed":
        return f"python -m aedt_agent.pi_agent status --case {case_arg}"
    if status == "canceled":
        return f"python -m aedt_agent.pi_agent status --case {case_arg}"
    if status == "succeeded":
        return f"python -m aedt_agent.pi_agent status --case {case_arg}"
    if graph_run_id:
        return (
            "python -m aedt_agent.pi_agent resume "
            f"--case {case_arg} --graph-run-id {_shell_arg(graph_run_id)}"
        )
    return f"python -m aedt_agent.pi_agent status --case {case_arg}"


def _available_commands(
    case: PiAgentCase,
    status: str,
    graph_run_id: str,
    *,
    approvals: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    case_arg = _case_arg(case)
    commands = {
        "status": f"python -m aedt_agent.pi_agent status --case {case_arg}",
        "web": f"python -m aedt_agent.pi_agent web --case {case_arg}",
    }
    if status == "not_started":
        commands["preflight"] = f"python -m aedt_agent.pi_agent preflight --case {case_arg}"
        commands["run"] = f"python -m aedt_agent.pi_agent run --case {case_arg}"
        return commands
    if status == "waiting_approval":
        approval_id = ""
        if approvals:
            approval_id = str(approvals[0].get("approval_id") or "")
        if approval_id:
            graph_arg = f" --graph-run-id {_shell_arg(graph_run_id)}" if graph_run_id else ""
            commands["approve"] = (
                "python -m aedt_agent.pi_agent approve "
                f"--case {case_arg} --approval-id {_shell_arg(approval_id)} --option-id approve"
            )
            commands["approve_and_resume"] = (
                "python -m aedt_agent.pi_agent approve "
                f"--case {case_arg} --approval-id {_shell_arg(approval_id)} "
                f"--option-id approve --resume{graph_arg}"
            )
            commands["reject"] = (
                "python -m aedt_agent.pi_agent reject "
                f"--case {case_arg} --approval-id {_shell_arg(approval_id)}"
            )
        if graph_run_id:
            commands["resume_after_decision"] = (
                "python -m aedt_agent.pi_agent resume "
                f"--case {case_arg} --graph-run-id {_shell_arg(graph_run_id)}"
            )
        return commands
    if status not in {"succeeded", "failed", "canceled"} and graph_run_id:
        commands["resume"] = (
            "python -m aedt_agent.pi_agent resume "
            f"--case {case_arg} --graph-run-id {_shell_arg(graph_run_id)}"
        )
    if status not in {"succeeded", "canceled"} and graph_run_id:
        commands["stop"] = (
            "python -m aedt_agent.pi_agent stop "
            f"--case {case_arg} --graph-run-id {_shell_arg(graph_run_id)}"
        )
    return commands


def _dashboard_url(case: PiAgentCase) -> str:
    host = case.dashboard_host
    if host in {"", "0.0.0.0", "::"}:
        host = "localhost"
    return f"http://{host}:{case.dashboard_port}"


def _case_arg(case: PiAgentCase) -> str:
    if case.source_path is not None:
        return _shell_arg(str(case.source_path))
    return "<case.json>"


def _shell_arg(value: str) -> str:
    text = str(value)
    if not text:
        return '""'
    special_chars = set(' \t\r\n"&()[]{};')
    if not any(char in special_chars for char in text):
        return text
    return '"' + text.replace("`", "``").replace('"', '`"') + '"'


def _failure_summary(
    graph_run: dict[str, Any],
    node_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    graph_error = graph_run.get("error") or {}
    failed_nodes = [
        {
            "node_id": item.get("node_id"),
            "sequence": item.get("sequence"),
            "error": item.get("error") or {},
        }
        for item in node_runs
        if item.get("status") == "failed"
    ]
    if not graph_error and not failed_nodes:
        return {}
    return {
        "graph_error": graph_error,
        "failed_nodes": failed_nodes,
    }


def _latest_artifacts(
    node_runs: list[dict[str, Any]],
    report_artifacts: dict[str, str],
) -> list[dict[str, Any]]:
    paths: list[str] = []
    for path in report_artifacts.values():
        if path:
            paths.append(str(path))
    for node_run in node_runs:
        paths.extend(str(path) for path in node_run.get("artifact_refs") or [])
        paths.extend(_artifact_paths_from_payload(node_run.get("output_payload") or {}))
    return [
        {
            "path": path,
            "kind": _artifact_kind(path),
            "exists": Path(path).is_file(),
        }
        for path in _unique_strings(paths)
    ]


def _artifact_paths_from_payload(value: Any, *, field_name: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            key_text = str(key)
            key_lower = key_text.casefold()
            if (
                key_lower in {
                    "artifact_refs",
                    "plot_artifacts",
                    "optimization_history_csv",
                    "report_html",
                    "report_json",
                    "touchstone_path",
                    "tdr_path",
                    "edit_manifest_path",
                    "solve_manifest_path",
                    "score_evidence_path",
                }
                or key_lower.endswith("_path")
                or "artifact" in key_lower
            ):
                paths.extend(_coerce_artifact_strings(child))
            else:
                paths.extend(_artifact_paths_from_payload(child, field_name=key_text))
        return paths
    if isinstance(value, list):
        paths: list[str] = []
        for child in value:
            paths.extend(_artifact_paths_from_payload(child, field_name=field_name))
        return paths
    if field_name.casefold() in {"artifact_refs", "optimization_history_csv", "report_html", "report_json"}:
        return _coerce_artifact_strings(value)
    return []


def _coerce_artifact_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        paths: list[str] = []
        for child in value:
            paths.extend(_coerce_artifact_strings(child))
        return paths
    if isinstance(value, dict):
        paths: list[str] = []
        for child in value.values():
            paths.extend(_coerce_artifact_strings(child))
        return paths
    return []


def _artifact_kind(path: str) -> str:
    name = Path(path).name.casefold()
    suffix = Path(path).suffix.casefold()
    if suffix in {".s4p", ".s2p", ".snp"}:
        return "touchstone"
    if suffix == ".csv" and "history" in name:
        return "history_csv"
    if suffix == ".csv" and "tdr" in name:
        return "tdr_csv"
    if suffix == ".html":
        return "report_html"
    if suffix in {".svg", ".png", ".jpg", ".jpeg"}:
        return "plot"
    if suffix == ".json" and "evidence" in name:
        return "score_evidence"
    if suffix == ".json" and "manifest" in name:
        return "manifest"
    if suffix == ".aedt":
        return "aedt_model"
    return "artifact"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


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
