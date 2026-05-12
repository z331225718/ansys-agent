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
class BenchmarkTask:
    task_id: str
    level: str
    domain: str
    requirement: str
    allowed_nodes: list[str] = field(default_factory=list)
    expected_workflow: list[str] = field(default_factory=list)
    required_api_categories: list[str] = field(default_factory=list)
    reference_script: str = ""
    validation_script: str = ""
    expected_outputs: list[str] = field(default_factory=list)
    known_failure_modes: list[str] = field(default_factory=list)
    grading: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkTask":
        return cls(
            task_id=str(data["task_id"]),
            level=str(data["level"]),
            domain=str(data.get("domain", "hfss")),
            requirement=str(data["requirement"]),
            allowed_nodes=_list_of_strings(data, "allowed_nodes"),
            expected_workflow=_list_of_strings(data, "expected_workflow"),
            required_api_categories=_list_of_strings(data, "required_api_categories"),
            reference_script=str(data.get("reference_script", "")),
            validation_script=str(data.get("validation_script", "")),
            expected_outputs=_list_of_strings(data, "expected_outputs"),
            known_failure_modes=_list_of_strings(data, "known_failure_modes"),
            grading=_mapping(data, "grading"),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "BenchmarkTask":
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise TypeError(f"{path} must contain a mapping")
        return cls.from_dict(data)


def load_tasks(directory: Path) -> list[BenchmarkTask]:
    return [BenchmarkTask.from_yaml(path) for path in sorted(directory.glob("*.yaml"))]
