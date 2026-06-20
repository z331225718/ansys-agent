from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from aedt_agent.agent.graph_executors import (
    GraphNodeExecutionContext,
    GraphNodeExecutorRegistry,
)
from aedt_agent.agent.mission import NodeRunStatus
from aedt_agent.reporting.brd_optimization_report import (
    build_brd_optimization_summary,
    render_brd_optimization_report_html,
    write_brd_optimization_history_csv,
)


def register_optimization_handlers(
    registry: GraphNodeExecutorRegistry,
) -> None:
    registry.register(
        "brd.optimization.prepare_working_project",
        prepare_working_project,
    )
    registry.register(
        "brd.optimization.prepare_next_solve",
        prepare_next_solve,
    )
    registry.register(
        "brd.optimization.decide_next_action",
        decide_next_action,
    )
    registry.register(
        "brd.optimization.write_report",
        write_optimization_report,
    )


def prepare_working_project(
    context: GraphNodeExecutionContext,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    source_project = Path(
        str(payload.get("source_project_path") or payload.get("project_path"))
    )
    working_project = Path(
        str(payload.get("working_project_path") or source_project)
    )
    if not source_project.is_file():
        raise FileNotFoundError(f"source_project_path not found: {source_project}")
    reset = bool(payload.get("reset_working_project", False))
    if working_project != source_project:
        _copy_project_bundle_once(
            source_project,
            working_project,
            reset=reset,
            run_root=_run_root(payload, working_project.parent),
        )

    loop_context = _initial_loop_context(payload, working_project)
    output = _solve_input(payload, loop_context, round_index=1)
    output["working_project_prepared"] = True
    output["source_project_path"] = str(source_project)
    return _ok(output)


def prepare_next_solve(
    context: GraphNodeExecutionContext,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    loop_context = _loop_context(payload)
    current_round = int(loop_context.get("round_index") or 1)
    next_round = current_round + 1
    loop_context["round_index"] = next_round
    project_path = str(
        payload.get("edited_project_path")
        or payload.get("project_path")
        or loop_context.get("latest_project_path")
        or loop_context.get("working_project_path")
        or ""
    )
    if project_path:
        loop_context["latest_project_path"] = project_path
        loop_context["working_project_path"] = project_path
    output = _solve_input(payload, loop_context, round_index=next_round)
    return _ok(output)


def decide_next_action(
    context: GraphNodeExecutionContext,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    loop_context = _loop_context(payload)
    score = dict(payload.get("score") or {})
    evidence = dict(payload.get("evidence_summary") or {})
    _refresh_progress_report(loop_context)

    current_round = int(loop_context.get("round_index") or 1)
    max_rounds = int(loop_context.get("max_rounds") or payload.get("max_rounds") or 3)
    score_status = str(score.get("status") or evidence.get("status") or "")
    candidate_actions = [
        item for item in loop_context.get("candidate_actions", [])
        if isinstance(item, dict)
    ]
    used_actions = len(loop_context.get("model_edit_manifest_paths") or [])

    if score_status == "pass" and not loop_context.get("continue_after_pass", False):
        output = _decision_payload(
            payload,
            loop_context,
            decision="complete",
            reason="score passed acceptance floor",
        )
        return _ok(output, outcome="complete")
    if current_round >= max_rounds:
        output = _decision_payload(
            payload,
            loop_context,
            decision="complete",
            reason=f"max_rounds reached: {current_round}/{max_rounds}",
        )
        return _ok(output, outcome="complete")

    selected, reason, source = _select_candidate_action(
        context,
        score=score,
        evidence=evidence,
        candidate_actions=candidate_actions,
        start_index=used_actions,
    )
    if selected is None:
        output = _decision_payload(
            payload,
            loop_context,
            decision="complete",
            reason=reason or "no executable candidate action remains",
        )
        return _ok(output, outcome="complete")

    if not _action_within_known_limits(selected):
        output = _decision_payload(
            payload,
            loop_context,
            decision="request_human_review",
            reason="selected candidate violates known geometry limits",
        )
        return _ok(output, outcome="approval_required")

    loop_context["last_decision_source"] = source
    loop_context["last_decision_reason"] = reason
    output = _decision_payload(
        payload,
        loop_context,
        decision="continue",
        reason=reason,
    )
    output.update(
        {
            "project_path": str(
                loop_context.get("latest_project_path")
                or loop_context.get("working_project_path")
                or payload.get("project_path")
                or ""
            ),
            "actions": [selected],
            "project_copy_mode": "working_project",
            "approval_reason": (
                "approve_optimization_action:"
                f"{selected.get('action_type', 'model_edit')}"
            ),
            "approval_options": [
                {"id": "approve", "label": "Approve model edit"},
                {"id": "reject", "label": "Reject model edit"},
            ],
        }
    )
    if loop_context.get("require_action_approval", False):
        return _ok(output, outcome="approval_required")
    return _ok(output, outcome="continue")


def write_optimization_report(
    context: GraphNodeExecutionContext,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    loop_context = _loop_context(payload)
    report = _refresh_progress_report(loop_context)
    output = {
        **payload,
        **report,
        "checks": [
            {
                "id": "raw_trace_policy",
                "status": "passed",
                "message": "raw s-parameters and TDR remain artifact-only",
            },
            {
                "id": "optimization_history_csv",
                "status": "passed" if report.get("optimization_history_csv") else "failed",
                "message": str(report.get("optimization_history_csv") or ""),
            },
        ],
        "loop_context": loop_context,
        "artifact_refs": [
            value
            for value in (
                report.get("optimization_history_csv"),
                report.get("report_html"),
                report.get("report_json"),
            )
            if value
        ],
    }
    return _ok(output, outcome="succeeded")


def _initial_loop_context(
    payload: Mapping[str, Any],
    working_project: Path,
) -> dict[str, Any]:
    report_dir = Path(
        str(payload.get("report_dir") or working_project.parent / "optimization_progress")
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    return {
        "round_index": 1,
        "source_project_path": str(
            payload.get("source_project_path") or payload.get("project_path") or ""
        ),
        "working_project_path": str(working_project),
        "latest_project_path": str(working_project),
        "report_dir": str(report_dir),
        "max_rounds": int(payload.get("max_rounds") or 3),
        "candidate_actions": list(payload.get("candidate_actions") or []),
        "require_action_approval": bool(payload.get("require_action_approval", False)),
        "continue_after_pass": bool(payload.get("continue_after_pass", False)),
        "solve_manifest_paths": [],
        "score_evidence_paths": [],
        "model_edit_manifest_paths": [],
        "solve": {
            "setup_name": str(payload.get("setup_name") or "Setup1"),
            "sweep_name": str(payload.get("sweep_name") or "Sweep1"),
            "tdr_expression": str(payload.get("tdr_expression") or "TDRZt(Diff1)"),
            "expected_port_count": int(payload.get("expected_port_count") or 4),
            "touchstone_name": str(payload.get("touchstone_name") or "channel.s4p"),
            "tdr_report_name": str(payload.get("tdr_report_name") or "ChannelTDR"),
            "tdr_differential_pairs": bool(payload.get("tdr_differential_pairs", True)),
            "tdr_observation_port": str(payload.get("tdr_observation_port") or "Diff1"),
            "project_copy_mode": "working_project",
            "run_analyze": bool(payload.get("run_analyze", True)),
            "export_tdr": bool(payload.get("export_tdr", True)),
            "sparameter_mode": str(payload.get("sparameter_mode") or "differential"),
        },
        "score": {
            "frequency_start_ghz": float(payload.get("frequency_start_ghz", 0.0)),
            "frequency_stop_ghz": float(payload.get("frequency_stop_ghz", 28.0)),
            "rl_target_db": float(payload.get("rl_target_db", -17.0)),
            "tdr_target_ohm": float(payload.get("tdr_target_ohm", 90.0)),
            "tdr_tolerance_ohm": float(payload.get("tdr_tolerance_ohm", 9.0)),
            "sparameter_mode": str(payload.get("sparameter_mode") or "differential"),
            "tdr_observation_port": str(payload.get("tdr_observation_port") or "Diff1"),
        },
    }


def _solve_input(
    payload: Mapping[str, Any],
    loop_context: dict[str, Any],
    *,
    round_index: int,
) -> dict[str, Any]:
    solve = dict(loop_context.get("solve") or {})
    score = dict(loop_context.get("score") or {})
    project_path = str(
        loop_context.get("latest_project_path")
        or loop_context.get("working_project_path")
        or payload.get("project_path")
        or ""
    )
    return {
        "project_path": project_path,
        "setup_name": solve.get("setup_name", "Setup1"),
        "sweep_name": solve.get("sweep_name", "Sweep1"),
        "tdr_expression": solve.get("tdr_expression", "TDRZt(Diff1)"),
        "expected_port_count": int(solve.get("expected_port_count", 4)),
        "touchstone_name": solve.get("touchstone_name", "channel.s4p"),
        "tdr_report_name": solve.get("tdr_report_name", "ChannelTDR"),
        "run_analyze": bool(solve.get("run_analyze", True)),
        "export_tdr": bool(solve.get("export_tdr", True)),
        "tdr_differential_pairs": bool(solve.get("tdr_differential_pairs", True)),
        "tdr_observation_port": solve.get("tdr_observation_port", "Diff1"),
        "project_copy_mode": "working_project",
        "frequency_start_ghz": float(score.get("frequency_start_ghz", 0.0)),
        "frequency_stop_ghz": float(score.get("frequency_stop_ghz", 28.0)),
        "rl_target_db": float(score.get("rl_target_db", -17.0)),
        "tdr_target_ohm": float(score.get("tdr_target_ohm", 90.0)),
        "tdr_tolerance_ohm": float(score.get("tdr_tolerance_ohm", 9.0)),
        "sparameter_mode": score.get("sparameter_mode", "differential"),
        "loop_context": {**loop_context, "round_index": round_index},
    }


def _decision_payload(
    payload: Mapping[str, Any],
    loop_context: dict[str, Any],
    *,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason": reason,
        "score": payload.get("score") or {},
        "evidence_summary": payload.get("evidence_summary") or {},
        "loop_context": loop_context,
    }


def _select_candidate_action(
    context: GraphNodeExecutionContext,
    *,
    score: Mapping[str, Any],
    evidence: Mapping[str, Any],
    candidate_actions: list[dict[str, Any]],
    start_index: int,
) -> tuple[dict[str, Any] | None, str, str]:
    llm_selection = _select_candidate_action_with_llm(
        context,
        score=score,
        evidence=evidence,
        candidate_actions=candidate_actions,
        start_index=start_index,
    )
    if llm_selection[0] is not None:
        return llm_selection

    target = float(score.get("tdr_target_ohm") or evidence.get("tdr_target_ohm") or 90.0)
    tdr_min = score.get("tdr_min_impedance_ohm") or evidence.get("tdr_min_impedance_ohm")
    tdr_max = score.get("tdr_max_impedance_ohm") or evidence.get("tdr_max_impedance_ohm")
    desired_effect = ""
    if tdr_min is not None and float(tdr_min) < target:
        desired_effect = "increase_impedance"
    elif tdr_max is not None and float(tdr_max) > target:
        desired_effect = "decrease_impedance"

    for index in range(start_index, len(candidate_actions)):
        action = candidate_actions[index]
        if desired_effect and str(action.get("expected_effect") or "") != desired_effect:
            continue
        return (
            action,
            f"deterministic candidate {index} selected for {desired_effect or 'next bounded edit'}",
            "deterministic",
        )
    if start_index < len(candidate_actions):
        return (
            candidate_actions[start_index],
            f"deterministic candidate {start_index} selected as next bounded edit",
            "deterministic",
        )
    return None, "no candidate action remains", "deterministic"


def _select_candidate_action_with_llm(
    context: GraphNodeExecutionContext,
    *,
    score: Mapping[str, Any],
    evidence: Mapping[str, Any],
    candidate_actions: list[dict[str, Any]],
    start_index: int,
) -> tuple[dict[str, Any] | None, str, str]:
    if start_index >= len(candidate_actions):
        return None, "no candidate action remains", "llm"
    try:
        from aedt_agent.agent.llm import LlmConfig, llm_complete_json

        config = LlmConfig.from_env(profile=context.node.profile)
        if not config.api_key:
            return None, "LLM not configured", "llm"
        user_payload = {
            "bounded_score": _bounded_score_for_decider(score, evidence),
            "candidate_actions": candidate_actions[start_index:],
            "start_index": start_index,
            "constraints": context.node.constraints,
        }
        result = llm_complete_json(
            (
                "Select the next BRD via optimization action from the provided "
                "candidate_actions. Use only bounded score evidence. Return JSON "
                "with decision=continue|complete|request_human_review, action_index "
                "as an absolute index when continuing, and reason."
            ),
            json.dumps(user_payload, ensure_ascii=False, indent=2),
            config=config,
        )
    except Exception:
        return None, "LLM selection unavailable", "llm"

    decision = str(result.get("decision") or "").strip()
    if decision in {"complete", "request_human_review"}:
        return None, str(result.get("reason") or decision), "llm"
    try:
        action_index = int(result.get("action_index"))
    except (TypeError, ValueError):
        return None, "LLM did not return a valid action_index", "llm"
    if action_index < start_index or action_index >= len(candidate_actions):
        return None, "LLM action_index is out of range", "llm"
    return (
        candidate_actions[action_index],
        str(result.get("reason") or f"LLM selected candidate {action_index}"),
        "llm",
    )


def _bounded_score_for_decider(
    score: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    fields = [
        "status",
        "touchstone_kind",
        "return_loss_trace",
        "insertion_loss_trace",
        "rl_worst_db",
        "rl_worst_frequency_ghz",
        "insertion_worst_db_in_band",
        "tdr_observation_port",
        "tdr_peak_deviation_ohm",
        "tdr_peak_time_ps",
        "tdr_min_impedance_ohm",
        "tdr_max_impedance_ohm",
        "tdr_proximity_mse_ohm2",
        "tdr_flatness_msd_ohm2",
        "rl_violation_sum_db",
        "optimization_objective",
        "pass_fail_reason",
    ]
    return {
        field: score.get(field) if field in score else evidence.get(field)
        for field in fields
    }


def _refresh_progress_report(loop_context: dict[str, Any]) -> dict[str, Any]:
    report_dir = Path(str(loop_context.get("report_dir") or "optimization_progress"))
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = build_brd_optimization_summary(
        score_evidence_paths=list(loop_context.get("score_evidence_paths") or []),
        model_edit_manifest_paths=list(loop_context.get("model_edit_manifest_paths") or []),
        solve_manifest_paths=list(loop_context.get("solve_manifest_paths") or []),
    )
    history_csv = write_brd_optimization_history_csv(
        summary,
        report_dir / "optimization_history.csv",
    )
    report_html = report_dir / "optimization_progress.html"
    report_json = report_dir / "optimization_progress.json"
    summary["optimization_history_csv"] = str(history_csv)
    report_html.write_text(
        render_brd_optimization_report_html(summary),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    loop_context["optimization_history_csv"] = str(history_csv)
    loop_context["report_html"] = str(report_html)
    loop_context["report_json"] = str(report_json)
    return {
        "status": summary.get("status", "unknown"),
        "optimization_history_csv": str(history_csv),
        "optimization_history_rows": list(summary.get("history_rows") or []),
        "report_html": str(report_html),
        "report_json": str(report_json),
        "final_score": summary.get("final_score") or {},
    }


def _action_within_known_limits(action: Mapping[str, Any]) -> bool:
    action_type = str(action.get("action_type") or "")
    radius = _dimension_value(action.get("target_radius"))
    diameter = _dimension_value(action.get("target_diameter"))
    if action_type == "anti_pad.enlarge":
        if radius is not None:
            return radius <= 22.0
        if diameter is not None:
            return diameter <= 44.0
    if action_type == "non_functional_pad.add_or_enlarge":
        if radius is not None:
            return 7.875 <= radius <= 10.0
        if diameter is not None:
            return 15.75 <= diameter <= 20.0
    return True


def _dimension_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return float(value.get("value"))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    number = "".join(char for char in text if char.isdigit() or char in ".-+")
    return float(number) if number else None


def _loop_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _copy_project_bundle_once(
    source_project: Path,
    working_project: Path,
    *,
    reset: bool,
    run_root: Path,
) -> None:
    source_project = source_project.resolve()
    working_project = working_project.resolve()
    run_root = run_root.resolve()
    if working_project.exists() and not reset:
        return
    _ensure_within(working_project, run_root)
    for target in (
        working_project,
        working_project.with_suffix(".aedb"),
        Path(f"{working_project}results"),
    ):
        _ensure_within(target.resolve(), run_root)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    working_project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_project, working_project)
    source_edb = source_project.with_suffix(".aedb")
    if source_edb.is_dir():
        shutil.copytree(source_edb, working_project.with_suffix(".aedb"))
    source_results = Path(f"{source_project}results")
    if source_results.is_dir():
        shutil.copytree(source_results, Path(f"{working_project}results"))


def _run_root(payload: Mapping[str, Any], fallback: Path) -> Path:
    return Path(str(payload.get("run_root") or fallback))


def _ensure_within(path: Path, root: Path) -> None:
    if path == root or path.is_relative_to(root):
        return
    raise ValueError(f"path is outside configured run_root: {path}")


def read_history_csv(path: str | Path, *, limit: int = 20) -> list[dict[str, str]]:
    csv_path = Path(path)
    if not csv_path.is_file():
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-limit:]


def _ok(payload: dict[str, Any], *, outcome: str = "succeeded") -> dict[str, Any]:
    return {
        "status": NodeRunStatus.SUCCEEDED,
        "outcome": outcome,
        "output_payload": payload,
        "artifact_refs": list(payload.get("artifact_refs") or []),
    }
