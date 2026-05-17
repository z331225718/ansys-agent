from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AedtModelFacts:
    project_id: str = ""
    design_id: str = ""
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    materials: dict[str, str] = field(default_factory=dict)
    faces: dict[str, list[Any]] = field(default_factory=dict)
    ports: dict[str, dict[str, Any]] = field(default_factory=dict)
    boundaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    setups: dict[str, dict[str, Any]] = field(default_factory=dict)
    sweeps: dict[str, dict[str, Any]] = field(default_factory=dict)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "design_id": self.design_id,
            "objects": self.objects,
            "materials": self.materials,
            "faces": self.faces,
            "ports": self.ports,
            "boundaries": self.boundaries,
            "setups": self.setups,
            "sweeps": self.sweeps,
            "reports": self.reports,
            "summary": {
                "object_count": len(self.objects),
                "port_count": len(self.ports),
                "boundary_count": len(self.boundaries),
                "setup_count": len(self.setups),
                "sweep_count": len(self.sweeps),
                "report_count": len(self.reports),
            },
        }


def inspect_aedt_model(source: Any) -> AedtModelFacts:
    snapshot = source.snapshot_state() if hasattr(source, "snapshot_state") else source
    if not isinstance(snapshot, dict):
        raise TypeError("AEDT inspection source must be a snapshot mapping or adapter")

    objects = _mapping(snapshot.get("objects"))
    materials = {}
    faces = {}
    for name, data in objects.items():
        if isinstance(data, dict):
            material = data.get("material") or data.get("material_name")
            if material:
                materials[name] = str(material)
            faces[name] = list(data.get("faces", [])) if isinstance(data.get("faces", []), list) else []

    return AedtModelFacts(
        project_id=str(snapshot.get("project_id", "")),
        design_id=str(snapshot.get("design_id", "")),
        objects=objects,
        materials=materials,
        faces=faces,
        ports=_mapping(snapshot.get("ports")),
        boundaries=_mapping(snapshot.get("boundaries")),
        setups=_mapping(snapshot.get("setups")),
        sweeps=_mapping(snapshot.get("sweeps")),
        reports=_mapping(snapshot.get("reports")),
    )


def _mapping(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        result[str(key)] = dict(item) if isinstance(item, dict) else {"value": item}
    return result
