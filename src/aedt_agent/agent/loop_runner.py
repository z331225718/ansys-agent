from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from aedt_agent.agent.graph_runner import advance_graph, create_graph_run, graph_status
from aedt_agent.agent.graph_template import load_graph_template


TERMINAL_GRAPH_STATUSES = {
    "succeeded",
    "failed",
    "canceled",
    "waiting_approval",
}
MIN_POLL_INTERVAL_SECONDS = 5


def load_loop_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{config_path} must contain a JSON object")
    payload.setdefault("template_id", "brd_reviewed_model_optimize_loop")
    payload.setdefault("goal", "Reviewed BRD local-cut optimization loop")
    payload.setdefault("poll_interval_seconds", 30)
    _validate_loop_config(payload, config_path)
    return payload


def validate_loop_config_for_run(
    config: dict[str, Any],
    *,
    check_paths: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(check_id: str, passed: bool, message: str, *, severity: str = "error") -> None:
        checks.append(
            {
                "id": check_id,
                "status": "passed" if passed else "failed",
                "severity": severity,
                "message": message,
            }
        )

    try:
        template = load_graph_template(str(config.get("template_id")))
        check("template_loadable", True, f"loaded template {template.template_id}")
    except Exception as exc:
        check("template_loadable", False, str(exc))

    graph_run_id = str(config.get("graph_run_id") or "").strip()
    if graph_run_id:
        check("resume_graph_run_id", True, f"will resume graph_run_id={graph_run_id}")
        return _validation_report(checks)

    source_project = str(config.get("source_project_path") or config.get("project_path") or "").strip()
    working_project = str(config.get("working_project_path") or "").strip()
    run_root = str(config.get("run_root") or "").strip()
    report_dir = str(config.get("report_dir") or "").strip()

    check("source_project_path_present", bool(source_project), source_project or "missing")
    check("working_project_path_present", bool(working_project), working_project or "missing")
    check("run_root_present", bool(run_root), run_root or "missing")
    check("report_dir_present", bool(report_dir), report_dir or "missing")
    if source_project and working_project:
        check(
            "working_project_is_separate",
            Path(source_project) != Path(working_project),
            "working project must be separate from the human-reviewed source project",
        )
    if run_root and working_project:
        check(
            "working_project_under_run_root",
            _path_is_under(Path(working_project), Path(run_root)),
            "working project should live under run_root so the loop edits one controlled copy",
        )
    if run_root and report_dir:
        check(
            "report_dir_under_run_root",
            _path_is_under(Path(report_dir), Path(run_root)),
            "report_dir should live under run_root for artifact review and cleanup",
        )

    if check_paths and source_project:
        check(
            "source_project_exists",
            Path(source_project).is_file(),
            source_project,
        )
    if check_paths and working_project:
        parent = Path(working_project).parent
        check(
            "working_project_parent_ready",
            parent.exists() or parent.parent.exists(),
            str(parent),
            severity="warning",
        )

    check(
        "poll_interval_seconds",
        int(config.get("poll_interval_seconds") or 30) >= MIN_POLL_INTERVAL_SECONDS,
        f"poll_interval_seconds={config.get('poll_interval_seconds', 30)}",
    )
    check(
        "max_rounds_positive",
        int(config.get("max_rounds") or 0) > 0,
        f"max_rounds={config.get('max_rounds')}",
    )
    check(
        "touchstone_is_s4p",
        str(config.get("touchstone_name") or "").casefold().endswith(".s4p")
        and int(config.get("expected_port_count") or 0) == 4,
        "differential reviewed BRD loop must export a four-port .s4p",
    )
    check(
        "differential_traces",
        str(config.get("sparameter_mode") or "").casefold() == "differential",
        "score worker must use differential SDD11/SDD21 evidence",
    )
    check(
        "tdr_diff1",
        "diff1" in str(config.get("tdr_expression") or "").casefold()
        and str(config.get("tdr_observation_port") or "").casefold() == "diff1",
        "TDR observation should default to Diff1 for this workflow",
    )
    reference_impedance = float(
        config.get(
            "reference_impedance_ohm",
            config.get("tdr_reference_impedance_ohm", config.get("tdr_target_ohm", 0)),
        )
        or 0
    )
    check(
        "differential_reference_impedance",
        abs(reference_impedance - 90.0) < 1e-9,
        "reviewed differential loop must score SDD/TDR with 90ohm differential reference",
    )
    check(
        "tdr_export_enabled",
        bool(config.get("export_tdr", True)),
        "TDR export is required before channel score",
    )
    check(
        "geometry_constraints",
        _geometry_constraints_valid(config),
        "anti-pad radius <=22mil; NFP radius in [7.875mil, 10mil]",
    )
    return _validation_report(checks)


def run_loop_from_config(
    runtime,
    config: dict[str, Any],
    *,
    worker_id: str = "loop-runner",
    max_workers: int = 2,
    poll_interval_seconds: int | None = None,
) -> dict[str, Any]:
    graph_run_id = str(config.get("graph_run_id") or "").strip()
    if graph_run_id:
        status_report = graph_status(runtime, graph_run_id)
        mission_id = str(status_report["graph_run"]["mission_id"])
    else:
        template = load_graph_template(str(config.get("template_id")))
        mission_id = str(config.get("mission_id") or "").strip()
        if not mission_id:
            mission = runtime.create_mission(str(config.get("goal")), [], [])
            mission_id = mission.mission_id
        graph_run = create_graph_run(
            runtime,
            mission_id,
            template,
            initial_payload=dict(config),
            max_steps=int(config.get("max_steps") or 64),
        )
        graph_run_id = graph_run.graph_run_id
    poll_seconds = int(
        poll_interval_seconds
        if poll_interval_seconds is not None
        else config.get("poll_interval_seconds")
        or 30
    )
    if poll_seconds < MIN_POLL_INTERVAL_SECONDS:
        raise ValueError(
            "poll_interval_seconds must be at least "
            f"{MIN_POLL_INTERVAL_SECONDS}"
        )
    last_signature: tuple[Any, ...] | None = None
    idle_polls = 0
    max_idle_polls = int(config.get("max_idle_polls") or 120)

    while True:
        report = advance_graph(
            runtime,
            graph_run_id,
            worker_id=worker_id,
            max_workers=max_workers,
        )
        status = str(report.get("status") or "")
        if status in TERMINAL_GRAPH_STATUSES:
            report["mission_id"] = mission_id
            report["graph_run_id"] = graph_run_id
            report["poll_interval_seconds"] = poll_seconds
            return report
        signature = _progress_signature(report)
        if signature == last_signature:
            idle_polls += 1
            if idle_polls >= max_idle_polls:
                status_report = graph_status(runtime, graph_run_id)
                status_report["mission_id"] = mission_id
                status_report["graph_run_id"] = graph_run_id
                status_report["loop_runner_status"] = "idle_poll_limit"
                status_report["poll_interval_seconds"] = poll_seconds
                return status_report
            time.sleep(max(1, poll_seconds))
        else:
            idle_polls = 0
        last_signature = signature


def _validate_loop_config(payload: dict[str, Any], config_path: Path) -> None:
    poll_seconds = int(payload.get("poll_interval_seconds") or 30)
    if poll_seconds < MIN_POLL_INTERVAL_SECONDS:
        raise ValueError(
            f"{config_path}: poll_interval_seconds must be at least "
            f"{MIN_POLL_INTERVAL_SECONDS}"
        )
    if payload.get("graph_run_id"):
        return
    if not str(payload.get("goal") or "").strip():
        raise ValueError(f"{config_path}: goal is required")
    if not str(payload.get("template_id") or "").strip():
        raise ValueError(f"{config_path}: template_id is required")


def _validation_report(checks: list[dict[str, Any]]) -> dict[str, Any]:
    error_failures = [
        item for item in checks
        if item["status"] == "failed" and item.get("severity") != "warning"
    ]
    warning_failures = [
        item for item in checks
        if item["status"] == "failed" and item.get("severity") == "warning"
    ]
    return {
        "status": "failed" if error_failures else "passed",
        "checks": checks,
        "failed_checks": [item["id"] for item in error_failures],
        "warning_checks": [item["id"] for item in warning_failures],
    }


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except Exception:
        return False


def _geometry_constraints_valid(config: dict[str, Any]) -> bool:
    constraints = config.get("geometry_constraints")
    if not isinstance(constraints, dict):
        return False
    anti_pad = constraints.get("anti_pad")
    nfp = constraints.get("non_functional_pad")
    if not isinstance(anti_pad, dict) or not isinstance(nfp, dict):
        return False
    try:
        anti_max = float(anti_pad.get("max_radius_mil"))
        nfp_min = float(nfp.get("min_radius_mil"))
        nfp_max = float(nfp.get("max_radius_mil"))
    except (TypeError, ValueError):
        return False
    return anti_max <= 22 and nfp_min >= 7.875 and nfp_max <= 10 and nfp_min <= nfp_max


def _progress_signature(report: dict[str, Any]) -> tuple[Any, ...]:
    graph_run = report.get("graph_run") or {}
    return (
        report.get("status"),
        graph_run.get("step_count"),
        graph_run.get("current_node_id"),
        tuple(
            (
                run.get("node_id"),
                run.get("sequence"),
                run.get("status"),
                run.get("edge_decision"),
            )
            for run in report.get("node_runs", [])
        ),
    )
