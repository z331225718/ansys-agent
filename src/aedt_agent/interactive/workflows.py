from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from aedt_agent.agent.graph_runner import advance_graph, create_graph_run, graph_status
from aedt_agent.agent.graph_template import GraphTemplate, load_graph_template
from aedt_agent.live.broker import LiveAedtError


_MAX_PAYLOAD_BYTES = 256 * 1024
_DEFAULT_TEMPLATE_IDS = (
    "layout_live_audit",
    "layout_live_parameterize_width",
    "layout_live_results_export",
    "layout_live_solve_export",
    "layout_live_solve_monitor",
    "layout_live_solve_start",
    "brd_before_after_compare",
    "brd_channel_optimize",
    "brd_iterative_optimize",
    "brd_local_cut_build",
    "brd_local_cut_solve_evidence",
    "brd_multi_channel_demo",
    "brd_real_solve_evidence",
    "brd_recorded_void_action",
    "brd_reviewed_model_optimize_loop",
    "via_optimize_demo",
)
_LIVE_SESSION_WORKFLOWS = frozenset(
    {
        "layout_live_audit",
        "layout_live_parameterize_width",
        "layout_live_results_export",
        "layout_live_solve_export",
        "layout_live_solve_monitor",
        "layout_live_solve_start",
    }
)
_LIVE_WORKFLOW_RISKS = {
    "layout_live_audit": "read_only",
    "layout_live_parameterize_width": "reversible_edit",
    "layout_live_results_export": "persistent_write",
    "layout_live_solve_export": "expensive",
    "layout_live_solve_monitor": "read_only",
    "layout_live_solve_start": "expensive",
}


