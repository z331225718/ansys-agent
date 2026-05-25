from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SWEEP_TYPE_ALIASES = {
    "fast": "Fast",
    "interpolating": "Interpolating",
    "interpolation": "Interpolating",
    "interpolate": "Interpolating",
    "discrete": "Discrete",
}


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
        optional={
            "name": str,
            "start": (str, int, float),
            "stop": (str, int, float),
            "points": int,
            "sweep_type": str,
            "type": str,
        },
        defaults={"name": "Sweep1", "start": "1GHz", "stop": "10GHz", "points": 101, "sweep_type": "Discrete"},
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
    "create_farfield_setup": NodeInputSchema(
        required={},
        optional={
            "name": str,
            "definition": str,
            "theta_start": (str, int, float),
            "theta_stop": (str, int, float),
            "theta_step": (str, int, float),
            "phi_start": (str, int, float),
            "phi_stop": (str, int, float),
            "phi_step": (str, int, float),
            "units": str,
        },
        defaults={
            "name": "InfiniteSphere1",
            "definition": "Theta-Phi",
            "theta_start": 0,
            "theta_stop": 180,
            "theta_step": 5,
            "phi_start": 0,
            "phi_stop": 360,
            "phi_step": 5,
            "units": "deg",
        },
    ),
    "create_antenna_report": NodeInputSchema(
        required={"setup": str, "farfield": str},
        optional={"sweep": str, "report_name": str, "expression": (str, list), "output_dir": str, "primary_sweep": str, "export_report": bool},
        defaults={
            "sweep": "LastAdaptive",
            "report_name": "3D Gain Pattern",
            "expression": "dB(GainTotal)",
            "primary_sweep": "Theta",
            "export_report": False,
        },
    ),
    "import_layout_file": NodeInputSchema(
        required={"layout_file": str},
        optional={"output_dir": str, "import_backend": str, "edb_backend": str},
        defaults={"import_backend": "pyedb", "edb_backend": "auto"},
    ),
    "select_layout_nets": NodeInputSchema(
        required={"signal_nets": (str, list), "reference_nets": (str, list)},
        optional={},
        defaults={},
    ),
    "create_layout_cutout": NodeInputSchema(
        required={"layout": (str, dict), "signal_nets": (str, list, dict), "reference_nets": (str, list, dict)},
        optional={"expansion_size": (int, float), "extent_type": str, "threads": int},
        defaults={"expansion_size": 0.002, "extent_type": "ConvexHull"},
    ),
    "configure_layout_stackup": NodeInputSchema(
        required={"layout": (str, dict)},
        optional={"stackup_rule": str, "stackup_xml": str},
        defaults={"stackup_rule": "preserve_board_stackup"},
    ),
    "locate_layout_port_candidates": NodeInputSchema(
        required={"layout": (str, dict), "signal_nets": (str, list, dict), "reference_nets": (str, list, dict)},
        optional={"candidate_rule": str},
        defaults={"candidate_rule": "differential_component_endpoints"},
    ),
    "create_layout_ports": NodeInputSchema(
        required={"layout": (str, dict), "signal_nets": (str, list, dict), "reference_nets": (str, list, dict)},
        optional={
            "port_rule": str,
            "impedance": (int, float, str),
            "port_candidates": (str, dict),
            "solderball_type": str,
            "solderball_diameter": str,
            "solderball_mid_diameter": str,
            "solderball_height": str,
            "solderball_material": str,
        },
        defaults={"port_rule": "component_cylinder_or_toggle_via_pin_gap_ports", "impedance": 50},
    ),
    "create_layout_setup": NodeInputSchema(
        required={"frequency": (str, int, float)},
        optional={
            "name": str,
            "sweep_start": (str, int, float),
            "sweep_stop": (str, int, float),
            "sweep_type": str,
            "sweep_points": int,
            "use_q3d_for_dc": bool,
        },
        defaults={
            "name": "Setup1",
            "sweep_start": "0GHz",
            "sweep_stop": "67GHz",
            "sweep_type": "Interpolating",
            "sweep_points": 501,
            "use_q3d_for_dc": True,
        },
    ),
    "solve_layout": NodeInputSchema(
        required={"setup": (str, dict)},
        optional={"cores": int},
        defaults={},
    ),
    "create_layout_sparam_tdr_report": NodeInputSchema(
        required={"setup": (str, dict)},
        optional={"output_dir": str, "touchstone_name": str, "tdr_name": str},
        defaults={"touchstone_name": "import_cutout_demo.s2p", "tdr_name": "import_cutout_tdr.csv"},
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
    if node_id == "create_sweep_or_export":
        errors.extend(_validate_sweep_inputs(normalized, set(inputs)))
    if node_id in {"create_port", "assign_boundary"}:
        for key in ("assignment", "reference"):
            if key in normalized and isinstance(normalized[key], dict) and not _looks_like_node_output(normalized[key]):
                errors.append(f"wrong value for {key}: expected node output reference")
    if not errors and node_id == "create_sweep_or_export":
        source_key = "type" if "type" in inputs and "sweep_type" not in inputs else "sweep_type"
        normalized["sweep_type"] = normalize_sweep_type(normalized.get(source_key, "Discrete"))
    return SchemaValidationResult(not errors, inputs=normalized if not errors else {}, errors=errors)


def _get_schema(node_id: str) -> NodeInputSchema:
    return NODE_SCHEMAS[node_id]


def _type_name(value: type | tuple[type, ...]) -> str:
    if isinstance(value, tuple):
        return " or ".join(item.__name__ for item in value)
    return value.__name__


def _looks_like_node_output(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("selected_face_id", "object_name", "created", "output"))


def normalize_sweep_type(value: Any) -> str:
    key = str(value).strip().replace("-", "_").replace(" ", "_").lower()
    if key not in SWEEP_TYPE_ALIASES:
        raise ValueError(f"unsupported sweep type: {value}")
    return SWEEP_TYPE_ALIASES[key]


def _validate_sweep_inputs(inputs: dict[str, Any], explicit_keys: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        canonical = normalize_sweep_type(inputs.get("sweep_type", inputs.get("type", "Discrete")))
    except ValueError as exc:
        errors.append(str(exc))
        canonical = ""
    if "type" in explicit_keys and "sweep_type" in explicit_keys:
        try:
            alias = normalize_sweep_type(inputs["type"])
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if canonical and alias != canonical:
                errors.append("conflicting sweep_type and type")
    return errors
