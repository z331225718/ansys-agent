from __future__ import annotations

from aedt_agent.interactive.contracts import CapabilityRisk, CapabilitySpec


_SELECTOR_PROPERTIES = {
    "target_width": {
        "description": "Target path width with units, for example 0.1mm.",
        "type": ["string", "number", "object"],
    },
    "tolerance": {
        "default": "1nm",
        "description": "Absolute width comparison tolerance.",
        "type": ["string", "number", "object"],
    },
    "nets": {"items": {"type": "string"}, "type": "array"},
    "layers": {"items": {"type": "string"}, "type": "array"},
    "primitive_ids": {"items": {"type": "string"}, "type": "array"},
    "parameterized": {"type": ["boolean", "null"]},
}


def _selector_schema() -> dict:
    return {
        "type": "object",
        "properties": dict(_SELECTOR_PROPERTIES),
        "additionalProperties": False,
    }


def _object_schema(required: list[str], properties: dict) -> dict:
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


class CapabilityCatalog:
    def __init__(self, specs: list[CapabilitySpec] | None = None) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        for spec in specs or builtin_capabilities():
            self.register(spec)

    def register(self, spec: CapabilitySpec) -> None:
        spec.validate()
        if spec.name in self._specs:
            raise ValueError(f"capability already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {name}") from exc

    def contains(self, name: str) -> bool:
        return name in self._specs

    def list_specs(self) -> list[CapabilitySpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def to_dict(self) -> dict:
        return {
            "version": "1",
            "capabilities": [spec.to_dict() for spec in self.list_specs()],
        }


def builtin_capabilities() -> list[CapabilitySpec]:
    path_output = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "count": {"type": "integer"},
            "paths": {"type": "array", "items": {"type": "object"}},
            "snapshot_digest": {"type": "string"},
        },
    }
    return [
        CapabilitySpec(
            name="layout.paths.list",
            description="List and filter HFSS 3D Layout path primitives without modifying the design.",
            risk=CapabilityRisk.READ_ONLY,
            input_schema=_object_schema(
                ["session_id"],
                {"session_id": {"type": "string"}, "selector": _selector_schema()},
            ),
            output_schema=path_output,
            postconditions=("source_project_unchanged",),
        ),
        CapabilitySpec(
            name="layout.path_width.parameterize.preview",
            description="Resolve a stable set of paths and preview binding their width to a design parameter.",
            risk=CapabilityRisk.READ_ONLY,
            input_schema=_object_schema(
                ["session_id", "selector", "variable_name", "variable_value"],
                {
                    "session_id": {"type": "string"},
                    "selector": _selector_schema(),
                    "variable_name": {"type": "string"},
                    "variable_value": {"type": ["string", "number", "object"]},
                },
            ),
            output_schema={
                "type": "object",
                "properties": {
                    "preview_id": {"type": "string"},
                    "target_count": {"type": "integer"},
                    "targets": {"type": "array", "items": {"type": "object"}},
                    "snapshot_digest": {"type": "string"},
                },
            },
            postconditions=("target_set_frozen_by_digest", "source_project_unchanged"),
        ),
        CapabilitySpec(
            name="layout.path_width.parameterize.apply",
            description="Apply an approved width-parameterization preview to a writable working copy.",
            risk=CapabilityRisk.REVERSIBLE_EDIT,
            input_schema=_object_schema(
                ["session_id", "preview_id"],
                {"session_id": {"type": "string"}, "preview_id": {"type": "string"}},
            ),
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"const": "verified"},
                    "target_count": {"type": "integer"},
                    "verified_count": {"type": "integer"},
                    "working_project_path": {"type": "string"},
                    "evidence": {"type": "object"},
                },
            },
            postconditions=(
                "source_project_unchanged",
                "target_count_unchanged",
                "every_target_width_references_variable",
                "every_target_reports_parameterized",
            ),
        ),
    ]
