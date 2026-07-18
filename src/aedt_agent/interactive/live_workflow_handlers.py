from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from aedt_agent.agent.evaluation import build_sparameter_evidence
from aedt_agent.agent.graph_executors import GraphNodeExecutionContext, GraphNodeExecutorRegistry
from aedt_agent.layout.mapped_touchstone import score_mapped_touchstone


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
    registry.register(
        "assistant.live.layout.validate_solve_setup",
        lambda context: _validate_solve_setup(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.preview_analysis_start",
        lambda context: _preview_analysis_start(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.apply_analysis_start",
        lambda context: _apply_analysis_start(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.solve_submission_scorecard",
        lambda context: _solve_submission_scorecard(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.validate_monitor_setup",
        lambda context: _validate_monitor_setup(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.poll_analysis_status",
        lambda context: _poll_analysis_status(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.analysis_stopped_scorecard",
        lambda context: _analysis_stopped_scorecard(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.validate_results_export",
        lambda context: _validate_results_export(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.preview_results_export",
        lambda context: _preview_results_export(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.apply_results_export",
        lambda context: _apply_results_export(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.results_export_scorecard",
        lambda context: _results_export_scorecard(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.validate_touchstone_score",
        lambda context: _validate_touchstone_score(context, live_manager, binding_resolver),
    )
    registry.register(
        "assistant.live.layout.touchstone_scorecard",
        lambda context: _touchstone_scorecard(context, live_manager, binding_resolver),
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
    technology = live_manager.layout_technology_inventory(
        session_id,
        project_name=project_name,
        design_name=design_name,
        max_items=500,
        include_padstack_layers=False,
    )
    connectivity_selector = dict(payload.get("connectivity_selector") or {})
    if not connectivity_selector and selector.get("nets"):
        connectivity_selector["nets"] = list(selector["nets"])
    connectivity = live_manager.layout_connectivity_inventory(
        session_id,
        project_name=project_name,
        design_name=design_name,
        selector=connectivity_selector,
        max_items=500,
        include_geometry_names=False,
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
        "technology": technology,
        "connectivity": connectivity,
        "live_session_reused": True,
    }
    return _success(output)


def _audit_layout_inventory(context: GraphNodeExecutionContext) -> dict[str, Any]:
    payload = dict(context.input_payload)
    routing = dict(payload.get("routing") or {})
    objects = dict(payload.get("objects") or {})
    variables = dict(payload.get("variables") or {})
    setups = dict(payload.get("setups") or {})
    technology = dict(payload.get("technology") or {})
    connectivity = dict(payload.get("connectivity") or {})
    checks = [
        _check("live_session_reused", payload.get("live_session_reused") is True),
        _check("routing_inventory", routing.get("design_unchanged") is True),
        _check("object_inventory", objects.get("design_unchanged") is True),
        _check("variable_inventory", variables.get("design_unchanged") is True),
        _check("setup_inventory", setups.get("design_unchanged") is True),
        _check("technology_inventory", technology.get("design_unchanged") is True),
        _check("connectivity_inventory", connectivity.get("design_unchanged") is True),
    ]
    passed = all(item["passed"] for item in checks)
    summary = {
        "path_count": int(routing.get("path_count") or 0),
        "net_count": len(routing.get("nets") or []),
        "layer_count": len(routing.get("layers") or []),
        "variable_count": int(variables.get("count") or 0),
        "setup_count": int(setups.get("setup_count") or 0),
        "stackup_layer_count": int((technology.get("counts") or {}).get("stackup_layers") or 0),
        "padstack_count": int((technology.get("counts") or {}).get("padstacks") or 0),
        "port_count": int((technology.get("counts") or {}).get("ports") or 0),
        "differential_pair_count": int(
            (technology.get("counts") or {}).get("differential_pairs") or 0
        ),
        "connectivity_net_count": int((connectivity.get("counts") or {}).get("nets") or 0),
        "component_count": int((connectivity.get("counts") or {}).get("components") or 0),
        "pin_count": int((connectivity.get("counts") or {}).get("pins") or 0),
        "via_count": int((connectivity.get("counts") or {}).get("vias") or 0),
        "truncated_connectivity_sections": list(
            connectivity.get("truncated_sections") or []
        ),
        "unavailable_connectivity_sections": list(
            connectivity.get("unavailable_sections") or []
        ),
        "unavailable_technology_sections": list(
            technology.get("unavailable_sections") or []
        ),
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
        "parameterization_result": result,
        "summary": {
            "target_count": int(result.get("target_count") or 0),
            "verified_count": int(result.get("verified_count") or 0),
            "variable_name": payload.get("variable_name"),
            "variable_value": payload.get("variable_value"),
        },
        "live_session_reused": True,
    }
    return _success(output, outcome="passed" if passed else "failed")


def _validate_solve_setup(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    setup_name = str(payload.get("setup_name") or "")
    sweep_name = str(payload.get("sweep_name") or "")
    inventory = live_manager.setup_inventory(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
    )
    setup = next((item for item in inventory.get("setups", []) if item.get("name") == setup_name), None)
    if setup is None:
        raise ValueError(f"unknown live layout setup: {setup_name}")
    if sweep_name and sweep_name not in set(setup.get("sweeps") or []):
        raise ValueError(f"unknown sweep {sweep_name} in setup {setup_name}")
    resources = {
        "cores": payload.get("cores"),
        "tasks": payload.get("tasks"),
        "gpus": payload.get("gpus"),
        "use_auto_settings": payload.get("use_auto_settings", True),
    }
    return _success(
        {
            **payload,
            "setup_inventory": inventory,
            "resources": resources,
            "live_session_reused": True,
        }
    )


def _preview_analysis_start(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    resources = dict(payload["resources"])
    preview = live_manager.preview_hfss_analysis_start(
        session_id,
        project_name=project_name,
        design_name=design_name,
        setup_name=str(payload["setup_name"]),
        cores=resources.get("cores"),
        tasks=resources.get("tasks"),
        gpus=resources.get("gpus"),
        use_auto_settings=resources.get("use_auto_settings", True),
        product="layout",
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


def _apply_analysis_start(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, _, _, binding = _live_target(context, binding_resolver)
    token = str(binding.get("operation_approval_token") or "")
    if not token:
        raise ValueError("operation_approval_token is required after approval of the solve preview")
    result = live_manager.apply_hfss_analysis_start(
        session_id,
        preview_id=str(payload["operation_preview_id"]),
        approval_token=token,
    )
    return _success(
        {
            **payload,
            "operation_result": result,
            "solve_result": result,
            "live_session_reused": True,
        }
    )


def _solve_submission_scorecard(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    result = dict(payload.get("operation_result") or {})
    status = live_manager.hfss_analysis_status(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
        setup_name=str(payload["setup_name"]),
    )
    checks = [
        _check("submitted", result.get("status") == "submitted" and result.get("started") is True),
        _check("non_blocking", result.get("blocking") is False),
        _check("status_observed", status.get("running") is True or status.get("latest_run") is not None),
        _check("project_not_saved", result.get("project_saved") is False),
    ]
    passed = all(item["passed"] for item in checks)
    output = {
        **payload,
        "status": "passed" if passed else "failed",
        "checks": checks,
        "summary": {
            "setup_name": payload["setup_name"],
            "run_id": result.get("run_id"),
            "resources": result.get("resources"),
            "observed_running": status.get("running"),
        },
        "analysis_status": status,
        "live_session_reused": True,
    }
    return _success(output, outcome="passed" if passed else "failed")


def _validate_monitor_setup(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    setup_name = str(payload.get("setup_name") or "").strip()
    if not setup_name:
        raise ValueError("setup_name is required for live solve monitoring")
    inventory = live_manager.setup_inventory(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
    )
    if setup_name not in {str(item.get("name") or "") for item in inventory.get("setups", [])}:
        raise ValueError(f"unknown live layout setup: {setup_name}")
    return _success(
        {
            **payload,
            "setup_name": setup_name,
            "setup_inventory": inventory,
            "poll_count": 0,
            "live_session_reused": True,
        }
    )


def _poll_analysis_status(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    status = live_manager.hfss_analysis_status(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
        setup_name=str(payload["setup_name"]),
    )
    latest_run = dict(status.get("latest_run") or {})
    running = status.get("running") is True or latest_run.get("state") in {"submitted", "running"}
    output = {
        **payload,
        "analysis_status": status,
        "poll_count": int(payload.get("poll_count") or 0) + 1,
        "observed_running": bool(payload.get("observed_running"))
        or status.get("running") is True
        or latest_run.get("state") == "running",
        "live_session_reused": True,
    }
    return _success(output, outcome="running" if running else "stopped")


def _analysis_stopped_scorecard(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    _live_target(context, binding_resolver)
    status = dict(payload.get("analysis_status") or {})
    latest_run = dict(status.get("latest_run") or {})
    solution_evidence = dict(latest_run.get("solution_evidence") or {})
    checks = [
        _check("target_setup", status.get("setup_name") == payload.get("setup_name")),
        _check("solver_not_running", status.get("running") is False),
        _check("poll_observed", int(payload.get("poll_count") or 0) >= 1),
        _check("not_known_canceled", latest_run.get("state") != "canceled"),
    ]
    passed = all(item["passed"] for item in checks)
    output = {
        **payload,
        "status": "passed" if passed else "failed",
        "checks": checks,
        "summary": {
            "setup_name": payload.get("setup_name"),
            "poll_count": int(payload.get("poll_count") or 0),
            "run_id": latest_run.get("run_id"),
            "last_known_state": latest_run.get("state") or "untracked_not_running",
            "solve_running_observed": bool(payload.get("observed_running")),
            "solve_success_verified": solution_evidence.get("solve_success_verified") is True,
            "result_freshness_verified": solution_evidence.get("result_freshness_verified") is True,
            "solution_verification_reasons": list(
                solution_evidence.get("verification_reasons") or []
            ),
        },
        "live_session_reused": True,
    }
    return _success(output, outcome="passed" if passed else "failed")


def _validate_results_export(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    export_kind = str(payload.get("export_kind") or "").strip().casefold()
    if export_kind not in {"touchstone", "report_csv"}:
        raise ValueError("export_kind must be touchstone or report_csv")
    status = live_manager.hfss_analysis_status(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
        setup_name=str(payload.get("setup_name") or "").strip(),
    )
    latest_run = dict(status.get("latest_run") or {})
    if status.get("running") is True or latest_run.get("state") in {"submitted", "running"}:
        raise ValueError("cannot export live layout results while AEDT solve is running or pending")
    inventory = live_manager.setup_inventory(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
    )
    setup_name = str(payload.get("setup_name") or "").strip()
    sweep_name = str(payload.get("sweep_name") or "").strip()
    report_name = str(payload.get("report_name") or "").strip()
    if export_kind == "touchstone":
        setup = next((item for item in inventory.get("setups", []) if item.get("name") == setup_name), None)
        if setup is None:
            raise ValueError("touchstone export requires an existing setup_name")
        if sweep_name and sweep_name not in set(setup.get("sweeps") or []):
            raise ValueError(f"unknown sweep {sweep_name} in setup {setup_name}")
    elif not report_name:
        raise ValueError("report_csv export requires report_name")
    spec = {
        "product": "layout",
        "export_kind": export_kind,
        "setup_name": setup_name,
        "sweep_name": sweep_name,
        "report_name": report_name,
        "artifact_name": str(payload.get("artifact_name") or "").strip(),
    }
    return _success(
        {
            **payload,
            "export_kind": export_kind,
            "export_spec": spec,
            "analysis_status": status,
            "setup_inventory": inventory,
            "live_session_reused": True,
        }
    )


def _preview_results_export(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    spec = dict(payload["export_spec"])
    preview = live_manager.preview_hfss_export(
        session_id,
        project_name=project_name,
        design_name=design_name,
        export_kind=spec["export_kind"],
        setup_name=spec["setup_name"],
        sweep_name=spec["sweep_name"],
        report_name=spec["report_name"],
        artifact_name=spec["artifact_name"],
        product="layout",
    )
    finalized_spec = {
        "product": "layout",
        "export_kind": str(preview.get("export_kind") or spec["export_kind"]),
        "setup_name": str(preview.get("setup_name") or spec["setup_name"]),
        "sweep_name": str(preview.get("sweep_name") or spec["sweep_name"]),
        "report_name": str(preview.get("report_name") or spec["report_name"]),
        "artifact_name": str(preview.get("artifact_name") or spec["artifact_name"]),
    }
    return _success(
        {
            **payload,
            "export_spec": finalized_spec,
            "operation_preview_id": preview["preview_id"],
            "operation_approval": preview.get("approval_request") or {},
            "operation_preview": preview,
            "live_session_reused": True,
        }
    )


def _apply_results_export(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, _, _, binding = _live_target(context, binding_resolver)
    token = str(binding.get("operation_approval_token") or "")
    if not token:
        raise ValueError("operation_approval_token is required after approval of the export preview")
    result = live_manager.apply_hfss_export(
        session_id,
        preview_id=str(payload["operation_preview_id"]),
        approval_token=token,
    )
    return _success(
        {
            **payload,
            "operation_result": result,
            "export_result": result,
            "live_session_reused": True,
        }
    )


def _results_export_scorecard(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    _, project_name, design_name, _ = _live_target(context, binding_resolver)
    result = dict(payload.get("export_result") or payload.get("operation_result") or {})
    solve_result = dict(payload.get("solve_result") or {})
    analysis_status = dict(payload.get("analysis_status") or {})
    latest_run = dict(analysis_status.get("latest_run") or {})
    solution_evidence = dict(latest_run.get("solution_evidence") or {})
    artifact = dict(result.get("artifact") or {})
    artifact_path = Path(str(artifact.get("path") or ""))
    manifest_path = Path(str(result.get("manifest_path") or ""))
    artifact_exists = artifact_path.is_file()
    manifest_exists = manifest_path.is_file()
    actual_sha256 = _file_sha256(artifact_path) if artifact_exists else ""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_exists else {}
    except (OSError, ValueError, TypeError):
        manifest = {}
    manifest_artifact = dict(manifest.get("artifact") or {})
    manifest_spec = dict(manifest.get("spec") or {})
    checks = [
        _check("verified", result.get("status") == "verified"),
        _check("layout_product", result.get("product") == "layout"),
        _check("artifact_exists", artifact_exists),
        _check("artifact_nonempty", artifact_exists and artifact_path.stat().st_size > 0),
        _check("artifact_size", artifact_exists and artifact.get("bytes") == artifact_path.stat().st_size),
        _check("artifact_sha256", bool(actual_sha256) and artifact.get("sha256") == actual_sha256),
        _check("manifest_exists", manifest_exists),
        _check(
            "manifest_artifact",
            manifest_artifact.get("path") == str(artifact_path)
            and manifest_artifact.get("sha256") == actual_sha256
            and manifest_artifact.get("bytes") == artifact.get("bytes"),
        ),
        _check("manifest_colocated", manifest_exists and artifact_exists and manifest_path.parent == artifact_path.parent),
        _check("manifest_target", manifest.get("project_name") == project_name and manifest.get("design_name") == design_name),
        _check("manifest_spec", manifest_spec == payload.get("export_spec")),
        _check("project_unchanged", result.get("project_unchanged") is True),
        _check("project_not_saved", result.get("project_saved") is False),
        _check("solver_not_running", analysis_status.get("running") is False),
        _check("not_known_canceled", latest_run.get("state") != "canceled"),
    ]
    export_verified = all(item["passed"] for item in checks)
    solve_verified = False
    if solve_result:
        solve_verified = (
            solve_result.get("status") == "submitted"
            and solve_result.get("started") is True
            and solve_result.get("blocking") is False
            and solve_result.get("project_saved") is False
        )
        checks.extend(
            [
                _check(
                    "solve_submitted",
                    solve_result.get("status") == "submitted" and solve_result.get("started") is True,
                ),
                _check("solve_non_blocking", solve_result.get("blocking") is False),
                _check("solve_project_not_saved", solve_result.get("project_saved") is False),
            ]
        )
    passed = all(item["passed"] for item in checks)
    artifact_refs = [str(path) for path in (artifact_path, manifest_path) if path.is_file()]
    output = {
        **payload,
        "status": "passed" if passed else "failed",
        "checks": checks,
        "summary": {
            "export_kind": payload.get("export_kind"),
            "artifact_path": str(artifact_path) if artifact_exists else "",
            "manifest_path": str(manifest_path) if manifest_exists else "",
            "sha256": actual_sha256,
            "bytes": artifact_path.stat().st_size if artifact_exists else 0,
            "solve_run_id": solve_result.get("run_id"),
            "poll_count": int(payload.get("poll_count") or 0),
            "last_known_state": latest_run.get("state") or "untracked_not_running",
            "solve_running_observed": bool(payload.get("observed_running")),
            "solve_submission_verified": solve_verified,
            "solve_success_verified": solution_evidence.get("solve_success_verified") is True,
            "result_freshness_verified": solution_evidence.get("result_freshness_verified") is True,
            "solution_verification_reasons": list(
                solution_evidence.get("verification_reasons") or []
            ),
            "result_export_verified": export_verified,
        },
        "artifact_refs": artifact_refs,
        "live_session_reused": True,
    }
    return _success(
        output,
        outcome="passed" if passed else "failed",
        artifact_refs=artifact_refs,
    )


def _validate_touchstone_score(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    session_id, project_name, design_name, _ = _live_target(context, binding_resolver)
    mode = str(payload.get("sparameter_mode") or "").strip().casefold()
    if mode not in {"single_ended", "differential"}:
        raise ValueError("sparameter_mode must be single_ended or differential")
    expected_port_order = _name_list(payload.get("expected_port_order"), "expected_port_order")
    expected_count = 1 if mode == "single_ended" else 2
    source_ports = _name_list(payload.get("source_ports"), "source_ports", expected_count)
    destination_ports = _name_list(
        payload.get("destination_ports"),
        "destination_ports",
        expected_count,
    )
    if set(source_ports).intersection(destination_ports):
        raise ValueError("source_ports and destination_ports must not overlap")
    missing_ports = [
        item
        for item in [*source_ports, *destination_ports]
        if item not in expected_port_order
    ]
    if missing_ports:
        raise ValueError(f"scoring ports are absent from expected_port_order: {missing_ports}")
    require_defined_pairs = payload.get("require_defined_differential_pairs", False)
    if type(require_defined_pairs) is not bool:
        raise ValueError("require_defined_differential_pairs must be a boolean")
    score_spec = {
        "sparameter_mode": mode,
        "expected_port_order": expected_port_order,
        "source_ports": source_ports,
        "destination_ports": destination_ports,
        "frequency_start_ghz": _finite_number(payload, "frequency_start_ghz"),
        "frequency_stop_ghz": _finite_number(payload, "frequency_stop_ghz"),
        "rl_target_db": _finite_number(payload, "rl_target_db"),
        "insertion_loss_min_db": _finite_number(payload, "insertion_loss_min_db"),
        "reference_impedance_ohm": _finite_number(payload, "reference_impedance_ohm"),
        "require_defined_differential_pairs": require_defined_pairs,
    }
    if score_spec["frequency_start_ghz"] < 0 or score_spec["frequency_stop_ghz"] <= score_spec["frequency_start_ghz"]:
        raise ValueError("frequency range must satisfy 0 <= start < stop")
    if score_spec["rl_target_db"] > 0 or score_spec["insertion_loss_min_db"] > 0:
        raise ValueError("RL and insertion-loss limits must be non-positive dB values")
    if score_spec["reference_impedance_ohm"] <= 0:
        raise ValueError("reference_impedance_ohm must be positive")

    status = live_manager.hfss_analysis_status(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
        setup_name=str(payload.get("setup_name") or "").strip(),
    )
    latest_run = dict(status.get("latest_run") or {})
    if status.get("running") is True or latest_run.get("state") in {"submitted", "running"}:
        raise ValueError("cannot score live layout results while AEDT solve is running or pending")
    inventory = live_manager.setup_inventory(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
    )
    actual_port_order = [str(item) for item in inventory.get("ports") or []]
    if actual_port_order != expected_port_order:
        raise ValueError(
            "expected_port_order does not match the current AEDT excitation order; refresh setup inventory"
        )
    technology_inventory = live_manager.layout_technology_inventory(
        session_id,
        project_name=project_name,
        design_name=design_name,
        max_items=2_000,
        include_padstack_layers=False,
    )
    if list(technology_inventory.get("ports") or []) != expected_port_order:
        raise ValueError(
            "expected_port_order does not match the current Layout technology inventory"
        )
    pair_validation = _differential_pair_validation(
        list(technology_inventory.get("differential_pairs") or []),
        source_ports=source_ports,
        destination_ports=destination_ports,
        mode=mode,
    )
    score_spec["differential_pair_validation"] = pair_validation
    if require_defined_pairs and not pair_validation.get("all_pairs_defined_and_active"):
        raise ValueError(
            "the requested differential source/destination pairs are not both active AEDT differential pairs"
        )
    setup_name = str(payload.get("setup_name") or "").strip()
    sweep_name = str(payload.get("sweep_name") or "").strip()
    setup = next((item for item in inventory.get("setups", []) if item.get("name") == setup_name), None)
    if setup is None:
        raise ValueError("Touchstone score requires an existing setup_name")
    if sweep_name and sweep_name not in set(setup.get("sweeps") or []):
        raise ValueError(f"unknown sweep {sweep_name} in setup {setup_name}")
    solution_inventory = live_manager.solution_inventory(
        session_id,
        product="layout",
        project_name=project_name,
        design_name=design_name,
        setup_name=setup_name,
    )
    if solution_inventory.get("target_solution_available") is not True:
        raise ValueError(
            "Touchstone score requires available solution data for the selected setup"
        )
    export_spec = {
        "product": "layout",
        "export_kind": "touchstone",
        "setup_name": setup_name,
        "sweep_name": sweep_name,
        "report_name": "",
        "artifact_name": str(payload.get("artifact_name") or "").strip(),
    }
    return _success(
        {
            **payload,
            "export_kind": "touchstone",
            "score_spec": score_spec,
            "export_spec": export_spec,
            "analysis_status": status,
            "setup_inventory": inventory,
            "technology_inventory": technology_inventory,
            "solution_inventory": solution_inventory,
            "live_session_reused": True,
        }
    )


def _touchstone_scorecard(context, live_manager, binding_resolver) -> dict[str, Any]:
    payload = _payload(context)
    _, project_name, design_name, _ = _live_target(context, binding_resolver)
    result = dict(payload.get("export_result") or payload.get("operation_result") or {})
    artifact = dict(result.get("artifact") or {})
    artifact_path = Path(str(artifact.get("path") or ""))
    manifest_path = Path(str(result.get("manifest_path") or ""))
    artifact_exists = artifact_path.is_file()
    manifest_exists = manifest_path.is_file()
    actual_sha256 = _file_sha256(artifact_path) if artifact_exists else ""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_exists else {}
    except (OSError, ValueError, TypeError):
        manifest = {}
    manifest_artifact = dict(manifest.get("artifact") or {})
    analysis_status = dict(payload.get("analysis_status") or {})
    latest_run = dict(analysis_status.get("latest_run") or {})
    solution_evidence = dict(latest_run.get("solution_evidence") or {})
    solution_inventory = dict(payload.get("solution_inventory") or {})
    solve_result = dict(payload.get("solve_result") or {})
    parameterization_result = dict(payload.get("parameterization_result") or {})
    solve_submission_verified = bool(solve_result) and (
        solve_result.get("status") == "submitted"
        and solve_result.get("started") is True
        and solve_result.get("blocking") is False
        and solve_result.get("project_saved") is False
    )
    parameterization_verified = bool(parameterization_result) and (
        parameterization_result.get("status") == "verified"
        and parameterization_result.get("verified_count")
        == parameterization_result.get("target_count")
        and parameterization_result.get("project_saved") is False
    )
    score_spec = dict(payload.get("score_spec") or {})
    expected_ports = list(score_spec.get("expected_port_order") or [])
    preview_ports = list((payload.get("operation_preview") or {}).get("ports") or [])
    manifest_ports = list(manifest.get("ports") or [])
    technology_ports = list((payload.get("technology_inventory") or {}).get("ports") or [])
    checks = [
        _check("verified_export", result.get("status") == "verified"),
        _check("layout_product", result.get("product") == "layout"),
        _check("touchstone_export", payload.get("export_kind") == "touchstone"),
        _check("artifact_exists", artifact_exists),
        _check("artifact_nonempty", artifact_exists and artifact_path.stat().st_size > 0),
        _check("artifact_sha256", bool(actual_sha256) and artifact.get("sha256") == actual_sha256),
        _check("manifest_exists", manifest_exists),
        _check(
            "manifest_artifact",
            manifest_artifact.get("path") == str(artifact_path)
            and manifest_artifact.get("sha256") == actual_sha256
            and manifest_artifact.get("bytes") == artifact.get("bytes"),
        ),
        _check("manifest_colocated", manifest_exists and artifact_exists and manifest_path.parent == artifact_path.parent),
        _check("manifest_target", manifest.get("project_name") == project_name and manifest.get("design_name") == design_name),
        _check("manifest_spec", manifest.get("spec") == payload.get("export_spec")),
        _check("preview_port_order", preview_ports == expected_ports),
        _check("manifest_port_order", manifest_ports == expected_ports),
        _check("technology_port_order", technology_ports == expected_ports),
        _check("project_unchanged", result.get("project_unchanged") is True),
        _check("project_not_saved", result.get("project_saved") is False),
        _check("solver_not_running", analysis_status.get("running") is False),
        _check("solve_not_known_canceled", latest_run.get("state") != "canceled"),
        _check(
            "target_solution_available",
            solution_inventory.get("target_solution_available") is True,
        ),
    ]
    if solve_result:
        checks.extend(
            [
                _check(
                    "solve_submitted",
                    solve_result.get("status") == "submitted" and solve_result.get("started") is True,
                ),
                _check("solve_non_blocking", solve_result.get("blocking") is False),
                _check("solve_project_not_saved", solve_result.get("project_saved") is False),
            ]
        )
    if parameterization_result:
        checks.extend(
            [
                _check("parameterization_verified", parameterization_result.get("status") == "verified"),
                _check(
                    "parameterization_readback_count",
                    parameterization_result.get("verified_count")
                    == parameterization_result.get("target_count"),
                ),
                _check(
                    "parameterization_project_not_saved",
                    parameterization_result.get("project_saved") is False,
                ),
            ]
        )
    if not all(item["passed"] for item in checks):
        artifact_refs = [str(path) for path in (artifact_path, manifest_path) if path.is_file()]
        return _success(
            {
                **payload,
                "status": "failed",
                "checks": checks,
                "summary": {"reason": "export evidence or port order verification failed"},
                "artifact_refs": artifact_refs,
                "live_session_reused": True,
            },
            outcome="failed",
            artifact_refs=artifact_refs,
        )

    score = score_mapped_touchstone(
        artifact_path,
        port_order=expected_ports,
        sparameter_mode=str(score_spec["sparameter_mode"]),
        source_ports=list(score_spec["source_ports"]),
        destination_ports=list(score_spec["destination_ports"]),
        frequency_start_ghz=float(score_spec["frequency_start_ghz"]),
        frequency_stop_ghz=float(score_spec["frequency_stop_ghz"]),
        rl_target_db=float(score_spec["rl_target_db"]),
        insertion_loss_min_db=float(score_spec["insertion_loss_min_db"]),
        reference_impedance_ohm=float(score_spec["reference_impedance_ohm"]),
    )
    bounded_samples = list(score.pop("bounded_samples"))
    spectral_evidence = build_sparameter_evidence(
        trace_id=f"{context.graph_run.graph_run_id}:{score['return_loss_trace']}",
        samples=bounded_samples,
        artifact_ref=str(artifact_path),
        rl_target_db=float(score["rl_target_db"]),
        bucket_count=min(128, max(1, len(bounded_samples))),
    )
    evidence_path = artifact_path.parent / f"{artifact_path.stem}.touchstone-score.json"
    evidence_payload = {
        "schema_version": 1,
        "source_artifact": {
            "path": str(artifact_path),
            "sha256": actual_sha256,
            "bytes": artifact_path.stat().st_size,
        },
        "export_manifest": str(manifest_path),
        "score_spec": score_spec,
        "score": score,
        "sparameter_evidence": spectral_evidence,
    }
    temporary_path = evidence_path.with_suffix(evidence_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(evidence_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(evidence_path)
    evidence_sha256 = _file_sha256(evidence_path)
    checks.extend(
        [
            _check("score_evidence_exists", evidence_path.is_file()),
            _check("score_evidence_nonempty", evidence_path.stat().st_size > 0),
            _check("score_source_sha256", evidence_payload["source_artifact"]["sha256"] == actual_sha256),
        ]
    )
    passed = all(item["passed"] for item in checks) and score["status"] == "pass"
    artifact_refs = [str(artifact_path), str(manifest_path), str(evidence_path)]
    output = {
        **payload,
        "status": "passed" if passed else "failed",
        "checks": checks,
        "score": score,
        "sparameter_evidence": spectral_evidence,
        "summary": {
            "score_status": score["status"],
            "touchstone_kind": score["touchstone_kind"],
            "sparameter_mode": score["sparameter_mode"],
            "port_order": score["port_order"],
            "source_ports": score["source_ports"],
            "destination_ports": score["destination_ports"],
            "differential_pair_validation": score_spec.get(
                "differential_pair_validation"
            ),
            "return_loss_trace": score["return_loss_trace"],
            "insertion_loss_trace": score["insertion_loss_trace"],
            "rl_target_db": score["rl_target_db"],
            "rl_worst_db": score["rl_worst_db"],
            "rl_worst_frequency_ghz": score["rl_worst_frequency_ghz"],
            "insertion_loss_min_db": score["insertion_loss_min_db"],
            "insertion_worst_db_in_band": score["insertion_worst_db_in_band"],
            "insertion_worst_frequency_ghz": score["insertion_worst_frequency_ghz"],
            "reference_impedance_ohm": score["reference_impedance_ohm"],
            "tdr_evaluated": False,
            "artifact_path": str(artifact_path),
            "manifest_path": str(manifest_path),
            "score_evidence_path": str(evidence_path),
            "score_evidence_sha256": evidence_sha256,
            "solve_run_id": solve_result.get("run_id"),
            "solve_submission_verified": solve_submission_verified,
            "solve_success_verified": solution_evidence.get("solve_success_verified") is True,
            "result_freshness_verified": solution_evidence.get("result_freshness_verified") is True,
            "solution_verification_reasons": list(
                solution_evidence.get("verification_reasons") or []
            ),
            "target_solution_available": solution_inventory.get("target_solution_available") is True,
            "solution_snapshot_digest": solution_inventory.get("snapshot_digest"),
            "solve_running_observed": bool(payload.get("observed_running")),
            "poll_count": int(payload.get("poll_count") or 0),
            "parameterization_verified": parameterization_verified,
            "parameterized_target_count": int(parameterization_result.get("target_count") or 0),
        },
        "artifact_refs": artifact_refs,
        "live_session_reused": True,
    }
    return _success(
        output,
        outcome="passed" if passed else "failed",
        artifact_refs=artifact_refs,
    )


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


def _name_list(value: Any, field: str, expected_count: int | None = None) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    names = [str(item).strip() for item in value]
    if any(not item for item in names):
        raise ValueError(f"{field} contains an empty name")
    if len(set(names)) != len(names):
        raise ValueError(f"{field} contains duplicate names")
    if expected_count is not None and len(names) != expected_count:
        raise ValueError(f"{field} must contain exactly {expected_count} port name(s)")
    return names


def _differential_pair_validation(
    records: list[dict[str, Any]],
    *,
    source_ports: list[str],
    destination_ports: list[str],
    mode: str,
) -> dict[str, Any]:
    if mode != "differential":
        return {
            "status": "not_applicable",
            "source_pair": "not_applicable",
            "destination_pair": "not_applicable",
            "all_pairs_defined_and_active": True,
        }

    def match_pair(ports: list[str]) -> tuple[str, str]:
        for record in records:
            if (
                record.get("positive_terminal") == ports[0]
                and record.get("negative_terminal") == ports[1]
            ):
                return (
                    "defined_active" if record.get("active") is True else "defined_inactive",
                    str(record.get("differential_mode") or ""),
                )
        if any(record.get("terminal_mapping_status") == "unavailable" for record in records):
            return "terminal_mapping_unavailable", ""
        return "not_defined", ""

    source_status, source_mode = match_pair(source_ports)
    destination_status, destination_mode = match_pair(destination_ports)
    all_active = source_status == "defined_active" and destination_status == "defined_active"
    return {
        "status": "verified" if all_active else "unverified",
        "source_pair": source_status,
        "source_differential_mode": source_mode,
        "destination_pair": destination_status,
        "destination_differential_mode": destination_mode,
        "all_pairs_defined_and_active": all_active,
    }


def _finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _payload(context: GraphNodeExecutionContext) -> dict[str, Any]:
    payload = dict(context.input_payload)
    payload.pop("_handoffs", None)
    return payload


def _success(
    output: dict[str, Any],
    *,
    outcome: str = "succeeded",
    artifact_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "outcome": outcome,
        "output_payload": output,
        "artifact_refs": list(artifact_refs or []),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
