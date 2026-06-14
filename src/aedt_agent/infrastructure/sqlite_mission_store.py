from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from aedt_agent.agent.actions import (
    ActionDecision,
    ActionExecutionRecord,
    ActionExecutionStatus,
    ActionRecord,
    ActionStatus,
    assert_action_transition,
)
from aedt_agent.agent.mission import (
    ApprovalDecision,
    ApprovalRequest,
    ArtifactManifest,
    CheckpointRecord,
    EngineeringConstraint,
    ErrorClass,
    EventRecord,
    EventType,
    EvidencePackage,
    GraphRunRecord,
    GraphRunStatus,
    JobAttemptRecord,
    JobAttemptStatus,
    JobError,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
    NodeRunRecord,
    NodeRunStatus,
    WorkerLease,
    utc_now_iso,
)

if TYPE_CHECKING:
    from aedt_agent.agent.orchestrator.loop_contracts import MissionLoopRecord


class SQLiteMissionStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    user_goal TEXT NOT NULL,
                    acceptance_criteria_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    vision_required INTEGER NOT NULL,
                    final_outcome_json TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    event_type TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(mission_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    capability TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    input_payload_json TEXT NOT NULL,
                    output_payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL,
                    retry_limit INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_json TEXT,
                    UNIQUE(mission_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    created_at TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    reason TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    selected_option_id TEXT,
                    comment TEXT
                );
                CREATE TABLE IF NOT EXISTS worker_leases (
                    lease_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    worker_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    released_at TEXT
                );
                CREATE TABLE IF NOT EXISTS graph_runs (
                    graph_run_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    template_id TEXT NOT NULL,
                    template_version INTEGER NOT NULL,
                    plan_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    current_node_id TEXT,
                    error_json TEXT
                );
                CREATE TABLE IF NOT EXISTS node_runs (
                    node_run_id TEXT PRIMARY KEY,
                    graph_run_id TEXT NOT NULL REFERENCES graph_runs(graph_run_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    node_id TEXT NOT NULL,
                    node_role TEXT NOT NULL,
                    node_kind TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    input_payload_json TEXT NOT NULL,
                    output_payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    evidence_package_id TEXT,
                    edge_decision TEXT,
                    error_json TEXT,
                    UNIQUE(graph_run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS artifact_manifests (
                    artifact_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    producer_kind TEXT NOT NULL,
                    producer_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    retention_policy TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_packages (
                    evidence_package_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    producer_kind TEXT NOT NULL,
                    producer_id TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    token_budget_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    attempt_number INTEGER NOT NULL,
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_json TEXT,
                    retry_decision TEXT,
                    UNIQUE(job_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS action_records (
                    action_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    action_type TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    target_json TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    reason_json TEXT NOT NULL,
                    adapter_mode TEXT NOT NULL,
                    adapter_input_json TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approval_id TEXT,
                    comparison_json TEXT,
                    decision TEXT,
                    error_json TEXT
                );
                CREATE TABLE IF NOT EXISTS action_executions (
                    execution_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES action_records(action_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    adapter_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    before_artifact_refs_json TEXT NOT NULL,
                    after_artifact_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_json TEXT NOT NULL,
                    error_json TEXT
                );
                CREATE TABLE IF NOT EXISTS mission_loops (
                    loop_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL UNIQUE REFERENCES missions(mission_id),
                    profile_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    iteration_count INTEGER NOT NULL,
                    job_attempt_count INTEGER NOT NULL,
                    evidence_query_calls INTEGER NOT NULL,
                    evidence_tokens INTEGER NOT NULL,
                    duplicate_action_count INTEGER NOT NULL,
                    consecutive_no_improvement INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    last_decision TEXT,
                    last_reason TEXT,
                    last_job_id TEXT,
                    retry_not_before TEXT
                );
                """
            )

    def create_mission(self, mission: MissionRecord) -> MissionRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO missions (
                    mission_id, user_goal, acceptance_criteria_json, constraints_json,
                    state, plan_version, created_at, updated_at, vision_required, final_outcome_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission.mission_id,
                    mission.user_goal,
                    _dump(mission.acceptance_criteria),
                    _dump([constraint.to_json_dict() for constraint in mission.constraints]),
                    mission.state.value,
                    mission.plan_version,
                    mission.created_at,
                    mission.updated_at,
                    int(mission.vision_required),
                    _dump(mission.final_outcome) if mission.final_outcome is not None else None,
                ),
            )
            self._append_event_in_tx(
                db,
                mission.mission_id,
                EventType.MISSION_CREATED,
                {"state": mission.state.value, "user_goal": mission.user_goal},
            )
        return mission

    def get_mission(self, mission_id: str) -> MissionRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
        return None if row is None else _mission_from_row(row)

    def update_mission_state(self, mission_id: str, state: MissionState) -> MissionRecord:
        from aedt_agent.agent.orchestrator.state_machine import assert_transition

        current = self.get_mission(mission_id)
        if current is None:
            raise KeyError(f"mission not found: {mission_id}")
        if current.state == state:
            return current
        assert_transition(current.state, state)
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                "UPDATE missions SET state = ?, updated_at = ? WHERE mission_id = ?",
                (state.value, now, mission_id),
            )
            self._append_event_in_tx(db, mission_id, EventType.MISSION_STATE_CHANGED, {"state": state.value})
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        return mission

    def set_mission_final_outcome(self, mission_id: str, outcome: dict) -> MissionRecord:
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        if mission.state not in {MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELED}:
            raise ValueError("final outcome can only be set for a terminal mission")
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                "UPDATE missions SET final_outcome_json = ?, updated_at = ? WHERE mission_id = ?",
                (_dump(outcome), now, mission_id),
            )
            self._append_event_in_tx(
                db,
                mission_id,
                EventType.MISSION_FINAL_OUTCOME_SET,
                {"code": outcome.get("code"), "decision": outcome.get("decision")},
            )
        updated = self.get_mission(mission_id)
        if updated is None:
            raise KeyError(f"mission not found: {mission_id}")
        return updated

    def create_mission_loop(self, record: "MissionLoopRecord") -> "MissionLoopRecord":
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO mission_loops (
                    loop_id, mission_id, profile_json, status, iteration_count,
                    job_attempt_count, evidence_query_calls, evidence_tokens,
                    duplicate_action_count, consecutive_no_improvement, started_at,
                    updated_at, completed_at, last_decision, last_reason, last_job_id,
                    retry_not_before
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.loop_id,
                    record.mission_id,
                    _dump(record.profile.to_json_dict()),
                    record.status.value,
                    record.iteration_count,
                    record.job_attempt_count,
                    record.evidence_query_calls,
                    record.evidence_tokens,
                    record.duplicate_action_count,
                    record.consecutive_no_improvement,
                    record.started_at,
                    record.updated_at,
                    record.completed_at,
                    None if record.last_decision is None else record.last_decision.value,
                    record.last_reason,
                    record.last_job_id,
                    record.retry_not_before,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.MISSION_LOOP_CREATED,
                {"loop_id": record.loop_id, "profile_id": record.profile.profile_id},
            )
        return record

    def get_mission_loop(self, mission_id: str) -> "MissionLoopRecord | None":
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM mission_loops WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
        return None if row is None else _mission_loop_from_row(row)

    def update_mission_loop(self, record: "MissionLoopRecord") -> "MissionLoopRecord":
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE mission_loops
                SET profile_json = ?, status = ?, iteration_count = ?,
                    job_attempt_count = ?, evidence_query_calls = ?, evidence_tokens = ?,
                    duplicate_action_count = ?, consecutive_no_improvement = ?,
                    updated_at = ?, completed_at = ?, last_decision = ?,
                    last_reason = ?, last_job_id = ?, retry_not_before = ?
                WHERE loop_id = ? AND mission_id = ?
                """,
                (
                    _dump(record.profile.to_json_dict()),
                    record.status.value,
                    record.iteration_count,
                    record.job_attempt_count,
                    record.evidence_query_calls,
                    record.evidence_tokens,
                    record.duplicate_action_count,
                    record.consecutive_no_improvement,
                    record.updated_at,
                    record.completed_at,
                    None if record.last_decision is None else record.last_decision.value,
                    record.last_reason,
                    record.last_job_id,
                    record.retry_not_before,
                    record.loop_id,
                    record.mission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"mission loop not found: {record.loop_id}")
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.MISSION_LOOP_UPDATED,
                {
                    "loop_id": record.loop_id,
                    "status": record.status.value,
                    "decision": None if record.last_decision is None else record.last_decision.value,
                },
            )
        loaded = self.get_mission_loop(record.mission_id)
        if loaded is None:
            raise KeyError(f"mission loop not found: {record.loop_id}")
        return loaded

    def create_job(
        self,
        mission_id: str,
        capability: str,
        idempotency_key: str,
        input_payload: dict,
        timeout_seconds: int,
        retry_limit: int,
    ) -> JobRecord:
        existing = self.get_job_by_idempotency_key(mission_id, idempotency_key)
        if existing is not None:
            return existing
        job = JobRecord.create(str(uuid4()), mission_id, capability, idempotency_key, input_payload, timeout_seconds, retry_limit)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO jobs (
                    job_id, mission_id, capability, idempotency_key, input_payload_json,
                    output_payload_json, artifact_refs_json, timeout_seconds, retry_limit,
                    status, created_at, updated_at, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.mission_id,
                    job.capability,
                    job.idempotency_key,
                    _dump(job.input_payload),
                    _dump(job.output_payload),
                    _dump(job.artifact_refs),
                    job.timeout_seconds,
                    job.retry_limit,
                    job.status.value,
                    job.created_at,
                    job.updated_at,
                    None,
                ),
            )
            self._append_event_in_tx(db, mission_id, EventType.JOB_CREATED, {"job_id": job.job_id, "capability": capability})
        return job

    def get_job(self, job_id: str) -> JobRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job not found: {job_id}")
        return _job_from_row(row)

    def get_job_by_idempotency_key(self, mission_id: str, idempotency_key: str) -> JobRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE mission_id = ? AND idempotency_key = ?",
                (mission_id, idempotency_key),
            ).fetchone()
        return None if row is None else _job_from_row(row)

    def list_jobs(self, mission_id: str) -> list[JobRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE mission_id = ? ORDER BY created_at, job_id", (mission_id,)).fetchall()
        return [_job_from_row(row) for row in rows]

    def next_queued_job(self, mission_id: str) -> JobRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE mission_id = ? AND status = ? ORDER BY created_at, job_id LIMIT 1",
                (mission_id, JobStatus.QUEUED.value),
            ).fetchone()
        return None if row is None else _job_from_row(row)

    def complete_job(self, job_id: str, output_payload: dict, artifact_refs: list[str]) -> JobRecord:
        job = self.get_job(job_id)
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                """
                UPDATE jobs
                SET status = ?, output_payload_json = ?, artifact_refs_json = ?, updated_at = ?, error_json = NULL
                WHERE job_id = ?
                """,
                (JobStatus.SUCCEEDED.value, _dump(output_payload), _dump(artifact_refs), now, job_id),
            )
            self._append_event_in_tx(db, job.mission_id, EventType.JOB_SUCCEEDED, {"job_id": job_id})
        return self.get_job(job_id)

    def fail_job(self, job_id: str, error: JobError) -> JobRecord:
        job = self.get_job(job_id)
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error_json = ? WHERE job_id = ?",
                (JobStatus.FAILED.value, now, _dump(error.to_json_dict()), job_id),
            )
            self._append_event_in_tx(db, job.mission_id, EventType.JOB_FAILED, {"job_id": job_id, "error": error.to_json_dict()})
        return self.get_job(job_id)

    def acquire_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        job = self.get_job(job_id)
        current = now or datetime.now(UTC)
        lease = WorkerLease(
            lease_id=str(uuid4()),
            job_id=job_id,
            worker_id=worker_id,
            acquired_at=current.isoformat(),
            expires_at=(current + timedelta(seconds=lease_seconds)).isoformat(),
            released_at=None,
        )
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (JobStatus.LEASED.value, utc_now_iso(), job_id),
            )
            db.execute(
                "INSERT INTO worker_leases VALUES (?, ?, ?, ?, ?, ?)",
                (lease.lease_id, lease.job_id, lease.worker_id, lease.acquired_at, lease.expires_at, lease.released_at),
            )
            self._append_event_in_tx(db, job.mission_id, EventType.JOB_LEASED, {"job_id": job_id, "worker_id": worker_id})
        return lease

    def release_job_lease(self, lease_id: str) -> WorkerLease:
        now = utc_now_iso()
        with self._connect() as db:
            db.execute("UPDATE worker_leases SET released_at = ? WHERE lease_id = ?", (now, lease_id))
            row = db.execute("SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)).fetchone()
        if row is None:
            raise KeyError(f"lease not found: {lease_id}")
        return _lease_from_row(row)

    def recover_expired_leases(self, now: datetime) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT worker_leases.*
                FROM worker_leases
                JOIN jobs ON jobs.job_id = worker_leases.job_id
                WHERE worker_leases.released_at IS NULL
                  AND worker_leases.expires_at < ?
                  AND jobs.status = ?
                """,
                (now.isoformat(), JobStatus.LEASED.value),
            ).fetchall()
            job_ids = [row["job_id"] for row in rows]
            for job_id in job_ids:
                db.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                    (JobStatus.QUEUED.value, utc_now_iso(), job_id),
                )
            db.executemany(
                "UPDATE worker_leases SET released_at = ? WHERE lease_id = ?",
                [(now.isoformat(), row["lease_id"]) for row in rows],
            )
        return job_ids

    def create_checkpoint(self, mission_id: str, job_id: str, artifact_refs: list[str], payload: dict) -> CheckpointRecord:
        checkpoint = CheckpointRecord(str(uuid4()), mission_id, job_id, utc_now_iso(), artifact_refs, payload)
        with self._connect() as db:
            db.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?)",
                (
                    checkpoint.checkpoint_id,
                    checkpoint.mission_id,
                    checkpoint.job_id,
                    checkpoint.created_at,
                    _dump(checkpoint.artifact_refs),
                    _dump(checkpoint.payload),
                ),
            )
            self._append_event_in_tx(db, mission_id, EventType.CHECKPOINT_CREATED, {"checkpoint_id": checkpoint.checkpoint_id})
        return checkpoint

    def create_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self._connect() as db:
            db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.approval_id,
                    approval.mission_id,
                    approval.reason,
                    _dump(approval.options),
                    approval.decision.value,
                    approval.created_at,
                    approval.resolved_at,
                    approval.selected_option_id,
                    approval.comment,
                ),
            )
            self._append_event_in_tx(db, approval.mission_id, EventType.APPROVAL_REQUESTED, {"approval_id": approval.approval_id})
        return approval

    def resolve_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        selected_option_id: str | None,
        comment: str | None,
    ) -> ApprovalRequest:
        now = utc_now_iso()
        with self._connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(f"approval not found: {approval_id}")
            db.execute(
                """
                UPDATE approvals
                SET decision = ?, resolved_at = ?, selected_option_id = ?, comment = ?
                WHERE approval_id = ?
                """,
                (decision.value, now, selected_option_id, comment, approval_id),
            )
            self._append_event_in_tx(
                db,
                row["mission_id"],
                EventType.APPROVAL_RESOLVED,
                {"approval_id": approval_id, "decision": decision.value, "selected_option_id": selected_option_id},
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        with self._connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"approval not found: {approval_id}")
        return _approval_from_row(row)

    def create_graph_run(self, record: GraphRunRecord) -> GraphRunRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO graph_runs (
                    graph_run_id, mission_id, template_id, template_version, plan_version,
                    status, created_at, updated_at, started_at, completed_at, current_node_id, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.graph_run_id,
                    record.mission_id,
                    record.template_id,
                    record.template_version,
                    record.plan_version,
                    record.status.value,
                    record.created_at,
                    record.updated_at,
                    record.started_at,
                    record.completed_at,
                    record.current_node_id,
                    _dump(record.error) if record.error is not None else None,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.GRAPH_RUN_CREATED,
                {"graph_run_id": record.graph_run_id, "template_id": record.template_id},
            )
        return record

    def get_graph_run(self, graph_run_id: str) -> GraphRunRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM graph_runs WHERE graph_run_id = ?", (graph_run_id,)).fetchone()
        return None if row is None else _graph_run_from_row(row)

    def list_graph_runs(self, mission_id: str) -> list[GraphRunRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM graph_runs WHERE mission_id = ? ORDER BY created_at, graph_run_id",
                (mission_id,),
            ).fetchall()
        return [_graph_run_from_row(row) for row in rows]

    def update_graph_run_status(
        self,
        graph_run_id: str,
        status: GraphRunStatus,
        current_node_id: str | None = None,
        error: dict | None = None,
    ) -> GraphRunRecord:
        graph_run = self.get_graph_run(graph_run_id)
        if graph_run is None:
            raise KeyError(f"graph run not found: {graph_run_id}")
        updated = graph_run.with_status(status, current_node_id=current_node_id, error=error)
        with self._connect() as db:
            db.execute(
                """
                UPDATE graph_runs
                SET status = ?, updated_at = ?, started_at = ?, completed_at = ?,
                    current_node_id = ?, error_json = ?
                WHERE graph_run_id = ?
                """,
                (
                    updated.status.value,
                    updated.updated_at,
                    updated.started_at,
                    updated.completed_at,
                    updated.current_node_id,
                    _dump(updated.error) if updated.error is not None else None,
                    graph_run_id,
                ),
            )
            self._append_event_in_tx(
                db,
                updated.mission_id,
                EventType.GRAPH_RUN_UPDATED,
                {"graph_run_id": graph_run_id, "status": updated.status.value},
            )
        return updated

    def create_node_run(self, record: NodeRunRecord) -> NodeRunRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO node_runs (
                    node_run_id, graph_run_id, mission_id, node_id, node_role, node_kind,
                    sequence, status, input_payload_json, output_payload_json, artifact_refs_json,
                    created_at, updated_at, started_at, completed_at, evidence_package_id,
                    edge_decision, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.node_run_id,
                    record.graph_run_id,
                    record.mission_id,
                    record.node_id,
                    record.node_role,
                    record.node_kind,
                    record.sequence,
                    record.status.value,
                    _dump(record.input_payload),
                    _dump(record.output_payload),
                    _dump(record.artifact_refs),
                    record.created_at,
                    record.updated_at,
                    record.started_at,
                    record.completed_at,
                    record.evidence_package_id,
                    record.edge_decision,
                    _dump(record.error) if record.error is not None else None,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.NODE_RUN_CREATED,
                {"graph_run_id": record.graph_run_id, "node_run_id": record.node_run_id, "node_id": record.node_id},
            )
        return record

    def get_node_run(self, node_run_id: str) -> NodeRunRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM node_runs WHERE node_run_id = ?", (node_run_id,)).fetchone()
        if row is None:
            raise KeyError(f"node run not found: {node_run_id}")
        return _node_run_from_row(row)

    def complete_node_run(
        self,
        node_run_id: str,
        status: NodeRunStatus,
        output_payload: dict,
        artifact_refs: list[str],
        evidence_package_id: str | None = None,
        edge_decision: str | None = None,
        error: dict | None = None,
    ) -> NodeRunRecord:
        updated = self.get_node_run(node_run_id).with_completion(
            status=status,
            output_payload=output_payload,
            artifact_refs=artifact_refs,
            evidence_package_id=evidence_package_id,
            edge_decision=edge_decision,
            error=error,
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE node_runs
                SET status = ?, output_payload_json = ?, artifact_refs_json = ?,
                    updated_at = ?, completed_at = ?, evidence_package_id = ?,
                    edge_decision = ?, error_json = ?
                WHERE node_run_id = ?
                """,
                (
                    updated.status.value,
                    _dump(updated.output_payload),
                    _dump(updated.artifact_refs),
                    updated.updated_at,
                    updated.completed_at,
                    updated.evidence_package_id,
                    updated.edge_decision,
                    _dump(updated.error) if updated.error is not None else None,
                    node_run_id,
                ),
            )
            self._append_event_in_tx(
                db,
                updated.mission_id,
                EventType.NODE_RUN_UPDATED,
                {"node_run_id": node_run_id, "status": updated.status.value},
            )
        return updated

    def list_node_runs(self, graph_run_id: str) -> list[NodeRunRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM node_runs WHERE graph_run_id = ? ORDER BY sequence, created_at, node_run_id",
                (graph_run_id,),
            ).fetchall()
        return [_node_run_from_row(row) for row in rows]

    def create_artifact_manifest(self, record: ArtifactManifest) -> ArtifactManifest:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO artifact_manifests (
                    artifact_id, mission_id, producer_kind, producer_id, path, kind,
                    sha256, size_bytes, created_at, metadata_json, retention_policy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.artifact_id,
                    record.mission_id,
                    record.producer_kind,
                    record.producer_id,
                    record.path,
                    record.kind,
                    record.sha256,
                    record.size_bytes,
                    record.created_at,
                    _dump(record.metadata),
                    record.retention_policy,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.ARTIFACT_MANIFEST_CREATED,
                {"artifact_id": record.artifact_id, "path": record.path, "kind": record.kind},
            )
        return record

    def list_artifact_manifests(self, mission_id: str) -> list[ArtifactManifest]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM artifact_manifests WHERE mission_id = ? ORDER BY created_at, artifact_id",
                (mission_id,),
            ).fetchall()
        return [_artifact_manifest_from_row(row) for row in rows]

    def create_evidence_package(self, record: EvidencePackage) -> EvidencePackage:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO evidence_packages (
                    evidence_package_id, mission_id, producer_kind, producer_id,
                    summary_json, artifact_refs_json, token_budget_json, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.evidence_package_id,
                    record.mission_id,
                    record.producer_kind,
                    record.producer_id,
                    _dump(record.summary),
                    _dump(record.artifact_refs),
                    _dump(record.token_budget),
                    record.created_at,
                    _dump(record.metadata),
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.EVIDENCE_PACKAGE_CREATED,
                {"evidence_package_id": record.evidence_package_id, "producer_id": record.producer_id},
            )
        return record

    def get_evidence_package(self, evidence_package_id: str) -> EvidencePackage:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM evidence_packages WHERE evidence_package_id = ?",
                (evidence_package_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"evidence package not found: {evidence_package_id}")
        return _evidence_package_from_row(row)

    def list_evidence_packages(self, mission_id: str) -> list[EvidencePackage]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM evidence_packages WHERE mission_id = ? ORDER BY created_at, evidence_package_id",
                (mission_id,),
            ).fetchall()
        return [_evidence_package_from_row(row) for row in rows]

    def create_job_attempt(self, record: JobAttemptRecord) -> JobAttemptRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO job_attempts (
                    attempt_id, mission_id, job_id, attempt_number, worker_id, status,
                    started_at, updated_at, completed_at, error_json, retry_decision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.attempt_id,
                    record.mission_id,
                    record.job_id,
                    record.attempt_number,
                    record.worker_id,
                    record.status.value,
                    record.started_at,
                    record.updated_at,
                    record.completed_at,
                    _dump(record.error) if record.error is not None else None,
                    record.retry_decision,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.JOB_ATTEMPT_CREATED,
                {"attempt_id": record.attempt_id, "job_id": record.job_id},
            )
        return record

    def complete_job_attempt(
        self,
        attempt_id: str,
        status: JobAttemptStatus,
        error: dict | None = None,
        retry_decision: str | None = None,
    ) -> JobAttemptRecord:
        attempt = self.get_job_attempt(attempt_id).with_completion(status, error=error, retry_decision=retry_decision)
        with self._connect() as db:
            db.execute(
                """
                UPDATE job_attempts
                SET status = ?, updated_at = ?, completed_at = ?, error_json = ?, retry_decision = ?
                WHERE attempt_id = ?
                """,
                (
                    attempt.status.value,
                    attempt.updated_at,
                    attempt.completed_at,
                    _dump(attempt.error) if attempt.error is not None else None,
                    attempt.retry_decision,
                    attempt_id,
                ),
            )
            self._append_event_in_tx(
                db,
                attempt.mission_id,
                EventType.JOB_ATTEMPT_UPDATED,
                {"attempt_id": attempt_id, "status": attempt.status.value, "retry_decision": retry_decision},
            )
        return attempt

    def get_job_attempt(self, attempt_id: str) -> JobAttemptRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM job_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise KeyError(f"job attempt not found: {attempt_id}")
        return _job_attempt_from_row(row)

    def list_job_attempts(self, job_id: str) -> list[JobAttemptRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM job_attempts WHERE job_id = ? ORDER BY attempt_number, started_at, attempt_id",
                (job_id,),
            ).fetchall()
        return [_job_attempt_from_row(row) for row in rows]

    def create_action(self, record: ActionRecord) -> ActionRecord:
        with self._connect() as db:
            duplicate = db.execute(
                "SELECT action_id FROM action_records WHERE mission_id = ? AND digest = ?",
                (record.mission_id, record.digest),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    f"duplicate action digest for mission: {record.mission_id} ({record.digest})"
                )
            db.execute(
                """
                INSERT INTO action_records (
                    action_id, mission_id, action_type, version, status, target_json,
                    parameters_json, constraints_json, reason_json, adapter_mode,
                    adapter_input_json, digest, created_at, updated_at, approval_id,
                    comparison_json, decision, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.action_id,
                    record.mission_id,
                    record.action_type,
                    record.version,
                    record.status.value,
                    _dump(record.target),
                    _dump(record.parameters),
                    _dump(record.constraints),
                    _dump(record.reason),
                    record.adapter_mode,
                    _dump(record.adapter_input),
                    record.digest,
                    record.created_at,
                    record.updated_at,
                    record.approval_id,
                    _dump(record.comparison) if record.comparison is not None else None,
                    None if record.decision is None else record.decision.value,
                    _dump(record.error) if record.error is not None else None,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.ACTION_CREATED,
                {"action_id": record.action_id, "action_type": record.action_type, "digest": record.digest},
            )
        return record

    def get_action(self, action_id: str) -> ActionRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM action_records WHERE action_id = ?", (action_id,)).fetchone()
        if row is None:
            raise KeyError(f"action not found: {action_id}")
        return _action_from_row(row)

    def list_actions(self, mission_id: str) -> list[ActionRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM action_records WHERE mission_id = ? ORDER BY created_at, action_id",
                (mission_id,),
            ).fetchall()
        return [_action_from_row(row) for row in rows]

    def update_action(self, record: ActionRecord) -> ActionRecord:
        current = self.get_action(record.action_id)
        assert_action_transition(current.status, record.status)
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE action_records
                SET status = ?, updated_at = ?, approval_id = ?, comparison_json = ?,
                    decision = ?, error_json = ?
                WHERE action_id = ?
                """,
                (
                    record.status.value,
                    record.updated_at,
                    record.approval_id,
                    _dump(record.comparison) if record.comparison is not None else None,
                    None if record.decision is None else record.decision.value,
                    _dump(record.error) if record.error is not None else None,
                    record.action_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"action not found: {record.action_id}")
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.ACTION_UPDATED,
                {
                    "action_id": record.action_id,
                    "status": record.status.value,
                    "decision": None if record.decision is None else record.decision.value,
                },
            )
        return self.get_action(record.action_id)

    def create_action_execution(self, record: ActionExecutionRecord) -> ActionExecutionRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO action_executions (
                    execution_id, action_id, mission_id, adapter_mode, status,
                    before_artifact_refs_json, after_artifact_refs_json, created_at,
                    updated_at, completed_at, result_json, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.execution_id,
                    record.action_id,
                    record.mission_id,
                    record.adapter_mode,
                    record.status.value,
                    _dump(record.before_artifact_refs),
                    _dump(record.after_artifact_refs),
                    record.created_at,
                    record.updated_at,
                    record.completed_at,
                    _dump(record.result),
                    _dump(record.error) if record.error is not None else None,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.ACTION_EXECUTION_CREATED,
                {"execution_id": record.execution_id, "action_id": record.action_id, "adapter_mode": record.adapter_mode},
            )
        return record

    def get_action_execution(self, execution_id: str) -> ActionExecutionRecord:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM action_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"action execution not found: {execution_id}")
        return _action_execution_from_row(row)

    def complete_action_execution(
        self,
        execution_id: str,
        status: ActionExecutionStatus,
        *,
        result: dict | None = None,
        error: dict | None = None,
    ) -> ActionExecutionRecord:
        updated = self.get_action_execution(execution_id).with_completion(status, result=result, error=error)
        with self._connect() as db:
            db.execute(
                """
                UPDATE action_executions
                SET status = ?, updated_at = ?, completed_at = ?, result_json = ?, error_json = ?
                WHERE execution_id = ?
                """,
                (
                    updated.status.value,
                    updated.updated_at,
                    updated.completed_at,
                    _dump(updated.result),
                    _dump(updated.error) if updated.error is not None else None,
                    execution_id,
                ),
            )
            self._append_event_in_tx(
                db,
                updated.mission_id,
                EventType.ACTION_EXECUTION_UPDATED,
                {"execution_id": execution_id, "action_id": updated.action_id, "status": updated.status.value},
            )
        return updated

    def list_action_executions(self, action_id: str) -> list[ActionExecutionRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM action_executions WHERE action_id = ? ORDER BY created_at, execution_id",
                (action_id,),
            ).fetchall()
        return [_action_execution_from_row(row) for row in rows]

    def list_events(self, mission_id: str) -> list[EventRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE mission_id = ? ORDER BY sequence", (mission_id,)).fetchall()
        return [_event_from_row(row) for row in rows]

    def _append_event_in_tx(self, db: sqlite3.Connection, mission_id: str, event_type: EventType, payload: dict) -> EventRecord:
        row = db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE mission_id = ?", (mission_id,)).fetchone()
        sequence = int(row["next_sequence"])
        event = EventRecord(str(uuid4()), mission_id, event_type, sequence, utc_now_iso(), payload)
        db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (event.event_id, event.mission_id, event.event_type.value, event.sequence, event.created_at, _dump(event.payload)),
        )
        return event


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _load(value: str | None, default: object) -> object:
    if value is None:
        return default
    return json.loads(value)


