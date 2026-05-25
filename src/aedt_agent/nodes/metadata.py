from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeStability(str, Enum):
    EXPERIMENTAL = "experimental"
    CANDIDATE = "candidate"
    STABLE = "stable"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class NodeMetadata:
    node_id: str
    display_name: str
    category: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_capabilities: list[str] = field(default_factory=list)
    version: str = "0.1.0"
    stability: NodeStability = NodeStability.CANDIDATE
    track: str = "hfss-core"
    ui_hints: dict[str, Any] = field(default_factory=dict)
    postchecks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "category": self.category,
            "description": self.description,
            "input_schema": _json_safe(self.input_schema),
            "output_schema": _json_safe(self.output_schema),
            "required_capabilities": list(self.required_capabilities),
            "version": self.version,
            "status": self.stability.value,
            "stability": self.stability.value,
            "track": self.track,
            "ui_hints": _json_safe(self.ui_hints),
            "postchecks": list(self.postchecks),
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, type):
        return value.__name__
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
