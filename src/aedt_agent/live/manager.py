from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
import secrets
from typing import Any

from aedt_agent.capability_learning import (
    CapabilityPromoter,
    CapabilityTraceStore,
    PromotionError,
    TraceStateError,
)
from aedt_agent.live.approval import HmacApprovalAuthority
from aedt_agent.live.broker import AedtBrokerRegistry, LiveAedtError
from aedt_agent.live.discovery import list_aedt_sessions
from aedt_agent.live.launcher import AedtLaunchError, AedtLauncher
from aedt_agent.live.target import AedtTarget
from aedt_agent.live.versioning import (
    DEFAULT_AEDT_VERSION,
    aedt_versions_match,
    normalize_aedt_version,
)


@dataclass(frozen=True)
class LiveSession:
    session_id: str
    target: AedtTarget
    version: str
    owned_by_assistant: bool = False
    pid: int | None = None
    port: int | None = None


class LiveAedtSessionManager:
    def __init__(
        self,
        *,
        registry: AedtBrokerRegistry | None = None,
        launcher: AedtLauncher | None = None,
        approval_verifier=None,
        required_port: int | None = None,
        required_project: str | None = None,
        required_design: str | None = None,
        required_version: str | None = None,
        strict_desktop: bool = False,
        exploration_validator=None,
        trace_store: CapabilityTraceStore | None = None,
        capability_promoter: CapabilityPromoter | None = None,
    ) -> None:
        self.registry = registry or AedtBrokerRegistry()
        self.launcher = launcher or AedtLauncher()
        if approval_verifier is None:
            from aedt_agent.desktop.approval_client import DesktopApprovalClient

            approval_verifier = DesktopApprovalClient.from_environment() or HmacApprovalAuthority.from_environment()
        self.approval_verifier = approval_verifier
        self._sessions: dict[str, LiveSession] = {}
        self._approval_contexts: dict[tuple[str, str], tuple[str, str, str]] = {}
        self._exploration_candidates: dict[str, dict[str, Any]] = {}
        self._exploration_previews: dict[tuple[str, str], dict[str, Any]] = {}
        self._tainted_exploration_sessions: set[str] = set()
        self._owned_pids: set[int] = set()
        self._owned_ports: set[int] = set()
        self.required_port = required_port
        self.required_project = str(required_project or "").strip() or None
        self.required_design = str(required_design or "").strip() or None
        self.strict_desktop = bool(strict_desktop)
        if required_version is None or not str(required_version).strip():
            self.required_version = None
        else:
            self.required_version = normalize_aedt_version(str(required_version))
        if self.strict_desktop and self.required_version is None:
            raise ValueError("required_version is required for a strict AEDT Desktop session")
        if exploration_validator is None:
            from aedt_agent.exploration.validator import OperationValidator
            from aedt_agent.knowledge.evidence import ApiMemoryEvidenceVerifier

            exploration_validator = OperationValidator(
                evidence_verifier=ApiMemoryEvidenceVerifier(),
            )
        self.exploration_validator = exploration_validator
        self.trace_store = trace_store or CapabilityTraceStore()
        self.capability_promoter = capability_promoter or CapabilityPromoter(self.trace_store)

    def list_sessions(self) -> dict[str, Any]:
        sessions = list_aedt_sessions()
        if self.required_port is not None:
            sessions = [
                session
                for session in sessions
                if self.required_port == session.get("grpc_port")
                or self.required_port in set(session.get("ports") or [])
            ]
        for session in sessions:
            ports = set(session.get("ports") or [])
            grpc_port = session.get("grpc_port")
            session["owned_by_assistant"] = (
                session.get("pid") in self._owned_pids
                or grpc_port in self._owned_ports
                or bool(ports.intersection(self._owned_ports))
            )
        return {"sessions": sessions, "selection_required": True}

    def launch(
        self,
        *,
        version: str = DEFAULT_AEDT_VERSION,
        port: int = 0,
        install_dir: str | None = None,
        non_graphical: bool = False,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        version = self._validate_requested_version(version)
        if self.required_port is not None:
            raise LiveAedtError("target_forbidden", "this MCP server is bound to an existing AEDT session")
        try:
            launched = self.launcher.launch(
                probe=lambda target, probe_timeout: self.registry.execute(
                    target,
                    "ping",
                    {},
                    version=version,
                    timeout=probe_timeout,
                ),
                version=version,
                port=port,
                install_dir=install_dir,
                non_graphical=non_graphical,
                timeout=timeout,
            )
        except AedtLaunchError as exc:
            raise LiveAedtError("launch_failed", str(exc)) from exc
        self._owned_pids.add(int(launched["pid"]))
        self._owned_ports.add(int(launched["port"]))
        attached = self.attach(port=int(launched["port"]), version=version)
        return {"launched": True, **launched, **attached}

    def attach(
        self,
        *,
        pid: int | None = None,
        port: int | None = None,
        version: str = DEFAULT_AEDT_VERSION,
    ) -> dict[str, Any]:
        version = self._validate_requested_version(version)
        if self.required_port is not None and port != self.required_port:
            raise LiveAedtError(
                "target_forbidden",
                f"this MCP server is restricted to AEDT gRPC port {self.required_port}",
            )
        expected_pid = pid if pid is not None and port is not None else None
        target = AedtTarget.from_values(pid=None if port is not None else pid, port=port)
        reused_broker = bool(
            getattr(self.registry, "has_target", lambda _target, **_kwargs: False)(
                target,
                version=version,
            )
        )
        probe = self.registry.execute(target, "ping", {}, version=version)
        try:
            self._validate_probe_version(probe, version)
        except LiveAedtError:
            if not reused_broker:
                try:
                    self.registry.release(target, version=version)
                except Exception:
                    pass
            raise
        if expected_pid is not None and probe.get("pid") != expected_pid:
            if not reused_broker:
                try:
                    self.registry.release(target, version=version)
                except Exception:
                    pass
            raise LiveAedtError(
                "target_mismatch",
                f"AEDT port {port} belongs to PID {probe.get('pid')}, not requested PID {expected_pid}",
            )
        session_id = secrets.token_urlsafe(18)
        reported_pid = probe.get("pid")
        reported_port = probe.get("port")
        owned = (
            target.value in (self._owned_pids if target.kind == "pid" else self._owned_ports)
            or reported_pid in self._owned_pids
            or reported_port in self._owned_ports
        )
        self._sessions[session_id] = LiveSession(
            session_id,
            target,
            version,
            owned,
            pid=_positive_int(reported_pid),
            port=_positive_int(reported_port, maximum=65535),
        )
        return {
            "live_session_id": session_id,
            "target": target.to_dict(),
            "probe": probe,
            "reused_broker": reused_broker,
            "owned_by_assistant": owned,
            "release_required": True,
            "release_tool": "release_live_aedt_session",
        }

    def release(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        result = self.registry.release(session.target, version=session.version)
        for key, value in list(self._sessions.items()):
            if _same_live_broker(value, session):
                del self._sessions[key]
                self._discard_session_approvals(key)
                self._discard_session_explorations(key)
        return {
            "live_session_id": session_id,
            **result,
            "owned_by_assistant": session.owned_by_assistant,
            "aedt_closed": False,
            "projects_closed": False,
        }

    def project_info(self, session_id: str) -> dict[str, Any]:
        return self._execute(session_id, "project_info", {})

    def workflow_binding(self, session_id: str) -> dict[str, Any]:
        """Return the stable target identity used to bind guarded graph workflows."""
        session = self._session(session_id)
        project = self.project_info(session_id)
        return {
            "version": session.version,
            "pid": session.pid,
            "port": session.port,
            "active_project": project.get("active_project"),
            "active_design": project.get("active_design"),
        }

    def register_guarded_preview(
        self,
        session_id: str,
        *,
        action: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Register a non-backend preview with the same native approval host."""
        self._session(session_id)
        return self._register_approval(session_id, action, result)

    def authorize_guarded_preview(
        self,
        session_id: str,
        *,
        action: str,
        preview_id: str,
        approval_token: str,
    ) -> None:
        """Verify and consume a one-use native approval for an external harness action."""
        self._require_approval(session_id, action, preview_id, approval_token)
        self._approval_contexts.pop((session_id, preview_id), None)

    def preview_project_save(self, session_id: str, *, project_name: str) -> dict[str, Any]:
        result = self._execute(session_id, "project_save_preview", {"project_name": project_name})
        return self._register_approval(session_id, "project.save", result)

    def apply_project_save(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        self._require_approval(session_id, "project.save", preview_id, approval_token)
        result = self._execute(session_id, "project_save_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def create_hfss_design(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        solution_type: str = "DrivenModal",
    ) -> dict[str, Any]:
        if self.strict_desktop:
            raise LiveAedtError(
                "preview_required",
                "direct design creation is disabled for an AEDT Desktop-bound MCP session",
            )
        return self._execute(
            session_id,
            "hfss_design_create",
            {"project_name": project_name, "design_name": design_name, "solution_type": solution_type},
        )

    def hfss_design_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "hfss_design_inventory",
            {"project_name": project_name, "design_name": design_name},
        )

    def setup_inventory(
        self,
        session_id: str,
        *,
        product: str,
        project_name: str,
        design_name: str,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "setup_inventory",
            {"product": product, "project_name": project_name, "design_name": design_name},
        )

    def solution_inventory(
        self,
        session_id: str,
        *,
        product: str,
        project_name: str,
        design_name: str,
        setup_name: str = "",
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "solution_inventory",
            {
                "product": product,
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
            },
        )

    def hfss_geometry_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        object_names: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "hfss_geometry_inventory",
            {"project_name": project_name, "design_name": design_name, "object_names": object_names or []},
        )

    def hfss_material_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        max_items: int = 100,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "hfss_material_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "max_items": max_items,
            },
        )

    def preview_hfss_material_assign(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        object_names: list[str],
        material_name: str,
        max_objects: int = 16,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_material_assign_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "object_names": object_names,
                "material_name": material_name,
                "max_objects": max_objects,
            },
        )
        return self._register_approval(session_id, "hfss.material.assign", result)

    def apply_hfss_material_assign(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "hfss.material.assign",
            preview_id,
            approval_token,
        )
        result = self._execute(
            session_id,
            "hfss_material_assign_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def hfss_mesh_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        max_items: int = 100,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "hfss_mesh_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "max_items": max_items,
            },
        )

    def preview_hfss_length_mesh_create(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        mesh_name: str,
        object_names: list[str],
        inside_selection: bool = True,
        maximum_length: str | None = "1mm",
        maximum_elements: int | None = 1000,
        max_objects: int = 16,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_length_mesh_create_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "mesh_name": mesh_name,
                "object_names": object_names,
                "inside_selection": inside_selection,
                "maximum_length": maximum_length,
                "maximum_elements": maximum_elements,
                "max_objects": max_objects,
            },
        )
        return self._register_approval(session_id, "hfss.mesh.length.create", result)

    def apply_hfss_length_mesh_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "hfss.mesh.length.create",
            preview_id,
            approval_token,
        )
        result = self._execute(
            session_id,
            "hfss_length_mesh_create_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def hfss_far_field_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        max_items: int = 100,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "hfss_far_field_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "max_items": max_items,
            },
        )

    def preview_hfss_infinite_sphere_create(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        sphere_name: str,
        definition: str = "Theta-Phi",
        angle1_start: float = 0.0,
        angle1_stop: float = 180.0,
        angle1_step: float = 10.0,
        angle2_start: float = 0.0,
        angle2_stop: float = 180.0,
        angle2_step: float = 10.0,
        units: str = "deg",
        polarization: str = "Linear",
        polarization_angle: float = 45.0,
        max_samples: int = 200_000,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_infinite_sphere_create_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "sphere_name": sphere_name,
                "definition": definition,
                "angle1_start": angle1_start,
                "angle1_stop": angle1_stop,
                "angle1_step": angle1_step,
                "angle2_start": angle2_start,
                "angle2_stop": angle2_stop,
                "angle2_step": angle2_step,
                "units": units,
                "polarization": polarization,
                "polarization_angle": polarization_angle,
                "max_samples": max_samples,
            },
        )
        return self._register_approval(session_id, "hfss.far_field.infinite_sphere.create", result)

    def apply_hfss_infinite_sphere_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "hfss.far_field.infinite_sphere.create",
            preview_id,
            approval_token,
        )
        result = self._execute(
            session_id,
            "hfss_infinite_sphere_create_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_geometry_create(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        primitives: list[dict[str, Any]],
        max_new_objects: int = 16,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_geometry_create_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "primitives": primitives,
                "max_new_objects": max_new_objects,
            },
        )
        return self._register_approval(session_id, "hfss.geometry.create", result)

    def apply_hfss_geometry_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.geometry.create", preview_id, approval_token)
        result = self._execute(
            session_id,
            "hfss_geometry_create_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_geometry_boundary_create(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        primitives: list[dict[str, Any]],
        boundaries: list[dict[str, Any]],
        max_new_objects: int = 16,
        max_new_boundaries: int = 16,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_geometry_boundary_create_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "primitives": primitives,
                "boundaries": boundaries,
                "max_new_objects": max_new_objects,
                "max_new_boundaries": max_new_boundaries,
            },
        )
        return self._register_approval(session_id, "hfss.geometry_boundary.create", result)

    def apply_hfss_geometry_boundary_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "hfss.geometry_boundary.create",
            preview_id,
            approval_token,
        )
        result = self._execute(
            session_id,
            "hfss_geometry_boundary_create_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_setup(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        setup_name: str,
        setup_type: str = "HFSSDriven",
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_setup_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
                "setup_type": setup_type,
                "properties": properties or {},
            },
        )
        return self._register_approval(session_id, "hfss.setup.create", result)

    def apply_hfss_setup(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.setup.create", preview_id, approval_token)
        result = self._execute(session_id, "hfss_setup_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_setup_update(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        setup_name: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_setup_update_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
                "properties": properties,
            },
        )
        return self._register_approval(session_id, "hfss.setup.update", result)

    def apply_hfss_setup_update(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.setup.update", preview_id, approval_token)
        result = self._execute(session_id, "hfss_setup_update_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_frequency_sweep_create(
        self,
        session_id: str,
        *,
        product: str,
        project_name: str,
        design_name: str,
        setup_name: str,
        sweep_name: str,
        range_type: str,
        sweep_type: str,
        unit: str,
        start_frequency: float,
        stop_frequency: float,
        count: int | None = None,
        step_size: float | None = None,
        save_fields: bool = True,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "frequency_sweep_create_preview",
            {
                "product": product,
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
                "sweep_name": sweep_name,
                "range_type": range_type,
                "sweep_type": sweep_type,
                "unit": unit,
                "start_frequency": start_frequency,
                "stop_frequency": stop_frequency,
                "count": count,
                "step_size": step_size,
                "save_fields": save_fields,
            },
        )
        return self._register_approval(session_id, "aedt.frequency_sweep.create", result)

    def apply_frequency_sweep_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(session_id, "aedt.frequency_sweep.create", preview_id, approval_token)
        result = self._execute(session_id, "frequency_sweep_create_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_setup_sweep_create(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        setup: dict[str, Any],
        sweep: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_setup_sweep_create_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "setup": setup,
                "sweep": sweep,
            },
        )
        return self._register_approval(session_id, "hfss.setup_sweep.create", result)

    def apply_hfss_setup_sweep_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "hfss.setup_sweep.create",
            preview_id,
            approval_token,
        )
        result = self._execute(
            session_id,
            "hfss_setup_sweep_create_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_report(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        report_name: str,
        setup_sweep_name: str,
        expressions: list[str],
        domain: str = "Sweep",
        plot_type: str = "Rectangular Plot",
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_report_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "report_name": report_name,
                "setup_sweep_name": setup_sweep_name,
                "expressions": expressions,
                "domain": domain,
                "plot_type": plot_type,
            },
        )
        return self._register_approval(session_id, "hfss.report.create", result)

    def apply_hfss_report(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.report.create", preview_id, approval_token)
        result = self._execute(session_id, "hfss_report_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_boundary(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        boundary_kind: str,
        boundary_name: str,
        assignment_face_ids: list[int],
        references: list[str | int] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_boundary_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "boundary_kind": boundary_kind,
                "boundary_name": boundary_name,
                "assignment_face_ids": assignment_face_ids,
                "references": references or [],
                "options": options or {},
            },
        )
        return self._register_approval(session_id, "hfss.boundary.create", result)

    def apply_hfss_boundary(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.boundary.create", preview_id, approval_token)
        result = self._execute(session_id, "hfss_boundary_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def start_hfss_analysis(
        self, session_id: str, *, project_name: str, design_name: str, setup_name: str, blocking: bool = False,
        product: str = "hfss",
    ) -> dict[str, Any]:
        if self.strict_desktop:
            raise LiveAedtError(
                "preview_required",
                "direct analysis start is disabled; use preview/apply in an AEDT Desktop-bound session",
            )
        return self._execute(
            session_id,
            "hfss_analysis_start",
            {"project_name": project_name, "design_name": design_name, "setup_name": setup_name, "blocking": blocking, "product": product},
        )

    def preview_hfss_analysis_start(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        setup_name: str,
        cores: int | None = None,
        tasks: int | None = None,
        gpus: int | None = None,
        use_auto_settings: bool = True,
        product: str = "hfss",
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_analysis_start_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
                "cores": cores,
                "tasks": tasks,
                "gpus": gpus,
                "use_auto_settings": use_auto_settings,
                "product": product,
            },
        )
        return self._register_approval(session_id, "hfss.analysis.start", result)

    def apply_hfss_analysis_start(
        self, session_id: str, *, preview_id: str, approval_token: str
    ) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.analysis.start", preview_id, approval_token)
        result = self._execute(session_id, "hfss_analysis_start_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def hfss_analysis_status(
        self, session_id: str, *, project_name: str, design_name: str, setup_name: str = "",
        product: str = "hfss",
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "hfss_analysis_status",
            {"project_name": project_name, "design_name": design_name, "setup_name": setup_name, "product": product},
        )

    def preview_hfss_analysis_cancel(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        setup_name: str = "",
        clean_stop: bool = True,
        product: str = "hfss",
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_analysis_cancel_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
                "clean_stop": clean_stop,
                "product": product,
            },
        )
        return self._register_approval(session_id, "hfss.analysis.cancel", result)

    def apply_hfss_analysis_cancel(
        self, session_id: str, *, preview_id: str, approval_token: str
    ) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.analysis.cancel", preview_id, approval_token)
        result = self._execute(session_id, "hfss_analysis_cancel_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_hfss_export(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        export_kind: str,
        setup_name: str = "",
        sweep_name: str = "",
        report_name: str = "",
        artifact_name: str = "",
        product: str = "hfss",
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "hfss_export_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "export_kind": export_kind,
                "setup_name": setup_name,
                "sweep_name": sweep_name,
                "report_name": report_name,
                "artifact_name": artifact_name,
                "product": product,
            },
        )
        return self._register_approval(session_id, "hfss.results.export", result)

    def apply_hfss_export(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        self._require_approval(session_id, "hfss.results.export", preview_id, approval_token)
        result = self._execute(session_id, "hfss_export_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def list_layout_paths(
        self, session_id: str, *, project_name: str, design_name: str, selector: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_paths_list",
            {"project_name": project_name, "design_name": design_name, "selector": selector or {}},
        )

    def layout_routing_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        selector: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_routing_inventory",
            {"project_name": project_name, "design_name": design_name, "selector": selector or {}},
        )

    def layout_technology_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        max_items: int = 500,
        include_padstack_layers: bool = False,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_technology_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "max_items": max_items,
                "include_padstack_layers": include_padstack_layers,
            },
        )

    def layout_connectivity_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        selector: dict[str, Any] | None = None,
        max_items: int = 500,
        include_geometry_names: bool = False,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_connectivity_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "selector": selector or {},
                "max_items": max_items,
                "include_geometry_names": include_geometry_names,
            },
        )

    def layout_port_candidate_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        signal_nets: list[str],
        reference_nets: list[str] | None = None,
        max_candidates: int = 100,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_port_candidate_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "signal_nets": signal_nets,
                "reference_nets": reference_nets or [],
                "max_candidates": max_candidates,
            },
        )

    def preview_layout_component_ports_create(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        component_name: str,
        signal_nets: list[str],
        allow_multiple_pins_per_net: bool = False,
        max_new_ports: int = 16,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "layout_component_ports_create_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "component_name": component_name,
                "signal_nets": signal_nets,
                "allow_multiple_pins_per_net": allow_multiple_pins_per_net,
                "max_new_ports": max_new_ports,
            },
        )
        return self._register_approval(session_id, "layout.component_ports.create", result)

    def apply_layout_component_ports_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "layout.component_ports.create",
            preview_id,
            approval_token,
        )
        result = self._execute(
            session_id,
            "layout_component_ports_create_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def layout_edge_port_candidate_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        signal_nets: list[str],
        local_cut_region: dict[str, Any],
        side: str,
        layer: str,
        max_candidates: int = 100,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_edge_port_candidate_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "signal_nets": signal_nets,
                "local_cut_region": local_cut_region,
                "side": side,
                "layer": layer,
                "max_candidates": max_candidates,
            },
        )

    def preview_layout_edge_ports_create(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        edge_targets: list[dict[str, Any]],
        max_new_ports: int = 16,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "layout_edge_ports_create_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "edge_targets": edge_targets,
                "max_new_ports": max_new_ports,
            },
        )
        return self._register_approval(session_id, "layout.edge_ports.create", result)

    def apply_layout_edge_ports_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "layout.edge_ports.create",
            preview_id,
            approval_token,
        )
        result = self._execute(
            session_id,
            "layout_edge_ports_create_apply",
            {"preview_id": preview_id},
        )
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def layout_object_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_object_inventory",
            {"project_name": project_name, "design_name": design_name},
        )

    def layout_object_property_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        object_kind: str,
        names: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "layout_object_property_inventory",
            {
                "project_name": project_name,
                "design_name": design_name,
                "object_kind": object_kind,
                "names": names or [],
            },
        )

    def preview_layout_object_property_update(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        object_kind: str,
        names: list[str],
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "layout_object_property_update_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "object_kind": object_kind,
                "names": names,
                "properties": properties,
            },
        )
        return self._register_approval(session_id, "layout.object.property.update", result)

    def apply_layout_object_property_update(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(session_id, "layout.object.property.update", preview_id, approval_token)
        result = self._execute(session_id, "layout_object_property_update_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def variable_inventory(
        self,
        session_id: str,
        *,
        product: str,
        project_name: str,
        design_name: str,
    ) -> dict[str, Any]:
        return self._execute(
            session_id,
            "variable_inventory",
            {"product": product, "project_name": project_name, "design_name": design_name},
        )

    def preview_variable_upsert(
        self,
        session_id: str,
        *,
        product: str,
        project_name: str,
        design_name: str,
        variable_name: str,
        expression: str,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "variable_upsert_preview",
            {
                "product": product,
                "project_name": project_name,
                "design_name": design_name,
                "variable_name": variable_name,
                "expression": expression,
            },
        )
        return self._register_approval(session_id, "aedt.variable.upsert", result)

    def apply_variable_upsert(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        self._require_approval(session_id, "aedt.variable.upsert", preview_id, approval_token)
        result = self._execute(session_id, "variable_upsert_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def preview_layout_width(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        selector: dict[str, Any],
        variable_name: str,
        variable_value: str,
    ) -> dict[str, Any]:
        result = self._execute(
            session_id,
            "layout_width_preview",
            {
                "project_name": project_name,
                "design_name": design_name,
                "selector": selector,
                "variable_name": variable_name,
                "variable_value": variable_value,
            },
        )
        return self._register_approval(session_id, "layout.path_width.parameterize", result)

    def apply_layout_width(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        self._require_approval(
            session_id,
            "layout.path_width.parameterize",
            preview_id,
            approval_token,
        )
        result = self._execute(session_id, "layout_width_apply", {"preview_id": preview_id})
        self._approval_contexts.pop((session_id, preview_id), None)
        return result

    def wait_for_approval(
        self,
        session_id: str,
        *,
        preview_id: str,
        timeout_seconds: float = 0,
    ) -> dict[str, Any]:
        self._session(session_id)
        context = self._approval_contexts.get((session_id, preview_id))
        if context is None:
            raise LiveAedtError("approval_required", "approval must reference a preview from this live session")
        poll = getattr(self.approval_verifier, "poll", None)
        if not callable(poll):
            return {
                "live_session_id": session_id,
                "preview_id": preview_id,
                "status": "external_manual",
                "approval_token": None,
            }
        result = poll(context[2], timeout_seconds=timeout_seconds)
        exploration = self._exploration_previews.get((session_id, preview_id))
        if exploration is not None:
            candidate = self._exploration_candidate(exploration["candidate_id"])
            self._record_exploration_approval_decision(
                session_id,
                preview_id,
                candidate,
                str(result.get("status") or ""),
            )
        return {"live_session_id": session_id, "preview_id": preview_id, **result}

    def propose_exploratory_operation(self, plan: dict[str, Any]) -> dict[str, Any]:
        from aedt_agent.exploration.contracts import OperationPlan

        normalized = OperationPlan.from_dict(plan).to_dict()
        encoded = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        candidate_id = "explore-candidate-" + hashlib.sha256(encoded).hexdigest()[:24]
        if candidate_id not in self._exploration_candidates and len(self._exploration_candidates) >= 64:
            raise LiveAedtError("candidate_limit", "release the MCP session before proposing more candidates")
        candidate = self._exploration_candidates.get(candidate_id)
        if candidate is None:
            try:
                trace = self.trace_store.create(
                    candidate_id=candidate_id,
                    intent=normalized["intent"],
                    plan=normalized,
                    environment={
                        "package_versions": dict(
                            getattr(self.exploration_validator, "package_versions", {})
                        )
                    },
                )
            except Exception as exc:
                raise LiveAedtError("trace_unavailable", f"could not create capability trace: {exc}") from exc
            candidate = {
                "status": "proposed",
                "plan": normalized,
                "trace_id": trace["trace_id"],
            }
            self._exploration_candidates[candidate_id] = candidate
        return {
            "candidate_id": candidate_id,
            "trace_id": candidate["trace_id"],
            "status": candidate["status"],
            "plan": normalized,
        }

    def validate_exploratory_operation(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._exploration_candidate(candidate_id)
        if candidate.get("status") == "validated":
            return {"candidate_id": candidate_id, "trace_id": candidate["trace_id"], **candidate["validation"]}
        if candidate.get("status") != "proposed":
            raise LiveAedtError("candidate_state_invalid", "only a proposed exploratory candidate can be validated")
        try:
            report = self.exploration_validator.validate(candidate["plan"])
        except Exception as exc:
            self._fail_exploration_trace(candidate, "plan_validation_failed", exc)
            raise
        self._transition_exploration_trace(
            candidate,
            "validated",
            "plan_validated",
            {
                "plan_digest": report["plan_digest"],
                "policy_version": report["policy_version"],
                "policy_digest": report["policy_digest"],
                "risk": report["risk"],
                "mutation_count": report["mutation_count"],
                "rollback_strategy": report["rollback_strategy"],
                "evidence_verification": report["evidence_verification"],
            },
        )
        candidate.update({"status": "validated", "validation": report})
        return {"candidate_id": candidate_id, "trace_id": candidate["trace_id"], **report}

    def preview_exploratory_operation(self, session_id: str, *, candidate_id: str) -> dict[str, Any]:
        self._session(session_id)
        if session_id in self._tainted_exploration_sessions:
            raise LiveAedtError("session_tainted", "release and reconnect before another exploratory operation")
        candidate = self._exploration_candidate(candidate_id)
        if candidate.get("status") != "validated":
            raise LiveAedtError("validation_required", "validate the exploratory candidate before preview")
        plan = candidate["validation"]["plan"]
        target = plan["target"]
        try:
            result = self._execute(
                session_id,
                "exploration_preview",
                {
                    "project_name": target["project_name"],
                    "design_name": target["design_name"],
                    "plan": plan,
                },
                timeout=30,
            )
        except Exception as exc:
            self._fail_exploration_trace(candidate, "preview_failed", exc)
            raise
        preview_id = str(result["preview_id"])
        context = {"candidate_id": candidate_id, "risk": plan["risk"], "trace_id": candidate["trace_id"]}
        self._exploration_previews[(session_id, preview_id)] = context
        try:
            self._transition_exploration_trace(
                candidate,
                "previewed",
                "preview_created",
                {
                    "live_session_id": session_id,
                    "preview": result,
                },
            )
        except Exception:
            self._exploration_previews.pop((session_id, preview_id), None)
            raise
        candidate["status"] = "previewed"
        if plan["risk"] == "reversible_edit":
            try:
                result = self._register_approval(session_id, "exploration.apply", result)
            except Exception as exc:
                self._exploration_previews.pop((session_id, preview_id), None)
                self._fail_exploration_trace(candidate, "approval_registration_failed", exc)
                raise
            self._record_exploration_approval_decision(
                session_id,
                preview_id,
                candidate,
                str(result.get("approval_status") or ""),
            )
        else:
            result.update({"approval_required": False, "release_required": True})
        return {"candidate_id": candidate_id, "trace_id": candidate["trace_id"], **result}

    def apply_exploratory_operation(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str = "",
    ) -> dict[str, Any]:
        self._session(session_id)
        context = self._exploration_previews.get((session_id, preview_id))
        if context is None:
            raise LiveAedtError("preview_required", "unknown exploratory preview for this live session")
        candidate = self._exploration_candidate(context["candidate_id"])
        if context["risk"] == "reversible_edit":
            self._require_approval(session_id, "exploration.apply", preview_id, approval_token)
            self._transition_exploration_trace(
                candidate,
                "approved",
                "host_approved",
                {"live_session_id": session_id, "preview_id": preview_id},
                idempotent=True,
            )
            candidate["status"] = "approved"
        try:
            result = self._execute(session_id, "exploration_apply", {"preview_id": preview_id}, timeout=30)
        except Exception as exc:
            self._fail_exploration_trace(candidate, "operation_failed", exc)
            self._approval_contexts.pop((session_id, preview_id), None)
            self._exploration_previews.pop((session_id, preview_id), None)
            raise
        self._approval_contexts.pop((session_id, preview_id), None)
        self._exploration_previews.pop((session_id, preview_id), None)
        status = str(result.get("status") or "failed")
        if status == "stale_preview":
            self._transition_exploration_trace(
                candidate,
                "failed",
                "preview_became_stale",
                {"result": result},
            )
            candidate["status"] = "failed"
        else:
            self._transition_exploration_trace(
                candidate,
                "applied",
                "operation_applied",
                {"result": result},
            )
            terminal_state = status if status in {"verified", "rolled_back", "rollback_failed"} else "failed"
            terminal_event = {
                "verified": "readback_verified",
                "rolled_back": "rollback_verified",
                "rollback_failed": "rollback_failed",
                "failed": "operation_result_failed",
            }[terminal_state]
            self._transition_exploration_trace(
                candidate,
                terminal_state,
                terminal_event,
                {"result": result},
            )
            candidate["status"] = terminal_state
        if status == "rollback_failed":
            self._tainted_exploration_sessions.add(session_id)
        return {
            "candidate_id": context["candidate_id"],
            "trace_id": candidate["trace_id"],
            **result,
        }

    def capture_capability_trace(self, candidate_id: str) -> dict[str, Any]:
        """Return only the trace owned by a candidate created by this manager."""
        candidate = self._exploration_candidate(candidate_id)
        try:
            trace = self.trace_store.export(candidate["trace_id"])
        except Exception as exc:
            raise LiveAedtError("trace_unavailable", f"could not capture capability trace: {exc}") from exc
        return {
            "candidate_id": candidate_id,
            "trace_id": candidate["trace_id"],
            "server_owned": True,
            "promotion_eligible": trace["sealed"] and trace["state"] == "verified",
            "trace": trace,
        }

    def promote_capability_candidate(
        self,
        trace_id: str,
        *,
        target_kind: str = "auto",
    ) -> dict[str, Any]:
        """Generate a disabled review candidate from a trace owned by this manager."""
        candidate = next(
            (
                value
                for value in self._exploration_candidates.values()
                if value.get("trace_id") == trace_id
            ),
            None,
        )
        if candidate is None:
            raise LiveAedtError(
                "trace_not_owned",
                "promotion requires a trace created by this Runtime MCP session",
            )
        try:
            trace = self.trace_store.export(trace_id)
        except Exception as exc:
            raise LiveAedtError("trace_unavailable", f"could not read capability trace: {exc}") from exc
        if trace.get("sealed") is not True or trace.get("state") != "verified":
            raise LiveAedtError("trace_not_verified", "promotion requires a sealed verified trace")
        try:
            result = self.capability_promoter.promote(trace_id, target_kind=target_kind)
        except PromotionError as exc:
            raise LiveAedtError(exc.code, str(exc)) from exc
        except ValueError as exc:
            raise LiveAedtError("promotion_invalid", str(exc)) from exc
        return {
            "status": "candidate",
            **result.to_dict(),
            "source_trace_verified": True,
            "capture_required": False,
            "next_action": "human_review_only",
            "auto_applied": False,
            "hot_registered": False,
            "committed": False,
        }

    def close(self) -> None:
        for candidate in self._exploration_candidates.values():
            self._abandon_exploration_trace(candidate, event="manager_closed")
        self.registry.close()
        self._sessions.clear()
        self._approval_contexts.clear()
        self._exploration_candidates.clear()
        self._exploration_previews.clear()
        self._tainted_exploration_sessions.clear()

    def _execute(
        self,
        session_id: str,
        command: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        session = self._session(session_id)
        project_name = arguments.get("project_name")
        if self.required_project is not None and project_name is not None and project_name != self.required_project:
            raise LiveAedtError(
                "project_forbidden",
                f"this MCP server is restricted to AEDT project {self.required_project}",
            )
        design_name = arguments.get("design_name")
        if self.required_design is not None and design_name is not None and design_name != self.required_design:
            raise LiveAedtError(
                "design_forbidden",
                f"this MCP server is restricted to AEDT design {self.required_design}",
            )
        result = self.registry.execute(
            session.target,
            command,
            arguments,
            version=session.version,
            **({"timeout": timeout} if timeout else {}),
        )
        if command == "project_info":
            if self.required_project is not None and result.get("active_project") != self.required_project:
                raise LiveAedtError("project_forbidden", "the active AEDT project changed after Desktop launch")
            if self.required_design is not None and result.get("active_design") != self.required_design:
                raise LiveAedtError("design_forbidden", "the active AEDT design changed after Desktop launch")
        return {"live_session_id": session_id, **result}

    def _validate_requested_version(self, version: str) -> str:
        try:
            normalized = normalize_aedt_version(version)
        except ValueError as exc:
            raise LiveAedtError("invalid_version", str(exc)) from exc
        if self.required_version is not None and normalized != self.required_version:
            raise LiveAedtError(
                "version_forbidden",
                f"this MCP server is restricted to AEDT {self.required_version}",
            )
        return normalized

    def _validate_probe_version(self, probe: dict[str, Any], requested_version: str) -> None:
        reported_version = probe.get("version")
        if reported_version is not None and not aedt_versions_match(reported_version, requested_version):
            raise LiveAedtError(
                "version_mismatch",
                f"AEDT target reports version {reported_version}, not requested {requested_version}",
            )
        if not self.strict_desktop:
            return
        if reported_version is None or probe.get("version_verified") is not True:
            raise LiveAedtError(
                "version_unverified",
                "the strict AEDT Desktop session could not verify the connected AEDT version",
            )
        if not aedt_versions_match(reported_version, self.required_version):
            raise LiveAedtError(
                "version_mismatch",
                f"AEDT target reports version {reported_version}, not required {self.required_version}",
            )

    def _session(self, session_id: str) -> LiveSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise LiveAedtError("session_not_found", f"unknown live session: {session_id}") from exc

    def _register_approval(self, session_id: str, action: str, result: dict[str, Any]) -> dict[str, Any]:
        preview_id = str(result["preview_id"])
        digest = str(result["snapshot_digest"])
        resource_id = f"{session_id}:{preview_id}"
        self._approval_contexts[(session_id, preview_id)] = (action, digest, resource_id)
        result["approval_source"] = "external_host_only"
        result["approval_request"] = {"action": action, "resource_id": resource_id, "digest": digest}
        register = getattr(self.approval_verifier, "register", None)
        if callable(register):
            try:
                registration = register(action, resource_id, digest, dict(result))
            except Exception as exc:
                self._approval_contexts.pop((session_id, preview_id), None)
                raise LiveAedtError("approval_host_unavailable", str(exc)) from exc
            result["approval_status"] = registration.get("status", "pending")
            result["approval_poll"] = {
                "tool": "wait_for_live_approval",
                "live_session_id": session_id,
                "preview_id": preview_id,
            }
        result["release_required"] = True
        return result

    def _require_approval(self, session_id: str, action: str, preview_id: str, token: str) -> None:
        context = self._approval_contexts.get((session_id, preview_id))
        if context is None or context[0] != action:
            raise LiveAedtError("approval_required", "approval must reference a preview from this live session")
        verifier = self.approval_verifier
        if verifier is None:
            raise LiveAedtError("approval_required", "live apply requires an external host-approved token")
        verify = getattr(verifier, "verify", verifier)
        try:
            parameter_count = len(inspect.signature(verify).parameters)
            approved = (
                verify(action, context[2], context[1], token)
                if parameter_count >= 4
                else verify(preview_id, token)
            )
        except (TypeError, ValueError):
            approved = False
        if not approved:
            raise LiveAedtError("approval_required", "invalid, expired, replayed, or mismatched host approval token")

    def _discard_session_approvals(self, session_id: str) -> None:
        for key in [key for key in self._approval_contexts if key[0] == session_id]:
            del self._approval_contexts[key]

    def _discard_session_explorations(self, session_id: str) -> None:
        for key in [key for key in self._exploration_previews if key[0] == session_id]:
            context = self._exploration_previews[key]
            candidate = self._exploration_candidates.get(context["candidate_id"])
            if candidate is not None:
                self._abandon_exploration_trace(candidate, event="live_session_released")
            del self._exploration_previews[key]
        self._tainted_exploration_sessions.discard(session_id)

    def _exploration_candidate(self, candidate_id: str) -> dict[str, Any]:
        try:
            return self._exploration_candidates[candidate_id]
        except KeyError as exc:
            raise LiveAedtError("candidate_not_found", f"unknown exploratory candidate: {candidate_id}") from exc

    def _record_exploration_approval_decision(
        self,
        session_id: str,
        preview_id: str,
        candidate: dict[str, Any],
        status: str,
    ) -> None:
        normalized = status.strip().lower()
        if normalized == "approved":
            self._transition_exploration_trace(
                candidate,
                "approved",
                "host_approved",
                {"live_session_id": session_id, "preview_id": preview_id},
                idempotent=True,
            )
            candidate["status"] = "approved"
            return
        if normalized not in {"rejected", "expired"}:
            return
        current = self._trace_state(candidate)
        terminal_state = "expired" if current == "approved" else normalized
        self._transition_exploration_trace(
            candidate,
            terminal_state,
            f"host_{normalized}",
            {"live_session_id": session_id, "preview_id": preview_id},
            idempotent=True,
        )
        candidate["status"] = terminal_state
        self._approval_contexts.pop((session_id, preview_id), None)
        self._exploration_previews.pop((session_id, preview_id), None)

    def _transition_exploration_trace(
        self,
        candidate: dict[str, Any],
        state: str,
        event: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        try:
            current = self.trace_store.get(candidate["trace_id"])
            if idempotent and current["state"] == state:
                return current
            return self.trace_store.transition(candidate["trace_id"], state, event, payload or {})
        except TraceStateError as exc:
            raise LiveAedtError("trace_state_invalid", str(exc)) from exc
        except Exception as exc:
            raise LiveAedtError("trace_unavailable", f"could not append capability trace: {exc}") from exc

    def _trace_state(self, candidate: dict[str, Any]) -> str:
        try:
            return str(self.trace_store.get(candidate["trace_id"])["state"])
        except Exception as exc:
            raise LiveAedtError("trace_unavailable", f"could not read capability trace: {exc}") from exc

    def _fail_exploration_trace(self, candidate: dict[str, Any], event: str, error: Exception) -> None:
        current = self._trace_state(candidate)
        if current in {"verified", "rolled_back", "rollback_failed", "failed", "rejected", "expired"}:
            return
        self._transition_exploration_trace(
            candidate,
            "failed",
            event,
            {
                "error": {
                    "code": str(getattr(error, "code", type(error).__name__)),
                    "message": str(error)[:1000],
                }
            },
        )
        candidate["status"] = "failed"

    def _abandon_exploration_trace(self, candidate: dict[str, Any], *, event: str) -> None:
        try:
            current = self._trace_state(candidate)
            if current in {"verified", "rolled_back", "rollback_failed", "failed", "rejected", "expired"}:
                return
            target = "expired" if current in {"previewed", "approved"} else "rejected"
            self._transition_exploration_trace(candidate, target, event, {})
            candidate["status"] = target
        except LiveAedtError:
            # Cleanup must still release the live broker even if the trace store is unavailable.
            return


def _positive_int(value: Any, *, maximum: int | None = None) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _same_live_broker(left: LiveSession, right: LiveSession) -> bool:
    if left.version != right.version:
        return False
    if left.target == right.target:
        return True
    if left.pid is not None and left.pid == right.pid:
        return True
    return left.port is not None and left.port == right.port