def _mission_from_row(row: sqlite3.Row) -> MissionRecord:
    return MissionRecord(
        mission_id=row["mission_id"],
        user_goal=row["user_goal"],
        acceptance_criteria=list(_load(row["acceptance_criteria_json"], [])),
        constraints=[EngineeringConstraint(**item) for item in _load(row["constraints_json"], [])],
        state=MissionState(row["state"]),
        plan_version=row["plan_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        vision_required=bool(row["vision_required"]),
        final_outcome=_load(row["final_outcome_json"], None),
    )


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    error_payload = _load(row["error_json"], None)
    error = None
    if error_payload is not None:
        error = JobError(
            error_class=ErrorClass(error_payload["error_class"]),
            message=error_payload["message"],
            retryable=error_payload["retryable"],
            details=error_payload.get("details", {}),
        )
    return JobRecord(
        job_id=row["job_id"],
        mission_id=row["mission_id"],
        capability=row["capability"],
        idempotency_key=row["idempotency_key"],
        input_payload=dict(_load(row["input_payload_json"], {})),
        output_payload=dict(_load(row["output_payload_json"], {})),
        artifact_refs=list(_load(row["artifact_refs_json"], [])),
        timeout_seconds=row["timeout_seconds"],
        retry_limit=row["retry_limit"],
        status=JobStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error=error,
    )


def _event_from_row(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=row["event_id"],
        mission_id=row["mission_id"],
        event_type=EventType(row["event_type"]),
        sequence=row["sequence"],
        created_at=row["created_at"],
        payload=dict(_load(row["payload_json"], {})),
    )


def _approval_from_row(row: sqlite3.Row) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=row["approval_id"],
        mission_id=row["mission_id"],
        reason=row["reason"],
        options=list(_load(row["options_json"], [])),
        decision=ApprovalDecision(row["decision"]),
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        selected_option_id=row["selected_option_id"],
        comment=row["comment"],
    )


