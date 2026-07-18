from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


class ExplorationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _exact(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExplorationError("invalid_plan", f"{name} must be an object")
    if set(value) != fields:
        raise ExplorationError("invalid_plan", f"{name} fields must be exactly {sorted(fields)}")
    return value


def _text(value: Any, name: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorationError("invalid_plan", f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ExplorationError("plan_too_large", f"{name} must contain at most {maximum} characters")
    return value


@dataclass(frozen=True)
class ApiEvidence:
    package: str
    package_version: str
    project: str
    symbol: str
    source_path: str
    snippet_digest: str
    query_id: str

    @classmethod
    def from_dict(cls, value: Any) -> "ApiEvidence":
        data = _exact(
            value,
            {"package", "package_version", "project", "symbol", "source_path", "snippet_digest", "query_id"},
            "evidence item",
        )
        digest = _text(data["snippet_digest"], "snippet_digest", 64)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise ExplorationError("invalid_evidence", "snippet_digest must be a SHA-256 hex digest")
        package = _text(data["package"], "package", 20)
        if package not in {"pyaedt", "pyedb"}:
            raise ExplorationError("invalid_evidence", "evidence package must be pyaedt or pyedb")
        query_id = _text(data["query_id"], "query_id", 80)
        if not query_id.startswith("query-"):
            raise ExplorationError("invalid_evidence", "query_id must come from Ansys API Memory")
        return cls(
            package,
            _text(data["package_version"], "package_version", 40),
            _text(data["project"], "project", 200),
            _text(data["symbol"], "symbol", 500),
            _text(data["source_path"], "source_path", 1000),
            digest.lower(),
            query_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class OperationTarget:
    product: str
    project_name: str
    design_name: str

    @classmethod
    def from_dict(cls, value: Any) -> "OperationTarget":
        data = _exact(value, {"product", "project_name", "design_name"}, "target")
        product = _text(data["product"], "product", 40).lower()
        if product not in {"desktop", "hfss", "hfss3dlayout"}:
            raise ExplorationError("invalid_target", "product must be desktop, hfss, or hfss3dlayout")
        return cls(
            product,
            _text(data["project_name"], "project_name", 200),
            _text(data["design_name"], "design_name", 200),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class OperationStep:
    id: str
    op: str
    path: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None
    value: Any = None

    @classmethod
    def from_dict(cls, value: Any, *, name: str = "step") -> "OperationStep":
        if not isinstance(value, dict):
            raise ExplorationError("invalid_plan", f"{name} must be an object")
        op = value.get("op")
        expected = {
            "read_attr": {"id", "op", "path"},
            "call": {"id", "op", "path", "args", "kwargs"},
            "set_attr": {"id", "op", "path", "value"},
        }.get(op)
        if expected is None:
            raise ExplorationError("operation_forbidden", f"unsupported operation: {op}")
        data = _exact(value, expected, name)
        args: tuple[Any, ...] = ()
        kwargs: dict[str, Any] | None = None
        step_value = None
        if op == "call":
            if not isinstance(data["args"], list) or not isinstance(data["kwargs"], dict):
                raise ExplorationError("invalid_plan", f"{name} call args/kwargs must be JSON array/object")
            _json_value(data["args"], f"{name}.args")
            _json_value(data["kwargs"], f"{name}.kwargs")
            args = tuple(data["args"])
            kwargs = dict(data["kwargs"])
        elif op == "set_attr":
            _json_value(data["value"], f"{name}.value")
            step_value = data["value"]
        return cls(
            _text(data["id"], f"{name}.id", 80),
            op,
            _text(data["path"], f"{name}.path", 500),
            args,
            kwargs,
            step_value,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {"id": self.id, "op": self.op, "path": self.path}
        if self.op == "call":
            result.update({"args": list(self.args), "kwargs": dict(self.kwargs or {})})
        elif self.op == "set_attr":
            result["value"] = self.value
        return result


@dataclass(frozen=True)
class ReadbackCheck:
    id: str
    path: str
    operator: str
    expected: Any

    @classmethod
    def from_dict(cls, value: Any) -> "ReadbackCheck":
        data = _exact(value, {"id", "path", "operator", "expected"}, "readback item")
        operator = _text(data["operator"], "readback.operator", 30)
        if operator not in {"equals", "not_equals", "contains", "truthy"}:
            raise ExplorationError("invalid_plan", "readback operator is unsupported")
        _json_value(data["expected"], "readback.expected")
        return cls(
            _text(data["id"], "readback.id", 80),
            _text(data["path"], "readback.path", 500),
            operator,
            data["expected"],
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class OperationPlan:
    schema_version: str
    intent: str
    target: OperationTarget
    risk: str
    evidence: tuple[ApiEvidence, ...]
    steps: tuple[OperationStep, ...]
    readback: tuple[ReadbackCheck, ...]
    rollback: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "OperationPlan":
        try:
            size = len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ExplorationError("invalid_plan", "operation plan must be JSON-compatible") from exc
        if size > 64 * 1024:
            raise ExplorationError("plan_too_large", "operation plan exceeds 64 KiB")
        data = _exact(
            value,
            {"schema_version", "intent", "target", "risk", "evidence", "steps", "readback", "rollback"},
            "operation plan",
        )
        if data["schema_version"] != "ansys-operation-plan/v1":
            raise ExplorationError("invalid_plan", "unsupported operation plan schema_version")
        risk = _text(data["risk"], "risk", 40)
        if risk not in {"read_only", "reversible_edit"}:
            raise ExplorationError("risk_forbidden", "only read_only and reversible_edit exploration are allowed")
        for field_name in ("evidence", "steps", "readback", "rollback"):
            if not isinstance(data[field_name], list):
                raise ExplorationError("invalid_plan", f"{field_name} must be an array")
        if not data["evidence"]:
            raise ExplorationError("evidence_required", "versioned API Memory evidence is required")
        if not data["steps"]:
            raise ExplorationError("invalid_plan", "at least one operation step is required")
        if len(data["steps"]) + len(data["readback"]) > 32:
            raise ExplorationError("plan_too_large", "operation plan supports at most 32 steps and checks")
        rollback = tuple(_text(item, "rollback step id", 80) for item in data["rollback"])
        return cls(
            data["schema_version"],
            _text(data["intent"], "intent", 1000),
            OperationTarget.from_dict(data["target"]),
            risk,
            tuple(ApiEvidence.from_dict(item) for item in data["evidence"]),
            tuple(OperationStep.from_dict(item, name=f"steps[{index}]") for index, item in enumerate(data["steps"])),
            tuple(ReadbackCheck.from_dict(item) for item in data["readback"]),
            rollback,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "intent": self.intent,
            "target": self.target.to_dict(),
            "risk": self.risk,
            "evidence": [item.to_dict() for item in self.evidence],
            "steps": [item.to_dict() for item in self.steps],
            "readback": [item.to_dict() for item in self.readback],
            "rollback": list(self.rollback),
        }


def _json_value(value: Any, name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExplorationError("invalid_plan", f"{name} must be JSON-compatible") from exc
