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


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed)}


def _success(output: dict[str, Any], *, outcome: str = "succeeded") -> dict[str, Any]:
    return {
        "status": "succeeded",
        "outcome": outcome,
        "output_payload": output,
        "artifact_refs": [],
    }
