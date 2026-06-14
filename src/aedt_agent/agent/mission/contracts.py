from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    GRAPH_RUN_CREATED = "graph_run_created"
    GRAPH_RUN_UPDATED = "graph_run_updated"
    NODE_RUN_CREATED = "node_run_created"
    NODE_RUN_UPDATED = "node_run_updated"
    ARTIFACT_MANIFEST_CREATED = "artifact_manifest_created"
    EVIDENCE_PACKAGE_CREATED = "evidence_package_created"
    JOB_ATTEMPT_CREATED = "job_attempt_created"
    JOB_ATTEMPT_UPDATED = "job_attempt_updated"
    ACTION_CREATED = "action_created"
    ACTION_UPDATED = "action_updated"
    ACTION_EXECUTION_CREATED = "action_execution_created"
    ACTION_EXECUTION_UPDATED = "action_execution_updated"
    MISSION_LOOP_CREATED = "mission_loop_created"
    MISSION_LOOP_UPDATED = "mission_loop_updated"
    MISSION_FINAL_OUTCOME_SET = "mission_final_outcome_set"


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


class GraphRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class NodeRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_APPROVAL = "waiting_approval"


class JobAttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


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


@dataclass(frozen=True)
class GraphRunRecord:
    graph_run_id: str
    mission_id: str
    template_id: str
    template_version: int
    plan_version: int
    status: GraphRunStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    current_node_id: str | None = None
    error: JsonDict | None = None

    @classmethod
    def create(
        cls,
        graph_run_id: str,
        mission_id: str,
        template_id: str,
        template_version: int,
        plan_version: int,
    ) -> "GraphRunRecord":
        now = utc_now_iso()
        return cls(
            graph_run_id=graph_run_id,
            mission_id=mission_id,
            template_id=template_id,
            template_version=template_version,
            plan_version=plan_version,
            status=GraphRunStatus.CREATED,
            created_at=now,
            updated_at=now,
        )

    def with_status(
        self,
        status: GraphRunStatus,
        *,
        current_node_id: str | None = None,
        error: JsonDict | None = None,
    ) -> "GraphRunRecord":
        now = utc_now_iso()
        started_at = self.started_at
        completed_at = self.completed_at
        if status == GraphRunStatus.RUNNING and started_at is None:
            started_at = now
        if status in {GraphRunStatus.SUCCEEDED, GraphRunStatus.FAILED, GraphRunStatus.CANCELED}:
            completed_at = now
        return replace(
            self,
            status=status,
            updated_at=now,
            started_at=started_at,
            completed_at=completed_at,
            current_node_id=current_node_id,
            error=error,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "graph_run_id": self.graph_run_id,
            "mission_id": self.mission_id,
            "template_id": self.template_id,
            "template_version": self.template_version,
            "plan_version": self.plan_version,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_node_id": self.current_node_id,
            "error": self.error,
        }


@dataclass(frozen=True)
class NodeRunRecord:
    node_run_id: str
    graph_run_id: str
    mission_id: str
    node_id: str
    node_role: str
    node_kind: str
    sequence: int
    status: NodeRunStatus
    input_payload: JsonDict
    output_payload: JsonDict
    artifact_refs: list[str]
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    evidence_package_id: str | None = None
    edge_decision: str | None = None
    error: JsonDict | None = None

    @classmethod
    def create(
        cls,
        node_run_id: str,
        graph_run_id: str,
        mission_id: str,
        node_id: str,
        node_role: str,
        node_kind: str,
        sequence: int,
        input_payload: JsonDict,
    ) -> "NodeRunRecord":
        now = utc_now_iso()
        return cls(
            node_run_id=node_run_id,
            graph_run_id=graph_run_id,
            mission_id=mission_id,
            node_id=node_id,
            node_role=node_role,
            node_kind=node_kind,
            sequence=sequence,
            status=NodeRunStatus.CREATED,
            input_payload=input_payload,
            output_payload={},
            artifact_refs=[],
            created_at=now,
            updated_at=now,
        )

    def with_status(self, status: NodeRunStatus) -> "NodeRunRecord":
        now = utc_now_iso()
        return replace(
            self,
            status=status,
            updated_at=now,
            started_at=now if status == NodeRunStatus.RUNNING and self.started_at is None else self.started_at,
        )

    def with_completion(
        self,
        status: NodeRunStatus,
        output_payload: JsonDict,
        artifact_refs: list[str],
        evidence_package_id: str | None = None,
        edge_decision: str | None = None,
        error: JsonDict | None = None,
    ) -> "NodeRunRecord":
        now = utc_now_iso()
        return replace(
            self,
            status=status,
            output_payload=output_payload,
            artifact_refs=artifact_refs,
            evidence_package_id=evidence_package_id,
            edge_decision=edge_decision,
            error=error,
            updated_at=now,
            completed_at=now,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "node_run_id": self.node_run_id,
            "graph_run_id": self.graph_run_id,
            "mission_id": self.mission_id,
            "node_id": self.node_id,
            "node_role": self.node_role,
            "node_kind": self.node_kind,
            "sequence": self.sequence,
            "status": self.status.value,
            "input_payload": self.input_payload,
            "output_payload": self.output_payload,
            "artifact_refs": self.artifact_refs,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "evidence_package_id": self.evidence_package_id,
            "edge_decision": self.edge_decision,
            "error": self.error,
        }


