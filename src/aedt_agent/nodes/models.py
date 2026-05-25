from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _list_of_strings(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return [str(item) for item in value]


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be a mapping")
    return dict(value)


@dataclass(frozen=True)
class NodeDefinition:
    node_id: str
    summary: str
    allowed_apis: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    prerequisites: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    status: str = "experimental"
    track: str = "hfss-core"

    @property
    def api_whitelist(self) -> list[str]:
        return list(self.allowed_apis)

    @property
    def is_experimental(self) -> bool:
        return self.status == "experimental"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeDefinition":
        status = str(data.get("status", "experimental"))
        track = str(data.get("track", "hfss-core"))
        if status not in {"experimental", "candidate", "stable", "deprecated"}:
            raise ValueError(f"invalid node status: {status}")
        if track not in {"hfss-core", "hfss-demo", "layout-brd", "postprocess"}:
            raise ValueError(f"invalid node track: {track}")
        return cls(
            node_id=str(data["node_id"]),
            summary=str(data["summary"]),
            allowed_apis=_list_of_strings(data, "allowed_apis"),
            inputs=_mapping(data, "inputs"),
            outputs=_mapping(data, "outputs"),
            prerequisites=_list_of_strings(data, "prerequisites"),
            examples=_list_of_strings(data, "examples"),
            status=status,
            track=track,
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "NodeDefinition":
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise TypeError(f"{path} must contain a mapping")
        return cls.from_dict(data)
