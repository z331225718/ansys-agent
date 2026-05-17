from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkflowParameter:
    name: str
    type: str = "string"
    default: Any | None = None
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    label: str | None = None
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowParameter":
        return cls(
            name=str(data["name"]),
            type=str(data.get("type", "string")),
            default=data.get("default"),
            unit=data.get("unit"),
            minimum=data.get("minimum"),
            maximum=data.get("maximum"),
            label=data.get("label"),
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "name": self.name,
                "type": self.type,
                "default": self.default,
                "unit": self.unit,
                "minimum": self.minimum,
                "maximum": self.maximum,
                "label": self.label,
                "description": self.description,
            }
        )


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    node_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    label: str | None = None
    position: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowNode":
        return cls(
            id=str(data["id"]),
            node_id=str(data["node_id"]),
            inputs=dict(data.get("inputs", {})),
            label=data.get("label"),
            position=dict(data.get("position", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "id": self.id,
                "node_id": self.node_id,
                "inputs": _json_safe(self.inputs),
                "label": self.label,
                "position": _json_safe(self.position),
            }
        )


@dataclass(frozen=True)
class WorkflowEdge:
    source: str
    target: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowEdge":
        source = data.get("source", data.get("from"))
        target = data.get("target", data.get("to"))
        if source is None or target is None:
            raise ValueError("workflow edge requires source/from and target/to")
        return cls(source=str(source), target=str(target))

    def to_dict(self) -> dict[str, str]:
        return {"from": self.source, "to": self.target}


@dataclass(frozen=True)
class WorkflowOutput:
    name: str
    source: str
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowOutput":
        return cls(name=str(data["name"]), source=str(data["source"]), description=str(data.get("description", "")))

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "source": self.source, "description": self.description}


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    parameters: list[WorkflowParameter] = field(default_factory=list)
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    validation: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[WorkflowOutput] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workflow":
        parameters = data.get("parameters", [])
        if isinstance(parameters, dict):
            parameters = [{"name": key, **(value if isinstance(value, dict) else {"default": value})} for key, value in parameters.items()]
        return cls(
            workflow_id=str(data["workflow_id"]),
            name=str(data["name"]),
            version=str(data.get("version", "0.1.0")),
            description=str(data.get("description", "")),
            parameters=[WorkflowParameter.from_dict(item) for item in parameters],
            nodes=[WorkflowNode.from_dict(item) for item in data.get("nodes", [])],
            edges=[WorkflowEdge.from_dict(item) for item in data.get("edges", [])],
            validation=[dict(item) for item in data.get("validation", [])],
            outputs=[WorkflowOutput.from_dict(item) for item in data.get("outputs", [])],
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_json(cls, text: str) -> "Workflow":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise TypeError("workflow JSON must contain an object")
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: Path) -> "Workflow":
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def from_stage_b_node_plan(cls, workflow_id: str, name: str, node_plan: list[dict[str, Any]]) -> "Workflow":
        nodes: list[WorkflowNode] = []
        edges: list[WorkflowEdge] = []
        for index, item in enumerate(node_plan):
            step_id = str(item.get("id") or item.get("step_id") or f"step_{index + 1}")
            node_id = str(item["node_id"])
            inputs = dict(item.get("inputs", {}))
            nodes.append(WorkflowNode(id=step_id, node_id=node_id, inputs=inputs))
            edges.extend(_edges_from_inputs(step_id, inputs))
        return cls(workflow_id=workflow_id, name=name, nodes=nodes, edges=edges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "validation": _json_safe(self.validation),
            "outputs": [output.to_dict() for output in self.outputs],
            "metadata": _json_safe(self.metadata),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)

    def write_json(self, path: Path) -> None:
        path.write_text(self.to_json() + "\n", encoding="utf-8")

    def node_by_id(self, node_id: str) -> WorkflowNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)


def workflow_parameter_ref(name: str) -> str:
    return f"parameters.{name}"


def workflow_node_ref(node_id: str, output_field: str) -> str:
    return f"{node_id}.output.{output_field}"


def _edges_from_inputs(step_id: str, inputs: dict[str, Any]) -> list[WorkflowEdge]:
    edges: list[WorkflowEdge] = []
    for key, value in _walk_input_refs(inputs):
        edges.append(WorkflowEdge(source=value, target=f"{step_id}.inputs.{key}"))
    return edges


def _walk_input_refs(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            refs.append((prefix.rstrip("."), ref))
        for key, item in value.items():
            refs.extend(_walk_input_refs(item, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            refs.extend(_walk_input_refs(item, f"{prefix}{index}."))
    return refs


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None and value != {}}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
