from __future__ import annotations

from dataclasses import dataclass, field
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
    join: str = "any"
    after: list[str] = field(default_factory=list)
    max_runs: int = 1
    handler: str = ""
    on_failure: str = "fail"
    retry_max_attempts: int = 1
    retry_backoff: str = "constant"
    retry_delay_seconds: float = 0.0
    fan_out: bool = False
    expand: bool = False
    # ── Agent v2 ──
    system_prompt: str = ""
    model: str = ""
    profile: str = "standard"
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        result = {
            "id": self.node_id,
            "role": self.role,
            "kind": self.kind,
            "capability": self.capability,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "join": self.join,
            "after": list(self.after),
            "max_runs": self.max_runs,
            "handler": self.handler,
            "on_failure": self.on_failure,
            "retry_max_attempts": self.retry_max_attempts,
            "retry_backoff": self.retry_backoff,
            "retry_delay_seconds": self.retry_delay_seconds,
            "fan_out": self.fan_out,
            "expand": self.expand,
        }
        if self.system_prompt:
            result["system_prompt"] = self.system_prompt
        if self.model:
            result["model"] = self.model
        if self.profile != "standard":
            result["profile"] = self.profile
        if self.constraints:
            result["constraints"] = dict(self.constraints)
        return result


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    from_node: str
    to_node: str
    on: str
    after: list[str] = field(default_factory=list)
    max_traversals: int = 1
    if_condition: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.edge_id,
            "from": self.from_node,
            "to": self.to_node,
            "on": self.on,
            "max_traversals": self.max_traversals,
        }
        if self.after:
            payload["after"] = list(self.after)
        if self.if_condition:
            payload["if"] = self.if_condition
        return payload


@dataclass(frozen=True)
class GraphTemplate:
    template_id: str
    version: int
    description: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    handoffs: dict[str, HandoffSchema]
    max_rounds: int = 0  # 0 = unlimited
    # ── Agent v2 layers ──
    prompts: dict[str, str] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)

    def node(self, node_id: str) -> GraphNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"graph template node not found: {node_id}")

    def to_json_dict(self) -> dict[str, Any]:
        result = {
            "template_id": self.template_id,
            "version": self.version,
            "description": self.description,
            "nodes": [node.to_json_dict() for node in self.nodes],
            "edges": [edge.to_json_dict() for edge in self.edges],
            "handoffs": {key: schema.to_json_dict() for key, schema in self.handoffs.items()},
        }
        if self.max_rounds:
            result["max_rounds"] = self.max_rounds
        if self.prompts:
            result["prompts"] = dict(self.prompts)
        if self.profiles:
            result["profiles"] = {k: dict(v) for k, v in self.profiles.items()}
        if self.environment:
            result["environment"] = dict(self.environment)
        if self.security:
            result["security"] = dict(self.security)
        return result


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
    return graph_template_from_mapping(data, source=str(template_path))


def graph_template_from_mapping(data: object, *, source: str = "graph template") -> GraphTemplate:
    if not isinstance(data, dict):
        raise GraphTemplateError(f"{source} must contain a YAML mapping")

    nodes = [_node_from_mapping(item) for item in _list(data, "nodes")]
    node_ids = {node.node_id for node in nodes}
    if len(node_ids) != len(nodes):
        raise GraphTemplateError("graph template contains duplicate node ids")

    raw_edges = _list(data, "edges")
    edges = [_edge_from_mapping(item, node_ids, index) for index, item in enumerate(raw_edges)]
    edge_ids = {edge.edge_id for edge in edges}
    if len(edge_ids) != len(edges):
        raise GraphTemplateError("graph template contains duplicate edge ids")
    handoffs = _handoffs_from_mapping(data.get("handoffs", {}))
    _validate_nodes(nodes, node_ids, handoffs)
    _validate_cycles(nodes, edges, raw_edges)

    return GraphTemplate(
        template_id=str(data.get("id") or data.get("template_id") or ""),
        version=int(data.get("version") or 1),
        description=str(data.get("description") or ""),
        nodes=nodes,
        edges=edges,
        handoffs=handoffs,
        max_rounds=int(data.get("max_rounds") or 0),
        prompts=_string_dict(data.get("prompts")),
        profiles=_profiles_from_mapping(data.get("profiles")),
        environment=_dict_or_empty(data.get("environment")),
        security=_dict_or_empty(data.get("security")),
    )


