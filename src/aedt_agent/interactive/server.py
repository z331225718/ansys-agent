from __future__ import annotations

import os

from aedt_agent.exploration.tool_contracts import AnsysOperationPlanInput, operation_plan_schema

from aedt_agent.interactive.kernel import InteractiveKernel
from aedt_agent.interactive.process_manager import ProcessLayoutSessionManager
from aedt_agent.live.manager import LiveAedtSessionManager


def create_server(
    *,
    kernel: InteractiveKernel | None = None,
    live_manager: LiveAedtSessionManager | None = None,
    workflow_manager=None,
):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the mcp extra to run the server: pip install -e .[mcp]") from exc

    runtime = kernel or InteractiveKernel(
        session_manager=ProcessLayoutSessionManager()
    )
    expected_port_text = os.environ.get("AEDT_AGENT_EXPECTED_PORT", "").strip()
    expected_port = int(expected_port_text) if expected_port_text else None
    expected_project = os.environ.get("AEDT_AGENT_EXPECTED_PROJECT")
    expected_design = os.environ.get("AEDT_AGENT_EXPECTED_DESIGN")
    expected_version = os.environ.get("AEDT_AGENT_EXPECTED_VERSION", "").strip() or None
    strict_desktop = os.environ.get("AEDT_AGENT_DESKTOP_STRICT", "").strip().lower() in {"1", "true", "yes"}
    default_live_version = expected_version or "2026.1"
    live = live_manager or LiveAedtSessionManager(
        required_port=expected_port,
        required_project=expected_project,
        required_design=expected_design,
        required_version=expected_version,
        strict_desktop=strict_desktop,
    )
    if workflow_manager is None:
        from aedt_agent.interactive.workflows import AssistantWorkflowManager

        workflow_manager = AssistantWorkflowManager(live_manager=live)
    workflows = workflow_manager
    scope_instructions = (
        "This Desktop-bound server can operate only on its preselected AEDT port, project, and design. "
        "Artifact sessions, target discovery, and AEDT launch are unavailable. "
        if strict_desktop
        else "Operate only on an explicitly selected AEDT target or a managed artifact session. "
        "Prefer attaching to a user-selected running session; launch AEDT only when the user requests it. "
    )
    server = FastMCP(
        "ansys-assistant",
        instructions=scope_instructions
        + (
            "Always release live sessions after use without closing AEDT or projects. "
            "Artifact tools never overwrite the source project: if a request explicitly forbids snapshots, "
            "working copies, or preview while demanding source overwrite, report blocked before calling them. "
            "Never invent an approval token; live apply and save require a token issued by the external host. "
            "When a Desktop approval dialog is configured, wait_for_live_approval only returns a token after "
            "the user clicks Approve; a rejected or expired request must not be retried implicitly. "
            "Report missing capabilities and backend failures truthfully."
            " Existing graph workflows are guarded Harness capabilities: inspect them first, preview start or "
            "advance, wait for native approval, and execute at most one graph step per approved apply."
            " A live workflow operation token is distinct from its graph-step token and may only be passed in "
            "operation_approval_token after wait_for_live_approval approves the operation preview."
        ),
    )

    def register_tool(enabled: bool = True):
        if enabled:
            return server.tool()

        def leave_unregistered(function):
            return function

        return leave_unregistered

    @server.tool()
    async def list_ansys_capabilities() -> dict:
        catalog = runtime.list_capabilities()
        if not strict_desktop:
            return catalog
        capabilities = catalog.get("capabilities", []) if isinstance(catalog, dict) else []
        unavailable = [
            str(item.get("name"))
            for item in capabilities
            if isinstance(item, dict) and item.get("name")
        ]
        return {
            **(catalog if isinstance(catalog, dict) else {"version": "1"}),
            "scope": "desktop_bound",
            "capabilities": [],
            "unavailable_capabilities": unavailable,
            "replacement_catalog_tool": "list_ansys_capabilities_v2",
        }

    @server.tool()
    async def list_ansys_capabilities_v2() -> dict:
        """List unified live/artifact capabilities, risks, approvals, side effects, and postconditions."""
        from aedt_agent.interactive.catalog_v2 import capability_catalog_v2

        return capability_catalog_v2(desktop_bound=strict_desktop)

    @server.tool()
    async def list_ansys_workflows() -> dict:
        """List the allowlisted, reusable graph workflows available through the Runtime Harness."""
        return workflows.list_workflows()

    @server.tool()
    async def inspect_ansys_workflow(workflow_id: str) -> dict:
        """Inspect a workflow graph, required inputs, workers, risk, and approval policy."""
        return workflows.inspect_workflow(workflow_id)

    @server.tool()
    async def preview_ansys_workflow_start(
        live_session_id: str,
        workflow_id: str,
        goal: str,
        initial_payload: dict,
        max_steps: int = 32,
    ) -> dict:
        """Freeze a session-bound workflow start; no graph node executes during preview."""
        return workflows.preview_start(
            live_session_id,
            workflow_id=workflow_id,
            goal=goal,
            initial_payload=initial_payload,
            max_steps=max_steps,
        )

    @server.tool()
    async def apply_ansys_workflow_start(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Create an approved mission and graph run without executing its first node."""
        return workflows.apply_start(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def get_ansys_workflow_status(graph_run_id: str) -> dict:
        """Read graph, node, job, handoff, and supervision state without advancing it."""
        return workflows.status(graph_run_id)

    @server.tool()
    async def preview_ansys_workflow_advance(live_session_id: str, graph_run_id: str) -> dict:
        """Freeze the next single graph scheduler step for native user approval."""
        return workflows.preview_advance(live_session_id, graph_run_id=graph_run_id)

    @server.tool()
    async def apply_ansys_workflow_advance(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
        max_workers: int = 1,
        operation_approval_token: str = "",
    ) -> dict:
        """Execute exactly one approved graph scheduler step and return its full status."""
        return workflows.apply_advance(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
            max_workers=max_workers,
            operation_approval_token=operation_approval_token,
        )

    @register_tool(not strict_desktop)
    async def open_layout_session(
        project_path: str,
        writable: bool = False,
        workspace: str | None = None,
        version: str = "2026.1",
        edb_backend: str = "auto",
    ) -> dict:
        """Open only a snapshot/working copy; do not call if the user forbids copies or demands source overwrite."""
        return runtime.open_layout_session(
            project_path,
            writable=writable,
            workspace=workspace,
            version=version,
            edb_backend=edb_backend,
        )

    @register_tool(not strict_desktop)
    async def close_layout_session(session_id: str) -> dict:
        return runtime.close_layout_session(session_id)

    @register_tool(not strict_desktop)
    async def list_layout_paths(session_id: str, selector: dict | None = None) -> dict:
        return runtime.execute_capability(
            "layout.paths.list",
            {"session_id": session_id, "selector": selector or {}},
        )

    @register_tool(not strict_desktop)
    async def preview_parameterize_path_width(
        session_id: str,
        selector: dict,
        variable_name: str,
        variable_value: str,
    ) -> dict:
        """Preview a working-copy edit; never use this to satisfy a request that prohibits preview or copies."""
        return runtime.execute_capability(
            "layout.path_width.parameterize.preview",
            {
                "session_id": session_id,
                "selector": selector,
                "variable_name": variable_name,
                "variable_value": variable_value,
            },
        )

    @register_tool(not strict_desktop)
    async def apply_parameterize_path_width(session_id: str, preview_id: str) -> dict:
        """Apply only to the managed working copy; never overwrites the source project."""
        return runtime.execute_capability(
            "layout.path_width.parameterize.apply",
            {"session_id": session_id, "preview_id": preview_id},
        )

    @register_tool(not strict_desktop)
    async def list_live_aedt_sessions() -> dict:
        """Discover running AEDT processes and listener ports without attaching."""
        return live.list_sessions()

    @register_tool(not strict_desktop)
    async def launch_live_aedt_session(
        version: str = "2026.1",
        port: int = 0,
        install_dir: str | None = None,
        non_graphical: bool = False,
        timeout: float = 120.0,
    ) -> dict:
        """Launch and attach one assistant-owned AEDT gRPC session; release still leaves AEDT running."""
        return live.launch(
            version=version,
            port=port,
            install_dir=install_dir,
            non_graphical=non_graphical,
            timeout=timeout,
        )

    @server.tool()
    async def attach_live_aedt_session(
        pid: int | None = None,
        port: int | None = None,
        version: str = default_live_version,
    ) -> dict:
        """Attach by pid, port, or a matching pid+port pair; release the session when done."""
        return live.attach(pid=pid, port=port, version=version)

    @server.tool()
    async def release_live_aedt_session(live_session_id: str) -> dict:
        """Release PyAEDT wrappers without closing AEDT or projects."""
        return live.release(live_session_id)

    @server.tool()
    async def get_live_aedt_project_info(live_session_id: str) -> dict:
        """Read project, active design, and design type from an attached AEDT session."""
        return live.project_info(live_session_id)

    @server.tool()
    async def preview_live_project_save(live_session_id: str, project_name: str) -> dict:
        """Preview saving one open live project; this does not write to disk."""
        return live.preview_project_save(live_session_id, project_name=project_name)

    @server.tool()
    async def apply_live_project_save(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Save one live project only with a short-lived token issued by the external host."""
        return live.apply_project_save(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @register_tool(not strict_desktop)
    async def create_live_hfss_design(
        live_session_id: str,
        project_name: str,
        design_name: str,
        solution_type: str = "DrivenModal",
    ) -> dict:
        """Create or activate an HFSS design in memory; this tool does not save the project."""
        return live.create_hfss_design(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            solution_type=solution_type,
        )

    @server.tool()
    async def get_live_hfss_design_inventory(
        live_session_id: str,
        project_name: str,
        design_name: str,
    ) -> dict:
        """Read HFSS solution type, setups, ports, boundaries, and reports without modifying the design."""
        return live.hfss_design_inventory(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
        )

    @server.tool()
    async def get_live_aedt_setup_inventory(
        live_session_id: str,
        product: str,
        project_name: str,
        design_name: str,
    ) -> dict:
        """List setup and sweep names for one existing HFSS or HFSS 3D Layout design."""
        return live.setup_inventory(
            live_session_id,
            product=product,
            project_name=project_name,
            design_name=design_name,
        )

    @server.tool()
    async def get_live_hfss_geometry_inventory(
        live_session_id: str,
        project_name: str,
        design_name: str,
        object_names: list[str] | None = None,
    ) -> dict:
        """Read explicit HFSS object and face IDs for later boundary or port selection."""
        return live.hfss_geometry_inventory(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            object_names=object_names,
        )

    @server.tool()
    async def preview_live_hfss_setup_create(
        live_session_id: str,
        project_name: str,
        design_name: str,
        setup_name: str,
        setup_type: str = "HFSSDriven",
        properties: dict | None = None,
    ) -> dict:
        """Preview creating one HFSS setup from an allowlisted property set without modifying the project."""
        return live.preview_hfss_setup(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            setup_name=setup_name,
            setup_type=setup_type,
            properties=properties,
        )

    @server.tool()
    async def apply_live_hfss_setup_create(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Create and verify an HFSS setup with an external host token; does not save the project."""
        return live.apply_hfss_setup(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def preview_live_hfss_setup_update(
        live_session_id: str,
        project_name: str,
        design_name: str,
        setup_name: str,
        properties: dict,
    ) -> dict:
        """Preview updating allowlisted properties on one existing HFSS setup."""
        return live.preview_hfss_setup_update(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            setup_name=setup_name,
            properties=properties,
        )

    @server.tool()
    async def apply_live_hfss_setup_update(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Apply a setup update with native approval, stale-state checks, readback, and rollback."""
        return live.apply_hfss_setup_update(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def preview_live_frequency_sweep_create(
        live_session_id: str,
        product: str,
        project_name: str,
        design_name: str,
        setup_name: str,
        sweep_name: str,
        range_type: str = "LinearCount",
        sweep_type: str = "Interpolating",
        unit: str = "GHz",
        start_frequency: float = 1.0,
        stop_frequency: float = 10.0,
        count: int | None = 401,
        step_size: float | None = None,
        save_fields: bool = True,
    ) -> dict:
        """Preview a bounded linear-count or linear-step sweep for HFSS or HFSS 3D Layout."""
        return live.preview_frequency_sweep_create(
            live_session_id,
            product=product,
            project_name=project_name,
            design_name=design_name,
            setup_name=setup_name,
            sweep_name=sweep_name,
            range_type=range_type,
            sweep_type=sweep_type,
            unit=unit,
            start_frequency=start_frequency,
            stop_frequency=stop_frequency,
            count=count,
            step_size=step_size,
            save_fields=save_fields,
        )

    @server.tool()
    async def apply_live_frequency_sweep_create(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Create and verify one previewed sweep with native approval and rollback on failure."""
        return live.apply_frequency_sweep_create(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def preview_live_hfss_report_create(
        live_session_id: str,
        project_name: str,
        design_name: str,
        report_name: str,
        setup_sweep_name: str,
        expressions: list[str],
        domain: str = "Sweep",
        plot_type: str = "Rectangular Plot",
    ) -> dict:
        """Preview creating one named HFSS report without modifying the project."""
        return live.preview_hfss_report(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            report_name=report_name,
            setup_sweep_name=setup_sweep_name,
            expressions=expressions,
            domain=domain,
            plot_type=plot_type,
        )

    @server.tool()
    async def apply_live_hfss_report_create(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Create and verify an HFSS report with an external host token; does not save the project."""
        return live.apply_hfss_report(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def preview_live_hfss_boundary_create(
        live_session_id: str,
        project_name: str,
        design_name: str,
        boundary_kind: str,
        boundary_name: str,
        assignment_face_ids: list[int],
        references: list[str | int] | None = None,
        options: dict | None = None,
    ) -> dict:
        """Preview radiation, wave-port, or lumped-port creation using explicit face IDs from geometry inventory."""
        return live.preview_hfss_boundary(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            boundary_kind=boundary_kind,
            boundary_name=boundary_name,
            assignment_face_ids=assignment_face_ids,
            references=references,
            options=options,
        )

    @server.tool()
    async def apply_live_hfss_boundary_create(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Create and verify an HFSS boundary or port with an external host token; does not save."""
        return live.apply_hfss_boundary(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @register_tool(not strict_desktop)
    async def start_live_hfss_analysis(
        live_session_id: str,
        project_name: str,
        design_name: str,
        setup_name: str,
        blocking: bool = False,
        product: str = "hfss",
    ) -> dict:
        """Compatibility entry point; prefer the preview/apply start tools for approved resource-bounded solves."""
        return live.start_hfss_analysis(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            setup_name=setup_name,
            blocking=blocking,
            product=product,
        )

    @server.tool()
    async def preview_live_hfss_analysis_start(
        live_session_id: str,
        project_name: str,
        design_name: str,
        setup_name: str,
        cores: int | None = None,
        tasks: int | None = None,
        gpus: int | None = None,
        use_auto_settings: bool = True,
        product: str = "hfss",
    ) -> dict:
        """Freeze setup state and a bounded compute budget before starting a non-blocking HFSS solve."""
        return live.preview_hfss_analysis_start(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            setup_name=setup_name,
            cores=cores,
            tasks=tasks,
            gpus=gpus,
            use_auto_settings=use_auto_settings,
            product=product,
        )

    @server.tool()
    async def apply_live_hfss_analysis_start(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Start a previewed HFSS solve only with an external host approval; always returns without waiting."""
        return live.apply_hfss_analysis_start(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def get_live_hfss_analysis_status(
        live_session_id: str,
        project_name: str,
        design_name: str,
        setup_name: str = "",
        product: str = "hfss",
    ) -> dict:
        """Read running state and setup inventory for one live HFSS design."""
        return live.hfss_analysis_status(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            setup_name=setup_name,
            product=product,
        )

    @server.tool()
    async def preview_live_hfss_analysis_cancel(
        live_session_id: str,
        project_name: str,
        design_name: str,
        setup_name: str = "",
        clean_stop: bool = True,
        product: str = "hfss",
    ) -> dict:
        """Preview interrupting the currently running AEDT simulation without changing it yet."""
        return live.preview_hfss_analysis_cancel(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            setup_name=setup_name,
            clean_stop=clean_stop,
            product=product,
        )

    @server.tool()
    async def apply_live_hfss_analysis_cancel(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Interrupt a previewed solve only with an external host approval token."""
        return live.apply_hfss_analysis_cancel(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def preview_live_hfss_results_export(
        live_session_id: str,
        project_name: str,
        design_name: str,
        export_kind: str,
        setup_name: str = "",
        sweep_name: str = "",
        report_name: str = "",
        artifact_name: str = "",
    ) -> dict:
        """Preview Touchstone or report CSV export into the server-managed export root."""
        return live.preview_hfss_export(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            export_kind=export_kind,
            setup_name=setup_name,
            sweep_name=sweep_name,
            report_name=report_name,
            artifact_name=artifact_name,
        )

    @server.tool()
    async def apply_live_hfss_results_export(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Export into the restricted root and emit a SHA-256 evidence manifest after host approval."""
        return live.apply_hfss_export(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def list_live_layout_paths(
        live_session_id: str,
        project_name: str,
        design_name: str,
        selector: dict | None = None,
    ) -> dict:
        """List Path/line objects in a live 3D Layout design; selector supports names, nets, layers, target_width."""
        return live.list_layout_paths(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            selector=selector,
        )

    @server.tool()
    async def get_live_layout_routing_inventory(
        live_session_id: str,
        project_name: str,
        design_name: str,
        selector: dict | None = None,
    ) -> dict:
        """Inventory live layout paths plus nets, layers, width expressions, and design/project variables."""
        return live.layout_routing_inventory(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            selector=selector,
        )

    @server.tool()
    async def get_live_layout_object_inventory(
        live_session_id: str,
        project_name: str,
        design_name: str,
    ) -> dict:
        """List 3D Layout components, pins, vias, nets, lines, shapes, and void categories read-only."""
        return live.layout_object_inventory(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
        )

    @server.tool()
    async def get_live_layout_object_property_inventory(
        live_session_id: str,
        project_name: str,
        design_name: str,
        object_kind: str,
        names: list[str] | None = None,
    ) -> dict:
        """Read stable properties for selected 3D Layout vias or components."""
        return live.layout_object_property_inventory(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            object_kind=object_kind,
            names=names,
        )

    @server.tool()
    async def preview_live_layout_object_property_update(
        live_session_id: str,
        project_name: str,
        design_name: str,
        object_kind: str,
        names: list[str],
        properties: dict,
    ) -> dict:
        """Preview allowlisted property changes on explicit via or component names."""
        return live.preview_layout_object_property_update(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            object_kind=object_kind,
            names=names,
            properties=properties,
        )

    @server.tool()
    async def apply_live_layout_object_property_update(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Apply a via/component property preview with approval, readback, and batch rollback."""
        return live.apply_layout_object_property_update(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def get_live_aedt_variable_inventory(
        live_session_id: str,
        product: str,
        project_name: str,
        design_name: str,
    ) -> dict:
        """List design and project variables for one existing HFSS or 3D Layout design."""
        return live.variable_inventory(
            live_session_id,
            product=product,
            project_name=project_name,
            design_name=design_name,
        )

    @server.tool()
    async def preview_live_aedt_variable_upsert(
        live_session_id: str,
        product: str,
        project_name: str,
        design_name: str,
        variable_name: str,
        expression: str,
    ) -> dict:
        """Preview creating or updating one AEDT variable without changing the design."""
        return live.preview_variable_upsert(
            live_session_id,
            product=product,
            project_name=project_name,
            design_name=design_name,
            variable_name=variable_name,
            expression=expression,
        )

    @server.tool()
    async def apply_live_aedt_variable_upsert(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Apply one previewed variable change with native approval, rollback, and readback."""
        return live.apply_variable_upsert(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def preview_live_parameterize_path_width(
        live_session_id: str,
        project_name: str,
        design_name: str,
        selector: dict,
        variable_name: str,
        variable_value: str,
    ) -> dict:
        """Preview without modifying; apply needs an external host token and the session must be released."""
        return live.preview_layout_width(
            live_session_id,
            project_name=project_name,
            design_name=design_name,
            selector=selector,
            variable_name=variable_name,
            variable_value=variable_value,
        )

    @server.tool()
    async def apply_live_parameterize_path_width(
        live_session_id: str,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        """Apply a live preview only with a host-approved token; does not save the project."""
        return live.apply_layout_width(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def wait_for_live_approval(
        live_session_id: str,
        preview_id: str,
        timeout_seconds: float = 0,
    ) -> dict:
        """Poll the external Desktop Host; only a human-approved preview can return a one-use token."""
        return live.wait_for_approval(
            live_session_id,
            preview_id=preview_id,
            timeout_seconds=timeout_seconds,
        )

    @server.tool()
    async def get_ansys_operation_plan_schema() -> dict:
        """Return the exact closed schema and a read-only example for ansys-operation-plan/v1."""
        return operation_plan_schema()

    @server.tool()
    async def propose_ansys_operation(plan: AnsysOperationPlanInput) -> dict:
        """Store an exact v1 plan and return the only candidate_id accepted by validate/preview."""
        return live.propose_exploratory_operation(plan)

    @server.tool()
    async def validate_ansys_operation(candidate_id: str) -> dict:
        """Validate a candidate_id returned by propose; use preview only after status=validated."""
        return live.validate_exploratory_operation(candidate_id)

    @server.tool()
    async def preview_exploratory_operation(live_session_id: str, candidate_id: str) -> dict:
        """After propose+validate, preflight that validated candidate_id and freeze target state."""
        return live.preview_exploratory_operation(live_session_id, candidate_id=candidate_id)

    @server.tool()
    async def apply_exploratory_operation(
        live_session_id: str,
        preview_id: str,
        approval_token: str = "",
    ) -> dict:
        """Apply only the frozen plan; reversible edits require native approval and verified readback."""
        return live.apply_exploratory_operation(
            live_session_id,
            preview_id=preview_id,
            approval_token=approval_token,
        )

    @server.tool()
    async def capture_capability_trace(candidate_id: str) -> dict:
        """Capture the append-only, redacted trace owned by a server-created exploratory candidate."""
        return live.capture_capability_trace(candidate_id)

    @server.tool()
    async def promote_ansys_capability(trace_id: str, target_kind: str = "auto") -> dict:
        """Final action for a verified trace: generate a disabled candidate, then report it; do not capture again."""
        return live.promote_capability_candidate(trace_id, target_kind=target_kind)

    return server


def main() -> None:
    create_server().run(show_banner=False)


if __name__ == "__main__":
    main()
