from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


JsonDict = dict[str, Any]


class CapabilityRisk(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE_EDIT = "reversible_edit"
    DESTRUCTIVE_EDIT = "destructive_edit"
    EXPENSIVE = "expensive"


class RouteKind(StrEnum):
    WORKFLOW = "workflow"
    CAPABILITY = "capability"
    CODE_FALLBACK = "code_fallback"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    risk: CapabilityRisk
    input_schema: JsonDict
    output_schema: JsonDict
    postconditions: tuple[str, ...] = ()
    version: str = "1"

    def validate(self) -> "CapabilitySpec":
        if not self.name.strip():
            raise ValueError("capability name is required")
        if self.input_schema.get("type") != "object":
            raise ValueError(f"capability {self.name} input_schema must be an object")
        if self.output_schema.get("type") != "object":
            raise ValueError(f"capability {self.name} output_schema must be an object")
        return self

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "postconditions": list(self.postconditions),
            "version": self.version,
        }


@dataclass(frozen=True)
class TaskRoute:
    kind: RouteKind
    target: str | None
    reason: str

    def to_dict(self) -> JsonDict:
        return {
            "kind": self.kind.value,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PathSelector:
    target_width_m: float | None = None
    tolerance_m: float = 1e-9
    nets: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()
    primitive_ids: tuple[str, ...] = ()
    parameterized: bool | None = None

    def validate(self) -> "PathSelector":
        if self.target_width_m is not None and self.target_width_m < 0:
            raise ValueError("target_width_m must be non-negative")
        if self.tolerance_m < 0:
            raise ValueError("tolerance_m must be non-negative")
        return self

    def to_dict(self) -> JsonDict:
        return {
            "target_width_m": self.target_width_m,
            "tolerance_m": self.tolerance_m,
            "nets": list(self.nets),
            "layers": list(self.layers),
            "primitive_ids": list(self.primitive_ids),
            "parameterized": self.parameterized,
        }


@dataclass(frozen=True)
class LayoutPathRecord:
    primitive_id: str
    name: str
    net: str
    layer: str
    width_m: float
    width_expression: str
    is_parameterized: bool

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ParameterizationPreview:
    preview_id: str
    session_id: str
    selector: PathSelector
    variable_name: str
    variable_value: str
    targets: tuple[LayoutPathRecord, ...]
    snapshot_digest: str
    working_project_path: str | None

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        selector: PathSelector,
        variable_name: str,
        variable_value: str,
        targets: list[LayoutPathRecord],
        working_project_path: str | None,
    ) -> "ParameterizationPreview":
        snapshot_digest = path_snapshot_digest(targets)
        payload = {
            "session_id": session_id,
            "selector": selector.to_dict(),
            "variable_name": variable_name,
            "variable_value": variable_value,
            "snapshot_digest": snapshot_digest,
        }
        preview_id = "preview-" + _digest(payload)[:24]
        return cls(
            preview_id=preview_id,
            session_id=session_id,
            selector=selector,
            variable_name=variable_name,
            variable_value=variable_value,
            targets=tuple(targets),
            snapshot_digest=snapshot_digest,
            working_project_path=working_project_path,
        )

    def to_dict(self) -> JsonDict:
        return {
            "preview_id": self.preview_id,
            "session_id": self.session_id,
            "selector": self.selector.to_dict(),
            "variable_name": self.variable_name,
            "variable_value": self.variable_value,
            "target_count": len(self.targets),
            "targets": [target.to_dict() for target in self.targets],
            "snapshot_digest": self.snapshot_digest,
            "working_project_path": self.working_project_path,
        }


@dataclass(frozen=True)
class ParameterizationResult:
    session_id: str
    preview_id: str
    status: str
    variable_name: str
    variable_value: str
    target_count: int
    verified_count: int
    before: tuple[LayoutPathRecord, ...]
    after: tuple[LayoutPathRecord, ...]
    working_project_path: str
    evidence: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "session_id": self.session_id,
            "preview_id": self.preview_id,
            "status": self.status,
            "variable_name": self.variable_name,
            "variable_value": self.variable_value,
            "target_count": self.target_count,
            "verified_count": self.verified_count,
            "before": [item.to_dict() for item in self.before],
            "after": [item.to_dict() for item in self.after],
            "working_project_path": self.working_project_path,
            "evidence": self.evidence,
        }


def path_snapshot_digest(records: list[LayoutPathRecord] | tuple[LayoutPathRecord, ...]) -> str:
    payload = [record.to_dict() for record in sorted(records, key=lambda item: item.primitive_id)]
    return _digest(payload)


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
