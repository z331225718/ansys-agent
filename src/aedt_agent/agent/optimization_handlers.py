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
        "brd.optimization.build_candidate_actions",
        build_candidate_actions,
    )
    registry.register(
        "brd.optimization.decide_next_action",
        decide_next_action,
    )
    registry.register(
        "brd.optimization.fail_optimization",
        fail_optimization,
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
    run_root = _run_root(payload, working_project.parent)
    cleanup_lock = bool(payload.get("cleanup_stale_aedt_lock", True))
    removed_lock_files = (
        _remove_project_lock(working_project, run_root=run_root)
        if cleanup_lock
        else []
    )
    if working_project != source_project:
        _copy_project_bundle_once(
            source_project,
            working_project,
            reset=reset,
            run_root=run_root,
        )

    loop_context = _initial_loop_context(payload, working_project)
    output = _solve_input(payload, loop_context, round_index=1)
    output["working_project_prepared"] = True
    output["source_project_path"] = str(source_project)
    output["stale_aedt_lock_files_removed"] = removed_lock_files
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


def build_candidate_actions(
    context: GraphNodeExecutionContext,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    loop_context = _loop_context(payload)
    explicit_actions = [
        item for item in loop_context.get("candidate_actions", [])
        if isinstance(item, dict)
    ]
    inventory_load_issues = _candidate_inventory_load_issues(loop_context)
    if inventory_load_issues:
        return _failed(
            {
                **payload,
                "loop_context": loop_context,
                "candidate_action_inventory_errors": inventory_load_issues,
            },
            code="invalid_candidate_action_inventory",
            message="; ".join(inventory_load_issues),
        )
    inventory = _candidate_inventory(loop_context)
    inventory_issues = _candidate_inventory_issues(inventory)
    if inventory_issues:
        return _failed(
            {
                **payload,
                "loop_context": loop_context,
                "candidate_action_inventory_errors": inventory_issues,
            },
            code="invalid_candidate_action_inventory",
            message="; ".join(inventory_issues),
        )
    generated_actions = _candidate_actions_from_inventory(payload, loop_context)
    merged_actions = _merge_candidate_actions(explicit_actions, generated_actions)
    if inventory and not merged_actions:
        issue = (
            "candidate_action_inventory produced zero executable actions; "
            "fill anti_pad_shape_layers/non_functional_pad_layers with reviewed "
            "object entries that include layer, shape or center evidence, and "
            "parasitic_target"
        )
        return _failed(
            {
                **payload,
                "loop_context": loop_context,
                "candidate_action_inventory_errors": [issue],
            },
            code="invalid_candidate_action_inventory",
            message=issue,
        )
    loop_context["candidate_actions"] = merged_actions
    loop_context["candidate_action_inventory_summary"] = {
        "explicit_action_count": len(explicit_actions),
        "generated_action_count": len(generated_actions),
        "candidate_action_count": len(merged_actions),
        "inventory_source": str(
            inventory.get("source")
            or "candidate_action_inventory"
        ),
    }
    output = _solve_input(
        payload,
        loop_context,
        round_index=int(loop_context.get("round_index") or 1),
    )
    output["candidate_action_count"] = len(merged_actions)
    output["candidate_action_inventory_summary"] = dict(
        loop_context["candidate_action_inventory_summary"]
    )
    return _ok(output)


def decide_next_action(
    context: GraphNodeExecutionContext,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    loop_context = _loop_context(payload)
    score = dict(payload.get("score") or {})
    evidence = dict(payload.get("evidence_summary") or {})

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

    selected, reason, source, selection_decision = _select_candidate_action(
        context,
        score=score,
        evidence=evidence,
        loop_context=loop_context,
        candidate_actions=candidate_actions,
        start_index=used_actions,
    )
    if selected is None:
        decision = (
            selection_decision
            if selection_decision in {"complete", "approval_required", "failed"}
            else "complete"
        )
        if not _decision_allowed(context, decision):
            decision = "failed"
        output = _decision_payload(
            payload,
            loop_context,
            decision=decision,
            reason=reason or "no executable candidate action remains",
        )
        outcome = "approval_required" if decision == "approval_required" else decision
        return _ok(output, outcome=outcome)

    if not _action_within_known_limits(selected):
        decision = "approval_required" if _decision_allowed(context, "approval_required") else "failed"
        output = _decision_payload(
            payload,
            loop_context,
            decision=decision,
            reason="selected candidate violates known geometry limits",
            selected_action=selected,
        )
        return _ok(output, outcome=decision)

    loop_context["last_decision_source"] = source
    loop_context["last_decision_reason"] = reason
    output = _decision_payload(
        payload,
        loop_context,
        decision="continue",
        reason=reason,
        selected_action=selected,
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
        if _decision_allowed(context, "approval_required"):
            return _ok(output, outcome="approval_required")
        failed = _decision_payload(
            payload,
            loop_context,
            decision="failed",
            reason="action approval gate is not part of this reviewed-model loop",
            selected_action=selected,
        )
        return _ok(failed, outcome="failed")
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


def fail_optimization(
    context: GraphNodeExecutionContext,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    approval_required = payload.get("approval_required")
    approval_reason = (
        approval_required.get("reason")
        if isinstance(approval_required, dict)
        else None
    )
    reason = str(
        payload.get("approval_reason")
        or approval_reason
        or payload.get("reason")
        or "optimization loop failed"
    )
    output = {
        **payload,
        "status": "failed",
        "decision": "failed",
        "reason": reason,
    }
    return {
        "status": NodeRunStatus.FAILED,
        "outcome": "failed",
        "output_payload": output,
        "artifact_refs": list(payload.get("artifact_refs") or []),
        "error": {
            "code": "optimization_loop_failed",
            "message": reason,
        },
    }


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
        "candidate_action_inventory": dict(
            payload.get("candidate_action_inventory")
            or payload.get("geometry_candidate_inventory")
            or {}
        ),
        "candidate_action_inventory_path": str(
            payload.get("candidate_action_inventory_path")
            or payload.get("candidate_action_inventory_file")
            or ""
        ),
        "candidate_action_policy": dict(payload.get("candidate_action_policy") or {}),
        "geometry_constraints": dict(payload.get("geometry_constraints") or {}),
        "require_action_approval": bool(payload.get("require_action_approval", False)),
        "continue_after_pass": bool(payload.get("continue_after_pass", False)),
        "solve_manifest_paths": [],
        "score_evidence_paths": [],
        "model_edit_manifest_paths": [],
        "solve": {
            "setup_name": str(payload.get("setup_name") or "Setup1"),
            "sweep_name": str(payload.get("sweep_name") or "Sweep1"),
            "tdr_expression": str(payload.get("tdr_expression") or "TDRZ(Diff1)"),
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
            "tdr_plot_time_stop_ps": float(
                payload.get("tdr_plot_time_stop_ps", 120.0)
            ),
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
        "solution_name": _solution_name(solve),
        "tdr_expression": solve.get("tdr_expression", "TDRZ(Diff1)"),
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
        "tdr_plot_time_stop_ps": float(score.get("tdr_plot_time_stop_ps", 120.0)),
        "loop_context": {**loop_context, "round_index": round_index},
    }


def _solution_name(solve: Mapping[str, Any]) -> str:
    explicit = str(solve.get("solution_name") or "").strip()
    if explicit:
        return explicit
    setup_name = str(solve.get("setup_name") or "Setup1")
    sweep_name = str(solve.get("sweep_name") or "Sweep1")
    return f"{setup_name} : {sweep_name}"


def _decision_payload(
    payload: Mapping[str, Any],
    loop_context: dict[str, Any],
    *,
    decision: str,
    reason: str,
    selected_action: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    score = payload.get("score") or {}
    evidence = payload.get("evidence_summary") or {}
    action = dict(selected_action or {})
    tdr_observation_port = str(
        action.get("tdr_observation_port")
        or score.get("tdr_observation_port")
        or evidence.get("tdr_observation_port")
        or ""
    )
    return {
        "decision": decision,
        "reason": reason,
        "selected_action": action,
        "tdr_observation_port": tdr_observation_port,
        "tdr_port_orientation_evidence": str(
            action.get("tdr_port_orientation_evidence")
            or evidence.get("tdr_port_orientation_evidence")
            or "unknown"
        ),
        "constraints_checked": list(action.get("constraints_checked") or []),
        "risk": str(action.get("risk") or ""),
        "rollback": str(action.get("rollback") or ""),
        "score": score,
        "evidence_summary": evidence,
        "loop_context": loop_context,
    }


def _select_candidate_action(
    context: GraphNodeExecutionContext,
    *,
    score: Mapping[str, Any],
    evidence: Mapping[str, Any],
    loop_context: Mapping[str, Any],
    candidate_actions: list[dict[str, Any]],
    start_index: int,
) -> tuple[dict[str, Any] | None, str, str, str]:
    llm_selection = _select_candidate_action_with_llm(
        context,
        score=score,
        evidence=evidence,
        loop_context=loop_context,
        candidate_actions=candidate_actions,
        start_index=start_index,
    )
    if llm_selection[0] is not None or llm_selection[3] in {
        "complete",
        "approval_required",
        "failed",
    }:
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
            "continue",
        )
    if start_index < len(candidate_actions):
        return (
            candidate_actions[start_index],
            f"deterministic candidate {start_index} selected as next bounded edit",
            "deterministic",
            "continue",
        )
    return None, "no candidate action remains", "deterministic", "complete"


def _select_candidate_action_with_llm(
    context: GraphNodeExecutionContext,
    *,
    score: Mapping[str, Any],
    evidence: Mapping[str, Any],
    loop_context: Mapping[str, Any],
    candidate_actions: list[dict[str, Any]],
    start_index: int,
) -> tuple[dict[str, Any] | None, str, str, str]:
    remaining_actions = candidate_actions[start_index:]
    inventory = _candidate_inventory(loop_context)
    if not remaining_actions and not inventory:
        return None, "no candidate action remains", "llm", "complete"
    try:
        from aedt_agent.agent.llm import LlmConfig, llm_complete_json

        config = LlmConfig.from_env(profile=context.node.profile)
        if not config.api_key:
            return None, "LLM not configured", "llm", ""
        user_payload = {
            "bounded_score": _bounded_score_for_decider(score, evidence),
            "candidate_action_inventory": inventory,
            "deterministic_fallback_actions": remaining_actions,
            "start_index": start_index,
            "constraints": context.node.constraints,
        }
        allowed = _allowed_decisions(context)
        result = llm_complete_json(
            (
                "Propose the next BRD via optimization action from the reviewed "
                "candidate_action_inventory and playbook rules. Use only bounded "
                "score evidence. You may either return selected_action directly "
                "using only inventory layer/shape/center facts, or choose an "
                "absolute action_index from deterministic_fallback_actions. "
                f"Return JSON with decision in {sorted(allowed)} and reason."
            ),
            json.dumps(user_payload, ensure_ascii=False, indent=2),
            config=config,
        )
    except Exception:
        return None, "LLM selection unavailable", "llm", ""

    decision = str(result.get("decision") or "").strip()
    if decision in {"complete", "approval_required", "request_human_review", "failed"}:
        normalized = "approval_required" if decision == "request_human_review" else decision
        if normalized not in _allowed_decisions(context):
            return None, f"LLM decision {normalized} is not allowed by this graph", "llm", "failed"
        return None, str(result.get("reason") or normalized), "llm", normalized
    selected_action = result.get("selected_action") or result.get("action")
    if isinstance(selected_action, Mapping):
        action = dict(selected_action)
        valid, validation_reason = _llm_selected_action_within_inventory(
            action,
            inventory,
        )
        if valid:
            return (
                action,
                str(result.get("reason") or validation_reason or "LLM proposed bounded action"),
                "llm_proposed",
                "continue",
            )
        return None, f"LLM selected_action outside candidate inventory: {validation_reason}", "llm", ""
    try:
        action_index = int(result.get("action_index"))
    except (TypeError, ValueError):
        return None, "LLM did not return a valid action_index", "llm", ""
    if action_index < start_index or action_index >= len(candidate_actions):
        return None, "LLM action_index is out of range", "llm", ""
    return (
        candidate_actions[action_index],
        str(result.get("reason") or f"LLM selected candidate {action_index}"),
        "llm",
        "continue",
    )


def _allowed_decisions(context: GraphNodeExecutionContext) -> set[str]:
    constraints = context.node.constraints if isinstance(context.node.constraints, dict) else {}
    values = constraints.get("allowed_decisions")
    if isinstance(values, list) and values:
        return {str(value) for value in values}
    return {"continue", "complete", "approval_required", "failed"}


def _decision_allowed(context: GraphNodeExecutionContext, decision: str) -> bool:
    return decision in _allowed_decisions(context)


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


def _candidate_actions_from_inventory(
    payload: Mapping[str, Any],
    loop_context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    inventory = _candidate_inventory(loop_context)
    if not inventory:
        return []
    actions: list[dict[str, Any]] = []
    geometry_constraints = dict(loop_context.get("geometry_constraints") or {})
    tdr_observation_port = str(
        inventory.get("tdr_observation_port")
        or loop_context.get("tdr_observation_port")
        or payload.get("tdr_observation_port")
        or "Diff1"
    )
    orientation_evidence = str(
        inventory.get("tdr_port_orientation_evidence")
        or payload.get("tdr_port_orientation_evidence")
        or "unknown"
    )
    tdr_feature_time = inventory.get("tdr_feature_time")

    for item in _inventory_items(
        inventory,
        "anti_pad_shape_layers",
        "anti_pad_candidates",
        "shape_backed_layers",
    ):
        if not isinstance(item, Mapping):
            continue
        for layer in _candidate_layers(item):
            plane_shape_ids = _layer_values(
                item,
                layer,
                "plane_shape_ids",
                "shape_ids",
                "selected_shape_ids",
                by_layer_keys=("plane_shape_ids_by_layer", "shape_ids_by_layer"),
            )
            action = {
                "hypothesis": str(
                    item.get("hypothesis")
                    or f"Reviewed shape-backed anti-pad candidate on {layer}"
                ),
                "evidence_refs": list(item.get("evidence_refs") or []),
                "tdr_observation_port": tdr_observation_port,
                "tdr_port_orientation_evidence": str(
                    item.get("tdr_port_orientation_evidence")
                    or orientation_evidence
                ),
                "target_region": str(item.get("target_region") or "reviewed_other"),
                "action_type": "anti_pad.enlarge",
                "layers": [layer],
                "plane_shape_ids": plane_shape_ids,
                "parasitic_target": str(
                    item.get("parasitic_target")
                    or f"reviewed_shape_on_{_safe_parameter_stem(layer)}"
                ),
                "center_source": str(item.get("center_source") or "padstack_instances"),
                "center_padstack_instance_ids": _layer_values(
                    item,
                    layer,
                    "center_padstack_instance_ids",
                    "padstack_instance_ids",
                    by_layer_keys=(
                        "center_padstack_instance_ids_by_layer",
                        "padstack_instance_ids_by_layer",
                    ),
                ),
                "bridge_center_padstack_instance_ids": _layer_values(
                    item,
                    layer,
                    "bridge_center_padstack_instance_ids",
                    by_layer_keys=("bridge_center_padstack_instance_ids_by_layer",),
                ),
                "target_radius": _target_radius(
                    item,
                    geometry_constraints,
                    constraint_key="anti_pad",
                    default_value=22.0,
                ),
                "parameter_name": str(
                    item.get("parameter_name")
                    or f"{_safe_parameter_stem(layer)}_void_r"
                ),
                "bridge_between_vias": bool(item.get("bridge_between_vias", True)),
                "constraints": {"max_diameter": "44mil"},
                "constraints_checked": list(
                    item.get("constraints_checked")
                    or ["anti_pad_radius <= 22mil"]
                ),
                "expected_effect": str(
                    item.get("expected_effect") or "increase_impedance"
                ),
                "risk": str(
                    item.get("risk")
                    or "May over-raise impedance or affect adjacent return current."
                ),
                "rollback": str(
                    item.get("rollback")
                    or f"restore {_safe_parameter_stem(layer)}_void_r or working checkpoint"
                ),
                "candidate_source": "candidate_action_inventory",
            }
            if tdr_feature_time is not None:
                action["tdr_feature_time"] = tdr_feature_time
            if item.get("bridge_via_centers") is not None:
                action["bridge_via_centers"] = item.get("bridge_via_centers")
            if item.get("via_centers") is not None:
                action["via_centers"] = item.get("via_centers")
            actions.append(action)

    for item in _inventory_items(
        inventory,
        "non_functional_pad_layers",
        "non_functional_pad_candidates",
        "mechanical_hole_layers",
    ):
        if not isinstance(item, Mapping):
            continue
        for layer in _candidate_layers(item):
            action = {
                "hypothesis": str(
                    item.get("hypothesis")
                    or f"Reviewed via-barrel NFP candidate on {layer}"
                ),
                "evidence_refs": list(item.get("evidence_refs") or []),
                "tdr_observation_port": tdr_observation_port,
                "tdr_port_orientation_evidence": str(
                    item.get("tdr_port_orientation_evidence")
                    or orientation_evidence
                ),
                "target_region": str(item.get("target_region") or "via_barrel"),
                "action_type": "non_functional_pad.add_or_enlarge",
                "implementation": str(item.get("implementation") or "shape"),
                "layers": [layer],
                "parasitic_target": str(
                    item.get("parasitic_target")
                    or f"reviewed_mechanical_hole_on_{_safe_parameter_stem(layer)}"
                ),
                "center_source": str(item.get("center_source") or "padstack_instances"),
                "center_padstack_instance_ids": _layer_values(
                    item,
                    layer,
                    "center_padstack_instance_ids",
                    "padstack_instance_ids",
                    by_layer_keys=(
                        "center_padstack_instance_ids_by_layer",
                        "padstack_instance_ids_by_layer",
                    ),
                ),
                "signal_nets": list(item.get("signal_nets") or []),
                "target_radius": _target_radius(
                    item,
                    geometry_constraints,
                    constraint_key="non_functional_pad",
                    default_value=7.875,
                ),
                "parameter_name": str(
                    item.get("parameter_name")
                    or f"{_safe_parameter_stem(layer)}_nfp_r"
                ),
                "constraints": {
                    "min_diameter": "15.75mil",
                    "max_diameter": "20mil",
                },
                "constraints_checked": list(
                    item.get("constraints_checked")
                    or ["non_functional_pad_radius in [7.875mil, 10mil]"]
                ),
                "expected_effect": str(
                    item.get("expected_effect") or "decrease_impedance"
                ),
                "risk": str(
                    item.get("risk")
                    or "May over-lower impedance or couple to adjacent structures."
                ),
                "rollback": str(
                    item.get("rollback")
                    or f"remove {_safe_parameter_stem(layer)} NFP circle shapes or restore checkpoint"
                ),
                "candidate_source": "candidate_action_inventory",
            }
            if tdr_feature_time is not None:
                action["tdr_feature_time"] = tdr_feature_time
            if item.get("via_centers") is not None:
                action["via_centers"] = item.get("via_centers")
            actions.append(action)
    return actions


def _candidate_inventory_load_issues(loop_context: Mapping[str, Any]) -> list[str]:
    inventory_path = str(
        loop_context.get("candidate_action_inventory_path")
        or loop_context.get("candidate_action_inventory_file")
        or ""
    ).strip()
    if not inventory_path:
        return []
    path = Path(inventory_path)
    if not path.is_file():
        return [f"candidate_action_inventory_path not found: {inventory_path}"]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"candidate_action_inventory_path is not valid JSON: {exc}"]
    if not isinstance(loaded, Mapping):
        return ["candidate_action_inventory_path must contain a JSON object"]
    nested = loaded.get("candidate_action_inventory")
    if nested is not None and not isinstance(nested, Mapping):
        return ["candidate_action_inventory must be a JSON object when nested"]
    return []


def _candidate_inventory(loop_context: Mapping[str, Any]) -> dict[str, Any]:
    value = loop_context.get("candidate_action_inventory")
    if isinstance(value, Mapping) and value:
        return dict(value)
    inventory_path = str(
        loop_context.get("candidate_action_inventory_path")
        or loop_context.get("candidate_action_inventory_file")
        or ""
    ).strip()
    if not inventory_path:
        return {}
    path = Path(inventory_path)
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        return {}
    nested = loaded.get("candidate_action_inventory")
    if isinstance(nested, Mapping):
        return dict(nested)
    return dict(loaded)


def _candidate_inventory_issues(inventory: Mapping[str, Any]) -> list[str]:
    if not inventory:
        return []
    issues: list[str] = []
    issues.extend(
        _inventory_group_issues(
            inventory,
            keys=(
                "anti_pad_shape_layers",
                "anti_pad_candidates",
                "shape_backed_layers",
            ),
            required_value_keys=(
                ("plane_shape_ids", "shape_ids", "selected_shape_ids"),
                ("center_padstack_instance_ids", "padstack_instance_ids"),
            ),
            by_layer_value_keys=(
                ("plane_shape_ids_by_layer", "shape_ids_by_layer"),
                (
                    "center_padstack_instance_ids_by_layer",
                    "padstack_instance_ids_by_layer",
                ),
            ),
            allow_via_centers=True,
            require_bridge_disambiguation=True,
        )
    )
    issues.extend(
        _inventory_group_issues(
            inventory,
            keys=(
                "non_functional_pad_layers",
                "non_functional_pad_candidates",
                "mechanical_hole_layers",
            ),
            required_value_keys=(
                ("center_padstack_instance_ids", "padstack_instance_ids"),
            ),
            by_layer_value_keys=(
                (
                    "center_padstack_instance_ids_by_layer",
                    "padstack_instance_ids_by_layer",
                ),
            ),
            allow_via_centers=True,
            require_bridge_disambiguation=False,
        )
    )
    return issues


def _inventory_group_issues(
    inventory: Mapping[str, Any],
    *,
    keys: tuple[str, ...],
    required_value_keys: tuple[tuple[str, ...], ...],
    by_layer_value_keys: tuple[tuple[str, ...], ...],
    allow_via_centers: bool,
    require_bridge_disambiguation: bool,
) -> list[str]:
    issues: list[str] = []
    key, items = _inventory_items_with_key(inventory, *keys)
    if key is None:
        return issues
    if not isinstance(items, list):
        return [f"{key} must be a list of reviewed object entries"]
    for index, item in enumerate(items):
        label = f"{key}[{index}]"
        if not isinstance(item, Mapping):
            issues.append(
                f"{label} must be an object with reviewed geometry facts; "
                f"got {type(item).__name__}"
            )
            continue
        layers = _candidate_layers(item)
        if not layers:
            issues.append(f"{label} must include layer or layers")
            continue
        if not str(item.get("parasitic_target") or "").strip():
            issues.append(f"{label} must include parasitic_target")
        for layer in layers:
            for value_keys, by_layer_keys in zip(required_value_keys, by_layer_value_keys):
                values = _layer_values(
                    item,
                    layer,
                    *value_keys,
                    by_layer_keys=by_layer_keys,
                )
                has_centers = set(value_keys) & {
                    "center_padstack_instance_ids",
                    "padstack_instance_ids",
                }
                if values:
                    continue
                if has_centers and allow_via_centers and item.get("via_centers"):
                    continue
                keys_text = " or ".join(value_keys)
                issues.append(f"{label} layer {layer} must include {keys_text}")
            if not require_bridge_disambiguation:
                continue
            bridge_enabled = bool(item.get("bridge_between_vias", True))
            centers = _layer_values(
                item,
                layer,
                "center_padstack_instance_ids",
                "padstack_instance_ids",
                by_layer_keys=(
                    "center_padstack_instance_ids_by_layer",
                    "padstack_instance_ids_by_layer",
                ),
            )
            bridge_centers = _layer_values(
                item,
                layer,
                "bridge_center_padstack_instance_ids",
                by_layer_keys=("bridge_center_padstack_instance_ids_by_layer",),
            )
            bridge_via_centers = _as_list(item.get("bridge_via_centers"))
            if (
                bridge_enabled
                and len(centers) > 2
                and not bridge_centers
                and not bridge_via_centers
            ):
                issues.append(
                    f"{label} layer {layer} lists more than two centers with "
                    "bridge_between_vias enabled; include "
                    "bridge_center_padstack_instance_ids or bridge_via_centers"
                )
    return issues


def _llm_selected_action_within_inventory(
    action: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> tuple[bool, str]:
    if not inventory:
        return False, "candidate_action_inventory is empty"
    action_type = str(action.get("action_type") or "")
    if action_type == "anti_pad.enlarge":
        items = _inventory_items(
            inventory,
            "anti_pad_shape_layers",
            "anti_pad_candidates",
            "shape_backed_layers",
        )
        required_shape_keys = (
            "plane_shape_ids",
            "shape_ids",
            "selected_shape_ids",
        )
    elif action_type == "non_functional_pad.add_or_enlarge":
        items = _inventory_items(
            inventory,
            "non_functional_pad_layers",
            "non_functional_pad_candidates",
            "mechanical_hole_layers",
        )
        required_shape_keys = ()
    else:
        return False, f"unsupported action_type: {action_type or '<missing>'}"

    layers = _candidate_layers(action)
    if not layers:
        return False, "selected_action must name layers"
    for layer in layers:
        match = _inventory_item_for_layer(items, layer)
        if match is None:
            return False, f"layer {layer} is not in reviewed inventory"
        if required_shape_keys:
            action_shape_ids = set(
                _layer_values(action, layer, *required_shape_keys)
            )
            inventory_shape_ids = set(
                _layer_values(
                    match,
                    layer,
                    *required_shape_keys,
                    by_layer_keys=("plane_shape_ids_by_layer", "shape_ids_by_layer"),
                )
            )
            if not action_shape_ids:
                return False, f"anti-pad layer {layer} must include plane_shape_ids"
            if inventory_shape_ids and not action_shape_ids.issubset(inventory_shape_ids):
                return False, f"plane_shape_ids for {layer} are outside inventory"
        action_centers = set(
            _layer_values(
                action,
                layer,
                "center_padstack_instance_ids",
                "padstack_instance_ids",
            )
        )
        inventory_centers = set(
            _layer_values(
                match,
                layer,
                "center_padstack_instance_ids",
                "padstack_instance_ids",
                by_layer_keys=(
                    "center_padstack_instance_ids_by_layer",
                    "padstack_instance_ids_by_layer",
                ),
            )
        )
        if action_centers and inventory_centers and not action_centers.issubset(inventory_centers):
            return False, f"center_padstack_instance_ids for {layer} are outside inventory"
        bridge_centers = set(
            _layer_values(action, layer, "bridge_center_padstack_instance_ids")
        )
        inventory_bridge_centers = set(
            _layer_values(
                match,
                layer,
                "bridge_center_padstack_instance_ids",
                by_layer_keys=("bridge_center_padstack_instance_ids_by_layer",),
            )
        )
        if (
            bridge_centers
            and inventory_bridge_centers
            and not bridge_centers.issubset(inventory_bridge_centers)
        ):
            return False, f"bridge_center_padstack_instance_ids for {layer} are outside inventory"
    return True, "selected_action is bounded by reviewed inventory"


def _inventory_item_for_layer(
    items: list[Any],
    layer: str,
) -> Mapping[str, Any] | None:
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if layer in _candidate_layers(item):
            return item
    return None


def _inventory_items(inventory: Mapping[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = inventory.get(key)
        if isinstance(value, list):
            return value
    return []


def _inventory_items_with_key(
    inventory: Mapping[str, Any],
    *keys: str,
) -> tuple[str | None, Any]:
    for key in keys:
        if key in inventory:
            return key, inventory.get(key)
    return None, None


def _candidate_layers(item: Mapping[str, Any]) -> list[str]:
    raw_layers = item.get("layers")
    if raw_layers is None:
        raw_layers = item.get("layer")
    if isinstance(raw_layers, list):
        return [str(layer) for layer in raw_layers if str(layer).strip()]
    if raw_layers is not None and str(raw_layers).strip():
        return [str(raw_layers)]
    return []


def _layer_values(
    item: Mapping[str, Any],
    layer: str,
    *keys: str,
    by_layer_keys: tuple[str, ...] = (),
) -> list[Any]:
    for key in by_layer_keys:
        values_by_layer = item.get(key)
        if isinstance(values_by_layer, Mapping):
            values = values_by_layer.get(layer)
            if values is not None:
                return _as_list(values)
    for key in keys:
        values = item.get(key)
        if values is not None:
            return _as_list(values)
    return []


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _target_radius(
    item: Mapping[str, Any],
    geometry_constraints: Mapping[str, Any],
    *,
    constraint_key: str,
    default_value: float,
) -> dict[str, Any]:
    radius = item.get("target_radius")
    if isinstance(radius, Mapping):
        return dict(radius)
    if radius is not None:
        return {"value": float(radius), "unit": "mil"}
    constraints = geometry_constraints.get(constraint_key)
    if isinstance(constraints, Mapping):
        if constraint_key == "anti_pad":
            value = constraints.get("max_radius_mil")
        else:
            value = constraints.get("min_radius_mil")
        if value is not None:
            return {"value": float(value), "unit": "mil"}
    return {"value": default_value, "unit": "mil"}


def _safe_parameter_stem(layer: str) -> str:
    stem = "".join(
        char.lower() if char.isalnum() else "_"
        for char in str(layer).strip()
    ).strip("_")
    return stem or "layer"


def _merge_candidate_actions(
    explicit_actions: list[dict[str, Any]],
    generated_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in [*explicit_actions, *generated_actions]:
        key = json.dumps(action, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(action))
    return merged


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


def _remove_project_lock(project_path: Path, *, run_root: Path) -> list[str]:
    lock_path = Path(f"{project_path}.lock")
    if not lock_path.exists():
        return []
    lock_path = lock_path.resolve()
    _ensure_within(lock_path, run_root.resolve())
    try:
        lock_path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"could not remove AEDT project lock: {lock_path}"
        ) from exc
    return [str(lock_path)]


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


def _failed(
    payload: dict[str, Any],
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": NodeRunStatus.FAILED,
        "outcome": "failed",
        "output_payload": payload,
        "artifact_refs": list(payload.get("artifact_refs") or []),
        "error": {
            "code": code,
            "message": message,
        },
    }
