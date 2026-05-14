from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


class PyaedtAdapter:
    def __init__(
        self,
        project_id: str,
        design_id: str,
        version: str = "2026.1",
        non_graphical: bool = True,
        ansysem_root: str = "",
        awp_root: str = "",
    ) -> None:
        _ensure_aedt_environment(version=version, ansysem_root=ansysem_root, awp_root=awp_root)
        from ansys.aedt.core import Hfss

        self.app = Hfss(
            project=project_id,
            design=design_id,
            version=version,
            non_graphical=non_graphical,
            new_desktop=True,
        )

    def health_check(self) -> bool:
        return bool(getattr(self.app, "design_name", ""))

    def execute_node_callable(self, fn: Callable[[Any], dict[str, Any] | None]) -> dict[str, Any]:
        return fn(self.app) or {}

    def snapshot_state(self) -> dict[str, Any]:
        objects = {}
        for name in getattr(self.app.modeler, "object_names", []):
            try:
                obj = self.app.modeler[name]
                material = getattr(obj, "material_name", "")
                faces = [face.id for face in getattr(obj, "faces", [])]
            except Exception:
                material = ""
                faces = []
            objects[name] = {"material": material, "faces": faces}
        return {
            "project_id": getattr(self.app, "project_name", ""),
            "design_id": getattr(self.app, "design_name", ""),
            "objects": objects,
            "ports": _safe_boundary_names(self.app, ("Port", "Lumped Port", "Wave Port")),
            "boundaries": _safe_boundary_names(self.app, ("Radiation", "Perfect E", "Finite Conductivity")),
            "setups": {name: {} for name in getattr(self.app, "setup_names", [])},
            "sweeps": _safe_sweep_names(self.app),
        }

    def release(self) -> None:
        self.app.release_desktop(close_projects=True, close_desktop=True)


def _safe_boundary_names(app: Any, boundary_types: tuple[str, ...]) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    try:
        for boundary in getattr(app, "boundaries", []):
            boundary_type = str(getattr(boundary, "type", ""))
            if boundary_type in boundary_types:
                output[str(getattr(boundary, "name", ""))] = {"type": boundary_type}
    except Exception:
        return {}
    return output


def _safe_sweep_names(app: Any) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    try:
        for setup in getattr(app, "setups", []):
            setup_name = str(getattr(setup, "name", ""))
            for sweep in getattr(setup, "sweeps", []):
                sweep_name = str(getattr(sweep, "name", ""))
                output[sweep_name] = {"setup": setup_name}
    except Exception:
        return {}
    return output


def _ensure_aedt_environment(version: str, ansysem_root: str = "", awp_root: str = "") -> None:
    suffix = _version_suffix(version)
    ansysem_var = f"ANSYSEM_ROOT{suffix}"
    awp_var = f"AWP_ROOT{suffix}"
    resolved_awp_root = _resolve_optional_path(awp_root)
    resolved_ansysem_root = _resolve_optional_path(ansysem_root)
    if not resolved_awp_root:
        candidate = Path("~/ansys_inc").expanduser() / f"v{suffix}"
        if candidate.exists():
            resolved_awp_root = candidate
    if not resolved_ansysem_root and resolved_awp_root:
        candidate = resolved_awp_root / "AnsysEM"
        if candidate.exists():
            resolved_ansysem_root = candidate
    if resolved_ansysem_root:
        os.environ.setdefault(ansysem_var, str(resolved_ansysem_root))
        os.environ["PATH"] = f"{resolved_ansysem_root}{os.pathsep}{os.environ.get('PATH', '')}"
    if resolved_awp_root:
        os.environ.setdefault(awp_var, str(resolved_awp_root))


def _resolve_optional_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.exists() else None


def _version_suffix(version: str) -> str:
    parts = version.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]) % 100}{int(parts[1])}"
    digits = "".join(char for char in version if char.isdigit())
    return digits[-3:] if len(digits) >= 3 else digits
