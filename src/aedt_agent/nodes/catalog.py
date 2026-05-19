from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.mcp.node_schemas import NODE_SCHEMAS, NodeInputSchema
from aedt_agent.nodes.metadata import NodeMetadata, NodeStability
from aedt_agent.nodes.registry import NodeRegistry


NODE_CATEGORIES: dict[str, str] = {
    "create_substrate": "geometry",
    "create_conductor_or_geometry_group": "geometry",
    "create_airbox": "geometry",
    "assign_boundary": "boundary",
    "create_port": "port",
    "create_wave_port": "port",
    "select_face": "geometry",
    "create_setup": "setup",
    "create_sweep_or_export": "sweep",
    "solve_setup": "solve",
    "create_sparameter_report": "postprocess",
    "create_farfield_setup": "postprocess",
    "create_antenna_report": "postprocess",
}

NODE_DISPLAY_NAMES: dict[str, str] = {
    "create_substrate": "Create Substrate",
    "create_conductor_or_geometry_group": "Create Geometry Group",
    "create_airbox": "Create Airbox",
    "assign_boundary": "Assign Boundary",
    "create_port": "Create Port",
    "create_wave_port": "Create Wave Port",
    "select_face": "Select Face",
    "create_setup": "Create Setup",
    "create_sweep_or_export": "Create Sweep Or Export",
    "solve_setup": "Solve Setup",
    "create_sparameter_report": "Create S-Parameter Report",
    "create_farfield_setup": "Create Farfield Setup",
    "create_antenna_report": "Create Antenna Report",
}

NODE_POSTCHECKS: dict[str, list[str]] = {
    "create_substrate": ["object_exists", "material_matches"],
    "create_conductor_or_geometry_group": ["objects_exist"],
    "create_airbox": ["air_region_created"],
    "assign_boundary": ["boundary_created"],
    "create_port": ["port_created"],
    "create_wave_port": ["wave_port_created"],
    "select_face": ["face_selected"],
    "create_setup": ["setup_created"],
    "create_sweep_or_export": ["sweep_created"],
    "solve_setup": ["setup_solved"],
    "create_sparameter_report": ["sparameter_report_created"],
    "create_farfield_setup": ["farfield_setup_created"],
    "create_antenna_report": ["antenna_report_created"],
}


class NodeCatalog:
    def __init__(self, metadata: dict[str, NodeMetadata]) -> None:
        self.metadata = dict(metadata)

    @classmethod
    def from_registry(cls, registry: NodeRegistry) -> "NodeCatalog":
        metadata: dict[str, NodeMetadata] = {}
        for definition in registry.list_nodes():
            schema = NODE_SCHEMAS.get(definition.node_id)
            if schema is None:
                continue
            metadata[definition.node_id] = NodeMetadata(
                node_id=definition.node_id,
                display_name=NODE_DISPLAY_NAMES.get(definition.node_id, _title_from_id(definition.node_id)),
                category=NODE_CATEGORIES.get(definition.node_id, "utility"),
                description=definition.summary,
                input_schema=_input_schema_to_json_schema(schema),
                output_schema=_output_schema(definition.outputs),
                required_capabilities=list(definition.allowed_apis),
                version="0.1.0",
                stability=NodeStability.CANDIDATE,
                ui_hints=_ui_hints(definition.node_id),
                postchecks=NODE_POSTCHECKS.get(definition.node_id, []),
            )
        return cls(metadata)

    @classmethod
    def from_directory(cls, directory: Path) -> "NodeCatalog":
        return cls.from_registry(NodeRegistry.from_directory(directory))

    def get(self, node_id: str) -> NodeMetadata:
        return self.metadata[node_id]

    def list_metadata(self) -> list[NodeMetadata]:
        return [self.metadata[node_id] for node_id in sorted(self.metadata)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "0.1.0",
            "nodes": [metadata.to_dict() for metadata in self.list_metadata()],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


def load_node_catalog(directory: Path = Path("nodes/catalog")) -> NodeCatalog:
    return NodeCatalog.from_directory(directory)


def write_node_catalog_json(directory: Path, output_path: Path) -> None:
    output_path.write_text(NodeCatalog.from_directory(directory).to_json() + "\n", encoding="utf-8")


def _input_schema_to_json_schema(schema: NodeInputSchema) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for key, expected in {**schema.required, **schema.optional}.items():
        properties[key] = {"type": _json_type(expected)}
    for key, value in schema.defaults.items():
        properties.setdefault(key, {"type": _json_type(type(value))})
        properties[key]["default"] = value
    return {
        "type": "object",
        "required": sorted(schema.required),
        "properties": properties,
        "additionalProperties": False,
    }


def _json_type(expected: type | tuple[type, ...]) -> str | list[str]:
    if isinstance(expected, tuple):
        return sorted({_json_type(item) for item in expected if isinstance(_json_type(item), str)})
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return mapping.get(expected, "string")


def _output_schema(outputs: dict[str, Any]) -> dict[str, Any]:
    creates = outputs.get("creates", [])
    if not isinstance(creates, list):
        creates = []
    properties: dict[str, Any] = {
        "created": {
            "type": "object",
            "description": "Created AEDT objects grouped by kind.",
        },
        "postcheck": {
            "type": "object",
            "description": "Node-local postcheck result.",
        },
    }
    for created_type in creates:
        field_name = _created_type_to_field(str(created_type))
        properties[field_name] = {"type": "string"}
    return {"type": "object", "properties": properties}


def _created_type_to_field(created_type: str) -> str:
    if created_type in {"substrate", "conductors", "air_region"}:
        return "object_name"
    if created_type == "port":
        return "port_name"
    if created_type == "boundary":
        return "boundary_name"
    if created_type == "setup":
        return "setup_name"
    if created_type == "sweep_or_report":
        return "sweep_name"
    if created_type == "farfield":
        return "farfield_name"
    if created_type in {"report", "antenna_report"}:
        return "report_name"
    if created_type == "face_id":
        return "selected_face_id"
    return f"{created_type}_name"


def _ui_hints(node_id: str) -> dict[str, Any]:
    return {
        "icon": _icon_for_node(node_id),
        "color": _color_for_category(NODE_CATEGORIES.get(node_id, "utility")),
        "draggable": True,
    }


def _icon_for_node(node_id: str) -> str:
    if "port" in node_id:
        return "plug"
    if "boundary" in node_id:
        return "shield"
    if "setup" in node_id:
        return "settings"
    if "sweep" in node_id:
        return "chart-line"
    if "face" in node_id:
        return "mouse-pointer-square"
    return "box"


def _color_for_category(category: str) -> str:
    colors = {
        "geometry": "#2563eb",
        "material": "#16a34a",
        "boundary": "#dc2626",
        "port": "#9333ea",
        "setup": "#ca8a04",
        "sweep": "#0891b2",
        "report/export": "#4b5563",
        "validation": "#0f766e",
    }
    return colors.get(category, "#64748b")


def _title_from_id(node_id: str) -> str:
    return " ".join(part.capitalize() for part in node_id.split("_"))
