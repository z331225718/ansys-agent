from __future__ import annotations

from typing import Any

from aedt_agent.agent.graph_executors import GraphNodeExecutionContext, GraphNodeExecutorRegistry


def register_live_workflow_handlers(
    registry: GraphNodeExecutorRegistry,
    live_manager,
    binding_resolver,
) -> None:
    registry.register(
        "assistant.live.layout.collect_inventory",
        lambda context: _collect_layout_inventory(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.audit_scorecard",
        _audit_layout_inventory,
    )
    registry.register(
        "assistant.live.layout.select_paths",
        lambda context: _select_layout_paths(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.preview_width_parameterization",
        lambda context: _preview_width_parameterization(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.apply_width_parameterization",
        lambda context: _apply_width_parameterization(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.width_scorecard",
        _width_scorecard,
    )


def _collect_layout_inventory(
    context: GraphNodeExecutionContext,
    live_manager,
    binding_resolver,
) -> dict[str, Any]:
    payload = dict(context.input_payload)
    live = binding_resolver(context.graph_run.graph_run_id)
    session_id = str(live.get("live_session_id") or "")
    binding = dict(live.get("target_binding") or {})
    project_name = str(binding.get("active_project") or "")
    design_name = str(binding.get("active_design") or "")
    if not session_id or not project_name or not design_name:
        raise ValueError("live workflow binding is incomplete")
    selector = dict(payload.get("selector") or {})
    routing = live_manager.layout_routing_inventory(
        session_id,
        project_name=project_name,
        design_name=design_name,
        selector=selector,
    )
    objects = live_manager.layout_object_inventory(
        session_id,
        project_name=project_name,
        design_name=design_name,
    )
    variables = live_manager.variable_inventory(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
    )
    setups = live_manager.setup_inventory(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
    )
    output = {
        **payload,
        "status": "collected",
        "project_name": project_name,
        "design_name": design_name,
        "routing": routing,
        "objects": objects,
        "variables": variables,
        "setups": setups,
        "live_session_reused": True,
    }
    return _success(output)


def _audit_layout_inventory(context: GraphNodeExecutionContext) -> dict[str, Any]:
    payload = dict(context.input_payload)
    routing = dict(payload.get("routing") or {})
    objects = dict(payload.get("objects") or {})
    variables = dict(payload.get("variables") or {})
    setups = dict(payload.get("setups") or {})
    checks = [
        _check("live_session_reused", payload.get("live_session_reused") is True),
        _check("routing_inventory", routing.get("design_unchanged") is True),
        _check("object_inventory", objects.get("design_unchanged") is True),
        _check("variable_inventory", variables.get("design_unchanged") is True),
        _check("setup_inventory", setups.get("design_unchanged") is True),
    ]
    passed = all(item["passed"] for item in checks)
    summary = {
        "path_count": int(routing.get("path_count") or 0),
        "net_count": len(routing.get("nets") or []),
        "layer_count": len(routing.get("layers") or []),
        "variable_count": int(variables.get("count") or 0),
        "setup_count": int(setups.get("setup_count") or 0),
        "unavailable_object_categories": list(objects.get("unavailable_categories") or []),
    }
    output = {
        **payload,
        "status": "passed" if passed else "failed",
        "checks": checks,
        "summary": summary,
        "live_session_reused": True,
    }
    return _success(output, outcome="passed" if passed else "failed")


def _select_layout_paths(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = dict(context.input_payload)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    selector = dict(payload.get("selector") or {})
    selection = live_manager.list_layout_paths(
        session_id,
        project_name=project_name,
        design_name=design_name,
        selector=selector,
    )
    if int(selection.get("count") or 0) <= 0:
        raise ValueError("live width workflow selector matched no layout paths")
    return _success({**payload, "selection": selection, "live_session_reused": True})


def _preview_width_parameterization(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = dict(context.input_payload)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    preview = live_manager.preview_layout_width(
        session_id,
        project_name=project_name,
        design_name=design_name,
        selector=dict(payload["selector"]),
        variable_name=str(payload["variable_name"]),
        variable_value=str(payload["variable_value"]),
    )
    return _success(
        {
            **payload,
            "operation_preview_id": preview["preview_id"],
            "operation_approval": preview.get("approval_request") or {},
            "operation_preview": preview,
            "live_session_reused": True,
        }
    )


def _apply_width_parameterization(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = dict(context.input_payload)
    session_id, _, _, binding = _live_target(context, binding_resolver)
    token = str(binding.get("operation_approval_token") or "")
    if not token:
        raise ValueError(
            "operation_approval_token is required after wait_for_live_approval approves the width preview"
        )
    result = live_manager.apply_layout_width(
        session_id,
        preview_id=str(payload["operation_preview_id"]),
        approval_token=token,
    )
    return _success({**payload, "operation_result": result, "live_session_reused": True})


def _width_scorecard(context: GraphNodeExecutionContext) -> dict[str, Any]:
    payload = dict(context.input_payload)
    result = dict(payload.get("operation_result") or {})
    checks = [
        _check("verified", result.get("status") == "verified"),
        _check("readback_count", result.get("verified_count") == result.get("target_count")),
        _check("project_not_saved", result.get("project_saved") is False),
        _check("live_session_reused", payload.get("live_session_reused") is True),
    ]
    passed = all(item["passed"] for item in checks)
    output = {
        **payload,
        "status": "passed" if passed else "failed",
        "checks": checks,
        "summary": {
            "target_count": int(result.get("target_count") or 0),
            "verified_count": int(result.get("verified_count") or 0),
            "variable_name": payload.get("variable_name"),
            "variable_value": payload.get("variable_value"),
        },
        "live_session_reused": True,
    }
    return _success(output, outcome="passed" if passed else "failed")


def _live_target(context, binding_resolver) -> tuple[str, str, str, dict[str, Any]]:
    binding = binding_resolver(context.graph_run.graph_run_id)
    session_id = str(binding.get("live_session_id") or "")
    target = dict(binding.get("target_binding") or {})
    project_name = str(target.get("active_project") or "")
    design_name = str(target.get("active_design") or "")
    if not session_id or not project_name or not design_name:
        raise ValueError("live workflow binding is incomplete")
    return session_id, project_name, design_name, binding


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed)}


def _success(output: dict[str, Any], *, outcome: str = "succeeded") -> dict[str, Any]:
    return {
        "status": "succeeded",
        "outcome": outcome,
        "output_payload": output,
        "artifact_refs": [],
    }
