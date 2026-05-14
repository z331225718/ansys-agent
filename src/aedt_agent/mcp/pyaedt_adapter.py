from __future__ import annotations

from typing import Any, Callable


class PyaedtAdapter:
    def __init__(
        self,
        project_id: str,
        design_id: str,
        version: str = "2026.1",
        non_graphical: bool = True,
    ) -> None:
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