def _lease_from_row(row: sqlite3.Row) -> WorkerLease:
    return WorkerLease(
        lease_id=row["lease_id"],
        job_id=row["job_id"],
        worker_id=row["worker_id"],
        acquired_at=row["acquired_at"],
        expires_at=row["expires_at"],
        released_at=row["released_at"],
    )


def _graph_run_from_row(row: sqlite3.Row) -> GraphRunRecord:
    return GraphRunRecord(
        graph_run_id=row["graph_run_id"],
        mission_id=row["mission_id"],
        template_id=row["template_id"],
        template_version=row["template_version"],
        plan_version=row["plan_version"],
        status=GraphRunStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        current_node_id=row["current_node_id"],
        error=_load(row["error_json"], None),
    )


def _node_run_from_row(row: sqlite3.Row) -> NodeRunRecord:
    return NodeRunRecord(
        node_run_id=row["node_run_id"],
        graph_run_id=row["graph_run_id"],
        mission_id=row["mission_id"],
        node_id=row["node_id"],
        node_role=row["node_role"],
        node_kind=row["node_kind"],
        sequence=row["sequence"],
        status=NodeRunStatus(row["status"]),
        input_payload=dict(_load(row["input_payload_json"], {})),
        output_payload=dict(_load(row["output_payload_json"], {})),
        artifact_refs=list(_load(row["artifact_refs_json"], [])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        evidence_package_id=row["evidence_package_id"],
        edge_decision=row["edge_decision"],
        error=_load(row["error_json"], None),
    )


def _artifact_manifest_from_row(row: sqlite3.Row) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=row["artifact_id"],
        mission_id=row["mission_id"],
        producer_kind=row["producer_kind"],
        producer_id=row["producer_id"],
        path=row["path"],
        kind=row["kind"],
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        created_at=row["created_at"],
        metadata=dict(_load(row["metadata_json"], {})),
        retention_policy=row["retention_policy"],
    )


