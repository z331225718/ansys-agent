from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_CORE_ARTIFACTS = [
    "preflight.json",
    "params.json",
    "workflow_run.json",
    "import_cutout_summary.json",
    "stdout.log",
    "stderr.log",
    "acceptance_report.json",
    "acceptance_report.html",
]


def build_brd_acceptance_summary(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    preflight = _read_json(run_dir / "preflight.json")
    params = _read_json(run_dir / "params.json")
    workflow = _read_json(run_dir / "workflow_run.json")
    cutout = _read_json(run_dir / "import_cutout_summary.json")

    outputs = workflow.get("outputs", {}) if isinstance(workflow.get("outputs"), dict) else {}
    port_actions = _port_actions(cutout)
    blocking_issues = _blocking_issues(preflight, workflow)
    warnings = _warnings(preflight)
    artifacts = _artifacts(run_dir, workflow, cutout)
    status = "failed" if blocking_issues or workflow.get("status") == "failed" or cutout.get("status") == "failed" else "succeeded"

    return {
        "report_type": "stage_c_brd_production_acceptance",
        "status": status,
        "run_dir": str(run_dir),
        "layout_file": _first_text(outputs.get("layout_file"), cutout.get("layout_file"), params.get("layout_file")),
        "signal_nets": _first_list(outputs.get("signal_nets"), cutout.get("signal_nets"), _split_nets(params.get("signal_nets"))),
        "reference_nets": _first_list(outputs.get("reference_nets"), cutout.get("reference_nets"), _split_nets(params.get("reference_nets"))),
        "edb_path": _first_text(outputs.get("edb_path"), cutout.get("edb_path")),
        "aedt_project": _first_text(outputs.get("aedt_project"), cutout.get("aedt_project")),
        "port_action_count": len(port_actions),
        "port_actions": port_actions,
        "step_statuses": _step_statuses(workflow),
        "preflight_checks": preflight.get("checks", []) if isinstance(preflight.get("checks"), list) else [],
        "warnings": warnings,
        "blocking_issues": blocking_issues,
        "optional_results": {
            "touchstone": _optional_result(_first_text(outputs.get("touchstone"), cutout.get("touchstone"))),
            "tdr": _optional_result(_first_text(outputs.get("tdr"), cutout.get("tdr"))),
        },
        "artifacts": artifacts,
        "logs": {
            "stdout": str(run_dir / "stdout.log") if (run_dir / "stdout.log").exists() else "",
            "stderr": str(run_dir / "stderr.log") if (run_dir / "stderr.log").exists() else "",
        },
    }


def write_brd_acceptance_summary(run_dir: Path) -> dict[str, Any]:
    summary = build_brd_acceptance_summary(run_dir)
    path = Path(run_dir) / "acceptance_report.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _port_actions(cutout: dict[str, Any]) -> list[dict[str, Any]]:
    plan = cutout.get("port_action_plan")
    if not isinstance(plan, dict):
        return []
    actions = plan.get("port_actions")
    return [dict(action) for action in actions if isinstance(action, dict)] if isinstance(actions, list) else []


def _blocking_issues(preflight: dict[str, Any], workflow: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for check in preflight.get("checks", []) if isinstance(preflight.get("checks"), list) else []:
        if isinstance(check, dict) and check.get("status") == "failed":
            issues.append(f"{check.get('id', 'preflight')}: {check.get('message', '')}".strip())
    for step in workflow.get("steps", []) if isinstance(workflow.get("steps"), list) else []:
        if isinstance(step, dict) and step.get("status") in {"failed", "rejected"}:
            message = str(step.get("error_message") or step.get("error") or "step failed")
            issues.append(f"{step.get('step_id', step.get('node_id', 'workflow_step'))}: {message}")
    if workflow and workflow.get("status") in {"failed", "rejected"} and not any("workflow" in issue for issue in issues):
        issues.append(f"workflow: {workflow.get('status')}")
    return issues


def _warnings(preflight: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for check in preflight.get("checks", []) if isinstance(preflight.get("checks"), list) else []:
        if isinstance(check, dict) and check.get("status") == "warning":
            warnings.append(f"{check.get('id', 'preflight')}: {check.get('message', '')}".strip())
    return warnings


def _artifacts(run_dir: Path, workflow: dict[str, Any], cutout: dict[str, Any]) -> dict[str, str]:
    artifacts = {name: str(run_dir / name) for name in _CORE_ARTIFACTS if (run_dir / name).exists()}
    outputs = workflow.get("outputs", {}) if isinstance(workflow.get("outputs"), dict) else {}
    for key in ["edb_path", "aedt_project", "touchstone", "tdr"]:
        value = _first_text(outputs.get(key), cutout.get(key))
        if value:
            artifacts[key] = value
    return artifacts


def _step_statuses(workflow: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for step in workflow.get("steps", []) if isinstance(workflow.get("steps"), list) else []:
        if isinstance(step, dict):
            step_id = str(step.get("step_id") or step.get("node_id") or "")
            if step_id:
                statuses[step_id] = str(step.get("status") or "")
    return statuses


def _optional_result(value: str) -> str:
    return value if value else "not_available"


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def _first_list(*values: Any) -> list[str]:
    for value in values:
        if isinstance(value, list) and value:
            return [str(item) for item in value]
    return []


def _split_nets(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []
