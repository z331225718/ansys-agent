from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from aedt_agent.interactive.contracts import (
    LayoutPathRecord,
    ParameterizationPreview,
    ParameterizationResult,
    PathSelector,
    path_snapshot_digest,
)
from aedt_agent.live.versioning import normalize_aedt_version


class LayoutSessionError(RuntimeError):
    pass


class StalePreviewError(LayoutSessionError):
    pass


class ParameterizationVerificationError(LayoutSessionError):
    pass


@dataclass
class LayoutSession:
    session_id: str
    edb: Any
    source_input_path: Path
    source_project_path: Path | None
    source_edb_path: Path
    active_project_path: Path | None
    active_edb_path: Path
    session_workspace: Path
    source_fingerprint: str
    writable: bool
    ephemeral: bool
    version: str
    edb_backend: str
    previews: dict[str, ParameterizationPreview] = field(default_factory=dict)

    @property
    def working_project_path(self) -> str | None:
        if not self.writable:
            return None
        return str(self.active_project_path or self.active_edb_path)


class LayoutSessionManager:
    def __init__(self, *, edb_factory: Callable[..., Any] | None = None) -> None:
        self._edb_factory = edb_factory
        self._sessions: dict[str, LayoutSession] = {}

    def open_session(
        self,
        project_path: str | Path,
        *,
        writable: bool = False,
        workspace: str | Path | None = None,
        version: str = "2026.1",
        edb_backend: str = "auto",
    ) -> dict[str, Any]:
        normalized_version = normalize_aedt_version(version)
        grpc_mode = _grpc_mode_for_version(edb_backend, normalized_version)
        source_input, source_project, source_edb = _resolve_layout_bundle(Path(project_path))
        source_fingerprint = _bundle_fingerprint(source_project, source_edb)
        session_id = f"layout-session-{uuid4().hex}"
        workspace_root = Path(workspace) if workspace is not None else (
            Path.cwd() / ".aedt-agent" / "interactive"
            if writable
            else Path(tempfile.gettempdir()) / "aedt-agent-interactive"
        )
        session_workspace = workspace_root.expanduser().resolve() / session_id
        active_project, active_edb = _copy_layout_bundle(
            source_project,
            source_edb,
            session_workspace,
        )
        try:
            _ensure_edb_environment(normalized_version)
            with _backend_output_to_stderr():
                edb = self._edb_class()(
                    edbpath=str(active_edb),
                    version=normalized_version,
                    grpc=grpc_mode,
                    isreadonly=not writable,
                )
        except Exception:
            shutil.rmtree(session_workspace, ignore_errors=True)
            raise
        session = LayoutSession(
            session_id=session_id,
            edb=edb,
            source_input_path=source_input,
            source_project_path=source_project,
            source_edb_path=source_edb,
            active_project_path=active_project,
            active_edb_path=active_edb,
            session_workspace=session_workspace,
            source_fingerprint=source_fingerprint,
            writable=writable,
            ephemeral=not writable,
            version=normalized_version,
            edb_backend=edb_backend,
        )
        self._sessions[session_id] = session
        return self.session_info(session_id)

    def get_session(self, session_id: str) -> LayoutSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown layout session: {session_id}") from exc

    def session_info(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        return {
            "session_id": session.session_id,
            "source_project_path": str(session.source_project_path or session.source_edb_path),
            "working_project_path": session.working_project_path,
            "active_edb_path": str(session.active_edb_path),
            "source_fingerprint": session.source_fingerprint,
            "writable": session.writable,
            "version": session.version,
            "edb_backend": session.edb_backend,
        }

    def close_session(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        close_error: Exception | None = None
        try:
            with _backend_output_to_stderr():
                _close_edb(session.edb)
        except Exception as exc:  # pragma: no cover - depends on external AEDT state
            close_error = exc
        finally:
            self._sessions.pop(session_id, None)
        source_unchanged = self._source_unchanged(session)
        scratch_removed = False
        if session.ephemeral:
            shutil.rmtree(session.session_workspace, ignore_errors=True)
            scratch_removed = not session.session_workspace.exists()
        if close_error is not None:
            raise LayoutSessionError(f"failed to close EDB session: {close_error}") from close_error
        if not source_unchanged:
            raise LayoutSessionError("source project changed while the layout session was active")
        return {
            "session_id": session_id,
            "closed": True,
            "source_unchanged": True,
            "working_project_path": session.working_project_path,
            "scratch_removed": scratch_removed,
        }

    def list_paths(
        self,
        session_id: str,
        selector: PathSelector | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        with _backend_output_to_stderr():
            selected = _select_paths(_layout_path_records(session.edb), (selector or PathSelector()).validate())
        return {
            "session_id": session_id,
            "count": len(selected),
            "paths": [record.to_dict() for record in selected],
            "snapshot_digest": path_snapshot_digest(selected),
            "working_project_path": session.working_project_path,
        }

    def preview_parameterize_width(
        self,
        session_id: str,
        *,
        selector: PathSelector,
        variable_name: str,
        variable_value: Any,
    ) -> ParameterizationPreview:
        session = self.get_session(session_id)
        normalized_name = _validate_variable_name(variable_name)
        normalized_value = dimension_to_edb_string(variable_value)
        dimension_to_meters(normalized_value)
        with _backend_output_to_stderr():
            targets = _select_paths(_layout_path_records(session.edb), selector.validate())
        if not targets:
            raise ValueError("path selector matched no primitives")
        preview = ParameterizationPreview.create(
            session_id=session_id,
            selector=selector,
            variable_name=normalized_name,
            variable_value=normalized_value,
            targets=targets,
            working_project_path=session.working_project_path,
        )
        session.previews[preview.preview_id] = preview
        return preview

    def apply_parameterize_width(
        self,
        session_id: str,
        preview_id: str,
    ) -> ParameterizationResult:
        session = self.get_session(session_id)
        if not session.writable:
            raise PermissionError("parameterization requires a writable working-copy session")
        try:
            preview = session.previews[preview_id]
        except KeyError as exc:
            raise KeyError(f"unknown preview_id for session: {preview_id}") from exc

        with _backend_output_to_stderr():
            current_targets = _select_paths(_layout_path_records(session.edb), preview.selector)
        if path_snapshot_digest(current_targets) != preview.snapshot_digest:
            raise StalePreviewError("layout paths changed after preview; create a new preview before applying")

        with _backend_output_to_stderr():
            primitives = _path_primitives_by_id(session.edb)
        target_ids = [record.primitive_id for record in preview.targets]
        missing = [primitive_id for primitive_id in target_ids if primitive_id not in primitives]
        if missing:
            raise StalePreviewError(f"preview target no longer exists: {missing[0]}")

        variable_created = False
        old_widths = {
            record.primitive_id: (
                record.width_expression if record.is_parameterized else record.width_m
            )
            for record in preview.targets
        }
        try:
            with _backend_output_to_stderr():
                variable_created = _create_design_parameter(
                    session.edb,
                    preview.variable_name,
                    preview.variable_value,
                )
                for primitive_id in target_ids:
                    primitives[primitive_id].width = preview.variable_name
                _save_edb(session.edb)
                after_by_id = {record.primitive_id: record for record in _layout_path_records(session.edb)}
            after = [after_by_id[primitive_id] for primitive_id in target_ids if primitive_id in after_by_id]
            verified = [
                record
                for record in after
                if record.is_parameterized
                and _expression_references_variable(record.width_expression, preview.variable_name)
            ]
            if len(after) != len(target_ids) or len(verified) != len(target_ids):
                raise ParameterizationVerificationError(
                    "postcondition failed: every target must retain its id and reference the parameter variable"
                )
            if not self._source_unchanged(session):
                raise ParameterizationVerificationError("source project changed during working-copy edit")
        except Exception:
            with _backend_output_to_stderr():
                _rollback_width_parameterization(
                    session.edb,
                    primitives,
                    old_widths,
                    preview.variable_name if variable_created else None,
                )
            raise

        session.previews.pop(preview_id, None)
        return ParameterizationResult(
            session_id=session_id,
            preview_id=preview_id,
            status="verified",
            variable_name=preview.variable_name,
            variable_value=preview.variable_value,
            target_count=len(target_ids),
            verified_count=len(verified),
            before=preview.targets,
            after=tuple(after),
            working_project_path=session.working_project_path or str(session.active_edb_path),
            evidence={
                "source_unchanged": True,
                "target_ids": target_ids,
                "snapshot_before": preview.snapshot_digest,
                "snapshot_after": path_snapshot_digest(after),
                "variable_is_parameter": _variable_is_parameter(session.edb, preview.variable_name),
            },
        )

    def _source_unchanged(self, session: LayoutSession) -> bool:
        return _bundle_fingerprint(session.source_project_path, session.source_edb_path) == session.source_fingerprint

    def _edb_class(self) -> Callable[..., Any]:
        if self._edb_factory is not None:
            return self._edb_factory
        from pyedb.generic.settings import settings

        # MCP uses stdout as its protocol stream and the CLI promises JSON output.
        settings.enable_screen_logs = False
        from pyedb import Edb

        return Edb


def dimension_to_meters(value: Any) -> float:
    if isinstance(value, dict):
        magnitude = float(value["value"])
        unit = str(value.get("unit") or "m").strip().casefold()
        return magnitude * _unit_factor(unit)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = re.fullmatch(
        r"([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*([A-Za-z\u00b5\u03bc]*)",
        text,
    )
    if not match:
        raise ValueError(f"invalid dimension: {value}")
    return float(match.group(1)) * _unit_factor((match.group(2) or "m").casefold())


def dimension_to_edb_string(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value['value']}{value.get('unit') or 'm'}"
    if isinstance(value, (int, float)):
        return f"{value}m"
    return str(value).strip()


def selector_from_payload(payload: dict[str, Any] | None) -> PathSelector:
    payload = dict(payload or {})
    target = payload.get("target_width")
    tolerance = payload.get("tolerance", "1nm")
    parameterized = payload.get("parameterized")
    if parameterized is not None and not isinstance(parameterized, bool):
        raise TypeError("selector parameterized must be boolean or null")
    return PathSelector(
        target_width_m=None if target is None else dimension_to_meters(target),
        tolerance_m=dimension_to_meters(tolerance),
        nets=tuple(str(value) for value in payload.get("nets") or ()),
        layers=tuple(str(value) for value in payload.get("layers") or ()),
        primitive_ids=tuple(str(value) for value in payload.get("primitive_ids") or ()),
        parameterized=parameterized,
    ).validate()


def _resolve_layout_bundle(path: Path) -> tuple[Path, Path | None, Path]:
    resolved = path.expanduser().resolve()
    if resolved.suffix.casefold() == ".aedt":
        if not resolved.is_file():
            raise FileNotFoundError(f"AEDT project not found: {resolved}")
        edb_path = resolved.with_suffix(".aedb")
        if not edb_path.is_dir():
            raise FileNotFoundError(f"3D Layout sidecar AEDB not found: {edb_path}")
        return resolved, resolved, edb_path
    if resolved.suffix.casefold() == ".aedb":
        if not resolved.is_dir():
            raise FileNotFoundError(f"AEDB not found: {resolved}")
        project_path = resolved.with_suffix(".aedt")
        return resolved, project_path if project_path.is_file() else None, resolved
    raise ValueError("project_path must point to an .aedt project or .aedb directory")


def _copy_layout_bundle(
    source_project: Path | None,
    source_edb: Path,
    destination: Path,
) -> tuple[Path | None, Path]:
    destination.mkdir(parents=True, exist_ok=False)
    active_project: Path | None = None
    if source_project is not None:
        active_project = destination / source_project.name
        shutil.copy2(source_project, active_project)
    active_edb = destination / source_edb.name
    shutil.copytree(source_edb, active_edb)
    return active_project, active_edb


def _bundle_fingerprint(project_path: Path | None, edb_path: Path) -> str:
    digest = hashlib.sha256()
    if project_path is not None:
        digest.update(project_path.name.encode("utf-8"))
        digest.update(b"\0")
        _hash_file_into(digest, project_path)
    for path in sorted((item for item in edb_path.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        digest.update(path.relative_to(edb_path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        _hash_file_into(digest, path)
    return digest.hexdigest()


def _hash_file_into(digest: Any, path: Path) -> None:
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)


def _layout_path_records(edb: Any) -> list[LayoutPathRecord]:
    records: list[LayoutPathRecord] = []
    for primitive in _modeler_primitives(edb):
        if not _is_path_primitive(primitive):
            continue
        width = getattr(primitive, "width", None)
        if width is None:
            continue
        try:
            width_m = float(width)
        except (TypeError, ValueError) as exc:
            raise LayoutSessionError(f"path width is not numeric for primitive {_primitive_id(primitive)}") from exc
        expression = _value_expression(width)
        records.append(
            LayoutPathRecord(
                primitive_id=_primitive_id(primitive),
                name=str(getattr(primitive, "name", "") or ""),
                net=_primitive_net(primitive),
                layer=_primitive_layer(primitive),
                width_m=width_m,
                width_expression=expression,
                is_parameterized=_primitive_is_parameterized(primitive, expression),
            )
        )
    records.sort(key=lambda record: (record.layer.casefold(), record.net.casefold(), record.primitive_id))
    return records


def _select_paths(records: list[LayoutPathRecord], selector: PathSelector) -> list[LayoutPathRecord]:
    nets = {value.casefold() for value in selector.nets}
    layers = {value.casefold() for value in selector.layers}
    primitive_ids = set(selector.primitive_ids)
    selected = []
    for record in records:
        if selector.target_width_m is not None and abs(record.width_m - selector.target_width_m) > selector.tolerance_m:
            continue
        if nets and record.net.casefold() not in nets:
            continue
        if layers and record.layer.casefold() not in layers:
            continue
        if primitive_ids and record.primitive_id not in primitive_ids:
            continue
        if selector.parameterized is not None and record.is_parameterized is not selector.parameterized:
            continue
        selected.append(record)
    return selected


def _modeler_primitives(edb: Any) -> list[Any]:
    modeler = getattr(edb, "modeler", None)
    if modeler is None:
        raise LayoutSessionError("EDB modeler is unavailable")
    getter = getattr(modeler, "get_primitives", None)
    if callable(getter):
        try:
            return list(getter(is_void=False) or [])
        except TypeError:
            return list(getter() or [])
    primitives = getattr(modeler, "primitives", None)
    if primitives is not None:
        return list(primitives.values() if isinstance(primitives, dict) else primitives)
    raise LayoutSessionError("EDB modeler does not expose primitives")


def _path_primitives_by_id(edb: Any) -> dict[str, Any]:
    return {
        _primitive_id(primitive): primitive
        for primitive in _modeler_primitives(edb)
        if _is_path_primitive(primitive)
    }


def _is_path_primitive(primitive: Any) -> bool:
    if primitive.__class__.__name__.casefold() == "path":
        return True
    for attr in ("primitive_type", "type", "object_type", "edb_object_type", "primitive_object_type"):
        value = getattr(primitive, attr, None)
        if value is None:
            continue
        normalized = str(value).strip().casefold().rsplit(".", 1)[-1]
        if normalized == "path":
            return True
    return False


def _primitive_id(primitive: Any) -> str:
    for attr in ("id", "primitive_id", "uid"):
        value = getattr(primitive, attr, None)
        if value is not None and str(value).strip():
            return str(value)
    raise LayoutSessionError("path primitive has no stable id")


def _primitive_net(primitive: Any) -> str:
    for attr in ("net_name", "net"):
        value = getattr(primitive, attr, None)
        if value is not None:
            return str(getattr(value, "name", value) or "")
    return ""


def _primitive_layer(primitive: Any) -> str:
    for attr in ("layer_name", "layer"):
        value = getattr(primitive, attr, None)
        if value is not None:
            return str(getattr(value, "name", value) or "")
    return ""


def _value_expression(value: Any) -> str:
    expression = getattr(value, "expression", None)
    if expression is not None:
        return str(expression)
    to_string = getattr(value, "ToString", None)
    if callable(to_string):
        return str(to_string())
    return str(value)


def _primitive_is_parameterized(primitive: Any, expression: str) -> bool:
    value = getattr(primitive, "is_parameterized", None)
    if callable(value):
        try:
            return bool(value())
        except Exception:
            pass
    elif value is not None:
        return bool(value)
    return bool(re.search(r"[$A-Za-z_][A-Za-z0-9_$]*", expression)) and not bool(
        re.fullmatch(r"[-+0-9.eE]+(?:m|mm|mil|um|\u00b5m|\u03bcm|nm)?", expression.strip())
    )


def _validate_variable_name(value: str) -> str:
    name = str(value).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("variable_name must be a design variable identifier without '$'")
    return name


def _create_design_parameter(edb: Any, name: str, value: str) -> bool:
    variables = getattr(edb, "design_variables", {}) or {}
    if name in variables:
        raise ValueError(f"design variable already exists: {name}")
    active_cell = getattr(edb, "active_cell", None)
    add_variable = getattr(active_cell, "add_variable", None)
    if callable(add_variable):
        try:
            add_variable(name, value, is_param=True)
        except TypeError:
            add_variable(name, value, True)
    else:
        add_design_variable = getattr(edb, "add_design_variable", None)
        if not callable(add_design_variable):
            raise LayoutSessionError("EDB backend cannot create design parameters")
        try:
            result = add_design_variable(name, value, is_parameter=True)
        except TypeError:
            result = add_design_variable(name, value)
        if isinstance(result, tuple) and not bool(result[0]):
            raise LayoutSessionError(f"failed to create design parameter: {name}")
        if isinstance(result, bool) and not result:
            raise LayoutSessionError(f"failed to create design parameter: {name}")
    if not _variable_is_parameter(edb, name):
        _delete_variable(edb, name)
        raise ParameterizationVerificationError(f"created variable is not a design parameter: {name}")
    return True


def _variable_is_parameter(edb: Any, name: str) -> bool:
    variables = getattr(edb, "design_variables", {}) or {}
    variable = variables.get(name) if hasattr(variables, "get") else None
    if variable is None:
        return False
    value = getattr(variable, "is_parameter", None)
    if callable(value):
        return bool(value())
    return bool(value)


def _delete_variable(edb: Any, name: str) -> None:
    variables = getattr(edb, "design_variables", {}) or {}
    variable = variables.get(name) if hasattr(variables, "get") else None
    delete = getattr(variable, "delete", None)
    if callable(delete):
        delete()


def _expression_references_variable(expression: str, variable_name: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_$]){re.escape(variable_name)}(?![A-Za-z0-9_$])", expression))


def _rollback_width_parameterization(
    edb: Any,
    primitives: dict[str, Any],
    old_widths: dict[str, Any],
    created_variable: str | None,
) -> None:
    rollback_error: Exception | None = None
    try:
        for primitive_id, old_width in old_widths.items():
            if primitive_id in primitives:
                primitives[primitive_id].width = old_width
        if created_variable:
            _delete_variable(edb, created_variable)
        _save_edb(edb)
    except Exception as exc:  # pragma: no cover - external backend failure
        rollback_error = exc
    if rollback_error is not None:
        raise LayoutSessionError(f"parameterization failed and rollback also failed: {rollback_error}") from rollback_error


def _save_edb(edb: Any) -> None:
    save = getattr(edb, "save", None)
    if not callable(save):
        raise LayoutSessionError("EDB backend does not expose save()")
    result = save()
    if result is False:
        raise LayoutSessionError("EDB save() returned False")


def _close_edb(edb: Any) -> None:
    close = getattr(edb, "close", None)
    if callable(close):
        close()


def _grpc_mode(value: str) -> bool | None:
    normalized = str(value).strip().casefold()
    if normalized in {"", "auto"}:
        return None
    if normalized in {"grpc", "true", "1", "yes", "on"}:
        return True
    if normalized in {"dotnet", "legacy", "false", "0", "no", "off"}:
        return False
    raise ValueError(f"unsupported edb_backend: {value}")


def _grpc_mode_for_version(value: str, version: str) -> bool | None:
    mode = _grpc_mode(value)
    release = tuple(int(part) for part in normalize_aedt_version(version).split("."))
    if mode is True and release < (2025, 2):
        raise ValueError(f"PyEDB gRPC is not supported by AEDT {release[0]}.{release[1]}; use auto or dotnet")
    if mode is None and release < (2026, 1):
        return False
    return mode


@contextmanager
def _backend_output_to_stderr():
    """Keep Python and child-process output away from an MCP stdio protocol stream."""

    saved_stdout_fd: int | None = None
    stdout_fd: int | None = None
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
        saved_stdout_fd = os.dup(stdout_fd)
        os.dup2(stderr_fd, stdout_fd)
    except (AttributeError, OSError, ValueError):
        saved_stdout_fd = None
        stdout_fd = None
    try:
        with redirect_stdout(sys.stderr):
            yield
    finally:
        if saved_stdout_fd is not None and stdout_fd is not None:
            try:
                sys.stderr.flush()
                os.dup2(saved_stdout_fd, stdout_fd)
            finally:
                os.close(saved_stdout_fd)


def _ensure_edb_environment(version: str) -> None:
    suffix = _version_suffix(version)
    ansysem_name = f"ANSYSEM_ROOT{suffix}"
    awp_name = f"AWP_ROOT{suffix}"
    ansys_name = f"ANSYS{suffix}_DIR"
    ansysem_root = _first_existing_path(
        os.environ.get(ansysem_name),
        _persistent_environment_value(ansysem_name),
        os.environ.get("AEDT_AGENT_ANSYSEM_ROOT"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ANSYS Inc" / f"v{suffix}" / "AnsysEM",
        Path.home() / "ansys_inc" / f"v{suffix}" / "AnsysEM",
    )
    awp_root = _first_existing_path(
        os.environ.get(awp_name),
        _persistent_environment_value(awp_name),
        os.environ.get("AEDT_AGENT_AWP_ROOT"),
        None if ansysem_root is None else ansysem_root.parent,
    )
    if ansysem_root is not None:
        os.environ.setdefault(ansysem_name, str(ansysem_root))
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        if str(ansysem_root) not in path_entries:
            os.environ["PATH"] = f"{ansysem_root}{os.pathsep}{os.environ.get('PATH', '')}"
    if awp_root is not None:
        os.environ.setdefault(awp_name, str(awp_root))
        ansys_dir = awp_root / "ANSYS"
        if ansys_dir.is_dir():
            os.environ.setdefault(ansys_name, str(ansys_dir))
    license_value = os.environ.get("ANSYSLMD_LICENSE_FILE") or _persistent_environment_value(
        "ANSYSLMD_LICENSE_FILE"
    )
    if license_value:
        os.environ.setdefault("ANSYSLMD_LICENSE_FILE", license_value)


def _first_existing_path(*values: str | Path | None) -> Path | None:
    for value in values:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_dir():
            return path.resolve()
    return None


def _persistent_environment_value(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows-only helper
        return None
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _version_suffix(version: str) -> str:
    parts = str(version).strip().split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]) % 100}{int(parts[1])}"
    digits = "".join(character for character in str(version) if character.isdigit())
    if len(digits) < 3:
        raise ValueError(f"cannot derive AEDT environment suffix from version: {version}")
    return digits[-3:]


def _unit_factor(unit: str) -> float:
    factors = {
        "m": 1.0,
        "meter": 1.0,
        "meters": 1.0,
        "mm": 1e-3,
        "mil": 25.4e-6,
        "mils": 25.4e-6,
        "um": 1e-6,
        "\u00b5m": 1e-6,
        "\u03bcm": 1e-6,
        "nm": 1e-9,
    }
    try:
        return factors[unit]
    except KeyError as exc:
        raise ValueError(f"unsupported dimension unit: {unit}") from exc