@dataclass(frozen=True)
class ArtifactManifest:
    artifact_id: str
    mission_id: str
    producer_kind: str
    producer_id: str
    path: str
    kind: str
    sha256: str
    size_bytes: int
    created_at: str
    metadata: JsonDict = field(default_factory=dict)
    retention_policy: str = "mission"

    @classmethod
    def create(
        cls,
        artifact_id: str,
        mission_id: str,
        producer_kind: str,
        producer_id: str,
        path: str,
        kind: str,
        sha256: str,
        size_bytes: int,
        metadata: JsonDict | None = None,
        retention_policy: str = "mission",
    ) -> "ArtifactManifest":
        return cls(
            artifact_id=artifact_id,
            mission_id=mission_id,
            producer_kind=producer_kind,
            producer_id=producer_id,
            path=path,
            kind=kind,
            sha256=sha256,
            size_bytes=size_bytes,
            created_at=utc_now_iso(),
            metadata=metadata or {},
            retention_policy=retention_policy,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "artifact_id": self.artifact_id,
            "mission_id": self.mission_id,
            "producer_kind": self.producer_kind,
            "producer_id": self.producer_id,
            "path": self.path,
            "kind": self.kind,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "retention_policy": self.retention_policy,
        }


@dataclass(frozen=True)
class EvidencePackage:
    evidence_package_id: str
    mission_id: str
    producer_kind: str
    producer_id: str
    summary: JsonDict
    artifact_refs: list[str]
    token_budget: JsonDict
    created_at: str
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        evidence_package_id: str,
        mission_id: str,
        producer_kind: str,
        producer_id: str,
        summary: JsonDict,
        artifact_refs: list[str],
        token_budget: JsonDict,
        metadata: JsonDict | None = None,
    ) -> "EvidencePackage":
        return cls(
            evidence_package_id=evidence_package_id,
            mission_id=mission_id,
            producer_kind=producer_kind,
            producer_id=producer_id,
            summary=summary,
            artifact_refs=artifact_refs,
            token_budget=token_budget,
            created_at=utc_now_iso(),
            metadata=metadata or {},
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "evidence_package_id": self.evidence_package_id,
            "mission_id": self.mission_id,
            "producer_kind": self.producer_kind,
            "producer_id": self.producer_id,
            "summary": self.summary,
            "artifact_refs": self.artifact_refs,
            "token_budget": self.token_budget,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class JobAttemptRecord:
    attempt_id: str
    mission_id: str
    job_id: str
    attempt_number: int
    worker_id: str
    status: JobAttemptStatus
    started_at: str
    updated_at: str
    completed_at: str | None = None
    error: JsonDict | None = None
    retry_decision: str | None = None

    @classmethod
    def create(
        cls,
        attempt_id: str,
        mission_id: str,
        job_id: str,
        attempt_number: int,
        worker_id: str,
    ) -> "JobAttemptRecord":
        now = utc_now_iso()
        return cls(
            attempt_id=attempt_id,
            mission_id=mission_id,
            job_id=job_id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            status=JobAttemptStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )

    def with_completion(
        self,
        status: JobAttemptStatus,
        error: JsonDict | None = None,
        retry_decision: str | None = None,
    ) -> "JobAttemptRecord":
        now = utc_now_iso()
        return replace(
            self,
            status=status,
            completed_at=now,
            updated_at=now,
            error=error,
            retry_decision=retry_decision,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "attempt_id": self.attempt_id,
            "mission_id": self.mission_id,
            "job_id": self.job_id,
            "attempt_number": self.attempt_number,
            "worker_id": self.worker_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "retry_decision": self.retry_decision,
        }
