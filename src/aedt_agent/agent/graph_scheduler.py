from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aedt_agent.agent.graph_template import GraphNode, GraphTemplate
from aedt_agent.agent.mission import GraphHandoffRecord, NodeRunRecord, NodeRunStatus


_ACTIVE_NODE_STATUSES = {
    NodeRunStatus.CREATED,
    NodeRunStatus.RUNNING,
    NodeRunStatus.WAITING_APPROVAL,
}
_TERMINAL_NODE_STATUSES = {
    NodeRunStatus.SUCCEEDED,
    NodeRunStatus.FAILED,
    NodeRunStatus.SKIPPED,
}


@dataclass(frozen=True)
class ReadyNode:
    node: GraphNode
    input_payload: dict[str, Any]
    handoff_ids: list[str]
    run_index: int


def ready_nodes(
    template: GraphTemplate,
    node_runs: list[NodeRunRecord],
    pending_handoffs: list[GraphHandoffRecord],
    *,
    initial_payload: dict[str, Any] | None = None,
) -> list[ReadyNode]:
    runs_by_node: dict[str, list[NodeRunRecord]] = {}
    for node_run in node_runs:
        runs_by_node.setdefault(node_run.node_id, []).append(node_run)
    handoffs_by_target: dict[str, list[GraphHandoffRecord]] = {}
    for handoff in pending_handoffs:
        handoffs_by_target.setdefault(handoff.to_node, []).append(handoff)
    edges_by_target = {
        node.node_id: [edge for edge in template.edges if edge.to_node == node.node_id]
        for node in template.nodes
    }
    edge_by_id = {edge.edge_id: edge for edge in template.edges}
    ready: list[ReadyNode] = []

    for node in template.nodes:
        runs = sorted(runs_by_node.get(node.node_id, []), key=lambda item: item.sequence)
        if len(runs) >= node.max_runs:
            continue
        if any(run.status in _ACTIVE_NODE_STATUSES for run in runs):
            continue
        incoming = edges_by_target[node.node_id]
        pending = handoffs_by_target.get(node.node_id, [])
        if not incoming:
            selected: list[GraphHandoffRecord] = []
        elif node.join == "all":
            required_sources = {edge.from_node for edge in incoming}
            arrived_sources = {handoff.from_node for handoff in pending}
            if not required_sources.issubset(arrived_sources):
                continue
            selected = list(pending)
        else:
            if not pending:
                continue
            selected = list(pending)

        dependencies = set(node.after)
        for handoff in selected:
            edge = edge_by_id.get(handoff.edge_id)
            if edge is not None:
                dependencies.update(edge.after)
        if not _dependencies_satisfied(dependencies, runs_by_node):
            continue

        payload = (
            dict(initial_payload or {})
            if not incoming
            else merge_handoff_payloads(selected)
        )
        ready.append(
            ReadyNode(
                node=node,
                input_payload=payload,
                handoff_ids=[handoff.handoff_id for handoff in selected],
                run_index=len(runs) + 1,
            )
        )
    return ready


def merge_handoff_payloads(handoffs: list[GraphHandoffRecord]) -> dict[str, Any]:
    provenance: dict[str, dict[str, Any]] = {}
    values_by_key: dict[str, list[Any]] = {}
    presence_by_key: dict[str, int] = {}
    for handoff in handoffs:
        provenance_key = handoff.from_node
        suffix = 2
        while provenance_key in provenance:
            provenance_key = f"{handoff.from_node}#{suffix}"
            suffix += 1
        provenance[provenance_key] = dict(handoff.payload)
        for key, value in handoff.payload.items():
            if key == "_handoffs":
                continue
            values_by_key.setdefault(key, []).append(value)
            presence_by_key[key] = presence_by_key.get(key, 0) + 1

    merged: dict[str, Any] = {"_handoffs": provenance}
    for key, values in values_by_key.items():
        if presence_by_key[key] == 1 or all(value == values[0] for value in values[1:]):
            merged[key] = values[0]
    return merged


def _dependencies_satisfied(
    dependencies: set[str],
    runs_by_node: dict[str, list[NodeRunRecord]],
) -> bool:
    for node_id in dependencies:
        runs = runs_by_node.get(node_id, [])
        if not runs:
            return False
        latest = max(runs, key=lambda item: item.sequence)
        if latest.status not in _TERMINAL_NODE_STATUSES:
            return False
    return True
