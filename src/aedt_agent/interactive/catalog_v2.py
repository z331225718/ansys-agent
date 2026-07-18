from __future__ import annotations

from typing import Any


_DESKTOP_BOUND_HIDDEN_TOOLS = frozenset(
    {
        "open_layout_session",
        "close_layout_session",
        "list_layout_paths",
        "preview_parameterize_path_width",
        "apply_parameterize_path_width",
        "list_live_aedt_sessions",
        "launch_live_aedt_session",
        "create_live_hfss_design",
        "start_live_hfss_analysis",
    }
)


def capability_catalog_v2(*, desktop_bound: bool = False) -> dict[str, Any]:
    capabilities = [
        _cap("aedt.sessions.list", "read_only", ["live"], ["list_live_aedt_sessions"]),
        _cap(
            "aedt.sessions.launch",
            "process_start",
            ["live"],
            ["launch_live_aedt_session"],
            side_effects=["starts_assistant_owned_aedt_process"],
        ),
        _cap("aedt.sessions.attach", "read_only", ["live"], ["attach_live_aedt_session"]),
        _cap(
            "aedt.approvals.wait",
            "read_only",
            ["live"],
            ["wait_for_live_approval"],
            postconditions=["token_returned_only_after_external_user_approval"],
        ),
        _cap(
            "aedt.sessions.release",
            "read_only",
            ["live"],
            ["release_live_aedt_session"],
            postconditions=["aedt_process_remains_running", "projects_remain_open"],
        ),
        _cap("aedt.projects.info", "read_only", ["live"], ["get_live_aedt_project_info"]),
        _cap(
            "aedt.projects.save",
            "persistent_write",
            ["live"],
            ["preview_live_project_save", "apply_live_project_save"],
            approval="external_host_token",
            postconditions=["preview_digest_unchanged", "project_saved"],
        ),
        _cap(
            "hfss.design.create",
            "reversible_edit",
            ["live"],
            ["create_live_hfss_design"],
            side_effects=["project_becomes_dirty"],
        ),
        _cap(
            "hfss.design.inventory",
            "read_only",
            ["live"],
            ["get_live_hfss_design_inventory"],
        ),
        _cap(
            "aedt.setups.inventory",
            "read_only",
            ["live"],
            ["get_live_aedt_setup_inventory"],
            postconditions=["design_unchanged", "setup_and_sweep_names_returned"],
        ),
        _cap(
            "hfss.geometry.inventory",
            "read_only",
            ["live"],
            ["get_live_hfss_geometry_inventory"],
            postconditions=["design_unchanged", "face_selector_digest_returned"],
        ),
        _cap(
            "hfss.setup.create",
            "reversible_edit",
            ["live"],
            ["preview_live_hfss_setup_create", "apply_live_hfss_setup_create"],
            approval="external_host_token",
            side_effects=["project_becomes_dirty"],
            postconditions=["setup_readback_verified", "rollback_on_failure", "project_not_saved"],
        ),
        _cap(
            "hfss.setup.update",
            "reversible_edit",
            ["live"],
            ["preview_live_hfss_setup_update", "apply_live_hfss_setup_update"],
            approval="external_host_token",
            side_effects=["project_becomes_dirty"],
            postconditions=["setup_snapshot_unchanged", "readback_verified", "rollback_on_failure"],
        ),
        _cap(
            "aedt.frequency_sweep.create",
            "reversible_edit",
            ["live"],
            ["preview_live_frequency_sweep_create", "apply_live_frequency_sweep_create"],
            approval="external_host_token",
            side_effects=["project_becomes_dirty"],
            postconditions=["bounded_range", "setup_snapshot_unchanged", "readback_verified", "rollback_on_failure"],
        ),
        _cap(
            "hfss.report.create",
            "reversible_edit",
            ["live"],
            ["preview_live_hfss_report_create", "apply_live_hfss_report_create"],
            approval="external_host_token",
            side_effects=["project_becomes_dirty"],
            postconditions=["report_readback_verified", "rollback_on_failure", "project_not_saved"],
        ),
        _cap(
            "hfss.boundary.create",
            "reversible_edit",
            ["live"],
            ["preview_live_hfss_boundary_create", "apply_live_hfss_boundary_create"],
            approval="external_host_token",
            side_effects=["project_becomes_dirty"],
            postconditions=[
                "explicit_face_ids",
                "geometry_digest_unchanged",
                "boundary_readback_verified",
                "rollback_on_failure",
                "project_not_saved",
            ],
        ),
        _cap(
            "hfss.analysis.start",
            "expensive",
            ["live"],
            ["preview_live_hfss_analysis_start", "apply_live_hfss_analysis_start"],
            approval="external_host_token",
            side_effects=["solver_job_started"],
            postconditions=["setup_digest_unchanged", "resource_budget_bounded", "non_blocking"],
            products=["hfss", "layout"],
        ),
        _cap(
            "hfss.analysis.status",
            "read_only",
            ["live"],
            ["get_live_hfss_analysis_status"],
            products=["hfss", "layout"],
        ),
        _cap(
            "hfss.analysis.cancel",
            "expensive",
            ["live"],
            ["preview_live_hfss_analysis_cancel", "apply_live_hfss_analysis_cancel"],
            approval="external_host_token",
            side_effects=["running_solver_job_interrupted"],
            postconditions=["running_state_digest_unchanged_before_cancel"],
            products=["hfss", "layout"],
        ),
        _cap(
            "hfss.results.export",
            "persistent_write",
            ["live"],
            ["preview_live_hfss_results_export", "apply_live_hfss_results_export"],
            approval="external_host_token",
            side_effects=["result_artifacts_written_to_managed_export_root"],
            postconditions=["design_unchanged", "artifact_sha256_verified", "evidence_manifest_written"],
        ),
        _cap(
            "layout.paths.list",
            "read_only",
            ["artifact", "live"],
            ["list_layout_paths", "list_live_layout_paths"],
            postconditions=["design_unchanged"],
        ),
        _cap(
            "layout.routing.inventory",
            "read_only",
            ["live"],
            ["get_live_layout_routing_inventory"],
            postconditions=["design_unchanged", "variables_and_routing_dimensions_returned"],
        ),
        _cap(
            "layout.objects.inventory",
            "read_only",
            ["live"],
            ["get_live_layout_object_inventory"],
            postconditions=["design_unchanged", "layout_object_categories_returned"],
        ),
        _cap(
            "layout.object_properties.inventory",
            "read_only",
            ["live"],
            ["get_live_layout_object_property_inventory"],
            postconditions=["design_unchanged", "explicit_via_or_component_properties_returned"],
        ),
        _cap(
            "layout.object_properties.update",
            "reversible_edit",
            ["live"],
            [
                "preview_live_layout_object_property_update",
                "apply_live_layout_object_property_update",
            ],
            approval="external_host_token",
            side_effects=["project_becomes_dirty"],
            postconditions=["snapshot_unchanged", "readback_verified", "batch_rollback_on_failure"],
        ),
        _cap(
            "aedt.variables.inventory",
            "read_only",
            ["live"],
            ["get_live_aedt_variable_inventory"],
            postconditions=["design_unchanged", "project_and_design_variables_returned"],
        ),
        _cap(
            "aedt.variables.upsert",
            "reversible_edit",
            ["live"],
            ["preview_live_aedt_variable_upsert", "apply_live_aedt_variable_upsert"],
            approval="external_host_token",
            side_effects=["project_becomes_dirty"],
            postconditions=["snapshot_unchanged", "readback_verified", "rollback_on_failure"],
        ),
        _cap(
            "layout.path_width.parameterize",
            "reversible_edit",
            ["artifact", "live"],
            [
                "preview_parameterize_path_width",
                "apply_parameterize_path_width",
                "preview_live_parameterize_path_width",
                "apply_live_parameterize_path_width",
            ],
            approval="external_host_token_for_live",
            postconditions=["target_digest_unchanged", "readback_verified", "rollback_on_failure"],
        ),
        _cap(
            "exploration.operation.propose_validate",
            "read_only",
            ["live"],
            ["get_ansys_operation_plan_schema", "propose_ansys_operation", "validate_ansys_operation"],
            postconditions=["declarative_plan_only", "current_version_source_evidence", "policy_validated"],
        ),
        _cap(
            "exploration.operation.execute",
            "reversible_edit",
            ["live"],
            ["preview_exploratory_operation", "apply_exploratory_operation"],
            approval="external_host_token_for_reversible_edit",
            side_effects=["bounded_live_property_edit"],
            postconditions=["target_digest_unchanged", "readback_verified", "server_snapshot_rollback"],
        ),
        _cap(
            "capability.trace.capture",
            "read_only",
            ["live"],
            ["capture_capability_trace"],
            postconditions=["server_owned_trace_only", "append_only_event_log", "secrets_redacted"],
        ),
        _cap(
            "capability.promotion.generate",
            "read_only",
            ["live"],
            ["promote_ansys_capability"],
            side_effects=["writes_disabled_candidate_under_managed_review_root"],
            postconditions=["verified_trace_only", "no_auto_apply", "no_hot_registration", "no_commit"],
        ),
        _cap(
            "workflow.graph.catalog",
            "read_only",
            ["workflow"],
            ["list_ansys_workflows", "inspect_ansys_workflow", "get_ansys_workflow_status"],
            postconditions=["allowlisted_templates_only", "graph_state_unchanged"],
        ),
        _cap(
            "workflow.graph.execute",
            "expensive",
            ["workflow"],
            [
                "preview_ansys_workflow_start",
                "apply_ansys_workflow_start",
                "preview_ansys_workflow_advance",
                "apply_ansys_workflow_advance",
            ],
            approval="external_host_token_per_start_and_step",
            side_effects=["mission_state_written", "approved_graph_step_may_run_workers"],
            postconditions=["target_binding_verified", "one_scheduler_step_per_apply"],
        ),
    ]
    unavailable = []
    if desktop_bound:
        available = []
        for capability in capabilities:
            tools = [tool for tool in capability["tools"] if tool not in _DESKTOP_BOUND_HIDDEN_TOOLS]
            if not tools:
                unavailable.append(
                    {
                        "name": capability["name"],
                        "reason": "unavailable_in_desktop_bound_scope",
                    }
                )
                continue
            filtered = dict(capability)
            filtered["tools"] = tools
            filtered["modes"] = [mode for mode in capability["modes"] if mode != "artifact"]
            available.append(filtered)
        capabilities = available
    return {
        "version": "2",
        "scope": "desktop_bound" if desktop_bound else "unified",
        "compatibility": {
            "v1_unchanged": True,
            "workflow_graph_unchanged": True,
            "workflow_mcp_added": True,
        },
        "defaults": {
            "implicit_live_target": False,
            "release_required": True,
            "live_apply_saves_project": False,
            "code_fallback_enabled": False,
        },
        "capabilities": capabilities,
        "unavailable_capabilities": unavailable,
    }


def _cap(
    name: str,
    risk: str,
    modes: list[str],
    tools: list[str],
    *,
    approval: str = "none",
    side_effects: list[str] | None = None,
    postconditions: list[str] | None = None,
    products: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "risk": risk,
        "modes": modes,
        "tools": tools,
        "approval": approval,
        "side_effects": side_effects or [],
        "postconditions": postconditions or [],
        "products": products or [],
    }
