from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class SessionRef:
    session_id: str
    project_id: str
    design_id: str


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    transaction_id: str
    output: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    traceback: str = ""
    elapsed_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status == ExecutionStatus.SUCCEEDED


@dataclass(frozen=True)
class NodeStep:
    node_id: str
    inputs: dict[str, Any]
    step_id: str = ""


@dataclass(frozen=True)
class NodePlan:
    plan: list[NodeStep]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodePlan":
        raw_steps = data.get("plan", [])
        if not isinstance(raw_steps, list):
            raise TypeError("plan must be a list")
        steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise TypeError("plan entries must be mappings")
            node_id = raw_step.get("node_id")
            inputs = raw_step.get("inputs", {})
            if not isinstance(node_id, str) or not node_id:
                raise TypeError("node_id must be a non-empty string")
            if not isinstance(inputs, dict):
                raise TypeError("inputs must be a mapping")
            step_id = raw_step.get("id") or raw_step.get("step_id") or ""
            if step_id is not None and not isinstance(step_id, str):
                raise TypeError("step id must be a string")
            steps.append(NodeStep(node_id=node_id, inputs=dict(inputs), step_id=step_id))
        return cls(plan=steps)
