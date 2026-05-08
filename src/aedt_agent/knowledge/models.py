from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _list_value(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise TypeError(f"{key} must be a list")


def _dict_value(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise TypeError(f"{key} must be a dict")


@dataclass(frozen=True)
class ApiSemantic:
    fqname: str
    domain: str
    category: str
    signature: str
    params: list[dict[str, Any]] = field(default_factory=list)
    returns: dict[str, Any] = field(default_factory=dict)
    docstring: str = ""
    constraints: list[str] = field(default_factory=list)
    common_errors: list[str] = field(default_factory=list)
    common_traps: list[str] = field(default_factory=list)
    examples_ref: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    confidence: str = "inferred"
    pyaedt_version: str = ""
    aedt_version: str = ""
    last_verified_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApiSemantic":
        return cls(
            fqname=str(data["fqname"]),
            domain=str(data.get("domain", "hfss")),
            category=str(data["category"]),
            signature=str(data.get("signature", "")),
            params=_list_value(data, "params"),
            returns=_dict_value(data, "returns"),
            docstring=str(data.get("docstring", "")),
            constraints=[str(item) for item in _list_value(data, "constraints")],
            common_errors=[str(item) for item in _list_value(data, "common_errors")],
            common_traps=[str(item) for item in _list_value(data, "common_traps")],
            examples_ref=[str(item) for item in _list_value(data, "examples_ref")],
            source_refs=[str(item) for item in _list_value(data, "source_refs")],
            confidence=str(data.get("confidence", "inferred")),
            pyaedt_version=str(data.get("pyaedt_version", "")),
            aedt_version=str(data.get("aedt_version", "")),
            last_verified_at=str(data.get("last_verified_at", "")),
        )


@dataclass(frozen=True)
class WorkflowCase:
    case_id: str
    domain: str
    task_type: str
    natural_language_task: str
    workflow_steps: list[str]
    api_used: list[str]
    parameters: dict[str, Any]
    reference_script: str
    validation_script: str
    expected_state: dict[str, Any]
    known_traps: list[str]
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowCase":
        return cls(
            case_id=str(data["case_id"]),
            domain=str(data.get("domain", "hfss")),
            task_type=str(data["task_type"]),
            natural_language_task=str(data["natural_language_task"]),
            workflow_steps=[str(item) for item in _list_value(data, "workflow_steps")],
            api_used=[str(item) for item in _list_value(data, "api_used")],
            parameters=_dict_value(data, "parameters"),
            reference_script=str(data["reference_script"]),
            validation_script=str(data["validation_script"]),
            expected_state=_dict_value(data, "expected_state"),
            known_traps=[str(item) for item in _list_value(data, "known_traps")],
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class CommonTrap:
    trap_id: str
    domain: str
    applies_to: list[str]
    symptom: str
    root_cause: str
    why_silent: str
    detection: str
    prevention: str
    validation_rule: str
    source: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommonTrap":
        return cls(
            trap_id=str(data["trap_id"]),
            domain=str(data.get("domain", "hfss")),
            applies_to=[str(item) for item in _list_value(data, "applies_to")],
            symptom=str(data["symptom"]),
            root_cause=str(data["root_cause"]),
            why_silent=str(data["why_silent"]),
            detection=str(data["detection"]),
            prevention=str(data["prevention"]),
            validation_rule=str(data["validation_rule"]),
            source=str(data["source"]),
        )
