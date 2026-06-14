from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from aedt_agent.agent.mission import utc_now_iso


JsonDict = dict[str, Any]


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    ACCEPTED = "accepted"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"
    FAILED = "failed"


class ActionDecision(StrEnum):
    ACCEPT = "accept"
    ROLLBACK = "rollback"
    REVIEW = "review"


class ActionExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    mission_id: str
    action_type: str
    version: int
    status: ActionStatus
    target: JsonDict
    parameters: JsonDict
    constraints: JsonDict
    reason: JsonDict
    adapter_mode: str
    adapter_input: JsonDict
    digest: str
    created_at: str
    updated_at: str
    approval_id: str | None = None
    comparison: JsonDict | None = None
    decision: ActionDecision | None = None
    error: JsonDict | None = None

    @classmethod
    def create(
        cls,
        action_id: str,
        mission_id: str,
        target: JsonDict,
        parameters: JsonDict,
        constraints: JsonDict,
        reason: JsonDict,
        adapter_mode: str,
        adapter_input: JsonDict,
        action_type: str = "adjust_layout_void",
        version: int = 1,
    ) -> "ActionRecord":
        now = utc_now_iso()
        engineering_payload = {
            "mission_id": mission_id,
            "action_type": action_type,
            "version": version,
            "target": target,
            "parameters": parameters,
            "constraints": constraints,
            "reason": reason,
            "adapter_mode": adapter_mode,
            "adapter_input": adapter_input,
        }
        return cls(
            action_id=action_id,
            mission_id=mission_id,
            action_type=action_type,
            version=version,
            status=ActionStatus.PROPOSED,
            target=dict(target),
            parameters=dict(parameters),
            constraints=dict(constraints),
            reason=dict(reason),
            adapter_mode=adapter_mode,
            adapter_input=dict(adapter_input),
            digest=_digest(engineering_payload),
            created_at=now,
            updated_at=now,
        )

    def with_status(
        self,
        status: ActionStatus,
        *,
        approval_id: str | None = None,
        comparison: JsonDict | None = None,
        decision: ActionDecision | None = None,
        error: JsonDict | None = None,
    ) -> "ActionRecord":
        return replace(
            self,
            status=status,
            updated_at=utc_now_iso(),
            approval_id=self.approval_id if approval_id is None else approval_id,
            comparison=self.comparison if comparison is None else comparison,
            decision=self.decision if decision is None else decision,
            error=error,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "action_id": self.action_id,
            "mission_id": self.mission_id,
            "action_type": self.action_type,
            "version": self.version,
            "status": self.status.value,
            "target": self.target,
            "parameters": self.parameters,
            "constraints": self.constraints,
            "reason": self.reason,
            "adapter_mode": self.adapter_mode,
            "adapter_input": self.adapter_input,
            "digest": self.digest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "approval_id": self.approval_id,
            "comparison": self.comparison,
            "decision": None if self.decision is None else self.decision.value,
            "error": self.error,
        }


@dataclass(frozen=True)
class ActionExecutionRecord:
    execution_id: str
    action_id: str
    mission_id: str
    adapter_mode: str
    status: ActionExecutionStatus
    before_artifact_refs: list[str]
    after_artifact_refs: list[str]
    created_at: str
    updated_at: str
    completed_at: str | None = None
    result: JsonDict = field(default_factory=dict)
    error: JsonDict | None = None

    @classmethod
    def create(
        cls,
        execution_id: str,
        action_id: str,
        mission_id: str,
        adapter_mode: str,
        before_artifact_refs: list[str],
        after_artifact_refs: list[str],
    ) -> "ActionExecutionRecord":
        now = utc_now_iso()
        return cls(
            execution_id=execution_id,
            action_id=action_id,
            mission_id=mission_id,
            adapter_mode=adapter_mode,
            status=ActionExecutionStatus.RUNNING,
            before_artifact_refs=list(before_artifact_refs),
            after_artifact_refs=list(after_artifact_refs),
            created_at=now,
            updated_at=now,
        )

    def with_completion(
        self,
        status: ActionExecutionStatus,
        *,
        result: JsonDict | None = None,
        error: JsonDict | None = None,
    ) -> "ActionExecutionRecord":
        now = utc_now_iso()
        return replace(
            self,
            status=status,
            updated_at=now,
            completed_at=now,
            result={} if result is None else result,
            error=error,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "execution_id": self.execution_id,
            "action_id": self.action_id,
            "mission_id": self.mission_id,
            "adapter_mode": self.adapter_mode,
            "status": self.status.value,
            "before_artifact_refs": self.before_artifact_refs,
            "after_artifact_refs": self.after_artifact_refs,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }


def _digest(payload: JsonDict) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
