from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any

from aedt_agent.live.target import AedtTarget
from aedt_agent.live.versioning import (
    DEFAULT_AEDT_VERSION,
    aedt_versions_match,
    extract_reported_aedt_version,
    normalize_aedt_version,
)


_ANALYSIS_SUBMISSION_GRACE_SECONDS = 5.0
_MAX_SOLUTION_EVIDENCE_ATTEMPTS = 8


class LiveBackendError(RuntimeError):
    code = "backend_error"


class InvalidCommandError(LiveBackendError):
    code = "invalid_command"


class AedtVersionMismatchError(LiveBackendError):
    code = "version_mismatch"


class LiveAedtBackend:
    def __init__(
        self,
        *,
        version: str = DEFAULT_AEDT_VERSION,
        desktop_factory=None,
        hfss_factory=None,
        layout_factory=None,
    ) -> None:
        self.version = normalize_aedt_version(version)
        self._desktop_factory = desktop_factory
        self._hfss_factory = hfss_factory
        self._layout_factory = layout_factory
        self._desktop = None
        self._target: AedtTarget | None = None
        self._reported_version: str | None = None
        self._version_verified = False
        self._apps: dict[tuple[str, str, str], Any] = {}
        self._previews: dict[str, dict[str, Any]] = {}
        self._analysis_runs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._active_analysis_runs: dict[tuple[str, str], str] = {}
        configured_export_root = os.environ.get("AEDT_AGENT_EXPORT_ROOT")
        self._export_root = Path(configured_export_root or Path.cwd() / ".aedt-agent" / "exports").resolve()
        self._lock = threading.RLock()

    def execute(self, target: AedtTarget, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if command == "release":
                return {"released": self.release()}
            if command == "ping":
                return self._ping(target)
            if command == "project_info":
                return self._project_info(target)
            if command == "project_save_preview":
                return self._project_save_preview(target, arguments)
            if command == "project_save_apply":
                return self._project_save_apply(target, arguments)
            if command == "hfss_design_create":
                return self._hfss_design_create(target, arguments)
            if command == "hfss_design_inventory":
                return self._hfss_design_inventory(target, arguments)
            if command == "setup_inventory":
                return self._setup_inventory(target, arguments)
            if command == "solution_inventory":
                return self._solution_inventory(target, arguments)
            if command == "hfss_geometry_inventory":
                return self._hfss_geometry_inventory(target, arguments)
            if command == "hfss_material_inventory":
                return self._hfss_material_inventory(target, arguments)
            if command == "hfss_material_assign_preview":
                return self._hfss_material_assign_preview(target, arguments)
            if command == "hfss_material_assign_apply":
                return self._hfss_material_assign_apply(target, arguments)
            if command == "hfss_mesh_inventory":
                return self._hfss_mesh_inventory(target, arguments)
            if command == "hfss_length_mesh_create_preview":
                return self._hfss_length_mesh_create_preview(target, arguments)
            if command == "hfss_length_mesh_create_apply":
                return self._hfss_length_mesh_create_apply(target, arguments)
            if command == "hfss_far_field_inventory":
                return self._hfss_far_field_inventory(target, arguments)
            if command == "hfss_infinite_sphere_create_preview":
                return self._hfss_infinite_sphere_create_preview(target, arguments)
            if command == "hfss_infinite_sphere_create_apply":
                return self._hfss_infinite_sphere_create_apply(target, arguments)
            if command == "hfss_surface_boundary_inventory":
                return self._hfss_surface_boundary_inventory(target, arguments)
            if command == "hfss_surface_boundary_create_preview":
                return self._hfss_surface_boundary_create_preview(target, arguments)
            if command == "hfss_surface_boundary_create_apply":
                return self._hfss_surface_boundary_create_apply(target, arguments)
            if command == "hfss_geometry_create_preview":
                return self._hfss_geometry_create_preview(target, arguments)
            if command == "hfss_geometry_create_apply":
                return self._hfss_geometry_create_apply(target, arguments)
            if command == "hfss_geometry_boundary_create_preview":
                return self._hfss_geometry_boundary_create_preview(target, arguments)
            if command == "hfss_geometry_boundary_create_apply":
                return self._hfss_geometry_boundary_create_apply(target, arguments)
            if command == "hfss_setup_preview":
                return self._hfss_setup_preview(target, arguments)
            if command == "hfss_setup_apply":
                return self._hfss_setup_apply(target, arguments)
            if command == "hfss_setup_update_preview":
                return self._hfss_setup_update_preview(target, arguments)
            if command == "hfss_setup_update_apply":
                return self._hfss_setup_update_apply(target, arguments)
            if command == "frequency_sweep_create_preview":
                return self._frequency_sweep_create_preview(target, arguments)
            if command == "frequency_sweep_create_apply":
                return self._frequency_sweep_create_apply(target, arguments)
            if command == "hfss_setup_sweep_create_preview":
                return self._hfss_setup_sweep_create_preview(target, arguments)
            if command == "hfss_setup_sweep_create_apply":
                return self._hfss_setup_sweep_create_apply(target, arguments)
            if command == "hfss_report_preview":
                return self._hfss_report_preview(target, arguments)
            if command == "hfss_report_apply":
                return self._hfss_report_apply(target, arguments)
            if command == "hfss_boundary_preview":
                return self._hfss_boundary_preview(target, arguments)
            if command == "hfss_boundary_apply":
                return self._hfss_boundary_apply(target, arguments)
            if command == "hfss_analysis_start":
                return self._hfss_analysis_start(target, arguments)
            if command == "hfss_analysis_start_preview":
                return self._hfss_analysis_start_preview(target, arguments)
            if command == "hfss_analysis_start_apply":
                return self._hfss_analysis_start_apply(target, arguments)
            if command == "hfss_analysis_status":
                return self._hfss_analysis_status(target, arguments)
            if command == "hfss_analysis_cancel_preview":
                return self._hfss_analysis_cancel_preview(target, arguments)
            if command == "hfss_analysis_cancel_apply":
                return self._hfss_analysis_cancel_apply(target, arguments)
            if command == "hfss_export_preview":
                return self._hfss_export_preview(target, arguments)
            if command == "hfss_export_apply":
                return self._hfss_export_apply(target, arguments)
            if command == "layout_paths_list":
                return self._layout_paths_list(target, arguments)
            if command == "layout_routing_inventory":
                return self._layout_routing_inventory(target, arguments)
            if command == "layout_technology_inventory":
                return self._layout_technology_inventory(target, arguments)
            if command == "layout_connectivity_inventory":
                return self._layout_connectivity_inventory(target, arguments)
            if command == "layout_port_candidate_inventory":
                return self._layout_port_candidate_inventory(target, arguments)
            if command == "layout_component_ports_create_preview":
                return self._layout_component_ports_create_preview(target, arguments)
            if command == "layout_component_ports_create_apply":
                return self._layout_component_ports_create_apply(target, arguments)
            if command == "layout_edge_port_candidate_inventory":
                return self._layout_edge_port_candidate_inventory(target, arguments)
            if command == "layout_edge_ports_create_preview":
                return self._layout_edge_ports_create_preview(target, arguments)
            if command == "layout_edge_ports_create_apply":
                return self._layout_edge_ports_create_apply(target, arguments)
            if command == "layout_object_inventory":
                return self._layout_object_inventory(target, arguments)
            if command == "layout_object_property_inventory":
                return self._layout_object_property_inventory(target, arguments)
            if command == "layout_object_property_update_preview":
                return self._layout_object_property_update_preview(target, arguments)
            if command == "layout_object_property_update_apply":
                return self._layout_object_property_update_apply(target, arguments)
            if command == "variable_inventory":
                return self._variable_inventory(target, arguments)
            if command == "variable_upsert_preview":
                return self._variable_upsert_preview(target, arguments)
            if command == "variable_upsert_apply":
                return self._variable_upsert_apply(target, arguments)
            if command == "layout_width_preview":
                return self._layout_width_preview(target, arguments)
            if command == "layout_width_apply":
                return self._layout_width_apply(target, arguments)
            if command == "exploration_preview":
                return self._exploration_preview(target, arguments)
            if command == "exploration_apply":
                return self._exploration_apply(target, arguments)
            raise InvalidCommandError(f"unsupported live AEDT command: {command}")

    def release(self) -> bool:
        self._apps.clear()
        self._previews.clear()
        self._analysis_runs.clear()
        self._active_analysis_runs.clear()
        if self._desktop is not None:
            desktop = self._desktop
            self._desktop = None
            self._target = None
            self._reported_version = None
            self._version_verified = False
            return bool(desktop.release_desktop(close_projects=False, close_on_exit=False))
        return False

    def _connection_kwargs(self, target: AedtTarget) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "version": self.version,
            "non_graphical": False,
            "new_desktop": False,
            "close_on_exit": False,
        }
        if target.kind == "pid":
            kwargs["aedt_process_id"] = target.value
        else:
            kwargs.update({"machine": "localhost", "port": target.value})
        return kwargs

    def _desktop_for(self, target: AedtTarget):
        if self._desktop is None:
            if self._desktop_factory is None:
                from ansys.aedt.core import Desktop

                self._desktop_factory = Desktop
            desktop = self._desktop_factory(**self._connection_kwargs(target))
            reported_version = _desktop_aedt_version(desktop)
            if reported_version is not None and not aedt_versions_match(reported_version, self.version):
                try:
                    desktop.release_desktop(close_projects=False, close_on_exit=False)
                except Exception:
                    pass
                raise AedtVersionMismatchError(
                    f"AEDT target {target.key} reports version {reported_version}, "
                    f"but the broker requires {self.version}"
                )
            self._desktop = desktop
            self._target = target
            self._reported_version = reported_version
            self._version_verified = reported_version is not None
        elif target != self._target and not self._target_aliases(target):
            raise LiveBackendError(f"broker is bound to {self._target.key}, not {target.key}")
        return self._desktop

    def _target_aliases(self, target: AedtTarget) -> bool:
        if target.kind == "pid":
            return getattr(self._desktop, "aedt_process_id", None) == target.value
        return getattr(self._desktop, "port", None) == target.value

    def _identity(self, desktop, target: AedtTarget) -> dict[str, Any]:
        return {
            "target": target.to_dict(),
            "pid": getattr(desktop, "aedt_process_id", None),
            "port": getattr(desktop, "port", None),
            "version": self._reported_version or self.version,
            "requested_version": self.version,
            "version_verified": self._version_verified,
        }

    def _ping(self, target: AedtTarget) -> dict[str, Any]:
        desktop = self._desktop_for(target)
        return self._identity(desktop, target) | {"connected": True, "project_names": list(_read(desktop, "project_list"))}

    def _project_info(self, target: AedtTarget) -> dict[str, Any]:
        desktop = self._desktop_for(target)
        project = desktop.active_project()
        design = desktop.active_design(project) if project is not None else None
        return self._identity(desktop, target) | {
            "project_names": list(_read(desktop, "project_list")),
            "active_project": _name(project),
            "active_design": _design_display_name(desktop, design),
            "design_type": design.GetDesignType() if design is not None else None,
        }

    def _project_save_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        desktop = self._desktop_for(target)
        project_name = _required(args, "project_name")
        project_names = list(_read(desktop, "project_list"))
        if project_name not in project_names:
            raise LiveBackendError(f"project is not open in the target AEDT session: {project_name}")
        state = {"project_name": project_name, "project_names": project_names, "target": target.to_dict()}
        digest = _digest(state)
        preview_id = "save-preview-" + _digest(state)[:24]
        self._previews[preview_id] = {
            "kind": "project_save",
            "target": target,
            "project_name": project_name,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            "project_name": project_name,
            "snapshot_digest": digest,
            "approval_required": True,
            "project_saved": False,
        }

    def _project_save_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        try:
            preview = self._previews[preview_id]
        except KeyError as exc:
            raise LiveBackendError("unknown live project save preview") from exc
        if preview.get("kind") != "project_save" or preview["target"] != target:
            raise LiveBackendError("save preview belongs to a different operation or AEDT target")
        desktop = self._desktop_for(target)
        if preview["project_name"] not in list(_read(desktop, "project_list")):
            raise LiveBackendError("stale live project save preview")
        if not desktop.save_project(project_name=preview["project_name"]):
            raise LiveBackendError("AEDT rejected the project save operation")
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "project_name": preview["project_name"],
            "project_saved": True,
        }

    def _app(
        self,
        target: AedtTarget,
        kind: str,
        project: str,
        design: str,
        *,
        allow_design_create: bool = False,
        **extra: Any,
    ):
        canonical_design = _canonical_design_name(design)
        if canonical_design != design:
            raise LiveBackendError(
                f"design_name must be the AEDT display name {canonical_design!r}, not the internal identifier {design!r}"
            )
        key = (kind, project, design)
        if key in self._apps:
            return self._apps[key]
        desktop = self._desktop_for(target)
        project_names = [str(item) for item in list(_read(desktop, "project_list"))]
        if project not in project_names:
            raise LiveBackendError(f"project is not open in the target AEDT session: {project}")
        if not allow_design_create:
            design_names = [_canonical_design_name(str(item)) for item in list(_read(desktop, "design_list", project))]
            if design not in design_names:
                raise LiveBackendError(
                    f"design is not present in AEDT project {project}: {design}; refusing implicit design creation"
                )
        kwargs = self._connection_kwargs(target) | {"project": project, "design": design} | extra
        if kind == "hfss":
            if self._hfss_factory is None:
                from ansys.aedt.core import Hfss

                self._hfss_factory = Hfss
            factory = self._hfss_factory
        else:
            if self._layout_factory is None:
                from ansys.aedt.core import Hfss3dLayout

                self._layout_factory = Hfss3dLayout
            factory = self._layout_factory
        app = factory(**kwargs)
        actual_project = str(getattr(app, "project_name", ""))
        actual_design = _canonical_design_name(str(getattr(app, "design_name", "")))
        if actual_project != project or actual_design != design:
            try:
                app.release_desktop(close_projects=False, close_on_exit=False)
            except Exception:
                pass
            raise LiveBackendError(
                f"PyAEDT attached to {actual_project}/{actual_design}, expected {project}/{design}"
            )
        self._apps[key] = app
        return app

    def _hfss_design_create(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        project = _required(args, "project_name")
        design = _required(args, "design_name")
        solution_type = str(args.get("solution_type") or "DrivenModal")
        app = self._app(
            target,
            "hfss",
            project,
            design,
            allow_design_create=True,
            solution_type=solution_type,
        )
        return {"created_or_activated": True, "project_name": app.project_name, "design_name": app.design_name}

    def _hfss_design_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "hfss", _required(args, "project_name"), _required(args, "design_name"))
        setup_attribute = "existing_analysis_setups" if hasattr(app, "existing_analysis_setups") else "setup_names"
        boundaries = []
        for boundary in list(getattr(app, "boundaries", []) or []):
            boundaries.append(
                {
                    "name": str(getattr(boundary, "name", boundary)),
                    "type": str(getattr(boundary, "type", boundary.__class__.__name__)),
                }
            )
        post = getattr(app, "post", None)
        reports = list(getattr(post, "all_report_names", []) or []) if post is not None else []
        setup_names = list(_read(app, setup_attribute))
        setup_details = []
        for setup_name in setup_names:
            try:
                setup = app.get_setup(setup_name)
                properties = {
                    name: _json_value(setup.props.get(name))
                    for name in sorted(_HFSS_SETUP_PROPERTIES)
                    if name in setup.props
                }
                sweeps = [str(getattr(item, "name", item)) for item in list(getattr(setup, "sweeps", []) or [])]
                setup_details.append(
                    {"name": str(setup_name), "properties": properties, "sweeps": sorted(sweeps)}
                )
            except Exception:
                setup_details.append(
                    {"name": str(setup_name), "properties": {}, "sweeps": [], "status": "unavailable"}
                )
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "solution_type": str(getattr(app, "solution_type", "")),
            "setups": setup_names,
            "setup_details": setup_details,
            "ports": [str(item) for item in list(getattr(app, "ports", []) or [])],
            "boundaries": boundaries,
            "reports": [str(item) for item in reports],
        }

    def _setup_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _variable_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        names = _setup_names(app)
        details = []
        for name in names:
            try:
                details.append({"name": name, "sweeps": _sweep_names(app, name)})
            except Exception:
                details.append({"name": name, "sweeps": [], "status": "unavailable"})
        return {
            "product": product,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "setup_count": len(names),
            "setups": details,
            "ports": _port_names(app),
            "port_order_source": _port_order_source(app),
            "design_unchanged": True,
        }

    def _solution_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _analysis_product(args)
        app = self._app(
            target,
            product,
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        setup_name = str(args.get("setup_name") or "").strip()
        if setup_name and setup_name not in _setup_names(app):
            raise LiveBackendError(f"unknown setup: {setup_name}")
        snapshot = _solution_snapshot(app, setup_name)
        return {
            "product": product,
            "project_name": app.project_name,
            "design_name": app.design_name,
            **snapshot,
            "observed_at": _utc_now(),
            "design_unchanged": True,
        }

    def _hfss_geometry_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "hfss", _required(args, "project_name"), _required(args, "design_name"))
        requested = {str(item) for item in args.get("object_names") or []}
        objects = []
        for name in list(getattr(app.modeler, "object_names", []) or []):
            if requested and str(name) not in requested:
                continue
            obj = app.modeler[str(name)]
            faces = []
            for face in list(getattr(obj, "faces", []) or []):
                faces.append(
                    {
                        "face_id": int(getattr(face, "id")),
                        "center": _json_value(getattr(face, "center", None)),
                        "area": _json_value(getattr(face, "area", None)),
                        "is_planar": _json_value(_safe_attribute(face, "is_planar")),
                    }
                )
            faces.sort(key=lambda item: item["face_id"])
            objects.append(
                {
                    "name": str(name),
                    "object_id": _json_value(getattr(obj, "id", None)),
                    "material_name": str(getattr(obj, "material_name", "")),
                    "solve_inside": bool(getattr(obj, "solve_inside", False)),
                    "bounding_box": _json_value(_safe_attribute(obj, "bounding_box")),
                    "volume": _json_value(_safe_attribute(obj, "volume")),
                    "faces": faces,
                }
            )
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "object_count": len(objects),
            "objects": objects,
            "snapshot_digest": _digest(objects),
        }

    def _hfss_material_inventory(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS material inventory requires an HFSS 3D design")
        max_items = _bounded_integer(
            args.get("max_items", 100),
            "max_items",
            minimum=1,
            maximum=500,
        )
        materials = _safe_attribute(app, "materials")
        material_keys = _safe_attribute(materials, "material_keys") if materials else None
        if not isinstance(material_keys, dict):
            raise LiveBackendError("HFSS project material catalog is unavailable")
        canonical_names = sorted(
            {
                str(_safe_attribute(item, "name") or key)
                for key, item in material_keys.items()
            },
            key=str.casefold,
        )
        selected_names = canonical_names[:max_items]
        records = [_hfss_material_snapshot(app, name) for name in selected_names]
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "material_count": len(canonical_names),
            "returned_count": len(records),
            "truncated": len(canonical_names) > len(records),
            "materials": records,
            "snapshot_digest": _digest(records),
            "design_unchanged": True,
        }

    def _hfss_material_assign_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS material assignment requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot assign HFSS materials while a simulation is running")
        max_objects = _bounded_integer(
            args.get("max_objects", 16),
            "max_objects",
            minimum=1,
            maximum=32,
        )
        object_names = _normalize_explicit_names(
            args.get("object_names"),
            field="object_names",
            maximum=max_objects,
        )
        material_name = str(args.get("material_name") or "").strip()
        if not _SAFE_AEDT_MATERIAL_NAME.fullmatch(material_name):
            raise LiveBackendError("material_name must be a safe AEDT material name")
        target_material = _hfss_material_snapshot(app, material_name)
        targets = _hfss_material_target_snapshot(app, object_names)
        sheets = [item["name"] for item in targets if not item["is_solid"]]
        if sheets:
            raise LiveBackendError(
                f"HFSS material assignment only supports solid objects: {sheets[0]}"
            )
        already_assigned = [
            item["name"]
            for item in targets
            if item["material_name"].casefold()
            == target_material["canonical_name"].casefold()
        ]
        if already_assigned:
            raise LiveBackendError(
                f"HFSS object already uses target material: {already_assigned[0]}"
            )
        state = {
            "targets": targets,
            "target_material": target_material,
        }
        state_digest = _digest(state)
        spec = {
            "object_names": object_names,
            "material_name": target_material["canonical_name"],
            "max_objects": max_objects,
            "target_solve_inside": target_material["is_dielectric"],
        }
        preview_id = "material-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_material_assign",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "target_count": len(targets),
            "targets_before": targets,
            "target_material": target_material,
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_material_assign_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_material_assign", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot assign HFSS materials while a simulation is running")
        try:
            current = {
                "targets": _hfss_material_target_snapshot(
                    app,
                    preview["spec"]["object_names"],
                ),
                "target_material": _hfss_material_snapshot(
                    app,
                    preview["spec"]["material_name"],
                ),
            }
        except LiveBackendError as exc:
            raise LiveBackendError("stale HFSS material assignment preview") from exc
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS material assignment preview")

        try:
            assigned = app.assign_material(
                preview["spec"]["object_names"],
                preview["spec"]["material_name"],
            )
            if assigned is not True:
                raise LiveBackendError("HFSS material assignment returned false")
            targets_after = _hfss_material_target_snapshot(
                app,
                preview["spec"]["object_names"],
            )
            targets_before = {
                item["name"]: item for item in preview["state"]["targets"]
            }
            for item in targets_after:
                before = targets_before[item["name"]]
                if (
                    item["object_id"],
                    item["bounding_box"],
                    item["volume"],
                    item["is_solid"],
                ) != (
                    before["object_id"],
                    before["bounding_box"],
                    before["volume"],
                    before["is_solid"],
                ):
                    raise LiveBackendError(
                        "HFSS object identity or geometry changed during material "
                        f"assignment: {item['name']}"
                    )
                if item["material_name"].casefold() != preview["spec"][
                    "material_name"
                ].casefold():
                    raise LiveBackendError(
                        f"HFSS material readback failed: {item['name']}"
                    )
                if item["solve_inside"] is not preview["spec"]["target_solve_inside"]:
                    raise LiveBackendError(
                        f"HFSS solve_inside readback failed after material assignment: {item['name']}"
                    )
        except Exception as exc:
            rollback = _rollback_hfss_material_assignment(
                app,
                preview["state"]["targets"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS material assignment failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS material assignment failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **preview["spec"],
            "target_count": len(targets_after),
            "verified_count": len(targets_after),
            "targets_before": preview["state"]["targets"],
            "targets_after": targets_after,
            "target_material": current["target_material"],
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_mesh_inventory(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS mesh inventory requires an HFSS 3D design")
        max_items = _bounded_integer(
            args.get("max_items", 100),
            "max_items",
            minimum=1,
            maximum=500,
        )
        operation_names = _hfss_mesh_operation_names(app)
        selected_names = operation_names[:max_items]
        selected = _hfss_mesh_operation_snapshot(app, selected_names)
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "mesh_operation_count": len(operation_names),
            "returned_count": len(selected),
            "truncated": len(operation_names) > len(selected),
            "mesh_operations": selected,
            "snapshot_digest": _digest(
                {"operation_names": operation_names, "records": selected}
            ),
            "design_unchanged": True,
        }

    def _hfss_length_mesh_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS length mesh creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS mesh operations while a simulation is running")
        spec = _normalize_hfss_length_mesh_spec(args)
        targets = _hfss_material_target_snapshot(app, spec["object_names"])
        sheets = [item["name"] for item in targets if not item["is_solid"]]
        if sheets:
            raise LiveBackendError(
                f"HFSS length mesh creation only supports solid objects: {sheets[0]}"
            )
        mesh_operation_names = _hfss_mesh_operation_names(app)
        if len(mesh_operation_names) > 500:
            raise LiveBackendError(
                "HFSS design has more than 500 mesh operations; bounded preview is unavailable"
            )
        mesh_operations = _hfss_mesh_operation_snapshot(app, mesh_operation_names)
        existing_casefold = {
            item["name"].casefold(): item["name"] for item in mesh_operations
        }
        if spec["mesh_name"].casefold() in existing_casefold:
            raise LiveBackendError(
                f"HFSS mesh operation already exists: {existing_casefold[spec['mesh_name'].casefold()]}"
            )
        state = {
            "targets": targets,
            "mesh_operations": mesh_operations,
        }
        state_digest = _digest(state)
        preview_id = "length-mesh-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_length_mesh_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "target_count": len(targets),
            "targets": targets,
            "existing_mesh_operation_count": len(mesh_operations),
            "existing_mesh_operation_names": [item["name"] for item in mesh_operations],
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_length_mesh_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_length_mesh_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS mesh operations while a simulation is running")
        try:
            current_mesh_names = _hfss_mesh_operation_names(app)
            if len(current_mesh_names) > 500:
                raise LiveBackendError("HFSS mesh operation inventory exceeded preview bound")
            current = {
                "targets": _hfss_material_target_snapshot(
                    app,
                    preview["spec"]["object_names"],
                ),
                "mesh_operations": _hfss_mesh_operation_snapshot(
                    app,
                    current_mesh_names,
                ),
            }
        except LiveBackendError as exc:
            raise LiveBackendError("stale HFSS length mesh create preview") from exc
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS length mesh create preview")

        spec = preview["spec"]
        created_name = ""
        try:
            operation = app.mesh.assign_length_mesh(
                assignment=spec["object_names"],
                inside_selection=spec["inside_selection"],
                maximum_length=spec["maximum_length"],
                maximum_elements=spec["maximum_elements"],
                name=spec["mesh_name"],
            )
            created_name = str(getattr(operation, "name", "")) if operation else ""
            if operation is None or created_name != spec["mesh_name"]:
                raise LiveBackendError("HFSS length mesh creation returned an unexpected name")
            after_operations = _hfss_mesh_operation_snapshot(app)
            operation_by_name = {item["name"]: item for item in after_operations}
            readback = operation_by_name.get(spec["mesh_name"])
            if readback is None:
                raise LiveBackendError("HFSS length mesh readback is missing")
            _verify_hfss_length_mesh_readback(spec, readback)
            before_names = {item["name"] for item in preview["state"]["mesh_operations"]}
            after_names = {item["name"] for item in after_operations}
            if after_names != before_names | {spec["mesh_name"]}:
                raise LiveBackendError("unexpected HFSS mesh operation inventory change")
        except Exception as exc:
            rollback = _rollback_hfss_mesh_operation(
                app,
                created_name or spec["mesh_name"],
                before_operations=preview["state"]["mesh_operations"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS length mesh creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS length mesh creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "target_count": len(spec["object_names"]),
            "created_mesh_operation_name": spec["mesh_name"],
            "mesh_operation": readback,
            "mesh_operation_count": len(after_operations),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_far_field_inventory(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS far-field inventory requires an HFSS 3D design")
        max_items = _bounded_integer(
            args.get("max_items", 100),
            "max_items",
            minimum=1,
            maximum=500,
        )
        names = _hfss_field_setup_names(app)
        selected_names = names[:max_items]
        records = _hfss_field_setup_snapshot(app, selected_names)
        boundaries = _hfss_boundary_records(app)
        sources = [item for item in boundaries if _supports_radiated_fields(item["type"])]
        solution_type = str(_safe_attribute(app, "solution_type") or "").strip()
        blockers = []
        if _far_field_solution_forbidden(solution_type):
            blockers.append("solution_type_does_not_support_radiated_fields")
        if not sources:
            blockers.append("radiation_pml_or_hybrid_boundary_required")
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "solution_type": solution_type,
            "field_setup_count": len(names),
            "returned_count": len(records),
            "truncated": len(names) > len(records),
            "field_setups": records,
            "radiated_field_sources": sources,
            "creation_ready": not blockers,
            "creation_blockers": blockers,
            "snapshot_digest": _digest(
                {
                    "solution_type": solution_type,
                    "boundaries": boundaries,
                    "field_setup_names": names,
                    "records": records,
                }
            ),
            "design_unchanged": True,
        }

    def _hfss_infinite_sphere_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS infinite sphere creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS far-field setups while a simulation is running")
        solution_type = str(_safe_attribute(app, "solution_type") or "").strip()
        if _far_field_solution_forbidden(solution_type):
            raise LiveBackendError(
                f"HFSS solution type does not support infinite spheres: {solution_type}"
            )
        spec = _normalize_hfss_infinite_sphere_spec(args)
        names = _hfss_field_setup_names(app)
        if len(names) > 500:
            raise LiveBackendError(
                "HFSS design has more than 500 field setups; bounded preview is unavailable"
            )
        records = _hfss_field_setup_snapshot(app, names)
        existing_casefold = {item["name"].casefold(): item["name"] for item in records}
        if spec["sphere_name"].casefold() in existing_casefold:
            raise LiveBackendError(
                f"HFSS field setup already exists: {existing_casefold[spec['sphere_name'].casefold()]}"
            )
        boundaries = _hfss_boundary_records(app)
        sources = [item for item in boundaries if _supports_radiated_fields(item["type"])]
        if not sources:
            raise LiveBackendError(
                "HFSS infinite sphere creation requires an existing Radiation, PML, or hybrid boundary"
            )
        state = {
            "solution_type": solution_type,
            "boundaries": boundaries,
            "field_setups": records,
        }
        state_digest = _digest(state)
        preview_id = "infinite-sphere-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_infinite_sphere_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "solution_type": solution_type,
            "radiated_field_sources": sources,
            "existing_field_setup_count": len(records),
            "existing_field_setup_names": [item["name"] for item in records],
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_infinite_sphere_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_infinite_sphere_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS far-field setups while a simulation is running")
        try:
            current_names = _hfss_field_setup_names(app)
            if len(current_names) > 500:
                raise LiveBackendError("HFSS field setup inventory exceeded preview bound")
            current = {
                "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
                "boundaries": _hfss_boundary_records(app),
                "field_setups": _hfss_field_setup_snapshot(app, current_names),
            }
        except LiveBackendError as exc:
            raise LiveBackendError("stale HFSS infinite sphere create preview") from exc
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS infinite sphere create preview")

        spec = preview["spec"]
        created_name = ""
        try:
            sphere = app.insert_infinite_sphere(
                definition=spec["definition"],
                theta_start=spec["angle1_start"],
                theta_stop=spec["angle1_stop"],
                theta_step=spec["angle1_step"],
                phi_start=spec["angle2_start"],
                phi_stop=spec["angle2_stop"],
                phi_step=spec["angle2_step"],
                units=spec["units"],
                custom_coordinate_system=None,
                use_slant_polarization=spec["polarization"] == "Slant",
                polarization_angle=spec["polarization_angle"],
                name=spec["sphere_name"],
            )
            created_name = str(getattr(sphere, "name", "") or "") if sphere else ""
            if sphere is None or created_name != spec["sphere_name"]:
                raise LiveBackendError("HFSS infinite sphere creation returned an unexpected name")
            after_records = _hfss_field_setup_snapshot(app)
            record_by_name = {item["name"]: item for item in after_records}
            readback = record_by_name.get(spec["sphere_name"])
            if readback is None:
                raise LiveBackendError("HFSS infinite sphere readback is missing")
            _verify_hfss_infinite_sphere_readback(spec, readback)
            before_names = {item["name"] for item in preview["state"]["field_setups"]}
            after_names = {item["name"] for item in after_records}
            if after_names != before_names | {spec["sphere_name"]}:
                raise LiveBackendError("unexpected HFSS field setup inventory change")
        except Exception as exc:
            rollback = _rollback_hfss_field_setup(
                app,
                created_name or spec["sphere_name"],
                before_setups=preview["state"]["field_setups"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS infinite sphere creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS infinite sphere creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "created_field_setup_name": spec["sphere_name"],
            "field_setup": readback,
            "field_setup_count": len(after_records),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_surface_boundary_inventory(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS surface boundary inventory requires an HFSS 3D design")
        max_items = _bounded_integer(
            args.get("max_items", 100),
            "max_items",
            minimum=1,
            maximum=500,
        )
        records = _hfss_surface_boundary_snapshot(app)
        selected = records[:max_items]
        supported = [item for item in records if item["kind"] != "other"]
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "boundary_count": len(records),
            "supported_surface_boundary_count": len(supported),
            "returned_count": len(selected),
            "truncated": len(records) > len(selected),
            "boundaries": selected,
            "snapshot_digest": _digest(records),
            "design_unchanged": True,
        }

    def _hfss_surface_boundary_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS surface boundary creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS surface boundaries while a simulation is running")
        spec = _normalize_hfss_surface_boundary_spec(args)
        solution_type = str(_safe_attribute(app, "solution_type") or "").strip()
        if spec["boundary_kind"] == "impedance" and not _impedance_solution_supported(
            solution_type
        ):
            raise LiveBackendError(
                f"HFSS solution type does not support sheet impedance: {solution_type}"
            )
        if spec["boundary_kind"] == "lumped_rlc" and not _lumped_rlc_solution_supported(
            solution_type
        ):
            raise LiveBackendError(
                f"HFSS solution type does not support Lumped RLC: {solution_type}"
            )
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        target_records = _hfss_surface_boundary_targets(geometry, spec)
        _validate_hfss_infinite_ground_targets(geometry, spec, target_records)
        target_geometry = _hfss_surface_boundary_target_snapshot(target_records, spec)
        if spec["boundary_kind"] in {"impedance", "lumped_rlc"}:
            solids = [
                item["name"]
                for item in target_records
                if _hfss_geometry_record_is_solid(item)
            ]
            if solids:
                raise LiveBackendError(
                    f"HFSS {spec['boundary_kind']} boundary requires sheet objects: {solids[0]}"
                )
        if spec["boundary_kind"] == "lumped_rlc":
            _validate_hfss_lumped_rlc_target(target_records)
            spec["options"]["integration_line"] = _hfss_lumped_rlc_integration_line(
                app,
                spec["object_names"][0],
                spec["options"]["integration_line_direction"],
            )
        boundaries = _hfss_surface_boundary_snapshot(app)
        boundary_names = _boundary_names(app)
        if len(boundary_names) > 500:
            raise LiveBackendError(
                "HFSS design has more than 500 boundaries; bounded preview is unavailable"
            )
        by_name = {item.casefold(): item for item in boundary_names}
        if spec["boundary_name"].casefold() in by_name:
            raise LiveBackendError(
                f"HFSS boundary already exists: {by_name[spec['boundary_name'].casefold()]}"
            )
        material = None
        if spec["boundary_kind"] == "finite_conductivity":
            material = _hfss_material_snapshot(
                app,
                spec["options"]["material_name"],
            )
            spec["options"]["material_name"] = material["canonical_name"]
        state = {
            "solution_type": solution_type,
            "model_units": str(_safe_attribute(app.modeler, "model_units") or "").strip(),
            "target_geometry": target_geometry,
            "boundary_names": boundary_names,
            "boundaries": boundaries,
            "material": material,
        }
        state_digest = _digest(state)
        preview_id = "surface-boundary-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_surface_boundary_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "solution_type": solution_type,
            "assignment_count": len(spec["object_names"] or spec["face_ids"]),
            "target_geometry": target_geometry,
            "target_material": material,
            "existing_boundary_count": len(boundary_names),
            "existing_boundary_names": boundary_names,
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_surface_boundary_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_surface_boundary_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS surface boundaries while a simulation is running")
        spec = preview["spec"]

        def current_state() -> dict[str, Any]:
            current_geometry = self._hfss_geometry_inventory(
                target,
                {"project_name": app.project_name, "design_name": app.design_name},
            )
            current_material = None
            if spec["boundary_kind"] == "finite_conductivity":
                current_material = _hfss_material_snapshot(
                    app,
                    spec["options"]["material_name"],
                )
            return {
                "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
                "model_units": str(
                    _safe_attribute(app.modeler, "model_units") or ""
                ).strip(),
                "target_geometry": _hfss_surface_boundary_target_snapshot(
                    _hfss_surface_boundary_targets(current_geometry, spec),
                    spec,
                ),
                "boundary_names": _boundary_names(app),
                "boundaries": _hfss_surface_boundary_snapshot(app),
                "material": current_material,
            }

        try:
            current = current_state()
            if _digest(current) != preview["digest"]:
                # PyAEDT lazily populates some material/boundary properties on first read.
                # A second full snapshot distinguishes that cache warm-up from a real stale edit.
                current = current_state()
        except LiveBackendError as exc:
            raise LiveBackendError("stale HFSS surface boundary create preview") from exc
        if _digest(current) != preview["digest"]:
            changed = [
                name
                for name in current
                if _digest(current[name]) != _digest(preview["state"].get(name))
            ]
            if "target_geometry" in changed:
                geometry_changes = _hfss_target_geometry_changes(
                    preview["state"]["target_geometry"],
                    current["target_geometry"],
                )
                changed[changed.index("target_geometry")] = (
                    "target_geometry(" + ", ".join(geometry_changes[:8]) + ")"
                )
            detail = ", ".join(changed) or "unknown state"
            raise LiveBackendError(
                f"stale HFSS surface boundary create preview: changed {detail}"
            )

        created_name = ""
        try:
            boundary = _create_hfss_surface_boundary(app, spec)
            created_name = str(getattr(boundary, "name", "") or "") if boundary else ""
            if boundary is None or created_name != spec["boundary_name"]:
                raise LiveBackendError("HFSS surface boundary creation returned an unexpected name")
            after_boundaries = _hfss_surface_boundary_snapshot(app)
            boundary_by_name = {item["name"]: item for item in after_boundaries}
            readback = boundary_by_name.get(spec["boundary_name"])
            if readback is None:
                raise LiveBackendError("HFSS surface boundary readback is missing")
            _verify_hfss_surface_boundary_readback(spec, readback)
            before_names = set(preview["state"]["boundary_names"])
            after_names = set(_boundary_names(app))
            if after_names != before_names | {spec["boundary_name"]}:
                raise LiveBackendError("unexpected HFSS boundary inventory change")
        except Exception as exc:
            rollback = _rollback_hfss_surface_boundary(
                app,
                created_name or spec["boundary_name"],
                before_boundaries=preview["state"]["boundaries"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS surface boundary creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS surface boundary creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "created_boundary_name": spec["boundary_name"],
            "boundary": readback,
            "boundary_count": len(after_boundaries),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_geometry_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS geometry creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot edit HFSS geometry while a simulation is running")
        max_new_objects = _bounded_integer(
            args.get("max_new_objects", 16),
            "max_new_objects",
            minimum=1,
            maximum=32,
        )
        primitives = _normalize_hfss_primitives(
            args.get("primitives"),
            max_new_objects=max_new_objects,
        )
        existing_names = [str(item) for item in list(getattr(app.modeler, "object_names", []) or [])]
        model_units = str(_safe_attribute(app.modeler, "model_units") or "").strip()
        if not model_units:
            raise LiveBackendError("HFSS model units are unavailable")
        requested_names = [item["name"] for item in primitives]
        existing_casefold = {item.casefold(): item for item in existing_names}
        conflicts = sorted(
            existing_casefold[item.casefold()]
            for item in requested_names
            if item.casefold() in existing_casefold
        )
        if conflicts:
            raise LiveBackendError(f"HFSS object already exists: {conflicts[0]}")
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        state = {
            "object_names": existing_names,
            "geometry_digest": geometry["snapshot_digest"],
            "model_units": model_units,
        }
        state_digest = _digest(state)
        spec = {
            "primitives": primitives,
            "requested_object_names": requested_names,
            "expected_object_count": len(primitives),
            "max_new_objects": max_new_objects,
            "model_units": model_units,
        }
        preview_id = "geometry-preview-" + _digest(spec | {"state": state_digest})[:24]
        self._previews[preview_id] = {
            "kind": "hfss_geometry_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "existing_object_count": len(existing_names),
            "existing_object_names": existing_names,
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_geometry_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_geometry_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot edit HFSS geometry while a simulation is running")
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        current_state = {
            "object_names": [str(item) for item in list(getattr(app.modeler, "object_names", []) or [])],
            "geometry_digest": geometry["snapshot_digest"],
            "model_units": str(_safe_attribute(app.modeler, "model_units") or "").strip(),
        }
        if _digest(current_state) != preview["digest"]:
            raise LiveBackendError("stale HFSS geometry create preview")

        created_names: list[str] = []
        try:
            for primitive in preview["spec"]["primitives"]:
                created = _create_hfss_primitive(app, primitive)
                current_names = [
                    str(item) for item in list(getattr(app.modeler, "object_names", []) or [])
                ]
                if primitive["name"] in current_names:
                    created_names.append(primitive["name"])
                if created is None or primitive["name"] not in current_names:
                    raise LiveBackendError(
                        f"HFSS geometry readback failed after creating {primitive['name']}"
                    )

            requested_names = preview["spec"]["requested_object_names"]
            if created_names != requested_names:
                raise LiveBackendError("HFSS geometry created object order does not match preview")
            readback = self._hfss_geometry_inventory(
                target,
                {
                    "project_name": app.project_name,
                    "design_name": app.design_name,
                    "object_names": requested_names,
                },
            )
            readback_names = [str(item["name"]) for item in readback["objects"]]
            if set(readback_names) != set(requested_names):
                raise LiveBackendError("HFSS geometry batch readback verification failed")
            _verify_hfss_primitive_readback(preview["spec"]["primitives"], readback["objects"])
        except Exception as exc:
            rollback = _rollback_hfss_objects(
                app,
                created_names,
                before_names=preview["state"]["object_names"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS geometry creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS geometry creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **preview["spec"],
            "created_object_count": len(created_names),
            "created_object_names": created_names,
            "objects": readback["objects"],
            "geometry_snapshot_digest": readback["snapshot_digest"],
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_geometry_boundary_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS geometry and boundary creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot edit HFSS geometry while a simulation is running")
        max_new_objects = _bounded_integer(
            args.get("max_new_objects", 16),
            "max_new_objects",
            minimum=1,
            maximum=32,
        )
        max_new_boundaries = _bounded_integer(
            args.get("max_new_boundaries", 16),
            "max_new_boundaries",
            minimum=1,
            maximum=32,
        )
        primitives = _normalize_hfss_primitives(
            args.get("primitives"),
            max_new_objects=max_new_objects,
        )
        existing_names = [str(item) for item in list(getattr(app.modeler, "object_names", []) or [])]
        requested_names = [item["name"] for item in primitives]
        existing_casefold = {item.casefold(): item for item in existing_names}
        conflicts = sorted(
            existing_casefold[item.casefold()]
            for item in requested_names
            if item.casefold() in existing_casefold
        )
        if conflicts:
            raise LiveBackendError(f"HFSS object already exists: {conflicts[0]}")
        existing_boundaries = _boundary_names(app)
        boundaries = _normalize_hfss_geometry_boundaries(
            args.get("boundaries"),
            new_object_names=requested_names,
            reference_object_names=existing_names + requested_names,
            existing_boundary_names=existing_boundaries,
            max_new_boundaries=max_new_boundaries,
        )
        model_units = str(_safe_attribute(app.modeler, "model_units") or "").strip()
        if not model_units:
            raise LiveBackendError("HFSS model units are unavailable")
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        state = {
            "object_names": existing_names,
            "geometry_digest": geometry["snapshot_digest"],
            "boundary_names": existing_boundaries,
            "model_units": model_units,
        }
        state_digest = _digest(state)
        spec = {
            "primitives": primitives,
            "boundaries": boundaries,
            "requested_object_names": requested_names,
            "requested_boundary_names": [item["boundary_name"] for item in boundaries],
            "expected_object_count": len(primitives),
            "expected_boundary_count": len(boundaries),
            "max_new_objects": max_new_objects,
            "max_new_boundaries": max_new_boundaries,
            "model_units": model_units,
        }
        preview_id = "geometry-boundary-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_geometry_boundary_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "existing_object_count": len(existing_names),
            "existing_boundary_count": len(existing_boundaries),
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_geometry_boundary_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_geometry_boundary_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot edit HFSS geometry while a simulation is running")
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        current_state = {
            "object_names": [str(item) for item in list(getattr(app.modeler, "object_names", []) or [])],
            "geometry_digest": geometry["snapshot_digest"],
            "boundary_names": _boundary_names(app),
            "model_units": str(_safe_attribute(app.modeler, "model_units") or "").strip(),
        }
        if _digest(current_state) != preview["digest"]:
            raise LiveBackendError("stale HFSS geometry and boundary create preview")

        spec = preview["spec"]
        created_names: list[str] = []
        created_boundary_names: list[str] = []
        resolved_boundaries: list[dict[str, Any]] = []
        try:
            for primitive in spec["primitives"]:
                created = _create_hfss_primitive(app, primitive)
                current_names = [
                    str(item) for item in list(getattr(app.modeler, "object_names", []) or [])
                ]
                if primitive["name"] in current_names:
                    created_names.append(primitive["name"])
                if created is None or primitive["name"] not in current_names:
                    raise LiveBackendError(
                        f"HFSS geometry readback failed after creating {primitive['name']}"
                    )

            readback = self._hfss_geometry_inventory(
                target,
                {
                    "project_name": app.project_name,
                    "design_name": app.design_name,
                    "object_names": spec["requested_object_names"],
                },
            )
            readback_by_name = {str(item["name"]): item for item in readback["objects"]}
            if set(readback_by_name) != set(spec["requested_object_names"]):
                raise LiveBackendError("HFSS atomic geometry readback verification failed")
            _verify_hfss_primitive_readback(spec["primitives"], readback["objects"])

            for boundary_spec in spec["boundaries"]:
                assignment = readback_by_name[boundary_spec["assignment_object"]]
                face_ids = _resolve_hfss_face_selector(
                    assignment,
                    boundary_spec["face_selector"],
                )
                boundary = _create_hfss_boundary(app, boundary_spec, face_ids)
                boundary_name = boundary_spec["boundary_name"]
                if not boundary or boundary_name not in _boundary_names(app):
                    raise LiveBackendError(
                        f"HFSS boundary readback failed after creating {boundary_name}"
                    )
                readback_type = str(getattr(boundary, "type", ""))
                if not _hfss_boundary_type_matches(
                    boundary_spec["boundary_kind"],
                    readback_type,
                ):
                    raise LiveBackendError(
                        f"HFSS boundary type readback failed for {boundary_name}: {readback_type}"
                    )
                created_boundary_names.append(boundary_name)
                resolved_boundaries.append(
                    {
                        **boundary_spec,
                        "assignment_face_ids": face_ids,
                        "readback_type": readback_type,
                    }
                )
            if created_boundary_names != spec["requested_boundary_names"]:
                raise LiveBackendError("HFSS boundary created order does not match preview")
        except Exception as exc:
            boundary_rollback = _rollback_hfss_boundaries(
                app,
                spec["requested_boundary_names"],
                before_names=preview["state"]["boundary_names"],
            )
            geometry_rollback = _rollback_hfss_objects(
                app,
                created_names,
                before_names=preview["state"]["object_names"],
            )
            if not boundary_rollback["complete"] or not geometry_rollback["complete"]:
                raise LiveBackendError(
                    "HFSS geometry and boundary creation failed and rollback is incomplete: "
                    f"boundaries={boundary_rollback}; geometry={geometry_rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS geometry and boundary creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "created_object_count": len(created_names),
            "created_object_names": created_names,
            "created_boundary_count": len(created_boundary_names),
            "created_boundary_names": created_boundary_names,
            "objects": readback["objects"],
            "resolved_boundaries": resolved_boundaries,
            "geometry_snapshot_digest": readback["snapshot_digest"],
            "automatic_rollback_on_failure": True,
            "atomic_geometry_boundary_transaction": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_setup_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "hfss", _required(args, "project_name"), _required(args, "design_name"))
        setup_name = _required(args, "setup_name")
        setup_type = str(args.get("setup_type") or "HFSSDriven")
        properties = dict(args.get("properties") or {})
        unsupported = sorted(set(properties).difference(_HFSS_SETUP_PROPERTIES))
        if unsupported:
            raise LiveBackendError(f"unsupported HFSS setup property: {unsupported[0]}")
        setup_names = _setup_names(app)
        if setup_name in setup_names:
            raise LiveBackendError(f"HFSS setup already exists: {setup_name}")
        state_digest = _digest(setup_names)
        preview_id = "setup-preview-" + _digest(
            {"project": app.project_name, "design": app.design_name, "name": setup_name, "type": setup_type, "properties": properties, "state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_setup",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "setup_name": setup_name,
            "setup_type": setup_type,
            "properties": properties,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            "setup_name": setup_name,
            "setup_type": setup_type,
            "properties": properties,
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _hfss_setup_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_setup", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _digest(_setup_names(app)) != preview["digest"]:
            raise LiveBackendError("stale HFSS setup preview")
        setup = None
        try:
            setup = app.create_setup(preview["setup_name"], setup_type=preview["setup_type"])
            if not setup:
                raise LiveBackendError("failed to create HFSS setup")
            for name, value in preview["properties"].items():
                setup.props[name] = value
            if preview["properties"] and not setup.update():
                raise LiveBackendError("failed to update HFSS setup properties")
            readback = app.get_setup(preview["setup_name"])
            after = {name: _json_value(readback.props.get(name)) for name in preview["properties"]}
            if any(str(after[name]) != str(value) for name, value in preview["properties"].items()):
                raise LiveBackendError("HFSS setup readback verification failed")
        except Exception:
            if preview["setup_name"] in _setup_names(app):
                try:
                    app.delete_setup(preview["setup_name"])
                except Exception:
                    pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "setup_name": preview["setup_name"],
            "properties": after,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_setup_update_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "hfss", _required(args, "project_name"), _required(args, "design_name"))
        setup_name = _required(args, "setup_name")
        properties = dict(args.get("properties") or {})
        if not properties:
            raise LiveBackendError("at least one HFSS setup property is required")
        unsupported = sorted(set(properties).difference(_HFSS_SETUP_PROPERTIES))
        if unsupported:
            raise LiveBackendError(f"unsupported HFSS setup property: {unsupported[0]}")
        if setup_name not in _setup_names(app):
            raise LiveBackendError(f"unknown HFSS setup: {setup_name}")
        setup = app.get_setup(setup_name)
        before = {
            name: {
                "existed": name in setup.props,
                "value": _json_value(setup.props.get(name)),
            }
            for name in properties
        }
        snapshot = {"setup_names": _setup_names(app), "setup_name": setup_name, "before": before}
        digest = _digest(snapshot)
        preview_id = "setup-update-preview-" + _digest(
            {**snapshot, "properties": properties}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_setup_update",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "setup_name": setup_name,
            "properties": properties,
            "before": before,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            "setup_name": setup_name,
            "before": before,
            "after": properties,
            "snapshot_digest": digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _hfss_setup_update_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_setup_update", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if preview["setup_name"] not in _setup_names(app):
            raise LiveBackendError("stale HFSS setup update preview")
        setup = app.get_setup(preview["setup_name"])
        current_before = {
            name: {
                "existed": name in setup.props,
                "value": _json_value(setup.props.get(name)),
            }
            for name in preview["properties"]
        }
        current = {
            "setup_names": _setup_names(app),
            "setup_name": preview["setup_name"],
            "before": current_before,
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS setup update preview")
        try:
            for name, value in preview["properties"].items():
                setup.props[name] = value
            if not setup.update():
                raise LiveBackendError("failed to update HFSS setup properties")
            readback = app.get_setup(preview["setup_name"])
            after = {name: _json_value(readback.props.get(name)) for name in preview["properties"]}
            if any(str(after[name]) != str(value) for name, value in preview["properties"].items()):
                raise LiveBackendError("HFSS setup update readback verification failed")
        except Exception:
            try:
                for name, state in preview["before"].items():
                    if state["existed"]:
                        setup.props[name] = state["value"]
                    else:
                        setup.props.pop(name, None)
                setup.update()
            except Exception:
                pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "setup_name": preview["setup_name"],
            "before": preview["before"],
            "after": after,
            "project_dirty": True,
            "project_saved": False,
        }

    def _frequency_sweep_create_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _variable_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        setup_name = _required(args, "setup_name")
        sweep_name = _required(args, "sweep_name")
        range_type = str(args.get("range_type") or "LinearCount")
        if range_type not in {"LinearCount", "LinearStep"}:
            raise LiveBackendError("range_type must be LinearCount or LinearStep")
        sweep_type = str(args.get("sweep_type") or "Interpolating")
        if sweep_type not in {"Discrete", "Interpolating", "Fast"}:
            raise LiveBackendError("sweep_type must be Discrete, Interpolating, or Fast")
        unit = str(args.get("unit") or "GHz")
        if unit not in {"Hz", "kHz", "MHz", "GHz", "THz"}:
            raise LiveBackendError("unsupported frequency unit")
        start = _positive_number(args, "start_frequency")
        stop = _positive_number(args, "stop_frequency")
        if stop <= start:
            raise LiveBackendError("stop_frequency must be greater than start_frequency")
        count = args.get("count")
        step = args.get("step_size")
        if range_type == "LinearCount":
            if type(count) is not int or not 2 <= count <= 100001:
                raise LiveBackendError("count must be an integer between 2 and 100001")
            step = None
        else:
            step = _positive_number(args, "step_size")
            if step >= stop - start:
                raise LiveBackendError("step_size must be smaller than the sweep span")
            count = None
        if setup_name not in _setup_names(app):
            raise LiveBackendError(f"unknown setup: {setup_name}")
        sweep_names = _sweep_names(app, setup_name)
        if sweep_name in sweep_names:
            raise LiveBackendError(f"frequency sweep already exists: {sweep_name}")
        state = {"setup_names": _setup_names(app), "setup_name": setup_name, "sweep_names": sweep_names}
        digest = _digest(state)
        spec = {
            "product": product,
            "setup_name": setup_name,
            "sweep_name": sweep_name,
            "range_type": range_type,
            "sweep_type": sweep_type,
            "unit": unit,
            "start_frequency": start,
            "stop_frequency": stop,
            "count": count,
            "step_size": step,
            "save_fields": bool(args.get("save_fields", True)),
        }
        preview_id = "sweep-create-preview-" + _digest({**spec, "state": digest})[:24]
        self._previews[preview_id] = {
            "kind": "frequency_sweep_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "snapshot_digest": digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _frequency_sweep_create_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "frequency_sweep_create", target)
        spec = preview["spec"]
        app = self._app(target, spec["product"], preview["project_name"], preview["design_name"])
        current = {
            "setup_names": _setup_names(app),
            "setup_name": spec["setup_name"],
            "sweep_names": _sweep_names(app, spec["setup_name"]),
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale frequency sweep preview")
        sweep = None
        try:
            common = {
                "setup": spec["setup_name"],
                "unit": spec["unit"],
                "start_frequency": spec["start_frequency"],
                "stop_frequency": spec["stop_frequency"],
                "name": spec["sweep_name"],
                "save_fields": spec["save_fields"],
                "sweep_type": spec["sweep_type"],
            }
            if spec["range_type"] == "LinearCount":
                sweep = app.create_linear_count_sweep(
                    **common,
                    num_of_freq_points=spec["count"],
                )
            else:
                sweep = app.create_linear_step_sweep(
                    **common,
                    step_size=spec["step_size"],
                )
            if not sweep or spec["sweep_name"] not in _sweep_names(app, spec["setup_name"]):
                raise LiveBackendError("frequency sweep readback verification failed")
        except Exception:
            if spec["sweep_name"] in _sweep_names(app, spec["setup_name"]):
                try:
                    app.get_setup(spec["setup_name"]).delete_sweep(spec["sweep_name"])
                except Exception:
                    pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_setup_sweep_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS setup and sweep creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot create an HFSS setup while a simulation is running")
        setup_spec = _normalize_hfss_setup_spec(args.get("setup"))
        sweep_spec = _normalize_hfss_sweep_spec(args.get("sweep"))
        setup_names = _setup_names(app)
        port_names = _hfss_port_names(app)
        if sweep_spec["sweep_type"] != "Discrete" and not port_names:
            raise LiveBackendError(
                f"{sweep_spec['sweep_type']} HFSS sweeps require at least one existing port"
            )
        existing_casefold = {item.casefold(): item for item in setup_names}
        setup_name = setup_spec["name"]
        if setup_name.casefold() in existing_casefold:
            raise LiveBackendError(
                f"HFSS setup already exists: {existing_casefold[setup_name.casefold()]}"
            )
        state = {"setup_names": setup_names, "port_names": port_names}
        state_digest = _digest(state)
        spec = {"setup": setup_spec, "sweep": sweep_spec}
        preview_id = "setup-sweep-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_setup_sweep_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "snapshot_digest": state_digest,
            "existing_setup_names": setup_names,
            "existing_port_names": port_names,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_setup_sweep_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_setup_sweep_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create an HFSS setup while a simulation is running")
        current = {
            "setup_names": _setup_names(app),
            "port_names": _hfss_port_names(app),
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS setup and sweep create preview")

        setup_spec = preview["spec"]["setup"]
        sweep_spec = preview["spec"]["sweep"]
        setup_name = setup_spec["name"]
        sweep_name = sweep_spec["name"]
        try:
            setup = app.create_setup(setup_name, setup_type=setup_spec["type"])
            if not setup or setup_name not in _setup_names(app):
                raise LiveBackendError("HFSS setup readback failed after creation")
            for name, value in setup_spec["properties"].items():
                setup.props[name] = value
            if setup_spec["properties"] and not setup.update():
                raise LiveBackendError("failed to update HFSS setup properties")
            setup_readback = app.get_setup(setup_name)
            property_readback = {
                name: _json_value(setup_readback.props.get(name))
                for name in setup_spec["properties"]
            }
            if any(
                str(property_readback[name]) != str(value)
                for name, value in setup_spec["properties"].items()
            ):
                raise LiveBackendError("HFSS setup property readback verification failed")

            common = {
                "setup": setup_name,
                "unit": sweep_spec["unit"],
                "start_frequency": sweep_spec["start_frequency"],
                "stop_frequency": sweep_spec["stop_frequency"],
                "name": sweep_name,
                "save_fields": sweep_spec["save_fields"],
                "sweep_type": sweep_spec["sweep_type"],
            }
            if sweep_spec["range_type"] == "LinearCount":
                sweep = app.create_linear_count_sweep(
                    **common,
                    num_of_freq_points=sweep_spec["count"],
                )
            else:
                sweep = app.create_linear_step_sweep(
                    **common,
                    step_size=sweep_spec["step_size"],
                )
            if not sweep or sweep_name not in _sweep_names(app, setup_name):
                raise LiveBackendError("HFSS sweep readback failed after creation")
            setup_inventory = {
                "name": setup_name,
                "type": setup_spec["type"],
                "properties": property_readback,
                "sweeps": _sweep_names(app, setup_name),
            }
            if setup_inventory["sweeps"] != [sweep_name]:
                raise LiveBackendError("HFSS setup and sweep inventory verification failed")
        except Exception as exc:
            rollback = _rollback_hfss_setup(
                app,
                setup_name,
                before_names=preview["state"]["setup_names"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS setup and sweep creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS setup and sweep creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "setup": setup_spec,
            "sweep": sweep_spec,
            "setup_inventory": setup_inventory,
            "created_setup_name": setup_name,
            "created_sweep_name": sweep_name,
            "atomic_setup_sweep_transaction": True,
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_report_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "hfss", _required(args, "project_name"), _required(args, "design_name"))
        report_name = _required(args, "report_name")
        setup_sweep_name = _required(args, "setup_sweep_name")
        expressions = [str(item) for item in args.get("expressions") or []]
        if not expressions:
            raise LiveBackendError("at least one report expression is required")
        if report_name in _report_names(app):
            raise LiveBackendError(f"HFSS report already exists: {report_name}")
        setup_name = setup_sweep_name.split(":", 1)[0].strip()
        if setup_name not in _setup_names(app):
            raise LiveBackendError(f"report references an unknown HFSS setup: {setup_name}")
        state = {"setups": _setup_names(app), "reports": _report_names(app)}
        state_digest = _digest(state)
        spec = {
            "report_name": report_name,
            "setup_sweep_name": setup_sweep_name,
            "expressions": expressions,
            "domain": str(args.get("domain") or "Sweep"),
            "plot_type": str(args.get("plot_type") or "Rectangular Plot"),
        }
        preview_id = "report-preview-" + _digest(spec | {"state": state_digest})[:24]
        self._previews[preview_id] = {
            "kind": "hfss_report",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _hfss_report_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_report", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        current = {"setups": _setup_names(app), "reports": _report_names(app)}
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS report preview")
        spec = preview["spec"]
        try:
            report = app.post.create_report(
                expressions=spec["expressions"],
                setup_sweep_name=spec["setup_sweep_name"],
                domain=spec["domain"],
                plot_type=spec["plot_type"],
                plot_name=spec["report_name"],
                show=False,
            )
            if not report or spec["report_name"] not in _report_names(app):
                raise LiveBackendError("HFSS report readback verification failed")
        except Exception:
            if spec["report_name"] in _report_names(app):
                try:
                    app.post.delete_report(spec["report_name"])
                except Exception:
                    pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_boundary_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "hfss", _required(args, "project_name"), _required(args, "design_name"))
        boundary_kind = _required(args, "boundary_kind").lower()
        if boundary_kind not in _HFSS_BOUNDARY_OPTIONS:
            raise LiveBackendError(f"unsupported HFSS boundary kind: {boundary_kind}")
        boundary_name = _required(args, "boundary_name")
        face_ids = args.get("assignment_face_ids") or []
        if not face_ids or any(type(item) is not int or item <= 0 for item in face_ids):
            raise LiveBackendError("assignment_face_ids must contain positive integer face IDs")
        if boundary_kind != "radiation" and len(face_ids) != 1:
            raise LiveBackendError(f"{boundary_kind} requires exactly one assignment face ID")
        references = list(args.get("references") or [])
        if any(not isinstance(item, (str, int)) or isinstance(item, bool) for item in references):
            raise LiveBackendError("references must contain only object names or face IDs")
        options = _normalize_hfss_boundary_options(
            boundary_kind,
            args.get("options"),
            "options",
        )
        unsupported = sorted(set(options).difference(_HFSS_BOUNDARY_OPTIONS[boundary_kind]))
        if unsupported:
            raise LiveBackendError(f"unsupported {boundary_kind} option: {unsupported[0]}")
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        known_faces = {
            face["face_id"]
            for obj in geometry["objects"]
            for face in obj["faces"]
        }
        missing = sorted(set(face_ids).difference(known_faces))
        if missing:
            raise LiveBackendError(f"unknown HFSS face ID: {missing[0]}")
        existing = _boundary_names(app)
        if boundary_name in existing:
            raise LiveBackendError(f"HFSS boundary or port already exists: {boundary_name}")
        state = {"geometry": geometry["snapshot_digest"], "boundaries": existing}
        state_digest = _digest(state)
        spec = {
            "boundary_kind": boundary_kind,
            "boundary_name": boundary_name,
            "assignment_face_ids": list(face_ids),
            "references": references,
            "options": options,
        }
        preview_id = "boundary-preview-" + _digest(spec | {"state": state_digest})[:24]
        self._previews[preview_id] = {
            "kind": "hfss_boundary",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "snapshot_digest": state_digest,
            "geometry_digest": geometry["snapshot_digest"],
            "approval_required": True,
            "project_dirty": False,
        }

    def _hfss_boundary_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_boundary", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        current = {"geometry": geometry["snapshot_digest"], "boundaries": _boundary_names(app)}
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS boundary preview")
        spec = preview["spec"]
        boundary = None
        try:
            boundary = _create_hfss_boundary(
                app,
                spec,
                spec["assignment_face_ids"],
            )
            if not boundary or spec["boundary_name"] not in _boundary_names(app):
                raise LiveBackendError("HFSS boundary readback verification failed")
        except Exception:
            if boundary is not None:
                try:
                    boundary.delete()
                except Exception:
                    pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_analysis_start(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _analysis_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        setup = _required(args, "setup_name")
        blocking = bool(args.get("blocking", False))
        started = bool(app.analyze_setup(setup, blocking=blocking))
        return {"started": started, "product": product, "setup_name": setup, "blocking": blocking}

    def _hfss_analysis_start_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _analysis_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        setup_name = _required(args, "setup_name")
        if setup_name not in _setup_names(app):
            raise LiveBackendError(f"unknown HFSS setup: {setup_name}")
        if _simulation_running(app):
            raise LiveBackendError("an AEDT simulation is already running")
        resources = _analysis_resources(args)
        state = _analysis_state(app, setup_name)
        digest = _digest(state)
        spec = {"product": product, "setup_name": setup_name, "resources": resources}
        preview_id = "analysis-preview-" + _digest(spec | {"state": digest})[:24]
        self._previews[preview_id] = {
            "kind": "hfss_analysis_start",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "snapshot_digest": digest,
            "approval_required": True,
            "risk": "expensive_solver_job",
            "blocking": False,
            "project_saved": False,
        }

    def _hfss_analysis_start_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_analysis_start", target)
        app = self._app(target, preview["spec"]["product"], preview["project_name"], preview["design_name"])
        spec = preview["spec"]
        if _digest(_analysis_state(app, spec["setup_name"])) != preview["digest"]:
            raise LiveBackendError("stale HFSS analysis preview")
        resources = spec["resources"]
        solution_before = _solution_snapshot(
            app,
            spec["setup_name"],
            query_solution_data=False,
        )
        started = bool(
            app.analyze_setup(
                spec["setup_name"],
                cores=resources["cores"],
                tasks=resources["tasks"],
                gpus=resources["gpus"],
                use_auto_settings=resources["use_auto_settings"],
                blocking=False,
            )
        )
        if not started:
            raise LiveBackendError("AEDT rejected the HFSS analysis start request")
        started_at = _utc_now()
        run_id = "aedt-run-" + _digest(
            {
                "target": target.to_dict(),
                "project": app.project_name,
                "design": app.design_name,
                "setup": spec["setup_name"],
                "started_at": started_at,
            }
        )[:24]
        running = _simulation_running(app)
        run = {
            "run_id": run_id,
            "product": spec["product"],
            "setup_name": spec["setup_name"],
            "resources": resources,
            "started_at": started_at,
            "state": "running" if running else "submitted",
            "_observed_running": running,
            "_submitted_monotonic": time.monotonic(),
            "_solution_before": solution_before,
        }
        run_key = (app.project_name, app.design_name, spec["setup_name"])
        self._analysis_runs[run_key] = run
        self._active_analysis_runs[(app.project_name, app.design_name)] = spec["setup_name"]
        del self._previews[preview_id]
        return {
            "status": "submitted",
            "started": True,
            "preview_id": preview_id,
            **_public_analysis_run(run),
            "blocking": False,
            "project_saved": False,
        }

    def _hfss_analysis_status(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _analysis_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        setup_attribute = "existing_analysis_setups" if hasattr(app, "existing_analysis_setups") else "setup_names"
        setup_name = str(args.get("setup_name") or "")
        running = _simulation_running(app)
        run = self._analysis_runs.get((app.project_name, app.design_name, setup_name)) if setup_name else None
        active_key = (app.project_name, app.design_name)
        if run is not None and self._active_analysis_runs.get(active_key) == setup_name:
            _refresh_analysis_run(run, running)
            if run.get("state") not in {"submitted", "running"}:
                self._active_analysis_runs.pop(active_key, None)
        if run is not None and run.get("state") in {"not_running", "not_running_unverified"}:
            _finalize_analysis_solution_evidence(run, app)
        return {
            "product": product,
            "running": running,
            "setups": list(_read(app, setup_attribute)),
            "setup_name": setup_name,
            "latest_run": _public_analysis_run(run) if run is not None else None,
            "observed_at": _utc_now(),
        }

    def _hfss_analysis_cancel_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _analysis_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        if not _simulation_running(app):
            raise LiveBackendError("no AEDT simulation is currently running")
        setup_name = str(args.get("setup_name") or "")
        state = {"running": True, "setups": _setup_names(app), "setup_name": setup_name}
        digest = _digest(state)
        preview_id = "cancel-preview-" + _digest(state)[:24]
        self._previews[preview_id] = {
            "kind": "hfss_analysis_cancel",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "setup_name": setup_name,
            "clean_stop": bool(args.get("clean_stop", True)),
            "product": product,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            "product": product,
            "setup_name": setup_name,
            "clean_stop": bool(args.get("clean_stop", True)),
            "snapshot_digest": digest,
            "approval_required": True,
            "risk": "interrupts_solver_job",
        }

    def _hfss_analysis_cancel_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_analysis_cancel", target)
        app = self._app(target, preview["product"], preview["project_name"], preview["design_name"])
        current = {"running": _simulation_running(app), "setups": _setup_names(app), "setup_name": preview["setup_name"]}
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS analysis cancel preview")
        stop_result = str(app.stop_simulations(clean_stop=preview["clean_stop"]))
        key = (app.project_name, app.design_name, preview["setup_name"])
        if preview["setup_name"] and key in self._analysis_runs:
            self._analysis_runs[key].update({"state": "canceled", "canceled_at": _utc_now()})
        self._active_analysis_runs.pop((app.project_name, app.design_name), None)
        del self._previews[preview_id]
        return {
            "status": "cancel_requested",
            "preview_id": preview_id,
            "product": preview["product"],
            "setup_name": preview["setup_name"],
            "clean_stop": preview["clean_stop"],
            "backend_message": stop_result,
            "running": _simulation_running(app),
        }

    def _hfss_export_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _analysis_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        export_kind = _required(args, "export_kind").lower()
        if export_kind not in {"touchstone", "report_csv"}:
            raise LiveBackendError(f"unsupported HFSS export kind: {export_kind}")
        running = _simulation_running(app)
        active_key = (app.project_name, app.design_name)
        active_setup = self._active_analysis_runs.get(active_key)
        active_run = self._analysis_runs.get((app.project_name, app.design_name, active_setup or ""))
        if active_run is not None:
            _refresh_analysis_run(active_run, running)
            if active_run.get("state") not in {"submitted", "running"}:
                self._active_analysis_runs.pop(active_key, None)
        if running or (active_run is not None and active_run.get("state") in {"submitted", "running"}):
            raise LiveBackendError("cannot export while an AEDT simulation is running or pending")
        setup_name = str(args.get("setup_name") or "").strip()
        sweep_name = str(args.get("sweep_name") or "").strip()
        report_name = str(args.get("report_name") or "").strip()
        if export_kind == "touchstone":
            if not setup_name or setup_name not in _setup_names(app):
                raise LiveBackendError("touchstone export requires an existing setup_name")
        elif not report_name or report_name not in _report_names(app):
            raise LiveBackendError("report_csv export requires an existing report_name")
        artifact_name = str(args.get("artifact_name") or report_name or setup_name).strip()
        if not _SAFE_ARTIFACT_NAME.fullmatch(artifact_name):
            raise LiveBackendError("artifact_name may contain only letters, numbers, space, dot, underscore, and hyphen")
        state = {
            "setups": _setup_names(app),
            "reports": _report_names(app),
            "ports": _port_names(app),
            "running": False,
            "product": product,
            "setup_name": setup_name,
            "sweep_name": sweep_name,
            "report_name": report_name,
        }
        digest = _digest(state)
        spec = {
            "product": product,
            "export_kind": export_kind,
            "setup_name": setup_name,
            "sweep_name": sweep_name,
            "report_name": report_name,
            "artifact_name": artifact_name,
        }
        preview_id = "export-preview-" + _digest(spec | {"state": digest})[:24]
        self._previews[preview_id] = {
            "kind": "hfss_export",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "snapshot_digest": digest,
            "approval_required": True,
            "export_root": str(self._export_root),
            "ports": list(state["ports"]),
            "port_order_source": _port_order_source(app),
            "path_policy": "server_managed_directory_only",
            "project_unchanged": True,
        }

    def _hfss_export_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_export", target)
        spec = preview["spec"]
        app = self._app(target, spec["product"], preview["project_name"], preview["design_name"])
        current = {
            "setups": _setup_names(app),
            "reports": _report_names(app),
            "ports": _port_names(app),
            "running": _simulation_running(app),
            "product": spec["product"],
            "setup_name": spec["setup_name"],
            "sweep_name": spec["sweep_name"],
            "report_name": spec["report_name"],
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS export preview")
        output_dir = (self._export_root / _safe_component(app.project_name) / _safe_component(app.design_name) / preview_id).resolve()
        _require_within(output_dir, self._export_root)
        output_dir.mkdir(parents=True, exist_ok=False)
        try:
            if spec["export_kind"] == "touchstone":
                port_count = max(1, len(current["ports"]))
                output_path = output_dir / f"{spec['artifact_name']}.s{port_count}p"
                exported = app.export_touchstone(
                    setup=spec["setup_name"],
                    sweep=spec["sweep_name"] or None,
                    output_file=str(output_path),
                )
                if not exported:
                    raise LiveBackendError("AEDT failed to export Touchstone data")
                artifact_path = Path(str(exported)) if isinstance(exported, str) else output_path
            else:
                exported = app.post.export_report_to_file(str(output_dir), spec["report_name"], "csv")
                if not exported:
                    raise LiveBackendError("AEDT failed to export report CSV data")
                artifact_path = Path(str(exported))
            artifact_path = artifact_path.resolve()
            _require_within(artifact_path, output_dir)
            if not artifact_path.is_file():
                raise LiveBackendError("AEDT reported export success but no artifact was created")
            artifact = {
                "path": str(artifact_path),
                "sha256": _file_sha256(artifact_path),
                "bytes": artifact_path.stat().st_size,
            }
            manifest = {
                "schema_version": 1,
                "exported_at": _utc_now(),
                "target": target.to_dict(),
                "project_name": app.project_name,
                "design_name": app.design_name,
                "snapshot_digest": preview["digest"],
                "ports": list(current["ports"]),
                "port_order_source": _port_order_source(app),
                "spec": spec,
                "artifact": artifact,
            }
            manifest_path = output_dir / f"{artifact_path.name}.evidence.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            _remove_empty_or_partial_export(output_dir)
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "product": spec["product"],
            "artifact": artifact,
            "manifest_path": str(manifest_path),
            "project_unchanged": True,
            "project_saved": False,
        }

    def _layout_paths_list(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        selector = dict(args.get("selector") or {})
        nets = {str(value) for value in selector.get("nets") or []}
        layers = {str(value) for value in selector.get("layers") or []}
        names = {str(value) for value in selector.get("names") or []}
        target_width = str(selector.get("target_width") or "")
        paths = []
        for name in app.modeler.line_names:
            line = app.modeler.lines[name]
            record = {
                "name": str(name),
                "net": str(line.net_name),
                "layer": str(line.placement_layer),
                "width_expression": str(line.width),
            }
            if nets and record["net"] not in nets:
                continue
            if layers and record["layer"] not in layers:
                continue
            if names and record["name"] not in names:
                continue
            if target_width and _normalized_expression(record["width_expression"]) != _normalized_expression(target_width):
                continue
            paths.append(record)
        return {"project_name": app.project_name, "design_name": app.design_name, "count": len(paths), "paths": paths}

    def _layout_routing_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        inventory = self._layout_paths_list(target, args)
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        variables = _variable_records(app)
        paths = inventory["paths"]
        return {
            **inventory,
            "path_count": inventory["count"],
            "nets": sorted({item["net"] for item in paths}),
            "layers": sorted({item["layer"] for item in paths}),
            "width_expressions": sorted({item["width_expression"] for item in paths}),
            "variables": variables,
            "variable_count": len(variables),
            "design_unchanged": True,
        }

    def _layout_technology_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(
            target,
            "layout",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        max_items = args.get("max_items", 500)
        if type(max_items) is not int or not 1 <= max_items <= 2_000:
            raise LiveBackendError("max_items must be an integer between 1 and 2000")
        include_padstack_layers = args.get("include_padstack_layers", False)
        if type(include_padstack_layers) is not bool:
            raise LiveBackendError("include_padstack_layers must be a boolean")

        unavailable = []
        stackup, stackup_error = _layout_stackup_records(app, max_items=max_items)
        if stackup_error:
            unavailable.append({"section": "stackup", "reason": stackup_error})
        padstacks, padstack_error = _layout_padstack_records(
            app,
            max_items=max_items,
            include_layers=include_padstack_layers,
        )
        if padstack_error:
            unavailable.append({"section": "padstacks", "reason": padstack_error})
        differential_pairs, differential_error = _layout_differential_pair_records(
            app,
            max_items=max_items,
        )
        if differential_error:
            unavailable.append(
                {"section": "differential_pairs", "reason": differential_error}
            )
        ports = _port_names(app)
        bounded_ports = ports[:max_items]
        if len(ports) > max_items:
            unavailable.append(
                {"section": "ports", "reason": "truncated_by_max_items"}
            )
        technology = {
            "stackup": stackup,
            "padstacks": padstacks,
            "ports": bounded_ports,
            "port_order_source": _port_order_source(app),
            "differential_pairs": differential_pairs,
        }
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            **technology,
            "counts": {
                "stackup_layers": len(stackup),
                "padstacks": len(padstacks),
                "ports": len(bounded_ports),
                "differential_pairs": len(differential_pairs),
            },
            "max_items": max_items,
            "include_padstack_layers": include_padstack_layers,
            "unavailable_sections": unavailable,
            "snapshot_digest": _digest(technology),
            "design_unchanged": True,
        }

    def _layout_connectivity_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(
            target,
            "layout",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        max_items = args.get("max_items", 500)
        if type(max_items) is not int or not 1 <= max_items <= 2_000:
            raise LiveBackendError("max_items must be an integer between 1 and 2000")
        include_geometry_names = args.get("include_geometry_names", False)
        if type(include_geometry_names) is not bool:
            raise LiveBackendError("include_geometry_names must be a boolean")
        selector = dict(args.get("selector") or {})
        requested_nets = _layout_selector_names(selector, "nets")
        requested_components = _layout_selector_names(selector, "components")
        unsupported_selector = sorted(set(selector).difference({"nets", "components"}))
        if unsupported_selector:
            raise LiveBackendError(f"unsupported layout connectivity selector: {unsupported_selector[0]}")

        unavailable = []
        collections: dict[str, dict[str, Any]] = {}
        for section, attribute in (
            ("nets", "nets"),
            ("components", "components"),
            ("pins", "pins"),
            ("vias", "vias"),
        ):
            try:
                collections[section] = {
                    str(name): value
                    for name, value in dict(getattr(app.modeler, attribute) or {}).items()
                }
            except Exception as exc:
                collections[section] = {}
                unavailable.append(
                    {
                        "section": section,
                        "reason": f"{type(exc).__name__}: {attribute} API unavailable",
                    }
                )

        net_names = {str(item) for item in collections["nets"]}
        component_names = {str(item) for item in collections["components"]}
        if requested_nets and not net_names:
            raise LiveBackendError("net selector cannot be verified because the net inventory is unavailable")
        if requested_components and not component_names:
            raise LiveBackendError(
                "component selector cannot be verified because the component inventory is unavailable"
            )
        missing_nets = sorted(requested_nets.difference(net_names))
        if missing_nets:
            raise LiveBackendError(f"unknown layout net: {missing_nets[0]}")
        missing_components = sorted(requested_components.difference(component_names))
        if missing_components:
            raise LiveBackendError(f"unknown layout component: {missing_components[0]}")

        pin_records = [
            _layout_terminal_record("pin", str(name), pin)
            for name, pin in sorted(collections["pins"].items(), key=lambda item: str(item[0]))
        ]
        if requested_components:
            pin_records = [item for item in pin_records if item["component_name"] in requested_components]
        if requested_nets:
            pin_records = [item for item in pin_records if item["net_name"] in requested_nets]

        if requested_nets:
            selected_net_names = set(requested_nets)
        elif requested_components:
            selected_net_names = {item["net_name"] for item in pin_records if item["net_name"]}
        else:
            selected_net_names = set(net_names)

        if requested_components and requested_nets:
            selected_component_names = {
                item["component_name"] for item in pin_records if item["component_name"]
            }
        elif requested_components:
            selected_component_names = set(requested_components)
        elif requested_nets:
            selected_component_names = {
                item["component_name"] for item in pin_records if item["component_name"]
            }
        else:
            selected_component_names = set(component_names)

        unresolved_components = sorted(selected_component_names.difference(component_names))
        if unresolved_components:
            unavailable.append(
                {
                    "section": "pin_component_references",
                    "reason": "pins reference components absent from the component inventory",
                    "names": unresolved_components[:max_items],
                }
            )
            selected_component_names.intersection_update(component_names)
        unresolved_nets = sorted(selected_net_names.difference(net_names))
        if unresolved_nets:
            unavailable.append(
                {
                    "section": "pin_net_references",
                    "reason": "pins reference nets absent from the net inventory",
                    "names": unresolved_nets[:max_items],
                }
            )

        via_records = [
            _layout_terminal_record("via", str(name), via)
            for name, via in sorted(collections["vias"].items(), key=lambda item: str(item[0]))
        ]
        if requested_nets or requested_components:
            via_records = [item for item in via_records if item["net_name"] in selected_net_names]

        component_records = [
            _layout_connectivity_component_record(name, collections["components"][name])
            for name in sorted(selected_component_names)
        ]
        net_classes, class_errors = _layout_net_classes(app)
        unavailable.extend(class_errors)
        geometry_name_budget = max_items
        net_records = []
        for name in sorted(selected_net_names):
            net_pins = [item for item in pin_records if item["net_name"] == name]
            net_vias = [item for item in via_records if item["net_name"] == name]
            geometry_names: list[str] = []
            geometry_count: int | None = None
            geometry_status = "not_requested"
            if include_geometry_names:
                net = collections["nets"].get(name)
                try:
                    all_geometry_names = sorted(
                        str(item) for item in list(getattr(net, "geometry_names") or [])
                    )
                    geometry_count = len(all_geometry_names)
                    geometry_names = all_geometry_names[:geometry_name_budget]
                    geometry_name_budget -= len(geometry_names)
                    geometry_status = (
                        "complete" if len(geometry_names) == len(all_geometry_names) else "truncated"
                    )
                except Exception as exc:
                    geometry_status = "unavailable"
                    unavailable.append(
                        {
                            "section": f"net_geometry:{name}",
                            "reason": f"{type(exc).__name__}: geometry_names API unavailable",
                        }
                    )
            net_records.append(
                {
                    "name": name,
                    "class": net_classes.get(name, "unknown"),
                    "component_count": len(
                        {item["component_name"] for item in net_pins if item["component_name"]}
                    ),
                    "pin_count": len(net_pins),
                    "via_count": len(net_vias),
                    "geometry_count": geometry_count,
                    "geometry_names": geometry_names,
                    "geometry_status": geometry_status,
                }
            )

        full_counts = {
            "nets": len(net_records),
            "components": len(component_records),
            "pins": len(pin_records),
            "vias": len(via_records),
        }
        truncated_sections = [
            section for section, count in full_counts.items() if count > max_items
        ]
        bounded = {
            "nets": net_records[:max_items],
            "components": component_records[:max_items],
            "pins": pin_records[:max_items],
            "vias": via_records[:max_items],
        }
        snapshot = {
            **bounded,
            "selector": {
                "nets": sorted(requested_nets),
                "components": sorted(requested_components),
            },
            "truncated_sections": truncated_sections,
        }
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "model_units": _safe_json_attribute(app.modeler, "model_units"),
            **bounded,
            "counts": full_counts,
            "returned_counts": {name: len(records) for name, records in bounded.items()},
            "selector": snapshot["selector"],
            "max_items": max_items,
            "include_geometry_names": include_geometry_names,
            "truncated_sections": truncated_sections,
            "unavailable_sections": unavailable,
            "snapshot_digest": _digest(snapshot),
            "design_unchanged": True,
        }

    def _layout_port_candidate_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(
            target,
            "layout",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        signal_nets = _unique_nonempty_names(args.get("signal_nets"), "signal_nets")
        if not signal_nets:
            raise LiveBackendError("signal_nets must contain at least one exact net name")
        reference_nets = _unique_nonempty_names(args.get("reference_nets"), "reference_nets")
        overlap = sorted(
            set(item.casefold() for item in signal_nets).intersection(
                item.casefold() for item in reference_nets
            )
        )
        if overlap:
            raise LiveBackendError(f"signal_nets and reference_nets overlap: {overlap[0]}")
        max_candidates = args.get("max_candidates", 100)
        if type(max_candidates) is not int or not 1 <= max_candidates <= 500:
            raise LiveBackendError("max_candidates must be an integer between 1 and 500")

        try:
            nets = {str(name) for name in dict(app.modeler.nets or {})}
        except Exception as exc:
            raise LiveBackendError("layout net inventory is unavailable") from exc
        missing = sorted(set(signal_nets + reference_nets).difference(nets))
        if missing:
            raise LiveBackendError(f"unknown layout net: {missing[0]}")
        components, unavailable = _layout_live_component_connections(
            app,
            relevant_nets=set(signal_nets + reference_nets),
        )
        from aedt_agent.layout.ports import score_layout_port_candidates

        report = score_layout_port_candidates(components, signal_nets, reference_nets)
        all_candidates = list(report.get("candidates") or [])
        candidates = all_candidates[:max_candidates]
        candidate_names = {
            str(item.get("name") or "") for item in candidates
        }
        recommended = [
            item
            for item in list(report.get("recommended_endpoints") or [])
            if str(item.get("name") or "") in candidate_names
        ]
        snapshot = {
            "signal_nets": signal_nets,
            "reference_nets": reference_nets,
            "candidates": candidates,
            "recommended_endpoints": recommended,
        }
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "model_units": _safe_json_attribute(app.modeler, "model_units"),
            "status": "ready" if len(recommended) >= 2 else "needs_user_hint",
            "signal_nets": signal_nets,
            "reference_nets": reference_nets,
            "component_count": len(components),
            "candidate_count": len(all_candidates),
            "returned_candidate_count": len(candidates),
            "recommended_endpoints": recommended,
            "candidates": candidates,
            "truncated": len(all_candidates) > max_candidates,
            "unavailable_components": unavailable[:max_candidates],
            "snapshot_digest": _digest(snapshot),
            "design_unchanged": True,
        }

    def _layout_component_ports_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "layout",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        component_name = _required(args, "component_name")
        signal_nets = _unique_nonempty_names(args.get("signal_nets"), "signal_nets")
        if not signal_nets:
            raise LiveBackendError("signal_nets must contain at least one exact net name")
        allow_multiple_pins_per_net = args.get("allow_multiple_pins_per_net", False)
        if type(allow_multiple_pins_per_net) is not bool:
            raise LiveBackendError("allow_multiple_pins_per_net must be a boolean")
        max_new_ports = args.get("max_new_ports", 16)
        if type(max_new_ports) is not int or not 1 <= max_new_ports <= 64:
            raise LiveBackendError("max_new_ports must be an integer between 1 and 64")
        if _simulation_running(app):
            raise LiveBackendError("cannot create layout ports while an AEDT simulation is running or pending")
        try:
            components = {str(name): value for name, value in dict(app.modeler.components or {}).items()}
            nets = {str(name) for name in dict(app.modeler.nets or {})}
        except Exception as exc:
            raise LiveBackendError("layout component or net inventory is unavailable") from exc
        if component_name not in components:
            raise LiveBackendError(f"unknown layout component: {component_name}")
        missing_nets = sorted(set(signal_nets).difference(nets))
        if missing_nets:
            raise LiveBackendError(f"unknown layout net: {missing_nets[0]}")
        component = components[component_name]
        try:
            pins = {
                str(name): pin
                for name, pin in dict(getattr(component, "pins") or {}).items()
            }
        except Exception as exc:
            raise LiveBackendError(f"component pin inventory is unavailable: {component_name}") from exc
        matching_pins = [
            _layout_terminal_record("pin", name, pin)
            for name, pin in sorted(pins.items())
            if str(_safe_attribute(pin, "net_name") or "") in signal_nets
        ]
        pins_by_net = {
            net: [item for item in matching_pins if item["net_name"] == net]
            for net in signal_nets
        }
        missing_component_nets = [net for net, records in pins_by_net.items() if not records]
        if missing_component_nets:
            raise LiveBackendError(
                f"component {component_name} has no pin on requested net: {missing_component_nets[0]}"
            )
        multiple = [net for net, records in pins_by_net.items() if len(records) > 1]
        if multiple and not allow_multiple_pins_per_net:
            raise LiveBackendError(
                f"component {component_name} has multiple pins on net {multiple[0]}; "
                "set allow_multiple_pins_per_net=true only after reviewing the pin list"
            )
        expected_port_count = len(matching_pins)
        if expected_port_count > max_new_ports:
            raise LiveBackendError(
                f"expected port count {expected_port_count} exceeds max_new_ports {max_new_ports}"
            )
        before_ports = _port_names(app)
        state = {
            "component_name": component_name,
            "signal_nets": signal_nets,
            "matching_pins": matching_pins,
            "before_ports": before_ports,
        }
        snapshot_digest = _digest(state)
        spec = {
            "component_name": component_name,
            "signal_nets": signal_nets,
            "allow_multiple_pins_per_net": allow_multiple_pins_per_net,
            "max_new_ports": max_new_ports,
            "expected_port_count": expected_port_count,
        }
        preview_id = "layout-port-preview-" + _digest({**spec, "snapshot": snapshot_digest})[:24]
        self._previews[preview_id] = {
            "kind": "layout_component_ports_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": snapshot_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "matching_pins": matching_pins,
            "before_ports": before_ports,
            "snapshot_digest": snapshot_digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _layout_component_ports_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "layout_component_ports_create", target)
        app = self._app(target, "layout", preview["project_name"], preview["design_name"])
        spec = dict(preview["spec"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create layout ports while an AEDT simulation is running or pending")
        current_preview = self._layout_component_ports_snapshot(app, spec)
        if _digest(current_preview) != preview["digest"]:
            raise LiveBackendError("stale layout component port preview")
        before_ports = list(preview["state"]["before_ports"])
        created_ports: list[str] = []
        try:
            creator = getattr(app, "create_ports_on_component_by_nets", None)
            if not callable(creator):
                raise LiveBackendError("PyAEDT create_ports_on_component_by_nets is unavailable")
            creator(spec["component_name"], list(spec["signal_nets"]))
            after_ports = _port_names(app)
            missing_before_ports = [name for name in before_ports if name not in set(after_ports)]
            if missing_before_ports:
                raise LiveBackendError(
                    f"existing layout port changed during creation: {missing_before_ports[0]}"
                )
            created_ports = [name for name in after_ports if name not in set(before_ports)]
            if len(created_ports) != spec["expected_port_count"]:
                raise LiveBackendError(
                    f"layout port readback count mismatch: expected {spec['expected_port_count']}, "
                    f"created {len(created_ports)}"
                )
            if len(created_ports) > spec["max_new_ports"]:
                raise LiveBackendError("layout port creation exceeded the approved max_new_ports")
        except Exception as exc:
            rollback = self._rollback_layout_ports(app, before_ports)
            if rollback["remaining_new_ports"] or rollback["missing_before_ports"]:
                raise LiveBackendError(
                    f"layout port creation failed and rollback was incomplete: {rollback}"
                ) from exc
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "created_ports": created_ports,
            "created_port_count": len(created_ports),
            "ports": _port_names(app),
            "port_order_source": _port_order_source(app),
            "project_dirty": True,
            "project_saved": False,
        }

    def _layout_edge_port_candidate_inventory(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "layout",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        signal_nets = _unique_nonempty_names(args.get("signal_nets"), "signal_nets")
        if not signal_nets:
            raise LiveBackendError("signal_nets must contain at least one exact net name")
        local_cut_region = dict(args.get("local_cut_region") or {})
        side = str(args.get("side") or "").strip().casefold()
        if side not in {"left", "right", "top", "bottom"}:
            raise LiveBackendError("side must be left, right, top, or bottom")
        layer = str(args.get("layer") or "").strip()
        if not layer:
            raise LiveBackendError("layer must be an exact non-empty Layout layer name")
        max_candidates = args.get("max_candidates", 100)
        if type(max_candidates) is not int or not 1 <= max_candidates <= 500:
            raise LiveBackendError("max_candidates must be an integer between 1 and 500")
        from aedt_agent.layout.local_cut import parse_local_cut_region
        from aedt_agent.layout.ports import find_uniform_line_edge_candidates

        try:
            region = parse_local_cut_region(local_cut_region)
        except ValueError as exc:
            raise LiveBackendError(str(exc)) from exc
        try:
            nets = {str(name) for name in dict(app.modeler.nets or {})}
            lines = {str(name): value for name, value in dict(app.modeler.lines or {}).items()}
            layer_names = {
                str(getattr(item, "name", ""))
                for item in list(app.modeler.layers.stackup_layers or [])
            }
        except Exception as exc:
            raise LiveBackendError("layout net, line, or stackup layer inventory is unavailable") from exc
        missing_nets = sorted(set(signal_nets).difference(nets))
        if missing_nets:
            raise LiveBackendError(f"unknown layout net: {missing_nets[0]}")
        if layer not in layer_names:
            raise LiveBackendError(f"unknown layout layer: {layer}")
        model_units = str(_safe_attribute(app.modeler, "model_units") or "m")
        scale = _layout_length_factor_to_meters(model_units) / _layout_length_factor_to_meters(
            region["unit"]
        )
        primitives = []
        unavailable_lines = []
        for name in sorted(lines):
            line = lines[name]
            net_name = str(_safe_attribute(line, "net_name") or "")
            placement_layer = str(_safe_attribute(line, "placement_layer") or "")
            if net_name not in signal_nets or placement_layer != layer:
                continue
            try:
                edges = [
                    [
                        [float(edge[0][0]) * scale, float(edge[0][1]) * scale],
                        [float(edge[1][0]) * scale, float(edge[1][1]) * scale],
                    ]
                    for edge in list(getattr(line, "edges") or [])
                ]
            except Exception as exc:
                unavailable_lines.append(
                    {
                        "name": name,
                        "reason": f"{type(exc).__name__}: line edge inventory unavailable",
                    }
                )
                continue
            primitives.append(
                SimpleNamespace(
                    name=name,
                    net_name=net_name,
                    layer=placement_layer,
                    edges=edges,
                )
            )
        report = find_uniform_line_edge_candidates(
            primitives,
            signal_nets=signal_nets,
            local_cut_region=region,
            hint={"side": side, "layer": layer, "port_type": "edge"},
        )
        all_candidates = list(report.get("candidates") or [])
        candidates = all_candidates[:max_candidates]
        truncated = len(all_candidates) > max_candidates
        status = "incomplete" if truncated else str(report.get("status") or "needs_user_hint")
        snapshot = {
            "signal_nets": signal_nets,
            "local_cut_region": region,
            "side": side,
            "layer": layer,
            "candidates": candidates,
            "truncated": truncated,
        }
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "status": status,
            "signal_nets": signal_nets,
            "local_cut_region": region,
            "side": side,
            "layer": layer,
            "coordinate_unit": region["unit"],
            "source_model_units": model_units,
            "candidate_count": len(all_candidates),
            "returned_candidate_count": len(candidates),
            "candidates": candidates,
            "truncated": truncated,
            "unavailable_lines": unavailable_lines[:max_candidates],
            "snapshot_digest": _digest(snapshot),
            "design_unchanged": True,
        }

    def _layout_edge_ports_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(
            target,
            "layout",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        max_new_ports = args.get("max_new_ports", 16)
        if type(max_new_ports) is not int or not 1 <= max_new_ports <= 64:
            raise LiveBackendError("max_new_ports must be an integer between 1 and 64")
        if _simulation_running(app):
            raise LiveBackendError("cannot create layout edge ports while a simulation is running or pending")
        targets = _normalize_layout_edge_targets(app, args.get("edge_targets"), max_new_ports=max_new_ports)
        before_ports = _port_names(app)
        state = {"edge_targets": targets, "before_ports": before_ports}
        snapshot_digest = _digest(state)
        spec = {"edge_targets": targets, "max_new_ports": max_new_ports}
        preview_id = "layout-edge-port-preview-" + _digest(
            {"edge_targets": targets, "max_new_ports": max_new_ports, "snapshot": snapshot_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "layout_edge_ports_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": snapshot_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "expected_port_count": len(targets),
            "before_ports": before_ports,
            "snapshot_digest": snapshot_digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _layout_edge_ports_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "layout_edge_ports_create", target)
        app = self._app(target, "layout", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create layout edge ports while a simulation is running or pending")
        spec = dict(preview["spec"])
        current_targets = _normalize_layout_edge_targets(
            app,
            [item["request"] for item in spec["edge_targets"]],
            max_new_ports=spec["max_new_ports"],
        )
        current_state = {"edge_targets": current_targets, "before_ports": _port_names(app)}
        if _digest(current_state) != preview["digest"]:
            raise LiveBackendError("stale layout edge port preview")
        before_ports = list(preview["state"]["before_ports"])
        created = []
        try:
            creator = getattr(app, "create_edge_port", None)
            if not callable(creator):
                raise LiveBackendError("PyAEDT create_edge_port is unavailable")
            known_ports = list(before_ports)
            for target_record in current_targets:
                request = dict(target_record["request"])
                kwargs = {
                    "is_circuit_port": request["port_type"] == "circuit",
                    "is_wave_port": request["port_type"] == "wave",
                }
                if request.get("reference_primitive"):
                    kwargs["reference_primitive"] = request["reference_primitive"]
                    kwargs["reference_edge_number"] = request["reference_edge_number"]
                if request["port_type"] == "wave":
                    kwargs.update(
                        {
                            "wave_horizontal_extension": request["wave_horizontal_extension"],
                            "wave_vertical_extension": request["wave_vertical_extension"],
                            "wave_launcher": request["wave_launcher"],
                        }
                    )
                result = creator(
                    request["primitive_name"],
                    request["edge_number"],
                    **kwargs,
                )
                after_step = _port_names(app)
                new_names = [name for name in after_step if name not in set(known_ports)]
                if not result or len(new_names) != 1:
                    raise LiveBackendError(
                        f"layout edge port readback mismatch for {request['primitive_name']} "
                        f"edge {request['edge_number']}: created {len(new_names)}"
                    )
                created.append({"port_name": new_names[0], "target": target_record})
                known_ports = after_step
            after_ports = _port_names(app)
            missing_before = [name for name in before_ports if name not in set(after_ports)]
            if missing_before:
                raise LiveBackendError(f"existing layout port changed during creation: {missing_before[0]}")
            if len(created) != len(current_targets) or len(created) > spec["max_new_ports"]:
                raise LiveBackendError("layout edge port batch count exceeded the approved preview")
        except Exception as exc:
            rollback = self._rollback_layout_ports(app, before_ports)
            if rollback["remaining_new_ports"] or rollback["missing_before_ports"]:
                raise LiveBackendError(
                    f"layout edge port creation failed and rollback was incomplete: {rollback}"
                ) from exc
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "edge_targets": current_targets,
            "expected_port_count": len(current_targets),
            "created_port_count": len(created),
            "created_ports": created,
            "ports": _port_names(app),
            "port_order_source": _port_order_source(app),
            "project_dirty": True,
            "project_saved": False,
        }

    def _layout_component_ports_snapshot(self, app: Any, spec: dict[str, Any]) -> dict[str, Any]:
        try:
            components = {str(name): value for name, value in dict(app.modeler.components or {}).items()}
        except Exception as exc:
            raise LiveBackendError("layout component inventory is unavailable") from exc
        component = components.get(spec["component_name"])
        if component is None:
            raise LiveBackendError("stale layout component port preview")
        try:
            pins = {
                str(name): pin
                for name, pin in dict(getattr(component, "pins") or {}).items()
            }
        except Exception as exc:
            raise LiveBackendError("component pin inventory is unavailable") from exc
        matching_pins = [
            _layout_terminal_record("pin", name, pin)
            for name, pin in sorted(pins.items())
            if str(_safe_attribute(pin, "net_name") or "") in spec["signal_nets"]
        ]
        return {
            "component_name": spec["component_name"],
            "signal_nets": list(spec["signal_nets"]),
            "matching_pins": matching_pins,
            "before_ports": _port_names(app),
        }

    @staticmethod
    def _rollback_layout_ports(app: Any, before_ports: list[str]) -> dict[str, Any]:
        before = set(before_ports)
        candidates = [name for name in _port_names(app) if name not in before]
        failures = []
        for name in reversed(candidates):
            try:
                if not app.delete_port(name, remove_geometry=True):
                    failures.append(name)
            except Exception:
                failures.append(name)
        remaining = [name for name in _port_names(app) if name not in before]
        missing_before = [name for name in before_ports if name not in set(_port_names(app))]
        return {
            "attempted_ports": candidates,
            "delete_failures": failures,
            "remaining_new_ports": remaining,
            "missing_before_ports": missing_before,
        }

    def _layout_object_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        attributes = {
            "components": "components",
            "pins": "pins",
            "vias": "vias",
            "nets": "nets",
            "lines": "line_names",
            "polygons": "polygon_names",
            "rectangles": "rectangle_names",
            "circles": "circle_names",
            "polygon_voids": "polygon_voids_names",
            "line_voids": "line_voids_names",
            "rectangle_voids": "rectangle_void_names",
            "circle_voids": "circle_voids_names",
        }
        categories: dict[str, dict[str, Any]] = {}
        unavailable: list[str] = []
        for category, attribute in attributes.items():
            try:
                value = getattr(app.modeler, attribute)
                names = sorted(str(item) for item in (value.keys() if isinstance(value, dict) else value or []))
                categories[category] = {"count": len(names), "names": names}
            except Exception:
                categories[category] = {"count": 0, "names": [], "status": "unavailable"}
                unavailable.append(category)
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "categories": categories,
            "unavailable_categories": unavailable,
            "design_unchanged": True,
        }

    def _layout_object_property_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        object_kind = _layout_object_kind(args)
        collection = dict(getattr(app.modeler, _LAYOUT_OBJECT_COLLECTIONS[object_kind]) or {})
        requested = [str(item) for item in args.get("names") or []]
        missing = sorted(set(requested).difference(collection))
        if missing:
            raise LiveBackendError(f"unknown layout {object_kind}: {missing[0]}")
        names = requested or sorted(str(item) for item in collection)
        records = [
            _layout_object_record(object_kind, name, collection[name])
            for name in names
        ]
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "object_kind": object_kind,
            "count": len(records),
            "objects": records,
            "snapshot_digest": _digest(records),
            "design_unchanged": True,
        }

    def _layout_object_property_update_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        object_kind = _layout_object_kind(args)
        properties = dict(args.get("properties") or {})
        if not properties:
            raise LiveBackendError("at least one layout object property is required")
        unsupported = sorted(set(properties).difference(_LAYOUT_OBJECT_WRITABLE_PROPERTIES[object_kind]))
        if unsupported:
            raise LiveBackendError(f"unsupported {object_kind} property: {unsupported[0]}")
        _validate_layout_object_properties(object_kind, properties)
        collection = dict(getattr(app.modeler, _LAYOUT_OBJECT_COLLECTIONS[object_kind]) or {})
        names = [str(item) for item in args.get("names") or []]
        if not names:
            raise LiveBackendError("names must select at least one layout object")
        if len(names) != len(set(names)):
            raise LiveBackendError("names must not contain duplicates")
        missing = sorted(set(names).difference(collection))
        if missing:
            raise LiveBackendError(f"unknown layout {object_kind}: {missing[0]}")
        before = [
            {
                "name": name,
                "properties": {
                    prop: _json_value(getattr(collection[name], prop))
                    for prop in properties
                },
            }
            for name in names
        ]
        digest = _digest(before)
        spec = {"object_kind": object_kind, "names": names, "properties": properties}
        preview_id = "layout-object-preview-" + _digest({**spec, "snapshot": digest})[:24]
        self._previews[preview_id] = {
            "kind": "layout_object_property_update",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "before": before,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "before": before,
            "target_count": len(names),
            "snapshot_digest": digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _layout_object_property_update_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "layout_object_property_update", target)
        spec = preview["spec"]
        app = self._app(target, "layout", preview["project_name"], preview["design_name"])
        collection = dict(getattr(app.modeler, _LAYOUT_OBJECT_COLLECTIONS[spec["object_kind"]]) or {})
        if any(name not in collection for name in spec["names"]):
            raise LiveBackendError("stale layout object preview")
        current = [
            {
                "name": name,
                "properties": {
                    prop: _json_value(getattr(collection[name], prop))
                    for prop in spec["properties"]
                },
            }
            for name in spec["names"]
        ]
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale layout object preview")
        try:
            for name in spec["names"]:
                for prop, value in spec["properties"].items():
                    setattr(collection[name], prop, value)
            after = [
                {
                    "name": name,
                    "properties": {
                        prop: _json_value(getattr(collection[name], prop))
                        for prop in spec["properties"]
                    },
                }
                for name in spec["names"]
            ]
            for record in after:
                for prop, expected in spec["properties"].items():
                    if not _property_values_equal(record["properties"][prop], expected):
                        raise LiveBackendError(f"layout {spec['object_kind']} {prop} readback verification failed")
        except Exception:
            for record in preview["before"]:
                obj = collection.get(record["name"])
                if obj is None:
                    continue
                for prop, value in record["properties"].items():
                    try:
                        setattr(obj, prop, value)
                    except Exception:
                        pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "target_count": len(spec["names"]),
            "after": after,
            "project_dirty": True,
            "project_saved": False,
        }

    def _variable_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _variable_product(args)
        app = self._app(target, product, _required(args, "project_name"), _required(args, "design_name"))
        variables = _variable_records(app)
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "product": product,
            "count": len(variables),
            "variables": variables,
            "design_unchanged": True,
        }

    def _variable_upsert_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        product = _variable_product(args)
        project = _required(args, "project_name")
        design = _required(args, "design_name")
        variable_name = _variable_name(args)
        expression = _required(args, "expression")
        app = self._app(target, product, project, design)
        variables = dict(getattr(app.variable_manager, "variables", {}) or {})
        existed = variable_name in variables
        before_expression = _variable_expression(variables[variable_name]) if existed else None
        snapshot = {
            "product": product,
            "project_name": project,
            "design_name": design,
            "variable_name": variable_name,
            "existed": existed,
            "before_expression": before_expression,
        }
        digest = _digest(snapshot)
        preview_id = "live-preview-" + _digest({**snapshot, "expression": expression})[:24]
        self._previews[preview_id] = {
            "kind": "variable_upsert",
            "target": target,
            **snapshot,
            "expression": expression,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            "snapshot_digest": digest,
            "product": product,
            "project_name": project,
            "design_name": design,
            "variable_name": variable_name,
            "scope": "project" if variable_name.startswith("$") else "design",
            "existed": existed,
            "before_expression": before_expression,
            "after_expression": expression,
            "approval_required": True,
            "project_dirty": False,
        }

    def _variable_upsert_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "variable_upsert", target)
        app = self._app(
            target,
            preview["product"],
            preview["project_name"],
            preview["design_name"],
        )
        variables = dict(getattr(app.variable_manager, "variables", {}) or {})
        existed = preview["variable_name"] in variables
        before_expression = (
            _variable_expression(variables[preview["variable_name"]]) if existed else None
        )
        current_snapshot = {
            "product": preview["product"],
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "variable_name": preview["variable_name"],
            "existed": existed,
            "before_expression": before_expression,
        }
        if _digest(current_snapshot) != preview["digest"]:
            raise LiveBackendError("stale variable preview")
        try:
            updated = app.variable_manager.set_variable(
                preview["variable_name"],
                preview["expression"],
                sweep=True,
            )
            if updated is False:
                raise LiveBackendError("failed to set AEDT variable")
            after_variables = dict(getattr(app.variable_manager, "variables", {}) or {})
            if preview["variable_name"] not in after_variables:
                raise LiveBackendError("AEDT variable readback is missing")
            after_expression = _variable_expression(after_variables[preview["variable_name"]])
            if _normalized_expression(after_expression) != _normalized_expression(preview["expression"]):
                raise LiveBackendError("AEDT variable readback verification failed")
        except Exception:
            try:
                if preview["existed"]:
                    app.variable_manager.set_variable(
                        preview["variable_name"],
                        preview["before_expression"],
                        sweep=True,
                    )
                else:
                    app.variable_manager.delete_variable(preview["variable_name"])
            except Exception:
                pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "product": preview["product"],
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "variable_name": preview["variable_name"],
            "before_expression": preview["before_expression"],
            "after_expression": after_expression,
            "project_dirty": True,
            "project_saved": False,
        }

    def _layout_width_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        project = _required(args, "project_name")
        design = _required(args, "design_name")
        variable_name = _required(args, "variable_name")
        variable_value = _required(args, "variable_value")
        inventory = self._layout_paths_list(target, args)
        paths = inventory["paths"]
        target_width = str((args.get("selector") or {}).get("target_width") or "")
        if target_width:
            paths = [item for item in paths if item["width_expression"] == target_width]
        if not paths:
            raise LiveBackendError("selector matched no live layout paths")
        digest = _digest(paths)
        preview_id = "live-preview-" + _digest(
            {"project": project, "design": design, "paths": paths, "variable": variable_name, "value": variable_value}
        )[:24]
        self._previews[preview_id] = {
            "kind": "layout_width",
            "target": target,
            "project_name": project,
            "design_name": design,
            "paths": paths,
            "digest": digest,
            "variable_name": variable_name,
            "variable_value": variable_value,
        }
        return {
            "preview_id": preview_id,
            "target_count": len(paths),
            "targets": paths,
            "snapshot_digest": digest,
            "approval_required": True,
            "project_dirty": False,
        }

    def _layout_width_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        try:
            preview = self._previews[preview_id]
        except KeyError as exc:
            raise LiveBackendError("unknown live layout preview") from exc
        if preview.get("kind") != "layout_width" or preview["target"] != target:
            raise LiveBackendError("preview belongs to a different AEDT target")
        app = self._app(target, "layout", preview["project_name"], preview["design_name"])
        current = []
        for original in preview["paths"]:
            line = app.modeler.lines[original["name"]]
            current.append(
                {
                    "name": original["name"],
                    "net": str(line.net_name),
                    "layer": str(line.placement_layer),
                    "width_expression": str(line.width),
                }
            )
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale live layout preview")
        variable_name = preview["variable_name"]
        originals = {item["name"]: item["width_expression"] for item in current}
        variables = getattr(app.variable_manager, "variables", {})
        variable_existed = variable_name in variables
        try:
            if not app.variable_manager.set_variable(variable_name, preview["variable_value"], sweep=True):
                raise LiveBackendError("failed to create live design parameter")
            for name in originals:
                app.modeler.lines[name].width = variable_name
            after = [
                {"name": name, "width_expression": str(app.modeler.lines[name].width)} for name in originals
            ]
            if any(item["width_expression"] != variable_name for item in after):
                raise LiveBackendError("live width readback verification failed")
        except Exception:
            for name, expression in originals.items():
                try:
                    app.modeler.lines[name].width = expression
                except Exception:
                    pass
            if not variable_existed:
                try:
                    app.variable_manager.delete_variable(variable_name)
                except Exception:
                    pass
            raise
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "target_count": len(originals),
            "verified_count": len(after),
            "after": after,
            "project_dirty": True,
            "project_saved": False,
        }

    def _exploration_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        from aedt_agent.exploration.executor import build_preview
        from aedt_agent.exploration.validator import OperationValidator

        plan = args.get("plan")
        if not isinstance(plan, dict):
            raise LiveBackendError("exploration plan must be an object")
        validation = OperationValidator().validate(plan)
        active_previews = [item for item in self._previews.values() if item.get("kind") == "exploration"]
        if len(active_previews) >= 16:
            raise LiveBackendError("too many active exploratory previews; apply, expire, or release them first")
        target_spec = validation["plan"]["target"]
        product = target_spec["product"]
        if product == "desktop":
            if validation["risk"] != "read_only":
                raise LiveBackendError("Desktop exploration is read-only")
            app = self._desktop_for(target)
        else:
            kind = "hfss" if product == "hfss" else "layout"
            app = self._app(target, kind, target_spec["project_name"], target_spec["design_name"])
        identity = {
            "target": target.to_dict(),
            "version": self.version,
            "product": product,
            "project_name": target_spec["project_name"],
            "design_name": target_spec["design_name"],
        }
        public, state = build_preview(app, validation, target_identity=identity)
        state.update(
            {
                "target": target,
                "product": product,
                "project_name": target_spec["project_name"],
                "design_name": target_spec["design_name"],
                "expires_at_monotonic": time.monotonic() + 300,
            }
        )
        self._previews[public["preview_id"]] = state
        return {**public, "expires_in_seconds": 300}

    def _exploration_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        from aedt_agent.exploration.executor import apply_preview

        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "exploration", target)
        if time.monotonic() >= preview["expires_at_monotonic"]:
            del self._previews[preview_id]
            raise LiveBackendError("exploratory preview expired")
        if preview["product"] == "desktop":
            app = self._desktop_for(target)
        else:
            kind = "hfss" if preview["product"] == "hfss" else "layout"
            app = self._app(target, kind, preview["project_name"], preview["design_name"])
        result = apply_preview(app, preview)
        del self._previews[preview_id]
        return result

    def _preview(self, preview_id: str, kind: str, target: AedtTarget) -> dict[str, Any]:
        try:
            preview = self._previews[preview_id]
        except KeyError as exc:
            raise LiveBackendError(f"unknown {kind} preview") from exc
        if preview.get("kind") != kind or preview.get("target") != target:
            raise LiveBackendError("preview belongs to a different operation or AEDT target")
        return preview


def _required(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise LiveBackendError(f"{name} must be a non-empty string")
    return value.strip()


def _name(value: Any) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "GetName", None)
    return str(getter()) if callable(getter) else str(getattr(value, "name", value))


def _canonical_design_name(value: str) -> str:
    stripped = value.strip()
    match = re.fullmatch(r"\d+;(.*)", stripped)
    return match.group(1).strip() if match else stripped


def _design_display_name(desktop: Any, design: Any) -> str | None:
    if design is None:
        return None
    try:
        if design.GetDesignType() == "HFSS 3D Layout Design":
            display_name = design.GetDesignName()
            if isinstance(display_name, str) and display_name.strip():
                return display_name.strip()
    except Exception:
        pass
    try:
        display_name = _read(desktop, "active_design_name")
    except Exception:
        display_name = None
    if isinstance(display_name, str) and display_name.strip():
        return _canonical_design_name(display_name)
    raw_name = _name(design)
    return _canonical_design_name(raw_name) if raw_name else None


def _normalized_expression(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _variable_product(arguments: dict[str, Any]) -> str:
    product = str(arguments.get("product") or "").strip().casefold()
    if product not in {"hfss", "layout"}:
        raise LiveBackendError("product must be hfss or layout")
    return product


def _variable_name(arguments: dict[str, Any]) -> str:
    name = _required(arguments, "variable_name")
    if not re.fullmatch(r"\$?[A-Za-z_][A-Za-z0-9_]*", name):
        raise LiveBackendError("variable_name must be a valid AEDT identifier")
    return name


def _variable_expression(value: Any) -> str:
    expression = getattr(value, "expression", None)
    if expression is None:
        expression = getattr(value, "value", value)
    return str(expression)


def _variable_records(app: Any) -> list[dict[str, str]]:
    return [
        {
            "name": str(name),
            "expression": _variable_expression(value),
            "scope": "project" if str(name).startswith("$") else "design",
        }
        for name, value in sorted(dict(getattr(app.variable_manager, "variables", {}) or {}).items())
    ]


def _desktop_aedt_version(desktop: Any) -> str | None:
    """Read the connected Desktop release instead of trusting the requested value."""
    odesktop = getattr(desktop, "odesktop", None)
    getter = getattr(odesktop, "GetVersion", None)
    candidates: list[Any] = []
    if callable(getter):
        try:
            candidates.append(getter())
        except Exception:
            pass
    candidates.extend(
        [
            getattr(desktop, "aedt_version_id", None),
            getattr(desktop, "desktop_version", None),
        ]
    )
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            return extract_reported_aedt_version(candidate)
        except ValueError:
            continue
    return None


def _read(value: Any, name: str, *args: Any) -> Any:
    attribute = getattr(value, name)
    return attribute(*args) if callable(attribute) else attribute


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_HFSS_SETUP_PROPERTIES = {
    "Frequency",
    "MaximumPasses",
    "MinimumPasses",
    "MinimumConvergedPasses",
    "MaxDeltaS",
    "PercentRefinement",
    "BasisOrder",
}
_HFSS_SETUP_TYPES_WITH_FREQUENCY_SWEEP = {
    "HFSSDriven",
    "HFSSDrivenAuto",
}

_HFSS_BOUNDARY_OPTIONS = {
    "radiation": set(),
    "wave_port": {"modes", "impedance", "renormalize", "deembed", "integration_line"},
    "lumped_port": {"impedance", "renormalize", "deembed", "integration_line"},
}
_HFSS_FACE_SELECTORS = {
    "only_face",
    "all_faces",
    "x_min",
    "x_max",
    "y_min",
    "y_max",
    "z_min",
    "z_max",
}

_HFSS_PRIMITIVE_FIELDS = {
    "box": {"kind", "name", "origin", "size", "material", "solve_inside"},
    "rectangle": {
        "kind",
        "name",
        "orientation",
        "origin",
        "size",
    },
    "cylinder": {
        "kind",
        "name",
        "axis",
        "origin",
        "radius",
        "height",
        "num_sides",
        "material",
        "solve_inside",
    },
    "region": {"kind", "name", "padding", "padding_type"},
}
_HFSS_REGION_PADDING_TYPES = {
    "Percentage Offset",
    "Absolute Offset",
    "Transverse Percentage Offset",
}
_SAFE_AEDT_OBJECT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_. -]{0,127}")
_SAFE_AEDT_MATERIAL_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. +()-]{0,127}")
_SAFE_AEDT_EXPRESSION = re.compile(r"[A-Za-z0-9_$+\-*/^().,% \t]{1,128}")

_LAYOUT_OBJECT_COLLECTIONS = {
    "via": "vias",
    "component": "components",
}
_LAYOUT_OBJECT_READABLE_PROPERTIES = {
    "via": ("start_layer", "stop_layer", "holediam", "net_name", "location", "angle", "lock_position"),
    "component": ("part", "part_type", "enabled", "placement_layer", "location", "angle", "lock_position"),
}
_LAYOUT_OBJECT_WRITABLE_PROPERTIES = {
    "via": {"net_name", "location", "angle", "lock_position"},
    "component": {"enabled", "placement_layer", "location", "angle", "lock_position"},
}

_SAFE_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}")


def _normalize_hfss_setup_spec(raw_setup: Any) -> dict[str, Any]:
    if not isinstance(raw_setup, dict):
        raise LiveBackendError("setup must be an object")
    unsupported = sorted(set(raw_setup).difference({"name", "type", "properties"}))
    if unsupported:
        raise LiveBackendError(f"unsupported setup field: {unsupported[0]}")
    name = str(raw_setup.get("name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
        raise LiveBackendError("setup.name must be a safe AEDT name")
    setup_type = str(raw_setup.get("type") or "HFSSDriven").strip()
    if setup_type not in _HFSS_SETUP_TYPES_WITH_FREQUENCY_SWEEP:
        raise LiveBackendError(
            "setup.type must be HFSSDriven or HFSSDrivenAuto for a frequency sweep"
        )
    properties = _normalize_hfss_setup_properties(raw_setup.get("properties"))
    return {"name": name, "type": setup_type, "properties": properties}


def _normalize_hfss_setup_properties(raw_properties: Any) -> dict[str, Any]:
    if raw_properties is None:
        properties: dict[str, Any] = {}
    elif isinstance(raw_properties, dict):
        properties = dict(raw_properties)
    else:
        raise LiveBackendError("setup.properties must be an object")
    unsupported = sorted(set(properties).difference(_HFSS_SETUP_PROPERTIES))
    if unsupported:
        raise LiveBackendError(f"unsupported HFSS setup property: {unsupported[0]}")
    normalized: dict[str, Any] = {}
    if "Frequency" in properties:
        frequency = properties["Frequency"]
        if not isinstance(frequency, str) or not _SAFE_AEDT_EXPRESSION.fullmatch(
            frequency.strip()
        ):
            raise LiveBackendError(
                "setup.properties.Frequency must be a bounded AEDT expression with explicit units"
            )
        normalized["Frequency"] = frequency.strip()
    integer_bounds = {
        "MaximumPasses": (1, 1000),
        "MinimumPasses": (1, 1000),
        "MinimumConvergedPasses": (0, 1000),
    }
    for name, (minimum, maximum) in integer_bounds.items():
        if name in properties:
            normalized[name] = _bounded_integer(
                properties[name],
                f"setup.properties.{name}",
                minimum=minimum,
                maximum=maximum,
            )
    if (
        "MaximumPasses" in normalized
        and "MinimumPasses" in normalized
        and normalized["MinimumPasses"] > normalized["MaximumPasses"]
    ):
        raise LiveBackendError(
            "setup.properties.MinimumPasses must not exceed MaximumPasses"
        )
    numeric_bounds = {
        "MaxDeltaS": (0.0, 1.0),
        "PercentRefinement": (0.0, 100.0),
    }
    for name, (minimum, maximum) in numeric_bounds.items():
        if name not in properties:
            continue
        value = properties[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not minimum < float(value) <= maximum
        ):
            raise LiveBackendError(
                f"setup.properties.{name} must be greater than {minimum:g} and at most {maximum:g}"
            )
        normalized[name] = value
    if "BasisOrder" in properties:
        basis_order = properties["BasisOrder"]
        if type(basis_order) is not int or basis_order not in {-1, 1, 2}:
            raise LiveBackendError("setup.properties.BasisOrder must be -1, 1, or 2")
        normalized["BasisOrder"] = basis_order
    return normalized


def _normalize_hfss_sweep_spec(raw_sweep: Any) -> dict[str, Any]:
    if not isinstance(raw_sweep, dict):
        raise LiveBackendError("sweep must be an object")
    allowed = {
        "name",
        "range_type",
        "sweep_type",
        "unit",
        "start_frequency",
        "stop_frequency",
        "count",
        "step_size",
        "save_fields",
    }
    unsupported = sorted(set(raw_sweep).difference(allowed))
    if unsupported:
        raise LiveBackendError(f"unsupported sweep field: {unsupported[0]}")
    name = str(raw_sweep.get("name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
        raise LiveBackendError("sweep.name must be a safe AEDT name")
    range_type = str(raw_sweep.get("range_type") or "LinearCount")
    if range_type not in {"LinearCount", "LinearStep"}:
        raise LiveBackendError("sweep.range_type must be LinearCount or LinearStep")
    sweep_type = str(raw_sweep.get("sweep_type") or "Interpolating")
    if sweep_type not in {"Discrete", "Interpolating", "Fast"}:
        raise LiveBackendError(
            "sweep.sweep_type must be Discrete, Interpolating, or Fast"
        )
    unit = str(raw_sweep.get("unit") or "GHz")
    if unit not in {"Hz", "kHz", "MHz", "GHz", "THz"}:
        raise LiveBackendError("unsupported sweep frequency unit")
    start = _positive_finite(raw_sweep.get("start_frequency", 1.0), "sweep.start_frequency")
    stop = _positive_finite(raw_sweep.get("stop_frequency", 10.0), "sweep.stop_frequency")
    if stop <= start:
        raise LiveBackendError("sweep.stop_frequency must be greater than start_frequency")
    save_fields = raw_sweep.get("save_fields", True)
    if type(save_fields) is not bool:
        raise LiveBackendError("sweep.save_fields must be boolean")
    count = None
    step_size = None
    if range_type == "LinearCount":
        count = _bounded_integer(
            raw_sweep.get("count", 401),
            "sweep.count",
            minimum=2,
            maximum=100001,
        )
    else:
        step_size = _positive_finite(raw_sweep.get("step_size"), "sweep.step_size")
        span = stop - start
        if step_size >= span:
            raise LiveBackendError("sweep.step_size must be smaller than the sweep span")
        estimated_points = math.ceil(span / step_size) + 1
        if estimated_points > 100001:
            raise LiveBackendError("sweep LinearStep would exceed 100001 frequency points")
    return {
        "name": name,
        "range_type": range_type,
        "sweep_type": sweep_type,
        "unit": unit,
        "start_frequency": start,
        "stop_frequency": stop,
        "count": count,
        "step_size": step_size,
        "save_fields": save_fields,
    }


def _positive_finite(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise LiveBackendError(f"{field} must be a positive finite number")
    return float(value)


def _bounded_integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise LiveBackendError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _normalize_hfss_primitives(
    raw_primitives: Any,
    *,
    max_new_objects: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw_primitives, list) or not raw_primitives:
        raise LiveBackendError("primitives must be a non-empty list")
    if len(raw_primitives) > max_new_objects:
        raise LiveBackendError(
            f"primitive count {len(raw_primitives)} exceeds max_new_objects {max_new_objects}"
        )
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    names_casefold: set[str] = set()
    for index, raw in enumerate(raw_primitives):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"primitives[{index}] must be an object")
        kind = str(raw.get("kind") or "").strip().casefold()
        if kind not in _HFSS_PRIMITIVE_FIELDS:
            raise LiveBackendError(f"unsupported HFSS primitive kind: {kind}")
        unsupported = sorted(set(raw).difference(_HFSS_PRIMITIVE_FIELDS[kind]))
        if unsupported:
            raise LiveBackendError(f"unsupported {kind} field: {unsupported[0]}")
        name = str(raw.get("name") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
            raise LiveBackendError(
                f"primitives[{index}].name must be a safe AEDT object name up to 128 characters"
            )
        if name in names or name.casefold() in names_casefold:
            raise LiveBackendError(f"primitives must not contain duplicate object names: {name}")
        names.add(name)
        names_casefold.add(name.casefold())
        primitive: dict[str, Any] = {"kind": kind, "name": name}
        if kind == "region":
            primitive["padding"] = _hfss_padding(raw.get("padding", 10))
            padding_type = str(raw.get("padding_type") or "Absolute Offset").strip()
            if padding_type not in _HFSS_REGION_PADDING_TYPES:
                raise LiveBackendError(f"unsupported HFSS region padding_type: {padding_type}")
            primitive["padding_type"] = padding_type
            normalized.append(primitive)
            continue

        if kind != "rectangle":
            material = str(raw.get("material") or "vacuum").strip()
            if not _SAFE_AEDT_MATERIAL_NAME.fullmatch(material):
                raise LiveBackendError(
                    f"primitives[{index}].material must be a safe AEDT material name"
                )
            primitive["material"] = material
        primitive["origin"] = _hfss_vector(
            raw.get("origin"),
            f"primitives[{index}].origin",
            length=3,
            positive=False,
        )
        if "solve_inside" in raw:
            if type(raw["solve_inside"]) is not bool:
                raise LiveBackendError(f"primitives[{index}].solve_inside must be boolean")
            primitive["solve_inside"] = raw["solve_inside"]
        if kind == "box":
            primitive["size"] = _hfss_vector(
                raw.get("size"),
                f"primitives[{index}].size",
                length=3,
                positive=True,
            )
        elif kind == "rectangle":
            orientation = str(raw.get("orientation") or "").strip().upper()
            if orientation not in {"XY", "YZ", "XZ", "ZX"}:
                raise LiveBackendError(
                    f"primitives[{index}].orientation must be XY, YZ, XZ, or ZX"
                )
            primitive["orientation"] = orientation
            primitive["size"] = _hfss_vector(
                raw.get("size"),
                f"primitives[{index}].size",
                length=2,
                positive=True,
            )
        else:
            axis = str(raw.get("axis") or "").strip().upper()
            if axis not in {"X", "Y", "Z"}:
                raise LiveBackendError(f"primitives[{index}].axis must be X, Y, or Z")
            primitive["axis"] = axis
            primitive["radius"] = _hfss_dimension(
                raw.get("radius"),
                f"primitives[{index}].radius",
                positive=True,
            )
            primitive["height"] = _hfss_dimension(
                raw.get("height"),
                f"primitives[{index}].height",
                positive=True,
            )
            primitive["num_sides"] = _bounded_integer(
                raw.get("num_sides", 0),
                f"primitives[{index}].num_sides",
                minimum=0,
                maximum=256,
            )
        normalized.append(primitive)
    region_indexes = [index for index, item in enumerate(normalized) if item["kind"] == "region"]
    if len(region_indexes) > 1:
        raise LiveBackendError("a geometry batch can create at most one HFSS region")
    if region_indexes and region_indexes[0] != len(normalized) - 1:
        raise LiveBackendError("the HFSS region must be the last primitive in the batch")
    return normalized


def _normalize_hfss_geometry_boundaries(
    raw_boundaries: Any,
    *,
    new_object_names: list[str],
    reference_object_names: list[str],
    existing_boundary_names: list[str],
    max_new_boundaries: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw_boundaries, list) or not raw_boundaries:
        raise LiveBackendError("boundaries must be a non-empty list")
    if len(raw_boundaries) > max_new_boundaries:
        raise LiveBackendError(
            f"boundary count {len(raw_boundaries)} exceeds max_new_boundaries {max_new_boundaries}"
        )
    new_objects = {item.casefold(): item for item in new_object_names}
    reference_objects = {item.casefold(): item for item in reference_object_names}
    unavailable_names = {item.casefold(): item for item in existing_boundary_names}
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_boundaries):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"boundaries[{index}] must be an object")
        unsupported_fields = sorted(
            set(raw).difference(
                {
                    "boundary_kind",
                    "boundary_name",
                    "assignment_object",
                    "face_selector",
                    "references",
                    "options",
                }
            )
        )
        if unsupported_fields:
            raise LiveBackendError(
                f"unsupported boundaries[{index}] field: {unsupported_fields[0]}"
            )
        boundary_kind = str(raw.get("boundary_kind") or "").strip().casefold()
        if boundary_kind not in _HFSS_BOUNDARY_OPTIONS:
            raise LiveBackendError(f"unsupported HFSS boundary kind: {boundary_kind}")
        boundary_name = str(raw.get("boundary_name") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(boundary_name):
            raise LiveBackendError(
                f"boundaries[{index}].boundary_name must be a safe AEDT name"
            )
        folded_name = boundary_name.casefold()
        if folded_name in unavailable_names:
            raise LiveBackendError(
                f"HFSS boundary or port already exists: {unavailable_names[folded_name]}"
            )
        if folded_name in names:
            raise LiveBackendError(f"boundaries must not contain duplicate names: {boundary_name}")
        names.add(folded_name)
        assignment_object = str(raw.get("assignment_object") or "").strip()
        folded_assignment = assignment_object.casefold()
        if folded_assignment not in new_objects:
            raise LiveBackendError(
                f"boundaries[{index}].assignment_object must name an object in this atomic batch"
            )
        face_selector = str(raw.get("face_selector") or "").strip().casefold()
        if face_selector not in _HFSS_FACE_SELECTORS:
            raise LiveBackendError(
                f"boundaries[{index}].face_selector must be only_face, all_faces, or an axis extreme"
            )
        if boundary_kind != "radiation" and face_selector == "all_faces":
            raise LiveBackendError(f"{boundary_kind} requires a selector that resolves to one face")
        references = list(raw.get("references") or [])
        normalized_references: list[str] = []
        for reference in references:
            if not isinstance(reference, str) or not _SAFE_AEDT_OBJECT_NAME.fullmatch(
                reference.strip()
            ):
                raise LiveBackendError(
                    f"boundaries[{index}].references must contain safe AEDT object names"
                )
            folded_reference = reference.strip().casefold()
            if folded_reference not in reference_objects:
                raise LiveBackendError(
                    f"boundaries[{index}] references unknown HFSS object: {reference.strip()}"
                )
            normalized_references.append(reference_objects[folded_reference])
        options = _normalize_hfss_boundary_options(
            boundary_kind,
            raw.get("options"),
            f"boundaries[{index}].options",
        )
        unsupported_options = sorted(
            set(options).difference(_HFSS_BOUNDARY_OPTIONS[boundary_kind])
        )
        if unsupported_options:
            raise LiveBackendError(
                f"unsupported {boundary_kind} option: {unsupported_options[0]}"
            )
        normalized.append(
            {
                "boundary_kind": boundary_kind,
                "boundary_name": boundary_name,
                "assignment_object": new_objects[folded_assignment],
                "face_selector": face_selector,
                "references": normalized_references,
                "options": options,
            }
        )
    return normalized


def _normalize_hfss_boundary_options(
    boundary_kind: str,
    raw_options: Any,
    field: str,
) -> dict[str, Any]:
    if raw_options is None:
        options: dict[str, Any] = {}
    elif isinstance(raw_options, dict):
        options = dict(raw_options)
    else:
        raise LiveBackendError(f"{field} must be an object")
    unsupported = sorted(set(options).difference(_HFSS_BOUNDARY_OPTIONS[boundary_kind]))
    if unsupported:
        raise LiveBackendError(f"unsupported {boundary_kind} option: {unsupported[0]}")
    normalized: dict[str, Any] = {}
    if "modes" in options:
        normalized["modes"] = _bounded_integer(
            options["modes"],
            f"{field}.modes",
            minimum=1,
            maximum=16,
        )
    if "impedance" in options:
        impedance = options["impedance"]
        if (
            isinstance(impedance, bool)
            or not isinstance(impedance, (int, float))
            or not math.isfinite(float(impedance))
            or float(impedance) <= 0
        ):
            raise LiveBackendError(f"{field}.impedance must be a positive finite number")
        normalized["impedance"] = impedance
    if "renormalize" in options:
        if type(options["renormalize"]) is not bool:
            raise LiveBackendError(f"{field}.renormalize must be boolean")
        normalized["renormalize"] = options["renormalize"]
    if "deembed" in options:
        deembed = options["deembed"]
        if boundary_kind == "lumped_port":
            if type(deembed) is not bool:
                raise LiveBackendError(f"{field}.deembed must be boolean for lumped_port")
        elif (
            isinstance(deembed, bool)
            or not isinstance(deembed, (int, float))
            or not math.isfinite(float(deembed))
            or float(deembed) < 0
        ):
            raise LiveBackendError(
                f"{field}.deembed must be a non-negative finite number for wave_port"
            )
        normalized["deembed"] = deembed
    if "integration_line" in options:
        integration_line = options["integration_line"]
        if type(integration_line) is int and 0 <= integration_line <= 5:
            normalized["integration_line"] = integration_line
        elif (
            isinstance(integration_line, list)
            and len(integration_line) == 2
            and all(isinstance(point, list) and len(point) == 3 for point in integration_line)
            and all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
                for point in integration_line
                for value in point
            )
        ):
            normalized["integration_line"] = integration_line
        else:
            raise LiveBackendError(
                f"{field}.integration_line must be an axis integer 0..5 or two numeric 3D points"
            )
    return normalized


def _hfss_vector(
    value: Any,
    field: str,
    *,
    length: int,
    positive: bool,
) -> list[int | float | str]:
    if not isinstance(value, list) or len(value) != length:
        raise LiveBackendError(f"{field} must contain exactly {length} dimensions")
    return [
        _hfss_dimension(item, f"{field}[{index}]", positive=positive)
        for index, item in enumerate(value)
    ]


def _hfss_dimension(value: Any, field: str, *, positive: bool) -> int | float | str:
    if isinstance(value, bool):
        raise LiveBackendError(f"{field} must be numeric or a bounded AEDT expression")
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric) or (positive and numeric <= 0):
            qualifier = "positive and " if positive else ""
            raise LiveBackendError(f"{field} must be {qualifier}finite")
        return value
    if not isinstance(value, str):
        raise LiveBackendError(f"{field} must be numeric or a bounded AEDT expression")
    expression = value.strip()
    if not _SAFE_AEDT_EXPRESSION.fullmatch(expression):
        raise LiveBackendError(f"{field} contains unsupported AEDT expression characters")
    if positive and expression.startswith("-"):
        raise LiveBackendError(f"{field} must not be an explicitly negative expression")
    return expression


def _hfss_padding(value: Any) -> int | float | str | list[int | float | str]:
    if isinstance(value, list):
        if len(value) != 6:
            raise LiveBackendError("region padding list must contain six offsets")
        return [
            _hfss_dimension(item, f"region padding[{index}]", positive=True)
            for index, item in enumerate(value)
        ]
    return _hfss_dimension(value, "region padding", positive=True)


def _hfss_material_snapshot(app: Any, requested_name: str) -> dict[str, Any]:
    materials = _safe_attribute(app, "materials")
    material_keys = _safe_attribute(materials, "material_keys") if materials else None
    if not isinstance(material_keys, dict):
        raise LiveBackendError("HFSS project material catalog is unavailable")
    by_name = {str(key).casefold(): value for key, value in material_keys.items()}
    material = by_name.get(requested_name.casefold())
    if material is None:
        raise LiveBackendError(
            "material_name must already exist in the current HFSS project material catalog"
        )
    canonical_name = str(_safe_attribute(material, "name") or requested_name).strip()
    if not _SAFE_AEDT_MATERIAL_NAME.fullmatch(canonical_name):
        raise LiveBackendError("HFSS target material has an unsafe AEDT name")
    is_dielectric = _safe_attribute(material, "is_dielectric")
    if type(is_dielectric) is not bool:
        raise LiveBackendError("HFSS target material dielectric classification is unavailable")
    electrical_properties = {}
    for name in (
        "conductivity",
        "permittivity",
        "permeability",
        "dielectric_loss_tangent",
        "magnetic_loss_tangent",
    ):
        prop = _safe_attribute(material, name)
        electrical_properties[name] = {
            "type": _safe_json_attribute(prop, "type"),
            "value": _safe_json_attribute(prop, "value"),
            "unit": _safe_json_attribute(prop, "unit"),
        }
    raw_definition = None
    definition_manager = _safe_attribute(materials, "odefinition_manager")
    getter = getattr(definition_manager, "GetData", None)
    if callable(getter):
        try:
            raw_definition = _json_value(getter(canonical_name))
        except Exception:
            raw_definition = None
    definition_evidence = (
        raw_definition
        if raw_definition is not None
        else {"electrical_properties": electrical_properties, "is_dielectric": is_dielectric}
    )
    return {
        "canonical_name": canonical_name,
        "is_dielectric": is_dielectric,
        "electrical_properties": electrical_properties,
        "definition_digest": _digest(definition_evidence),
    }


def _hfss_material_target_snapshot(
    app: Any,
    object_names: list[str],
) -> list[dict[str, Any]]:
    available = [str(item) for item in list(getattr(app.modeler, "object_names", []) or [])]
    available_set = set(available)
    records = []
    for name in object_names:
        if name not in available_set:
            raise LiveBackendError(f"unknown exact HFSS object name: {name}")
        obj = app.modeler[name]
        object_id = _safe_json_attribute(obj, "id")
        if object_id is None:
            raise LiveBackendError(f"HFSS object ID is unavailable: {name}")
        volume = _safe_json_attribute(obj, "volume")
        try:
            is_solid = abs(float(volume)) > 1e-18
        except (TypeError, ValueError):
            is_solid = False
        if not is_solid:
            records.append(
                {
                    "name": name,
                    "object_id": object_id,
                    "material_name": "",
                    "solve_inside": None,
                    "color": None,
                    "transparency": None,
                    "bounding_box": _safe_json_attribute(obj, "bounding_box"),
                    "volume": volume,
                    "is_solid": False,
                }
            )
            continue
        material_name = str(
            _fresh_hfss_object_attribute(obj, "_material_name", "material_name") or ""
        ).strip('"')
        if not material_name:
            raise LiveBackendError(f"HFSS object material is unavailable: {name}")
        solve_inside = _fresh_hfss_object_attribute(
            obj,
            "_solve_inside",
            "solve_inside",
        )
        if type(solve_inside) is not bool:
            raise LiveBackendError(f"HFSS object solve_inside is unavailable: {name}")
        color = _fresh_hfss_object_attribute(obj, "_color", "color")
        transparency = _fresh_hfss_object_attribute(
            obj,
            "_transparency",
            "transparency",
        )
        records.append(
            {
                "name": name,
                "object_id": object_id,
                "material_name": material_name,
                "solve_inside": solve_inside,
                "color": _json_value(color),
                "transparency": _json_value(transparency),
                "bounding_box": _safe_json_attribute(obj, "bounding_box"),
                "volume": volume,
                "is_solid": is_solid,
            }
        )
    return records


def _fresh_hfss_object_attribute(obj: Any, cache_name: str, attribute: str) -> Any:
    try:
        setattr(obj, cache_name, None)
    except Exception:
        pass
    return _safe_attribute(obj, attribute)


def _rollback_hfss_material_assignment(
    app: Any,
    targets_before: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = []
    restored_names = []
    for before in targets_before:
        name = before["name"]
        try:
            restored = app.assign_material(name, before["material_name"])
            if restored is not True:
                raise LiveBackendError("material restore returned false")
            obj = app.modeler[name]
            obj.solve_inside = before["solve_inside"]
            if isinstance(before.get("color"), list) and len(before["color"]) == 3:
                obj.color = tuple(before["color"])
            if before.get("transparency") is not None:
                obj.transparency = before["transparency"]
            restored_names.append(name)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        after = _hfss_material_target_snapshot(
            app,
            [item["name"] for item in targets_before],
        )
    except Exception as exc:
        after = []
        readback_error = f"{type(exc).__name__}: {exc}"
    expected = {
        item["name"]: (
            item["object_id"],
            item["material_name"].casefold(),
            item["solve_inside"],
            item.get("color"),
            item.get("transparency"),
            item.get("bounding_box"),
            item.get("volume"),
        )
        for item in targets_before
    }
    actual = {
        item["name"]: (
            item["object_id"],
            item["material_name"].casefold(),
            item["solve_inside"],
            item.get("color"),
            item.get("transparency"),
            item.get("bounding_box"),
            item.get("volume"),
        )
        for item in after
    }
    mismatched = sorted(name for name, value in expected.items() if actual.get(name) != value)
    return {
        "complete": not errors and not readback_error and not mismatched,
        "restored_object_names": restored_names,
        "mismatched_object_names": mismatched,
        "errors": errors,
        "readback_error": readback_error,
    }


def _normalize_hfss_length_mesh_spec(args: dict[str, Any]) -> dict[str, Any]:
    mesh_name = str(args.get("mesh_name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(mesh_name):
        raise LiveBackendError("mesh_name must be a safe AEDT name")
    max_objects = _bounded_integer(
        args.get("max_objects", 16),
        "max_objects",
        minimum=1,
        maximum=32,
    )
    object_names = _normalize_explicit_names(
        args.get("object_names"),
        field="object_names",
        maximum=max_objects,
    )
    inside_selection = args.get("inside_selection", True)
    if type(inside_selection) is not bool:
        raise LiveBackendError("inside_selection must be boolean")
    maximum_length = args.get("maximum_length", "1mm")
    if maximum_length is not None:
        maximum_length = _normalize_hfss_mesh_length(maximum_length)
    maximum_elements = args.get("maximum_elements", 1000)
    if maximum_elements is not None:
        maximum_elements = _bounded_integer(
            maximum_elements,
            "maximum_elements",
            minimum=1,
            maximum=10_000_000,
        )
    if maximum_length is None and maximum_elements is None:
        raise LiveBackendError(
            "maximum_length and maximum_elements must not both be null"
        )
    return {
        "mesh_name": mesh_name,
        "object_names": object_names,
        "inside_selection": inside_selection,
        "maximum_length": maximum_length,
        "maximum_elements": maximum_elements,
        "max_objects": max_objects,
    }


def _normalize_hfss_mesh_length(value: Any) -> str:
    if not isinstance(value, str):
        raise LiveBackendError(
            "maximum_length must be a bounded AEDT expression with explicit units"
        )
    expression = value.strip()
    if not _SAFE_AEDT_EXPRESSION.fullmatch(expression):
        raise LiveBackendError("maximum_length contains unsupported AEDT expression characters")
    literal = re.fullmatch(
        r"([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:[eE]([+-]?\d+))?([A-Za-z]+)",
        expression,
    )
    if literal:
        numeric = float(literal.group(1)) * (10 ** int(literal.group(2) or 0))
        if not math.isfinite(numeric) or numeric <= 0:
            raise LiveBackendError("maximum_length literal must be positive")
    elif re.fullmatch(r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?", expression):
        raise LiveBackendError("maximum_length numeric literal must include explicit units")
    elif expression.startswith("-"):
        raise LiveBackendError("maximum_length must not be explicitly negative")
    return expression


def _hfss_mesh_operation_names(app: Any) -> list[str]:
    mesh = _safe_attribute(app, "mesh")
    if mesh is None:
        raise LiveBackendError("HFSS mesh API is unavailable")
    try:
        names = [str(item) for item in list(mesh.meshoperation_names or [])]
    except Exception as exc:
        raise LiveBackendError("HFSS mesh operation names are unavailable") from exc
    return sorted(names, key=str.casefold)


def _hfss_mesh_operation_snapshot(
    app: Any,
    operation_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    requested_names = operation_names or _hfss_mesh_operation_names(app)
    if not requested_names:
        return []
    try:
        mesh_oo = app.get_oo_object(app.odesign, "Mesh")
        records = []
        for name in requested_names:
            prop_names = list(app.get_oo_properties(mesh_oo, name) or [])
            if not prop_names:
                raise LiveBackendError("HFSS mesh OO properties are unavailable")
            props = {
                str(prop): app.get_oo_property_value(mesh_oo, name, str(prop))
                for prop in prop_names
            }
            records.append(_hfss_mesh_operation_record(name, props.get("Type"), props))
        return sorted(records, key=lambda item: item["name"].casefold())
    except Exception:
        pass

    mesh = _safe_attribute(app, "mesh")
    if mesh is None:
        raise LiveBackendError("HFSS mesh API is unavailable")
    try:
        setattr(mesh, "_meshoperations", None)
    except Exception:
        pass
    try:
        operations = list(mesh.meshoperations or [])
    except Exception as exc:
        raise LiveBackendError("HFSS mesh operation inventory is unavailable") from exc
    by_name = {}
    for operation in operations:
        name = str(getattr(operation, "name", "") or "").strip()
        if not name:
            raise LiveBackendError("HFSS mesh operation name is unavailable")
        props = dict(getattr(operation, "props", {}) or {})
        by_name[name] = _hfss_mesh_operation_record(
            name,
            getattr(operation, "type", "") or props.get("Type"),
            props,
        )
    missing = [name for name in requested_names if name not in by_name]
    if missing:
        raise LiveBackendError(f"HFSS mesh operation readback is missing: {missing[0]}")
    records = [by_name[name] for name in requested_names]
    return sorted(records, key=lambda item: item["name"].casefold())


def _hfss_mesh_operation_record(
    name: str,
    operation_type: Any,
    props: dict[str, Any],
) -> dict[str, Any]:
    region = str(props.get("Region") or "").strip()
    if "RefineInside" in props:
        inside_selection = _mesh_bool(props["RefineInside"], default=False)
    else:
        inside_selection = region.casefold().startswith("inside")
    return {
        "name": name,
        "type": str(operation_type or "").strip(),
        "object_names": _hfss_mesh_assignments(props),
        "inside_selection": inside_selection,
        "enabled": _mesh_bool(props.get("Enabled"), default=True),
        "restrict_length": _mesh_bool(
            props.get("RestrictLength", props.get("Restrict Length")),
            default=False,
        ),
        "maximum_length": str(
            props.get("MaxLength", props.get("Max Length", "")) or ""
        ),
        "restrict_elements": _mesh_bool(
            props.get("RestrictElem", props.get("Restrict Max Elems")),
            default=False,
        ),
        "maximum_elements": _optional_int(
            props.get("NumMaxElem", props.get("Max Elems"))
        ),
        "property_digest": _digest(_json_value(props)),
    }


def _mesh_bool(value: Any, *, default: bool) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return default


def _hfss_mesh_assignments(props: dict[str, Any]) -> list[str]:
    raw = props.get("Objects", props.get("Assignment", []))
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple)):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        values = []
    return values


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _verify_hfss_length_mesh_readback(
    spec: dict[str, Any],
    readback: dict[str, Any],
) -> None:
    normalized_type = readback["type"].casefold().replace(" ", "")
    if normalized_type != "lengthbased":
        raise LiveBackendError("HFSS mesh operation type readback is not Length Based")
    if readback["object_names"] != spec["object_names"]:
        raise LiveBackendError("HFSS length mesh object assignment readback failed")
    if readback["inside_selection"] is not spec["inside_selection"]:
        raise LiveBackendError("HFSS length mesh region readback failed")
    if readback["enabled"] is not True:
        raise LiveBackendError("HFSS length mesh operation is not enabled")
    expected_restrict_length = spec["maximum_length"] is not None
    if readback["restrict_length"] is not expected_restrict_length:
        raise LiveBackendError("HFSS length restriction readback failed")
    if expected_restrict_length and readback["maximum_length"] != spec["maximum_length"]:
        raise LiveBackendError("HFSS maximum length readback failed")
    expected_restrict_elements = spec["maximum_elements"] is not None
    if readback["restrict_elements"] is not expected_restrict_elements:
        raise LiveBackendError("HFSS maximum element restriction readback failed")
    if expected_restrict_elements and readback["maximum_elements"] != spec[
        "maximum_elements"
    ]:
        raise LiveBackendError("HFSS maximum element count readback failed")


def _rollback_hfss_mesh_operation(
    app: Any,
    created_name: str,
    *,
    before_operations: list[dict[str, Any]],
) -> dict[str, Any]:
    before_names = {item["name"] for item in before_operations}
    delete_error = ""
    try:
        current = _hfss_mesh_operation_snapshot(app)
        operation_names = {item["name"] for item in current}
        if created_name in operation_names and created_name not in before_names:
            mesh = app.mesh
            try:
                setattr(mesh, "_meshoperations", None)
            except Exception:
                pass
            operation = next(
                item for item in list(mesh.meshoperations or []) if item.name == created_name
            )
            deleted = operation.delete()
            if deleted is not True:
                raise LiveBackendError("mesh operation delete returned false")
    except Exception as exc:
        try:
            if created_name not in before_names:
                app.mesh.omeshmodule.DeleteOp([created_name])
                setattr(app.mesh, "_meshoperations", None)
        except Exception as fallback_exc:
            delete_error = (
                f"{type(exc).__name__}: {exc}; raw fallback failed: "
                f"{type(fallback_exc).__name__}: {fallback_exc}"
            )
    readback_error = ""
    try:
        after = _hfss_mesh_operation_snapshot(app)
    except Exception as exc:
        after = []
        readback_error = f"{type(exc).__name__}: {exc}"
    return {
        "complete": not delete_error and not readback_error and after == before_operations,
        "deleted_mesh_operation": created_name if after == before_operations else "",
        "remaining_mesh_operations": [item["name"] for item in after],
        "delete_error": delete_error,
        "readback_error": readback_error,
    }


def _normalize_hfss_infinite_sphere_spec(args: dict[str, Any]) -> dict[str, Any]:
    sphere_name = str(args.get("sphere_name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(sphere_name):
        raise LiveBackendError("sphere_name must be a safe AEDT name")
    definition_aliases = {
        "theta-phi": "Theta-Phi",
        "el over az": "El Over Az",
        "az over el": "Az Over El",
    }
    requested_definition = str(args.get("definition") or "Theta-Phi").strip().casefold()
    definition = definition_aliases.get(requested_definition)
    if definition is None:
        raise LiveBackendError("definition must be Theta-Phi, El Over Az, or Az Over El")
    units = str(args.get("units") or "deg").strip().casefold()
    if units not in {"deg", "rad"}:
        raise LiveBackendError("units must be deg or rad")
    angle_limit = 360_000.0 if units == "deg" else 2_000.0 * math.pi
    angles = {
        field: _bounded_float(
            args.get(field, default),
            field,
            minimum=-angle_limit,
            maximum=angle_limit,
        )
        for field, default in (
            ("angle1_start", 0.0),
            ("angle1_stop", 180.0),
            ("angle1_step", 10.0),
            ("angle2_start", 0.0),
            ("angle2_stop", 180.0),
            ("angle2_step", 10.0),
        )
    }
    for prefix in ("angle1", "angle2"):
        start = angles[f"{prefix}_start"]
        stop = angles[f"{prefix}_stop"]
        step = angles[f"{prefix}_step"]
        if stop <= start:
            raise LiveBackendError(f"{prefix}_stop must be greater than {prefix}_start")
        if step <= 0:
            raise LiveBackendError(f"{prefix}_step must be positive")
        if step > stop - start:
            raise LiveBackendError(f"{prefix}_step must not exceed the requested angle span")
    angle1_count = _inclusive_sample_count(
        angles["angle1_start"], angles["angle1_stop"], angles["angle1_step"]
    )
    angle2_count = _inclusive_sample_count(
        angles["angle2_start"], angles["angle2_stop"], angles["angle2_step"]
    )
    sample_count = angle1_count * angle2_count
    max_samples = _bounded_integer(
        args.get("max_samples", 200_000),
        "max_samples",
        minimum=4,
        maximum=1_000_000,
    )
    if sample_count > max_samples:
        raise LiveBackendError(
            f"far-field sample count {sample_count} exceeds max_samples {max_samples}"
        )
    polarization_aliases = {"linear": "Linear", "slant": "Slant"}
    polarization = polarization_aliases.get(
        str(args.get("polarization") or "Linear").strip().casefold()
    )
    if polarization is None:
        raise LiveBackendError("polarization must be Linear or Slant")
    polarization_angle = _bounded_float(
        args.get("polarization_angle", 45.0),
        "polarization_angle",
        minimum=-angle_limit,
        maximum=angle_limit,
    )
    angle1_axis, angle2_axis = _far_field_axis_names(definition)
    return {
        "sphere_name": sphere_name,
        "definition": definition,
        "angle1_axis": angle1_axis,
        "angle2_axis": angle2_axis,
        **angles,
        "units": units,
        "angle1_count": angle1_count,
        "angle2_count": angle2_count,
        "sample_count": sample_count,
        "max_samples": max_samples,
        "coordinate_system": "Global",
        "polarization": polarization,
        "polarization_angle": polarization_angle,
    }


def _bounded_float(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveBackendError(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < minimum or normalized > maximum:
        raise LiveBackendError(f"{field} must be between {minimum} and {maximum}")
    return normalized


def _inclusive_sample_count(start: float, stop: float, step: float) -> int:
    return int(math.floor((stop - start) / step + 1e-12)) + 1


def _far_field_axis_names(definition: str) -> tuple[str, str]:
    if definition == "Theta-Phi":
        return "Theta", "Phi"
    if definition == "El Over Az":
        return "Azimuth", "Elevation"
    return "Elevation", "Azimuth"


def _far_field_solution_forbidden(solution_type: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "", solution_type).casefold()
    return normalized in {"eigenmode", "characteristicmode"}


def _hfss_boundary_records(app: Any) -> list[dict[str, Any]]:
    records = []
    for item in list(getattr(app, "boundaries", []) or []):
        properties = dict(getattr(item, "properties", {}) or {})
        if not properties:
            properties = dict(getattr(item, "props", {}) or {})
        records.append(
            {
                "name": str(getattr(item, "name", item)).strip(),
                "type": str(getattr(item, "type", item.__class__.__name__)).strip(),
                "property_digest": _digest(_json_value(properties)) if properties else None,
            }
        )
    return sorted(records, key=lambda item: (item["name"].casefold(), item["type"].casefold()))


def _supports_radiated_fields(boundary_type: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "", boundary_type).casefold()
    return any(token in normalized for token in ("radiation", "pml", "hybrid"))


def _hfss_field_setup_names(app: Any) -> list[str]:
    try:
        names = [str(item).strip() for item in list(app.field_setup_names or [])]
    except Exception as exc:
        raise LiveBackendError("HFSS field setup names are unavailable") from exc
    if any(not item for item in names):
        raise LiveBackendError("HFSS field setup name is unavailable")
    return sorted(set(names), key=str.casefold)


def _hfss_field_setup_snapshot(
    app: Any,
    setup_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    requested_names = _hfss_field_setup_names(app) if setup_names is None else setup_names
    if not requested_names:
        return []
    try:
        setups = list(app.field_setups or [])
    except Exception as exc:
        raise LiveBackendError("HFSS field setup inventory is unavailable") from exc
    records: dict[str, dict[str, Any]] = {}
    for setup in setups:
        name = str(getattr(setup, "name", "") or "").strip()
        if not name:
            raise LiveBackendError("HFSS field setup name readback is unavailable")
        properties = dict(getattr(setup, "properties", {}) or {})
        if not properties:
            properties = dict(getattr(setup, "props", {}) or {})
        if not properties:
            raise LiveBackendError(f"HFSS field setup properties are unavailable: {name}")
        records[name] = _hfss_field_setup_record(
            name,
            getattr(setup, "type", ""),
            properties,
        )
    missing = [name for name in requested_names if name not in records]
    if missing:
        raise LiveBackendError(f"HFSS field setup readback is missing: {missing[0]}")
    return sorted(
        [records[name] for name in requested_names],
        key=lambda item: item["name"].casefold(),
    )


def _hfss_field_setup_record(
    name: str,
    setup_type: Any,
    properties: dict[str, Any],
) -> dict[str, Any]:
    readback_type = str(
        _field_property(properties, "Type") or setup_type or ""
    ).strip()
    normalized_type = re.sub(r"[\s_-]+", "", readback_type).casefold()
    record: dict[str, Any] = {
        "name": name,
        "type": readback_type,
        "kind": (
            "infinite_sphere"
            if "infinitesphere" in normalized_type or "farfieldsphere" in normalized_type
            else "other_field_setup"
        ),
        "property_digest": _digest(_json_value(properties)),
    }
    if record["kind"] != "infinite_sphere":
        return record
    definition = str(
        _field_property(properties, "CS Definition", "CSDefinition") or ""
    ).strip()
    if definition not in {"Theta-Phi", "El Over Az", "Az Over El"}:
        raise LiveBackendError(f"HFSS infinite sphere definition is unavailable: {name}")
    angle1_axis, angle2_axis = _far_field_axis_names(definition)
    record.update(
        {
            "definition": definition,
            "angle1_axis": angle1_axis,
            "angle2_axis": angle2_axis,
            "angle1_start": _field_axis_property(properties, angle1_axis, "start"),
            "angle1_stop": _field_axis_property(properties, angle1_axis, "stop"),
            "angle1_step": _field_axis_property(properties, angle1_axis, "step"),
            "angle2_start": _field_axis_property(properties, angle2_axis, "start"),
            "angle2_stop": _field_axis_property(properties, angle2_axis, "stop"),
            "angle2_step": _field_axis_property(properties, angle2_axis, "step"),
            "coordinate_system": str(
                _field_property(properties, "Coordinate System", "CoordSystem") or ""
            ).strip(),
            "polarization": str(
                _field_property(properties, "Polarization") or ""
            ).strip(),
            "polarization_angle": str(
                _field_property(properties, "Slant Angle", "SlantAngle") or ""
            ).strip(),
        }
    )
    return record


def _field_property(properties: dict[str, Any], *aliases: str) -> Any:
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(key).casefold()): value
        for key, value in properties.items()
    }
    for alias in aliases:
        key = re.sub(r"[^a-z0-9]", "", alias.casefold())
        if key in normalized:
            return normalized[key]
    return None


def _field_axis_property(properties: dict[str, Any], axis: str, position: str) -> str:
    aliases = (
        f"{position} {axis}",
        f"{axis} {position}",
        f"{axis}{position}",
        f"{position}{axis}",
    )
    value = _field_property(properties, *aliases)
    if value is None or not str(value).strip():
        raise LiveBackendError(f"HFSS far-field {axis} {position} readback is unavailable")
    return str(value).strip()


def _verify_hfss_infinite_sphere_readback(
    spec: dict[str, Any],
    readback: dict[str, Any],
) -> None:
    if readback.get("kind") != "infinite_sphere":
        raise LiveBackendError("HFSS field setup type readback is not Infinite Sphere")
    if readback.get("definition") != spec["definition"]:
        raise LiveBackendError("HFSS infinite sphere definition readback failed")
    if (
        readback.get("angle1_axis") != spec["angle1_axis"]
        or readback.get("angle2_axis") != spec["angle2_axis"]
    ):
        raise LiveBackendError("HFSS infinite sphere angle axis readback failed")
    for field in (
        "angle1_start",
        "angle1_stop",
        "angle1_step",
        "angle2_start",
        "angle2_stop",
        "angle2_step",
    ):
        if not _angle_readback_matches(readback.get(field), spec[field], spec["units"]):
            raise LiveBackendError(f"HFSS infinite sphere {field} readback failed")
    if str(readback.get("coordinate_system") or "").casefold() != "global":
        raise LiveBackendError("HFSS infinite sphere coordinate system readback failed")
    if readback.get("polarization") != spec["polarization"]:
        raise LiveBackendError("HFSS infinite sphere polarization readback failed")
    if spec["polarization"] == "Slant" and not _angle_readback_matches(
        readback.get("polarization_angle"),
        spec["polarization_angle"],
        spec["units"],
    ):
        raise LiveBackendError("HFSS infinite sphere polarization angle readback failed")


def _angle_readback_matches(actual: Any, expected: float, units: str) -> bool:
    match = re.fullmatch(
        r"([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)\s*([A-Za-z]+)",
        str(actual or "").strip(),
    )
    if match is None or match.group(2).casefold() != units.casefold():
        return False
    try:
        value = float(match.group(1))
    except ValueError:
        return False
    return math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-9)


def _rollback_hfss_field_setup(
    app: Any,
    created_name: str,
    *,
    before_setups: list[dict[str, Any]],
) -> dict[str, Any]:
    before_names = {item["name"] for item in before_setups}
    delete_error = ""
    try:
        current_names = set(_hfss_field_setup_names(app))
        if created_name in current_names and created_name not in before_names:
            setup = next(item for item in list(app.field_setups or []) if item.name == created_name)
            deleted = setup.delete()
            if deleted is not True:
                raise LiveBackendError("field setup delete returned false")
    except Exception as exc:
        try:
            if created_name not in before_names:
                app.oradfield.DeleteSetup([created_name])
        except Exception as fallback_exc:
            delete_error = (
                f"{type(exc).__name__}: {exc}; raw fallback failed: "
                f"{type(fallback_exc).__name__}: {fallback_exc}"
            )
    readback_error = ""
    try:
        after = _hfss_field_setup_snapshot(app)
    except Exception as exc:
        after = []
        readback_error = f"{type(exc).__name__}: {exc}"
    return {
        "complete": not delete_error and not readback_error and after == before_setups,
        "deleted_field_setup": created_name if after == before_setups else "",
        "remaining_field_setups": [item["name"] for item in after],
        "delete_error": delete_error,
        "readback_error": readback_error,
    }


_HFSS_SURFACE_BOUNDARY_TYPES = {
    "perfect_e": "Perfect E",
    "perfect_h": "Perfect H",
    "finite_conductivity": "Finite Conductivity",
    "impedance": "Impedance",
    "lumped_rlc": "Lumped RLC",
}


def _normalize_hfss_surface_boundary_spec(args: dict[str, Any]) -> dict[str, Any]:
    boundary_kind = str(args.get("boundary_kind") or "").strip().casefold()
    if boundary_kind not in _HFSS_SURFACE_BOUNDARY_TYPES:
        raise LiveBackendError(
            "boundary_kind must be perfect_e, perfect_h, finite_conductivity, impedance, or lumped_rlc"
        )
    boundary_name = str(args.get("boundary_name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(boundary_name):
        raise LiveBackendError("boundary_name must be a safe AEDT name")
    max_assignments = _bounded_integer(
        args.get("max_assignments", 16),
        "max_assignments",
        minimum=1,
        maximum=64,
    )
    raw_object_names = args.get("object_names") or []
    raw_face_ids = args.get("face_ids") or []
    if raw_object_names and raw_face_ids:
        raise LiveBackendError("use exactly one of object_names or face_ids")
    if not raw_object_names and not raw_face_ids:
        raise LiveBackendError("one of object_names or face_ids must be non-empty")
    object_names: list[str] = []
    face_ids: list[int] = []
    if raw_object_names:
        object_names = _normalize_explicit_names(
            raw_object_names,
            field="object_names",
            maximum=max_assignments,
        )
    else:
        if not isinstance(raw_face_ids, list) or len(raw_face_ids) > max_assignments:
            raise LiveBackendError(
                f"face_ids must contain at most {max_assignments} explicit face IDs"
            )
        for item in raw_face_ids:
            if type(item) is not int or item <= 0:
                raise LiveBackendError("face_ids must contain positive integer face IDs")
            if item in face_ids:
                raise LiveBackendError(f"face_ids must not contain duplicates: {item}")
            face_ids.append(item)
    if boundary_kind in {"impedance", "lumped_rlc"} and face_ids:
        raise LiveBackendError(
            f"{boundary_kind} requires explicit sheet object_names, not face_ids"
        )
    if boundary_kind == "lumped_rlc" and len(object_names) != 1:
        raise LiveBackendError("lumped_rlc requires exactly one explicit sheet object name")
    options = _normalize_hfss_surface_boundary_options(
        boundary_kind,
        args.get("options"),
    )
    return {
        "boundary_kind": boundary_kind,
        "boundary_type": _HFSS_SURFACE_BOUNDARY_TYPES[boundary_kind],
        "boundary_name": boundary_name,
        "assignment_kind": "objects" if object_names else "faces",
        "object_names": object_names,
        "face_ids": face_ids,
        "options": options,
        "max_assignments": max_assignments,
    }


def _normalize_hfss_surface_boundary_options(
    boundary_kind: str,
    raw_options: Any,
) -> dict[str, Any]:
    if raw_options is None:
        raw_options = {}
    if not isinstance(raw_options, dict):
        raise LiveBackendError("options must be an object")
    allowed = {
        "perfect_e": {"is_infinite_ground"},
        "perfect_h": set(),
        "finite_conductivity": {
            "material_name",
            "use_thickness",
            "thickness",
            "roughness",
            "is_infinite_ground",
            "is_two_sided",
            "is_internal",
            "is_shell_element",
        },
        "impedance": {"resistance", "reactance", "is_infinite_ground"},
        "lumped_rlc": {
            "rlc_type",
            "integration_line_direction",
            "resistance",
            "inductance",
            "capacitance",
        },
    }[boundary_kind]
    unsupported = sorted(set(raw_options).difference(allowed))
    if unsupported:
        raise LiveBackendError(
            f"unsupported {boundary_kind} option: {unsupported[0]}"
        )
    if boundary_kind == "perfect_h":
        return {}
    if boundary_kind == "perfect_e":
        return {
            "is_infinite_ground": _surface_boundary_bool_option(
                raw_options,
                "is_infinite_ground",
                False,
            )
        }
    if boundary_kind == "impedance":
        return {
            "resistance": _bounded_float(
                raw_options.get("resistance", 50.0),
                "options.resistance",
                minimum=0.0,
                maximum=1e12,
            ),
            "reactance": _bounded_float(
                raw_options.get("reactance", 0.0),
                "options.reactance",
                minimum=-1e12,
                maximum=1e12,
            ),
            "is_infinite_ground": _surface_boundary_bool_option(
                raw_options,
                "is_infinite_ground",
                False,
            ),
        }
    if boundary_kind == "lumped_rlc":
        rlc_type_by_name = {"parallel": "Parallel", "serial": "Serial"}
        rlc_type = str(raw_options.get("rlc_type") or "Parallel").strip().casefold()
        if rlc_type not in rlc_type_by_name:
            raise LiveBackendError("options.rlc_type must be Parallel or Serial")
        direction_by_name = {
            name.casefold(): name
            for name in ("XNeg", "YNeg", "ZNeg", "XPos", "YPos", "ZPos")
        }
        direction = str(
            raw_options.get("integration_line_direction") or "XNeg"
        ).strip().casefold()
        if direction not in direction_by_name:
            raise LiveBackendError(
                "options.integration_line_direction must be XNeg, YNeg, ZNeg, XPos, YPos, or ZPos"
            )
        values = {
            "resistance": _optional_positive_surface_value(
                raw_options.get("resistance"),
                "options.resistance",
                maximum=1e12,
            ),
            "inductance": _optional_positive_surface_value(
                raw_options.get("inductance"),
                "options.inductance",
                maximum=1e6,
            ),
            "capacitance": _optional_positive_surface_value(
                raw_options.get("capacitance"),
                "options.capacitance",
                maximum=1e3,
            ),
        }
        if all(value is None for value in values.values()):
            raise LiveBackendError(
                "lumped_rlc requires at least one positive resistance, inductance, or capacitance"
            )
        return {
            "rlc_type": rlc_type_by_name[rlc_type],
            "integration_line_direction": direction_by_name[direction],
            **values,
        }
    material_name = str(raw_options.get("material_name") or "").strip()
    if not _SAFE_AEDT_MATERIAL_NAME.fullmatch(material_name):
        raise LiveBackendError(
            "finite_conductivity options.material_name must name an existing material"
        )
    use_thickness = _surface_boundary_bool_option(
        raw_options,
        "use_thickness",
        False,
    )
    is_two_sided = _surface_boundary_bool_option(
        raw_options,
        "is_two_sided",
        False,
    )
    is_internal = _surface_boundary_bool_option(
        raw_options,
        "is_internal",
        True,
    )
    is_shell_element = _surface_boundary_bool_option(
        raw_options,
        "is_shell_element",
        False,
    )
    if not is_two_sided and is_shell_element:
        raise LiveBackendError(
            "options.is_shell_element requires options.is_two_sided=true"
        )
    return {
        "material_name": material_name,
        "use_thickness": use_thickness,
        "thickness": _normalize_surface_length(
            raw_options.get("thickness", "0.1mm"),
            "options.thickness",
            allow_zero=False,
        ),
        "roughness": _normalize_surface_length(
            raw_options.get("roughness", "0um"),
            "options.roughness",
            allow_zero=True,
        ),
        "is_infinite_ground": _surface_boundary_bool_option(
            raw_options,
            "is_infinite_ground",
            False,
        ),
        "is_two_sided": is_two_sided,
        "is_internal": is_internal,
        "is_shell_element": is_shell_element,
    }


def _surface_boundary_bool_option(
    options: dict[str, Any],
    name: str,
    default: bool,
) -> bool:
    value = options.get(name, default)
    if type(value) is not bool:
        raise LiveBackendError(f"options.{name} must be boolean")
    return value


def _optional_positive_surface_value(
    value: Any,
    field: str,
    *,
    maximum: float,
) -> float | None:
    if value is None:
        return None
    normalized = _positive_finite(value, field)
    if normalized > maximum:
        raise LiveBackendError(f"{field} must be at most {maximum:g}")
    return normalized


def _normalize_surface_length(value: Any, field: str, *, allow_zero: bool) -> str:
    if not isinstance(value, str):
        raise LiveBackendError(f"{field} must be an AEDT expression with explicit units")
    expression = value.strip()
    if not _SAFE_AEDT_EXPRESSION.fullmatch(expression):
        raise LiveBackendError(f"{field} contains unsupported AEDT expression characters")
    literal = re.fullmatch(
        r"([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:[eE]([+-]?\d+))?([A-Za-z]+)",
        expression,
    )
    if literal:
        numeric = float(literal.group(1)) * (10 ** int(literal.group(2) or 0))
        if not math.isfinite(numeric) or numeric < 0 or (not allow_zero and numeric == 0):
            qualifier = "non-negative" if allow_zero else "positive"
            raise LiveBackendError(f"{field} literal must be {qualifier}")
    elif re.fullmatch(
        r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?",
        expression,
    ):
        raise LiveBackendError(f"{field} numeric literal must include explicit units")
    elif expression.startswith("-"):
        raise LiveBackendError(f"{field} must not be explicitly negative")
    return expression


def _impedance_solution_supported(solution_type: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "", solution_type).casefold()
    return normalized in {
        "modal",
        "terminal",
        "drivenmodal",
        "driventerminal",
        "transient",
        "eigenmode",
    }


def _lumped_rlc_solution_supported(solution_type: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "", solution_type).casefold()
    return normalized in {
        "modal",
        "terminal",
        "drivenmodal",
        "driventerminal",
        "transient",
        "sbr",
        "sbr+",
        "eigenmode",
    }


def _hfss_surface_boundary_targets(
    geometry: dict[str, Any],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    objects = list(geometry.get("objects") or [])
    by_name = {item["name"]: item for item in objects}
    if spec["object_names"]:
        missing = [name for name in spec["object_names"] if name not in by_name]
        if missing:
            raise LiveBackendError(f"unknown HFSS object: {missing[0]}")
        return [by_name[name] for name in spec["object_names"]]
    owners = []
    owner_names = set()
    face_owner = {
        int(face["face_id"]): item
        for item in objects
        for face in list(item.get("faces") or [])
    }
    for face_id in spec["face_ids"]:
        owner = face_owner.get(face_id)
        if owner is None:
            raise LiveBackendError(f"unknown HFSS face ID: {face_id}")
        if owner["name"] not in owner_names:
            owners.append(owner)
            owner_names.add(owner["name"])
    return owners


def _hfss_geometry_record_is_solid(record: dict[str, Any]) -> bool:
    volume = _optional_float(record.get("volume"))
    return volume is not None and abs(volume) > 1e-18


def _hfss_surface_boundary_target_snapshot(
    target_geometry: list[dict[str, Any]],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_faces = set(spec["face_ids"])
    records = []
    for item in target_geometry:
        faces = [
            {
                "face_id": int(face["face_id"]),
                "center": _stable_geometry_value(face.get("center")),
                "area": _stable_geometry_value(face.get("area")),
                "is_planar": face.get("is_planar"),
            }
            for face in list(item.get("faces") or [])
            if not selected_faces or int(face["face_id"]) in selected_faces
        ]
        records.append(
            {
                "name": item["name"],
                "object_id": item.get("object_id"),
                "material_name": item.get("material_name"),
                "solve_inside": item.get("solve_inside"),
                "bounding_box": _stable_geometry_value(item.get("bounding_box")),
                "is_solid": _hfss_geometry_record_is_solid(item),
                "faces": sorted(faces, key=lambda face: face["face_id"]),
            }
        )
    return records


def _stable_geometry_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, list):
        return [_stable_geometry_value(item) for item in value]
    if isinstance(value, tuple):
        return [_stable_geometry_value(item) for item in value]
    return value


def _hfss_target_geometry_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[str]:
    changes: list[str] = []
    before_objects = {item["name"]: item for item in before}
    after_objects = {item["name"]: item for item in after}
    for name in sorted(set(before_objects) | set(after_objects), key=str.casefold):
        old = before_objects.get(name)
        new = after_objects.get(name)
        if old is None or new is None:
            changes.append(f"{name}:presence")
            continue
        for field in sorted((set(old) | set(new)) - {"faces"}):
            if _digest(old.get(field)) != _digest(new.get(field)):
                changes.append(f"{name}:{field}")
        old_faces = {int(item["face_id"]): item for item in list(old.get("faces") or [])}
        new_faces = {int(item["face_id"]): item for item in list(new.get("faces") or [])}
        for face_id in sorted(set(old_faces) | set(new_faces)):
            old_face = old_faces.get(face_id)
            new_face = new_faces.get(face_id)
            if old_face is None or new_face is None:
                changes.append(f"{name}:face[{face_id}]:presence")
                continue
            for field in sorted(set(old_face) | set(new_face)):
                if _digest(old_face.get(field)) != _digest(new_face.get(field)):
                    changes.append(f"{name}:face[{face_id}]:{field}")
    return changes or ["unclassified"]


def _validate_hfss_infinite_ground_targets(
    geometry: dict[str, Any],
    spec: dict[str, Any],
    target_geometry: list[dict[str, Any]],
) -> None:
    if not (spec.get("options") or {}).get("is_infinite_ground", False):
        return
    if spec["object_names"]:
        invalid = []
        for item in target_geometry:
            faces = list(item.get("faces") or [])
            if _hfss_geometry_record_is_solid(item) or not faces or any(
                face.get("is_planar") is not True for face in faces
            ):
                invalid.append(item["name"])
        if invalid:
            raise LiveBackendError(
                "HFSS infinite-ground boundary requires planar sheet objects: " + invalid[0]
            )
        return

    face_records = {
        int(face["face_id"]): face
        for item in list(geometry.get("objects") or [])
        for face in list(item.get("faces") or [])
    }
    non_planar = [
        face_id
        for face_id in spec["face_ids"]
        if face_records[face_id].get("is_planar") is not True
    ]
    if non_planar:
        raise LiveBackendError(
            f"HFSS infinite-ground boundary requires planar faces: {non_planar[0]}"
        )


def _validate_hfss_lumped_rlc_target(target_geometry: list[dict[str, Any]]) -> None:
    target = target_geometry[0]
    faces = list(target.get("faces") or [])
    if not faces or any(face.get("is_planar") is not True for face in faces):
        raise LiveBackendError(
            "HFSS lumped_rlc boundary requires one planar sheet object: " + target["name"]
        )


def _hfss_lumped_rlc_integration_line(
    app: Any,
    object_name: str,
    direction_name: str,
) -> dict[str, list[str]]:
    try:
        directions = app.axis_directions
    except Exception:
        directions = None
    direction = getattr(directions, direction_name, None) if directions else None
    if direction is None:
        raise LiveBackendError("HFSS axis direction inventory is unavailable")
    try:
        raw_start, raw_end = app.modeler.get_mid_points_on_dir(object_name, direction)
        start = [float(item) for item in raw_start]
        end = [float(item) for item in raw_end]
    except Exception as exc:
        raise LiveBackendError(
            "HFSS lumped_rlc integration line could not be resolved"
        ) from exc
    if (
        len(start) != 3
        or len(end) != 3
        or not all(math.isfinite(item) for item in start + end)
        or all(math.isclose(a, b, rel_tol=0.0, abs_tol=1e-15) for a, b in zip(start, end))
    ):
        raise LiveBackendError("HFSS lumped_rlc integration line must have two distinct 3D points")
    model_units = str(_safe_attribute(app.modeler, "model_units") or "").strip()
    if not re.fullmatch(r"[A-Za-z]+", model_units):
        raise LiveBackendError("HFSS model units are unavailable for Lumped RLC")
    return {
        "start": [str(item) + model_units for item in start],
        "end": [str(item) + model_units for item in end],
    }


def _hfss_surface_boundary_snapshot(app: Any) -> list[dict[str, Any]]:
    try:
        boundaries = list(getattr(app, "boundaries", []) or [])
    except Exception as exc:
        raise LiveBackendError("HFSS boundary inventory is unavailable") from exc
    records = []
    for boundary in boundaries:
        name = str(getattr(boundary, "name", "") or "").strip()
        if not name:
            raise LiveBackendError("HFSS boundary name readback is unavailable")
        props = dict(getattr(boundary, "props", {}) or {})
        properties = dict(getattr(boundary, "properties", {}) or {})
        boundary_type = str(
            _field_property(properties, "Type")
            or getattr(boundary, "type", "")
            or props.get("Type")
            or ""
        ).strip()
        records.append(
            _hfss_surface_boundary_record(
                name,
                boundary_type,
                props,
                properties,
            )
        )
    return sorted(records, key=lambda item: item["name"].casefold())


def _hfss_surface_boundary_record(
    name: str,
    boundary_type: str,
    props: dict[str, Any],
    properties: dict[str, Any],
) -> dict[str, Any]:
    normalized_type = re.sub(r"[\s_-]+", "", boundary_type).casefold()
    kind_by_type = {
        "perfecte": "perfect_e",
        "perfecth": "perfect_h",
        "finiteconductivity": "finite_conductivity",
        "impedance": "impedance",
        "lumpedrlc": "lumped_rlc",
    }
    kind = kind_by_type.get(normalized_type, "other")
    object_names = _boundary_assignment_names(props.get("Objects"))
    face_ids = _boundary_assignment_face_ids(props.get("Faces"))
    record: dict[str, Any] = {
        "name": name,
        "type": boundary_type,
        "kind": kind,
        "assignment_kind": "objects" if object_names else "faces" if face_ids else "unavailable",
        "object_names": object_names,
        "face_ids": face_ids,
        "property_digest": _digest(
            {"props": _json_value(props), "properties": _json_value(properties)}
        ),
    }
    if kind == "perfect_e":
        record["options"] = {
            "is_infinite_ground": _boundary_readback_bool(
                props.get("InfGroundPlane", _field_property(properties, "Inf Ground Plane")),
                False,
            )
        }
    elif kind == "perfect_h":
        record["options"] = {}
    elif kind == "finite_conductivity":
        record["options"] = {
            "material_name": str(
                _field_property(properties, "Material/Material")
                or props.get("Material")
                or ""
            ).strip(),
            "use_thickness": _boundary_readback_bool(
                props.get("UseThickness", _field_property(properties, "Use Thickness")),
                False,
            ),
            "thickness": str(
                _field_property(properties, "Thickness") or props.get("Thickness") or ""
            ).strip(),
            "roughness": str(
                _field_property(properties, "Roughness") or props.get("Roughness") or ""
            ).strip(),
            "is_infinite_ground": _boundary_readback_bool(
                props.get("InfGroundPlane", _field_property(properties, "Inf Ground Plane")),
                False,
            ),
            "is_two_sided": _boundary_readback_bool(props.get("IsTwoSided"), False),
            "is_internal": _boundary_readback_bool(props.get("IsInternal"), False),
            "is_shell_element": _boundary_readback_bool(props.get("IsShellElement"), False),
        }
    elif kind == "impedance":
        record["options"] = {
            "resistance": str(
                _field_property(properties, "Resistance") or props.get("Resistance") or ""
            ).strip(),
            "reactance": str(
                _field_property(properties, "Reactance") or props.get("Reactance") or ""
            ).strip(),
            "is_infinite_ground": _boundary_readback_bool(
                props.get("InfGroundPlane", _field_property(properties, "Inf Ground Plane")),
                False,
            ),
        }
    elif kind == "lumped_rlc":
        record["options"] = {
            "rlc_type": str(
                _field_property(properties, "RLC Type") or props.get("RLC Type") or ""
            ).strip(),
            "use_resistance": _boundary_readback_bool(
                props.get("UseResist", _field_property(properties, "Use Resist")),
                False,
            ),
            "resistance": str(
                _field_property(properties, "Resistance") or props.get("Resistance") or ""
            ).strip(),
            "use_inductance": _boundary_readback_bool(
                props.get("UseInduct", _field_property(properties, "Use Induct")),
                False,
            ),
            "inductance": str(
                _field_property(properties, "Inductance") or props.get("Inductance") or ""
            ).strip(),
            "use_capacitance": _boundary_readback_bool(
                props.get("UseCap", _field_property(properties, "Use Cap")),
                False,
            ),
            "capacitance": str(
                _field_property(properties, "Capacitance") or props.get("Capacitance") or ""
            ).strip(),
            "integration_line": _boundary_integration_line(props.get("CurrentLine")),
        }
    else:
        record["options"] = {}
    return record


def _boundary_assignment_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _boundary_assignment_face_ids(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)):
        values = value
    elif value is None:
        values = []
    else:
        values = [value]
    face_ids = []
    for item in values:
        try:
            face_id = int(item)
        except (TypeError, ValueError):
            continue
        if face_id > 0:
            face_ids.append(face_id)
    return face_ids


def _boundary_readback_bool(value: Any, default: bool) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return default


def _boundary_integration_line(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {"start": [], "end": []}
    return {
        "start": [str(item).strip() for item in list(value.get("Start") or [])],
        "end": [str(item).strip() for item in list(value.get("End") or [])],
    }


def _create_hfss_surface_boundary(app: Any, spec: dict[str, Any]) -> Any:
    assignment = spec["object_names"] or spec["face_ids"]
    options = spec["options"]
    name = spec["boundary_name"]
    if spec["boundary_kind"] == "perfect_e":
        return app.assign_perfect_e(
            assignment,
            is_infinite_ground=options["is_infinite_ground"],
            name=name,
        )
    if spec["boundary_kind"] == "perfect_h":
        return app.assign_perfect_h(assignment, name=name)
    if spec["boundary_kind"] == "finite_conductivity":
        return app.assign_finite_conductivity(
            assignment,
            material=options["material_name"],
            use_thickness=options["use_thickness"],
            thickness=options["thickness"],
            roughness=options["roughness"],
            is_infinite_ground=options["is_infinite_ground"],
            is_two_side=options["is_two_sided"],
            is_internal=options["is_internal"],
            is_shell_element=options["is_shell_element"],
            name=name,
        )
    if spec["boundary_kind"] == "lumped_rlc":
        direction = getattr(app.axis_directions, options["integration_line_direction"])
        return app.assign_lumped_rlc_to_sheet(
            spec["object_names"][0],
            direction,
            name=name,
            rlc_type=options["rlc_type"],
            resistance=options["resistance"],
            inductance=options["inductance"],
            capacitance=options["capacitance"],
        )
    return app.assign_impedance_to_sheet(
        assignment,
        name=name,
        resistance=options["resistance"],
        reactance=options["reactance"],
        is_infinite_ground=options["is_infinite_ground"],
        coordinate_system="Global",
    )


def _verify_hfss_surface_boundary_readback(
    spec: dict[str, Any],
    readback: dict[str, Any],
) -> None:
    if readback.get("kind") != spec["boundary_kind"]:
        raise LiveBackendError("HFSS surface boundary type readback failed")
    if readback.get("assignment_kind") != spec["assignment_kind"]:
        raise LiveBackendError("HFSS surface boundary assignment kind readback failed")
    if readback.get("object_names") != spec["object_names"]:
        raise LiveBackendError("HFSS surface boundary object assignment readback failed")
    if readback.get("face_ids") != spec["face_ids"]:
        raise LiveBackendError("HFSS surface boundary face assignment readback failed")
    expected = spec["options"]
    actual = readback.get("options") or {}
    kind = spec["boundary_kind"]
    if kind == "perfect_h":
        return
    if kind == "lumped_rlc":
        if actual.get("rlc_type") != expected["rlc_type"]:
            raise LiveBackendError("HFSS Lumped RLC type readback failed")
        if not _integration_line_readback_matches(
            actual.get("integration_line"),
            expected["integration_line"],
        ):
            raise LiveBackendError("HFSS Lumped RLC integration-line readback failed")
        quantities = (
            ("resistance", "use_resistance", "ohm"),
            ("inductance", "use_inductance", "H"),
            ("capacitance", "use_capacitance", "F"),
        )
        for value_field, use_field, unit in quantities:
            enabled = expected[value_field] is not None
            if actual.get(use_field) is not enabled:
                raise LiveBackendError(
                    f"HFSS Lumped RLC {value_field} enable readback failed"
                )
            if enabled and not _quantity_boundary_readback_matches(
                actual.get(value_field),
                expected[value_field],
                unit,
            ):
                raise LiveBackendError(
                    f"HFSS Lumped RLC {value_field} readback failed"
                )
        return
    if actual.get("is_infinite_ground") is not expected["is_infinite_ground"]:
        raise LiveBackendError("HFSS surface boundary infinite-ground readback failed")
    if kind == "perfect_e":
        return
    if kind == "impedance":
        if not _numeric_boundary_readback_matches(
            actual.get("resistance"), expected["resistance"]
        ):
            raise LiveBackendError("HFSS impedance resistance readback failed")
        if not _numeric_boundary_readback_matches(
            actual.get("reactance"), expected["reactance"]
        ):
            raise LiveBackendError("HFSS impedance reactance readback failed")
        return
    if str(actual.get("material_name") or "").casefold() != expected[
        "material_name"
    ].casefold():
        raise LiveBackendError("HFSS finite-conductivity material readback failed")
    for field in ("use_thickness", "is_two_sided"):
        if actual.get(field) is not expected[field]:
            raise LiveBackendError(f"HFSS finite-conductivity {field} readback failed")
    if expected["use_thickness"] and _normalized_expression(
        str(actual.get("thickness") or "")
    ) != _normalized_expression(expected["thickness"]):
        raise LiveBackendError("HFSS finite-conductivity thickness readback failed")
    if _normalized_expression(str(actual.get("roughness") or "")) != _normalized_expression(
        expected["roughness"]
    ):
        raise LiveBackendError("HFSS finite-conductivity roughness readback failed")
    relevant_side_field = "is_shell_element" if expected["is_two_sided"] else "is_internal"
    if actual.get(relevant_side_field) is not expected[relevant_side_field]:
        raise LiveBackendError(
            f"HFSS finite-conductivity {relevant_side_field} readback failed"
        )


def _numeric_boundary_readback_matches(actual: Any, expected: float) -> bool:
    try:
        value = float(actual)
    except (TypeError, ValueError):
        return False
    return math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-9)


def _quantity_boundary_readback_matches(actual: Any, expected: float, unit: str) -> bool:
    match = re.fullmatch(
        r"\s*([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)\s*([A-Za-z]+)\s*",
        str(actual or ""),
    )
    if not match or match.group(2).casefold() != unit.casefold():
        return False
    try:
        value = float(match.group(1))
    except ValueError:
        return False
    return math.isclose(
        value,
        expected,
        rel_tol=1e-9,
        abs_tol=max(abs(expected) * 1e-12, 1e-30),
    )


def _integration_line_readback_matches(actual: Any, expected: Any) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    for endpoint in ("start", "end"):
        actual_values = list(actual.get(endpoint) or [])
        expected_values = list(expected.get(endpoint) or [])
        if len(actual_values) != 3 or len(expected_values) != 3:
            return False
        if any(
            _normalized_expression(str(value)) != _normalized_expression(str(wanted))
            for value, wanted in zip(actual_values, expected_values)
        ):
            return False
    return True


def _rollback_hfss_surface_boundary(
    app: Any,
    created_name: str,
    *,
    before_boundaries: list[dict[str, Any]],
) -> dict[str, Any]:
    before_names = {item["name"] for item in before_boundaries}
    delete_error = ""
    try:
        current = _hfss_surface_boundary_snapshot(app)
        current_names = {item["name"] for item in current}
        if created_name in current_names and created_name not in before_names:
            boundary = next(
                item for item in list(app.boundaries or []) if item.name == created_name
            )
            deleted = boundary.delete()
            if deleted is not True:
                raise LiveBackendError("surface boundary delete returned false")
    except Exception as exc:
        try:
            if created_name not in before_names:
                app.oboundary.DeleteBoundaries([created_name])
        except Exception as fallback_exc:
            delete_error = (
                f"{type(exc).__name__}: {exc}; raw fallback failed: "
                f"{type(fallback_exc).__name__}: {fallback_exc}"
            )
    readback_error = ""
    try:
        after = _hfss_surface_boundary_snapshot(app)
    except Exception as exc:
        after = []
        readback_error = f"{type(exc).__name__}: {exc}"
    return {
        "complete": not delete_error and not readback_error and after == before_boundaries,
        "deleted_boundary": created_name if after == before_boundaries else "",
        "remaining_boundaries": [item["name"] for item in after],
        "delete_error": delete_error,
        "readback_error": readback_error,
    }


def _create_hfss_primitive(app: Any, primitive: dict[str, Any]) -> Any:
    modeler = app.modeler
    kind = primitive["kind"]
    if kind == "box":
        created = modeler.create_box(
            primitive["origin"],
            primitive["size"],
            name=primitive["name"],
            material=primitive["material"],
        )
    elif kind == "rectangle":
        created = modeler.create_rectangle(
            primitive["orientation"],
            primitive["origin"],
            primitive["size"],
            name=primitive["name"],
        )
    elif kind == "cylinder":
        created = modeler.create_cylinder(
            primitive["axis"],
            primitive["origin"],
            primitive["radius"],
            primitive["height"],
            num_sides=primitive["num_sides"],
            name=primitive["name"],
            material=primitive["material"],
        )
    else:
        created = modeler.create_region(
            pad_value=primitive["padding"],
            pad_type=primitive["padding_type"],
            name=primitive["name"],
        )
    if created is not None and "solve_inside" in primitive:
        created.solve_inside = primitive["solve_inside"]
    return created


def _resolve_hfss_face_selector(
    object_record: dict[str, Any],
    selector: str,
) -> list[int]:
    faces = list(object_record.get("faces") or [])
    if not faces:
        raise LiveBackendError(
            f"HFSS object has no readable faces: {object_record.get('name', '')}"
        )
    if selector == "all_faces":
        return [int(item["face_id"]) for item in faces]
    if selector == "only_face":
        if len(faces) != 1:
            raise LiveBackendError(
                f"only_face selector is ambiguous for {object_record.get('name', '')}: "
                f"{len(faces)} faces"
            )
        return [int(faces[0]["face_id"])]
    axis_index = {"x": 0, "y": 1, "z": 2}[selector[0]]
    direction = selector.split("_", 1)[1]
    coordinates: list[tuple[dict[str, Any], float]] = []
    for face in faces:
        center = face.get("center")
        if not isinstance(center, list) or len(center) != 3:
            raise LiveBackendError(
                f"HFSS face center is unavailable for {object_record.get('name', '')}"
            )
        try:
            coordinate = float(center[axis_index])
        except (TypeError, ValueError) as exc:
            raise LiveBackendError(
                f"HFSS face center is not numeric for {object_record.get('name', '')}"
            ) from exc
        coordinates.append((face, coordinate))
    target = min(value for _, value in coordinates) if direction == "min" else max(
        value for _, value in coordinates
    )
    tolerance = max(1e-12, abs(target) * 1e-9)
    selected = [face for face, value in coordinates if abs(value - target) <= tolerance]
    if len(selected) != 1:
        raise LiveBackendError(
            f"{selector} selector is ambiguous for {object_record.get('name', '')}: "
            f"{len(selected)} faces at the extreme"
        )
    return [int(selected[0]["face_id"])]


def _create_hfss_boundary(
    app: Any,
    spec: dict[str, Any],
    face_ids: list[int],
) -> Any:
    if spec["boundary_kind"] == "radiation":
        return app.assign_radiation_boundary_to_faces(
            face_ids,
            name=spec["boundary_name"],
        )
    if len(face_ids) != 1:
        raise LiveBackendError(f"{spec['boundary_kind']} requires exactly one resolved face")
    method = app.wave_port if spec["boundary_kind"] == "wave_port" else app.lumped_port
    return method(
        assignment=face_ids[0],
        reference=spec["references"] or None,
        name=spec["boundary_name"],
        **spec["options"],
    )


def _hfss_boundary_type_matches(boundary_kind: str, readback_type: str) -> bool:
    normalized = readback_type.strip().casefold().replace("_", " ")
    if boundary_kind == "radiation":
        return "radiation" in normalized
    if boundary_kind == "wave_port":
        return "wave" in normalized and "port" in normalized
    return "lumped" in normalized and "port" in normalized


def _verify_hfss_primitive_readback(
    primitives: list[dict[str, Any]],
    objects: list[dict[str, Any]],
) -> None:
    by_name = {str(item.get("name") or ""): item for item in objects}
    for primitive in primitives:
        record = by_name.get(primitive["name"])
        if record is None:
            raise LiveBackendError(f"HFSS object readback missing: {primitive['name']}")
        if primitive["kind"] in {"box", "cylinder"} and str(
            record.get("material_name") or ""
        ).casefold() != str(
            primitive["material"]
        ).casefold():
            raise LiveBackendError(f"HFSS material readback failed: {primitive['name']}")
        if "solve_inside" in primitive and record.get("solve_inside") is not primitive["solve_inside"]:
            raise LiveBackendError(f"HFSS solve_inside readback failed: {primitive['name']}")


def _rollback_hfss_objects(
    app: Any,
    created_names: list[str],
    *,
    before_names: list[str],
) -> dict[str, Any]:
    deletion_error = ""
    if created_names:
        try:
            deleted = app.modeler.delete(list(reversed(created_names)))
            if deleted is False:
                _raw_delete_hfss_objects(app, created_names)
        except Exception as exc:
            try:
                _raw_delete_hfss_objects(app, created_names)
            except Exception as fallback_exc:
                deletion_error = (
                    f"{type(exc).__name__}: {exc}; raw fallback failed: "
                    f"{type(fallback_exc).__name__}: {fallback_exc}"
                )
    readback_error = ""
    try:
        current_names = [
            str(item) for item in list(getattr(app.modeler, "object_names", []) or [])
        ]
    except Exception as exc:
        try:
            current_names = _raw_hfss_object_names(app)
        except Exception as fallback_exc:
            current_names = []
            readback_error = (
                f"{type(exc).__name__}: {exc}; raw fallback failed: "
                f"{type(fallback_exc).__name__}: {fallback_exc}"
            )
    before = set(before_names)
    current = set(current_names)
    created = set(created_names)
    missing_old = sorted(before.difference(current))
    remaining_created = sorted(created.intersection(current))
    unexpected = sorted(current.difference(before).difference(created))
    return {
        "complete": (
            not deletion_error
            and not readback_error
            and not missing_old
            and not remaining_created
            and not unexpected
        ),
        "deleted_objects": sorted(created.difference(current)),
        "remaining_created_objects": remaining_created,
        "missing_old_objects": missing_old,
        "unexpected_objects": unexpected,
        "delete_error": deletion_error,
        "readback_error": readback_error,
    }


def _rollback_hfss_boundaries(
    app: Any,
    created_names: list[str],
    *,
    before_names: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    current_before_delete = set(_boundary_names(app))
    by_name = {
        str(getattr(item, "name", item)): item
        for item in list(getattr(app, "boundaries", []) or [])
    }
    for name in reversed(created_names):
        if name not in current_before_delete:
            continue
        try:
            boundary = by_name.get(name)
            if boundary is not None and hasattr(boundary, "delete"):
                deleted = boundary.delete()
                if deleted is False:
                    app.oboundary.DeleteBoundaries([name])
            else:
                app.oboundary.DeleteBoundaries([name])
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    current = set(_boundary_names(app))
    before = set(before_names)
    created = set(created_names).intersection(current_before_delete)
    missing_old = sorted(before.difference(current))
    remaining_created = sorted(created.intersection(current))
    unexpected = sorted(current.difference(before).difference(created))
    return {
        "complete": not errors and not missing_old and not remaining_created and not unexpected,
        "deleted_boundaries": sorted(created.difference(current)),
        "remaining_created_boundaries": remaining_created,
        "missing_old_boundaries": missing_old,
        "unexpected_boundaries": unexpected,
        "delete_errors": errors,
    }


def _raw_delete_hfss_objects(app: Any, names: list[str]) -> None:
    editor = app.modeler.oeditor
    editor.Delete(
        [
            "NAME:Selections",
            "Selections:=",
            ",".join(reversed(names)),
        ]
    )


def _raw_hfss_object_names(app: Any) -> list[str]:
    editor = app.modeler.oeditor
    names: list[str] = []
    seen = set()
    for group in ("Solids", "Sheets", "Lines", "Unclassified"):
        for item in list(editor.GetObjectsInGroup(group) or []):
            name = str(item)
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def _setup_names(app: Any) -> list[str]:
    attribute = "existing_analysis_setups" if hasattr(app, "existing_analysis_setups") else "setup_names"
    return sorted(str(item) for item in list(_read(app, attribute)))


def _rollback_hfss_setup(
    app: Any,
    setup_name: str,
    *,
    before_names: list[str],
) -> dict[str, Any]:
    inventory_error = ""
    try:
        current_before_delete = set(_setup_names(app))
    except Exception as exc:
        current_before_delete = set(before_names)
        current_before_delete.add(setup_name)
        inventory_error = f"{type(exc).__name__}: {exc}"
    delete_error = ""
    if setup_name in current_before_delete:
        try:
            app.oanalysis.DeleteSetups([setup_name])
        except Exception as exc:
            try:
                deleted = app.delete_setup(setup_name)
                if deleted is False:
                    raise LiveBackendError("setup delete returned false")
            except Exception as fallback_exc:
                delete_error = (
                    f"{type(exc).__name__}: {exc}; public fallback failed: "
                    f"{type(fallback_exc).__name__}: {fallback_exc}"
                )
    readback_error = ""
    try:
        current = set(_setup_names(app))
    except Exception as exc:
        current = set()
        readback_error = f"{type(exc).__name__}: {exc}"
    before = set(before_names)
    created = {setup_name}.intersection(current_before_delete)
    missing_old = sorted(before.difference(current))
    remaining_created = sorted(created.intersection(current))
    unexpected = sorted(current.difference(before).difference(created))
    return {
        "complete": (
            not delete_error
            and not readback_error
            and not missing_old
            and not remaining_created
            and not unexpected
        ),
        "deleted_setups": sorted(created.difference(current)),
        "remaining_created_setups": remaining_created,
        "missing_old_setups": missing_old,
        "unexpected_setups": unexpected,
        "initial_inventory_error": inventory_error,
        "delete_error": delete_error,
        "readback_error": readback_error,
    }


def _sweep_names(app: Any, setup_name: str) -> list[str]:
    if setup_name not in _setup_names(app):
        return []
    setup = app.get_setup(setup_name)
    return sorted(str(getattr(item, "name", item)) for item in list(getattr(setup, "sweeps", []) or []))


def _positive_number(arguments: dict[str, Any], name: str) -> float:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise LiveBackendError(f"{name} must be a positive number")
    return float(value)


def _analysis_product(arguments: dict[str, Any]) -> str:
    product = str(arguments.get("product") or "hfss").strip().casefold()
    if product not in {"hfss", "layout"}:
        raise LiveBackendError("analysis product must be hfss or layout")
    return product


def _layout_object_kind(arguments: dict[str, Any]) -> str:
    kind = str(arguments.get("object_kind") or "").strip().casefold()
    if kind not in _LAYOUT_OBJECT_COLLECTIONS:
        raise LiveBackendError("object_kind must be via or component")
    return kind


def _layout_object_record(kind: str, name: str, obj: Any) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    unavailable = []
    for prop in _LAYOUT_OBJECT_READABLE_PROPERTIES[kind]:
        try:
            properties[prop] = _json_value(getattr(obj, prop))
        except Exception:
            unavailable.append(prop)
    return {"name": name, "properties": properties, "unavailable_properties": unavailable}


def _layout_selector_names(selector: dict[str, Any], field: str) -> set[str]:
    raw = selector.get(field) or []
    if not isinstance(raw, list):
        raise LiveBackendError(f"layout connectivity selector {field} must be a list")
    values = set()
    for item in raw:
        value = str(item).strip()
        if not value:
            raise LiveBackendError(f"layout connectivity selector {field} must not contain empty names")
        values.add(value)
    return values


def _unique_nonempty_names(raw: Any, field: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LiveBackendError(f"{field} must be a list of exact AEDT names")
    values = []
    seen = set()
    seen_casefold = set()
    for item in raw:
        if not isinstance(item, str):
            raise LiveBackendError(f"{field} must contain only string AEDT names")
        value = item.strip()
        if not value:
            raise LiveBackendError(f"{field} must not contain empty names")
        if value in seen or value.casefold() in seen_casefold:
            raise LiveBackendError(f"{field} must not contain duplicate names: {value}")
        values.append(value)
        seen.add(value)
        seen_casefold.add(value.casefold())
    return values


def _normalize_explicit_names(raw: Any, *, field: str, maximum: int) -> list[str]:
    values = _unique_nonempty_names(raw, field)
    if not values:
        raise LiveBackendError(f"{field} must contain at least one exact AEDT name")
    if len(values) > maximum:
        raise LiveBackendError(f"{field} exceeds the approved maximum of {maximum}")
    for value in values:
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(value):
            raise LiveBackendError(f"{field} must contain safe exact AEDT names")
    return values


def _layout_live_component_connections(
    app: Any,
    *,
    relevant_nets: set[str],
) -> tuple[list[Any], list[dict[str, str]]]:
    from aedt_agent.layout.ports import ComponentConnection

    try:
        components = {
            str(name): component
            for name, component in dict(app.modeler.components or {}).items()
        }
    except Exception as exc:
        raise LiveBackendError("layout component inventory is unavailable") from exc
    units = str(_safe_attribute(app.modeler, "model_units") or "m")
    output = []
    unavailable = []
    for name in sorted(components):
        component = components[name]
        try:
            pins = {
                str(pin_name): pin
                for pin_name, pin in dict(getattr(component, "pins") or {}).items()
            }
        except Exception as exc:
            unavailable.append(
                {
                    "name": name,
                    "reason": f"{type(exc).__name__}: component pin inventory unavailable",
                }
            )
            continue
        pin_records = []
        for pin_name, pin in sorted(pins.items()):
            net_name = str(_safe_attribute(pin, "net_name") or "")
            if net_name not in relevant_nets:
                continue
            location = _safe_attribute(pin, "location")
            position = _layout_position_in_meters(location, units)
            pin_records.append(
                {
                    "pin": pin_name,
                    "net": net_name,
                    "position": position,
                    "padstack": str(
                        _safe_attribute(pin, "padstack_definition")
                        or _safe_attribute(pin, "padstackname")
                        or ""
                    ),
                    "start_layer": str(_safe_attribute(pin, "start_layer") or ""),
                    "stop_layer": str(_safe_attribute(pin, "stop_layer") or ""),
                }
            )
        if not pin_records:
            continue
        bbox = _layout_bbox_in_meters(_safe_attribute(component, "bounding_box"), units)
        if bbox is None:
            unavailable.append(
                {
                    "name": name,
                    "reason": "component bounding_box is unavailable or malformed",
                }
            )
            continue
        output.append(
            ComponentConnection(
                name=name,
                partname=str(_safe_attribute(component, "part") or ""),
                component_type=str(_safe_attribute(component, "part_type") or ""),
                layer=str(_safe_attribute(component, "placement_layer") or ""),
                bbox=bbox,
                pins=pin_records,
            )
        )
    return output, unavailable


def _layout_position_in_meters(value: Any, units: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return [0.0, 0.0]
    try:
        factor = _layout_length_factor_to_meters(units)
        return [float(value[0]) * factor, float(value[1]) * factor]
    except (TypeError, ValueError):
        return [0.0, 0.0]


def _layout_bbox_in_meters(value: Any, units: str) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        factor = _layout_length_factor_to_meters(units)
        return [float(item) * factor for item in value]
    except (TypeError, ValueError):
        return None


def _layout_length_factor_to_meters(units: str) -> float:
    factors = {
        "m": 1.0,
        "meter": 1.0,
        "meters": 1.0,
        "cm": 1e-2,
        "mm": 1e-3,
        "um": 1e-6,
        "nm": 1e-9,
        "in": 0.0254,
        "inch": 0.0254,
        "mil": 0.0000254,
        "mils": 0.0000254,
    }
    normalized = str(units).strip().casefold()
    if normalized not in factors:
        raise LiveBackendError(f"unsupported layout model units: {units}")
    return factors[normalized]


def _normalize_layout_edge_targets(
    app: Any,
    raw_targets: Any,
    *,
    max_new_ports: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw_targets, list) or not raw_targets:
        raise LiveBackendError("edge_targets must be a non-empty list")
    if len(raw_targets) > max_new_ports:
        raise LiveBackendError(
            f"edge target count {len(raw_targets)} exceeds max_new_ports {max_new_ports}"
        )
    try:
        lines = {str(name): value for name, value in dict(app.modeler.lines or {}).items()}
    except Exception as exc:
        raise LiveBackendError("layout line inventory is unavailable") from exc
    geometries: dict[str, Any] = dict(lines)
    if any(isinstance(item, dict) and item.get("reference_primitive") for item in raw_targets):
        try:
            geometries.update(
                {str(name): value for name, value in dict(app.modeler.geometries or {}).items()}
            )
        except Exception as exc:
            raise LiveBackendError("layout geometry inventory is unavailable for reference edges") from exc
    allowed = {
        "primitive_name",
        "edge_number",
        "port_type",
        "reference_primitive",
        "reference_edge_number",
        "wave_horizontal_extension",
        "wave_vertical_extension",
        "wave_launcher",
    }
    normalized = []
    seen = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"edge_targets[{index}] must be an object")
        unsupported = sorted(set(raw).difference(allowed))
        if unsupported:
            raise LiveBackendError(f"unsupported edge target field: {unsupported[0]}")
        primitive_name = str(raw.get("primitive_name") or "").strip()
        if not primitive_name or primitive_name not in lines:
            raise LiveBackendError(f"unknown layout line primitive: {primitive_name}")
        edge_number = raw.get("edge_number")
        if type(edge_number) is not int or edge_number < 0:
            raise LiveBackendError("edge_number must be a non-negative integer")
        key = (primitive_name, edge_number)
        if key in seen:
            raise LiveBackendError(
                f"edge_targets must not duplicate {primitive_name} edge {edge_number}"
            )
        seen.add(key)
        port_type = str(raw.get("port_type") or "circuit").strip().casefold()
        if port_type not in {"circuit", "wave"}:
            raise LiveBackendError("port_type must be circuit or wave")
        request: dict[str, Any] = {
            "primitive_name": primitive_name,
            "edge_number": edge_number,
            "port_type": port_type,
        }
        reference_primitive = str(raw.get("reference_primitive") or "").strip()
        reference_record = None
        if reference_primitive:
            if reference_primitive not in geometries:
                raise LiveBackendError(f"unknown layout reference primitive: {reference_primitive}")
            reference_edge_number = raw.get("reference_edge_number", 0)
            if type(reference_edge_number) is not int or reference_edge_number < 0:
                raise LiveBackendError("reference_edge_number must be a non-negative integer")
            if (reference_primitive, reference_edge_number) == key:
                raise LiveBackendError("reference edge must differ from the signal edge")
            request["reference_primitive"] = reference_primitive
            request["reference_edge_number"] = reference_edge_number
            reference_record = _layout_geometry_edge_record(
                geometries[reference_primitive],
                reference_primitive,
                reference_edge_number,
            )
        elif "reference_edge_number" in raw:
            raise LiveBackendError("reference_edge_number requires reference_primitive")
        if port_type == "wave":
            request["wave_horizontal_extension"] = _bounded_edge_port_factor(
                raw.get("wave_horizontal_extension", 5),
                "wave_horizontal_extension",
            )
            request["wave_vertical_extension"] = _bounded_edge_port_factor(
                raw.get("wave_vertical_extension", 3),
                "wave_vertical_extension",
            )
            wave_launcher = str(raw.get("wave_launcher") or "1mm").strip()
            if not wave_launcher or len(wave_launcher) > 128:
                raise LiveBackendError("wave_launcher must be a non-empty AEDT expression up to 128 characters")
            request["wave_launcher"] = wave_launcher
        elif any(
            field in raw
            for field in (
                "wave_horizontal_extension",
                "wave_vertical_extension",
                "wave_launcher",
            )
        ):
            raise LiveBackendError("wave port options require port_type=wave")
        normalized.append(
            {
                "request": request,
                "primary_edge": _layout_geometry_edge_record(
                    lines[primitive_name],
                    primitive_name,
                    edge_number,
                ),
                "reference_edge": reference_record,
            }
        )
    return normalized


def _layout_geometry_edge_record(geometry: Any, name: str, edge_number: int) -> dict[str, Any]:
    try:
        edges = list(getattr(geometry, "edges") or [])
        edge = edges[edge_number]
        start = [float(edge[0][0]), float(edge[0][1])]
        end = [float(edge[1][0]), float(edge[1][1])]
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise LiveBackendError(f"invalid edge {edge_number} on layout primitive {name}") from exc
    return {
        "primitive_name": name,
        "edge_number": edge_number,
        "start": start,
        "end": end,
        "midpoint": [(start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0],
        "length": math.hypot(end[0] - start[0], end[1] - start[1]),
        "net_name": str(_safe_attribute(geometry, "net_name") or ""),
        "layer": str(_safe_attribute(geometry, "placement_layer") or ""),
    }


def _bounded_edge_port_factor(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveBackendError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 < normalized <= 100:
        raise LiveBackendError(f"{field} must be greater than 0 and at most 100")
    return normalized


def _layout_terminal_record(kind: str, name: str, terminal: Any) -> dict[str, Any]:
    attributes = ("net_name", "start_layer", "stop_layer", "location", "holediam")
    record: dict[str, Any] = {"name": name, "kind": kind}
    unavailable = []
    for attribute in attributes:
        try:
            value = getattr(terminal, attribute)
            record["hole_diameter" if attribute == "holediam" else attribute] = _json_value(value)
        except Exception:
            record["hole_diameter" if attribute == "holediam" else attribute] = None
            unavailable.append(attribute)
    component_name = ""
    if kind == "pin":
        for attribute in ("componentname", "component_name"):
            value = _safe_attribute(terminal, attribute)
            if value:
                component_name = str(value)
                break
        if not component_name and "-" in name:
            component_name = "-".join(name.split("-")[:-1])
    record["component_name"] = component_name
    record["net_name"] = str(record.get("net_name") or "")
    record["unavailable_properties"] = unavailable
    return record


def _layout_connectivity_component_record(name: str, component: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"name": name}
    unavailable = []
    for attribute in ("part", "part_type", "enabled", "placement_layer", "location", "angle"):
        try:
            record[attribute] = _json_value(getattr(component, attribute))
        except Exception:
            record[attribute] = None
            unavailable.append(attribute)
    try:
        record["pin_count"] = len(dict(getattr(component, "pins") or {}))
    except Exception:
        record["pin_count"] = None
        unavailable.append("pins")
    record["unavailable_properties"] = unavailable
    return record


def _layout_net_classes(app: Any) -> tuple[dict[str, str], list[dict[str, str]]]:
    classes = {}
    errors = []
    for net_class, attribute in (
        ("power_ground", "power_nets"),
        ("signal", "signal_nets"),
        ("unclassified", "no_nets"),
    ):
        try:
            values = dict(getattr(app.modeler, attribute) or {})
        except Exception as exc:
            errors.append(
                {
                    "section": f"net_classes:{net_class}",
                    "reason": f"{type(exc).__name__}: {attribute} API unavailable",
                }
            )
            continue
        for name in values:
            classes[str(name)] = net_class
    return classes, errors


def _validate_layout_object_properties(kind: str, properties: dict[str, Any]) -> None:
    if "location" in properties:
        value = properties["location"]
        if not isinstance(value, list) or len(value) != 2 or any(
            isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
        ):
            raise LiveBackendError("location must contain exactly two numeric values in the design model units")
    if "lock_position" in properties and type(properties["lock_position"]) is not bool:
        raise LiveBackendError("lock_position must be boolean")
    if "enabled" in properties and type(properties["enabled"]) is not bool:
        raise LiveBackendError("enabled must be boolean")
    for name in ("net_name", "placement_layer"):
        if name in properties and (not isinstance(properties[name], str) or not properties[name].strip()):
            raise LiveBackendError(f"{name} must be a non-empty string")
    if "angle" in properties and (
        isinstance(properties["angle"], bool) or not isinstance(properties["angle"], (int, float, str))
    ):
        raise LiveBackendError("angle must be numeric or an AEDT expression")


def _property_values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return _normalized_expression(actual) == _normalized_expression(expected)
    return actual == expected


def _layout_stackup_records(
    app: Any,
    *,
    max_items: int,
) -> tuple[list[dict[str, Any]], str]:
    try:
        layers = list(app.modeler.layers.stackup_layers or [])
    except Exception as exc:
        return [], f"{type(exc).__name__}: stackup API unavailable"
    records = []
    for index, layer in enumerate(layers[:max_items]):
        records.append(
            {
                "order": index,
                "name": str(getattr(layer, "name", "")),
                "type": str(getattr(layer, "type", "")),
                "id": _safe_json_attribute(layer, "id"),
                "thickness": _safe_json_attribute(layer, "thickness"),
                "thickness_units": _safe_json_attribute(layer, "thickness_units"),
                "lower_elevation": _safe_json_attribute(layer, "lower_elevation"),
                "material": _safe_json_attribute(layer, "material"),
                "fill_material": _safe_json_attribute(layer, "fill_material"),
                "roughness": _safe_json_attribute(layer, "roughness"),
                "etch": _safe_json_attribute(layer, "etch"),
                "is_negative": _safe_json_attribute(layer, "is_negative"),
                "top_bottom": _safe_json_attribute(layer, "top_bottom"),
            }
        )
    error = "truncated_by_max_items" if len(layers) > max_items else ""
    return records, error


def _layout_padstack_records(
    app: Any,
    *,
    max_items: int,
    include_layers: bool,
) -> tuple[list[dict[str, Any]], str]:
    try:
        padstacks = dict(app.modeler.padstacks or {})
    except Exception as exc:
        return [], f"{type(exc).__name__}: padstack API unavailable"
    records = []
    items = sorted(
        ((str(name), padstack) for name, padstack in padstacks.items()),
        key=lambda item: item[0],
    )
    for name, padstack in items[:max_items]:
        layer_records = []
        layers_value = _safe_attribute(padstack, "layers")
        layers = dict(layers_value or {}) if isinstance(layers_value, dict) else {}
        if include_layers:
            for layer_name in sorted(str(item) for item in layers)[:max_items]:
                layer = layers[layer_name]
                layer_records.append(
                    {
                        "name": layer_name,
                        "id": _safe_json_attribute(layer, "id"),
                        "pad": _padstack_hole_record(_safe_attribute(layer, "pad")),
                        "antipad": _padstack_hole_record(_safe_attribute(layer, "antipad")),
                        "thermal": _padstack_hole_record(_safe_attribute(layer, "thermal")),
                        "connection_direction": _safe_json_attribute(layer, "connectiondir"),
                    }
                )
        records.append(
            {
                "name": name,
                "material": _safe_json_attribute(padstack, "mat"),
                "plating_percent": _safe_json_attribute(padstack, "plating"),
                "hole_range": _safe_json_attribute(padstack, "holerange"),
                "hole": _padstack_hole_record(_safe_attribute(padstack, "hole")),
                "layer_count": len(layers),
                "layer_names": sorted(str(item) for item in layers)[:max_items],
                "layers": layer_records,
            }
        )
    truncated = len(items) > max_items or any(
        item["layer_count"] > max_items for item in records
    )
    return records, "truncated_by_max_items" if truncated else ""


def _padstack_hole_record(hole: Any) -> dict[str, Any] | None:
    if hole is None:
        return None
    return {
        "shape": _safe_json_attribute(hole, "shape"),
        "sizes": _safe_json_attribute(hole, "sizes"),
        "x": _safe_json_attribute(hole, "x"),
        "y": _safe_json_attribute(hole, "y"),
        "rotation": _safe_json_attribute(hole, "rot"),
    }


def _layout_differential_pair_records(
    app: Any,
    *,
    max_items: int,
) -> tuple[list[dict[str, Any]], str]:
    saver = getattr(app, "save_diff_pairs_to_file", None)
    if callable(saver):
        try:
            with tempfile.TemporaryDirectory(prefix="ansys-agent-diff-pairs-") as directory:
                path = Path(directory) / "pairs.csv"
                if not saver(str(path)):
                    return [], "SaveDiffPairsToFile returned false"
                if not path.is_file():
                    return [], "SaveDiffPairsToFile did not create a file"
                with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                    rows = list(csv.reader(handle))
        except Exception as exc:
            return [], f"{type(exc).__name__}: differential pair export failed"
        records = []
        malformed = False
        for row in rows[:max_items]:
            if len(row) < 8:
                malformed = True
                continue
            records.append(
                {
                    "positive_terminal": row[0].strip(),
                    "negative_terminal": row[1].strip(),
                    "active": row[2].strip() == "1",
                    "matched": row[3].strip() == "1",
                    "differential_mode": row[4].strip(),
                    "differential_reference_ohm": _optional_float(row[5]),
                    "common_mode": row[6].strip(),
                    "common_reference_ohm": _optional_float(row[7]),
                }
            )
        errors = []
        if len(rows) > max_items:
            errors.append("truncated_by_max_items")
        if malformed:
            errors.append("malformed_diff_pair_rows_skipped")
        return records, "; ".join(errors)

    getter = getattr(app, "get_differential_pairs", None)
    if not callable(getter):
        return [], "differential pair API unavailable"
    try:
        names = [str(item) for item in list(getter() or [])]
    except Exception as exc:
        return [], f"{type(exc).__name__}: differential pair API failed"
    return (
        [{"differential_mode": item, "terminal_mapping_status": "unavailable"} for item in names[:max_items]],
        "terminal_mapping_unavailable" if names else "",
    )


def _safe_json_attribute(owner: Any, attribute: str) -> Any:
    value = _safe_attribute(owner, attribute)
    return _json_value(value) if value is not None else None


def _safe_attribute(owner: Any, attribute: str) -> Any:
    try:
        value = getattr(owner, attribute)
        if callable(value):
            value = value()
        return value
    except Exception:
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _report_names(app: Any) -> list[str]:
    return sorted(str(item) for item in list(getattr(app.post, "all_report_names", []) or []))


def _port_names(app: Any) -> list[str]:
    fallback: list[str] = []
    for attribute in ("ports", "excitation_names", "port_list"):
        values = getattr(app, attribute, None)
        if values is not None:
            normalized = [str(item) for item in list(values or [])]
            if normalized:
                return normalized
            fallback = normalized
    return fallback


def _port_order_source(app: Any) -> str:
    for attribute in ("ports", "excitation_names", "port_list"):
        values = getattr(app, attribute, None)
        if values is not None and list(values or []):
            return f"pyaedt.{attribute}"
    return "unavailable"


def _hfss_port_names(app: Any) -> list[str]:
    for attribute in ("ports", "excitation_names", "port_list"):
        names = _safe_string_list(app, attribute)
        if names:
            return sorted(set(names))
    return []


def _boundary_names(app: Any) -> list[str]:
    names = {str(item) for item in list(getattr(app, "ports", []) or [])}
    names.update(str(getattr(item, "name", item)) for item in list(getattr(app, "boundaries", []) or []))
    return sorted(names)


def _simulation_running(app: Any) -> bool:
    owner = app if hasattr(app, "are_there_simulations_running") else app.desktop_class
    return bool(_read(owner, "are_there_simulations_running"))


def _refresh_analysis_run(run: dict[str, Any], running: bool) -> None:
    if run.get("state") == "canceled":
        return
    if running:
        run["state"] = "running"
        run["_observed_running"] = True
        run["last_observed_at"] = _utc_now()
        return
    if run.get("_observed_running") or run.get("state") == "running":
        run["state"] = "not_running"
        run.setdefault("last_observed_at", _utc_now())
        return
    submitted_at = float(run.get("_submitted_monotonic") or time.monotonic())
    if time.monotonic() - submitted_at >= _ANALYSIS_SUBMISSION_GRACE_SECONDS:
        run["state"] = "not_running_unverified"
        run.setdefault("last_observed_at", _utc_now())


def _public_analysis_run(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if not key.startswith("_")}


def _solution_snapshot(
    app: Any,
    setup_name: str,
    *,
    query_solution_data: bool = True,
) -> dict[str, Any]:
    existing_solutions = _safe_string_list(app, "existing_analysis_sweeps")
    setup = None
    if setup_name:
        try:
            setup = app.get_setup(setup_name)
        except Exception:
            setup = None
    setup_is_solved = (
        _safe_optional_bool(setup, "is_solved") if query_solution_data else None
    )
    sweeps = []
    try:
        setup_sweeps = list(getattr(setup, "sweeps", []) or [])
    except Exception:
        setup_sweeps = []
    for sweep in setup_sweeps:
        sweeps.append(
            {
                "name": str(getattr(sweep, "name", sweep)),
                "is_solved": (
                    _safe_optional_bool(sweep, "is_solved")
                    if query_solution_data
                    else None
                ),
            }
        )
    target_solutions = [
        item
        for item in existing_solutions
        if not setup_name or _solution_setup_name(item) == setup_name
    ]
    results = _results_directory_snapshot(app)
    snapshot = {
        "setup_name": setup_name,
        "setup_is_solved": setup_is_solved,
        "target_solution_available": bool(setup_is_solved is True or target_solutions),
        "target_solution_names": target_solutions,
        "existing_analysis_sweeps": existing_solutions,
        "sweeps": sweeps,
        "results": results,
    }
    return {**snapshot, "snapshot_digest": _digest(snapshot)}


def _finalize_analysis_solution_evidence(run: dict[str, Any], app: Any) -> None:
    previous_evidence = dict(run.get("solution_evidence") or {})
    if previous_evidence.get("result_freshness_verified") is True:
        return
    if int(previous_evidence.get("verification_attempt") or 0) >= _MAX_SOLUTION_EVIDENCE_ATTEMPTS:
        return
    setup_name = str(run.get("setup_name") or "")
    before = dict(run.get("_solution_before") or {})
    after = _solution_snapshot(app, setup_name)
    before_results = dict(before.get("results") or {})
    after_results = dict(after.get("results") or {})
    results_changed = bool(
        after_results.get("snapshot_digest")
        and after_results.get("snapshot_digest") != before_results.get("snapshot_digest")
    )
    submitted_ns = _iso_timestamp_ns(str(run.get("started_at") or ""))
    latest_mtime_ns = int(after_results.get("latest_mtime_ns") or 0)
    result_written_after_submit = bool(
        submitted_ns
        and latest_mtime_ns
        and latest_mtime_ns >= submitted_ns - 2_000_000_000
    )
    observed_running = bool(run.get("_observed_running"))
    solution_available = after.get("target_solution_available") is True
    freshness_verified = bool(
        observed_running
        and solution_available
        and results_changed
        and result_written_after_submit
        and not after_results.get("truncated")
        and not after_results.get("scan_error")
    )
    reasons = []
    if not observed_running:
        reasons.append("solver_running_state_was_not_observed")
    if not solution_available:
        reasons.append("target_solution_data_is_not_available")
    if not results_changed:
        reasons.append("results_directory_snapshot_did_not_change")
    if not result_written_after_submit:
        reasons.append("no_result_file_timestamp_after_submission")
    if after_results.get("truncated"):
        reasons.append("results_directory_scan_was_truncated")
    if after_results.get("scan_error"):
        reasons.append("results_directory_scan_failed")
    run["solution_evidence"] = {
        "verification_attempt": int(previous_evidence.get("verification_attempt") or 0) + 1,
        "setup_name": setup_name,
        "before_snapshot_digest": before.get("snapshot_digest"),
        "after_snapshot_digest": after.get("snapshot_digest"),
        "target_solution_available": solution_available,
        "setup_is_solved": after.get("setup_is_solved"),
        "target_solution_names": list(after.get("target_solution_names") or []),
        "results_snapshot_changed": results_changed,
        "result_written_after_submit": result_written_after_submit,
        "solve_running_observed": observed_running,
        "solve_success_verified": freshness_verified,
        "result_freshness_verified": freshness_verified,
        "verification_reasons": reasons or ["fresh_solution_artifacts_verified"],
        "results": after_results,
        "verified_at": _utc_now(),
    }


def _safe_string_list(owner: Any, attribute: str) -> list[str]:
    try:
        values = getattr(owner, attribute, [])
        if callable(values):
            values = values()
        return [str(item) for item in list(values or [])]
    except Exception:
        return []


def _safe_optional_bool(owner: Any, attribute: str) -> bool | None:
    if owner is None:
        return None
    try:
        value = getattr(owner, attribute)
        if callable(value):
            value = value()
        return bool(value)
    except Exception:
        return None


def _solution_setup_name(solution_name: str) -> str:
    return str(solution_name).split(":", 1)[0].strip()


def _results_directory_snapshot(
    app: Any,
    *,
    max_files: int = 20_000,
    max_directories: int = 20_000,
) -> dict[str, Any]:
    try:
        root_value = getattr(app, "results_directory", "")
        if callable(root_value):
            root_value = root_value()
        root = Path(str(root_value or "")).resolve()
    except Exception as exc:
        return _empty_results_snapshot(scan_error=type(exc).__name__)
    if not str(root_value or "") or not root.is_dir():
        return _empty_results_snapshot(results_directory=str(root) if str(root_value or "") else "")

    file_count = 0
    directory_count = 0
    total_bytes = 0
    latest_mtime_ns = 0
    latest_relative_path = ""
    truncated = False
    scan_error = ""
    records = []
    try:
        for current_root, directory_names, file_names in os.walk(root):
            directory_names.sort()
            file_names.sort()
            directory_count += 1
            if directory_count > max_directories:
                truncated = True
                break
            for file_name in file_names:
                path = Path(current_root) / file_name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                relative = str(path.relative_to(root))
                file_count += 1
                total_bytes += int(stat.st_size)
                mtime_ns = int(stat.st_mtime_ns)
                records.append((relative, int(stat.st_size), mtime_ns))
                if mtime_ns >= latest_mtime_ns:
                    latest_mtime_ns = mtime_ns
                    latest_relative_path = relative
                if file_count >= max_files:
                    truncated = True
                    break
            if truncated:
                break
    except OSError as exc:
        scan_error = type(exc).__name__
    aggregate = {
        "file_count": file_count,
        "directory_count": directory_count,
        "total_bytes": total_bytes,
        "latest_mtime_ns": latest_mtime_ns,
        "latest_relative_path": latest_relative_path,
        "truncated": truncated,
        "scan_error": scan_error,
        "records_digest": _digest(records),
    }
    return {
        "results_directory": str(root),
        "exists": True,
        **aggregate,
        "snapshot_digest": _digest(aggregate),
    }


def _empty_results_snapshot(
    *,
    results_directory: str = "",
    scan_error: str = "",
) -> dict[str, Any]:
    aggregate = {
        "file_count": 0,
        "directory_count": 0,
        "total_bytes": 0,
        "latest_mtime_ns": 0,
        "latest_relative_path": "",
        "truncated": False,
        "scan_error": scan_error,
        "records_digest": _digest([]),
    }
    return {
        "results_directory": results_directory,
        "exists": False,
        **aggregate,
        "snapshot_digest": _digest(aggregate),
    }


def _iso_timestamp_ns(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000)
    except (TypeError, ValueError, OverflowError):
        return 0


def _analysis_resources(args: dict[str, Any]) -> dict[str, Any]:
    limits = {"cores": (1, 256), "tasks": (1, 64), "gpus": (0, 32)}
    resources: dict[str, Any] = {}
    for name, (minimum, maximum) in limits.items():
        value = args.get(name)
        if value is not None and (type(value) is not int or not minimum <= value <= maximum):
            raise LiveBackendError(f"{name} must be an integer from {minimum} to {maximum}, or null")
        resources[name] = value
    use_auto_settings = args.get("use_auto_settings", True)
    if type(use_auto_settings) is not bool:
        raise LiveBackendError("use_auto_settings must be a boolean")
    resources["use_auto_settings"] = use_auto_settings
    return resources


def _analysis_state(app: Any, setup_name: str) -> dict[str, Any]:
    setup = app.get_setup(setup_name)
    properties = getattr(setup, "props", {})
    return {
        "setups": _setup_names(app),
        "setup_name": setup_name,
        "setup_properties": _json_value(properties),
        "running": _simulation_running(app),
    }


def _safe_component(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    return cleaned[:80] or "unnamed"


def _require_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise LiveBackendError("export path escaped the configured export root") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_empty_or_partial_export(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)
