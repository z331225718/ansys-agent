from __future__ import annotations

import threading
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import time
from typing import Any

from aedt_agent.live.target import AedtTarget
from aedt_agent.live.versioning import (
    DEFAULT_AEDT_VERSION,
    aedt_versions_match,
    extract_reported_aedt_version,
    normalize_aedt_version,
)


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
            if command == "hfss_geometry_inventory":
                return self._hfss_geometry_inventory(target, arguments)
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
                    }
                )
            objects.append(
                {
                    "name": str(name),
                    "object_id": _json_value(getattr(obj, "id", None)),
                    "material_name": str(getattr(obj, "material_name", "")),
                    "solve_inside": bool(getattr(obj, "solve_inside", False)),
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
        options = dict(args.get("options") or {})
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
            if spec["boundary_kind"] == "radiation":
                boundary = app.assign_radiation_boundary_to_faces(
                    spec["assignment_face_ids"],
                    name=spec["boundary_name"],
                )
            elif spec["boundary_kind"] == "wave_port":
                boundary = app.wave_port(
                    assignment=spec["assignment_face_ids"][0],
                    reference=spec["references"] or None,
                    name=spec["boundary_name"],
                    **spec["options"],
                )
            else:
                boundary = app.lumped_port(
                    assignment=spec["assignment_face_ids"][0],
                    reference=spec["references"] or None,
                    name=spec["boundary_name"],
                    **spec["options"],
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
        run = {
            "run_id": run_id,
            "product": spec["product"],
            "setup_name": spec["setup_name"],
            "resources": resources,
            "started_at": started_at,
            "state": "running" if _simulation_running(app) else "submitted",
        }
        self._analysis_runs[(app.project_name, app.design_name, spec["setup_name"])] = run
        del self._previews[preview_id]
        return {
            "status": "submitted",
            "started": True,
            "preview_id": preview_id,
            **run,
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
        if run is not None and not running and run["state"] in {"running", "submitted"}:
            run["state"] = "not_running"
            run.setdefault("last_observed_at", _utc_now())
        return {
            "product": product,
            "running": running,
            "setups": list(_read(app, setup_attribute)),
            "setup_name": setup_name,
            "latest_run": dict(run) if run is not None else None,
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
        app = self._app(target, "hfss", _required(args, "project_name"), _required(args, "design_name"))
        export_kind = _required(args, "export_kind").lower()
        if export_kind not in {"touchstone", "report_csv"}:
            raise LiveBackendError(f"unsupported HFSS export kind: {export_kind}")
        if _simulation_running(app):
            raise LiveBackendError("cannot export while an AEDT simulation is running")
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
            "ports": sorted(str(item) for item in list(getattr(app, "ports", []) or [])),
            "running": False,
            "setup_name": setup_name,
            "sweep_name": sweep_name,
            "report_name": report_name,
        }
        digest = _digest(state)
        spec = {
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
            "path_policy": "server_managed_directory_only",
            "project_unchanged": True,
        }

    def _hfss_export_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_export", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        spec = preview["spec"]
        current = {
            "setups": _setup_names(app),
            "reports": _report_names(app),
            "ports": sorted(str(item) for item in list(getattr(app, "ports", []) or [])),
            "running": _simulation_running(app),
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

_HFSS_BOUNDARY_OPTIONS = {
    "radiation": set(),
    "wave_port": {"modes", "impedance", "renormalize", "deembed", "integration_line"},
    "lumped_port": {"impedance", "renormalize", "deembed", "integration_line"},
}

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


def _setup_names(app: Any) -> list[str]:
    attribute = "existing_analysis_setups" if hasattr(app, "existing_analysis_setups") else "setup_names"
    return sorted(str(item) for item in list(_read(app, attribute)))


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


def _report_names(app: Any) -> list[str]:
    return sorted(str(item) for item in list(getattr(app.post, "all_report_names", []) or []))


def _boundary_names(app: Any) -> list[str]:
    names = {str(item) for item in list(getattr(app, "ports", []) or [])}
    names.update(str(getattr(item, "name", item)) for item in list(getattr(app, "boundaries", []) or []))
    return sorted(names)


def _simulation_running(app: Any) -> bool:
    owner = app if hasattr(app, "are_there_simulations_running") else app.desktop_class
    return bool(_read(owner, "are_there_simulations_running"))


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