def _evidence_package_from_row(row: sqlite3.Row) -> EvidencePackage:
    return EvidencePackage(
        evidence_package_id=row["evidence_package_id"],
        mission_id=row["mission_id"],
        producer_kind=row["producer_kind"],
        producer_id=row["producer_id"],
        summary=dict(_load(row["summary_json"], {})),
        artifact_refs=list(_load(row["artifact_refs_json"], [])),
        token_budget=dict(_load(row["token_budget_json"], {})),
        created_at=row["created_at"],
        metadata=dict(_load(row["metadata_json"], {})),
    )


def _job_attempt_from_row(row: sqlite3.Row) -> JobAttemptRecord:
    return JobAttemptRecord(
        attempt_id=row["attempt_id"],
        mission_id=row["mission_id"],
        job_id=row["job_id"],
        attempt_number=row["attempt_number"],
        worker_id=row["worker_id"],
        status=JobAttemptStatus(row["status"]),
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        error=_load(row["error_json"], None),
        retry_decision=row["retry_decision"],
    )


def _action_from_row(row: sqlite3.Row) -> ActionRecord:
    decision = row["decision"]
    return ActionRecord(
        action_id=row["action_id"],
        mission_id=row["mission_id"],
        action_type=row["action_type"],
        version=row["version"],
        status=ActionStatus(row["status"]),
        target=dict(_load(row["target_json"], {})),
        parameters=dict(_load(row["parameters_json"], {})),
        constraints=dict(_load(row["constraints_json"], {})),
        reason=dict(_load(row["reason_json"], {})),
        adapter_mode=row["adapter_mode"],
        adapter_input=dict(_load(row["adapter_input_json"], {})),
        digest=row["digest"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        approval_id=row["approval_id"],
        comparison=_load(row["comparison_json"], None),
        decision=None if decision is None else ActionDecision(decision),
        error=_load(row["error_json"], None),
    )


def _action_execution_from_row(row: sqlite3.Row) -> ActionExecutionRecord:
    return ActionExecutionRecord(
        execution_id=row["execution_id"],
        action_id=row["action_id"],
        mission_id=row["mission_id"],
        adapter_mode=row["adapter_mode"],
        status=ActionExecutionStatus(row["status"]),
        before_artifact_refs=list(_load(row["before_artifact_refs_json"], [])),
        after_artifact_refs=list(_load(row["after_artifact_refs_json"], [])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        result=dict(_load(row["result_json"], {})),
        error=_load(row["error_json"], None),
    )


def _mission_loop_from_row(row: sqlite3.Row) -> "MissionLoopRecord":
    from aedt_agent.agent.orchestrator.loop_contracts import (
        LoopDecisionType,
        MissionLoopRecord,
        MissionLoopStatus,
    )
    from aedt_agent.agent.policies import ExecutionProfile

    last_decision = row["last_decision"]
    return MissionLoopRecord(
        loop_id=row["loop_id"],
        mission_id=row["mission_id"],
        profile=ExecutionProfile.from_json_dict(dict(_load(row["profile_json"], {}))),
        status=MissionLoopStatus(row["status"]),
        iteration_count=row["iteration_count"],
        job_attempt_count=row["job_attempt_count"],
        evidence_query_calls=row["evidence_query_calls"],
        evidence_tokens=row["evidence_tokens"],
        duplicate_action_count=row["duplicate_action_count"],
        consecutive_no_improvement=row["consecutive_no_improvement"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        last_decision=None if last_decision is None else LoopDecisionType(last_decision),
        last_reason=row["last_reason"],
        last_job_id=row["last_job_id"],
        retry_not_before=row["retry_not_before"],
    )
