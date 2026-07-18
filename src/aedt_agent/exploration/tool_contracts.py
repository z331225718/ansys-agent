from __future__ import annotations

from typing import Any, Literal

try:
    from typing_extensions import TypedDict
except ImportError:  # pragma: no cover - FastMCP installs typing_extensions.
    from typing import TypedDict


class ApiEvidenceInput(TypedDict):
    package: Literal["pyaedt", "pyedb"]
    package_version: str
    project: str
    symbol: str
    source_path: str
    snippet_digest: str
    query_id: str


class OperationTargetInput(TypedDict):
    product: Literal["desktop", "hfss", "hfss3dlayout"]
    project_name: str
    design_name: str


class ReadAttrStepInput(TypedDict):
    id: str
    op: Literal["read_attr"]
    path: str


class CallStepInput(TypedDict):
    id: str
    op: Literal["call"]
    path: str
    args: list[Any]
    kwargs: dict[str, Any]


class SetAttrStepInput(TypedDict):
    id: str
    op: Literal["set_attr"]
    path: str
    value: Any


class ReadbackCheckInput(TypedDict):
    id: str
    path: str
    operator: Literal["equals", "not_equals", "contains", "truthy"]
    expected: Any


class AnsysOperationPlanInput(TypedDict):
    schema_version: Literal["ansys-operation-plan/v1"]
    intent: str
    target: OperationTargetInput
    risk: Literal["read_only", "reversible_edit"]
    evidence: list[ApiEvidenceInput]
    steps: list[ReadAttrStepInput | CallStepInput | SetAttrStepInput]
    readback: list[ReadbackCheckInput]
    rollback: list[str]


def operation_plan_schema() -> dict[str, Any]:
    step_common = {
        "id": {"type": "string"},
        "path": {"type": "string", "description": "Public dotted path rooted at the bound PyAEDT app."},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ansys-operation-plan/v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "intent",
            "target",
            "risk",
            "evidence",
            "steps",
            "readback",
            "rollback",
        ],
        "properties": {
            "schema_version": {"const": "ansys-operation-plan/v1"},
            "intent": {"type": "string"},
            "target": {
                "type": "object",
                "additionalProperties": False,
                "required": ["product", "project_name", "design_name"],
                "properties": {
                    "product": {"enum": ["desktop", "hfss", "hfss3dlayout"]},
                    "project_name": {"type": "string"},
                    "design_name": {"type": "string"},
                },
            },
            "risk": {"enum": ["read_only", "reversible_edit"]},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "package",
                        "package_version",
                        "project",
                        "symbol",
                        "source_path",
                        "snippet_digest",
                        "query_id",
                    ],
                    "properties": {
                        "package": {"enum": ["pyaedt", "pyedb"]},
                        "package_version": {"type": "string"},
                        "project": {"type": "string"},
                        "symbol": {"type": "string"},
                        "source_path": {"type": "string"},
                        "snippet_digest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                        "query_id": {"type": "string", "pattern": "^query-"},
                    },
                },
            },
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "oneOf": [
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "op", "path"],
                            "properties": {**step_common, "op": {"const": "read_attr"}},
                        },
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "op", "path", "args", "kwargs"],
                            "properties": {
                                **step_common,
                                "op": {"const": "call"},
                                "args": {"type": "array"},
                                "kwargs": {"type": "object"},
                            },
                        },
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "op", "path", "value"],
                            "properties": {
                                **step_common,
                                "op": {"const": "set_attr"},
                                "value": {},
                            },
                        },
                    ]
                },
            },
            "readback": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "path", "operator", "expected"],
                    "properties": {
                        "id": {"type": "string"},
                        "path": {"type": "string"},
                        "operator": {"enum": ["equals", "not_equals", "contains", "truthy"]},
                        "expected": {},
                    },
                },
            },
            "rollback": {
                "type": "array",
                "items": {"type": "string", "description": "Mutation step id restored from server snapshot."},
            },
        },
        "rules": [
            "read_only plans require empty readback and rollback arrays",
            "reversible_edit plans require exact-path readback and every mutation step id in rollback",
            "copy operation_evidence from inspect_ansys_symbol without renaming fields",
        ],
        "example_read_only": {
            "schema_version": "ansys-operation-plan/v1",
            "intent": "Read one evidenced public property",
            "target": {
                "product": "hfss3dlayout",
                "project_name": "ProjectName",
                "design_name": "DesignName",
            },
            "risk": "read_only",
            "evidence": ["COPY inspect_ansys_symbol.operation_evidence OBJECT HERE"],
            "steps": [{"id": "read-property", "op": "read_attr", "path": "modeler.lines.line1.width"}],
            "readback": [],
            "rollback": [],
        },
    }