def _node_from_mapping(value: object) -> GraphNode:
    if not isinstance(value, dict):
        raise GraphTemplateError("graph node must be a mapping")
    node_id = str(value.get("id") or "")
    if not node_id:
        raise GraphTemplateError("graph node id is required")
    after = _string_list(value.get("after"), field_name=f"node {node_id} after")
    max_runs = _positive_int(value.get("max_runs", 1), field_name=f"node {node_id} max_runs")
    on_failure = _on_failure(value.get("on_failure"), node_id)
    retry_max_attempts = _positive_int(value.get("retry_max_attempts", 1), field_name=f"node {node_id} retry_max_attempts")
    retry_backoff = _backoff(value.get("retry_backoff"), node_id)
    retry_delay_seconds = _non_negative_float(value.get("retry_delay_seconds", 0.0), field_name=f"node {node_id} retry_delay_seconds")
    fan_out = bool(value.get("fan_out", False))
    expand = bool(value.get("expand", False))
    system_prompt = str(value.get("system_prompt") or "")
    model = str(value.get("model") or "")
    profile = str(value.get("profile") or "standard")
    constraints = dict(value.get("constraints") or {}) if isinstance(value.get("constraints"), dict) else {}
    return GraphNode(
        node_id=node_id,
        role=str(value.get("role") or ""),
        kind=str(value.get("kind") or ""),
        capability=str(value.get("capability") or ""),
        input_schema=str(value.get("input_schema") or ""),
        output_schema=str(value.get("output_schema") or ""),
        join=str(value.get("join") or "any"),
        after=after,
        max_runs=max_runs,
        handler=str(value.get("handler") or ""),
        on_failure=on_failure,
        retry_max_attempts=retry_max_attempts,
        retry_backoff=retry_backoff,
        retry_delay_seconds=retry_delay_seconds,
        fan_out=fan_out,
        expand=expand,
        system_prompt=system_prompt,
        model=model,
        profile=profile,
        constraints=constraints,
    )


def _edge_from_mapping(value: object, node_ids: set[str], index: int) -> GraphEdge:
    if not isinstance(value, dict):
        raise GraphTemplateError("graph edge must be a mapping")
    from_node = str(value.get("from") or "")
    to_node = str(value.get("to") or "")
    if from_node not in node_ids:
        raise GraphTemplateError(f"graph edge references unknown node: {from_node}")
    if to_node not in node_ids:
        raise GraphTemplateError(f"graph edge references unknown node: {to_node}")
    outcome = str(value.get("on", value.get(True)) or "")
    edge_id = str(value.get("id") or f"{index}:{from_node}:{to_node}:{outcome}")
    return GraphEdge(
        edge_id=edge_id,
        from_node=from_node,
        to_node=to_node,
        on=outcome,
        after=_string_list(value.get("after"), field_name=f"edge {edge_id} after"),
        max_traversals=_positive_int(
            value.get("max_traversals", 1),
            field_name=f"edge {edge_id} max_traversals",
        ),
        if_condition=str(value.get("if") or ""),
    )


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


