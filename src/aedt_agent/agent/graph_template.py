from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aedt_agent.agent.handoff import HandoffSchema


class GraphTemplateError(ValueError):
    """Raised when a graph template is malformed."""


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    role: str
    kind: str
    capability: str = ""
    input_schema: str = ""
    output_schema: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "role": self.role,
            "kind": self.kind,
            "capability": self.capability,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


@dataclass(frozen=True)
class GraphEdge:
    from_node: str
    to_node: str
    on: str
    after: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        payload = {"from": self.from_node, "to": self.to_node, "on": self.on}
        if self.after:
            payload["after"] = self.after
        return payload


@dataclass(frozen=True)
class GraphTemplate:
    template_id: str
    version: int
    description: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    handoffs: dict[str, HandoffSchema]

    def node(self, node_id: str) -> GraphNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"graph template node not found: {node_id}")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "version": self.version,
            "description": self.description,
            "nodes": [node.to_json_dict() for node in self.nodes],
            "edges": [edge.to_json_dict() for edge in self.edges],
            "handoffs": {key: schema.to_json_dict() for key, schema in self.handoffs.items()},
        }


def resolve_template_path(template: str | Path) -> Path:
    path = Path(template)
    if path.exists():
        return path
    root = Path(__file__).resolve().parents[3]
    candidate = root / "docs" / "agent_templates" / f"{template}.yaml"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"graph template not found: {template}")


def load_graph_template(path: str | Path) -> GraphTemplate:
    template_path = resolve_template_path(path)
    data = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GraphTemplateError(f"{template_path} must contain a YAML mapping")

    nodes = [_node_from_mapping(item) for item in _list(data, "nodes")]
    node_ids = {node.node_id for node in nodes}
    if len(node_ids) != len(nodes):
        raise GraphTemplateError("graph template contains duplicate node ids")

    edges = [_edge_from_mapping(item, node_ids) for item in _list(data, "edges")]
    handoffs = _handoffs_from_mapping(data.get("handoffs", {}))

    return GraphTemplate(
        template_id=str(data.get("id") or ""),
        version=int(data.get("version") or 1),
        description=str(data.get("description") or ""),
        nodes=nodes,
        edges=edges,
        handoffs=handoffs,
    )


def _node_from_mapping(value: object) -> GraphNode:
    if not isinstance(value, dict):
        raise GraphTemplateError("graph node must be a mapping")
    node_id = str(value.get("id") or "")
    if not node_id:
        raise GraphTemplateError("graph node id is required")
    return GraphNode(
        node_id=node_id,
        role=str(value.get("role") or ""),
        kind=str(value.get("kind") or ""),
        capability=str(value.get("capability") or ""),
        input_schema=str(value.get("input_schema") or ""),
        output_schema=str(value.get("output_schema") or ""),
    )


def _edge_from_mapping(value: object, node_ids: set[str]) -> GraphEdge:
    if not isinstance(value, dict):
        raise GraphTemplateError("graph edge must be a mapping")
    from_node = str(value.get("from") or "")
    to_node = str(value.get("to") or "")
    if from_node not in node_ids:
        raise GraphTemplateError(f"graph edge references unknown node: {from_node}")
    if to_node not in node_ids:
        raise GraphTemplateError(f"graph edge references unknown node: {to_node}")
    return GraphEdge(from_node=from_node, to_node=to_node, on=str(value.get("on") or ""), after=str(value.get("after") or ""))


def _handoffs_from_mapping(value: object) -> dict[str, HandoffSchema]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise GraphTemplateError("handoffs must be a mapping")
    output: dict[str, HandoffSchema] = {}
    for schema_id, schema_value in value.items():
        if not isinstance(schema_value, dict):
            raise GraphTemplateError(f"handoff schema must be a mapping: {schema_id}")
        required = schema_value.get("required_fields") or []
        if not isinstance(required, list):
            raise GraphTemplateError(f"handoff required_fields must be a list: {schema_id}")
        output[str(schema_id)] = HandoffSchema(str(schema_id), [str(item) for item in required])
    return output


def _list(data: dict[str, Any], key: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise GraphTemplateError(f"graph template {key} must be a list")
    return value
