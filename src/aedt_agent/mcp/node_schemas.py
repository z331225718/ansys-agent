from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NodeInputSchema:
    required: dict[str, type | tuple[type, ...]]
    optional: dict[str, type | tuple[type, ...]] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed_keys(self) -> set[str]:
        return set(self.required) | set(self.optional) | set(self.defaults)


@dataclass(frozen=True)
class SchemaValidationResult:
    passed: bool
    inputs: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


NODE_SCHEMAS: dict[str, NodeInputSchema] = {
    "create_substrate": NodeInputSchema(
        required={"origin": list, "size": list, "material": str},
        optional={"name": str, "units": str},
        defaults={"name": "Substrate", "units": "mm"},
    ),
    "create_conductor_or_geometry_group": NodeInputSchema(
        required={"geometry": list},
        optional={"group_name": str},
        defaults={},
    ),
    "create_airbox": NodeInputSchema(
        required={"padding": (int, float, list)},
        optional={"name": str},
        defaults={"name": "AirBox"},
    ),
    "assign_boundary": NodeInputSchema(
        required={"assignment": (str, list, dict), "boundary_type": str},
        optional={"name": str},
        defaults={"name": "Boundary"},
    ),
    "create_port": NodeInputSchema(
        required={"port_type": str, "assignment": (str, int, dict)},
        optional={"name": str, "integration_line": (list, dict), "reference": (str, int, list, dict), "impedance": (int, float, str)},
        defaults={"name": "Port1", "impedance": 50},
    ),
    "create_wave_port": NodeInputSchema(
        required={"assignment": (str, int, dict)},
        optional={"name": str, "integration_line": (list, dict), "reference": (str, int, list, dict), "impedance": (int, float, str)},
        defaults={"name": "WavePort1", "impedance": 50},
    ),
    "select_face": NodeInputSchema(
        required={"object_name": str},
        optional={"axis": str, "side": str},
        defaults={"axis": "x", "side": "max"},
    ),
    "create_setup": NodeInputSchema(
        required={"frequency": (str, int, float)},
        optional={"name": str, "max_passes": int},
        defaults={"name": "Setup1", "max_passes": 10},
    ),
    "create_sweep_or_export": NodeInputSchema(
        required={"setup": str},
        optional={"name": str, "start": (str, int, float), "stop": (str, int, float), "points": int},
        defaults={"name": "Sweep1", "start": "1GHz", "stop": "10GHz", "points": 101},
    ),
    "solve_setup": NodeInputSchema(
        required={"setup": str},
        optional={"cores": int},
        defaults={},
    ),
    "create_sparameter_report": NodeInputSchema(
        required={"setup": str, "sweep": str},
        optional={"report_name": str, "output_dir": str, "touchstone_name": str, "ports": list},
        defaults={"report_name": "S Parameter Plot", "touchstone_name": "sparameters.s2p"},
    ),
}


def describe_node_schema(node_id: str) -> dict[str, Any]:
    schema = _get_schema(node_id)
    return {
        "node_id": node_id,
        "required": sorted(schema.required),
        "optional": sorted(set(schema.optional) | set(schema.defaults)),
        "defaults": dict(schema.defaults),
    }


def validate_node_inputs(node_id: str, inputs: dict[str, Any]) -> SchemaValidationResult:
    if not isinstance(inputs, dict):
        return SchemaValidationResult(False, errors=["inputs must be a mapping"])
    try:
        schema = _get_schema(node_id)
    except KeyError:
        return SchemaValidationResult(False, errors=[f"unknown node_id: {node_id}"])

    errors: list[str] = []
    normalized = dict(schema.defaults)
    normalized.update(inputs)
    for key in schema.required:
        if key not in normalized:
            errors.append(f"missing required input: {key}")
    for key in inputs:
        if key not in schema.allowed_keys:
            errors.append(f"unknown input: {key}")
    for key, expected_type in {**schema.required, **schema.optional}.items():
        if key in normalized and not isinstance(normalized[key], expected_type):
            errors.append(f"wrong type for {key}: expected {_type_name(expected_type)}")
    if node_id in {"create_port", "assign_boundary"}:
        for key in ("assignment", "reference"):
            if key in normalized and isinstance(normalized[key], dict) and not _looks_like_node_output(normalized[key]):
                errors.append(f"wrong value for {key}: expected node output reference")
    return SchemaValidationResult(not errors, inputs=normalized if not errors else {}, errors=errors)


def _get_schema(node_id: str) -> NodeInputSchema:
    return NODE_SCHEMAS[node_id]


def _type_name(value: type | tuple[type, ...]) -> str:
    if isinstance(value, tuple):
        return " or ".join(item.__name__ for item in value)
    return value.__name__


def _looks_like_node_output(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("selected_face_id", "object_name", "created", "output"))
