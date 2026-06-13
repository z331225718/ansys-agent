from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


JsonDict = dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MissionState(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    WAITING_WORKER = "waiting_worker"
    WAITING_APPROVAL = "waiting_approval"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class EventType(StrEnum):
    MISSION_CREATED = "mission_created"
    MISSION_STATE_CHANGED = "mission_state_changed"
    JOB_CREATED = "job_created"
    JOB_LEASED = "job_leased"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"
    CHECKPOINT_CREATED = "checkpoint_created"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ErrorClass(StrEnum):
    INVALID_INPUT = "invalid_input"
    LICENSE_UNAVAILABLE = "license_unavailable"
    WORKER_CRASH = "worker_crash"
    TIMEOUT = "timeout"
    INVALID_MODEL = "invalid_model"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EngineeringConstraint:
    name: str
    value: Any

    def to_json_dict(self) -> JsonDict:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True)
class MissionRecord:
    mission_id: str
    user_goal: str
    acceptance_criteria: list[JsonDict]
    constraints: list[EngineeringConstraint]
    state: MissionState
    plan_version: int
    created_at: str
    updated_at: str
    vision_required: bool = False
    final_outcome: JsonDict | None = None

    @classmethod
    def create(
        cls,
        mission_id: str,
        user_goal: str,
        acceptance_criteria: list[JsonDict],
        constraints: list[EngineeringConstraint] | None = None,
        vision_required: bool = False,
    ) -> "MissionRecord":
        now = utc_now_iso()
        return cls(
            mission_id=mission_id,
            user_goal=user_goal,
            acceptance_criteria=acceptance_criteria,
            constraints=constraints or [],
            state=MissionState.CREATED,
            plan_version=1,
            created_at=now,
            updated_at=now,
            vision_required=vision_required,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "mission_id": self.mission_id,
            "user_goal": self.user_goal,
            "acceptance_criteria": self.acceptance_criteria,
            "constraints": [constraint.to_json_dict() for constraint in self.constraints],
            "state": self.state.value,
            "plan_version": self.plan_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "vision_required": self.vision_required,
            "final_outcome": self.final_outcome,
        }


@dataclass(frozen=True)
class JobError:
    error_class: ErrorClass
    message: str
    retryable: bool
    details: JsonDict = field(default_factory=dict)

    def to_json_dict(self) -> JsonDict:
        return {
            "error_class": self.error_class.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    mission_id: str
    capability: str
    idempotency_key: str
    input_payload: JsonDict
    output_payload: JsonDict
    artifact_refs: list[str]
    timeout_seconds: int
    retry_limit: int
    status: JobStatus
    created_at: str
    updated_at: str
    error: JobError | None = None

    @classmethod
    def create(
        cls,
        job_id: str,
        mission_id: str,
        capability: str,
        idempotency_key: str,
        input_payload: JsonDict,
        timeout_seconds: int,
        retry_limit: int,
    ) -> "JobRecord":
        now = utc_now_iso()
        return cls(
            job_id=job_id,
            mission_id=mission_id,
            capability=capability,
            idempotency_key=idempotency_key,
            input_payload=input_payload,
            output_payload={},
            artifact_refs=[],
            timeout_seconds=timeout_seconds,
            retry_limit=retry_limit,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "job_id": self.job_id,
            "mission_id": self.mission_id,
            "capability": self.capability,
            "idempotency_key": self.idempotency_key,
            "input_payload": self.input_payload,
            "output_payload": self.output_payload,
            "artifact_refs": self.artifact_refs,
            "timeout_seconds": self.timeout_seconds,
            "retry_limit": self.retry_limit,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": None if self.error is None else self.error.to_json_dict(),
        }


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    mission_id: str
    event_type: EventType
    sequence: int
    created_at: str
    payload: JsonDict

    def to_json_dict(self) -> JsonDict:
        return {
            "event_id": self.event_id,
            "mission_id": self.mission_id,
            "event_type": self.event_type.value,
            "sequence": self.sequence,
            "created_at": self.created_at,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class CheckpointRecord:
    checkpoint_id: str
    mission_id: str
    job_id: str
    created_at: str
    artifact_refs: list[str]
    payload: JsonDict

    def to_json_dict(self) -> JsonDict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "mission_id": self.mission_id,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "artifact_refs": self.artifact_refs,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    mission_id: str
    reason: str
    options: list[JsonDict]
    decision: ApprovalDecision
    created_at: str
    resolved_at: str | None = None
    selected_option_id: str | None = None
    comment: str | None = None

    @classmethod
    def create(
        cls,
        approval_id: str,
        mission_id: str,
        reason: str,
        options: list[JsonDict],
    ) -> "ApprovalRequest":
        return cls(
            approval_id=approval_id,
            mission_id=mission_id,
            reason=reason,
            options=options,
            decision=ApprovalDecision.PENDING,
            created_at=utc_now_iso(),
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "approval_id": self.approval_id,
            "mission_id": self.mission_id,
            "reason": self.reason,
            "options": self.options,
            "decision": self.decision.value,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "selected_option_id": self.selected_option_id,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class WorkerLease:
    lease_id: str
    job_id: str
    worker_id: str
    acquired_at: str
    expires_at: str
    released_at: str | None

    def to_json_dict(self) -> JsonDict:
        return {
            "lease_id": self.lease_id,
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "released_at": self.released_at,
        }
