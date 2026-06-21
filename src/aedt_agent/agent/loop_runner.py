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
