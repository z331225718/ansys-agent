from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aedt_agent.nodes.models import NodeDefinition


@dataclass
class NodeRegistry:
    nodes: dict[str, NodeDefinition] = field(default_factory=dict)

    @classmethod
    def from_directory(cls, directory: Path) -> "NodeRegistry":
        nodes = {}
        for path in sorted(directory.glob("*.yaml")):
            node = NodeDefinition.from_yaml(path)
            nodes[node.node_id] = node
        return cls(nodes=nodes)

    def get(self, node_id: str) -> NodeDefinition:
        return self.nodes[node_id]

    def list_nodes(self) -> list[NodeDefinition]:
        return [self.nodes[node_id] for node_id in sorted(self.nodes)]

    def api_whitelist(self, node_ids: list[str] | None = None) -> list[str]:
        if node_ids is None:
            selected = self.list_nodes()
        else:
            selected = [self.get(node_id) for node_id in node_ids]
        seen: set[str] = set()
        ordered: list[str] = []
        for node in selected:
            for api in node.allowed_apis:
                if api not in seen:
                    seen.add(api)
                    ordered.append(api)
        return ordered