def _string_list(value: object, *, field_name: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise GraphTemplateError(f"{field_name} must be a string or list")
    return [str(item) for item in value]


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise GraphTemplateError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GraphTemplateError(f"{field_name} must be a positive integer") from exc
    if parsed <= 0:
        raise GraphTemplateError(f"{field_name} must be a positive integer")
    return parsed


def _non_negative_float(value: object, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise GraphTemplateError(f"{field_name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise GraphTemplateError(f"{field_name} must be a number") from exc
    if parsed < 0:
        raise GraphTemplateError(f"{field_name} must be non-negative")
    return parsed


def _on_failure(value: object, node_id: str) -> str:
    if value in (None, ""):
        return "fail"
    raw = str(value).strip()
    valid = {"fail", "skip", "retry"}
    if raw in valid:
        return raw
    if raw.startswith("fallback:"):
        fallback_target = raw[len("fallback:"):].strip()
        if not fallback_target:
            raise GraphTemplateError(f"node {node_id} on_failure fallback target is empty")
        return raw
    raise GraphTemplateError(
        f"node {node_id} on_failure must be fail|skip|retry|fallback:<node_id>, got: {raw}"
    )


def _backoff(value: object, node_id: str) -> str:
    if value in (None, ""):
        return "constant"
    raw = str(value).strip()
    valid = {"constant", "linear", "exponential"}
    if raw not in valid:
        raise GraphTemplateError(
            f"node {node_id} retry_backoff must be constant|linear|exponential, got: {raw}"
        )
    return raw


def _dict_or_empty(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _profiles_from_mapping(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for k, v in value.items():
        if isinstance(v, dict):
            result[str(k)] = dict(v)
    return result


def _validate_nodes(
    nodes: list[GraphNode],
    node_ids: set[str],
    handoffs: dict[str, HandoffSchema],
) -> None:
    builtin_roles = {"planner", "validator", "scorecard", "approval_gate"}
    supported_kinds = {"llm", "program", "worker", "human_gate", "agent"}
    for node in nodes:
        if node.kind not in supported_kinds:
            raise GraphTemplateError(f"graph node has unsupported kind: {node.node_id} ({node.kind})")
        if node.join not in {"any", "all"}:
            raise GraphTemplateError(f"graph node has unsupported join: {node.node_id} ({node.join})")
        for dependency in node.after:
            if dependency not in node_ids:
                raise GraphTemplateError(
                    f"graph node after references unknown node: {node.node_id} -> {dependency}"
                )
        if node.kind == "worker" and not node.capability:
            raise GraphTemplateError(f"worker node capability is required: {node.node_id}")
        if node.kind in {"program", "llm"} and node.role not in builtin_roles and not node.handler:
            raise GraphTemplateError(f"graph node handler is required: {node.node_id}")
        if node.kind == "agent" and not node.system_prompt:
            raise GraphTemplateError(f"agent node system_prompt is required: {node.node_id}")
        if node.on_failure.startswith("fallback:"):
            fallback_target = node.on_failure[len("fallback:"):]
            if fallback_target not in node_ids:
                raise GraphTemplateError(
                    f"graph node on_failure fallback references unknown node: {node.node_id} -> {fallback_target}"
                )
        if node.retry_max_attempts > 1 and node.on_failure != "retry":
            raise GraphTemplateError(
                f"graph node retry_max_attempts requires on_failure=retry: {node.node_id}"
            )
        if node.fan_out and node.expand:
            raise GraphTemplateError(
                f"graph node cannot have both fan_out and expand: {node.node_id}"
            )
        if node.fan_out and (
            node.on_failure in {"skip", "retry"} or node.on_failure.startswith("fallback")
        ):
            raise GraphTemplateError(
                f"graph node fan_out is incompatible with on_failure={node.on_failure}: {node.node_id}"
            )
        for schema_id in (node.input_schema, node.output_schema):
            if schema_id and schema_id not in handoffs:
                raise GraphTemplateError(
                    f"graph node references unknown handoff schema: {node.node_id} ({schema_id})"
                )


def _validate_cycles(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    raw_edges: list[object],
) -> None:
    adjacency: dict[str, list[tuple[str, int]]] = {node.node_id: [] for node in nodes}
    for index, edge in enumerate(edges):
        adjacency[edge.from_node].append((edge.to_node, index))
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str) -> None:
        visited.add(node_id)
        active.add(node_id)
        for target, edge_index in adjacency[node_id]:
            if target not in visited:
                visit(target)
            elif target in active:
                raw_edge = raw_edges[edge_index]
                explicit_limit = isinstance(raw_edge, dict) and "max_traversals" in raw_edge
                if not explicit_limit:
                    edge = edges[edge_index]
                    raise GraphTemplateError(
                        f"graph cycle edge requires explicit max_traversals: {edge.edge_id}"
                    )
        active.remove(node_id)

    for node in nodes:
        if node.node_id not in visited:
            visit(node.node_id)