class AssistantWorkflowManager:
    """Expose the existing graph runtime as a guarded Assistant capability."""

    def __init__(
        self,
        *,
        live_manager,
        db_path: str | Path | None = None,
        template_ids: tuple[str, ...] = _DEFAULT_TEMPLATE_IDS,
        runtime_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.live_manager = live_manager
        self.db_path = Path(
            db_path
            or os.environ.get("AEDT_AGENT_WORKFLOW_DB", "")
            or Path(".aedt-agent") / "assistant-workflows" / "missions.db"
        ).resolve()
        self.template_ids = tuple(template_ids)
        self._runtime_factory = runtime_factory or _default_runtime_factory
        self._runtime = None
        self._previews: dict[tuple[str, str], dict[str, Any]] = {}
        self._live_graph_bindings: dict[str, dict[str, Any]] = {}
        self._graph_registry = _graph_registry(live_manager, self._resolve_live_graph_binding)

    def list_workflows(self) -> dict[str, Any]:
        return {
            "version": "1",
            "execution_model": "guarded_graph_step",
            "runtime_profile": os.environ.get("AEDT_AGENT_WORKFLOW_PROFILE", "safe-recorded"),
            "attached_live_session_reuse": "per_workflow",
            "workflows": [self._descriptor(self._template(item)) for item in self.template_ids],
        }

    def inspect_workflow(self, workflow_id: str) -> dict[str, Any]:
        template = self._template(workflow_id)
        return {
            **self._descriptor(template),
            "graph": template.to_json_dict(),
        }

    def preview_start(
        self,
        live_session_id: str,
        *,
        workflow_id: str,
        goal: str,
        initial_payload: dict[str, Any],
        max_steps: int = 32,
    ) -> dict[str, Any]:
        template = self._template(workflow_id)
        payload = _validated_payload(initial_payload)
        if "_assistant_live" in payload:
            raise ValueError("initial_payload contains a reserved server-owned field: _assistant_live")
        if not str(goal).strip():
            raise ValueError("workflow goal is required")
        if not 1 <= int(max_steps) <= 256:
            raise ValueError("max_steps must be between 1 and 256")
        binding = self.live_manager.workflow_binding(live_session_id)
        missing = sorted(set(self._required_initial_fields(template)).difference(payload))
        preview = self._new_preview(
            live_session_id,
            "workflow.graph.start",
            {
                "workflow_id": template.template_id,
                "workflow_version": template.version,
                "goal": str(goal).strip(),
                "initial_payload": payload,
                "max_steps": int(max_steps),
                "target_binding": binding,
                "missing_recommended_inputs": missing,
            },
        )
        return self.live_manager.register_guarded_preview(
            live_session_id,
            action="workflow.graph.start",
            result=preview,
        )

    def apply_start(
        self,
        live_session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        preview = self._preview(live_session_id, preview_id, "workflow.graph.start")
        data = preview["data"]
        if self.live_manager.workflow_binding(live_session_id) != data["target_binding"]:
            raise LiveAedtError("target_mismatch", "active AEDT target changed after workflow preview")
        if self._template(data["workflow_id"]).version != data["workflow_version"]:
            raise LiveAedtError("preview_stale", "workflow template changed after the preview was created")
        self.live_manager.authorize_guarded_preview(
            live_session_id,
            action="workflow.graph.start",
            preview_id=preview_id,
            approval_token=approval_token,
        )
        runtime = self._get_runtime()
        mission = runtime.create_mission(
            data["goal"],
            acceptance_criteria=[
                {
                    "kind": "assistant_workflow_binding",
                    "workflow_id": data["workflow_id"],
                    "target_binding": data["target_binding"],
                }
            ],
            constraints=[],
        )
        graph_run = create_graph_run(
            runtime,
            mission.mission_id,
            self._template(data["workflow_id"]),
            initial_payload=self._bound_initial_payload(live_session_id, data),
            max_steps=data["max_steps"],
        )
        if data["workflow_id"] in _LIVE_SESSION_WORKFLOWS:
            self._live_graph_bindings[graph_run.graph_run_id] = {
                "live_session_id": live_session_id,
                "target_binding": dict(data["target_binding"]),
            }
        self._previews.pop((live_session_id, preview_id), None)
        return {
            "started": True,
            "execution_started": False,
            "mission_id": mission.mission_id,
            "graph_run_id": graph_run.graph_run_id,
            "status": graph_run.status.value,
            "next_tool": "preview_ansys_workflow_advance",
        }

    def status(self, graph_run_id: str) -> dict[str, Any]:
        return graph_status(self._get_runtime(), str(graph_run_id))

    def preview_advance(self, live_session_id: str, *, graph_run_id: str) -> dict[str, Any]:
        binding = self.live_manager.workflow_binding(live_session_id)
        report = self.status(graph_run_id)
        self._validate_binding(report["mission_id"], binding)
        if report["status"] in {"succeeded", "failed", "canceled"}:
            raise ValueError(f"workflow graph is already terminal: {report['status']}")
        preview = self._new_preview(
            live_session_id,
            "workflow.graph.advance",
            {
                "graph_run_id": str(graph_run_id),
                "status": report["status"],
                "step_count": report["graph_run"]["step_count"],
                "state_digest": _graph_state_digest(report),
                "target_binding": binding,
                "operation_approval_required": _operation_approval_requirement(report),
            },
        )
        return self.live_manager.register_guarded_preview(
            live_session_id,
            action="workflow.graph.advance",
            result=preview,
        )

    def apply_advance(
        self,
        live_session_id: str,
        *,
        preview_id: str,
        approval_token: str,
        max_workers: int = 1,
        operation_approval_token: str = "",
    ) -> dict[str, Any]:
        if not 1 <= int(max_workers) <= 4:
            raise ValueError("max_workers must be between 1 and 4")
        preview = self._preview(live_session_id, preview_id, "workflow.graph.advance")
        operation_requirement = preview["data"].get("operation_approval_required")
        if operation_requirement and not str(operation_approval_token or ""):
            raise LiveAedtError(
                "operation_approval_required",
                "approve the nested live operation preview and pass its token in operation_approval_token",
            )
        if not operation_requirement and operation_approval_token:
            raise LiveAedtError(
                "unexpected_operation_approval_token",
                "this graph step has no pending live operation approval",
            )
        current = self.status(preview["data"]["graph_run_id"])
        if _graph_state_digest(current) != preview["data"]["state_digest"]:
            raise LiveAedtError("preview_stale", "workflow graph advanced after the preview was created")
        binding = self.live_manager.workflow_binding(live_session_id)
        if binding != preview["data"]["target_binding"]:
            raise LiveAedtError("target_mismatch", "active AEDT target changed after workflow preview")
        self._validate_binding(current["mission_id"], binding)
        self._live_graph_bindings[preview["data"]["graph_run_id"]] = {
            "live_session_id": live_session_id,
            "target_binding": binding,
            "operation_approval_token": str(operation_approval_token or ""),
        }
        try:
            self.live_manager.authorize_guarded_preview(
                live_session_id,
                action="workflow.graph.advance",
                preview_id=preview_id,
                approval_token=approval_token,
            )
            report = advance_graph(
                self._get_runtime(),
                preview["data"]["graph_run_id"],
                worker_id="assistant-workflow",
                max_workers=int(max_workers),
                registry=self._graph_registry,
            )
        finally:
            if preview["data"]["graph_run_id"] in self._live_graph_bindings:
                self._live_graph_bindings[preview["data"]["graph_run_id"]].pop(
                    "operation_approval_token",
                    None,
                )
        self._previews.pop((live_session_id, preview_id), None)
        return report

    def _get_runtime(self):
        if self._runtime is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._runtime = self._runtime_factory(self.db_path)
        return self._runtime

    def _template(self, workflow_id: str) -> GraphTemplate:
        normalized = str(workflow_id).strip()
        if normalized not in self.template_ids:
            raise KeyError(f"unknown or disabled Assistant workflow: {normalized}")
        return load_graph_template(normalized)

    def _descriptor(self, template: GraphTemplate) -> dict[str, Any]:
        worker_capabilities = sorted(
            {node.capability for node in template.nodes if node.kind == "worker" and node.capability}
        )
        expensive = any("solve" in item for item in worker_capabilities)
        mutating = any(token in item for item in worker_capabilities for token in ("build", "edit", "action"))
        return {
            "workflow_id": template.template_id,
            "version": template.version,
            "description": template.description,
            "node_count": len(template.nodes),
            "max_rounds": template.max_rounds,
            "worker_capabilities": worker_capabilities,
            "recommended_initial_fields": self._required_initial_fields(template),
            "risk": _LIVE_WORKFLOW_RISKS.get(
                template.template_id,
                "expensive" if expensive else "reversible_edit" if mutating else "read_only",
            ),
            "approval": "external_host_token_per_start_and_step",
            "execution_backend": (
                "live_aedt_graph_handlers"
                if template.template_id in _LIVE_SESSION_WORKFLOWS
                else "mission_process_harness"
            ),
            "attached_live_session_reuse": template.template_id in _LIVE_SESSION_WORKFLOWS,
        }

    @staticmethod
    def _bound_initial_payload(_live_session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return dict(data["initial_payload"])

    def _resolve_live_graph_binding(self, graph_run_id: str) -> dict[str, Any]:
        try:
            return dict(self._live_graph_bindings[graph_run_id])
        except KeyError as exc:
            raise LiveAedtError(
                "live_workflow_binding_missing",
                "preview and approve this graph step from its bound live AEDT session",
            ) from exc

    @staticmethod
    def _required_initial_fields(template: GraphTemplate) -> list[str]:
        target_nodes = {edge.to_node for edge in template.edges}
        root_nodes = [node for node in template.nodes if node.node_id not in target_nodes]
        required: set[str] = set()
        for node in root_nodes:
            schema_id = node.input_schema or node.output_schema
            if schema_id and schema_id in template.handoffs:
                required.update(template.handoffs[schema_id].required_fields)
        return sorted(required)

    def _new_preview(self, session_id: str, action: str, data: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        preview_id = f"workflow-preview-{digest[:24]}"
        record = {"action": action, "data": data, "snapshot_digest": digest}
        self._previews[(session_id, preview_id)] = record
        return {"preview_id": preview_id, "snapshot_digest": digest, **data}

    def _preview(self, session_id: str, preview_id: str, action: str) -> dict[str, Any]:
        record = self._previews.get((session_id, preview_id))
        if record is None or record["action"] != action:
            raise LiveAedtError("approval_required", "workflow apply must reference its session-bound preview")
        return record

    def _validate_binding(self, mission_id: str, binding: dict[str, Any]) -> None:
        mission = self._get_runtime().get_mission(mission_id)
        criteria = mission.acceptance_criteria or []
        expected = next(
            (item.get("target_binding") for item in criteria if item.get("kind") == "assistant_workflow_binding"),
            None,
        )
        if expected != binding:
            raise LiveAedtError("target_mismatch", "workflow is bound to a different AEDT Desktop target")


def _validated_payload(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("initial_payload must be an object")
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise ValueError(f"initial_payload exceeds {_MAX_PAYLOAD_BYTES} bytes")
    return json.loads(encoded.decode("utf-8"))


def _graph_state_digest(report: dict[str, Any]) -> str:
    state = {
        "status": report["status"],
        "step_count": report["graph_run"]["step_count"],
        "current_node_id": report["graph_run"].get("current_node_id"),
        "node_runs": [
            {
                "node_run_id": item["node_run_id"],
                "status": item["status"],
                "edge_decision": item.get("edge_decision"),
            }
            for item in report.get("node_runs", [])
        ],
    }
    encoded = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation_approval_requirement(report: dict[str, Any]) -> dict[str, Any] | None:
    preview_nodes = {
        "layout_live_parameterize_width": {"preview_parameterization"},
        "layout_live_results_export": {"preview_export"},
        "layout_live_solve_export": {"preview_analysis", "preview_export"},
        "layout_live_solve_start": {"preview_analysis"},
    }
    expected_nodes = preview_nodes.get(str(report.get("template_id") or ""))
    if expected_nodes is None:
        return None
    completed = [
        item
        for item in report.get("node_runs", [])
        if item.get("status") == "succeeded"
    ]
    if not completed or completed[-1].get("node_id") not in expected_nodes:
        return None
    output = dict(completed[-1].get("output_payload") or {})
    preview_id = str(output.get("operation_preview_id") or "")
    if not preview_id:
        return None
    return {
        "preview_id": preview_id,
        "wait_tool": "wait_for_live_approval",
        "apply_argument": "operation_approval_token",
    }


def _default_runtime_factory(db_path: Path):
    # Keep the CLI and Assistant on the same proven worker registry until it is
    # promoted into a public runtime factory module.
    from aedt_agent.agent.cli import _load_execution_profile, _runtime_with_workers

    profile_value = os.environ.get("AEDT_AGENT_WORKFLOW_PROFILE", "").strip()
    profile = _load_execution_profile(profile_value) if profile_value else None
    return _runtime_with_workers(db_path, profile)


def _graph_registry(live_manager, binding_resolver):
    from aedt_agent.agent.graph_executors import GraphNodeExecutorRegistry
    from aedt_agent.agent.optimization_handlers import register_optimization_handlers
    from aedt_agent.interactive.live_workflow_handlers import register_live_workflow_handlers

    registry = GraphNodeExecutorRegistry()
    register_optimization_handlers(registry)
    register_live_workflow_handlers(registry, live_manager, binding_resolver)
    return registry
