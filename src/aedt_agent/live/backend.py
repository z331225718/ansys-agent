from __future__ import annotations

import contextlib
import csv
import hashlib
import io
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
from uuid import uuid4

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
        # Inventory wrappers are not interchangeable.  A PyAEDT collection can
        # fail while the underlying oEditor call remains usable, so never use a
        # single failed wrapper call as a session-wide negative capability cache.
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
            if command == "open_aedt_python_preview":
                return self._open_aedt_python_preview(target, arguments)
            if command == "open_aedt_python_apply":
                return self._open_aedt_python_apply(target, arguments)
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
            if command == "hfss_material_create_preview":
                return self._hfss_material_create_preview(target, arguments)
            if command == "hfss_material_create_apply":
                return self._hfss_material_create_apply(target, arguments)
            if command == "hfss_material_update_preview":
                return self._hfss_material_update_preview(target, arguments)
            if command == "hfss_material_update_apply":
                return self._hfss_material_update_apply(target, arguments)
            if command == "hfss_material_delete_preview":
                return self._hfss_material_delete_preview(target, arguments)
            if command == "hfss_material_delete_apply":
                return self._hfss_material_delete_apply(target, arguments)
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
            if command == "hfss_coordinate_system_inventory":
                return self._hfss_coordinate_system_inventory(target, arguments)
            if command == "hfss_coordinate_system_create_preview":
                return self._hfss_coordinate_system_create_preview(target, arguments)
            if command == "hfss_coordinate_system_create_apply":
                return self._hfss_coordinate_system_create_apply(target, arguments)
            if command == "hfss_geometry_create_preview":
                return self._hfss_geometry_create_preview(target, arguments)
            if command == "hfss_geometry_create_apply":
                return self._hfss_geometry_create_apply(target, arguments)
            if command == "hfss_geometry_move_preview":
                return self._hfss_geometry_move_preview(target, arguments)
            if command == "hfss_geometry_move_apply":
                return self._hfss_geometry_move_apply(target, arguments)
            if command == "hfss_geometry_rotate_preview":
                return self._hfss_geometry_rotate_preview(target, arguments)
            if command == "hfss_geometry_rotate_apply":
                return self._hfss_geometry_rotate_apply(target, arguments)
            if command == "hfss_antipad_subtract_preview":
                return self._hfss_antipad_subtract_preview(target, arguments)
            if command == "hfss_antipad_subtract_apply":
                return self._hfss_antipad_subtract_apply(target, arguments)
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
            if command == "hfss_port_inventory":
                return self._hfss_port_inventory(target, arguments)
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
            if command == "layout_material_create_assign_preview":
                return self._layout_material_create_assign_preview(target, arguments)
            if command == "layout_material_create_assign_apply":
                return self._layout_material_create_assign_apply(target, arguments)
            if command == "layout_via_create_preview":
                return self._layout_via_create_preview(target, arguments)
            if command == "layout_via_create_apply":
                return self._layout_via_create_apply(target, arguments)
            if command == "layout_via_update_preview":
                return self._layout_via_update_preview(target, arguments)
            if command == "layout_via_update_apply":
                return self._layout_via_update_apply(target, arguments)
            if command == "layout_via_delete_preview":
                return self._layout_via_delete_preview(target, arguments)
            if command == "layout_via_delete_apply":
                return self._layout_via_delete_apply(target, arguments)
            if command == "layout_antipad_circle_create_preview":
                return self._layout_antipad_circle_create_preview(target, arguments)
            if command == "layout_antipad_circle_create_apply":
                return self._layout_antipad_circle_create_apply(target, arguments)
            if command == "layout_connectivity_inventory":
                return self._layout_connectivity_inventory(target, arguments)
            if command == "layout_signal_via_inventory":
                return self._layout_signal_via_inventory(target, arguments)
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
            if command == "layout_property_schema":
                return self._layout_property_schema(target, arguments)
            if command == "layout_properties_read":
                return self._layout_properties_read(target, arguments)
            if command == "controlled_read_schema":
                return self._controlled_read_schema(target, arguments)
            if command == "controlled_read_execute":
                return self._controlled_read_execute(target, arguments)
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
            if command == "variable_batch_upsert_preview":
                return self._variable_batch_upsert_preview(target, arguments)
            if command == "variable_batch_upsert_apply":
                return self._variable_batch_upsert_apply(target, arguments)
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

    def _open_aedt_python_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        """Freeze unrestricted AEDT/PyAEDT code for explicit Desktop approval.

        This is deliberately separate from the typed Harness commands.  It is not a
        sandbox: the code executes with the permissions of the AEDT Desktop user.
        """
        product = _required(args, "product").strip().lower()
        if product not in {"hfss", "layout"}:
            raise LiveBackendError("product must be exactly 'hfss' or 'layout'")
        project_name = _required(args, "project_name")
        design_name = _required(args, "design_name")
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            raise LiveBackendError("code must be a non-empty Python string")
        if "\x00" in code or len(code.encode("utf-8")) > 131_072:
            raise LiveBackendError("code must be valid UTF-8 text no longer than 128 KiB")
        try:
            compile(code, "<approved-aedt-python>", "exec")
        except SyntaxError as exc:
            raise LiveBackendError(f"Python syntax error: {exc}") from exc

        app = self._app(target, product, project_name, design_name)
        desktop = self._desktop_for(target)
        project_path = self._open_aedt_project_path(app, desktop, project_name)
        if not project_path.exists():
            raise LiveBackendError(
                "unable to locate an accessible on-disk AEDT project (.aedt) or EDB database (.aedb) for backup"
            )
        identity = self._open_aedt_identity(app, desktop, target, product, project_path)
        source_fingerprint = _open_aedt_source_fingerprint(project_path)
        code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        change_summary = _open_aedt_change_summary(args.get("change_summary"))
        state = {
            "identity": identity,
            "source_fingerprint": source_fingerprint,
            "code_sha256": code_sha256,
            "change_summary": change_summary,
        }
        digest = _digest(state)
        preview_id = "open-aedt-python-preview-" + digest[:24]
        backup_root = Path(
            os.environ.get("AEDT_AGENT_BACKUP_ROOT")
            or project_path.parent / ".aedt-agent-backups"
        ).resolve()
        backup_dir = backup_root / datetime.now(timezone.utc).strftime("%Y%m%d") / preview_id
        self._previews[preview_id] = {
            "kind": "open_aedt_python",
            "target": target,
            "product": product,
            "project_name": project_name,
            "design_name": design_name,
            "project_path": str(project_path),
            "identity": identity,
            "source_fingerprint": source_fingerprint,
            "code": code,
            "code_sha256": code_sha256,
            "change_summary": change_summary,
            "digest": digest,
            "backup_dir": str(backup_dir),
        }
        return {
            "preview_id": preview_id,
            "snapshot_digest": digest,
            "approval_required": True,
            "execution_policy": "open_with_approval",
            "risk": "arbitrary AEDT/PyAEDT Python executes as the AEDT Desktop user",
            "target_identity": identity,
            "code_sha256": code_sha256,
            "code_bytes": len(code.encode("utf-8")),
            "change_summary": change_summary,
            "approval_display": {
                "change_summary": change_summary,
                "target": f"{project_name} / {design_name} ({product})",
                "backup": str(backup_dir),
                "code_sha256": code_sha256[:16] + "...",
                "risk": "完全访问 Python；执行前会保存并备份工程",
            },
            "source_fingerprint": source_fingerprint,
            "backup_plan": {
                "required": True,
                "action": "save_active_project_then_copy_project_or_aedb",
                "destination": str(backup_dir),
                "source_project": str(project_path),
            },
            "automatic_rollback": False,
            "project_saved": False,
        }

    def _open_aedt_python_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        try:
            preview = self._previews[preview_id]
        except KeyError as exc:
            raise LiveBackendError("unknown open AEDT Python preview") from exc
        if preview.get("kind") != "open_aedt_python" or preview.get("target") != target:
            raise LiveBackendError("open AEDT Python preview belongs to a different AEDT target")

        app = self._app(
            target,
            str(preview["product"]),
            str(preview["project_name"]),
            str(preview["design_name"]),
        )
        desktop = self._desktop_for(target)
        project_path = self._open_aedt_project_path(app, desktop, str(preview["project_name"]))
        current_identity = self._open_aedt_identity(app, desktop, target, str(preview["product"]), project_path)
        if current_identity != preview["identity"]:
            raise LiveBackendError("stale open AEDT Python preview: project, design, or project file changed")
        if _open_aedt_source_fingerprint(project_path) != preview["source_fingerprint"]:
            raise LiveBackendError("stale open AEDT Python preview: on-disk project or AEDB contents changed")

        backup = self._create_open_aedt_backup(desktop, preview, project_path)
        events: list[Any] = []

        def emit(value: Any) -> None:
            if len(events) < 64:
                events.append(_json_value(value))

        stdout = io.StringIO()
        stderr = io.StringIO()
        namespace = {
            "app": app,
            "desktop": desktop,
            "oeditor": getattr(getattr(app, "modeler", None), "oeditor", None),
            "emit": emit,
            "__name__": "__aedt_open_operation__",
        }
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(compile(str(preview["code"]), "<approved-aedt-python>", "exec"), namespace, namespace)
        except BaseException as exc:
            # Do not let SystemExit or KeyboardInterrupt terminate the persistent broker.
            result = {
                "status": "failed",
                "preview_id": preview_id,
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
                "backup": backup,
                "events": events,
                "stdout": stdout.getvalue()[-16_384:],
                "stderr": stderr.getvalue()[-16_384:],
                "postcondition": "unverified; inspect AEDT and restore the backup if needed",
                "automatic_rollback": False,
                "project_saved_before_execution": True,
            }
            del self._previews[preview_id]
            return result

        del self._previews[preview_id]
        return {
            "status": "completed",
            "preview_id": preview_id,
            "backup": backup,
            "events": events,
            "stdout": stdout.getvalue()[-16_384:],
            "stderr": stderr.getvalue()[-16_384:],
            "postcondition": "unverified; validate the requested AEDT state before any further edit",
            "automatic_rollback": False,
            "project_saved_before_execution": True,
        }

    def _open_aedt_project_path(self, app: Any, desktop: Any, project_name: str) -> Path:
        """Find the persisted AEDT project or the imported EDB directory.

        PyAEDT's ``project_file`` is derived from the project name and may be
        stale for a live layout imported from an AEDB.  Prefer an existing path
        and use the Desktop project path as a second source of truth.
        """
        project = desktop.active_project()
        names = [project_name, str(getattr(app, "project_name", "")), _name(project) or ""]
        base_names = [Path(name).stem for name in names if name]
        candidates: list[Path] = []

        def add_candidate(value: Any) -> None:
            if not value:
                return
            candidate = Path(str(value)).expanduser()
            candidates.append(candidate)
            if candidate.suffix:
                return
            for base_name in base_names:
                candidates.append(candidate / f"{base_name}.aedt")
                candidates.append(candidate / f"{base_name}.aedb")

        for attribute in ("project_file", "project_path"):
            add_candidate(getattr(app, attribute, None))
        project = desktop.active_project()
        if project is not None:
            get_path = getattr(project, "GetPath", None)
            if callable(get_path):
                add_candidate(str(get_path() or "").strip())

        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.is_file() and resolved.suffix.lower() == ".aedt":
                return resolved
            if resolved.is_dir() and resolved.suffix.lower() == ".aedb":
                return resolved

        # A manually opened AEDT project may expose only a directory through COM.
        for candidate in candidates:
            directory = candidate if not candidate.suffix else candidate.parent
            if not directory.is_dir():
                continue
            matches = [item.resolve() for item in directory.glob("*.aedt") if item.is_file()]
            if len(matches) == 1:
                return matches[0]
        raise LiveBackendError("unable to determine the active AEDT project or AEDB path for backup")

    def _open_aedt_identity(
        self,
        app: Any,
        desktop: Any,
        target: AedtTarget,
        product: str,
        project_path: Path,
    ) -> dict[str, Any]:
        project = desktop.active_project()
        design = desktop.active_design(project) if project is not None else None
        return {
            "target": target.to_dict(),
            "product": product,
            "project_name": str(getattr(app, "project_name", "")),
            "design_name": _canonical_design_name(str(getattr(app, "design_name", ""))),
            "desktop_active_project": _name(project),
            "desktop_active_design": _design_display_name(desktop, design),
            "design_type": str(getattr(app, "design_type", "")),
            "project_path": str(project_path.resolve()),
        }

    def _create_open_aedt_backup(self, desktop: Any, preview: dict[str, Any], project_path: Path) -> dict[str, Any]:
        if not desktop.save_project(project_name=str(preview["project_name"])):
            raise LiveBackendError("AEDT rejected the required pre-execution project save")
        if not project_path.exists():
            raise LiveBackendError("project or AEDB path disappeared after the required pre-execution save")
        destination = Path(str(preview["backup_dir"])).resolve()
        staging = destination.with_name(destination.name + f".staging-{uuid4().hex}")
        copied_relative: list[Path] = []
        try:
            staging.mkdir(parents=True, exist_ok=False)
            if project_path.is_dir():
                aedb_copy = staging / project_path.name
                shutil.copytree(project_path, aedb_copy)
                copied_relative.append(Path(project_path.name))
                source_kind = "aedb_directory"
            else:
                project_copy = staging / project_path.name
                shutil.copy2(project_path, project_copy)
                copied_relative.append(Path(project_path.name))
                aedb = project_path.with_suffix(".aedb")
                if aedb.is_dir():
                    aedb_copy = staging / aedb.name
                    shutil.copytree(aedb, aedb_copy)
                    copied_relative.append(Path(aedb.name))
                source_kind = "aedt_project"
            backup_fingerprint = _open_aedt_source_fingerprint(project_path)
            manifest = {
                "schema_version": 1,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source_path": str(project_path),
                "source_kind": source_kind,
                "source_fingerprint": backup_fingerprint,
                "preview_id": preview["digest"],
                "project_name": preview["project_name"],
                "design_name": preview["design_name"],
                "copied_entries": [str(item).replace("\\", "/") for item in copied_relative],
            }
            (staging / "backup-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            staging.replace(destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise
        return {
            "directory": str(destination),
            "files": [str(destination / item) for item in copied_relative],
            "source_kind": source_kind,
            "manifest": str(destination / "backup-manifest.json"),
            "source_fingerprint": backup_fingerprint,
            "project_saved_before_backup": True,
            "restore_hint": "close the affected project and reopen the backed-up .aedt project or AEDB database from this directory",
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
        catalog = _hfss_material_catalog_snapshot(app)
        all_records = catalog["materials"]
        records = all_records[:max_items]
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "material_count": len(all_records),
            "returned_count": len(records),
            "truncated": len(all_records) > len(records),
            "materials": records,
            "snapshot_digest": _digest(catalog),
            "design_unchanged": True,
        }

    def _hfss_material_create_preview(
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
            raise LiveBackendError("HFSS material creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS materials while a simulation is running")
        spec = _normalize_hfss_material_create_spec(args)
        catalog = _hfss_material_catalog_snapshot(app)
        by_name = {item["canonical_name"].casefold(): item for item in catalog["materials"]}
        existing = by_name.get(spec["material_name"].casefold())
        if existing is not None:
            raise LiveBackendError(f"HFSS material already exists: {existing['canonical_name']}")
        library_name = _hfss_existing_material_name(app, spec["material_name"])
        if library_name:
            raise LiveBackendError(
                f"HFSS material name collides with an AEDT material library entry: {library_name}"
            )
        state = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "material_catalog": catalog,
        }
        state_digest = _digest(state)
        preview_id = "material-create-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_material_create",
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
            "existing_material_count": len(catalog["materials"]),
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_material_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_material_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS materials while a simulation is running")
        current = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "material_catalog": _hfss_material_catalog_snapshot(app),
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS material create preview")

        spec = preview["spec"]
        before_catalog = preview["state"]["material_catalog"]
        created_name = ""
        try:
            materials = _safe_attribute(app, "materials")
            add_material = getattr(materials, "add_material", None)
            if not callable(add_material):
                raise LiveBackendError("HFSS material creation API is unavailable")
            properties = {
                name: spec[name]
                for name in (
                    "permittivity",
                    "permeability",
                    "conductivity",
                    "dielectric_loss_tangent",
                    "magnetic_loss_tangent",
                )
            }
            material = add_material(spec["material_name"], properties=properties)
            created_name = str(_safe_attribute(material, "name") or "").strip()
            if not material or created_name != spec["material_name"]:
                raise LiveBackendError("HFSS material creation returned an unexpected name")
            if spec["appearance"] is not None:
                material.material_appearance = list(spec["appearance"])
                update = getattr(material, "update", None)
                if not callable(update) or update() is False:
                    raise LiveBackendError("HFSS material appearance update returned false")

            after_catalog = _hfss_material_catalog_snapshot(app)
            before_names = {
                item["canonical_name"] for item in before_catalog["materials"]
            }
            after_names = {
                item["canonical_name"] for item in after_catalog["materials"]
            }
            if after_names != before_names | {spec["material_name"]}:
                raise LiveBackendError("unexpected HFSS material catalog change")
            readback = next(
                (
                    item
                    for item in after_catalog["materials"]
                    if item["canonical_name"] == spec["material_name"]
                ),
                None,
            )
            if readback is None:
                raise LiveBackendError("HFSS material readback is missing")
            _verify_hfss_material_create_readback(spec, readback)
        except Exception as exc:
            rollback = _rollback_hfss_material_create(
                app,
                created_name or spec["material_name"],
                before_catalog=before_catalog,
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS material creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS material creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "created_material_name": spec["material_name"],
            "material": readback,
            "material_count": len(after_catalog["materials"]),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_material_update_preview(
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
            raise LiveBackendError("HFSS material update requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot update HFSS materials while a simulation is running")
        spec = _normalize_hfss_material_update_spec(args)
        catalog = _hfss_material_catalog_snapshot(app)
        catalog_by_name = {
            item["canonical_name"].casefold(): item for item in catalog["materials"]
        }
        canonical_updates = []
        for update in spec["updates"]:
            before = catalog_by_name.get(update["material_name"].casefold())
            if before is None:
                raise LiveBackendError(
                    "material_name must already exist in the current HFSS project material catalog"
                )
            if before["canonical_name"] != update["material_name"]:
                raise LiveBackendError(
                    "material_name must preserve the exact case of the current HFSS project material"
                )
            canonical_updates.append({**update, "material_name": before["canonical_name"]})
        spec = {**spec, "updates": canonical_updates}
        target_names = [item["material_name"] for item in canonical_updates]
        _refresh_hfss_material_objects(app, target_names)
        catalog = _hfss_material_catalog_snapshot(app)
        refreshed_by_name = {
            item["canonical_name"]: item for item in catalog["materials"]
        }
        targets = []
        for update in canonical_updates:
            before = refreshed_by_name[update["material_name"]]
            _validate_hfss_material_update_target(before, update)
            targets.append(before)
        references = _hfss_material_reference_snapshot(app, target_names)
        material_object_ids = _hfss_material_object_ids(app, target_names)
        raw_definitions = {
            name: _hfss_material_raw_definition(app, name) for name in target_names
        }
        state = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "material_catalog": catalog,
            "targets": targets,
            "references": references,
            "material_object_ids": material_object_ids,
            "raw_definitions": raw_definitions,
        }
        state_digest = _digest(state)
        preview_id = "material-update-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_material_update",
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
            "reference_count": len(references),
            "references_before": references,
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_material_update_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_material_update", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot update HFSS materials while a simulation is running")
        target_names = [item["material_name"] for item in preview["spec"]["updates"]]
        try:
            current = {
                "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
                "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
                "material_catalog": _hfss_material_catalog_snapshot(app),
                "targets": [
                    _hfss_material_snapshot(app, name) for name in target_names
                ],
                "references": _hfss_material_reference_snapshot(app, target_names),
                "material_object_ids": _hfss_material_object_ids(app, target_names),
                "raw_definitions": {
                    name: _hfss_material_raw_definition(app, name)
                    for name in target_names
                },
            }
        except LiveBackendError as exc:
            raise LiveBackendError("stale HFSS material update preview") from exc
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS material update preview")

        before_catalog = preview["state"]["material_catalog"]
        before_targets = preview["state"]["targets"]
        before_references = preview["state"]["references"]
        expected_object_ids = preview["state"]["material_object_ids"]
        raw_definitions = preview["state"]["raw_definitions"]
        try:
            for update in preview["spec"]["updates"]:
                material = _hfss_material_object(app, update["material_name"])
                if id(material) != expected_object_ids[update["material_name"]]:
                    raise LiveBackendError(
                        f"HFSS material object changed during apply: {update['material_name']}"
                    )
                for property_name in _HFSS_MATERIAL_NUMERIC_PROPERTIES:
                    if property_name in update:
                        setattr(material, property_name, update[property_name])
                if "appearance" in update:
                    material.material_appearance = list(update["appearance"])
                updater = getattr(material, "update", None)
                if not callable(updater) or updater() is False:
                    raise LiveBackendError(
                        f"HFSS material update returned false: {update['material_name']}"
                    )

            after_catalog = _hfss_material_catalog_snapshot(app)
            targets_after = _verify_hfss_material_update_catalog(
                before_catalog,
                after_catalog,
                preview["spec"]["updates"],
            )
            after_raw_definitions = {
                name: _hfss_material_raw_definition(app, name)
                for name in target_names
            }
            _verify_hfss_material_raw_definition_updates(
                raw_definitions,
                after_raw_definitions,
                preview["spec"]["updates"],
            )
            references_after = _hfss_material_reference_snapshot(app, target_names)
            if references_after != before_references:
                raise LiveBackendError(
                    "HFSS material references or referenced object state changed during update"
                )
            if _hfss_material_object_ids(app, target_names) != expected_object_ids:
                raise LiveBackendError("HFSS material object identity changed during update")
        except Exception as exc:
            rollback = _rollback_hfss_material_updates(
                app,
                before_targets=before_targets,
                before_catalog=before_catalog,
                before_references=before_references,
                expected_object_ids=expected_object_ids,
                raw_definitions=raw_definitions,
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS material update failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS material update failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **preview["spec"],
            "updated_material_names": target_names,
            "updated_material_count": len(target_names),
            "targets_before": before_targets,
            "targets_after": targets_after,
            "reference_count": len(references_after),
            "references_before": before_references,
            "references_after": references_after,
            "material_count": len(after_catalog["materials"]),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_material_delete_preview(
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
            raise LiveBackendError("HFSS material deletion requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot delete HFSS materials while a simulation is running")
        spec = _normalize_hfss_material_delete_spec(args)
        catalog = _hfss_material_catalog_snapshot(app)
        catalog_by_name = {
            item["canonical_name"].casefold(): item for item in catalog["materials"]
        }
        canonical_names = []
        for requested_name in spec["names"]:
            material = catalog_by_name.get(requested_name.casefold())
            if material is None:
                raise LiveBackendError(
                    "material names must already exist in the current HFSS project material catalog"
                )
            if material["canonical_name"] != requested_name:
                raise LiveBackendError(
                    "material names must preserve the exact case of current HFSS project materials"
                )
            canonical_names.append(material["canonical_name"])
        spec = {**spec, "names": canonical_names}
        _refresh_hfss_material_objects(app, canonical_names)
        catalog = _hfss_material_catalog_snapshot(app)
        refreshed_by_name = {
            item["canonical_name"]: item for item in catalog["materials"]
        }
        targets = [refreshed_by_name[name] for name in canonical_names]
        references = _hfss_material_reference_snapshot(app, canonical_names)
        if references:
            raise LiveBackendError(
                "HFSS materials must have zero solid-object references before deletion: "
                f"{references[0]['material_name']} -> {references[0]['name']}"
            )
        boundaries = _hfss_material_boundary_reference_snapshot(app, canonical_names)
        referenced_boundaries = [item for item in boundaries if item["material_names"]]
        if referenced_boundaries:
            first = referenced_boundaries[0]
            raise LiveBackendError(
                "HFSS materials must have zero boundary references before deletion: "
                f"{first['material_names'][0]} -> {first['name']}"
            )
        material_object_ids = _hfss_material_object_ids(app, canonical_names)
        raw_definitions = {
            name: _hfss_material_raw_definition(app, name) for name in canonical_names
        }
        state = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "material_catalog": catalog,
            "targets": targets,
            "references": references,
            "boundaries": boundaries,
            "material_object_ids": material_object_ids,
            "raw_definitions": raw_definitions,
        }
        state_digest = _digest(state)
        preview_id = "material-delete-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_material_delete",
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
            "solid_reference_count": 0,
            "boundary_reference_count": 0,
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_material_delete_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_material_delete", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot delete HFSS materials while a simulation is running")
        names = list(preview["spec"]["names"])
        try:
            current = {
                "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
                "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
                "material_catalog": _hfss_material_catalog_snapshot(app),
                "targets": [_hfss_material_snapshot(app, name) for name in names],
                "references": _hfss_material_reference_snapshot(app, names),
                "boundaries": _hfss_material_boundary_reference_snapshot(app, names),
                "material_object_ids": _hfss_material_object_ids(app, names),
                "raw_definitions": {
                    name: _hfss_material_raw_definition(app, name) for name in names
                },
            }
        except LiveBackendError as exc:
            raise LiveBackendError("stale HFSS material delete preview") from exc
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS material delete preview")

        before_catalog = preview["state"]["material_catalog"]
        before_boundaries = preview["state"]["boundaries"]
        expected_object_ids = preview["state"]["material_object_ids"]
        deleted_names = []
        try:
            materials = _safe_attribute(app, "materials")
            remover = getattr(materials, "remove_material", None)
            if not callable(remover):
                raise LiveBackendError("HFSS material removal API is unavailable")
            for name in names:
                material = _hfss_material_object(app, name)
                if id(material) != expected_object_ids[name]:
                    raise LiveBackendError(
                        f"HFSS material object changed during delete apply: {name}"
                    )
                if remover(name) is not True:
                    raise LiveBackendError(f"HFSS material removal returned false: {name}")
                deleted_names.append(name)
                if name in _hfss_project_material_names(app):
                    raise LiveBackendError(f"HFSS material still exists after deletion: {name}")

            after_catalog = _hfss_material_catalog_snapshot(app)
            _verify_hfss_material_delete_catalog(before_catalog, after_catalog, names)
            references_after = _hfss_material_reference_snapshot(app, names)
            if references_after:
                raise LiveBackendError("deleted HFSS material still has solid references")
            boundaries_after = _hfss_material_boundary_reference_snapshot(app, names)
            if boundaries_after != before_boundaries:
                raise LiveBackendError("HFSS boundary state changed during material deletion")
        except Exception as exc:
            rollback = _rollback_hfss_material_deletes(
                app,
                deleted_names=deleted_names,
                raw_definitions=preview["state"]["raw_definitions"],
                before_catalog=before_catalog,
                before_boundaries=before_boundaries,
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS material deletion failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS material deletion failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **preview["spec"],
            "deleted_material_names": deleted_names,
            "deleted_material_count": len(deleted_names),
            "targets_before": preview["state"]["targets"],
            "solid_reference_count": 0,
            "boundary_reference_count": 0,
            "remaining_material_count": len(after_catalog["materials"]),
            "absence_digest": _digest(
                {"deleted_material_names": deleted_names, "material_catalog": after_catalog}
            ),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
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

    def _hfss_coordinate_system_inventory(
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
            raise LiveBackendError("HFSS coordinate system inventory requires an HFSS 3D design")
        max_items = _bounded_integer(
            args.get("max_items", 100),
            "max_items",
            minimum=1,
            maximum=500,
        )
        snapshot = _hfss_coordinate_system_snapshot(app)
        records = snapshot["coordinate_systems"]
        selected = records[:max_items]
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "model_units": snapshot["model_units"],
            "active_coordinate_system": snapshot["active_coordinate_system"],
            "coordinate_system_count": len(records),
            "relative_coordinate_system_count": sum(
                item["kind"] == "relative" for item in records
            ),
            "returned_count": len(selected),
            "truncated": len(records) > len(selected),
            "coordinate_systems": selected,
            "snapshot_digest": _digest(snapshot),
            "design_unchanged": True,
        }

    def _hfss_coordinate_system_create_preview(
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
            raise LiveBackendError("HFSS coordinate system creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS coordinate systems while a simulation is running")
        spec = _normalize_hfss_coordinate_system_spec(args)
        snapshot = _hfss_coordinate_system_snapshot(app)
        records = snapshot["coordinate_systems"]
        by_name = {item["name"].casefold(): item for item in records}
        existing = by_name.get(spec["coordinate_system_name"].casefold())
        if existing is not None:
            raise LiveBackendError(
                f"HFSS coordinate system already exists: {existing['name']}"
            )
        reference = by_name.get(spec["reference_coordinate_system"].casefold())
        if reference is None:
            raise LiveBackendError(
                "reference_coordinate_system must be Global or an existing relative coordinate system"
            )
        if reference["kind"] not in {"global", "relative"}:
            raise LiveBackendError(
                "reference_coordinate_system must be Global or an existing relative coordinate system"
            )
        spec["reference_coordinate_system"] = reference["name"]
        state = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "coordinate_system_snapshot": snapshot,
            "variables": _variable_records(app),
        }
        state_digest = _digest(state)
        preview_id = "coordinate-system-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_coordinate_system_create",
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
            "model_units": snapshot["model_units"],
            "reference_coordinate_system_record": reference,
            "active_coordinate_system_before": snapshot["active_coordinate_system"],
            "existing_coordinate_system_count": len(records),
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_coordinate_system_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_coordinate_system_create", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS coordinate systems while a simulation is running")
        spec = preview["spec"]
        current = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "coordinate_system_snapshot": _hfss_coordinate_system_snapshot(app),
            "variables": _variable_records(app),
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS coordinate system create preview")

        before_snapshot = preview["state"]["coordinate_system_snapshot"]
        created_name = ""
        try:
            coordinate_system = app.modeler.create_coordinate_system(
                origin=spec["origin"],
                reference_cs=spec["reference_coordinate_system"],
                name=spec["coordinate_system_name"],
                mode="axis",
                x_pointing=spec["x_axis"],
                y_pointing=spec["y_axis"],
            )
            created_name = str(getattr(coordinate_system, "name", "") or "")
            if coordinate_system is None or created_name != spec["coordinate_system_name"]:
                raise LiveBackendError("HFSS coordinate system creation returned an unexpected name")
            _set_hfss_working_coordinate_system(
                app,
                before_snapshot["active_coordinate_system"],
            )
            after_snapshot = _hfss_coordinate_system_snapshot(app)
            before_names = {item["name"] for item in before_snapshot["coordinate_systems"]}
            after_names = {item["name"] for item in after_snapshot["coordinate_systems"]}
            if after_names != before_names | {spec["coordinate_system_name"]}:
                raise LiveBackendError("unexpected HFSS coordinate system inventory change")
            if (
                after_snapshot["active_coordinate_system"]
                != before_snapshot["active_coordinate_system"]
            ):
                raise LiveBackendError("HFSS active coordinate system was not restored")
            readback = next(
                (
                    item
                    for item in after_snapshot["coordinate_systems"]
                    if item["name"] == spec["coordinate_system_name"]
                ),
                None,
            )
            if readback is None:
                raise LiveBackendError("HFSS coordinate system readback is missing")
            _verify_hfss_coordinate_system_readback(
                spec,
                readback,
                model_units=after_snapshot["model_units"],
            )
        except Exception as exc:
            rollback = _rollback_hfss_coordinate_system(
                app,
                created_name or spec["coordinate_system_name"],
                before_snapshot=before_snapshot,
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS coordinate system creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS coordinate system creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "created_coordinate_system_name": spec["coordinate_system_name"],
            "coordinate_system": readback,
            "coordinate_system_count": len(after_snapshot["coordinate_systems"]),
            "active_coordinate_system_restored": True,
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

    def _hfss_geometry_move_preview(
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
            raise LiveBackendError("HFSS geometry movement requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot move HFSS geometry while a simulation is running")
        spec = _normalize_hfss_geometry_moves(args)
        active_coordinate_system = _hfss_active_coordinate_system(app)
        if active_coordinate_system.casefold() != "global":
            raise LiveBackendError(
                "HFSS geometry movement requires Global to be the active coordinate system"
            )
        state = _hfss_geometry_move_state(app, spec["names"])
        by_name = {item["name"].casefold(): item for item in state["geometry"]}
        canonical_moves = []
        targets = []
        for move in spec["moves"]:
            record = by_name.get(move["name"].casefold())
            if record is None:
                raise LiveBackendError(
                    "HFSS geometry move names must already exist in the current design"
                )
            if record["name"] != move["name"]:
                raise LiveBackendError(
                    "HFSS geometry move names must preserve exact object-name case"
                )
            _validate_hfss_geometry_move_target(record)
            canonical_moves.append({**move, "name": record["name"]})
            targets.append(record)
        spec = {
            **spec,
            "moves": canonical_moves,
            "names": [item["name"] for item in canonical_moves],
            "model_units": state["model_units"],
            "coordinate_system": "Global",
        }
        state = _hfss_geometry_move_state(app, spec["names"])
        state_digest = _digest(state)
        preview_id = "geometry-move-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_geometry_move",
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
            "boundary_count": len(state["boundaries"]),
            "mesh_operation_count": len(state["mesh_operations"]),
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_geometry_move_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_geometry_move", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot move HFSS geometry while a simulation is running")
        names = list(preview["spec"]["names"])
        current = _hfss_geometry_move_state(app, names)
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS geometry move preview")

        moved: list[dict[str, Any]] = []
        try:
            modeler = _safe_attribute(app, "modeler")
            mover = getattr(modeler, "move", None)
            if not callable(mover):
                raise LiveBackendError("HFSS geometry move API is unavailable")
            for move in preview["spec"]["moves"]:
                if mover([move["name"]], move["vector"]) is not True:
                    raise LiveBackendError(
                        f"HFSS geometry move returned false: {move['name']}"
                    )
                moved.append(move)

            after = _hfss_geometry_move_state(app, names)
            expected_geometry = _translated_hfss_geometry_snapshot(
                preview["state"]["geometry"],
                preview["spec"]["moves"],
            )
            _verify_hfss_geometry_move_state(
                preview["state"],
                after,
                expected_geometry=expected_geometry,
            )
        except Exception as exc:
            rollback = _rollback_hfss_geometry_moves(
                app,
                moved,
                before_state=preview["state"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS geometry movement failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS geometry movement failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        after_by_name = {item["name"]: item for item in after["geometry"]}
        targets_after = [after_by_name[name] for name in names]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **preview["spec"],
            "moved_object_count": len(moved),
            "moved_object_names": names,
            "targets_before": preview["state"]["targets"],
            "targets_after": targets_after,
            "geometry_snapshot_digest": _digest(after["geometry"]),
            "boundaries_preserved": after["boundaries"] == preview["state"]["boundaries"],
            "mesh_operations_preserved": (
                after["mesh_operations"] == preview["state"]["mesh_operations"]
            ),
            "active_coordinate_system_preserved": (
                after["active_coordinate_system"]
                == preview["state"]["active_coordinate_system"]
            ),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_geometry_rotate_preview(
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
            raise LiveBackendError("HFSS geometry rotation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot rotate HFSS geometry while a simulation is running")
        spec = _normalize_hfss_geometry_rotations(args)
        active_coordinate_system = _hfss_active_coordinate_system(app)
        if active_coordinate_system.casefold() != "global":
            raise LiveBackendError(
                "HFSS geometry rotation requires Global to be the active coordinate system"
            )
        state = _hfss_geometry_rotation_state(app, spec["names"])
        by_name = {item["name"].casefold(): item for item in state["geometry"]}
        canonical_rotations = []
        targets = []
        for rotation in spec["rotations"]:
            record = by_name.get(rotation["name"].casefold())
            if record is None:
                raise LiveBackendError(
                    "HFSS geometry rotation names must already exist in the current design"
                )
            if record["name"] != rotation["name"]:
                raise LiveBackendError(
                    "HFSS geometry rotation names must preserve exact object-name case"
                )
            _validate_hfss_geometry_rotation_target(record, rotation)
            canonical_rotations.append({**rotation, "name": record["name"]})
            targets.append(record)
        spec = {
            **spec,
            "rotations": canonical_rotations,
            "names": [item["name"] for item in canonical_rotations],
            "model_units": state["model_units"],
            "coordinate_system": "Global",
            "rotation_origin": [0.0, 0.0, 0.0],
            "angle_units": "deg",
        }
        state = _hfss_geometry_rotation_state(app, spec["names"])
        state_digest = _digest(state)
        preview_id = "geometry-rotate-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_geometry_rotate",
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
            "boundary_count": len(state["boundaries"]),
            "mesh_operation_count": len(state["mesh_operations"]),
            "snapshot_digest": state_digest,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_geometry_rotate_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_geometry_rotate", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot rotate HFSS geometry while a simulation is running")
        names = list(preview["spec"]["names"])
        current = _hfss_geometry_rotation_state(app, names)
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS geometry rotation preview")

        rotated: list[dict[str, Any]] = []
        try:
            modeler = _safe_attribute(app, "modeler")
            rotator = getattr(modeler, "rotate", None)
            if not callable(rotator):
                raise LiveBackendError("HFSS geometry rotation API is unavailable")
            for rotation in preview["spec"]["rotations"]:
                if rotator(
                    [rotation["name"]],
                    rotation["axis"],
                    angle=rotation["angle_degrees"],
                    units="deg",
                ) is not True:
                    raise LiveBackendError(
                        f"HFSS geometry rotation returned false: {rotation['name']}"
                    )
                rotated.append(rotation)

            after = _hfss_geometry_rotation_state(app, names)
            _verify_hfss_geometry_rotation_state(
                preview["state"],
                after,
                rotations=preview["spec"]["rotations"],
            )
        except Exception as exc:
            rollback = _rollback_hfss_geometry_rotations(
                app,
                rotated,
                before_state=preview["state"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS geometry rotation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS geometry rotation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        after_by_name = {item["name"]: item for item in after["geometry"]}
        targets_after = [after_by_name[name] for name in names]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **preview["spec"],
            "rotated_object_count": len(rotated),
            "rotated_object_names": names,
            "targets_before": preview["state"]["targets"],
            "targets_after": targets_after,
            "geometry_snapshot_digest": _digest(after["geometry"]),
            "boundaries_preserved": after["boundaries"] == preview["state"]["boundaries"],
            "mesh_operations_preserved": (
                after["mesh_operations"] == preview["state"]["mesh_operations"]
            ),
            "active_coordinate_system_preserved": (
                after["active_coordinate_system"]
                == preview["state"]["active_coordinate_system"]
            ),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _hfss_antipad_subtract_preview(
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
            raise LiveBackendError("HFSS anti-pad subtraction requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot subtract an HFSS anti-pad while a simulation is running")
        if _hfss_active_coordinate_system(app).casefold() != "global":
            raise LiveBackendError("HFSS anti-pad subtraction requires Global to be active")
        spec = _normalize_hfss_antipad_subtract(args)
        state = _hfss_antipad_subtract_state(app, spec["blank_object_name"])
        blank = _exact_hfss_geometry_target(state, spec["blank_object_name"])
        spec = _complete_hfss_antipad_spec(
            spec,
            blank,
            state["model_units"],
            state["blank_material"],
        )
        existing_names = {item["name"].casefold(): item["name"] for item in state["geometry"]}
        conflict = existing_names.get(spec["tool_name"].casefold())
        if conflict is not None:
            raise LiveBackendError(f"HFSS anti-pad tool object already exists: {conflict}")
        state = _hfss_antipad_subtract_state(app, spec["blank_object_name"])
        state_digest = _digest(state)
        preview_id = "hfss-antipad-preview-" + _digest(
            {"spec": spec, "state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_antipad_subtract",
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
            "blank_before": blank,
            "boundary_count": len(state["boundaries"]),
            "mesh_operation_count": len(state["mesh_operations"]),
            "snapshot_digest": state_digest,
            "approval_required": True,
            "automatic_rollback_on_failure": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_antipad_subtract_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_antipad_subtract", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot subtract an HFSS anti-pad while a simulation is running")
        spec = preview["spec"]
        current = _hfss_antipad_subtract_state(app, spec["blank_object_name"])
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS anti-pad subtraction preview")

        created_tool_name = ""
        subtract_attempted = False
        try:
            modeler = _safe_attribute(app, "modeler")
            create_cylinder = getattr(modeler, "create_cylinder", None)
            subtract = getattr(modeler, "subtract", None)
            if not callable(create_cylinder) or not callable(subtract):
                raise LiveBackendError("HFSS cylinder or subtract API is unavailable")
            tool = create_cylinder(
                "Z",
                spec["tool_origin"],
                spec["radius"],
                spec["tool_height"],
                num_sides=0,
                name=spec["tool_name"],
                material="vacuum",
            )
            created_name = str(_safe_attribute(tool, "name") or "").strip()
            if not created_name:
                names = [str(item) for item in list(getattr(modeler, "object_names", []) or [])]
                created_name = spec["tool_name"] if spec["tool_name"] in names else ""
            created_tool_name = created_name
            if created_name != spec["tool_name"]:
                raise LiveBackendError("HFSS anti-pad cylinder returned an unexpected name")
            try:
                tool.solve_inside = True
            except Exception as exc:
                raise LiveBackendError("HFSS anti-pad tool solve-inside assignment failed") from exc
            subtract_attempted = True
            if subtract(
                spec["blank_object_name"],
                spec["tool_name"],
                keep_originals=False,
            ) is not True:
                raise LiveBackendError("HFSS anti-pad subtract returned false")
            after = _hfss_antipad_subtract_state(app, spec["blank_object_name"])
            _verify_hfss_antipad_subtract_state(
                preview["state"],
                after,
                spec=spec,
            )
        except Exception as exc:
            rollback = _rollback_hfss_antipad_subtract(
                app,
                spec,
                before_state=preview["state"],
                created_tool_name=created_tool_name,
                subtract_attempted=subtract_attempted,
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS anti-pad subtraction failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS anti-pad subtraction failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        blank_after = _exact_hfss_geometry_target(after, spec["blank_object_name"])
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "blank_before": _exact_hfss_geometry_target(
                preview["state"], spec["blank_object_name"]
            ),
            "blank_after": blank_after,
            "removed_volume": _canonical_hfss_geometry_value(
                float(_exact_hfss_geometry_target(
                    preview["state"], spec["blank_object_name"]
                )["volume"]) - float(blank_after["volume"])
            ),
            "tool_deleted": True,
            "boundaries_preserved": after["boundaries"] == preview["state"]["boundaries"],
            "mesh_operations_preserved": (
                after["mesh_operations"] == preview["state"]["mesh_operations"]
            ),
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

    def _hfss_port_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS port inventory requires an HFSS 3D design")
        max_items = _bounded_integer(
            args.get("max_items", 100),
            "max_items",
            minimum=1,
            maximum=500,
        )
        boundaries = _hfss_port_boundary_snapshot(app)
        ports = [item for item in boundaries if item["kind"] in {"wave_port", "lumped_port"}]
        selected = ports[:max_items]
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "port_count": len(ports),
            "returned_count": len(selected),
            "truncated": len(ports) > len(selected),
            "ports": selected,
            "snapshot_digest": _digest({"boundaries": boundaries}),
            "design_unchanged": True,
        }

    def _hfss_boundary_preview(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(
            target,
            "hfss",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != "hfss":
            raise LiveBackendError("HFSS boundary creation requires an HFSS 3D design")
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS boundaries or ports while a simulation is running")
        spec = _normalize_hfss_boundary_spec(args)
        solution_type = str(_safe_attribute(app, "solution_type") or "").strip()
        if spec["boundary_kind"] in {"wave_port", "lumped_port"} and not _modal_port_solution_supported(
            solution_type
        ):
            raise LiveBackendError(
                "typed HFSS port creation currently requires a DrivenModal solution: "
                + solution_type
            )
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        target_snapshot = _hfss_boundary_target_snapshot(geometry, spec)
        _validate_hfss_boundary_target(spec, target_snapshot)
        boundaries = _hfss_port_boundary_snapshot(app)
        existing_casefold = {item["name"].casefold(): item["name"] for item in boundaries}
        if spec["boundary_name"].casefold() in existing_casefold:
            raise LiveBackendError(
                "HFSS boundary or port already exists: "
                + existing_casefold[spec["boundary_name"].casefold()]
            )
        resolved_integration_line = None
        if spec["boundary_kind"] in {"wave_port", "lumped_port"}:
            assignment = (
                spec["assignment_face_ids"][0]
                if spec["boundary_kind"] == "wave_port"
                else spec["assignment_object_name"]
            )
            resolved_integration_line = _hfss_port_integration_line(
                app,
                assignment,
                spec["options"]["integration_line_direction"],
            )
        model_units = str(_safe_attribute(app.modeler, "model_units") or "").strip()
        state = {
            "solution_type": solution_type,
            "model_units": model_units,
            "geometry": geometry["snapshot_digest"],
            "boundaries": boundaries,
        }
        state_digest = _digest(state)
        preview_id = "boundary-preview-" + _digest(
            spec
            | {
                "resolved_integration_line": resolved_integration_line,
                "state": state_digest,
            }
        )[:24]
        self._previews[preview_id] = {
            "kind": "hfss_boundary",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "resolved_integration_line": resolved_integration_line,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "solution_type": solution_type,
            "target_geometry": target_snapshot,
            "resolved_integration_line": resolved_integration_line,
            "snapshot_digest": state_digest,
            "geometry_digest": geometry["snapshot_digest"],
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _hfss_boundary_apply(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "hfss_boundary", target)
        app = self._app(target, "hfss", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create HFSS boundaries or ports while a simulation is running")
        geometry = self._hfss_geometry_inventory(
            target,
            {"project_name": app.project_name, "design_name": app.design_name},
        )
        current = {
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "model_units": str(_safe_attribute(app.modeler, "model_units") or "").strip(),
            "geometry": geometry["snapshot_digest"],
            "boundaries": _hfss_port_boundary_snapshot(app),
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale HFSS boundary preview")
        spec = preview["spec"]
        created_name = ""
        try:
            boundary = _create_hfss_boundary(
                app,
                spec,
                spec["assignment_face_ids"],
                resolved_integration_line=preview["resolved_integration_line"],
            )
            created_name = str(getattr(boundary, "name", "") or "") if boundary else ""
            if boundary is None or created_name != spec["boundary_name"]:
                raise LiveBackendError("HFSS boundary creation returned an unexpected name")
            after_boundaries = _hfss_port_boundary_snapshot(app)
            readback_by_name = {item["name"]: item for item in after_boundaries}
            readback = readback_by_name.get(spec["boundary_name"])
            if readback is None:
                raise LiveBackendError("HFSS boundary readback is missing")
            _verify_hfss_boundary_readback(
                spec,
                readback,
                preview["resolved_integration_line"],
            )
            before_names = {item["name"] for item in preview["state"]["boundaries"]}
            after_names = {item["name"] for item in after_boundaries}
            if after_names != before_names | {spec["boundary_name"]}:
                raise LiveBackendError("unexpected HFSS boundary inventory change")
        except Exception as exc:
            rollback = _rollback_hfss_boundary(
                app,
                created_name or spec["boundary_name"],
                before_boundaries=preview["state"]["boundaries"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"HFSS boundary creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"HFSS boundary creation failed: {type(exc).__name__}: {exc}"
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
        try:
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
        except Exception as exc:
            paths = _layout_native_line_records(app, selector)
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

    def _layout_material_create_assign_preview(
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
        if str(_safe_attribute(app, "design_type") or "").strip().casefold() != (
            "hfss 3d layout design"
        ):
            raise LiveBackendError(
                "3D Layout material creation and assignment requires an HFSS 3D Layout design"
            )
        if _simulation_running(app):
            raise LiveBackendError(
                "cannot create or assign 3D Layout materials while a simulation is running"
            )
        spec = _normalize_layout_material_create_assign_spec(args)
        material_catalog = _hfss_material_catalog_snapshot(app)
        by_name = {
            item["canonical_name"].casefold(): item
            for item in material_catalog["materials"]
        }
        existing = by_name.get(spec["material_name"].casefold())
        if existing is not None:
            raise LiveBackendError(
                f"3D Layout project material already exists: {existing['canonical_name']}"
            )
        library_name = _hfss_existing_material_name(app, spec["material_name"])
        if library_name:
            raise LiveBackendError(
                "3D Layout material name collides with an AEDT material library entry: "
                f"{library_name}"
            )
        stackup = _layout_full_stackup_snapshot(app)
        layer = _layout_stackup_layer_record(stackup, spec["layer_name"])
        spec["expected_material_class"] = _validate_layout_material_assignment_role(
            spec,
            layer,
        )
        state = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "material_catalog": material_catalog,
            "stackup": stackup,
        }
        state_digest = _digest(state)
        preview_id = "layout-material-create-assign-preview-" + _digest(
            spec | {"state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "layout_material_create_assign",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
            "layer": layer,
        }
        return {
            "preview_id": preview_id,
            **spec,
            "layer": layer,
            "before_assignment": layer[spec["assignment_field"]],
            "after_assignment": spec["material_name"],
            "existing_material_count": len(material_catalog["materials"]),
            "stackup_layer_count": len(stackup),
            "snapshot_digest": state_digest,
            "approval_required": True,
            "automatic_rollback_on_failure": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _layout_material_create_assign_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(
            preview_id,
            "layout_material_create_assign",
            target,
        )
        app = self._app(
            target,
            "layout",
            preview["project_name"],
            preview["design_name"],
        )
        if _simulation_running(app):
            raise LiveBackendError(
                "cannot create or assign 3D Layout materials while a simulation is running"
            )
        current = {
            "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
            "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
            "material_catalog": _hfss_material_catalog_snapshot(app),
            "stackup": _layout_full_stackup_snapshot(app),
        }
        if _digest(current) != preview["digest"]:
            raise LiveBackendError(
                "stale 3D Layout material create-and-assign preview"
            )

        spec = preview["spec"]
        before_catalog = preview["state"]["material_catalog"]
        before_stackup = preview["state"]["stackup"]
        created_name = ""
        try:
            materials = _safe_attribute(app, "materials")
            add_material = getattr(materials, "add_material", None)
            if not callable(add_material):
                raise LiveBackendError(
                    "3D Layout project material creation API is unavailable"
                )
            properties = {
                name: spec[name]
                for name in (
                    "permittivity",
                    "permeability",
                    "conductivity",
                    "dielectric_loss_tangent",
                    "magnetic_loss_tangent",
                )
            }
            material = add_material(spec["material_name"], properties=properties)
            created_name = str(_safe_attribute(material, "name") or "").strip()
            if not material or created_name != spec["material_name"]:
                raise LiveBackendError(
                    "3D Layout material creation returned an unexpected name"
                )
            if spec["appearance"] is not None:
                material.material_appearance = list(spec["appearance"])
                update = getattr(material, "update", None)
                if not callable(update) or update() is False:
                    raise LiveBackendError(
                        "3D Layout material appearance update returned false"
                    )

            layer_object = _layout_stackup_layer_object(
                app,
                name=spec["layer_name"],
                expected_id=preview["layer"]["id"],
                expected_type=preview["layer"]["type"],
            )
            setattr(layer_object, spec["assignment_field"], spec["material_name"])
            _restore_layout_layer_native_color(layer_object, preview["layer"])

            after_catalog = _hfss_material_catalog_snapshot(app)
            after_stackup = _layout_full_stackup_snapshot(app)
            before_names = {
                item["canonical_name"] for item in before_catalog["materials"]
            }
            after_names = {
                item["canonical_name"] for item in after_catalog["materials"]
            }
            if after_names != before_names | {spec["material_name"]}:
                raise LiveBackendError(
                    "unexpected 3D Layout project material catalog change"
                )
            material_readback = next(
                (
                    item
                    for item in after_catalog["materials"]
                    if item["canonical_name"] == spec["material_name"]
                ),
                None,
            )
            if material_readback is None:
                raise LiveBackendError("3D Layout material readback is missing")
            layer_readback = _layout_stackup_layer_record(
                after_stackup,
                spec["layer_name"],
            )
            _verify_layout_material_create_assign_readback(
                spec,
                preview["layer"],
                material_readback,
                layer_readback,
                before_stackup=before_stackup,
                after_stackup=after_stackup,
            )
        except Exception as exc:
            rollback = _rollback_layout_material_create_assign(
                app,
                spec,
                preview["layer"],
                created_name or spec["material_name"],
                before_catalog=before_catalog,
                before_stackup=before_stackup,
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    "3D Layout material create-and-assign failed and rollback is incomplete: "
                    f"{rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                "3D Layout material create-and-assign failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            **spec,
            "created_material_name": spec["material_name"],
            "material": material_readback,
            "layer": layer_readback,
            "before_assignment": preview["layer"][spec["assignment_field"]],
            "after_assignment": layer_readback[spec["assignment_field"]],
            "material_count": len(after_catalog["materials"]),
            "stackup_layer_count": len(after_stackup),
            "material_catalog_digest": _digest(after_catalog),
            "stackup_digest": _digest(after_stackup),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _layout_via_create_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        project = _required(args, "project_name")
        design = _required(args, "design_name")
        app = self._app(target, "layout", project, design)
        if _simulation_running(app):
            raise LiveBackendError("cannot create 3D Layout vias while a simulation is running")
        spec = _normalize_layout_via_create_spec(args)
        state = _layout_via_create_state(app, spec)
        state_digest = _digest(state)
        preview_id = "layout-via-create-preview-" + _digest(
            {"state": state, "spec": spec}
        )[:24]
        self._previews[preview_id] = {
            "kind": "layout_via_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "vias": spec["vias"],
            "via_count": len(spec["vias"]),
            "model_units": state["model_units"],
            "snapshot_digest": state_digest,
            "dependency_digest": state_digest,
            "dependency_summary": {
                "padstacks": sorted(state["padstacks"]),
                "signal_layers": sorted(state["signal_layers"]),
                "nets": sorted(state["nets"]),
            },
            "approval_required": True,
            "automatic_rollback_on_failure": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _layout_via_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "layout_via_create", target)
        app = self._app(
            target,
            "layout",
            preview["project_name"],
            preview["design_name"],
        )
        if _simulation_running(app):
            raise LiveBackendError("cannot create 3D Layout vias while a simulation is running")
        spec = preview["spec"]
        try:
            current_state = _layout_via_create_state(app, spec)
        except LiveBackendError as exc:
            raise LiveBackendError("stale 3D Layout via create preview") from exc
        if _digest(current_state) != preview["digest"]:
            raise LiveBackendError("stale 3D Layout via create preview")

        created_names: list[str] = []
        try:
            create_via = getattr(_safe_attribute(app, "modeler"), "create_via", None)
            if not callable(create_via):
                raise LiveBackendError("3D Layout via creation API is unavailable")
            for via_spec in spec["vias"]:
                created = create_via(
                    name=via_spec["name"],
                    padstack=via_spec["padstack"],
                    x=via_spec["x"],
                    y=via_spec["y"],
                    rotation=via_spec["rotation_degrees"],
                    hole_diam=via_spec["hole_diameter"],
                    top_layer=via_spec["top_layer"],
                    bot_layer=via_spec["bottom_layer"],
                    net=via_spec["net_name"],
                )
                created_name = str(_safe_attribute(created, "name") or "").strip()
                if created_name:
                    created_names.append(created_name)
                if not created or created_name != via_spec["name"]:
                    raise LiveBackendError(
                        f"3D Layout via creation returned an unexpected name: {via_spec['name']}"
                    )
                # PyAEDT 1.3.0 passes vrotation to CreateVia, but AEDT 2026.1
                # still reports 0deg. Set and verify the public Angle property.
                created.angle = f"{via_spec['rotation_degrees']}deg"
                created.lock_position = via_spec["lock_position"]

            readback = [
                _layout_native_via_record(app, via_spec["name"])
                for via_spec in spec["vias"]
            ]
            _verify_layout_via_create_readback(
                app,
                spec,
                readback,
                before_state=preview["state"],
            )
        except Exception as exc:
            rollback = _rollback_layout_via_create(
                app,
                spec,
                created_names,
                before_state=preview["state"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    "3D Layout via creation failed and rollback is incomplete: "
                    f"{rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"3D Layout via creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "vias": readback,
            "via_count": len(readback),
            "model_units": preview["state"]["model_units"],
            "dependency_digest": preview["digest"],
            "readback_digest": _digest(readback),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _layout_via_update_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        project = _required(args, "project_name")
        design = _required(args, "design_name")
        app = self._app(target, "layout", project, design)
        if _simulation_running(app):
            raise LiveBackendError("cannot update 3D Layout vias while a simulation is running")
        spec = _normalize_layout_via_update_spec(args)
        state = _layout_via_update_state(app, spec)
        state_digest = _digest(state)
        preview_id = "layout-via-update-preview-" + _digest(
            {"state": state, "spec": spec}
        )[:24]
        self._previews[preview_id] = {
            "kind": "layout_via_update",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "updates": spec["updates"],
            "before": state["vias"],
            "via_count": len(spec["updates"]),
            "model_units": state["model_units"],
            "snapshot_digest": state_digest,
            "approval_required": True,
            "automatic_rollback_on_failure": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _layout_via_update_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "layout_via_update", target)
        app = self._app(
            target,
            "layout",
            preview["project_name"],
            preview["design_name"],
        )
        if _simulation_running(app):
            raise LiveBackendError("cannot update 3D Layout vias while a simulation is running")
        spec = preview["spec"]
        try:
            current_state = _layout_via_update_state(app, spec)
        except LiveBackendError as exc:
            raise LiveBackendError("stale 3D Layout via update preview") from exc
        if _digest(current_state) != preview["digest"]:
            raise LiveBackendError("stale 3D Layout via update preview")

        before_by_name = {item["name"]: item for item in preview["state"]["vias"]}
        touched_names: list[str] = []
        try:
            for update in spec["updates"]:
                name = update["name"]
                before = before_by_name[name]
                via = _layout_via_object(app, name)
                touched_names.append(name)
                changes_position = "location" in update or "rotation_degrees" in update
                if changes_position and before["lock_position"]:
                    via.lock_position = False
                if "net_name" in update and update["net_name"] != before["net_name"]:
                    via.net_name = update["net_name"]
                if "location" in update and not _layout_locations_equal(
                    before["location"], update["location"]
                ):
                    via.location = list(update["location"])
                if "rotation_degrees" in update and not _layout_angles_equal(
                    float(before["rotation_degrees"]),
                    float(update["rotation_degrees"]),
                ):
                    via.angle = f"{update['rotation_degrees']}deg"
                if "lock_position" in update:
                    via.lock_position = update["lock_position"]
                elif changes_position and before["lock_position"]:
                    via.lock_position = True

            readback = [
                _layout_native_via_record(app, update["name"])
                for update in spec["updates"]
            ]
            _verify_layout_via_update_readback(
                app,
                spec,
                readback,
                before_state=preview["state"],
            )
        except Exception as exc:
            rollback = _rollback_layout_via_update(
                app,
                spec,
                touched_names,
                before_state=preview["state"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    "3D Layout via update failed and rollback is incomplete: "
                    f"{rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"3D Layout via update failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "updates": spec["updates"],
            "before": preview["state"]["vias"],
            "vias": readback,
            "via_count": len(readback),
            "model_units": preview["state"]["model_units"],
            "snapshot_digest": preview["digest"],
            "readback_digest": _digest(readback),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _layout_via_delete_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        project = _required(args, "project_name")
        design = _required(args, "design_name")
        app = self._app(target, "layout", project, design)
        if _simulation_running(app):
            raise LiveBackendError("cannot delete 3D Layout vias while a simulation is running")
        spec = _normalize_layout_via_delete_spec(args)
        state = _layout_via_delete_state(app, spec)
        state_digest = _digest(state)
        preview_id = "layout-via-delete-preview-" + _digest(
            {"state": state, "spec": spec}
        )[:24]
        self._previews[preview_id] = {
            "kind": "layout_via_delete",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "names": spec["names"],
            "before": state["vias"],
            "via_count": len(spec["names"]),
            "model_units": state["model_units"],
            "snapshot_digest": state_digest,
            "approval_required": True,
            "automatic_rollback_on_failure": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _layout_via_delete_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "layout_via_delete", target)
        app = self._app(
            target,
            "layout",
            preview["project_name"],
            preview["design_name"],
        )
        if _simulation_running(app):
            raise LiveBackendError("cannot delete 3D Layout vias while a simulation is running")
        spec = preview["spec"]
        try:
            current_state = _layout_via_delete_state(app, spec)
        except LiveBackendError as exc:
            raise LiveBackendError("stale 3D Layout via delete preview") from exc
        if _digest(current_state) != preview["digest"]:
            raise LiveBackendError("stale 3D Layout via delete preview")

        deleted_names: list[str] = []
        try:
            editor = _layout_modeler_editor(app)
            delete = getattr(editor, "Delete", None)
            if not callable(delete):
                raise LiveBackendError("3D Layout native via delete API is unavailable")
            for name in spec["names"]:
                try:
                    delete([name])
                except Exception:
                    _invalidate_layout_via_cache(app, [name])
                    if not _layout_native_name_matches(app, name):
                        deleted_names.append(name)
                    raise
                _invalidate_layout_via_cache(app, [name])
                if _layout_native_name_matches(app, name):
                    raise LiveBackendError(f"3D Layout via remained after delete: {name}")
                deleted_names.append(name)
            _verify_layout_via_delete_readback(
                app,
                spec,
                before_state=preview["state"],
            )
        except Exception as exc:
            rollback = _rollback_layout_via_delete(
                app,
                spec,
                deleted_names,
                before_state=preview["state"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    "3D Layout via delete failed and rollback is incomplete: "
                    f"{rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"3D Layout via delete failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "names": spec["names"],
            "before": preview["state"]["vias"],
            "deleted_names": deleted_names,
            "via_count": len(deleted_names),
            "model_units": preview["state"]["model_units"],
            "snapshot_digest": preview["digest"],
            "absence_digest": _digest(
                {name: _layout_native_name_matches(app, name) for name in spec["names"]}
            ),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _layout_antipad_circle_create_preview(
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
        if _simulation_running(app):
            raise LiveBackendError("cannot create 3D Layout anti-pads while a simulation is running")
        spec = _normalize_layout_antipad_circle_create(args)
        state = _layout_antipad_circle_create_state(app, spec)
        canonical = []
        owners = {item["name"].casefold(): item for item in state["owners"]}
        for item in spec["voids"]:
            owner = owners.get(item["owner_name"].casefold())
            if owner is None:
                raise LiveBackendError(f"3D Layout anti-pad owner does not exist: {item['owner_name']}")
            if owner["name"] != item["owner_name"]:
                raise LiveBackendError(
                    f"owner_name must match AEDT case exactly: {owner['name']}"
                )
            _validate_layout_antipad_inside_owner(item, owner)
            canonical.append({**item, "owner_name": owner["name"], "layer_name": owner["layer_name"]})
        spec = {**spec, "voids": canonical, "names": [item["name"] for item in canonical]}
        state = _layout_antipad_circle_create_state(app, spec)
        state_digest = _digest(state)
        preview_id = "layout-antipad-preview-" + _digest(
            {"spec": spec, "state": state_digest}
        )[:24]
        self._previews[preview_id] = {
            "kind": "layout_antipad_circle_create",
            "target": target,
            "project_name": app.project_name,
            "design_name": app.design_name,
            "spec": spec,
            "state": state,
            "digest": state_digest,
        }
        return {
            "preview_id": preview_id,
            "project_name": app.project_name,
            "design_name": app.design_name,
            **spec,
            "void_count": len(spec["voids"]),
            "owners": state["owners"],
            "model_units": state["model_units"],
            "verification_scope": state["verification_scope"],
            "global_inventory_status": state["global_inventory_status"],
            "target_presence_scope": state["target_presence_scope"],
            "global_side_effects_unverified": state["global_side_effects_unverified"],
            "snapshot_digest": state_digest,
            "approval_required": True,
            "automatic_rollback_on_failure": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _layout_antipad_circle_create_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "layout_antipad_circle_create", target)
        app = self._app(target, "layout", preview["project_name"], preview["design_name"])
        if _simulation_running(app):
            raise LiveBackendError("cannot create 3D Layout anti-pads while a simulation is running")
        spec = preview["spec"]
        try:
            current = _layout_antipad_circle_create_state(app, spec)
        except LiveBackendError as exc:
            raise LiveBackendError("stale 3D Layout anti-pad create preview") from exc
        if _digest(current) != preview["digest"]:
            raise LiveBackendError("stale 3D Layout anti-pad create preview")

        try:
            editor = _layout_modeler_editor(app)
            create = getattr(editor, "CreateCircleVoid", None)
            if not callable(create):
                raise LiveBackendError("3D Layout native circle-void API is unavailable")
            units = preview["state"]["model_units"]
            for item in spec["voids"]:
                result = create(
                    [
                        "NAME:Contents",
                        "owner:=",
                        item["owner_name"],
                        "circle voidGeometry:=",
                        [
                            "Name:=",
                            item["name"],
                            "LayerName:=",
                            item["layer_name"],
                            "lw:=",
                            "0",
                            "x:=",
                            f"{item['center'][0]:.15g}{units}",
                            "y:=",
                            f"{item['center'][1]:.15g}{units}",
                            "r:=",
                            f"{item['radius']:.15g}{units}",
                        ],
                    ]
                )
                created_name = str(result or "").strip()
                if created_name != item["name"]:
                    raise LiveBackendError(
                        f"3D Layout anti-pad creation returned an unexpected name: {created_name}"
                    )
            readback = [_layout_native_circle_void_record(app, item) for item in spec["voids"]]
            _verify_layout_antipad_circle_create_state(
                app,
                spec,
                readback,
                before_state=preview["state"],
            )
        except Exception as exc:
            rollback = _rollback_layout_antipad_circle_create(
                app,
                spec,
                before_state=preview["state"],
            )
            if not rollback["complete"]:
                raise LiveBackendError(
                    f"3D Layout anti-pad creation failed and rollback is incomplete: {rollback}"
                ) from exc
            if isinstance(exc, LiveBackendError):
                raise
            raise LiveBackendError(
                f"3D Layout anti-pad creation failed: {type(exc).__name__}: {exc}"
            ) from exc

        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "voids": readback,
            "void_count": len(readback),
            "model_units": preview["state"]["model_units"],
            "verification_scope": preview["state"]["verification_scope"],
            "global_inventory_status": preview["state"]["global_inventory_status"],
            "target_presence_scope": preview["state"]["target_presence_scope"],
            "global_side_effects_unverified": preview["state"]["global_side_effects_unverified"],
            "readback_digest": _digest(readback),
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
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
                        "reason": f"{type(exc).__name__}: {attribute} API unavailable: {exc}",
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
        errors: list[str] = []
        for category, attribute in attributes.items():
            try:
                value = getattr(app.modeler, attribute)
                names = sorted(str(item) for item in (value.keys() if isinstance(value, dict) else value or []))
                categories[category] = {"count": len(names), "names": names}
            except Exception as exc:
                categories[category] = {"count": 0, "names": [], "status": "unavailable"}
                unavailable.append(category)
                errors.append(f"{category}: {type(exc).__name__}: {exc}")
        capability_failure = _layout_inventory_failure_reasons(errors)
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "categories": categories,
            "unavailable_categories": unavailable,
            "capability_status": "partial" if unavailable else "available",
            "retry_recommended": bool(unavailable),
            "fallback_hint": "Some PyAEDT collection wrappers were unavailable; use a targeted native read before declaring an AEDT API unsupported." if capability_failure else "",
            "design_unchanged": True,
        }

    def _layout_signal_via_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        """Return signal vias and their native location/layer state without PyAEDT collections."""

        app = self._app(
            target,
            "layout",
            _required(args, "project_name"),
            _required(args, "design_name"),
        )
        max_items = args.get("max_items", 200)
        if type(max_items) is not int or not 1 <= max_items <= 500:
            raise LiveBackendError("max_items must be an integer between 1 and 500")
        crossing_layer = str(args.get("crossing_layer") or "").strip()
        editor = _layout_modeler_editor(app)
        get_signal_nets = getattr(editor, "GetNetClassNets", None)
        filter_objects = getattr(editor, "FilterObjectList", None)
        if not callable(get_signal_nets) or not callable(filter_objects):
            raise LiveBackendError("3D Layout native signal-via inventory API is unavailable")
        try:
            raw_signal_nets = get_signal_nets("Non Power/Ground")
        except Exception as exc:
            raise LiveBackendError(
                f"3D Layout signal-net inventory failed: {type(exc).__name__}: {exc}"
            ) from exc
        signal_nets = _layout_native_name_list(raw_signal_nets, label="signal-net inventory", maximum=5_000)
        via_names: set[str] = set()
        for net_name in signal_nets:
            candidates = _layout_native_find_objects(app, "Net", net_name, maximum=100_000)
            try:
                raw_vias = filter_objects("Type", "via", candidates)
            except Exception as exc:
                raise LiveBackendError(
                    f"3D Layout signal-via filter failed for {net_name}: {type(exc).__name__}: {exc}"
                ) from exc
            via_names.update(_layout_native_name_list(raw_vias, label="signal-via filter", maximum=100_000))
        names = sorted(via_names)
        if len(names) > 5_000:
            raise LiveBackendError("3D Layout signal-via inventory exceeds the 5000-item safety limit")
        native_records = _layout_native_property_records(
            app,
            "via",
            names,
            _layout_native_profile_property_ids("via", "via_target/v1") or [],
            available_names=set(names),
        )
        records = [_layout_via_target_record_from_native(record) for record in native_records]
        if crossing_layer:
            records = [
                record
                for record in records
                if record.get("values", {}).get("start_layer", {}).get("value") == crossing_layer
                or record.get("values", {}).get("stop_layer", {}).get("value") == crossing_layer
            ]
        returned = records[:max_items]
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "signal_nets": signal_nets,
            "signal_net_count": len(signal_nets),
            "crossing_layer": crossing_layer,
            "count": len(returned),
            "total_matching_count": len(records),
            "truncated": len(records) > len(returned),
            "objects": returned,
            "inventory_source": "native_oeditor",
            "status": "ok" if not any(record["status"] != "ok" for record in returned) else "partial",
            "snapshot_digest": _digest(returned),
            "design_unchanged": True,
        }

    def _layout_object_property_inventory(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        object_kind = _layout_object_kind(args)
        requested = [str(item) for item in args.get("names") or []]
        profile = str(args.get("profile") or "").strip()
        try:
            collection = dict(getattr(app.modeler, _LAYOUT_OBJECT_COLLECTIONS[object_kind]) or {})
            collection_source = "pyaedt_collection"
        except Exception as exc:
            if object_kind == "via" and profile == "via_target/v1":
                collection = {
                    name: None
                    for name in _layout_native_find_objects(app, "Type", "via", maximum=50)
                }
                collection_source = "native_oeditor_fallback"
            else:
                raise LiveBackendError(f"layout {object_kind} inventory is unavailable") from exc
        if profile:
            response = _layout_via_target_inventory(
                app,
                object_kind=object_kind,
                collection=collection,
                requested=requested,
                profile=profile,
                max_items=args.get("max_items", 25),
            )
            response["inventory_source"] = collection_source
            return response
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

    def _layout_property_schema(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        requested_kind = str(args.get("object_kind") or "").strip().casefold()
        if requested_kind and requested_kind not in _LAYOUT_NATIVE_PROPERTY_SCHEMA:
            raise LiveBackendError("object_kind is not supported by the native property bridge")
        object_kinds = _layout_native_property_schema(requested_kind or None)
        return {
            "schema_version": "layout_native_property/v1",
            "project_name": app.project_name,
            "design_name": app.design_name,
            "object_kinds": object_kinds,
            "schema_digest": _digest(object_kinds),
            "design_unchanged": True,
        }

    def _layout_properties_read(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        object_kind = str(args.get("object_kind") or "").strip().casefold()
        if object_kind not in _LAYOUT_NATIVE_PROPERTY_SCHEMA:
            raise LiveBackendError("object_kind is not supported by the native property bridge")
        names = _normalize_explicit_names(args.get("names"), field="names", maximum=50)
        profile = str(args.get("profile") or "").strip()
        raw_property_ids = args.get("property_ids")
        if bool(profile) == (raw_property_ids is not None):
            raise LiveBackendError("provide exactly one of profile or property_ids")
        if profile:
            property_ids = _layout_native_profile_property_ids(object_kind, profile)
            if property_ids is None:
                return _layout_property_not_supported_response(
                    app,
                    object_kind=object_kind,
                    names=names,
                    profile=profile,
                    unsupported_property_ids=[],
                    reason="profile_not_supported",
                )
        else:
            property_ids = _layout_native_property_ids(raw_property_ids)
            unsupported = sorted(set(property_ids).difference(_LAYOUT_NATIVE_PROPERTY_SCHEMA[object_kind]["properties"]))
            if unsupported:
                return _layout_property_not_supported_response(
                    app,
                    object_kind=object_kind,
                    names=names,
                    profile="",
                    unsupported_property_ids=unsupported,
                    reason="property_not_supported",
                )
        try:
            collection = dict(getattr(app.modeler, _LAYOUT_OBJECT_COLLECTIONS[object_kind]) or {})
            available_names: set[str] | None = set(collection)
            inventory_source = "pyaedt_collection"
        except Exception as exc:
            # An explicit-name property query can use oEditor directly. Do not
            # let a collection wrapper failure hide a working native API.
            collection = {}
            available_names = None
            inventory_source = "native_oeditor_fallback"
        records = _layout_native_property_records(
            app,
            object_kind,
            names,
            property_ids,
            available_names=available_names,
        )
        complete = all(record["status"] == "ok" for record in records)
        response = {
            "schema_version": "layout_native_property/v1",
            "project_name": app.project_name,
            "design_name": app.design_name,
            "object_kind": object_kind,
            "profile": profile,
            "property_ids": property_ids,
            "count": len(records),
            "records": records,
            "status": "ok" if complete else "partial",
            "inventory_source": inventory_source,
            "design_unchanged": True,
        }
        if len(json.dumps(response, ensure_ascii=True, default=str).encode("utf-8")) > 256 * 1024:
            raise LiveBackendError("layout native property response exceeds the approved 256 KiB limit")
        response["response_digest"] = _digest(
            {
                "object_kind": object_kind,
                "profile": profile,
                "property_ids": property_ids,
                "records": records,
            }
        )
        return response

    def _controlled_read_schema(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        from aedt_agent.controlled import read_program_schema

        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            **read_program_schema(),
            "design_unchanged": True,
        }

    def _controlled_read_execute(self, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        from aedt_agent.controlled import ControlledProgramError, execute_read_program, validate_read_program

        app = self._app(target, "layout", _required(args, "project_name"), _required(args, "design_name"))
        try:
            validation = validate_read_program(args.get("program"), product="layout")
            result = execute_read_program(app, validation)
        except ControlledProgramError as exc:
            raise LiveBackendError(str(exc)) from exc
        return {
            "project_name": app.project_name,
            "design_name": app.design_name,
            "schema_version": "controlled-aedt-read/v1",
            "design_unchanged": True,
            **result,
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
        expression = _variable_expression_input(args.get("expression"), "expression")
        app = self._app(target, product, project, design)
        state = _variable_state(app, product)
        changes = _variable_changes(
            state["variables"],
            [{"name": variable_name, "expression": expression}],
        )
        change = changes[0]
        if change["action"] == "noop":
            raise LiveBackendError("AEDT variable expression is already equal to the requested value")
        snapshot = {
            "product": product,
            "project_name": project,
            "design_name": design,
            "state": state,
        }
        digest = _digest(snapshot)
        preview_id = "live-preview-" + _digest({**snapshot, "changes": changes})[:24]
        self._previews[preview_id] = {
            "kind": "variable_upsert",
            "target": target,
            **snapshot,
            "changes": changes,
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
            "action": change["action"],
            "existed": change["existed"],
            "before_expression": change["before_expression"],
            "after_expression": expression,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
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
        current_snapshot = {
            "product": preview["product"],
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "state": _variable_state(app, preview["product"]),
        }
        if _digest(current_snapshot) != preview["digest"]:
            raise LiveBackendError("stale variable preview")
        applied = _apply_variable_changes(
            app,
            changes=preview["changes"],
            before_records=preview["state"]["variables"],
        )
        change = applied["changes"][0]
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "product": preview["product"],
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "variable_name": change["name"],
            "scope": change["scope"],
            "action": change["action"],
            "before_expression": change["before_expression"],
            "after_expression": change["readback_expression"],
            "automatic_rollback_on_failure": True,
            "project_dirty": True,
            "project_saved": False,
        }

    def _variable_batch_upsert_preview(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        product = _variable_product(args)
        project = _required(args, "project_name")
        design = _required(args, "design_name")
        max_variables = _bounded_integer(
            args.get("max_variables", 16),
            "max_variables",
            minimum=1,
            maximum=32,
        )
        requested = _normalize_variable_batch(args.get("variables"), max_variables)
        app = self._app(target, product, project, design)
        state = _variable_state(app, product)
        changes = _variable_changes(state["variables"], requested)
        changed = [item for item in changes if item["action"] != "noop"]
        if not changed:
            raise LiveBackendError("AEDT variable batch would make no changes")
        snapshot = {
            "product": product,
            "project_name": project,
            "design_name": design,
            "state": state,
        }
        digest = _digest(snapshot)
        preview_id = "variable-batch-preview-" + _digest(
            {**snapshot, "changes": changes}
        )[:24]
        self._previews[preview_id] = {
            "kind": "variable_batch_upsert",
            "target": target,
            **snapshot,
            "changes": changes,
            "digest": digest,
        }
        return {
            "preview_id": preview_id,
            "snapshot_digest": digest,
            "product": product,
            "project_name": project,
            "design_name": design,
            "design_type": state["design_type"],
            "requested_count": len(changes),
            "change_count": len(changed),
            "create_count": sum(item["action"] == "create" for item in changes),
            "update_count": sum(item["action"] == "update" for item in changes),
            "noop_count": sum(item["action"] == "noop" for item in changes),
            "changes": changes,
            "approval_required": True,
            "project_dirty": False,
            "project_saved": False,
        }

    def _variable_batch_upsert_apply(
        self,
        target: AedtTarget,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        preview_id = _required(args, "preview_id")
        preview = self._preview(preview_id, "variable_batch_upsert", target)
        app = self._app(
            target,
            preview["product"],
            preview["project_name"],
            preview["design_name"],
        )
        current_snapshot = {
            "product": preview["product"],
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "state": _variable_state(app, preview["product"]),
        }
        if _digest(current_snapshot) != preview["digest"]:
            raise LiveBackendError("stale variable batch preview")
        applied = _apply_variable_changes(
            app,
            changes=preview["changes"],
            before_records=preview["state"]["variables"],
        )
        del self._previews[preview_id]
        return {
            "status": "verified",
            "preview_id": preview_id,
            "product": preview["product"],
            "project_name": preview["project_name"],
            "design_name": preview["design_name"],
            "requested_count": len(applied["changes"]),
            "change_count": sum(
                item["action"] != "noop" for item in applied["changes"]
            ),
            "create_count": sum(
                item["action"] == "create" for item in applied["changes"]
            ),
            "update_count": sum(
                item["action"] == "update" for item in applied["changes"]
            ),
            "noop_count": sum(
                item["action"] == "noop" for item in applied["changes"]
            ),
            "changes": applied["changes"],
            "variables": applied["variables"],
            "automatic_rollback_on_failure": True,
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


def _open_aedt_source_fingerprint(path: Path) -> dict[str, Any]:
    """Return a deterministic content fingerprint for the source being backed up."""
    source = path.resolve()
    if source.is_file():
        files = [source]
        companion = source.with_suffix(".aedb")
        if companion.is_dir():
            files.extend(sorted(item for item in companion.rglob("*") if item.is_file()))
        root = source.parent
        kind = "aedt_project"
    elif source.is_dir():
        files = sorted(item for item in source.rglob("*") if item.is_file())
        root = source
        kind = "directory"
    else:
        raise LiveBackendError("cannot fingerprint a missing AEDT project or AEDB path")
    entries = []
    for item in files:
        digest = hashlib.sha256()
        with item.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        relative = item.relative_to(root).as_posix()
        entries.append({"path": relative, "bytes": item.stat().st_size, "sha256": digest.hexdigest()})
    return {
        "algorithm": "sha256-file-manifest-v1",
        "kind": kind,
        "file_count": len(entries),
        "total_bytes": sum(int(item["bytes"]) for item in entries),
        "digest": _digest(entries),
    }


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


def _variable_expression_input(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LiveBackendError(f"{field} must be an AEDT expression string")
    expression = value.strip()
    if not expression:
        raise LiveBackendError(f"{field} must not be empty")
    if len(expression) > 512:
        raise LiveBackendError(f"{field} exceeds the maximum length of 512 characters")
    if re.search(r"[\x00-\x1f\x7f]", expression):
        raise LiveBackendError(f"{field} must not contain control characters")
    return expression


def _normalize_variable_batch(value: Any, max_variables: int) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise LiveBackendError("variables must be a non-empty list")
    if len(value) > max_variables:
        raise LiveBackendError(f"variables exceeds max_variables={max_variables}")
    normalized = []
    seen: dict[str, str] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"variables[{index}] must be an object")
        unsupported = sorted(set(raw).difference({"name", "expression"}))
        if unsupported:
            raise LiveBackendError(
                f"variables[{index}] contains unsupported field: {unsupported[0]}"
            )
        name = _variable_name({"variable_name": raw.get("name")})
        folded = name.casefold()
        if folded in seen:
            raise LiveBackendError(
                f"variables contains duplicate case-insensitive name: {name}"
            )
        seen[folded] = name
        normalized.append(
            {
                "name": name,
                "expression": _variable_expression_input(
                    raw.get("expression"),
                    f"variables[{index}].expression",
                ),
            }
        )
    return normalized


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
        for name, value in sorted(
            dict(getattr(app.variable_manager, "variables", {}) or {}).items(),
            key=lambda item: str(item[0]),
        )
    ]


def _variable_state(app: Any, product: str) -> dict[str, Any]:
    if _simulation_running(app):
        raise LiveBackendError("cannot change AEDT variables while a simulation is running")
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    normalized_type = re.sub(r"[\s_-]+", "", design_type).casefold()
    expected = "hfss" if product == "hfss" else "hfss3dlayoutdesign"
    if normalized_type != expected:
        raise LiveBackendError(
            f"product {product} does not match active design type: {design_type or 'unavailable'}"
        )
    return {
        "design_type": design_type,
        "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
        "variables": _variable_records(app),
    }


def _variable_changes(
    before_records: list[dict[str, str]],
    requested: list[dict[str, str]],
) -> list[dict[str, Any]]:
    by_name = {item["name"]: item for item in before_records}
    by_folded = {item["name"].casefold(): item["name"] for item in before_records}
    changes = []
    for spec in requested:
        name = spec["name"]
        conflicting = by_folded.get(name.casefold())
        if conflicting is not None and conflicting != name:
            raise LiveBackendError(
                f"AEDT variable name differs only by case from existing variable: {conflicting}"
            )
        before = by_name.get(name)
        existed = before is not None
        before_expression = before["expression"] if before else None
        if existed and _variable_expressions_equal(before_expression, spec["expression"]):
            action = "noop"
        else:
            action = "update" if existed else "create"
        changes.append(
            {
                "name": name,
                "scope": "project" if name.startswith("$") else "design",
                "action": action,
                "existed": existed,
                "before_expression": before_expression,
                "after_expression": spec["expression"],
            }
        )
    return changes


def _variable_expressions_equal(actual: Any, expected: Any) -> bool:
    actual_text = str(actual).strip()
    expected_text = str(expected).strip()
    if _normalized_expression(actual_text) == _normalized_expression(expected_text):
        return True
    quantity_pattern = re.compile(
        r"([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)\s*([A-Za-z]*)"
    )
    actual_match = quantity_pattern.fullmatch(actual_text)
    expected_match = quantity_pattern.fullmatch(expected_text)
    if not actual_match or not expected_match:
        return False
    if actual_match.group(2).casefold() != expected_match.group(2).casefold():
        return False
    return math.isclose(
        float(actual_match.group(1)),
        float(expected_match.group(1)),
        rel_tol=1e-12,
        abs_tol=1e-15,
    )


def _apply_variable_changes(
    app: Any,
    *,
    changes: list[dict[str, Any]],
    before_records: list[dict[str, str]],
) -> dict[str, Any]:
    attempted: list[dict[str, Any]] = []
    try:
        for change in changes:
            if change["action"] == "noop":
                continue
            attempted.append(change)
            if change["existed"]:
                _change_existing_variable_expression(
                    app,
                    change["name"],
                    change["after_expression"],
                )
            else:
                updated = app.variable_manager.set_variable(
                    change["name"],
                    change["after_expression"],
                    sweep=True,
                )
                if updated is False:
                    raise LiveBackendError(
                        f"failed to create AEDT variable: {change['name']}"
                    )
            current_by_name = {item["name"]: item for item in _variable_records(app)}
            readback = current_by_name.get(change["name"])
            if readback is None or not _variable_expressions_equal(
                readback["expression"],
                change["after_expression"],
            ):
                raise LiveBackendError(
                    f"AEDT variable readback verification failed: {change['name']}"
                )
        after_records = _variable_records(app)
        _verify_variable_batch_readback(before_records, after_records, changes)
    except Exception as exc:
        rollback = _rollback_variable_changes(
            app,
            attempted=attempted,
            before_records=before_records,
        )
        if not rollback["complete"]:
            raise LiveBackendError(
                f"AEDT variable update failed and rollback is incomplete: {rollback}"
            ) from exc
        if isinstance(exc, LiveBackendError):
            raise
        raise LiveBackendError(
            f"AEDT variable update failed: {type(exc).__name__}: {exc}"
        ) from exc
    after_by_name = {item["name"]: item for item in after_records}
    readback_changes = []
    for change in changes:
        record = after_by_name[change["name"]]
        readback_changes.append(
            {
                **change,
                "readback_expression": record["expression"],
            }
        )
    return {"changes": readback_changes, "variables": after_records}


def _change_existing_variable_expression(app: Any, name: str, expression: str) -> None:
    variables = dict(getattr(app.variable_manager, "variables", {}) or {})
    variable = variables.get(name)
    if variable is None:
        raise LiveBackendError(f"AEDT variable disappeared before update: {name}")
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    is_layout = re.sub(r"[\s_-]+", "", design_type).casefold() == "hfss3dlayoutdesign"
    if name.startswith("$"):
        target = getattr(app, "oproject", None) or getattr(app, "_oproject", None)
        tab_name = "ProjectVariableTab"
        prop_server = "ProjectVariables"
    else:
        target = getattr(app, "odesign", None) or getattr(app, "_odesign", None)
        circuit_parameter = False
        try:
            circuit_parameter = bool(variable.is_circuit_parameter)
        except Exception:
            circuit_parameter = False
        if is_layout and circuit_parameter:
            tab_name = "DefinitionParameterTab"
        else:
            tab_name = "LocalVariableTab"
        if is_layout:
            get_name = getattr(target, "GetName", None)
            if not callable(get_name):
                raise LiveBackendError("AEDT layout design identifier is unavailable")
            prop_server = "Instance:" + str(get_name())
        else:
            prop_server = "LocalVariables"
    change_property = getattr(target, "ChangeProperty", None)
    if not callable(change_property):
        updated = app.variable_manager.set_variable(name, expression, sweep=True)
        if updated is False:
            raise LiveBackendError(f"failed to update AEDT variable: {name}")
        return
    change_property(
        [
            "NAME:AllTabs",
            [
                f"NAME:{tab_name}",
                ["NAME:PropServers", prop_server],
                ["NAME:ChangedProps", [f"NAME:{name}", "Value:=", expression]],
            ],
        ]
    )


def _verify_variable_batch_readback(
    before_records: list[dict[str, str]],
    after_records: list[dict[str, str]],
    changes: list[dict[str, Any]],
) -> None:
    before_by_name = {item["name"]: item for item in before_records}
    after_by_name = {item["name"]: item for item in after_records}
    change_by_name = {item["name"]: item for item in changes}
    expected_names = set(before_by_name) | {
        item["name"] for item in changes if item["action"] == "create"
    }
    if set(after_by_name) != expected_names:
        raise LiveBackendError("unexpected AEDT variable inventory change")
    for name, record in after_by_name.items():
        change = change_by_name.get(name)
        expected_expression = (
            change["after_expression"] if change else before_by_name[name]["expression"]
        )
        if not _variable_expressions_equal(record["expression"], expected_expression):
            raise LiveBackendError(f"AEDT variable batch readback failed: {name}")


def _rollback_variable_changes(
    app: Any,
    *,
    attempted: list[dict[str, Any]],
    before_records: list[dict[str, str]],
) -> dict[str, Any]:
    errors = []
    for change in reversed(attempted):
        if not change["existed"]:
            continue
        try:
            _change_existing_variable_expression(
                app,
                change["name"],
                change["before_expression"],
            )
        except Exception as exc:
            errors.append(f"restore {change['name']}: {type(exc).__name__}: {exc}")
    for change in reversed(attempted):
        if change["existed"]:
            continue
        try:
            current_names = {item["name"] for item in _variable_records(app)}
            if change["name"] in current_names:
                app.variable_manager.delete_variable(change["name"])
        except Exception as exc:
            errors.append(f"delete {change['name']}: {type(exc).__name__}: {exc}")
    try:
        after_records = _variable_records(app)
    except Exception as exc:
        after_records = []
        errors.append(f"readback: {type(exc).__name__}: {exc}")
    complete = _variable_record_sets_equal(after_records, before_records)
    return {
        "complete": complete,
        "attempted_names": [item["name"] for item in attempted],
        "remaining_variables": [item["name"] for item in after_records],
        "errors": errors,
    }


def _variable_record_sets_equal(
    actual: list[dict[str, str]],
    expected: list[dict[str, str]],
) -> bool:
    actual_by_name = {item["name"]: item for item in actual}
    expected_by_name = {item["name"]: item for item in expected}
    return set(actual_by_name) == set(expected_by_name) and all(
        actual_by_name[name].get("scope") == record.get("scope")
        and _variable_expressions_equal(
            actual_by_name[name].get("expression"),
            record.get("expression"),
        )
        for name, record in expected_by_name.items()
    )


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
_HFSS_TYPED_PORT_OPTIONS = {
    "wave_port": {
        "modes",
        "renormalize",
        "deembed",
        "integration_line_direction",
        "characteristic_impedance",
    },
    "lumped_port": {
        "impedance",
        "renormalize",
        "deembed",
        "integration_line_direction",
    },
}
_HFSS_AXIS_DIRECTIONS = ("XNeg", "YNeg", "ZNeg", "XPos", "YPos", "ZPos")
_HFSS_CHARACTERISTIC_IMPEDANCES = {"Zpi", "Zpv", "Zvi", "Zwave"}
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
_SAFE_AEDT_LAYER_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. +()-]{0,127}")
_SAFE_AEDT_EXPRESSION = re.compile(r"[A-Za-z0-9_$+\-*/^().,% \t]{1,128}")
_HFSS_MATERIAL_NUMERIC_PROPERTIES = (
    "permittivity",
    "permeability",
    "conductivity",
    "dielectric_loss_tangent",
    "magnetic_loss_tangent",
)
_HFSS_MATERIAL_UPDATE_FIELDS = {
    "material_name",
    *_HFSS_MATERIAL_NUMERIC_PROPERTIES,
    "appearance",
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
_LAYOUT_NATIVE_PROPERTY_SCHEMA = {
    "via": {
        "max_objects": 50,
        "max_properties": 8,
        "properties": {
            "net": {"native_name": "Net", "value_type": "string"},
            "location": {"native_name": "Location", "value_type": "point_2d"},
            "start_layer": {"native_name": "Start Layer", "value_type": "string"},
            "stop_layer": {"native_name": "Stop Layer", "value_type": "string"},
            "padstack_definition": {"native_name": "Padstack Definition", "value_type": "string"},
            "hole_diameter": {"native_name": "HoleDiameter", "value_type": "string"},
            "angle": {"native_name": "Angle", "value_type": "string"},
            "lock_position": {"native_name": "LockPosition", "value_type": "boolean"},
        },
        "profiles": {
            "via_target/v1": ("net", "location", "start_layer", "stop_layer"),
        },
    },
}

_LAYOUT_VIA_CREATE_FIELDS = {
    "name",
    "padstack",
    "x",
    "y",
    "rotation_degrees",
    "hole_diameter",
    "top_layer",
    "bottom_layer",
    "net_name",
    "lock_position",
}
_LAYOUT_VIA_UPDATE_FIELDS = {
    "name",
    "net_name",
    "location",
    "rotation_degrees",
    "lock_position",
}
_LAYOUT_RECONSTRUCTIBLE_VIA_NATIVE_FIELDS = {
    "Type",
    "LockPosition",
    "Name",
    "Net",
    "Padstack Definition",
    "Padstack Usage",
    "Start Layer",
    "Stop Layer",
    "Backdrill Top",
    "Top Offset",
    "Backdrill Bottom",
    "Bottom Offset",
    "OverrideHoleDiameter",
    "HoleDiameter",
    "Location",
    "Angle",
}

_SAFE_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}")


def _normalize_hfss_material_create_spec(args: dict[str, Any]) -> dict[str, Any]:
    material_name = str(args.get("material_name") or "").strip()
    if not _SAFE_AEDT_MATERIAL_NAME.fullmatch(material_name):
        raise LiveBackendError("material_name must be a safe AEDT material name")
    properties = {
        "permittivity": _bounded_float(
            args.get("permittivity", 1.0),
            "permittivity",
            minimum=1e-12,
            maximum=1e9,
        ),
        "permeability": _bounded_float(
            args.get("permeability", 1.0),
            "permeability",
            minimum=1e-12,
            maximum=1e9,
        ),
        "conductivity": _bounded_float(
            args.get("conductivity", 0.0),
            "conductivity",
            minimum=0.0,
            maximum=1e12,
        ),
        "dielectric_loss_tangent": _bounded_float(
            args.get("dielectric_loss_tangent", 0.0),
            "dielectric_loss_tangent",
            minimum=0.0,
            maximum=1e6,
        ),
        "magnetic_loss_tangent": _bounded_float(
            args.get("magnetic_loss_tangent", 0.0),
            "magnetic_loss_tangent",
            minimum=0.0,
            maximum=1e6,
        ),
    }
    appearance_value = args.get("appearance")
    appearance = None
    if appearance_value is not None:
        if not isinstance(appearance_value, list) or len(appearance_value) != 4:
            raise LiveBackendError("appearance must contain [red, green, blue, transparency]")
        rgb = []
        for index, value in enumerate(appearance_value[:3]):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
                raise LiveBackendError(f"appearance[{index}] must be an integer from 0 to 255")
            rgb.append(value)
        transparency = _bounded_float(
            appearance_value[3],
            "appearance[3]",
            minimum=0.0,
            maximum=1.0,
        )
        appearance = [*rgb, transparency]
    return {
        "material_name": material_name,
        **properties,
        "appearance": appearance,
    }


def _normalize_hfss_material_update_spec(args: dict[str, Any]) -> dict[str, Any]:
    max_materials = _bounded_integer(
        args.get("max_materials", 16),
        "max_materials",
        minimum=1,
        maximum=32,
    )
    raw_updates = args.get("updates")
    if not isinstance(raw_updates, list) or not raw_updates:
        raise LiveBackendError("updates must contain at least one typed material update")
    if len(raw_updates) > max_materials:
        raise LiveBackendError(f"updates exceeds the approved maximum of {max_materials}")
    normalized = []
    seen_names = set()
    for index, raw in enumerate(raw_updates):
        field = f"updates[{index}]"
        if not isinstance(raw, dict):
            raise LiveBackendError(f"{field} must be an object")
        unsupported = sorted(set(raw).difference(_HFSS_MATERIAL_UPDATE_FIELDS))
        if unsupported:
            raise LiveBackendError(f"unsupported {field} field: {unsupported[0]}")
        material_name = str(raw.get("material_name") or "").strip()
        if not _SAFE_AEDT_MATERIAL_NAME.fullmatch(material_name):
            raise LiveBackendError(
                f"{field}.material_name must be a safe exact AEDT material name"
            )
        folded_name = material_name.casefold()
        if folded_name in seen_names:
            raise LiveBackendError("material update names must be unique case-insensitively")
        seen_names.add(folded_name)
        update: dict[str, Any] = {"material_name": material_name}
        if "permittivity" in raw:
            update["permittivity"] = _bounded_float(
                raw["permittivity"],
                f"{field}.permittivity",
                minimum=1e-12,
                maximum=1e9,
            )
        if "permeability" in raw:
            update["permeability"] = _bounded_float(
                raw["permeability"],
                f"{field}.permeability",
                minimum=1e-12,
                maximum=1e9,
            )
        if "conductivity" in raw:
            update["conductivity"] = _bounded_float(
                raw["conductivity"],
                f"{field}.conductivity",
                minimum=0.0,
                maximum=1e12,
            )
        for property_name in (
            "dielectric_loss_tangent",
            "magnetic_loss_tangent",
        ):
            if property_name in raw:
                update[property_name] = _bounded_float(
                    raw[property_name],
                    f"{field}.{property_name}",
                    minimum=0.0,
                    maximum=1e6,
                )
        if "appearance" in raw:
            update["appearance"] = _normalize_hfss_material_appearance(
                raw["appearance"],
                f"{field}.appearance",
            )
        if len(update) == 1:
            raise LiveBackendError(f"{field} must change at least one supported field")
        normalized.append(update)
    return {"updates": normalized, "max_materials": max_materials}


def _normalize_hfss_material_delete_spec(args: dict[str, Any]) -> dict[str, Any]:
    max_materials = _bounded_integer(
        args.get("max_materials", 16),
        "max_materials",
        minimum=1,
        maximum=32,
    )
    raw_names = args.get("names")
    if not isinstance(raw_names, list) or not raw_names:
        raise LiveBackendError("names must contain at least one exact HFSS material name")
    if len(raw_names) > max_materials:
        raise LiveBackendError(f"names exceeds the approved maximum of {max_materials}")
    names = []
    seen = set()
    for index, raw_name in enumerate(raw_names):
        if not isinstance(raw_name, str):
            raise LiveBackendError("names must contain only string HFSS material names")
        name = raw_name.strip()
        if not _SAFE_AEDT_MATERIAL_NAME.fullmatch(name):
            raise LiveBackendError(f"names[{index}] must be a safe exact AEDT material name")
        folded = name.casefold()
        if folded in seen:
            raise LiveBackendError("material delete names must be unique case-insensitively")
        seen.add(folded)
        names.append(name)
    return {"names": names, "max_materials": max_materials}


def _normalize_hfss_material_appearance(value: Any, field: str) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 4:
        raise LiveBackendError(f"{field} must contain [red, green, blue, transparency]")
    rgb = []
    for index, item in enumerate(value[:3]):
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255:
            raise LiveBackendError(f"{field}[{index}] must be an integer from 0 to 255")
        rgb.append(item)
    transparency = _bounded_float(
        value[3],
        f"{field}[3]",
        minimum=0.0,
        maximum=1.0,
    )
    return [*rgb, transparency]


def _normalize_layout_via_create_spec(args: dict[str, Any]) -> dict[str, Any]:
    max_vias = _bounded_integer(
        args.get("max_vias", 16),
        "max_vias",
        minimum=1,
        maximum=32,
    )
    raw_vias = args.get("vias")
    if not isinstance(raw_vias, list) or not raw_vias:
        raise LiveBackendError("vias must contain at least one typed via specification")
    if len(raw_vias) > max_vias:
        raise LiveBackendError(f"vias exceeds the approved maximum of {max_vias}")
    normalized = []
    seen_names = set()
    for index, raw in enumerate(raw_vias):
        field = f"vias[{index}]"
        if not isinstance(raw, dict):
            raise LiveBackendError(f"{field} must be an object")
        unsupported = sorted(set(raw).difference(_LAYOUT_VIA_CREATE_FIELDS))
        if unsupported:
            raise LiveBackendError(f"unsupported {field} field: {unsupported[0]}")
        name = str(raw.get("name") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
            raise LiveBackendError(f"{field}.name must be a safe exact AEDT object name")
        folded_name = name.casefold()
        if folded_name in seen_names:
            raise LiveBackendError("via names must be unique case-insensitively")
        seen_names.add(folded_name)
        padstack = str(raw.get("padstack") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(padstack):
            raise LiveBackendError(f"{field}.padstack must be a safe exact padstack name")
        top_layer = str(raw.get("top_layer") or "").strip()
        bottom_layer = str(raw.get("bottom_layer") or "").strip()
        for layer_field, layer_name in (
            ("top_layer", top_layer),
            ("bottom_layer", bottom_layer),
        ):
            if not _SAFE_AEDT_LAYER_NAME.fullmatch(layer_name):
                raise LiveBackendError(
                    f"{field}.{layer_field} must be a safe exact stackup layer name"
                )
        if top_layer == bottom_layer:
            raise LiveBackendError(f"{field} top_layer and bottom_layer must be different")
        net_name = str(raw.get("net_name") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(net_name):
            raise LiveBackendError(f"{field}.net_name must be a safe exact existing net name")
        hole_diameter = raw.get("hole_diameter")
        if hole_diameter is not None:
            hole_diameter = _bounded_float(
                hole_diameter,
                f"{field}.hole_diameter",
                minimum=1e-12,
                maximum=1e6,
            )
        lock_position = raw.get("lock_position", False)
        if type(lock_position) is not bool:
            raise LiveBackendError(f"{field}.lock_position must be boolean")
        normalized.append(
            {
                "name": name,
                "padstack": padstack,
                "x": _bounded_float(
                    raw.get("x"),
                    f"{field}.x",
                    minimum=-1e9,
                    maximum=1e9,
                ),
                "y": _bounded_float(
                    raw.get("y"),
                    f"{field}.y",
                    minimum=-1e9,
                    maximum=1e9,
                ),
                "rotation_degrees": _bounded_float(
                    raw.get("rotation_degrees", 0.0),
                    f"{field}.rotation_degrees",
                    minimum=-3600.0,
                    maximum=3600.0,
                ),
                "hole_diameter": hole_diameter,
                "top_layer": top_layer,
                "bottom_layer": bottom_layer,
                "net_name": net_name,
                "lock_position": lock_position,
            }
        )
    return {"vias": normalized, "max_vias": max_vias}


def _normalize_layout_via_update_spec(args: dict[str, Any]) -> dict[str, Any]:
    max_vias = _bounded_integer(
        args.get("max_vias", 16),
        "max_vias",
        minimum=1,
        maximum=32,
    )
    raw_updates = args.get("updates")
    if not isinstance(raw_updates, list) or not raw_updates:
        raise LiveBackendError("updates must contain at least one typed via update")
    if len(raw_updates) > max_vias:
        raise LiveBackendError(f"updates exceeds the approved maximum of {max_vias}")
    normalized = []
    seen_names = set()
    for index, raw in enumerate(raw_updates):
        field = f"updates[{index}]"
        if not isinstance(raw, dict):
            raise LiveBackendError(f"{field} must be an object")
        unsupported = sorted(set(raw).difference(_LAYOUT_VIA_UPDATE_FIELDS))
        if unsupported:
            raise LiveBackendError(f"unsupported {field} field: {unsupported[0]}")
        name = str(raw.get("name") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
            raise LiveBackendError(f"{field}.name must be a safe exact AEDT object name")
        folded_name = name.casefold()
        if folded_name in seen_names:
            raise LiveBackendError("via update names must be unique case-insensitively")
        seen_names.add(folded_name)
        mutable_fields = set(raw).difference({"name"})
        if not mutable_fields:
            raise LiveBackendError(
                f"{field} must include net_name, location, rotation_degrees, or lock_position"
            )
        update: dict[str, Any] = {"name": name}
        if "net_name" in raw:
            net_name = str(raw.get("net_name") or "").strip()
            if not _SAFE_AEDT_OBJECT_NAME.fullmatch(net_name):
                raise LiveBackendError(
                    f"{field}.net_name must be a safe exact existing net name"
                )
            update["net_name"] = net_name
        if "location" in raw:
            location = raw.get("location")
            if not isinstance(location, list) or len(location) != 2:
                raise LiveBackendError(
                    f"{field}.location must contain two numeric model-unit coordinates"
                )
            update["location"] = [
                _bounded_float(
                    value,
                    f"{field}.location[{coordinate_index}]",
                    minimum=-1e9,
                    maximum=1e9,
                )
                for coordinate_index, value in enumerate(location)
            ]
        if "rotation_degrees" in raw:
            update["rotation_degrees"] = _bounded_float(
                raw.get("rotation_degrees"),
                f"{field}.rotation_degrees",
                minimum=-3600.0,
                maximum=3600.0,
            )
        if "lock_position" in raw:
            lock_position = raw.get("lock_position")
            if type(lock_position) is not bool:
                raise LiveBackendError(f"{field}.lock_position must be boolean")
            update["lock_position"] = lock_position
        normalized.append(update)
    return {"updates": normalized, "max_vias": max_vias}


def _normalize_layout_via_delete_spec(args: dict[str, Any]) -> dict[str, Any]:
    max_vias = _bounded_integer(
        args.get("max_vias", 16),
        "max_vias",
        minimum=1,
        maximum=32,
    )
    raw_names = args.get("names")
    if not isinstance(raw_names, list) or not raw_names:
        raise LiveBackendError("names must contain at least one exact via name")
    if len(raw_names) > max_vias:
        raise LiveBackendError(f"names exceeds the approved maximum of {max_vias}")
    names = []
    seen = set()
    for index, raw_name in enumerate(raw_names):
        name = str(raw_name or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
            raise LiveBackendError(
                f"names[{index}] must be a safe exact AEDT object name"
            )
        folded = name.casefold()
        if folded in seen:
            raise LiveBackendError("via delete names must be unique case-insensitively")
        seen.add(folded)
        names.append(name)
    return {"names": names, "max_vias": max_vias}


def _normalize_layout_material_create_assign_spec(
    args: dict[str, Any],
) -> dict[str, Any]:
    material_spec = _normalize_hfss_material_create_spec(args)
    layer_name = str(args.get("layer_name") or "").strip()
    if not _SAFE_AEDT_LAYER_NAME.fullmatch(layer_name):
        raise LiveBackendError("layer_name must be a safe exact AEDT stackup layer name")
    assignment_field = str(args.get("assignment_field") or "material").strip()
    if assignment_field not in {"material", "fill_material"}:
        raise LiveBackendError(
            "assignment_field must be material or fill_material"
        )
    return {
        **material_spec,
        "layer_name": layer_name,
        "assignment_field": assignment_field,
    }


def _normalize_hfss_coordinate_system_spec(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("coordinate_system_name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
        raise LiveBackendError("coordinate_system_name must be a safe AEDT name")
    if name.casefold() == "global":
        raise LiveBackendError("coordinate_system_name must not be Global")
    reference = str(args.get("reference_coordinate_system") or "Global").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(reference):
        raise LiveBackendError("reference_coordinate_system must be a safe AEDT name")
    origin = _hfss_vector(
        args.get("origin"),
        "origin",
        length=3,
        positive=False,
    )
    x_axis = _finite_numeric_vector(args.get("x_axis"), "x_axis")
    y_axis = _finite_numeric_vector(args.get("y_axis"), "y_axis")
    x_norm = math.sqrt(sum(float(item) ** 2 for item in x_axis))
    y_norm = math.sqrt(sum(float(item) ** 2 for item in y_axis))
    if x_norm <= 1e-15:
        raise LiveBackendError("x_axis must be nonzero")
    if y_norm <= 1e-15:
        raise LiveBackendError("y_axis must be nonzero")
    cross = (
        float(x_axis[1]) * float(y_axis[2]) - float(x_axis[2]) * float(y_axis[1]),
        float(x_axis[2]) * float(y_axis[0]) - float(x_axis[0]) * float(y_axis[2]),
        float(x_axis[0]) * float(y_axis[1]) - float(x_axis[1]) * float(y_axis[0]),
    )
    cross_norm = math.sqrt(sum(item**2 for item in cross))
    if cross_norm <= 1e-12 * x_norm * y_norm:
        raise LiveBackendError("x_axis and y_axis must not be collinear")
    return {
        "coordinate_system_name": name,
        "reference_coordinate_system": reference,
        "mode": "axis",
        "origin": origin,
        "x_axis": x_axis,
        "y_axis": y_axis,
    }


def _finite_numeric_vector(value: Any, field: str) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 3:
        raise LiveBackendError(f"{field} must contain exactly 3 numeric values")
    normalized: list[int | float] = []
    for index, item in enumerate(value):
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            raise LiveBackendError(f"{field}[{index}] must be a finite number")
        normalized.append(item)
    return normalized


def _hfss_coordinate_system_snapshot(app: Any) -> dict[str, Any]:
    modeler = getattr(app, "modeler", None)
    model_units = str(_safe_attribute(modeler, "model_units") or "").strip()
    if not model_units:
        raise LiveBackendError("HFSS model units are unavailable")
    editor = getattr(modeler, "oeditor", None)
    if editor is None:
        raise LiveBackendError("HFSS coordinate system editor is unavailable")
    get_names = getattr(editor, "GetCoordinateSystems", None)
    get_active = getattr(editor, "GetActiveCoordinateSystem", None)
    get_child = getattr(editor, "GetChildObject", None)
    if not all(callable(item) for item in (get_names, get_active, get_child)):
        raise LiveBackendError("HFSS coordinate system readback API is unavailable")
    try:
        names = [str(item) for item in list(get_names() or [])]
        active = str(get_active() or "").strip()
    except Exception as exc:
        raise LiveBackendError(
            f"HFSS coordinate system inventory failed: {type(exc).__name__}: {exc}"
        ) from exc
    if len(names) > 500:
        raise LiveBackendError(
            "HFSS design has more than 500 coordinate systems; bounded inventory is unavailable"
        )
    folded_names = [item.casefold() for item in names]
    if len(set(folded_names)) != len(folded_names):
        raise LiveBackendError(
            "HFSS coordinate system inventory contains duplicate case-insensitive names"
        )
    if not active:
        raise LiveBackendError("HFSS active coordinate system is unavailable")
    if "Global" not in names:
        raise LiveBackendError("HFSS Global coordinate system is missing from inventory")

    records = [
        {
            "name": "Global",
            "type": "Global",
            "kind": "global",
            "reference_coordinate_system": "",
            "mode": "Global",
            "origin": [f"0{model_units}", f"0{model_units}", f"0{model_units}"],
            "x_axis": [1.0, 0.0, 0.0],
            "y_axis": [0.0, 1.0, 0.0],
            "properties": {},
            "property_digest": _digest({"type": "Global"}),
        }
    ]
    for name in sorted((item for item in names if item != "Global"), key=str.casefold):
        try:
            child = get_child(name)
            prop_names = sorted(str(item) for item in list(child.GetPropNames() or []))
            properties = {
                prop_name: _json_value(child.GetPropValue(prop_name))
                for prop_name in prop_names
            }
        except Exception as exc:
            raise LiveBackendError(
                f"HFSS coordinate system property readback failed for {name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        raw_type = str(properties.get("Type") or "").strip()
        folded_type = raw_type.casefold()
        if folded_type == "relative":
            kind = "relative"
        elif "face" in folded_type:
            kind = "face"
        elif "object" in folded_type:
            kind = "object"
        else:
            kind = "other"
        records.append(
            {
                "name": name,
                "type": raw_type,
                "kind": kind,
                "reference_coordinate_system": str(
                    properties.get("Reference CS") or ""
                ).strip(),
                "mode": str(properties.get("Mode") or "").strip(),
                "origin": _coordinate_property_vector(
                    properties,
                    ("Origin/X", "Origin/Y", "Origin/Z"),
                ),
                "x_axis": _coordinate_property_vector(
                    properties,
                    ("X Axis/X", "X Axis/Y", "X Axis/Z"),
                ),
                "y_axis": _coordinate_property_vector(
                    properties,
                    ("Y Point/X", "Y Point/Y", "Y Point/Z"),
                ),
                "properties": properties,
                "property_digest": _digest(properties),
            }
        )
    inventory_names = {item["name"] for item in records}
    if active not in inventory_names:
        raise LiveBackendError(
            f"HFSS active coordinate system is not in inventory: {active}"
        )
    return {
        "model_units": model_units,
        "active_coordinate_system": active,
        "coordinate_systems": records,
    }


def _coordinate_property_vector(
    properties: dict[str, Any],
    names: tuple[str, str, str],
) -> list[Any] | None:
    if not all(name in properties for name in names):
        return None
    return [properties[name] for name in names]


def _set_hfss_working_coordinate_system(app: Any, name: str) -> None:
    editor = getattr(getattr(app, "modeler", None), "oeditor", None)
    setter = getattr(editor, "SetWCS", None)
    if not callable(setter):
        raise LiveBackendError("HFSS working coordinate system API is unavailable")
    try:
        setter(
            [
                "NAME:SetWCS Parameter",
                "Working Coordinate System:=",
                name,
                "RegionDepCSOk:=",
                False,
            ]
        )
    except Exception as exc:
        raise LiveBackendError(
            f"failed to restore HFSS working coordinate system: {type(exc).__name__}: {exc}"
        ) from exc


def _verify_hfss_coordinate_system_readback(
    spec: dict[str, Any],
    readback: dict[str, Any],
    *,
    model_units: str,
) -> None:
    if readback.get("kind") != "relative":
        raise LiveBackendError("HFSS coordinate system type readback failed")
    if str(readback.get("reference_coordinate_system") or "").casefold() != spec[
        "reference_coordinate_system"
    ].casefold():
        raise LiveBackendError("HFSS coordinate system reference readback failed")
    if str(readback.get("mode") or "").casefold() not in {"axis", "axis/position"}:
        raise LiveBackendError("HFSS coordinate system mode readback failed")
    actual_origin = readback.get("origin")
    if not isinstance(actual_origin, list) or len(actual_origin) != 3:
        raise LiveBackendError("HFSS coordinate system origin readback is unavailable")
    for index, (actual, expected) in enumerate(zip(actual_origin, spec["origin"])):
        if not _coordinate_origin_component_matches(actual, expected, model_units):
            raise LiveBackendError(
                f"HFSS coordinate system origin[{index}] readback failed"
            )
    for field in ("x_axis", "y_axis"):
        actual_vector = readback.get(field)
        expected_vector = spec[field]
        if not isinstance(actual_vector, list) or len(actual_vector) != 3:
            raise LiveBackendError(f"HFSS coordinate system {field} readback is unavailable")
        for index, (actual, expected) in enumerate(zip(actual_vector, expected_vector)):
            if not (
                _numeric_boundary_readback_matches(actual, float(expected))
                or _quantity_boundary_readback_matches(
                    actual,
                    float(expected),
                    model_units,
                )
            ):
                raise LiveBackendError(
                    f"HFSS coordinate system {field}[{index}] readback failed: "
                    f"expected {expected}, got {actual}"
                )


def _coordinate_origin_component_matches(
    actual: Any,
    expected: int | float | str,
    model_units: str,
) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return _numeric_boundary_readback_matches(
            actual, float(expected)
        ) or _quantity_boundary_readback_matches(actual, float(expected), model_units)
    expected_text = str(expected).strip()
    if _normalized_expression(str(actual)) == _normalized_expression(expected_text):
        return True
    numeric = re.fullmatch(
        r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?",
        expected_text,
    )
    return bool(
        numeric
        and _quantity_boundary_readback_matches(
            actual,
            float(expected_text),
            model_units,
        )
    )


def _rollback_hfss_coordinate_system(
    app: Any,
    created_name: str,
    *,
    before_snapshot: dict[str, Any],
) -> dict[str, Any]:
    errors = []
    try:
        _set_hfss_working_coordinate_system(
            app,
            before_snapshot["active_coordinate_system"],
        )
    except Exception as exc:
        errors.append(f"restore active before delete: {type(exc).__name__}: {exc}")
    try:
        current_names = {
            item["name"] for item in _hfss_coordinate_system_snapshot(app)["coordinate_systems"]
        }
        if created_name in current_names:
            candidates = [
                item
                for item in list(getattr(app.modeler, "coordinate_systems", []) or [])
                if str(getattr(item, "name", "") or "") == created_name
            ]
            if len(candidates) != 1:
                raise LiveBackendError(
                    f"created coordinate system wrapper is unavailable: {created_name}"
                )
            if candidates[0].delete() is False:
                raise LiveBackendError(
                    f"failed to delete created coordinate system: {created_name}"
                )
    except Exception as exc:
        errors.append(f"delete {created_name}: {type(exc).__name__}: {exc}")
    try:
        _set_hfss_working_coordinate_system(
            app,
            before_snapshot["active_coordinate_system"],
        )
    except Exception as exc:
        errors.append(f"restore active after delete: {type(exc).__name__}: {exc}")
    try:
        after_snapshot = _hfss_coordinate_system_snapshot(app)
    except Exception as exc:
        after_snapshot = None
        errors.append(f"readback: {type(exc).__name__}: {exc}")
    complete = after_snapshot is not None and _digest(after_snapshot) == _digest(before_snapshot)
    return {
        "complete": complete,
        "created_coordinate_system_name": created_name,
        "active_coordinate_system": (
            after_snapshot.get("active_coordinate_system") if after_snapshot else None
        ),
        "errors": errors,
    }


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


def _normalize_hfss_boundary_spec(args: dict[str, Any]) -> dict[str, Any]:
    boundary_kind = str(args.get("boundary_kind") or "").strip().casefold()
    if boundary_kind not in _HFSS_BOUNDARY_OPTIONS:
        raise LiveBackendError(f"unsupported HFSS boundary kind: {boundary_kind}")
    boundary_name = str(args.get("boundary_name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(boundary_name):
        raise LiveBackendError("boundary_name must be a safe AEDT name")
    raw_face_ids = args.get("assignment_face_ids") or []
    if not isinstance(raw_face_ids, list) or any(
        type(item) is not int or item <= 0 for item in raw_face_ids
    ):
        raise LiveBackendError("assignment_face_ids must contain positive integer face IDs")
    face_ids = list(raw_face_ids)
    if len(face_ids) != len(set(face_ids)):
        raise LiveBackendError("assignment_face_ids must not contain duplicates")
    if len(face_ids) > 64:
        raise LiveBackendError("assignment_face_ids exceeds the maximum of 64")
    object_name = str(args.get("assignment_object_name") or "").strip()
    if object_name and not _SAFE_AEDT_OBJECT_NAME.fullmatch(object_name):
        raise LiveBackendError("assignment_object_name must be a safe AEDT object name")
    if boundary_kind == "lumped_port":
        if face_ids or not object_name:
            raise LiveBackendError(
                "lumped_port requires one assignment_object_name and no assignment_face_ids"
            )
    else:
        if object_name:
            raise LiveBackendError(
                f"{boundary_kind} requires assignment_face_ids, not assignment_object_name"
            )
        if not face_ids:
            raise LiveBackendError("assignment_face_ids must contain positive integer face IDs")
        if boundary_kind == "wave_port" and len(face_ids) != 1:
            raise LiveBackendError("wave_port requires exactly one assignment face ID")
    references = list(args.get("references") or [])
    if references:
        raise LiveBackendError(
            "typed DrivenModal boundary creation does not accept references; "
            "terminal-port references require a separate verified Harness"
        )
    if boundary_kind == "radiation":
        options = _normalize_hfss_boundary_options(boundary_kind, args.get("options"), "options")
    else:
        options = _normalize_hfss_typed_port_options(
            boundary_kind,
            args.get("options"),
            "options",
        )
    return {
        "boundary_kind": boundary_kind,
        "boundary_name": boundary_name,
        "assignment_face_ids": face_ids,
        "assignment_object_name": object_name,
        "references": [],
        "options": options,
    }


def _normalize_hfss_typed_port_options(
    port_kind: str,
    raw_options: Any,
    field: str,
) -> dict[str, Any]:
    if raw_options is None:
        options: dict[str, Any] = {}
    elif isinstance(raw_options, dict):
        options = dict(raw_options)
    else:
        raise LiveBackendError(f"{field} must be an object")
    unsupported = sorted(set(options).difference(_HFSS_TYPED_PORT_OPTIONS[port_kind]))
    if unsupported:
        raise LiveBackendError(f"unsupported typed {port_kind} option: {unsupported[0]}")
    direction_raw = str(options.get("integration_line_direction") or "XNeg").strip()
    direction_by_casefold = {item.casefold(): item for item in _HFSS_AXIS_DIRECTIONS}
    direction = direction_by_casefold.get(direction_raw.casefold())
    if direction is None:
        raise LiveBackendError(
            f"{field}.integration_line_direction must be one of "
            + ", ".join(_HFSS_AXIS_DIRECTIONS)
        )
    renormalize = options.get("renormalize", True)
    if type(renormalize) is not bool:
        raise LiveBackendError(f"{field}.renormalize must be boolean")
    normalized: dict[str, Any] = {
        "integration_line_direction": direction,
        "renormalize": renormalize,
    }
    if port_kind == "wave_port":
        normalized["modes"] = _bounded_integer(
            options.get("modes", 1),
            f"{field}.modes",
            minimum=1,
            maximum=16,
        )
        deembed = options.get("deembed", 0.0)
        if (
            isinstance(deembed, bool)
            or not isinstance(deembed, (int, float))
            or not math.isfinite(float(deembed))
            or not 0 <= float(deembed) <= 1000000
        ):
            raise LiveBackendError(
                f"{field}.deembed must be a finite millimeter value between 0 and 1000000"
            )
        normalized["deembed"] = float(deembed)
        characteristic = str(options.get("characteristic_impedance") or "Zpi").strip()
        by_casefold = {item.casefold(): item for item in _HFSS_CHARACTERISTIC_IMPEDANCES}
        characteristic = by_casefold.get(characteristic.casefold(), "")
        if not characteristic:
            raise LiveBackendError(
                f"{field}.characteristic_impedance must be Zpi, Zpv, Zvi, or Zwave"
            )
        normalized["characteristic_impedance"] = characteristic
    else:
        impedance = options.get("impedance", 50.0)
        if (
            isinstance(impedance, bool)
            or not isinstance(impedance, (int, float))
            or not math.isfinite(float(impedance))
            or not 0 < float(impedance) <= 1000000000
        ):
            raise LiveBackendError(
                f"{field}.impedance must be a positive finite ohm value at most 1000000000"
            )
        deembed = options.get("deembed", False)
        if type(deembed) is not bool:
            raise LiveBackendError(f"{field}.deembed must be boolean for lumped_port")
        normalized["impedance"] = float(impedance)
        normalized["deembed"] = deembed
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


def _hfss_material_catalog_snapshot(app: Any) -> dict[str, Any]:
    materials = _safe_attribute(app, "materials")
    material_keys = _safe_attribute(materials, "material_keys") if materials else None
    if not isinstance(material_keys, dict):
        raise LiveBackendError("HFSS project material catalog is unavailable")
    definition_manager = _safe_attribute(materials, "odefinition_manager")
    project_name_getter = getattr(definition_manager, "GetProjectMaterialNames", None)
    if callable(project_name_getter):
        try:
            project_names = [str(item).strip() for item in list(project_name_getter() or [])]
        except Exception as exc:
            raise LiveBackendError(
                f"HFSS project material name readback failed: {type(exc).__name__}: {exc}"
            ) from exc
    else:
        project_names = [
            str(_safe_attribute(material, "name") or key).strip()
            for key, material in material_keys.items()
        ]
    if len(project_names) > 500:
        raise LiveBackendError("HFSS project material catalog exceeds the 500 material safety limit")
    normalized_names = set()
    names = []
    loader = getattr(materials, "_aedmattolibrary", None)
    for canonical_name in project_names:
        normalized = canonical_name.casefold()
        if normalized in normalized_names:
            raise LiveBackendError("HFSS project material catalog contains duplicate case-insensitive names")
        normalized_names.add(normalized)
        if normalized not in material_keys:
            if not callable(loader):
                raise LiveBackendError(
                    f"HFSS project material is absent from the PyAEDT cache: {canonical_name}"
                )
            try:
                loaded = loader(canonical_name)
            except Exception as exc:
                raise LiveBackendError(
                    f"HFSS project material refresh failed for {canonical_name}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if not loaded or normalized not in material_keys:
                raise LiveBackendError(
                    f"HFSS project material refresh returned no definition: {canonical_name}"
                )
        names.append(canonical_name)
    return {
        "materials": [
            _hfss_material_snapshot(app, name)
            for name in sorted(names, key=str.casefold)
        ]
    }


def _hfss_existing_material_name(app: Any, requested_name: str) -> str:
    materials = _safe_attribute(app, "materials")
    if materials is None:
        raise LiveBackendError("HFSS project material catalog is unavailable")
    getter = getattr(materials, "_get_aedt_case_name", None)
    if callable(getter):
        try:
            existing = getter(requested_name)
        except Exception as exc:
            raise LiveBackendError(
                f"HFSS material library collision check failed: {type(exc).__name__}: {exc}"
            ) from exc
        return str(existing).strip() if existing else ""
    names = _safe_attribute(materials, "mat_names_aedt")
    if not isinstance(names, list):
        raise LiveBackendError("HFSS material library collision check is unavailable")
    by_name = {str(item).casefold(): str(item) for item in names}
    return by_name.get(requested_name.casefold(), "")


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
    for manager_name in ("omaterial_manager", "odefinition_manager"):
        definition_manager = _safe_attribute(materials, manager_name)
        getter = getattr(definition_manager, "GetData", None)
        if callable(getter):
            try:
                raw_definition = _json_value(getter(canonical_name))
            except Exception:
                raw_definition = None
            if raw_definition is not None:
                break
    appearance = _safe_json_attribute(material, "material_appearance")
    definition_evidence = (
        raw_definition
        if raw_definition is not None
        else {
            "electrical_properties": electrical_properties,
            "is_dielectric": is_dielectric,
            "appearance": appearance,
        }
    )
    return {
        "canonical_name": canonical_name,
        "is_dielectric": is_dielectric,
        "electrical_properties": electrical_properties,
        "appearance": appearance,
        "definition_digest": _digest(definition_evidence),
    }


def _hfss_material_object(app: Any, requested_name: str) -> Any:
    materials = _safe_attribute(app, "materials")
    material_keys = _safe_attribute(materials, "material_keys") if materials else None
    if not isinstance(material_keys, dict):
        raise LiveBackendError("HFSS project material catalog is unavailable")
    by_name = {
        str(_safe_attribute(material, "name") or key).casefold(): material
        for key, material in material_keys.items()
    }
    material = by_name.get(requested_name.casefold())
    if material is None:
        raise LiveBackendError(f"HFSS project material is unavailable: {requested_name}")
    canonical_name = str(_safe_attribute(material, "name") or "").strip()
    if canonical_name != requested_name:
        raise LiveBackendError(
            f"HFSS project material exact name changed: expected {requested_name}, got {canonical_name}"
        )
    return material


def _hfss_material_object_ids(app: Any, material_names: list[str]) -> dict[str, int]:
    return {name: id(_hfss_material_object(app, name)) for name in material_names}


def _refresh_hfss_material_objects(app: Any, material_names: list[str]) -> None:
    materials = _safe_attribute(app, "materials")
    loader = getattr(materials, "_aedmattolibrary", None)
    if not callable(loader):
        raise LiveBackendError("PyAEDT material refresh API is unavailable")
    for name in material_names:
        try:
            material = loader(name)
        except Exception as exc:
            raise LiveBackendError(
                f"PyAEDT material refresh failed for {name}: {type(exc).__name__}: {exc}"
            ) from exc
        canonical_name = str(_safe_attribute(material, "name") or "").strip()
        if canonical_name != name:
            raise LiveBackendError(
                f"PyAEDT material refresh returned an unexpected name: {canonical_name}"
            )


def _hfss_material_raw_definition(app: Any, material_name: str) -> Any:
    materials = _safe_attribute(app, "materials")
    if materials is None:
        raise LiveBackendError("HFSS project material catalog is unavailable")
    for manager_name in ("omaterial_manager", "odefinition_manager"):
        manager = _safe_attribute(materials, manager_name)
        getter = getattr(manager, "GetData", None)
        if not callable(getter):
            continue
        try:
            raw_definition = _json_value(getter(material_name))
        except Exception:
            continue
        if raw_definition is not None:
            return raw_definition
    raise LiveBackendError(f"HFSS raw material definition is unavailable: {material_name}")


def _hfss_project_material_names(app: Any) -> list[str]:
    materials = _safe_attribute(app, "materials")
    definition_manager = _safe_attribute(materials, "odefinition_manager")
    getter = getattr(definition_manager, "GetProjectMaterialNames", None)
    if not callable(getter):
        raise LiveBackendError("HFSS project material name readback is unavailable")
    try:
        names = [str(item).strip() for item in list(getter() or [])]
    except Exception as exc:
        raise LiveBackendError(
            f"HFSS project material name readback failed: {type(exc).__name__}: {exc}"
        ) from exc
    if len(names) > 500:
        raise LiveBackendError("HFSS project material catalog exceeds the 500 material safety limit")
    if len({name.casefold() for name in names}) != len(names):
        raise LiveBackendError(
            "HFSS project material catalog contains duplicate case-insensitive names"
        )
    return names


def _hfss_material_reference_snapshot(
    app: Any,
    material_names: list[str],
) -> list[dict[str, Any]]:
    object_names = [
        str(item) for item in list(getattr(app.modeler, "object_names", []) or [])
    ]
    if len(object_names) > 5000:
        raise LiveBackendError(
            "HFSS design exceeds the 5000 object material-reference safety limit"
        )
    if len(set(object_names)) != len(object_names):
        raise LiveBackendError("HFSS design contains duplicate exact object names")
    target_names = {item.casefold() for item in material_names}
    records = _hfss_material_target_snapshot(app, object_names)
    return [
        item
        for item in records
        if item["is_solid"] and item["material_name"].casefold() in target_names
    ]


def _hfss_material_boundary_reference_snapshot(
    app: Any,
    material_names: list[str],
) -> list[dict[str, Any]]:
    try:
        boundaries = list(getattr(app, "boundaries", []) or [])
    except Exception as exc:
        raise LiveBackendError("HFSS boundary inventory is unavailable") from exc
    target_by_name = {name.casefold(): name for name in material_names}
    records = []
    for boundary in boundaries:
        name = str(_safe_attribute(boundary, "name") or "").strip()
        boundary_type = str(_safe_attribute(boundary, "type") or "").strip()
        if not name or not boundary_type:
            raise LiveBackendError("HFSS boundary name or type readback is unavailable")
        try:
            props = _json_value(dict(getattr(boundary, "props", {}) or {}))
            properties = _json_value(dict(getattr(boundary, "properties", {}) or {}))
        except Exception as exc:
            raise LiveBackendError(
                f"HFSS boundary properties are unavailable: {name}"
            ) from exc
        referenced = _matching_hfss_material_values(
            {"props": props, "properties": properties},
            target_by_name,
        )
        records.append(
            {
                "name": name,
                "type": boundary_type,
                "material_names": [
                    target_by_name[item]
                    for item in target_by_name
                    if item in referenced
                ],
                "property_digest": _digest(
                    {"props": props, "properties": properties}
                ),
            }
        )
    return sorted(records, key=lambda item: item["name"].casefold())


def _matching_hfss_material_values(
    value: Any,
    target_by_name: dict[str, str],
) -> set[str]:
    matches = set()
    if isinstance(value, dict):
        for item in value.values():
            matches.update(_matching_hfss_material_values(item, target_by_name))
        return matches
    if isinstance(value, list):
        for item in value:
            matches.update(_matching_hfss_material_values(item, target_by_name))
        return matches
    if isinstance(value, str):
        normalized = value.strip().strip('"').casefold()
        if normalized in target_by_name:
            matches.add(normalized)
    return matches


def _validate_hfss_material_update_target(
    before: dict[str, Any],
    update: dict[str, Any],
) -> None:
    properties = before.get("electrical_properties")
    if not isinstance(properties, dict):
        raise LiveBackendError(
            f"HFSS material electrical property readback is unavailable: {before['canonical_name']}"
        )
    numeric_before = {}
    for property_name in _HFSS_MATERIAL_NUMERIC_PROPERTIES:
        record = properties.get(property_name)
        if not isinstance(record, dict) or record.get("type") != "simple":
            raise LiveBackendError(
                "HFSS material update only supports materials whose five electromagnetic "
                f"properties are simple numeric values: {before['canonical_name']}"
            )
        value = record.get("value")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise LiveBackendError(
                f"HFSS material {property_name} numeric readback is unavailable: "
                f"{before['canonical_name']}"
            ) from exc
        if not math.isfinite(numeric):
            raise LiveBackendError(
                f"HFSS material {property_name} numeric readback is not finite: "
                f"{before['canonical_name']}"
            )
        numeric_before[property_name] = numeric
    appearance = before.get("appearance")
    if not isinstance(appearance, list) or len(appearance) != 4:
        raise LiveBackendError(
            f"HFSS material appearance readback is unavailable: {before['canonical_name']}"
        )
    unchanged = True
    for property_name in _HFSS_MATERIAL_NUMERIC_PROPERTIES:
        if property_name in update and not math.isclose(
            numeric_before[property_name],
            update[property_name],
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            unchanged = False
    if "appearance" in update and not _hfss_material_appearance_equal(
        appearance,
        update["appearance"],
    ):
        unchanged = False
    if unchanged:
        raise LiveBackendError(
            f"HFSS material update is a no-op: {before['canonical_name']}"
        )
    conductivity = update.get("conductivity", numeric_before["conductivity"])
    expected_is_dielectric = conductivity < 100000.0
    if before.get("is_dielectric") is not expected_is_dielectric:
        raise LiveBackendError(
            "HFSS material update cannot cross the dielectric/conductor threshold; "
            "use a dedicated classification-change workflow so referenced object "
            f"solve_inside state is handled explicitly: {before['canonical_name']}"
        )
    if not str(before.get("definition_digest") or ""):
        raise LiveBackendError(
            f"HFSS material definition digest is unavailable: {before['canonical_name']}"
        )


def _hfss_material_appearance_equal(left: Any, right: Any) -> bool:
    if not isinstance(left, list) or not isinstance(right, list):
        return False
    if len(left) != 4 or len(right) != 4 or left[:3] != right[:3]:
        return False
    try:
        return math.isclose(
            float(left[3]),
            float(right[3]),
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
    except (TypeError, ValueError):
        return False


def _verify_hfss_material_update_catalog(
    before_catalog: dict[str, Any],
    after_catalog: dict[str, Any],
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    before_materials = list(before_catalog.get("materials") or [])
    after_materials = list(after_catalog.get("materials") or [])
    before_by_name = {item["canonical_name"]: item for item in before_materials}
    after_by_name = {item["canonical_name"]: item for item in after_materials}
    if set(after_by_name) != set(before_by_name):
        raise LiveBackendError("unexpected HFSS material catalog name change during update")
    updates_by_name = {item["material_name"]: item for item in updates}
    for name, before in before_by_name.items():
        if name not in updates_by_name and after_by_name[name] != before:
            raise LiveBackendError(
                f"unrequested HFSS material changed during update: {name}"
            )
    verified = []
    for update in updates:
        name = update["material_name"]
        before = before_by_name[name]
        after = after_by_name[name]
        if after.get("canonical_name") != name:
            raise LiveBackendError(f"HFSS material name readback mismatch: {name}")
        if after.get("is_dielectric") is not before.get("is_dielectric"):
            raise LiveBackendError(
                f"HFSS material dielectric classification changed unexpectedly: {name}"
            )
        before_properties = dict(before.get("electrical_properties") or {})
        after_properties = dict(after.get("electrical_properties") or {})
        for property_name in _HFSS_MATERIAL_NUMERIC_PROPERTIES:
            before_record = before_properties.get(property_name)
            after_record = after_properties.get(property_name)
            if property_name not in update:
                if after_record != before_record:
                    raise LiveBackendError(
                        f"unrequested HFSS material property changed: {name}.{property_name}"
                    )
                continue
            if not isinstance(after_record, dict) or after_record.get("type") != "simple":
                raise LiveBackendError(
                    f"HFSS material property did not read back as simple: {name}.{property_name}"
                )
            try:
                actual = float(after_record.get("value"))
            except (TypeError, ValueError) as exc:
                raise LiveBackendError(
                    f"HFSS material numeric readback is unavailable: {name}.{property_name}"
                ) from exc
            if not math.isfinite(actual) or not math.isclose(
                actual,
                update[property_name],
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise LiveBackendError(
                    f"HFSS material property readback mismatch: {name}.{property_name}"
                )
            if after_record.get("unit") != before_record.get("unit"):
                raise LiveBackendError(
                    f"HFSS material property unit changed unexpectedly: {name}.{property_name}"
                )
        if "appearance" in update:
            if not _hfss_material_appearance_equal(after.get("appearance"), update["appearance"]):
                raise LiveBackendError(f"HFSS material appearance readback mismatch: {name}")
        elif after.get("appearance") != before.get("appearance"):
            raise LiveBackendError(f"unrequested HFSS material appearance changed: {name}")
        if not str(after.get("definition_digest") or ""):
            raise LiveBackendError(f"HFSS material definition digest is unavailable: {name}")
        if after.get("definition_digest") == before.get("definition_digest"):
            raise LiveBackendError(f"HFSS material definition did not change: {name}")
        verified.append(after)
    return verified


def _verify_hfss_material_raw_definition_updates(
    before_definitions: dict[str, Any],
    after_definitions: dict[str, Any],
    updates: list[dict[str, Any]],
) -> None:
    if set(after_definitions) != set(before_definitions):
        raise LiveBackendError("HFSS raw material definition target set changed")
    appearance_fields = {"appearance", "red", "green", "blue", "transparency"}
    for update in updates:
        name = update["material_name"]
        allowed_fields = {
            property_name.casefold()
            for property_name in _HFSS_MATERIAL_NUMERIC_PROPERTIES
            if property_name in update
        }
        if "appearance" in update:
            allowed_fields.update(appearance_fields)
        masked_before = _mask_hfss_material_definition_fields(
            before_definitions[name],
            allowed_fields,
        )
        masked_after = _mask_hfss_material_definition_fields(
            after_definitions[name],
            allowed_fields,
        )
        if masked_after != masked_before:
            raise LiveBackendError(
                f"unrequested native HFSS material definition data changed: {name}"
            )


def _mask_hfss_material_definition_fields(value: Any, allowed_fields: set[str]) -> Any:
    marker = "<ansys-agent-requested-update>"
    if isinstance(value, list):
        masked = []
        index = 0
        while index < len(value):
            item = value[index]
            if (
                isinstance(item, str)
                and item.endswith(":=")
                and item[:-2].strip().casefold() in allowed_fields
                and index + 1 < len(value)
            ):
                masked.extend((item, marker))
                index += 2
                continue
            masked.append(_mask_hfss_material_definition_fields(item, allowed_fields))
            index += 1
        return masked
    if isinstance(value, dict):
        return {
            key: (
                marker
                if str(key).strip().rstrip(":=").casefold() in allowed_fields
                else _mask_hfss_material_definition_fields(item, allowed_fields)
            )
            for key, item in value.items()
        }
    return value


def _verify_hfss_material_delete_catalog(
    before_catalog: dict[str, Any],
    after_catalog: dict[str, Any],
    deleted_names: list[str],
) -> None:
    before_by_name = {
        item["canonical_name"]: item
        for item in list(before_catalog.get("materials") or [])
    }
    after_by_name = {
        item["canonical_name"]: item
        for item in list(after_catalog.get("materials") or [])
    }
    expected_names = set(before_by_name).difference(deleted_names)
    if set(after_by_name) != expected_names:
        raise LiveBackendError("unexpected HFSS material catalog name change during deletion")
    for name in expected_names:
        if after_by_name[name] != before_by_name[name]:
            raise LiveBackendError(
                f"unrequested HFSS material changed during deletion: {name}"
            )


def _rollback_hfss_material_deletes(
    app: Any,
    *,
    deleted_names: list[str],
    raw_definitions: dict[str, Any],
    before_catalog: dict[str, Any],
    before_boundaries: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = []
    restored_names = []
    materials = _safe_attribute(app, "materials")
    definition_manager = _safe_attribute(materials, "odefinition_manager")
    add_material = getattr(definition_manager, "AddMaterial", None)
    loader = getattr(materials, "_aedmattolibrary", None)
    for name in deleted_names:
        try:
            current_by_name = {
                item.casefold(): item for item in _hfss_project_material_names(app)
            }
            if name.casefold() in current_by_name:
                raise LiveBackendError(
                    "same-name material appeared during rollback; refusing to overwrite it"
                )
            if not callable(add_material):
                raise LiveBackendError("native HFSS material reconstruction API is unavailable")
            if name not in raw_definitions:
                raise LiveBackendError("frozen raw HFSS material definition is unavailable")
            add_material(raw_definitions[name])
            if not callable(loader) or not loader(name):
                raise LiveBackendError(
                    "PyAEDT material cache refresh failed after reconstruction"
                )
            if _hfss_material_raw_definition(app, name) != raw_definitions[name]:
                raise LiveBackendError("native HFSS material reconstruction readback mismatch")
            restored_names.append(name)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        after_catalog = _hfss_material_catalog_snapshot(app)
        after_boundaries = _hfss_material_boundary_reference_snapshot(
            app,
            list(raw_definitions),
        )
        after_references = _hfss_material_reference_snapshot(
            app,
            list(raw_definitions),
        )
    except Exception as exc:
        after_catalog = {"materials": []}
        after_boundaries = []
        after_references = []
        readback_error = f"{type(exc).__name__}: {exc}"
    catalog_match = _digest(after_catalog) == _digest(before_catalog)
    boundaries_match = after_boundaries == before_boundaries
    references_clear = not after_references
    complete = (
        not errors
        and not readback_error
        and catalog_match
        and boundaries_match
        and references_clear
    )
    return {
        "complete": complete,
        "restored_material_names": restored_names,
        "catalog_match": catalog_match,
        "boundaries_match": boundaries_match,
        "references_clear": references_clear,
        "before_catalog_digest": _digest(before_catalog),
        "after_catalog_digest": _digest(after_catalog) if not readback_error else "",
        "errors": errors,
        "readback_error": readback_error,
    }


def _rollback_hfss_material_updates(
    app: Any,
    *,
    before_targets: list[dict[str, Any]],
    before_catalog: dict[str, Any],
    before_references: list[dict[str, Any]],
    expected_object_ids: dict[str, int],
    raw_definitions: dict[str, Any],
) -> dict[str, Any]:
    errors = []
    restored_names = []
    materials = _safe_attribute(app, "materials")
    definition_manager = _safe_attribute(materials, "odefinition_manager")
    edit_material = getattr(definition_manager, "EditMaterial", None)
    loader = getattr(materials, "_aedmattolibrary", None)
    for before in reversed(before_targets):
        name = before["canonical_name"]
        try:
            material = _hfss_material_object(app, name)
            if id(material) != expected_object_ids.get(name):
                raise LiveBackendError("material object identity changed; refusing to overwrite it")
            if not callable(edit_material):
                raise LiveBackendError("native HFSS material restore API is unavailable")
            if name not in raw_definitions:
                raise LiveBackendError("frozen raw HFSS material definition is unavailable")
            edit_material(name, raw_definitions[name])
            if not callable(loader) or not loader(name):
                raise LiveBackendError("PyAEDT material cache refresh failed after restore")
            restored_names.append(name)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        after_catalog = _hfss_material_catalog_snapshot(app)
        after_references = _hfss_material_reference_snapshot(
            app,
            [item["canonical_name"] for item in before_targets],
        )
    except Exception as exc:
        after_catalog = {"materials": []}
        after_references = []
        readback_error = f"{type(exc).__name__}: {exc}"
    catalog_match = _digest(after_catalog) == _digest(before_catalog)
    references_match = after_references == before_references
    complete = not errors and not readback_error and catalog_match and references_match
    return {
        "complete": complete,
        "restored_material_names": restored_names,
        "catalog_match": catalog_match,
        "references_match": references_match,
        "before_catalog_digest": _digest(before_catalog),
        "after_catalog_digest": _digest(after_catalog) if not readback_error else "",
        "errors": errors,
        "readback_error": readback_error,
    }


def _verify_hfss_material_create_readback(
    spec: dict[str, Any],
    readback: dict[str, Any],
) -> None:
    if readback.get("canonical_name") != spec["material_name"]:
        raise LiveBackendError("HFSS material name readback mismatch")
    if type(readback.get("is_dielectric")) is not bool:
        raise LiveBackendError("HFSS material dielectric classification readback is unavailable")
    properties = readback.get("electrical_properties")
    if not isinstance(properties, dict):
        raise LiveBackendError("HFSS material electrical property readback is unavailable")
    for name in (
        "permittivity",
        "permeability",
        "conductivity",
        "dielectric_loss_tangent",
        "magnetic_loss_tangent",
    ):
        property_readback = properties.get(name)
        if not isinstance(property_readback, dict) or property_readback.get("type") != "simple":
            raise LiveBackendError(f"HFSS material {name} did not read back as a simple property")
        actual = property_readback.get("value")
        try:
            actual_numeric = float(actual)
        except (TypeError, ValueError) as exc:
            raise LiveBackendError(f"HFSS material {name} numeric readback is unavailable") from exc
        if not math.isfinite(actual_numeric) or not math.isclose(
            actual_numeric,
            spec[name],
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise LiveBackendError(
                f"HFSS material {name} readback mismatch: expected {spec[name]}, got {actual}"
            )
    if spec["appearance"] is not None:
        actual_appearance = readback.get("appearance")
        if not isinstance(actual_appearance, list) or len(actual_appearance) != 4:
            raise LiveBackendError("HFSS material appearance readback is unavailable")
        if actual_appearance[:3] != spec["appearance"][:3]:
            raise LiveBackendError("HFSS material RGB appearance readback mismatch")
        try:
            actual_transparency = float(actual_appearance[3])
        except (TypeError, ValueError) as exc:
            raise LiveBackendError("HFSS material transparency readback is unavailable") from exc
        if not math.isclose(
            actual_transparency,
            spec["appearance"][3],
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise LiveBackendError("HFSS material transparency readback mismatch")
    if not str(readback.get("definition_digest") or ""):
        raise LiveBackendError("HFSS material definition digest is unavailable")


def _rollback_hfss_material_create(
    app: Any,
    created_name: str,
    *,
    before_catalog: dict[str, Any],
) -> dict[str, Any]:
    materials = _safe_attribute(app, "materials")
    errors = []
    removed_name = ""
    before_names = {
        str(item["canonical_name"]).casefold()
        for item in before_catalog.get("materials", [])
    }
    material_keys = _safe_attribute(materials, "material_keys") if materials else None
    if not isinstance(material_keys, dict):
        errors.append("HFSS project material catalog is unavailable during rollback")
    else:
        by_name = {
            str(_safe_attribute(material, "name") or key).casefold():
            str(_safe_attribute(material, "name") or key)
            for key, material in material_keys.items()
        }
        normalized_created = created_name.casefold()
        if normalized_created in by_name and normalized_created not in before_names:
            removed_name = by_name[normalized_created]
            remover = getattr(materials, "remove_material", None)
            if not callable(remover):
                errors.append("HFSS material removal API is unavailable")
            else:
                try:
                    if remover(removed_name) is not True:
                        errors.append("HFSS material removal returned false")
                    else:
                        _invalidate_pyaedt_material_name_cache(materials)
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        after_catalog = _hfss_material_catalog_snapshot(app)
    except Exception as exc:
        after_catalog = {"materials": []}
        readback_error = f"{type(exc).__name__}: {exc}"
    complete = (
        not errors
        and not readback_error
        and _digest(after_catalog) == _digest(before_catalog)
    )
    return {
        "complete": complete,
        "removed_material_name": removed_name,
        "before_digest": _digest(before_catalog),
        "after_digest": _digest(after_catalog) if not readback_error else "",
        "errors": errors,
        "readback_error": readback_error,
    }


def _invalidate_pyaedt_material_name_cache(materials: Any) -> None:
    """Force PyAEDT to rebuild names after Definition Manager removal."""
    for attribute in ("_mats", "_mats_lower"):
        if hasattr(materials, attribute):
            try:
                setattr(materials, attribute, [])
            except Exception:
                pass


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


def _modal_port_solution_supported(solution_type: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "", solution_type).casefold()
    return normalized in {"modal", "drivenmodal"}


def _hfss_boundary_target_snapshot(
    geometry: dict[str, Any],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    if spec["assignment_object_name"]:
        surface_spec = {
            "object_names": [spec["assignment_object_name"]],
            "face_ids": [],
        }
    else:
        surface_spec = {
            "object_names": [],
            "face_ids": spec["assignment_face_ids"],
        }
    targets = _hfss_surface_boundary_targets(geometry, surface_spec)
    return _hfss_surface_boundary_target_snapshot(targets, surface_spec)


def _validate_hfss_boundary_target(
    spec: dict[str, Any],
    target_snapshot: list[dict[str, Any]],
) -> None:
    if spec["boundary_kind"] == "radiation":
        return
    if spec["boundary_kind"] == "wave_port":
        faces = [
            face
            for item in target_snapshot
            for face in list(item.get("faces") or [])
        ]
        if len(faces) != 1 or faces[0].get("is_planar") is not True:
            raise LiveBackendError("HFSS wave_port requires exactly one planar face")
        return
    if len(target_snapshot) != 1:
        raise LiveBackendError("HFSS lumped_port requires exactly one planar sheet object")
    target = target_snapshot[0]
    faces = list(target.get("faces") or [])
    if (
        target.get("is_solid") is not False
        or not faces
        or any(face.get("is_planar") is not True for face in faces)
    ):
        raise LiveBackendError(
            "HFSS lumped_port requires exactly one planar sheet object: " + target["name"]
        )


def _hfss_axis_direction(app: Any, direction_name: str) -> Any:
    try:
        directions = app.axis_directions
    except Exception:
        directions = None
    direction = getattr(directions, direction_name, None) if directions else None
    if direction is None:
        raise LiveBackendError("HFSS axis direction inventory is unavailable")
    return direction


def _hfss_port_integration_line(
    app: Any,
    assignment: str | int,
    direction_name: str,
) -> dict[str, list[str]]:
    direction = _hfss_axis_direction(app, direction_name)
    try:
        wave_resolver = getattr(app, "_get_reference_and_integration_points", None)
        if type(assignment) is int and callable(wave_resolver):
            object_name = app.modeler.oeditor.GetObjectNameByFaceID(assignment)
            _, raw_start, raw_end = wave_resolver(
                assignment,
                direction,
                object_name,
            )
        else:
            raw_start, raw_end = app.modeler.get_mid_points_on_dir(assignment, direction)
        start = [float(item) for item in raw_start]
        end = [float(item) for item in raw_end]
    except Exception as exc:
        raise LiveBackendError("HFSS port integration line could not be resolved") from exc
    if (
        len(start) != 3
        or len(end) != 3
        or not all(math.isfinite(item) for item in start + end)
        or all(math.isclose(a, b, rel_tol=0.0, abs_tol=1e-15) for a, b in zip(start, end))
    ):
        raise LiveBackendError("HFSS port integration line must have two distinct 3D points")
    model_units = str(_safe_attribute(app.modeler, "model_units") or "").strip()
    if not re.fullmatch(r"[A-Za-z]+", model_units):
        raise LiveBackendError("HFSS model units are unavailable for port creation")
    return {
        "start": [str(item) + model_units for item in start],
        "end": [str(item) + model_units for item in end],
    }


def _hfss_port_boundary_snapshot(app: Any) -> list[dict[str, Any]]:
    try:
        boundaries = list(getattr(app, "boundaries", []) or [])
    except Exception as exc:
        raise LiveBackendError("HFSS boundary inventory is unavailable") from exc
    records = []
    for boundary in boundaries:
        name = str(getattr(boundary, "name", "") or "").strip()
        boundary_type = str(getattr(boundary, "type", "") or "").strip()
        if not name or not boundary_type:
            raise LiveBackendError("HFSS boundary name or type readback is unavailable")
        try:
            props = dict(getattr(boundary, "props", {}) or {})
        except Exception as exc:
            raise LiveBackendError(f"HFSS boundary properties are unavailable: {name}") from exc
        records.append(_hfss_port_boundary_record(name, boundary_type, props))
    return sorted(records, key=lambda item: item["name"].casefold())


def _hfss_port_boundary_record(
    name: str,
    boundary_type: str,
    props: dict[str, Any],
) -> dict[str, Any]:
    normalized_type = re.sub(r"[\s_-]+", "", boundary_type).casefold()
    kind = {
        "waveport": "wave_port",
        "lumpedport": "lumped_port",
        "radiation": "radiation",
    }.get(normalized_type, "other")
    object_names = _boundary_assignment_names(props.get("Objects"))
    face_ids = _boundary_assignment_face_ids(props.get("Faces"))
    record: dict[str, Any] = {
        "name": name,
        "type": boundary_type,
        "kind": kind,
        "assignment_kind": "objects" if object_names else "faces" if face_ids else "unavailable",
        "object_names": object_names,
        "face_ids": face_ids,
        "property_digest": _digest(_json_value(props)),
    }
    if kind not in {"wave_port", "lumped_port"}:
        record["options"] = {}
        return record
    try:
        modes = dict(props.get("Modes") or {})
    except Exception:
        modes = {}
    mode_records = []
    for mode_name in sorted(modes, key=str.casefold):
        try:
            mode = dict(modes[mode_name] or {})
        except Exception:
            mode = {}
        int_line = _boundary_integration_line(mode.get("IntLine"))
        mode_records.append(
            {
                "name": str(mode_name),
                "mode_number": int(mode.get("ModeNum") or 0),
                "use_integration_line": _boundary_readback_bool(
                    mode.get("UseIntLine"),
                    False,
                ),
                "integration_line": int_line,
                "characteristic_impedance": str(mode.get("CharImp") or "").strip(),
            }
        )
    options: dict[str, Any] = {
        "renormalize": _boundary_readback_bool(
            props.get("RenormalizeAllTerminals"),
            True,
        ),
        "deembed_enabled": _boundary_readback_bool(props.get("DoDeembed"), False),
        "integration_line": next(
            (
                item["integration_line"]
                for item in mode_records
                if item["use_integration_line"]
            ),
            {"start": [], "end": []},
        ),
        "modes": mode_records,
    }
    if kind == "wave_port":
        try:
            options["mode_count"] = int(props.get("NumModes") or len(mode_records))
        except (TypeError, ValueError):
            options["mode_count"] = 0
        options["deembed_distance"] = str(props.get("DeembedDist") or "").strip()
    else:
        options["mode_count"] = len(mode_records)
        options["impedance"] = str(props.get("Impedance") or "").strip()
    record["options"] = options
    return record


def _create_hfss_boundary(
    app: Any,
    spec: dict[str, Any],
    face_ids: list[int],
    *,
    resolved_integration_line: dict[str, list[str]] | None = None,
) -> Any:
    if spec["boundary_kind"] == "radiation":
        return app.assign_radiation_boundary_to_faces(
            face_ids,
            name=spec["boundary_name"],
        )
    if "integration_line_direction" in spec["options"]:
        if not isinstance(resolved_integration_line, dict):
            raise LiveBackendError("resolved HFSS port integration line is unavailable")
        integration_line = [
            list(resolved_integration_line.get("start") or []),
            list(resolved_integration_line.get("end") or []),
        ]
        if spec["boundary_kind"] == "wave_port":
            return app.wave_port(
                assignment=face_ids[0],
                reference=None,
                name=spec["boundary_name"],
                integration_line=integration_line,
                modes=spec["options"]["modes"],
                renormalize=spec["options"]["renormalize"],
                deembed=spec["options"]["deembed"],
                characteristic_impedance=spec["options"]["characteristic_impedance"],
            )
        model_units = str(_safe_attribute(app.modeler, "model_units") or "").strip()
        return app.lumped_port(
            assignment=spec["assignment_object_name"],
            reference=None,
            name=spec["boundary_name"],
            integration_line=_numeric_integration_line(integration_line, model_units),
            impedance=spec["options"]["impedance"],
            renormalize=spec["options"]["renormalize"],
            deembed=spec["options"]["deembed"],
        )
    if len(face_ids) != 1:
        raise LiveBackendError(f"{spec['boundary_kind']} requires exactly one resolved face")
    method = app.wave_port if spec["boundary_kind"] == "wave_port" else app.lumped_port
    assignment = (
        spec.get("assignment_object")
        if spec["boundary_kind"] == "lumped_port"
        else face_ids[0]
    )
    return method(
        assignment=assignment,
        reference=spec["references"] or None,
        name=spec["boundary_name"],
        **spec["options"],
    )


def _numeric_integration_line(
    integration_line: list[list[str]],
    unit: str,
) -> list[list[float]]:
    if not re.fullmatch(r"[A-Za-z]+", unit):
        raise LiveBackendError("HFSS model units are unavailable for port creation")
    numeric: list[list[float]] = []
    for point in integration_line:
        if len(point) != 3:
            raise LiveBackendError("HFSS port integration point must contain three coordinates")
        values = []
        for expression in point:
            match = re.fullmatch(
                r"\s*([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)\s*([A-Za-z]+)\s*",
                str(expression),
            )
            if not match or match.group(2).casefold() != unit.casefold():
                raise LiveBackendError("HFSS port integration coordinate unit mismatch")
            values.append(float(match.group(1)))
        numeric.append(values)
    return numeric


def _verify_hfss_boundary_readback(
    spec: dict[str, Any],
    readback: dict[str, Any],
    resolved_integration_line: dict[str, list[str]] | None,
) -> None:
    if readback.get("kind") != spec["boundary_kind"]:
        raise LiveBackendError("HFSS boundary type readback failed")
    if readback.get("face_ids") != spec["assignment_face_ids"]:
        raise LiveBackendError("HFSS boundary face assignment readback failed")
    expected_objects = [spec["assignment_object_name"]] if spec["assignment_object_name"] else []
    if readback.get("object_names") != expected_objects:
        raise LiveBackendError("HFSS boundary object assignment readback failed")
    if spec["boundary_kind"] == "radiation":
        return
    expected = spec["options"]
    actual = dict(readback.get("options") or {})
    if actual.get("renormalize") is not expected["renormalize"]:
        raise LiveBackendError("HFSS port renormalization readback failed")
    if not _integration_line_readback_matches(
        actual.get("integration_line"),
        resolved_integration_line,
    ):
        raise LiveBackendError("HFSS port integration line readback failed")
    if spec["boundary_kind"] == "wave_port":
        if actual.get("mode_count") != expected["modes"]:
            raise LiveBackendError("HFSS wave-port mode count readback failed")
        modes = list(actual.get("modes") or [])
        if len(modes) != expected["modes"] or any(
            item.get("characteristic_impedance") != expected["characteristic_impedance"]
            for item in modes
        ):
            raise LiveBackendError("HFSS wave-port characteristic impedance readback failed")
        deembed_enabled = expected["deembed"] > 0
        if actual.get("deembed_enabled") is not deembed_enabled:
            raise LiveBackendError("HFSS wave-port deembed enable readback failed")
        if deembed_enabled and not _quantity_boundary_readback_matches(
            actual.get("deembed_distance"),
            expected["deembed"],
            "mm",
        ):
            raise LiveBackendError("HFSS wave-port deembed distance readback failed")
        return
    if actual.get("mode_count") != 1:
        raise LiveBackendError("HFSS lumped-port mode count readback failed")
    if actual.get("deembed_enabled") is not expected["deembed"]:
        raise LiveBackendError("HFSS lumped-port deembed readback failed")
    if not _quantity_boundary_readback_matches(
        actual.get("impedance"),
        expected["impedance"],
        "ohm",
    ):
        raise LiveBackendError("HFSS lumped-port impedance readback failed")


def _rollback_hfss_boundary(
    app: Any,
    created_name: str,
    *,
    before_boundaries: list[dict[str, Any]],
) -> dict[str, Any]:
    before_names = {item["name"] for item in before_boundaries}
    delete_error = ""
    try:
        current = _hfss_port_boundary_snapshot(app)
        if created_name in {item["name"] for item in current} and created_name not in before_names:
            boundary = next(
                item for item in list(app.boundaries or []) if item.name == created_name
            )
            if boundary.delete() is not True:
                raise LiveBackendError("HFSS boundary delete returned false")
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
        after = _hfss_port_boundary_snapshot(app)
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


def _normalize_hfss_antipad_subtract(args: dict[str, Any]) -> dict[str, Any]:
    blank_name = str(args.get("blank_object_name") or "").strip()
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(blank_name):
        raise LiveBackendError("blank_object_name must be a safe AEDT object name")
    center = args.get("center")
    if not isinstance(center, list) or len(center) != 2:
        raise LiveBackendError("center must contain exactly two numeric model-unit values")
    center_values = [
        _bounded_float(value, f"center[{index}]", minimum=-1e9, maximum=1e9)
        for index, value in enumerate(center)
    ]
    radius = _bounded_float(args.get("radius"), "radius", minimum=0.0, maximum=1e9)
    if math.isclose(radius, 0.0, rel_tol=0.0, abs_tol=1e-15):
        raise LiveBackendError("radius must be greater than zero")
    tool_name = str(args.get("tool_name") or "").strip()
    if not tool_name:
        tool_name = "__AEDT_AGENT_AP_" + _digest(
            {"blank": blank_name, "center": center_values, "radius": radius}
        )[:12]
    if not _SAFE_AEDT_OBJECT_NAME.fullmatch(tool_name):
        raise LiveBackendError("tool_name must be a safe AEDT object name")
    if blank_name.casefold() == tool_name.casefold():
        raise LiveBackendError("tool_name must differ from blank_object_name")
    return {
        "blank_object_name": blank_name,
        "tool_name": tool_name,
        "center": center_values,
        "radius": radius,
    }


def _hfss_antipad_subtract_state(app: Any, blank_name: str) -> dict[str, Any]:
    state = _hfss_geometry_rotation_state(app, [blank_name])
    if len(state["geometry"]) > 5000:
        raise LiveBackendError("HFSS geometry catalog exceeds the 5000 object safety limit")
    blank = _exact_hfss_geometry_target(state, blank_name)
    return {
        **state,
        "blank_material": _hfss_material_snapshot(app, blank["material_name"]),
    }


def _exact_hfss_geometry_target(state: dict[str, Any], requested: str) -> dict[str, Any]:
    exact = [item for item in state["geometry"] if item["name"] == requested]
    if len(exact) == 1:
        return exact[0]
    folded = [item for item in state["geometry"] if item["name"].casefold() == requested.casefold()]
    if folded:
        raise LiveBackendError(
            f"blank_object_name must match AEDT case exactly: {folded[0]['name']}"
        )
    raise LiveBackendError(f"HFSS anti-pad blank object does not exist: {requested}")


def _complete_hfss_antipad_spec(
    spec: dict[str, Any],
    blank: dict[str, Any],
    model_units: str,
    material: dict[str, Any],
) -> dict[str, Any]:
    _validate_hfss_geometry_move_target(blank)
    volume = _optional_float(blank.get("volume"))
    if volume is None or volume <= 0:
        raise LiveBackendError("HFSS anti-pad blank must be a solid object")
    material_name = str(blank.get("material_name") or "").strip()
    if not material_name or material.get("canonical_name", "").casefold() != material_name.casefold():
        raise LiveBackendError("HFSS anti-pad blank material readback is inconsistent")
    if material.get("is_dielectric") is not False:
        raise LiveBackendError("HFSS anti-pad blank must use a conductor-classified material")
    bbox = [float(value) for value in blank["bounding_box"]]
    x_span, y_span, thickness = (
        bbox[3] - bbox[0],
        bbox[4] - bbox[1],
        bbox[5] - bbox[2],
    )
    if min(x_span, y_span, thickness) <= 0:
        raise LiveBackendError("HFSS anti-pad blank must have a finite 3D bounding box")
    if thickness >= min(x_span, y_span):
        raise LiveBackendError("HFSS anti-pad blank must be a Z-normal thin layer solid")
    x, y = spec["center"]
    radius = spec["radius"]
    tolerance = max(radius * 1e-9, 1e-12)
    if (
        x - radius < bbox[0] - tolerance
        or x + radius > bbox[3] + tolerance
        or y - radius < bbox[1] - tolerance
        or y + radius > bbox[4] + tolerance
    ):
        raise LiveBackendError("HFSS anti-pad circle must fit inside the blank XY bounding box")
    overshoot = max(thickness * 0.1, radius * 1e-6, 1e-9)
    expected_removed_volume = math.pi * radius * radius * thickness
    if expected_removed_volume >= volume:
        raise LiveBackendError("HFSS anti-pad would remove the entire blank object")
    return {
        **spec,
        "model_units": model_units,
        "tool_axis": "Z",
        "tool_origin": [x, y, bbox[2] - overshoot],
        "tool_height": thickness + 2.0 * overshoot,
        "blank_z_range": [bbox[2], bbox[5]],
        "expected_removed_volume": expected_removed_volume,
        "tool_overshoot": overshoot,
        "blank_material_digest": material["definition_digest"],
    }


def _verify_hfss_antipad_subtract_state(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    spec: dict[str, Any],
) -> None:
    for field in (
        "design_type",
        "solution_type",
        "model_units",
        "active_coordinate_system",
        "boundaries",
        "mesh_operations",
        "blank_material",
    ):
        if after[field] != before[field]:
            raise LiveBackendError(f"HFSS anti-pad subtraction changed protected state: {field}")
    before_by_name = {item["name"]: item for item in before["geometry"]}
    after_by_name = {item["name"]: item for item in after["geometry"]}
    if set(after_by_name) != set(before_by_name):
        raise LiveBackendError("HFSS anti-pad subtraction changed the object catalog")
    blank_name = spec["blank_object_name"]
    for name, original in before_by_name.items():
        current = after_by_name[name]
        if name != blank_name:
            if current != original:
                raise LiveBackendError(f"HFSS anti-pad subtraction changed non-target geometry: {name}")
            continue
        for field in ("name", "object_id", "material_name", "solve_inside", "bounding_box"):
            if current.get(field) != original.get(field):
                raise LiveBackendError(
                    f"HFSS anti-pad subtraction changed protected blank state: {field}"
                )
        before_volume = float(original["volume"])
        after_volume = float(current["volume"])
        removed = before_volume - after_volume
        expected = float(spec["expected_removed_volume"])
        if after_volume <= 0 or not math.isclose(
            removed,
            expected,
            rel_tol=1e-6,
            abs_tol=max(1e-9, expected * 1e-8),
        ):
            raise LiveBackendError(
                "HFSS anti-pad removed volume does not prove a full through-layer circular cut"
            )
        if len(current.get("faces") or []) <= len(original.get("faces") or []):
            raise LiveBackendError("HFSS anti-pad cylindrical cut face was not observed")


def _rollback_hfss_antipad_subtract(
    app: Any,
    spec: dict[str, Any],
    *,
    before_state: dict[str, Any],
    created_tool_name: str,
    subtract_attempted: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    modeler = _safe_attribute(app, "modeler")
    cleanup = getattr(modeler, "cleanup_objects", None)
    if callable(cleanup):
        try:
            cleanup()
        except Exception:
            pass
    current_names = [str(item) for item in list(getattr(modeler, "object_names", []) or [])]
    tool_present = spec["tool_name"] in current_names
    blank_changed = True
    try:
        current = _hfss_antipad_subtract_state(app, spec["blank_object_name"])
        blank_changed = _exact_hfss_geometry_target(
            current, spec["blank_object_name"]
        ) != _exact_hfss_geometry_target(before_state, spec["blank_object_name"])
    except Exception:
        pass
    if subtract_attempted and (blank_changed or not tool_present):
        undo = getattr(_safe_attribute(app, "odesign"), "Undo", None)
        if not callable(undo):
            errors.append("HFSS design Undo API is unavailable")
        else:
            try:
                undo()
                if callable(cleanup):
                    cleanup()
            except Exception as exc:
                errors.append(f"undo: {type(exc).__name__}: {exc}")
    current_names = [str(item) for item in list(getattr(modeler, "object_names", []) or [])]
    before_names = {item["name"] for item in before_state["geometry"]}
    candidates = [
        name
        for name in dict.fromkeys((created_tool_name, spec["tool_name"]))
        if name and name in current_names and name not in before_names
    ]
    if candidates:
        try:
            deleted = modeler.delete(candidates)
            if deleted is False:
                raise LiveBackendError("tool delete returned false")
            if callable(cleanup):
                cleanup()
        except Exception as exc:
            errors.append(f"tool_delete: {type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        restored = _hfss_antipad_subtract_state(app, spec["blank_object_name"])
    except Exception as exc:
        restored = {}
        readback_error = f"{type(exc).__name__}: {exc}"
    state_restored = bool(restored) and restored == before_state
    return {
        "complete": not errors and not readback_error and state_restored,
        "state_restored": state_restored,
        "errors": errors,
        "readback_error": readback_error,
    }


def _normalize_hfss_geometry_moves(args: dict[str, Any]) -> dict[str, Any]:
    max_objects = _bounded_integer(
        args.get("max_objects", 16),
        "max_objects",
        minimum=1,
        maximum=32,
    )
    raw_moves = args.get("moves")
    if not isinstance(raw_moves, list) or not raw_moves:
        raise LiveBackendError("moves must be a non-empty list")
    if len(raw_moves) > max_objects:
        raise LiveBackendError(
            f"geometry move count {len(raw_moves)} exceeds max_objects {max_objects}"
        )
    moves = []
    seen = set()
    for index, raw in enumerate(raw_moves):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"moves[{index}] must be an object")
        unsupported = sorted(set(raw).difference({"name", "vector"}))
        if unsupported:
            raise LiveBackendError(
                f"unsupported moves[{index}] field: {unsupported[0]}"
            )
        name = str(raw.get("name") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
            raise LiveBackendError(f"moves[{index}].name must be a safe AEDT object name")
        folded = name.casefold()
        if folded in seen:
            raise LiveBackendError(f"moves must not contain duplicate object names: {name}")
        seen.add(folded)
        vector = _finite_numeric_vector(raw.get("vector"), f"moves[{index}].vector")
        if all(math.isclose(float(item), 0.0, rel_tol=0.0, abs_tol=0.0) for item in vector):
            raise LiveBackendError(f"moves[{index}].vector must not be the zero vector")
        moves.append({"name": name, "vector": vector})
    return {
        "moves": moves,
        "names": [item["name"] for item in moves],
        "expected_object_count": len(moves),
        "max_objects": max_objects,
    }


def _hfss_active_coordinate_system(app: Any) -> str:
    editor = _safe_attribute(_safe_attribute(app, "modeler"), "oeditor")
    getter = getattr(editor, "GetActiveCoordinateSystem", None)
    if not callable(getter):
        raise LiveBackendError("HFSS active coordinate system readback is unavailable")
    try:
        active = str(getter() or "").strip()
    except Exception as exc:
        raise LiveBackendError(
            "HFSS active coordinate system readback is unavailable"
        ) from exc
    if not active:
        raise LiveBackendError("HFSS active coordinate system readback is unavailable")
    return active


def _hfss_geometry_move_state(app: Any, target_names: list[str]) -> dict[str, Any]:
    modeler = _safe_attribute(app, "modeler")
    model_units = str(_safe_attribute(modeler, "model_units") or "").strip()
    if not re.fullmatch(r"[A-Za-z]+", model_units):
        raise LiveBackendError("HFSS model units are unavailable for geometry movement")
    names = [str(item) for item in list(getattr(modeler, "object_names", []) or [])]
    if len(names) > 5000:
        raise LiveBackendError("HFSS design has more than 5000 objects; bounded move is unavailable")
    geometry = []
    for name in names:
        try:
            obj = modeler[name]
        except Exception as exc:
            raise LiveBackendError(f"HFSS geometry object readback failed: {name}") from exc
        faces = []
        for face in list(getattr(obj, "faces", []) or []):
            faces.append(
                {
                    "face_id": _json_value(getattr(face, "id", None)),
                    "center": _json_value(_safe_attribute(face, "center")),
                    "area": _json_value(_safe_attribute(face, "area")),
                    "is_planar": _json_value(_safe_attribute(face, "is_planar")),
                }
            )
        faces.sort(key=lambda item: str(item["face_id"]))
        geometry.append(
            _canonical_hfss_geometry_value(
                {
                    "name": name,
                    "object_id": _json_value(getattr(obj, "id", None)),
                    "material_name": str(getattr(obj, "material_name", "") or ""),
                    "solve_inside": _json_value(_safe_attribute(obj, "solve_inside")),
                    "bounding_box": _json_value(_safe_attribute(obj, "bounding_box")),
                    "volume": _json_value(_safe_attribute(obj, "volume")),
                    "faces": faces,
                }
            )
        )
    geometry.sort(key=lambda item: item["name"].casefold())
    by_name = {item["name"]: item for item in geometry}
    targets = [by_name[name] for name in target_names if name in by_name]
    mesh_names = _hfss_mesh_operation_names(app)
    if len(mesh_names) > 500:
        raise LiveBackendError(
            "HFSS design has more than 500 mesh operations; bounded move is unavailable"
        )
    return {
        "design_type": str(_safe_attribute(app, "design_type") or "").strip(),
        "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
        "model_units": model_units,
        "active_coordinate_system": _hfss_active_coordinate_system(app),
        "geometry": geometry,
        "targets": targets,
        "boundaries": _hfss_material_boundary_reference_snapshot(app, []),
        "mesh_operations": _hfss_mesh_operation_snapshot(app, mesh_names),
    }


def _canonical_hfss_geometry_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return value
        rounded = round(value, 12)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, list):
        return [_canonical_hfss_geometry_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical_hfss_geometry_value(item)
            for key, item in value.items()
        }
    return value


def _validate_hfss_geometry_move_target(record: dict[str, Any]) -> None:
    bounding_box = record.get("bounding_box")
    if (
        not isinstance(bounding_box, list)
        or len(bounding_box) != 6
        or any(type(item) not in {int, float} or not math.isfinite(float(item)) for item in bounding_box)
    ):
        raise LiveBackendError(
            f"HFSS geometry move requires a numeric bounding box: {record.get('name', '')}"
        )
    faces = list(record.get("faces") or [])
    if not faces:
        raise LiveBackendError(
            f"HFSS geometry move supports only solid or sheet objects: {record.get('name', '')}"
        )
    for face in faces:
        center = face.get("center")
        if (
            not isinstance(center, list)
            or len(center) != 3
            or any(type(item) not in {int, float} or not math.isfinite(float(item)) for item in center)
        ):
            raise LiveBackendError(
                f"HFSS face center is unavailable for geometry move: {record.get('name', '')}"
            )
        area = face.get("area")
        if type(area) not in {int, float} or not math.isfinite(float(area)) or float(area) < 0:
            raise LiveBackendError(
                f"HFSS face area is unavailable for geometry move: {record.get('name', '')}"
            )


def _translated_hfss_geometry_snapshot(
    before_geometry: list[dict[str, Any]],
    moves: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    vectors = {item["name"]: [float(value) for value in item["vector"]] for item in moves}
    translated = []
    for original in before_geometry:
        record = json.loads(json.dumps(original))
        vector = vectors.get(record["name"])
        if vector is not None:
            bbox = list(record["bounding_box"])
            record["bounding_box"] = [
                float(value) + vector[index % 3] for index, value in enumerate(bbox)
            ]
            for face in record["faces"]:
                face["center"] = [
                    float(value) + vector[index]
                    for index, value in enumerate(face["center"])
                ]
        translated.append(_canonical_hfss_geometry_value(record))
    return translated


def _verify_hfss_geometry_move_state(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    expected_geometry: list[dict[str, Any]],
) -> None:
    for field in (
        "design_type",
        "solution_type",
        "model_units",
        "active_coordinate_system",
        "boundaries",
        "mesh_operations",
    ):
        if after[field] != before[field]:
            raise LiveBackendError(f"HFSS geometry move changed protected state: {field}")
    if after["geometry"] != expected_geometry:
        raise LiveBackendError("HFSS geometry move typed readback does not match requested vectors")


def _rollback_hfss_geometry_moves(
    app: Any,
    moved: list[dict[str, Any]],
    *,
    before_state: dict[str, Any],
) -> dict[str, Any]:
    errors = []
    modeler = _safe_attribute(app, "modeler")
    mover = getattr(modeler, "move", None)
    if not callable(mover):
        errors.append("HFSS geometry move API is unavailable")
    else:
        for move in reversed(moved):
            inverse = [-float(item) for item in move["vector"]]
            try:
                if mover([move["name"]], inverse) is not True:
                    raise LiveBackendError("inverse move returned false")
            except Exception as exc:
                errors.append(f"{move['name']}: {type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        after = _hfss_geometry_move_state(
            app,
            [item["name"] for item in before_state["targets"]],
        )
    except Exception as exc:
        after = {}
        readback_error = f"{type(exc).__name__}: {exc}"
    state_restored = bool(after) and after == before_state
    return {
        "complete": not errors and not readback_error and state_restored,
        "restored_object_names": [item["name"] for item in moved] if state_restored else [],
        "state_restored": state_restored,
        "move_errors": errors,
        "readback_error": readback_error,
    }


def _normalize_hfss_geometry_rotations(args: dict[str, Any]) -> dict[str, Any]:
    max_objects = _bounded_integer(
        args.get("max_objects", 16),
        "max_objects",
        minimum=1,
        maximum=32,
    )
    raw_rotations = args.get("rotations")
    if not isinstance(raw_rotations, list) or not raw_rotations:
        raise LiveBackendError("rotations must be a non-empty list")
    if len(raw_rotations) > max_objects:
        raise LiveBackendError(
            f"geometry rotation count {len(raw_rotations)} exceeds max_objects {max_objects}"
        )
    rotations = []
    seen = set()
    for index, raw in enumerate(raw_rotations):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"rotations[{index}] must be an object")
        unsupported = sorted(set(raw).difference({"name", "axis", "angle_degrees"}))
        if unsupported:
            raise LiveBackendError(
                f"unsupported rotations[{index}] field: {unsupported[0]}"
            )
        name = str(raw.get("name") or "").strip()
        if not _SAFE_AEDT_OBJECT_NAME.fullmatch(name):
            raise LiveBackendError(
                f"rotations[{index}].name must be a safe AEDT object name"
            )
        folded = name.casefold()
        if folded in seen:
            raise LiveBackendError(
                f"rotations must not contain duplicate object names: {name}"
            )
        seen.add(folded)
        axis = str(raw.get("axis") or "").strip().upper()
        if axis not in {"X", "Y", "Z"}:
            raise LiveBackendError(f"rotations[{index}].axis must be X, Y, or Z")
        angle = _bounded_float(
            raw.get("angle_degrees"),
            f"rotations[{index}].angle_degrees",
            minimum=-360.0,
            maximum=360.0,
        )
        if math.isclose(abs(angle), 0.0, rel_tol=0.0, abs_tol=1e-12) or math.isclose(
            abs(angle),
            360.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise LiveBackendError(
                f"rotations[{index}].angle_degrees must produce a nonzero rotation"
            )
        rotations.append(
            {
                "name": name,
                "axis": axis,
                "angle_degrees": angle,
            }
        )
    return {
        "rotations": rotations,
        "names": [item["name"] for item in rotations],
        "expected_object_count": len(rotations),
        "max_objects": max_objects,
    }


def _hfss_geometry_rotation_state(app: Any, target_names: list[str]) -> dict[str, Any]:
    state = _hfss_geometry_move_state(app, target_names)
    modeler = _safe_attribute(app, "modeler")
    geometry = []
    for original in state["geometry"]:
        try:
            obj = modeler[original["name"]]
        except Exception as exc:
            raise LiveBackendError(
                f"HFSS geometry object readback failed: {original['name']}"
            ) from exc
        vertices = []
        for vertex in list(getattr(obj, "vertices", []) or []):
            vertices.append(
                {
                    "vertex_id": _json_value(getattr(vertex, "id", None)),
                    "position": _json_value(_safe_attribute(vertex, "position")),
                }
            )
        vertices.sort(key=lambda item: str(item["vertex_id"]))
        geometry.append(
            _canonical_hfss_geometry_value({**original, "vertices": vertices})
        )
    geometry.sort(key=lambda item: item["name"].casefold())
    by_name = {item["name"]: item for item in geometry}
    return {
        **state,
        "geometry": geometry,
        "targets": [by_name[name] for name in target_names if name in by_name],
    }


def _validate_hfss_geometry_rotation_target(
    record: dict[str, Any],
    rotation: dict[str, Any],
) -> None:
    _validate_hfss_geometry_move_target(record)
    points = [list(item["center"]) for item in record["faces"]]
    for vertex in list(record.get("vertices") or []):
        position = vertex.get("position")
        if (
            not isinstance(position, list)
            or len(position) != 3
            or any(
                type(item) not in {int, float} or not math.isfinite(float(item))
                for item in position
            )
        ):
            raise LiveBackendError(
                f"HFSS vertex position is unavailable for geometry rotation: {record.get('name', '')}"
            )
        points.append(list(position))
    if not any(
        not _hfss_rotation_points_close(
            point,
            _rotate_hfss_point(
                point,
                rotation["axis"],
                rotation["angle_degrees"],
            ),
        )
        for point in points
    ):
        raise LiveBackendError(
            f"HFSS geometry rotation is not observable from face or vertex readback: {record.get('name', '')}"
        )


def _rotate_hfss_point(
    point: list[int | float],
    axis: str,
    angle_degrees: float,
) -> list[float]:
    angle = math.radians(float(angle_degrees))
    cosine = math.cos(angle)
    sine = math.sin(angle)
    x, y, z = (float(item) for item in point)
    if axis == "X":
        rotated = [x, cosine * y - sine * z, sine * y + cosine * z]
    elif axis == "Y":
        rotated = [cosine * x + sine * z, y, -sine * x + cosine * z]
    else:
        rotated = [cosine * x - sine * y, sine * x + cosine * y, z]
    return _canonical_hfss_geometry_value(rotated)


def _hfss_rotation_points_close(actual: Any, expected: Any) -> bool:
    if not isinstance(actual, list) or not isinstance(expected, list):
        return False
    if len(actual) != 3 or len(expected) != 3:
        return False
    return all(
        type(left) in {int, float}
        and type(right) in {int, float}
        and math.isfinite(float(left))
        and math.isfinite(float(right))
        and math.isclose(
            float(left),
            float(right),
            rel_tol=1e-10,
            abs_tol=1e-9,
        )
        for left, right in zip(actual, expected)
    )


def _verify_hfss_geometry_rotation_state(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    rotations: list[dict[str, Any]],
) -> None:
    for field in (
        "design_type",
        "solution_type",
        "model_units",
        "active_coordinate_system",
        "boundaries",
        "mesh_operations",
    ):
        if after[field] != before[field]:
            raise LiveBackendError(f"HFSS geometry rotation changed protected state: {field}")
    before_by_name = {item["name"]: item for item in before["geometry"]}
    after_by_name = {item["name"]: item for item in after["geometry"]}
    if set(after_by_name) != set(before_by_name):
        raise LiveBackendError("HFSS geometry rotation changed the geometry object catalog")
    rotations_by_name = {item["name"]: item for item in rotations}
    for name, original in before_by_name.items():
        current = after_by_name[name]
        rotation = rotations_by_name.get(name)
        if rotation is None:
            if current != original:
                raise LiveBackendError(
                    f"HFSS geometry rotation changed a non-target object: {name}"
                )
            continue
        _verify_hfss_rotated_object(original, current, rotation)


def _verify_hfss_rotated_object(
    before: dict[str, Any],
    after: dict[str, Any],
    rotation: dict[str, Any],
) -> None:
    name = before["name"]
    for field in (
        "name",
        "object_id",
        "material_name",
        "solve_inside",
        "volume",
    ):
        if after.get(field) != before.get(field):
            raise LiveBackendError(
                f"HFSS geometry rotation changed protected target state: {name}.{field}"
            )
    _validate_hfss_geometry_move_target(after)
    before_faces = {item["face_id"]: item for item in before["faces"]}
    after_faces = {item["face_id"]: item for item in after["faces"]}
    if set(after_faces) != set(before_faces):
        raise LiveBackendError(f"HFSS geometry rotation changed face identity: {name}")
    for face_id, original in before_faces.items():
        current = after_faces[face_id]
        for field in ("face_id", "area", "is_planar"):
            if current.get(field) != original.get(field):
                raise LiveBackendError(
                    f"HFSS geometry rotation changed face state: {name}.{face_id}.{field}"
                )
        expected = _rotate_hfss_point(
            original["center"],
            rotation["axis"],
            rotation["angle_degrees"],
        )
        if not _hfss_rotation_points_close(current.get("center"), expected):
            raise LiveBackendError(
                f"HFSS geometry rotation face-center readback failed: {name}.{face_id}"
            )
    before_vertices = {item["vertex_id"]: item for item in before.get("vertices") or []}
    after_vertices = {item["vertex_id"]: item for item in after.get("vertices") or []}
    if set(after_vertices) != set(before_vertices):
        raise LiveBackendError(f"HFSS geometry rotation changed vertex identity: {name}")
    for vertex_id, original in before_vertices.items():
        expected = _rotate_hfss_point(
            original["position"],
            rotation["axis"],
            rotation["angle_degrees"],
        )
        if not _hfss_rotation_points_close(
            after_vertices[vertex_id].get("position"),
            expected,
        ):
            raise LiveBackendError(
                f"HFSS geometry rotation vertex readback failed: {name}.{vertex_id}"
            )
    _verify_hfss_points_inside_bounding_box(after)


def _verify_hfss_points_inside_bounding_box(record: dict[str, Any]) -> None:
    bbox = list(record["bounding_box"])
    points = [item["center"] for item in record["faces"]]
    points.extend(item["position"] for item in record.get("vertices") or [])
    for point in points:
        if any(
            float(point[index]) < float(bbox[index]) - 1e-9
            or float(point[index]) > float(bbox[index + 3]) + 1e-9
            for index in range(3)
        ):
            raise LiveBackendError(
                f"HFSS geometry rotation produced inconsistent bounding-box readback: {record['name']}"
            )


def _rollback_hfss_geometry_rotations(
    app: Any,
    rotated: list[dict[str, Any]],
    *,
    before_state: dict[str, Any],
) -> dict[str, Any]:
    errors = []
    modeler = _safe_attribute(app, "modeler")
    rotator = getattr(modeler, "rotate", None)
    if not callable(rotator):
        errors.append("HFSS geometry rotation API is unavailable")
    else:
        for rotation in reversed(rotated):
            try:
                if rotator(
                    [rotation["name"]],
                    rotation["axis"],
                    angle=-float(rotation["angle_degrees"]),
                    units="deg",
                ) is not True:
                    raise LiveBackendError("inverse rotation returned false")
            except Exception as exc:
                errors.append(f"{rotation['name']}: {type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        after = _hfss_geometry_rotation_state(
            app,
            [item["name"] for item in before_state["targets"]],
        )
    except Exception as exc:
        after = {}
        readback_error = f"{type(exc).__name__}: {exc}"
    state_restored = bool(after) and after == before_state
    return {
        "complete": not errors and not readback_error and state_restored,
        "restored_object_names": [item["name"] for item in rotated]
        if state_restored
        else [],
        "state_restored": state_restored,
        "rotation_errors": errors,
        "readback_error": readback_error,
    }


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


def _layout_native_property_schema(object_kind: str | None = None) -> list[dict[str, Any]]:
    kinds = (object_kind,) if object_kind else tuple(sorted(_LAYOUT_NATIVE_PROPERTY_SCHEMA))
    result = []
    for kind in kinds:
        definition = _LAYOUT_NATIVE_PROPERTY_SCHEMA[kind]
        result.append(
            {
                "id": kind,
                "max_objects": definition["max_objects"],
                "max_properties": definition["max_properties"],
                "profiles": [
                    {"id": profile, "property_ids": list(property_ids)}
                    for profile, property_ids in sorted(definition["profiles"].items())
                ],
                "properties": [
                    {"id": property_id, "value_type": properties["value_type"]}
                    for property_id, properties in sorted(definition["properties"].items())
                ],
            }
        )
    return result


def _layout_native_profile_property_ids(object_kind: str, profile: str) -> list[str] | None:
    values = _LAYOUT_NATIVE_PROPERTY_SCHEMA[object_kind]["profiles"].get(profile)
    return list(values) if values else None


def _layout_native_property_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise LiveBackendError("property_ids must be a non-empty list from the published property schema")
    if len(raw) > 8:
        raise LiveBackendError("property_ids exceeds the approved maximum of 8")
    property_ids = []
    seen = set()
    for item in raw:
        value = str(item).strip()
        normalized = value.casefold()
        if not value or normalized in seen:
            raise LiveBackendError("property_ids must contain unique canonical property ids")
        property_ids.append(value)
        seen.add(normalized)
    return property_ids


def _layout_property_not_supported_response(
    app: Any,
    *,
    object_kind: str,
    names: list[str],
    profile: str,
    unsupported_property_ids: list[str],
    reason: str,
) -> dict[str, Any]:
    response = {
        "schema_version": "layout_native_property/v1",
        "project_name": app.project_name,
        "design_name": app.design_name,
        "object_kind": object_kind,
        "profile": profile,
        "property_ids": [],
        "count": len(names),
        "records": [],
        "status": reason,
        "unsupported_property_ids": unsupported_property_ids,
        "design_unchanged": True,
    }
    response["response_digest"] = _digest(
        {
            "object_kind": object_kind,
            "profile": profile,
            "unsupported_property_ids": unsupported_property_ids,
            "reason": reason,
        }
    )
    return response


def _layout_native_property_records(
    app: Any,
    object_kind: str,
    names: list[str],
    property_ids: list[str],
    *,
    available_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    properties = _LAYOUT_NATIVE_PROPERTY_SCHEMA[object_kind]["properties"]
    try:
        get_property_value = getattr(_layout_modeler_editor(app), "GetPropertyValue", None)
        if not callable(get_property_value):
            raise LiveBackendError("3D Layout native property API is unavailable")
    except Exception:
        return [
            {
                "name": name,
                "status": "unavailable",
                "properties": {
                    property_id: {"status": "unavailable"} for property_id in property_ids
                },
            }
            for name in names
        ]
    records = []
    for name in names:
        # PyAEDT's already-open collection is the trusted exact-name inventory.
        # Some AEDT releases reject the otherwise read-only oEditor.FindObjects call.
        # Do not make that optional native lookup a prerequisite for property reads.
        if available_names is not None:
            exists = name in available_names
        else:
            exists = _layout_native_name_matches(app, name) == [name]
        if not exists:
            records.append({"name": name, "status": "not_found", "properties": {}})
            continue
        values: dict[str, dict[str, Any]] = {}
        for property_id in property_ids:
            definition = properties[property_id]
            try:
                raw = _json_value(get_property_value("BaseElementTab", name, definition["native_name"]))
                values[property_id] = _layout_native_property_value(property_id, raw)
            except Exception:
                values[property_id] = {"status": "read_failed"}
        status = "ok" if all(value["status"] == "ok" for value in values.values()) else "partial"
        records.append({"name": name, "status": status, "properties": values})
    return records


def _layout_native_property_value(property_id: str, raw: Any) -> dict[str, Any]:
    if property_id == "location":
        return _layout_via_target_value(property_id, raw)
    text = str(raw).strip()
    if not text:
        return {"status": "invalid_value", "raw": raw}
    if property_id == "lock_position":
        normalized = text.casefold()
        if normalized in {"true", "false"}:
            return {"status": "ok", "raw": raw, "value": normalized == "true"}
        return {"status": "invalid_value", "raw": raw}
    return {"status": "ok", "raw": raw, "value": text}


def _layout_via_target_inventory(
    app: Any,
    *,
    object_kind: str,
    collection: dict[str, Any],
    requested: list[str],
    profile: str,
    max_items: Any,
) -> dict[str, Any]:
    if object_kind != "via" or profile != "via_target/v1":
        raise LiveBackendError("profile via_target/v1 is available only for object_kind via")
    limit = _bounded_integer(max_items, "max_items", minimum=1, maximum=50)
    names = _unique_nonempty_names(requested, "names")
    if names and len(names) > limit:
        raise LiveBackendError(f"names count {len(names)} exceeds max_items {limit}")
    truncated = False
    if not names:
        all_names = sorted(str(name) for name in collection)
        names = all_names[:limit]
        truncated = len(all_names) > len(names)
    native_records = _layout_native_property_records(
        app,
        "via",
        names,
        _layout_native_profile_property_ids("via", "via_target/v1") or [],
        available_names=set(collection),
    )
    records = [_layout_via_target_record_from_native(record) for record in native_records]
    complete = bool(records) and all(record["status"] == "ok" for record in records)
    return {
        "project_name": app.project_name,
        "design_name": app.design_name,
        "object_kind": "via",
        "profile": "via_target/v1",
        "max_items": limit,
        "count": len(records),
        "objects": records,
        "not_found_names": [
            record["name"] for record in records if record["status"] == "not_found"
        ],
        "truncated": truncated,
        "status": "ok" if complete and not truncated else "partial",
        "snapshot_digest": _digest(records),
        "design_unchanged": True,
    }


def _layout_via_target_not_found(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "not_found",
        "target_eligible": False,
        "values": {},
    }


def _layout_via_target_record_from_native(record: dict[str, Any]) -> dict[str, Any]:
    if record["status"] == "not_found":
        return _layout_via_target_not_found(record["name"])
    values = dict(record.get("properties") or {})
    eligible = bool(values) and all(value.get("status") == "ok" for value in values.values())
    result: dict[str, Any] = {
        "name": record["name"],
        "status": "ok" if eligible else "partial",
        "target_eligible": eligible,
        "values": values,
    }
    if eligible:
        result["via_target_digest"] = _digest(
            {
                "profile": "via_target/v1",
                "name": record["name"],
                "net": values["net"]["value"],
                "location": values["location"]["value"],
                "start_layer": values["start_layer"]["value"],
                "stop_layer": values["stop_layer"]["value"],
            }
        )
    return result


def _layout_via_target_value(key: str, raw: Any) -> dict[str, Any]:
    text = str(raw).strip()
    if not text:
        return {"status": "invalid_value", "raw": raw, "error": "value is empty"}
    if key != "location":
        return {"status": "ok", "raw": raw, "value": text}
    parts = [item.strip() for item in text.split(",")]
    if len(parts) != 2 or not all(parts):
        return {
            "status": "invalid_value",
            "raw": raw,
            "error": "Location must contain two comma-separated coordinates",
        }
    return {"status": "ok", "raw": raw, "value": {"x": parts[0], "y": parts[1]}}


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


def _layout_modeler_editor(app: Any) -> Any:
    editor = _safe_attribute(_safe_attribute(app, "modeler"), "oeditor")
    if editor is None:
        raise LiveBackendError("3D Layout native editor API is unavailable")
    return editor


def _layout_native_name_matches(app: Any, name: str) -> list[str]:
    return _layout_native_find_objects(app, "Name", name, maximum=2)


def _layout_native_find_objects(
    app: Any,
    field: str,
    value: str,
    *,
    maximum: int,
) -> list[str]:
    """Call one exact native query without inferring session-wide capability state."""

    find_objects = getattr(_layout_modeler_editor(app), "FindObjects", None)
    if not callable(find_objects):
        raise LiveBackendError("3D Layout native object lookup API is unavailable")
    try:
        raw = find_objects(field, value)
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout native object lookup failed for {field}={value!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return _layout_native_name_list(raw, label="object lookup", maximum=maximum)


def _layout_native_name_list(raw: Any, *, label: str, maximum: int) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (str, bytes, dict)):
        raise LiveBackendError(f"3D Layout native {label} returned an invalid response")
    try:
        names = sorted({str(item) for item in list(raw) if str(item)})
    except TypeError as exc:
        raise LiveBackendError(f"3D Layout native {label} returned an invalid response") from exc
    if len(names) > maximum:
        raise LiveBackendError(f"3D Layout native {label} exceeds the limit of {maximum}")
    return names


def _layout_native_line_records(app: Any, selector: dict[str, Any]) -> list[dict[str, str]]:
    """Enumerate lines through the documented native query when a wrapper fails."""

    nets = {str(value) for value in selector.get("nets") or []}
    layers = {str(value) for value in selector.get("layers") or []}
    names = {str(value) for value in selector.get("names") or []}
    target_width = str(selector.get("target_width") or "")
    line_names = _layout_native_find_objects(app, "Type", "line", maximum=100_000)
    get_property_value = getattr(_layout_modeler_editor(app), "GetPropertyValue", None)
    if not callable(get_property_value):
        raise LiveBackendError("3D Layout native line property API is unavailable")
    records = []
    for name in line_names:
        try:
            record = {
                "name": name,
                "net": str(get_property_value("BaseElementTab", name, "Net")),
                "layer": str(get_property_value("BaseElementTab", name, "PlacementLayer")),
                "width_expression": str(get_property_value("BaseElementTab", name, "LineWidth")),
            }
        except Exception as exc:
            raise LiveBackendError(
                f"3D Layout native line property read failed for {name}: {type(exc).__name__}: {exc}"
            ) from exc
        if nets and record["net"] not in nets:
            continue
        if layers and record["layer"] not in layers:
            continue
        if names and record["name"] not in names:
            continue
        if target_width and _normalized_expression(record["width_expression"]) != _normalized_expression(target_width):
            continue
        records.append(record)
    return records


def _layout_exact_name(values: list[str], requested: str, label: str) -> str:
    exact = [item for item in values if item == requested]
    if len(exact) == 1:
        return exact[0]
    folded = [item for item in values if item.casefold() == requested.casefold()]
    if folded:
        raise LiveBackendError(f"{label} must match AEDT case exactly: {folded[0]}")
    raise LiveBackendError(f"{label} does not exist: {requested}")


def _normalize_layout_antipad_circle_create(args: dict[str, Any]) -> dict[str, Any]:
    max_voids = _bounded_integer(
        args.get("max_voids", 16),
        "max_voids",
        minimum=1,
        maximum=32,
    )
    raw_voids = args.get("voids")
    if not isinstance(raw_voids, list) or not raw_voids:
        raise LiveBackendError("voids must be a non-empty list")
    if len(raw_voids) > max_voids:
        raise LiveBackendError(
            f"anti-pad count {len(raw_voids)} exceeds max_voids {max_voids}"
        )
    normalized = []
    names = set()
    for index, raw in enumerate(raw_voids):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"voids[{index}] must be an object")
        unsupported = sorted(set(raw).difference({"name", "owner_name", "center", "radius"}))
        if unsupported:
            raise LiveBackendError(f"unsupported voids[{index}] field: {unsupported[0]}")
        name = str(raw.get("name") or "").strip()
        owner_name = str(raw.get("owner_name") or "").strip()
        for field, value in (("name", name), ("owner_name", owner_name)):
            if not _SAFE_AEDT_OBJECT_NAME.fullmatch(value):
                raise LiveBackendError(
                    f"voids[{index}].{field} must be a safe AEDT object name"
                )
        if name.casefold() in names:
            raise LiveBackendError(f"voids must not contain duplicate names: {name}")
        names.add(name.casefold())
        center = raw.get("center")
        if not isinstance(center, list) or len(center) != 2:
            raise LiveBackendError(
                f"voids[{index}].center must contain two numeric model-unit values"
            )
        center_values = [
            _bounded_float(
                value,
                f"voids[{index}].center[{axis}]",
                minimum=-1e9,
                maximum=1e9,
            )
            for axis, value in enumerate(center)
        ]
        radius = _bounded_float(
            raw.get("radius"),
            f"voids[{index}].radius",
            minimum=0.0,
            maximum=1e9,
        )
        if math.isclose(radius, 0.0, rel_tol=0.0, abs_tol=1e-15):
            raise LiveBackendError(f"voids[{index}].radius must be greater than zero")
        normalized.append(
            {
                "name": name,
                "owner_name": owner_name,
                "center": center_values,
                "radius": radius,
            }
        )
    return {
        "voids": normalized,
        "names": [item["name"] for item in normalized],
        "max_voids": max_voids,
    }


def _layout_antipad_circle_create_state(app: Any, spec: dict[str, Any]) -> dict[str, Any]:
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    if design_type != "HFSS 3D Layout Design":
        raise LiveBackendError("circle-void anti-pad creation requires HFSS 3D Layout Design")
    modeler = _safe_attribute(app, "modeler")
    model_units = str(_safe_attribute(modeler, "model_units") or "").strip()
    if not model_units:
        raise LiveBackendError("3D Layout model units are unavailable")
    circle_void_names, inventory_error = _layout_circle_void_inventory(app)
    global_inventory_status = "verified" if inventory_error is None else "unavailable"
    owner_names = sorted(
        {item["owner_name"] for item in spec["voids"]},
        key=str.casefold,
    )
    owners = [_layout_native_antipad_owner_record(app, name) for name in owner_names]
    target_presence = {
        name: sorted(owner["name"] for owner in owners if name in owner["void_names"])
        for name in spec["names"]
    }
    for name, matches in target_presence.items():
        if matches:
            raise LiveBackendError(f"3D Layout anti-pad object already exists: {name}")
    return {
        "design_type": design_type,
        "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
        "model_units": model_units,
        "owners": owners,
        "circle_void_names": circle_void_names,
        "target_presence": target_presence,
        "target_presence_scope": "owner_void_catalog",
        "verification_scope": "global_inventory" if inventory_error is None else "named_object",
        "global_inventory_status": global_inventory_status,
        "global_inventory_error": inventory_error,
        "global_side_effects_unverified": inventory_error is not None,
    }


def _layout_circle_void_inventory(app: Any) -> tuple[list[str] | None, str | None]:
    """Return the global catalog when supported, otherwise a narrowly scoped fallback.

    AEDT 2024 R2 can expose property reads and CreateCircleVoid while its gRPC
    FindObjects implementation fails for the global circle-void query. That
    limitation must not prevent a named, owner-scoped operation, but malformed
    responses and unrelated backend failures remain fail-closed.
    """

    editor = _layout_modeler_editor(app)
    find_objects = getattr(editor, "FindObjects", None)
    if not callable(find_objects):
        return None, "FindObjects is unavailable"
    try:
        raw_names = find_objects("Type", "circle void")
    except Exception as exc:
        if _layout_global_inventory_is_unavailable(exc):
            return None, f"{type(exc).__name__}: {exc}"
        raise LiveBackendError("3D Layout circle-void inventory failed") from exc
    if raw_names is None:
        raw_names = []
    if isinstance(raw_names, (str, bytes, dict)):
        raise LiveBackendError("3D Layout circle-void inventory returned an invalid response")
    try:
        circle_void_names = sorted(str(item) for item in list(raw_names))
    except TypeError as exc:
        raise LiveBackendError("3D Layout circle-void inventory returned an invalid response") from exc
    if len(circle_void_names) > 100_000:
        raise LiveBackendError("3D Layout circle-void inventory exceeds the safety limit")
    return circle_void_names, None


def _layout_global_inventory_is_unavailable(exc: Exception) -> bool:
    detail = f"{type(exc).__name__}: {exc}".casefold()
    return (
        "grpcapierror" in detail
        or "failed to execute grpc aedt command: findobjects" in detail
        or "findobjects is unavailable" in detail
        or "findobjects is not supported" in detail
    )


def _layout_grpc_inventory_failure(detail: str) -> bool:
    normalized = str(detail).casefold()
    return "grpcapierror" in normalized and (
        "findobjects" in normalized or "getalllayernames" in normalized
    ) or "failed to execute grpc aedt command: findobjects" in normalized or (
        "failed to execute grpc aedt command: getalllayernames" in normalized
    )


def _layout_inventory_failure_reasons(records: list[Any]) -> list[str]:
    return [str(record) for record in records if _layout_grpc_inventory_failure(str(record))]


def _layout_native_object_properties(
    app: Any,
    name: str,
    *,
    maximum_properties: int,
    label: str,
) -> dict[str, Any] | None:
    editor = _layout_modeler_editor(app)
    try:
        raw_properties = editor.GetProperties("BaseElementTab", name)
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout {label} lookup failed: {name}: {type(exc).__name__}: {exc}"
        ) from exc
    if raw_properties is None:
        return None
    if isinstance(raw_properties, (str, bytes, dict)):
        raise LiveBackendError(f"3D Layout {label} property response is invalid: {name}")
    try:
        property_names = [str(item) for item in list(raw_properties)]
    except TypeError as exc:
        raise LiveBackendError(f"3D Layout {label} property response is invalid: {name}") from exc
    if not property_names:
        return None
    if len(property_names) > maximum_properties:
        raise LiveBackendError(f"3D Layout {label} property count is unsupported: {name}")
    try:
        native = {
            prop: _json_value(editor.GetPropertyValue("BaseElementTab", name, prop))
            for prop in property_names
        }
    except Exception as exc:
        raise LiveBackendError(f"3D Layout {label} property readback failed: {name}") from exc
    native_name = str(native.get("Name") or "").strip()
    if native_name != name:
        if native_name.casefold() == name.casefold():
            raise LiveBackendError(f"{label} must match AEDT case exactly: {native_name}")
        raise LiveBackendError(f"3D Layout {label} identity readback failed: {name}")
    return native


def _layout_native_antipad_owner_record(app: Any, name: str) -> dict[str, Any]:
    native = _layout_native_object_properties(
        app,
        name,
        maximum_properties=256,
        label="anti-pad owner",
    )
    if native is None:
        raise LiveBackendError(f"3D Layout anti-pad owner is missing or ambiguous: {name}")
    editor = _layout_modeler_editor(app)
    for required in ("Type", "Name", "PlacementLayer"):
        if required not in native:
            raise LiveBackendError(f"3D Layout anti-pad owner is missing property: {required}")
    if sum(len(str(key)) + len(str(value)) for key, value in native.items()) > 64 * 1024:
        raise LiveBackendError(f"3D Layout anti-pad owner readback exceeds 64 KiB: {name}")
    owner_type = str(native["Type"]).strip().casefold()
    if owner_type not in {"rect", "poly"}:
        raise LiveBackendError("3D Layout anti-pad owner must be a rectangle or polygon")
    layer_name = str(native["PlacementLayer"]).strip()
    stackup = _layout_full_stackup_snapshot(app)
    layer = next((item for item in stackup if item["name"] == layer_name), None)
    if layer is None or str(layer.get("type") or "").casefold() != "signal":
        raise LiveBackendError("3D Layout anti-pad owner must be on a signal layer")
    if layer.get("is_negative") is True:
        raise LiveBackendError(
            "3D Layout anti-pad circle voids on negative signal layers are unsupported"
        )
    try:
        polygon = editor.GetPolygon(name)
        points = []
        for point in list(polygon.GetPoints() or []):
            if int(point.IsArc()) != 0:
                raise LiveBackendError("3D Layout anti-pad owner polygons with arcs are unsupported")
            position = [point.GetX(), point.GetY()]
            points.append(
                [
                    _layout_si_length_in_model_units(
                        value,
                        _safe_attribute(app.modeler, "model_units"),
                        "owner point",
                    )
                    for value in position
                ]
            )
        owner_voids = sorted(str(item) for item in list(editor.GetPolygonVoids(name) or []))
    except LiveBackendError:
        raise
    except Exception as exc:
        raise LiveBackendError(f"3D Layout anti-pad owner geometry readback failed: {name}") from exc
    if len(points) < 3 or len(points) > 100_000:
        raise LiveBackendError("3D Layout anti-pad owner polygon point count is unsupported")
    return {
        "name": str(native["Name"]),
        "type": owner_type,
        "layer_name": layer_name,
        "points": points,
        "void_names": owner_voids,
        "native_property_digest": _digest(native),
    }


def _layout_si_length_in_model_units(value: Any, model_units: str, field: str) -> float:
    try:
        from ansys.aedt.core.generic.constants import unit_converter

        return float(
            unit_converter(
                float(value),
                input_units="meter",
                output_units=model_units,
            )
        )
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout {field} SI readback cannot be converted to {model_units}: {value}"
        ) from exc


def _validate_layout_antipad_inside_owner(
    item: dict[str, Any],
    owner: dict[str, Any],
) -> None:
    point = [float(value) for value in item["center"]]
    polygon = [[float(value) for value in pair] for pair in owner["points"]]
    if not _point_inside_polygon(point, polygon):
        raise LiveBackendError(
            f"3D Layout anti-pad center is outside owner: {item['name']}"
        )
    minimum_distance = min(
        _point_segment_distance(point, polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    )
    if minimum_distance + 1e-12 < float(item["radius"]):
        raise LiveBackendError(
            f"3D Layout anti-pad circle crosses the owner boundary: {item['name']}"
        )


def _point_inside_polygon(point: list[float], polygon: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        x1, y1 = first
        x2, y2 = second
        if _point_segment_distance(point, first, second) <= 1e-12:
            return True
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
    return inside


def _point_segment_distance(
    point: list[float],
    first: list[float],
    second: list[float],
) -> float:
    px, py = point
    x1, y1 = first
    x2, y2 = second
    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-30:
        return math.hypot(px - x1, py - y1)
    ratio = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_squared))
    return math.hypot(px - (x1 + ratio * dx), py - (y1 + ratio * dy))


def _layout_native_circle_void_record(
    app: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    name = spec["name"]
    native = _layout_native_object_properties(
        app,
        name,
        maximum_properties=128,
        label="anti-pad",
    )
    if native is None:
        raise LiveBackendError(f"3D Layout anti-pad is missing or ambiguous: {name}")
    editor = _layout_modeler_editor(app)
    try:
        owner_voids = [str(item) for item in list(editor.GetPolygonVoids(spec["owner_name"]) or [])]
    except Exception as exc:
        raise LiveBackendError(f"3D Layout anti-pad readback failed: {name}") from exc
    for required in ("Type", "Name", "PlacementLayer", "Center", "Radius", "LockPosition"):
        if required not in native:
            raise LiveBackendError(f"3D Layout anti-pad is missing property: {required}")
    if sum(len(str(key)) + len(str(value)) for key, value in native.items()) > 32 * 1024:
        raise LiveBackendError(f"3D Layout anti-pad readback exceeds 32 KiB: {name}")
    center_parts = [item.strip() for item in str(native["Center"]).split(",")]
    if len(center_parts) != 2:
        raise LiveBackendError(f"3D Layout anti-pad center readback is invalid: {name}")
    try:
        center = [float(item) for item in center_parts]
    except ValueError as exc:
        raise LiveBackendError(f"3D Layout anti-pad center is non-numeric: {name}") from exc
    radius = _layout_length_in_model_units(native["Radius"], app.modeler.model_units, "Radius")
    return {
        "name": str(native["Name"]),
        "type": str(native["Type"]),
        "owner_name": spec["owner_name"],
        "layer_name": str(native["PlacementLayer"]),
        "center": center,
        "radius": radius,
        "lock_position": _layout_native_bool(native["LockPosition"], "LockPosition"),
        "owner_membership_verified": owner_voids.count(name) == 1,
        "native_property_digest": _digest(native),
    }


def _verify_layout_antipad_circle_create_state(
    app: Any,
    spec: dict[str, Any],
    readback: list[dict[str, Any]],
    *,
    before_state: dict[str, Any],
) -> None:
    if len(readback) != len(spec["voids"]):
        raise LiveBackendError("3D Layout anti-pad readback count mismatch")
    by_name = {item["name"]: item for item in readback}
    for expected in spec["voids"]:
        actual = by_name.get(expected["name"])
        if actual is None:
            raise LiveBackendError(f"3D Layout anti-pad readback is missing: {expected['name']}")
        if actual["type"].casefold() != "circle void":
            raise LiveBackendError("3D Layout anti-pad type readback failed")
        if actual["layer_name"] != expected["layer_name"]:
            raise LiveBackendError("3D Layout anti-pad layer readback failed")
        if not actual["owner_membership_verified"]:
            raise LiveBackendError("3D Layout anti-pad owner membership readback failed")
        if not _layout_locations_equal(actual["center"], expected["center"]):
            raise LiveBackendError("3D Layout anti-pad center readback failed")
        if not math.isclose(
            float(actual["radius"]),
            float(expected["radius"]),
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise LiveBackendError("3D Layout anti-pad radius readback failed")
    after = _layout_antipad_circle_create_state_allow_existing(app, spec)
    if before_state["global_inventory_status"] == "verified":
        expected_names = sorted(before_state["circle_void_names"] + spec["names"])
        if after["circle_void_names"] != expected_names:
            raise LiveBackendError("3D Layout anti-pad changed an unexpected circle-void object")
    elif after["global_inventory_status"] != "unavailable":
        raise LiveBackendError("3D Layout anti-pad global inventory verification scope changed")
    before_owners = {item["name"]: item for item in before_state["owners"]}
    after_owners = {item["name"]: item for item in after["owners"]}
    for owner_name, original in before_owners.items():
        current = after_owners[owner_name]
        for field in ("name", "type", "layer_name", "points", "native_property_digest"):
            if current[field] != original[field]:
                raise LiveBackendError(f"3D Layout anti-pad changed protected owner state: {field}")
        additions = sorted(
            item["name"] for item in spec["voids"] if item["owner_name"] == owner_name
        )
        if current["void_names"] != sorted(original["void_names"] + additions):
            raise LiveBackendError("3D Layout anti-pad owner void catalog verification failed")


def _layout_antipad_circle_create_state_allow_existing(
    app: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    relaxed = {**spec, "names": []}
    return _layout_antipad_circle_create_state(app, relaxed)


def _rollback_layout_antipad_circle_create(
    app: Any,
    spec: dict[str, Any],
    *,
    before_state: dict[str, Any],
) -> dict[str, Any]:
    errors = []
    editor = _layout_modeler_editor(app)
    expected = {item["name"]: item for item in spec["voids"]}
    try:
        current = _layout_antipad_circle_create_state_allow_existing(app, spec)
        before_owners = {item["name"]: item for item in before_state["owners"]}
        current_owners = {item["name"]: item for item in current["owners"]}
        candidates = []
        for name, item in expected.items():
            owner_before = before_owners[item["owner_name"]]
            owner_current = current_owners[item["owner_name"]]
            if name not in owner_before["void_names"] and name in owner_current["void_names"]:
                actual = _layout_native_circle_void_record(app, item)
                if (
                    actual["type"].casefold() != "circle void"
                    or actual["layer_name"] != owner_before["layer_name"]
                    or not actual["owner_membership_verified"]
                    or not _layout_locations_equal(actual["center"], item["center"])
                    or not math.isclose(
                        float(actual["radius"]), float(item["radius"]), rel_tol=1e-9, abs_tol=1e-12
                    )
                ):
                    raise LiveBackendError(f"3D Layout rollback target verification failed: {name}")
                candidates.append(name)
    except Exception as exc:
        errors.append(f"candidate_readback: {type(exc).__name__}: {exc}")
        candidates = []
    for name in reversed(candidates):
        try:
            editor.Delete(name)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    readback_error = ""
    try:
        restored = _layout_antipad_circle_create_state(app, spec)
    except Exception as exc:
        restored = {}
        readback_error = f"{type(exc).__name__}: {exc}"
    state_restored = bool(restored) and _layout_antipad_state_restored(restored, before_state)
    return {
        "complete": not errors and not readback_error and state_restored,
        "state_restored": state_restored,
        "deleted_names": candidates,
        "errors": errors,
        "readback_error": readback_error,
    }


def _layout_antipad_state_restored(restored: dict[str, Any], before: dict[str, Any]) -> bool:
    for field in (
        "design_type",
        "solution_type",
        "model_units",
        "owners",
        "target_presence",
        "target_presence_scope",
        "global_inventory_status",
    ):
        if restored.get(field) != before.get(field):
            return False
    if before["global_inventory_status"] == "verified":
        return restored.get("circle_void_names") == before.get("circle_void_names")
    return restored.get("global_inventory_status") == "unavailable"


def _layout_via_dependency_state(
    app: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    if design_type != "HFSS 3D Layout Design":
        raise LiveBackendError("3D Layout via creation requires HFSS 3D Layout Design")
    stackup = _layout_full_stackup_snapshot(app)
    stackup_names = [str(item["name"]) for item in stackup]
    stackup_by_name = {str(item["name"]): item for item in stackup}

    padstack_records, padstack_error = _layout_padstack_records(
        app,
        max_items=501,
        include_layers=True,
    )
    if padstack_error or len(padstack_records) > 500:
        raise LiveBackendError(
            "3D Layout padstack catalog is unavailable or exceeds the 500 definition safety limit"
        )
    padstack_names = [str(item["name"]) for item in padstack_records]
    padstack_by_name = {str(item["name"]): item for item in padstack_records}
    try:
        net_names = sorted(str(item) for item in dict(app.modeler.nets or {}))
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout net inventory is unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    if len(net_names) > 100_000:
        raise LiveBackendError("3D Layout net inventory exceeds the 100000 name safety limit")

    selected_padstacks = {}
    selected_layers = {}
    selected_nets = {}
    target_presence = {}
    for via in spec["vias"]:
        padstack_name = _layout_exact_name(
            padstack_names,
            via["padstack"],
            "padstack",
        )
        selected_padstacks[padstack_name] = padstack_by_name[padstack_name]
        for field in ("top_layer", "bottom_layer"):
            layer_name = _layout_exact_name(stackup_names, via[field], field)
            layer = stackup_by_name[layer_name]
            if str(layer.get("type") or "").casefold() != "signal":
                raise LiveBackendError(f"{field} must reference a signal layer: {layer_name}")
            selected_layers[layer_name] = layer
        net_name = _layout_exact_name(net_names, via["net_name"], "net_name")
        selected_nets[net_name] = {"name": net_name}
        target_presence[via["name"]] = _layout_native_name_matches(app, via["name"])

    model_units = str(_safe_attribute(_safe_attribute(app, "modeler"), "model_units") or "").strip()
    if not model_units:
        raise LiveBackendError("3D Layout model units are unavailable")
    return {
        "design_type": design_type,
        "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
        "model_units": model_units,
        "stackup": stackup,
        "padstacks": selected_padstacks,
        "signal_layers": selected_layers,
        "nets": selected_nets,
        "target_presence": target_presence,
    }


def _layout_via_create_state(app: Any, spec: dict[str, Any]) -> dict[str, Any]:
    state = _layout_via_dependency_state(app, spec)
    occupied = {
        name: matches
        for name, matches in state["target_presence"].items()
        if matches
    }
    if occupied:
        name = next(iter(occupied))
        raise LiveBackendError(f"3D Layout object name already exists: {name}")
    return state


def _layout_via_update_dependencies(
    app: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    if design_type != "HFSS 3D Layout Design":
        raise LiveBackendError("3D Layout via update requires HFSS 3D Layout Design")
    stackup = _layout_full_stackup_snapshot(app)
    try:
        net_names = sorted(str(item) for item in dict(app.modeler.nets or {}))
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout net inventory is unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    if len(net_names) > 100_000:
        raise LiveBackendError("3D Layout net inventory exceeds the 100000 name safety limit")
    selected_nets = {}
    for update in spec["updates"]:
        if "net_name" not in update:
            continue
        net_name = _layout_exact_name(net_names, update["net_name"], "net_name")
        selected_nets[net_name] = {"name": net_name}
    model_units = str(
        _safe_attribute(_safe_attribute(app, "modeler"), "model_units") or ""
    ).strip()
    if not model_units:
        raise LiveBackendError("3D Layout model units are unavailable")
    return {
        "design_type": design_type,
        "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
        "model_units": model_units,
        "stackup": stackup,
        "net_names": net_names,
        "selected_nets": selected_nets,
    }


def _layout_via_update_state(app: Any, spec: dict[str, Any]) -> dict[str, Any]:
    state = _layout_via_update_dependencies(app, spec)
    vias = []
    for update in spec["updates"]:
        record = _layout_native_via_record(app, update["name"])
        if str(record["type"]).casefold() != "via":
            raise LiveBackendError(f"3D Layout object is not a via: {update['name']}")
        _layout_via_object(app, update["name"])
        if not _layout_via_update_changes_record(record, update):
            raise LiveBackendError(
                f"3D Layout via update is already equal to the requested values: {update['name']}"
            )
        vias.append(record)
    return {**state, "vias": vias}


def _layout_via_object(app: Any, name: str) -> Any:
    try:
        collection = dict(_safe_attribute(_safe_attribute(app, "modeler"), "vias") or {})
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout via wrapper inventory is unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    actual_name = _layout_exact_name(
        [str(item) for item in collection],
        name,
        "via name",
    )
    return collection[actual_name]


def _layout_via_update_changes_record(
    record: dict[str, Any],
    update: dict[str, Any],
) -> bool:
    if "net_name" in update and record["net_name"] != update["net_name"]:
        return True
    if "location" in update and not _layout_locations_equal(
        record["location"], update["location"]
    ):
        return True
    if "rotation_degrees" in update and not _layout_angles_equal(
        float(record["rotation_degrees"]),
        float(update["rotation_degrees"]),
    ):
        return True
    if "lock_position" in update and record["lock_position"] is not update["lock_position"]:
        return True
    return False


def _layout_via_delete_dependencies(
    app: Any,
    padstack_names: list[str],
) -> dict[str, Any]:
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    if design_type != "HFSS 3D Layout Design":
        raise LiveBackendError("3D Layout via delete requires HFSS 3D Layout Design")
    stackup = _layout_full_stackup_snapshot(app)
    padstack_records, padstack_error = _layout_padstack_records(
        app,
        max_items=501,
        include_layers=True,
    )
    if padstack_error or len(padstack_records) > 500:
        raise LiveBackendError(
            "3D Layout padstack catalog is unavailable or exceeds the 500 definition safety limit"
        )
    available_padstacks = [str(item["name"]) for item in padstack_records]
    padstack_by_name = {str(item["name"]): item for item in padstack_records}
    selected_padstacks = {}
    for requested in padstack_names:
        actual = _layout_exact_name(available_padstacks, requested, "padstack")
        selected_padstacks[actual] = padstack_by_name[actual]
    try:
        net_names = sorted(str(item) for item in dict(app.modeler.nets or {}))
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout net inventory is unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    if len(net_names) > 100_000:
        raise LiveBackendError("3D Layout net inventory exceeds the 100000 name safety limit")
    model_units = str(
        _safe_attribute(_safe_attribute(app, "modeler"), "model_units") or ""
    ).strip()
    if not model_units:
        raise LiveBackendError("3D Layout model units are unavailable")
    return {
        "design_type": design_type,
        "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
        "model_units": model_units,
        "stackup": stackup,
        "padstacks": selected_padstacks,
        "net_names": net_names,
    }


def _layout_via_delete_state(app: Any, spec: dict[str, Any]) -> dict[str, Any]:
    vias = []
    for name in spec["names"]:
        record = _layout_native_via_record(app, name)
        if str(record["type"]).casefold() != "via":
            raise LiveBackendError(f"3D Layout object is not a via: {name}")
        _layout_via_object(app, name)
        vias.append(record)
    padstack_names = list(dict.fromkeys(item["padstack"] for item in vias))
    state = _layout_via_delete_dependencies(app, padstack_names)
    stackup_by_name = {str(item["name"]): item for item in state["stackup"]}
    for record in vias:
        for field in ("top_layer", "bottom_layer"):
            layer_name = record[field]
            layer = stackup_by_name.get(layer_name)
            if layer is None:
                raise LiveBackendError(
                    f"3D Layout via {record['name']} references a missing {field}: {layer_name}"
                )
            if str(layer.get("type") or "").casefold() != "signal":
                raise LiveBackendError(
                    f"3D Layout via {record['name']} {field} is not a signal layer"
                )
        if not _layout_via_no_net(record["net_name"]):
            _layout_exact_name(state["net_names"], record["net_name"], "net_name")
        _validate_layout_via_reconstructible(
            record,
            model_units=state["model_units"],
        )
    return {**state, "vias": vias}


def _layout_via_no_net(net_name: Any) -> bool:
    return str(net_name or "").strip().casefold() in {
        "",
        "<no-net>",
        "no-net",
        "<none>",
    }


def _validate_layout_via_reconstructible(
    record: dict[str, Any],
    *,
    model_units: str,
) -> None:
    native = dict(record["native_properties"])
    unsupported = sorted(
        set(native).difference(_LAYOUT_RECONSTRUCTIBLE_VIA_NATIVE_FIELDS)
    )
    if unsupported:
        raise LiveBackendError(
            f"3D Layout via {record['name']} has an unsupported native property: {unsupported[0]}"
        )
    for field in ("Backdrill Top", "Backdrill Bottom"):
        if field in native and str(native[field]).strip() not in {"", "----"}:
            raise LiveBackendError(
                f"3D Layout via delete does not support custom {field}: {record['name']}"
            )
    for field in ("Top Offset", "Bottom Offset"):
        if field not in native:
            continue
        value = _layout_length_in_model_units(native[field], model_units, field)
        if not math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-12):
            raise LiveBackendError(
                f"3D Layout via delete does not support nonzero {field}: {record['name']}"
            )


def _layout_native_via_record(app: Any, name: str) -> dict[str, Any]:
    matches = _layout_native_name_matches(app, name)
    if matches != [name]:
        raise LiveBackendError(f"3D Layout via is missing or ambiguous: {name}")
    editor = _layout_modeler_editor(app)
    get_properties = getattr(editor, "GetProperties", None)
    get_property_value = getattr(editor, "GetPropertyValue", None)
    if not callable(get_properties) or not callable(get_property_value):
        raise LiveBackendError("3D Layout native via property API is unavailable")
    try:
        property_names = [
            str(item) for item in list(get_properties("BaseElementTab", name) or [])
        ]
        if not property_names or len(property_names) > 128:
            raise LiveBackendError(
                f"3D Layout via property list is empty or exceeds 128 fields: {name}"
            )
        native = {
            property_name: _json_value(
                get_property_value("BaseElementTab", name, property_name)
            )
            for property_name in property_names
        }
    except LiveBackendError:
        raise
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout native via readback failed for {name}: {type(exc).__name__}: {exc}"
        ) from exc
    if sum(len(str(key)) + len(str(value)) for key, value in native.items()) > 32 * 1024:
        raise LiveBackendError(f"3D Layout via property readback exceeds 32 KiB: {name}")
    required = {
        "Type",
        "Name",
        "Net",
        "Padstack Definition",
        "Start Layer",
        "Stop Layer",
        "OverrideHoleDiameter",
        "HoleDiameter",
        "Location",
        "Angle",
        "LockPosition",
    }
    missing = sorted(required.difference(native))
    if missing:
        raise LiveBackendError(f"3D Layout via readback is missing property: {missing[0]}")
    location_parts = [item.strip() for item in str(native["Location"]).split(",")]
    if len(location_parts) != 2:
        raise LiveBackendError(f"3D Layout via Location readback is invalid: {name}")
    try:
        location = [float(item) for item in location_parts]
    except ValueError as exc:
        raise LiveBackendError(f"3D Layout via Location readback is non-numeric: {name}") from exc
    angle_text = str(native["Angle"]).strip()
    if not angle_text.casefold().endswith("deg"):
        raise LiveBackendError(f"3D Layout via Angle readback is invalid: {name}")
    try:
        angle_degrees = float(angle_text[:-3])
    except ValueError as exc:
        raise LiveBackendError(f"3D Layout via Angle readback is non-numeric: {name}") from exc
    override_hole = _layout_native_bool(native["OverrideHoleDiameter"], "OverrideHoleDiameter")
    return {
        "name": str(native["Name"]),
        "type": str(native["Type"]),
        "padstack": str(native["Padstack Definition"]),
        "top_layer": str(native["Start Layer"]),
        "bottom_layer": str(native["Stop Layer"]),
        "net_name": str(native["Net"]),
        "location": location,
        "rotation_degrees": angle_degrees,
        "lock_position": _layout_native_bool(native["LockPosition"], "LockPosition"),
        "override_hole_diameter": override_hole,
        "hole_diameter": str(native["HoleDiameter"]),
        "native_property_digest": _digest(native),
        "native_properties": native,
    }


def _layout_native_bool(value: Any, field: str) -> bool:
    if type(value) is bool:
        return value
    normalized = str(value).strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise LiveBackendError(f"3D Layout native {field} readback is not boolean")


def _layout_length_in_model_units(value: Any, model_units: str, field: str) -> float:
    try:
        from ansys.aedt.core.generic.constants import unit_converter
        from ansys.aedt.core.generic.numbers_utils import decompose_variable_value

        magnitude, units = decompose_variable_value(str(value).strip())
        return float(
            unit_converter(
                magnitude,
                input_units=units or model_units,
                output_units=model_units,
            )
        )
    except Exception as exc:
        raise LiveBackendError(
            f"3D Layout {field} readback cannot be converted to {model_units}: {value}"
        ) from exc


def _layout_angles_equal(actual: float, expected: float) -> bool:
    delta = (actual - expected) % 360.0
    return math.isclose(delta, 0.0, abs_tol=1e-9) or math.isclose(
        delta,
        360.0,
        abs_tol=1e-9,
    )


def _layout_locations_equal(actual: Any, expected: Any) -> bool:
    if not isinstance(actual, (list, tuple)) or not isinstance(expected, (list, tuple)):
        return False
    if len(actual) != 2 or len(expected) != 2:
        return False
    try:
        return all(
            math.isclose(float(actual_value), float(expected_value), rel_tol=0.0, abs_tol=1e-9)
            for actual_value, expected_value in zip(actual, expected)
        )
    except (TypeError, ValueError):
        return False


def _verify_layout_via_create_readback(
    app: Any,
    spec: dict[str, Any],
    readback: list[dict[str, Any]],
    *,
    before_state: dict[str, Any],
) -> None:
    if len(readback) != len(spec["vias"]):
        raise LiveBackendError("3D Layout via batch readback count mismatch")
    by_name = {item["name"]: item for item in readback}
    if len(by_name) != len(readback):
        raise LiveBackendError("3D Layout via batch readback contains duplicate names")
    model_units = before_state["model_units"]
    for expected in spec["vias"]:
        actual = by_name.get(expected["name"])
        if actual is None:
            raise LiveBackendError(f"3D Layout via readback is missing: {expected['name']}")
        for field in ("padstack", "top_layer", "bottom_layer", "net_name"):
            if actual[field] != expected[field]:
                raise LiveBackendError(
                    f"3D Layout via {expected['name']} {field} readback mismatch"
                )
        if str(actual["type"]).casefold() != "via":
            raise LiveBackendError(f"3D Layout object is not a via: {expected['name']}")
        if any(
            not math.isclose(float(actual_value), float(expected_value), rel_tol=0.0, abs_tol=1e-9)
            for actual_value, expected_value in zip(
                actual["location"],
                [expected["x"], expected["y"]],
            )
        ):
            raise LiveBackendError(
                f"3D Layout via {expected['name']} location readback mismatch in {model_units}"
            )
        if not _layout_angles_equal(
            float(actual["rotation_degrees"]),
            float(expected["rotation_degrees"]),
        ):
            raise LiveBackendError(
                f"3D Layout via {expected['name']} rotation readback mismatch"
            )
        if actual["lock_position"] is not expected["lock_position"]:
            raise LiveBackendError(
                f"3D Layout via {expected['name']} lock_position readback mismatch"
            )
        if expected["hole_diameter"] is None:
            if actual["override_hole_diameter"] is not False:
                raise LiveBackendError(
                    f"3D Layout via {expected['name']} unexpectedly overrides hole diameter"
                )
        else:
            if actual["override_hole_diameter"] is not True:
                raise LiveBackendError(
                    f"3D Layout via {expected['name']} hole override readback mismatch"
                )
            actual_hole = _layout_length_in_model_units(
                actual["hole_diameter"],
                model_units,
                "HoleDiameter",
            )
            if not math.isclose(
                actual_hole,
                float(expected["hole_diameter"]),
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise LiveBackendError(
                    f"3D Layout via {expected['name']} hole diameter readback mismatch"
                )
    after_state = _layout_via_dependency_state(app, spec)
    before_dependencies = dict(before_state)
    after_dependencies = dict(after_state)
    before_dependencies.pop("target_presence", None)
    after_dependencies.pop("target_presence", None)
    if _digest(after_dependencies) != _digest(before_dependencies):
        raise LiveBackendError("3D Layout via creation changed a frozen dependency")
    expected_presence = {item["name"]: [item["name"]] for item in spec["vias"]}
    if after_state["target_presence"] != expected_presence:
        raise LiveBackendError("3D Layout via creation target presence readback mismatch")


def _verify_layout_via_update_readback(
    app: Any,
    spec: dict[str, Any],
    readback: list[dict[str, Any]],
    *,
    before_state: dict[str, Any],
) -> None:
    if len(readback) != len(spec["updates"]):
        raise LiveBackendError("3D Layout via update readback count mismatch")
    before_by_name = {item["name"]: item for item in before_state["vias"]}
    after_by_name = {item["name"]: item for item in readback}
    if len(after_by_name) != len(readback):
        raise LiveBackendError("3D Layout via update readback contains duplicate names")
    native_fields = {
        "net_name": "Net",
        "location": "Location",
        "rotation_degrees": "Angle",
        "lock_position": "LockPosition",
    }
    immutable_fields = (
        "name",
        "type",
        "padstack",
        "top_layer",
        "bottom_layer",
        "override_hole_diameter",
        "hole_diameter",
    )
    for update in spec["updates"]:
        name = update["name"]
        before = before_by_name.get(name)
        actual = after_by_name.get(name)
        if before is None or actual is None:
            raise LiveBackendError(f"3D Layout via update readback is missing: {name}")
        for field in immutable_fields:
            if actual[field] != before[field]:
                raise LiveBackendError(
                    f"3D Layout via update changed immutable {field}: {name}"
                )
        if "net_name" in update and actual["net_name"] != update["net_name"]:
            raise LiveBackendError(f"3D Layout via {name} net_name readback mismatch")
        if "location" in update and not _layout_locations_equal(
            actual["location"], update["location"]
        ):
            raise LiveBackendError(f"3D Layout via {name} location readback mismatch")
        if "rotation_degrees" in update and not _layout_angles_equal(
            float(actual["rotation_degrees"]),
            float(update["rotation_degrees"]),
        ):
            raise LiveBackendError(f"3D Layout via {name} rotation readback mismatch")
        if "lock_position" in update and actual["lock_position"] is not update["lock_position"]:
            raise LiveBackendError(f"3D Layout via {name} lock_position readback mismatch")

        allowed_native = {
            native_fields[field]
            for field in update
            if field in native_fields
        }
        before_native = dict(before["native_properties"])
        after_native = dict(actual["native_properties"])
        if set(after_native) != set(before_native):
            raise LiveBackendError(
                f"3D Layout via {name} native property schema changed during update"
            )
        for field in sorted(set(before_native).difference(allowed_native)):
            if after_native[field] != before_native[field]:
                raise LiveBackendError(
                    f"3D Layout via {name} changed unrelated native property: {field}"
                )

    after_dependencies = _layout_via_update_dependencies(app, spec)
    before_dependencies = dict(before_state)
    before_vias = before_dependencies.pop("vias")
    before_net_names = set(before_dependencies.pop("net_names"))
    after_net_names = set(after_dependencies.pop("net_names"))
    if _digest(after_dependencies) != _digest(before_dependencies):
        raise LiveBackendError("3D Layout via update changed a frozen dependency")
    added_nets = sorted(after_net_names.difference(before_net_names))
    if added_nets:
        raise LiveBackendError(
            f"3D Layout via update unexpectedly added a net: {added_nets[0]}"
        )
    before_via_by_name = {item["name"]: item for item in before_vias}
    allowed_removed_nets = {
        before_via_by_name[update["name"]]["net_name"]
        for update in spec["updates"]
        if "net_name" in update
        and update["net_name"] != before_via_by_name[update["name"]]["net_name"]
    }
    unexpected_removed_nets = sorted(
        before_net_names.difference(after_net_names).difference(allowed_removed_nets)
    )
    if unexpected_removed_nets:
        raise LiveBackendError(
            "3D Layout via update unexpectedly removed a net: "
            f"{unexpected_removed_nets[0]}"
        )


def _verify_layout_via_delete_readback(
    app: Any,
    spec: dict[str, Any],
    *,
    before_state: dict[str, Any],
) -> None:
    remaining = {
        name: matches
        for name in spec["names"]
        if (matches := _layout_native_name_matches(app, name))
    }
    if remaining:
        raise LiveBackendError(
            f"3D Layout via delete target remains after apply: {next(iter(remaining))}"
        )
    after_dependencies = _layout_via_delete_dependencies(
        app,
        list(before_state["padstacks"]),
    )
    before_dependencies = dict(before_state)
    before_vias = before_dependencies.pop("vias")
    before_net_names = set(before_dependencies.pop("net_names"))
    after_net_names = set(after_dependencies.pop("net_names"))
    if _digest(after_dependencies) != _digest(before_dependencies):
        raise LiveBackendError("3D Layout via delete changed a frozen dependency")
    added_nets = sorted(after_net_names.difference(before_net_names))
    if added_nets:
        raise LiveBackendError(
            f"3D Layout via delete unexpectedly added a net: {added_nets[0]}"
        )
    allowed_removed_nets = {
        item["net_name"]
        for item in before_vias
        if not _layout_via_no_net(item["net_name"])
    }
    unexpected_removed_nets = sorted(
        before_net_names.difference(after_net_names).difference(allowed_removed_nets)
    )
    if unexpected_removed_nets:
        raise LiveBackendError(
            "3D Layout via delete unexpectedly removed a net: "
            f"{unexpected_removed_nets[0]}"
        )


def _invalidate_layout_via_cache(app: Any, names: list[str]) -> None:
    modeler = _safe_attribute(app, "modeler")
    cache = _safe_attribute(modeler, "_vias")
    if isinstance(cache, dict):
        for name in names:
            cache.pop(name, None)


def _rollback_layout_via_create(
    app: Any,
    spec: dict[str, Any],
    created_names: list[str],
    *,
    before_state: dict[str, Any],
) -> dict[str, Any]:
    requested_names = [item["name"] for item in spec["vias"]]
    # Delete only names positively returned by this transaction. If another
    # client races in with a requested name, leaving rollback incomplete is
    # safer than deleting an object that the Harness did not create.
    candidates = list(dict.fromkeys(created_names))
    errors = []
    editor = None
    try:
        editor = _layout_modeler_editor(app)
    except Exception as exc:
        errors.append(f"native editor unavailable: {type(exc).__name__}: {exc}")
    if editor is not None:
        delete = getattr(editor, "Delete", None)
        if not callable(delete):
            errors.append("native via delete API unavailable")
        else:
            for name in reversed(candidates):
                try:
                    delete([name])
                except Exception as exc:
                    errors.append(f"delete {name} failed: {type(exc).__name__}: {exc}")
    _invalidate_layout_via_cache(app, candidates)
    remaining = {}
    for name in requested_names:
        try:
            matches = _layout_native_name_matches(app, name)
        except Exception as exc:
            matches = [f"readback-error:{type(exc).__name__}:{exc}"]
        if matches:
            remaining[name] = matches
    try:
        restored_state = _layout_via_create_state(app, spec)
        dependencies_restored = _digest(restored_state) == _digest(before_state)
    except Exception as exc:
        restored_state = {}
        dependencies_restored = False
        errors.append(f"dependency restore readback failed: {type(exc).__name__}: {exc}")
    if remaining:
        errors.append("created via names remain after rollback")
    if not dependencies_restored:
        errors.append("frozen via dependencies were not restored")
    return {
        "complete": not errors and not remaining and dependencies_restored,
        "attempted_names": candidates,
        "remaining_names": remaining,
        "before_digest": _digest(before_state),
        "after_digest": _digest(restored_state) if restored_state else "",
        "errors": errors,
    }


def _rollback_layout_via_update(
    app: Any,
    spec: dict[str, Any],
    touched_names: list[str],
    *,
    before_state: dict[str, Any],
) -> dict[str, Any]:
    before_by_name = {item["name"]: item for item in before_state["vias"]}
    attempted_names = list(dict.fromkeys(touched_names))
    errors = []
    for name in reversed(attempted_names):
        before = before_by_name.get(name)
        if before is None:
            errors.append(f"missing rollback snapshot for {name}")
            continue
        try:
            via = _layout_via_object(app, name)
            via.lock_position = False
            via.net_name = before["net_name"]
            via.location = list(before["location"])
            via.angle = f"{before['rotation_degrees']}deg"
            via.lock_position = bool(before["lock_position"])
        except Exception as exc:
            errors.append(f"restore {name} failed: {type(exc).__name__}: {exc}")

    restored_state: dict[str, Any] = {}
    try:
        restored_state = _layout_via_update_state(app, spec)
        restored = _digest(restored_state) == _digest(before_state)
    except Exception as exc:
        restored = False
        errors.append(f"rollback readback failed: {type(exc).__name__}: {exc}")
    if not restored:
        errors.append("full native via update snapshot was not restored")
    return {
        "complete": not errors and restored,
        "attempted_names": attempted_names,
        "before_digest": _digest(before_state),
        "after_digest": _digest(restored_state) if restored_state else "",
        "errors": errors,
    }


def _rollback_layout_via_delete(
    app: Any,
    spec: dict[str, Any],
    deleted_names: list[str],
    *,
    before_state: dict[str, Any],
) -> dict[str, Any]:
    before_by_name = {item["name"]: item for item in before_state["vias"]}
    deleted = set(deleted_names)
    attempted_names = [name for name in spec["names"] if name in deleted]
    restored_names = []
    errors = []
    for name in attempted_names:
        try:
            _recreate_layout_via_from_record(
                app,
                before_by_name[name],
                model_units=before_state["model_units"],
            )
            restored_names.append(name)
        except Exception as exc:
            errors.append(f"restore {name} failed: {type(exc).__name__}: {exc}")

    restored_state: dict[str, Any] = {}
    try:
        restored_state = _layout_via_delete_state(app, spec)
        restored = _digest(restored_state) == _digest(before_state)
    except Exception as exc:
        restored = False
        errors.append(f"rollback readback failed: {type(exc).__name__}: {exc}")
    if not restored:
        errors.append("full native via delete snapshot was not restored")
    return {
        "complete": not errors and restored,
        "attempted_names": attempted_names,
        "restored_names": restored_names,
        "before_digest": _digest(before_state),
        "after_digest": _digest(restored_state) if restored_state else "",
        "errors": errors,
    }


def _recreate_layout_via_from_record(
    app: Any,
    record: dict[str, Any],
    *,
    model_units: str,
) -> None:
    name = record["name"]
    if _layout_native_name_matches(app, name):
        raise LiveBackendError(
            f"cannot restore 3D Layout via because the name is occupied: {name}"
        )
    create_via = getattr(_safe_attribute(app, "modeler"), "create_via", None)
    if not callable(create_via):
        raise LiveBackendError("3D Layout via creation API is unavailable during rollback")
    hole_diameter = None
    if record["override_hole_diameter"]:
        hole_diameter = _layout_length_in_model_units(
            record["hole_diameter"],
            model_units,
            "HoleDiameter",
        )
    created_name = ""
    try:
        created = create_via(
            name=name,
            padstack=record["padstack"],
            x=float(record["location"][0]),
            y=float(record["location"][1]),
            rotation=float(record["rotation_degrees"]),
            hole_diam=hole_diameter,
            top_layer=record["top_layer"],
            bot_layer=record["bottom_layer"],
            net=None if _layout_via_no_net(record["net_name"]) else record["net_name"],
        )
        created_name = str(_safe_attribute(created, "name") or "").strip()
        if not created or created_name != name:
            raise LiveBackendError(
                f"3D Layout via rollback returned an unexpected name: {created_name or name}"
            )
        created.angle = f"{record['rotation_degrees']}deg"
        created.lock_position = bool(record["lock_position"])
        restored = _layout_native_via_record(app, name)
        if restored["native_properties"] != record["native_properties"]:
            raise LiveBackendError(
                f"3D Layout via rollback native property mismatch: {name}"
            )
    except Exception:
        if created_name:
            try:
                _layout_modeler_editor(app).Delete([created_name])
            except Exception:
                pass
            _invalidate_layout_via_cache(app, [created_name])
        raise


def _layout_full_stackup_snapshot(app: Any) -> list[dict[str, Any]]:
    records, error = _layout_stackup_records(app, max_items=501)
    if error:
        raise LiveBackendError(
            f"3D Layout full stackup snapshot is unavailable: {error}"
        )
    if len(records) > 500:
        raise LiveBackendError(
            "3D Layout stackup exceeds the 500 layer write safety limit"
        )
    names = [str(item.get("name") or "") for item in records]
    folded = [item.casefold() for item in names]
    if not all(names):
        raise LiveBackendError("3D Layout stackup contains an unnamed layer")
    if len(set(folded)) != len(folded):
        raise LiveBackendError(
            "3D Layout stackup contains duplicate case-insensitive layer names"
        )
    editor = _safe_attribute(_safe_attribute(app, "modeler"), "layers")
    editor = _safe_attribute(editor, "oeditor") if editor is not None else None
    get_layer_info = getattr(editor, "GetLayerInfo", None)
    if not callable(get_layer_info):
        raise LiveBackendError(
            "3D Layout native stackup layer readback API is unavailable"
        )
    for record in records:
        name = record["name"]
        try:
            raw_info = [str(item) for item in list(get_layer_info(name) or [])]
        except Exception as exc:
            raise LiveBackendError(
                f"3D Layout native layer readback failed for {name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not raw_info or len(raw_info) > 256:
            raise LiveBackendError(
                f"3D Layout native layer readback is empty or exceeds 256 fields: {name}"
            )
        if sum(len(item) for item in raw_info) > 64 * 1024:
            raise LiveBackendError(
                f"3D Layout native layer readback exceeds 64 KiB: {name}"
            )
        native_properties = {}
        for index, item in enumerate(raw_info):
            key, separator, value = item.partition(": ")
            property_name = key.strip() if separator else f"__raw_{index}"
            if property_name in native_properties:
                raise LiveBackendError(
                    f"3D Layout native layer readback contains duplicate field {property_name}: {name}"
                )
            native_properties[property_name] = value if separator else item
        # ChangeLayer serializes Thickness0 in base units even when its physical
        # value is unchanged. The typed thickness fields above retain the
        # semantic value, while all non-redundant native fields remain exact.
        if "Thickness0" in native_properties:
            native_properties["Thickness0"] = {
                "value": record.get("thickness"),
                "units": record.get("thickness_units"),
            }
        record["native_properties"] = native_properties
    return records


def _restore_layout_layer_native_color(
    layer: Any,
    before_layer: dict[str, Any],
) -> None:
    native_properties = before_layer.get("native_properties")
    if not isinstance(native_properties, dict) or "Color" not in native_properties:
        return
    raw_color = str(native_properties["Color"]).strip()
    if raw_color.endswith("d"):
        raw_color = raw_color[:-1]
    try:
        color = int(raw_color)
    except ValueError as exc:
        raise LiveBackendError("3D Layout native layer color readback is invalid") from exc
    if color < 0 or color > 0xFFFFFF:
        raise LiveBackendError("3D Layout native layer color is outside RGB bounds")
    setter = getattr(layer, "set_layer_color", None)
    if not callable(setter):
        raise LiveBackendError("3D Layout layer color restore API is unavailable")
    red = (color >> 16) & 0xFF
    green = (color >> 8) & 0xFF
    blue = color & 0xFF
    if setter(red, green, blue) is False:
        raise LiveBackendError("3D Layout layer color restore returned false")


def _layout_stackup_layer_record(
    stackup: list[dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    exact = [item for item in stackup if item.get("name") == name]
    if len(exact) == 1:
        return exact[0]
    folded = [
        str(item.get("name") or "")
        for item in stackup
        if str(item.get("name") or "").casefold() == name.casefold()
    ]
    if folded:
        raise LiveBackendError(
            f"layer_name must match AEDT case exactly: {folded[0]}"
        )
    raise LiveBackendError(f"3D Layout stackup layer does not exist: {name}")


def _layout_stackup_layer_object(
    app: Any,
    *,
    name: str,
    expected_id: Any,
    expected_type: str,
) -> Any:
    try:
        layers = list(app.modeler.layers.stackup_layers or [])
    except Exception as exc:
        raise LiveBackendError(
            "3D Layout stackup layer API is unavailable: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    matches = [item for item in layers if str(_safe_attribute(item, "name") or "") == name]
    if len(matches) != 1:
        raise LiveBackendError(
            f"3D Layout stackup layer object is missing or ambiguous: {name}"
        )
    layer = matches[0]
    if _safe_json_attribute(layer, "id") != expected_id:
        raise LiveBackendError(f"3D Layout stackup layer id changed: {name}")
    if str(_safe_attribute(layer, "type") or "") != expected_type:
        raise LiveBackendError(f"3D Layout stackup layer type changed: {name}")
    return layer


def _validate_layout_material_assignment_role(
    spec: dict[str, Any],
    layer: dict[str, Any],
) -> str:
    layer_type = str(layer.get("type") or "").casefold()
    field = spec["assignment_field"]
    if layer_type not in {"signal", "dielectric"}:
        raise LiveBackendError(
            "3D Layout material assignment supports only signal and dielectric layers"
        )
    if layer_type == "dielectric" and field != "material":
        raise LiveBackendError(
            "dielectric layers require assignment_field=material"
        )
    expected_class = (
        "conductor"
        if layer_type == "signal" and field == "material"
        else "dielectric"
    )
    is_dielectric = float(spec["conductivity"]) < 100_000.0
    if (expected_class == "dielectric") != is_dielectric:
        raise LiveBackendError(
            f"{layer_type} layer {field} requires a {expected_class} material; "
            f"conductivity={spec['conductivity']} S/m classifies as "
            f"{'dielectric' if is_dielectric else 'conductor'} at the PyAEDT 100000 S/m threshold"
        )
    return expected_class


def _verify_layout_material_create_assign_readback(
    spec: dict[str, Any],
    before_layer: dict[str, Any],
    material_readback: dict[str, Any],
    layer_readback: dict[str, Any],
    *,
    before_stackup: list[dict[str, Any]],
    after_stackup: list[dict[str, Any]],
) -> None:
    _verify_hfss_material_create_readback(spec, material_readback)
    expected_is_dielectric = spec["expected_material_class"] == "dielectric"
    if material_readback.get("is_dielectric") is not expected_is_dielectric:
        raise LiveBackendError(
            "3D Layout material classification readback does not match the target layer role"
        )
    for field in ("name", "id", "type"):
        if layer_readback.get(field) != before_layer.get(field):
            raise LiveBackendError(
                f"3D Layout target layer {field} changed during assignment"
            )
    assignment_field = spec["assignment_field"]
    if layer_readback.get(assignment_field) != spec["material_name"]:
        raise LiveBackendError(
            f"3D Layout layer {assignment_field} readback mismatch"
        )
    expected_stackup = json.loads(json.dumps(before_stackup))
    expected_layer = _layout_stackup_layer_record(
        expected_stackup,
        spec["layer_name"],
    )
    expected_layer[assignment_field] = spec["material_name"]
    native_field = {
        "material": "Material0",
        "fill_material": "FillMaterial0",
    }[assignment_field]
    native_properties = expected_layer.get("native_properties")
    if not isinstance(native_properties, dict) or native_field not in native_properties:
        raise LiveBackendError(
            f"3D Layout native {native_field} readback is unavailable"
        )
    native_properties[native_field] = spec["material_name"]
    if _digest(after_stackup) != _digest(expected_stackup):
        raise LiveBackendError(
            "unexpected 3D Layout stackup change outside the approved layer field"
        )


def _rollback_layout_material_create_assign(
    app: Any,
    spec: dict[str, Any],
    before_layer: dict[str, Any],
    created_name: str,
    *,
    before_catalog: dict[str, Any],
    before_stackup: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = []
    field = spec["assignment_field"]
    try:
        current_stackup = _layout_full_stackup_snapshot(app)
        current_layer = _layout_stackup_layer_record(
            current_stackup,
            spec["layer_name"],
        )
        layer_object = None
        if current_layer.get(field) != before_layer.get(field):
            layer_object = _layout_stackup_layer_object(
                app,
                name=spec["layer_name"],
                expected_id=before_layer["id"],
                expected_type=before_layer["type"],
            )
            setattr(layer_object, field, before_layer.get(field))
        current_native = current_layer.get("native_properties")
        before_native = before_layer.get("native_properties")
        if (
            isinstance(current_native, dict)
            and isinstance(before_native, dict)
            and current_native.get("Color") != before_native.get("Color")
        ):
            if layer_object is None:
                layer_object = _layout_stackup_layer_object(
                    app,
                    name=spec["layer_name"],
                    expected_id=before_layer["id"],
                    expected_type=before_layer["type"],
                )
            _restore_layout_layer_native_color(layer_object, before_layer)
    except Exception as exc:
        errors.append(f"layer restore failed: {type(exc).__name__}: {exc}")

    material_rollback = _rollback_hfss_material_create(
        app,
        created_name,
        before_catalog=before_catalog,
    )
    if not material_rollback["complete"]:
        errors.append("material removal or catalog restore failed")

    stackup_readback_error = ""
    try:
        after_stackup = _layout_full_stackup_snapshot(app)
    except Exception as exc:
        after_stackup = []
        stackup_readback_error = f"{type(exc).__name__}: {exc}"
    stackup_complete = (
        not stackup_readback_error
        and _digest(after_stackup) == _digest(before_stackup)
    )
    if not stackup_complete:
        errors.append("full stackup snapshot was not restored")
    return {
        "complete": not errors and material_rollback["complete"] and stackup_complete,
        "layer_name": spec["layer_name"],
        "assignment_field": field,
        "stackup_before_digest": _digest(before_stackup),
        "stackup_after_digest": _digest(after_stackup) if not stackup_readback_error else "",
        "stackup_readback_error": stackup_readback_error,
        "material_rollback": material_rollback,
        "errors": errors,
    }


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


def _open_aedt_change_summary(value: Any) -> str:
    """Keep the approval intent concise; it is bound to the frozen code digest."""
    if value is None or not str(value).strip():
        return "执行已批准的 AEDT/PyAEDT 修改脚本"
    if not isinstance(value, str):
        raise LiveBackendError("change_summary must be a short text string")
    summary = " ".join(value.split())
    if len(summary) > 600:
        raise LiveBackendError("change_summary must be at most 600 characters")
    return summary


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
