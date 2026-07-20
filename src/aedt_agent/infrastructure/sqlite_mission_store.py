from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
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
    GraphHandoffRecord,
    GraphHandoffStatus,
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


class JobExecutionConflictError(RuntimeError):
    def __init__(
        self,
        job_id: str,
        operation: str,
        lease_id: str | None,
        current_status: str,
        active_lease_ids: list[str],
    ):
        requested_fence = lease_id or "unleased"
        active_fences = ", ".join(active_lease_ids) or "none"
        super().__init__(
            f"job execution conflict during {operation}: job={job_id}, "
            f"requested_lease={requested_fence}, status={current_status}, "
            f"active_leases={active_fences}"
        )
        self.job_id = job_id
        self.operation = operation
        self.lease_id = lease_id
        self.current_status = current_status
        self.active_lease_ids = active_lease_ids


class _GraphInterventionRejected(RuntimeError):
    def __init__(self, code: str, message: str, **details: object):
        super().__init__(message)
        self.error = {
            "code": code,
            "message": message,
            "details": details,
        }


class SQLiteMissionStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
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
                    error_json TEXT,
                    template_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    initial_payload_json TEXT NOT NULL DEFAULT '{}',
                    step_count INTEGER NOT NULL DEFAULT 0,
                    max_steps INTEGER NOT NULL DEFAULT 32
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
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    lease_id TEXT REFERENCES worker_leases(lease_id),
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
                CREATE TABLE IF NOT EXISTS graph_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    graph_run_id TEXT NOT NULL REFERENCES graph_runs(graph_run_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    edge_id TEXT NOT NULL,
                    source_node_run_id TEXT NOT NULL,
                    from_node TEXT NOT NULL,
                    to_node TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    consumed_by_node_run_id TEXT
                );
                CREATE TABLE IF NOT EXISTS graph_node_jobs (
                    graph_run_id TEXT NOT NULL REFERENCES graph_runs(graph_run_id),
                    node_id TEXT NOT NULL,
                    run_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(graph_run_id, node_id, run_index),
                    UNIQUE(graph_run_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS graph_interventions (
                    intervention_id TEXT PRIMARY KEY,
                    graph_run_id TEXT NOT NULL REFERENCES graph_runs(graph_run_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    action TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    expected_cursor INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(graph_run_id, idempotency_key)
                );
                """
            )
            self._ensure_column(db, "graph_runs", "template_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "graph_runs", "initial_payload_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "graph_runs", "step_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "graph_runs", "max_steps", "INTEGER NOT NULL DEFAULT 32")
            self._ensure_column(db, "job_attempts", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "job_attempts", "lease_id", "TEXT REFERENCES worker_leases(lease_id)")
            db.execute(
                """
                UPDATE job_attempts
                SET lease_id = (
                    SELECT worker_leases.lease_id
                    FROM worker_leases
                    WHERE worker_leases.job_id = job_attempts.job_id
                      AND worker_leases.worker_id = job_attempts.worker_id
                      AND worker_leases.released_at IS NULL
                    ORDER BY worker_leases.acquired_at, worker_leases.lease_id
                    LIMIT 1
                )
                WHERE lease_id IS NULL AND status = ?
                  AND 1 = (
                      SELECT COUNT(*)
                      FROM worker_leases
                      WHERE worker_leases.job_id = job_attempts.job_id
                        AND worker_leases.worker_id = job_attempts.worker_id
                        AND worker_leases.released_at IS NULL
                  )
                """,
                (JobAttemptStatus.RUNNING.value,),
            )

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

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

    def list_missions(self, limit: int = 50) -> list[MissionRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM missions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_mission_from_row(row) for row in rows]

    def update_mission_state(self, mission_id: str, state: MissionState) -> MissionRecord:
        from aedt_agent.agent.orchestrator.state_machine import assert_transition

        now = utc_now_iso()
        with self._connect() as db:
            # Read current state within the same transaction to avoid TOCTOU
            row = db.execute("SELECT state FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
            if row is None:
                raise KeyError(f"mission not found: {mission_id}")
            current = MissionState(row["state"])
            if current == state:
                cursor = db.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,))
                return _mission_from_row(cursor.fetchone())
            assert_transition(current, state)
            # Atomic UPDATE with state precondition prevents races
            cursor = db.execute(
                "UPDATE missions SET state = ?, updated_at = ? WHERE mission_id = ? AND state = ?",
                (state.value, now, mission_id, current.value),
            )
            if cursor.rowcount != 1:
                latest_row = db.execute(
                    "SELECT * FROM missions WHERE mission_id = ?",
                    (mission_id,),
                ).fetchone()
                if latest_row is not None and MissionState(latest_row["state"]) == state:
                    return _mission_from_row(latest_row)
                raise RuntimeError(f"concurrent modification detected for mission {mission_id}: expected state {current.value}")
            self._append_event_in_tx(db, mission_id, EventType.MISSION_STATE_CHANGED, {"state": state.value})
            cursor = db.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,))
        return _mission_from_row(cursor.fetchone())

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
        import sqlite3 as _sqlite3

        job = JobRecord.create(str(uuid4()), mission_id, capability, idempotency_key, input_payload, timeout_seconds, retry_limit)
        with self._connect() as db:
            try:
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
            except _sqlite3.IntegrityError:
                # Race: another thread inserted the same idempotency_key
                return self.get_job_by_idempotency_key(mission_id, idempotency_key)
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

    @staticmethod
    def _job_execution_fence_sql(lease_id: str | None) -> tuple[str, tuple[str, ...]]:
        if lease_id is None:
            return (
                """
                jobs.status = ?
                AND NOT EXISTS (
                    SELECT 1 FROM worker_leases
                    WHERE worker_leases.job_id = jobs.job_id
                )
                """,
                (JobStatus.QUEUED.value,),
            )
        return (
            """
            jobs.status = ?
            AND EXISTS (
                SELECT 1 FROM worker_leases AS requested_lease
                WHERE requested_lease.lease_id = ?
                  AND requested_lease.job_id = jobs.job_id
                  AND requested_lease.released_at IS NULL
            )
            AND NOT EXISTS (
                SELECT 1 FROM worker_leases AS other_lease
                WHERE other_lease.job_id = jobs.job_id
                  AND other_lease.released_at IS NULL
                  AND other_lease.lease_id <> ?
            )
            """,
            (JobStatus.LEASED.value, lease_id, lease_id),
        )

    @staticmethod
    def _raise_job_execution_conflict_in_tx(
        db: sqlite3.Connection,
        job_id: str,
        operation: str,
        lease_id: str | None,
    ) -> None:
        job_row = db.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if job_row is None:
            raise KeyError(f"job not found: {job_id}")
        lease_rows = db.execute(
            """
            SELECT lease_id FROM worker_leases
            WHERE job_id = ? AND released_at IS NULL
            ORDER BY acquired_at, lease_id
            """,
            (job_id,),
        ).fetchall()
        raise JobExecutionConflictError(
            job_id=job_id,
            operation=operation,
            lease_id=lease_id,
            current_status=job_row["status"],
            active_lease_ids=[row["lease_id"] for row in lease_rows],
        )

    def complete_job(
        self,
        job_id: str,
        output_payload: dict,
        artifact_refs: list[str],
        *,
        lease_id: str | None = None,
    ) -> JobRecord:
        now = utc_now_iso()
        fence_sql, fence_params = self._job_execution_fence_sql(lease_id)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                UPDATE jobs
                SET status = ?, output_payload_json = ?, artifact_refs_json = ?, updated_at = ?, error_json = NULL
                WHERE job_id = ? AND {fence_sql}
                """,
                (
                    JobStatus.SUCCEEDED.value,
                    _dump(output_payload),
                    _dump(artifact_refs),
                    now,
                    job_id,
                    *fence_params,
                ),
            )
            if cursor.rowcount != 1:
                self._raise_job_execution_conflict_in_tx(
                    db, job_id, "complete", lease_id
                )
            job_row = db.execute(
                "SELECT mission_id FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            self._append_event_in_tx(
                db,
                job_row["mission_id"],
                EventType.JOB_SUCCEEDED,
                {"job_id": job_id, "lease_id": lease_id},
            )
        return self.get_job(job_id)

    def fail_job(
        self,
        job_id: str,
        error: JobError,
        *,
        lease_id: str | None = None,
    ) -> JobRecord:
        now = utc_now_iso()
        fence_sql, fence_params = self._job_execution_fence_sql(lease_id)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                UPDATE jobs SET status = ?, updated_at = ?, error_json = ?
                WHERE job_id = ? AND {fence_sql}
                """,
                (
                    JobStatus.FAILED.value,
                    now,
                    _dump(error.to_json_dict()),
                    job_id,
                    *fence_params,
                ),
            )
            if cursor.rowcount != 1:
                self._raise_job_execution_conflict_in_tx(db, job_id, "fail", lease_id)
            job_row = db.execute(
                "SELECT mission_id FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            self._append_event_in_tx(
                db,
                job_row["mission_id"],
                EventType.JOB_FAILED,
                {"job_id": job_id, "lease_id": lease_id, "error": error.to_json_dict()},
            )
        return self.get_job(job_id)

    def cancel_job(
        self,
        job_id: str,
        error: JobError,
        *,
        lease_id: str | None = None,
    ) -> JobRecord:
        now = utc_now_iso()
        fence_sql, fence_params = self._job_execution_fence_sql(lease_id)
        with self._connect() as db:
            cursor = db.execute(
                f"""
                UPDATE jobs SET status = ?, updated_at = ?, error_json = ?
                WHERE job_id = ? AND {fence_sql}
                """,
                (
                    JobStatus.CANCELED.value,
                    now,
                    _dump(error.to_json_dict()),
                    job_id,
                    *fence_params,
                ),
            )
            if cursor.rowcount != 1:
                self._raise_job_execution_conflict_in_tx(
                    db, job_id, "cancel", lease_id
                )
            job_row = db.execute(
                "SELECT mission_id FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            self._append_event_in_tx(
                db,
                job_row["mission_id"],
                EventType.JOB_CANCELED,
                {"job_id": job_id, "lease_id": lease_id, "error": error.to_json_dict()},
            )
        return self.get_job(job_id)

    def requeue_failed_job(self, job_id: str) -> JobRecord:
        job = self.get_job(job_id)
        if job.status != JobStatus.FAILED:
            raise ValueError(f"only failed jobs can be requeued: {job.status.value}")
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error_json = NULL WHERE job_id = ?",
                (JobStatus.QUEUED.value, now, job_id),
            )
            self._append_event_in_tx(
                db,
                job.mission_id,
                EventType.JOB_REQUEUED,
                {"job_id": job_id},
            )
        return self.get_job(job_id)

    def acquire_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
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
            # Atomic UPDATE with status precondition prevents double-lease
            cursor = db.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ? AND status = ?",
                (JobStatus.LEASED.value, utc_now_iso(), job_id, JobStatus.QUEUED.value),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"job {job_id} is not queued or already leased")
            # Fetch mission_id within same tx
            job_row = db.execute("SELECT mission_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            db.execute(
                "INSERT INTO worker_leases VALUES (?, ?, ?, ?, ?, ?)",
                (lease.lease_id, lease.job_id, lease.worker_id, lease.acquired_at, lease.expires_at, lease.released_at),
            )
            self._append_event_in_tx(
                db,
                job_row["mission_id"],
                EventType.JOB_LEASED,
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "lease_id": lease.lease_id,
                },
            )
        return lease

    def release_job_lease(self, lease_id: str) -> WorkerLease:
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                """
                UPDATE worker_leases SET released_at = ?
                WHERE lease_id = ? AND released_at IS NULL
                """,
                (now, lease_id),
            )
            row = db.execute("SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)).fetchone()
        if row is None:
            raise KeyError(f"lease not found: {lease_id}")
        return _lease_from_row(row)

    def list_active_job_leases(self, job_id: str) -> list[WorkerLease]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM worker_leases
                WHERE job_id = ? AND released_at IS NULL
                ORDER BY acquired_at, lease_id
                """,
                (job_id,),
            ).fetchall()
        return [_lease_from_row(row) for row in rows]

    def release_active_job_leases(self, job_id: str) -> list[WorkerLease]:
        leases = self.list_active_job_leases(job_id)
        return [self.release_job_lease(lease.lease_id) for lease in leases]

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
            job_ids: list[str] = []
            for row in rows:
                cursor = db.execute(
                    """
                    UPDATE jobs SET status = ?, updated_at = ?
                    WHERE job_id = ? AND status = ?
                      AND EXISTS (
                          SELECT 1 FROM worker_leases
                          WHERE worker_leases.lease_id = ?
                            AND worker_leases.job_id = jobs.job_id
                            AND worker_leases.released_at IS NULL
                            AND worker_leases.expires_at < ?
                      )
                    """,
                    (
                        JobStatus.QUEUED.value,
                        utc_now_iso(),
                        row["job_id"],
                        JobStatus.LEASED.value,
                        row["lease_id"],
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                db.execute(
                    """
                    UPDATE worker_leases SET released_at = ?
                    WHERE lease_id = ? AND released_at IS NULL
                    """,
                    (now.isoformat(), row["lease_id"]),
                )
                job_ids.append(row["job_id"])
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
            if ApprovalDecision(row["decision"]) != ApprovalDecision.PENDING:
                raise ValueError(f"approval already resolved: {approval_id}")
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

    def list_approvals(
        self,
        mission_id: str,
        *,
        decision: ApprovalDecision | None = None,
    ) -> list[ApprovalRequest]:
        if decision is None:
            query = "SELECT * FROM approvals WHERE mission_id = ? ORDER BY created_at, approval_id"
            params = (mission_id,)
        else:
            query = (
                "SELECT * FROM approvals WHERE mission_id = ? AND decision = ? "
                "ORDER BY created_at, approval_id"
            )
            params = (mission_id, decision.value)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [_approval_from_row(row) for row in rows]

    def create_graph_run(self, record: GraphRunRecord) -> GraphRunRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO graph_runs (
                    graph_run_id, mission_id, template_id, template_version, plan_version,
                    status, created_at, updated_at, started_at, completed_at, current_node_id,
                    error_json, template_snapshot_json, initial_payload_json, step_count, max_steps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _dump(record.template_snapshot),
                    _dump(record.initial_payload),
                    record.step_count,
                    record.max_steps,
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

    def increment_graph_step(self, graph_run_id: str) -> GraphRunRecord:
        now = utc_now_iso()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE graph_runs SET step_count = step_count + 1, updated_at = ? WHERE graph_run_id = ?",
                (now, graph_run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"graph run not found: {graph_run_id}")
            self._append_event_in_tx(
                db,
                self.get_graph_run(graph_run_id).mission_id,
                EventType.GRAPH_STEP_ADVANCED,
                {"graph_run_id": graph_run_id},
            )
        return self.get_graph_run(graph_run_id)

    def update_graph_run_snapshot(self, graph_run_id: str, snapshot: dict) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE graph_runs SET template_snapshot_json = ? WHERE graph_run_id = ?",
                (_dump(snapshot), graph_run_id),
            )

    def create_graph_handoff(self, record: GraphHandoffRecord) -> GraphHandoffRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO graph_handoffs (
                    handoff_id, graph_run_id, mission_id, edge_id, source_node_run_id,
                    from_node, to_node, outcome, payload_json, status, created_at,
                    consumed_at, consumed_by_node_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.handoff_id,
                    record.graph_run_id,
                    record.mission_id,
                    record.edge_id,
                    record.source_node_run_id,
                    record.from_node,
                    record.to_node,
                    record.outcome,
                    _dump(record.payload),
                    record.status.value,
                    record.created_at,
                    record.consumed_at,
                    record.consumed_by_node_run_id,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.GRAPH_HANDOFF_CREATED,
                {
                    "handoff_id": record.handoff_id,
                    "graph_run_id": record.graph_run_id,
                    "edge_id": record.edge_id,
                },
            )
        return record

    def list_graph_handoffs(
        self,
        graph_run_id: str,
        *,
        status: GraphHandoffStatus | None = None,
        to_node: str | None = None,
    ) -> list[GraphHandoffRecord]:
        clauses = ["graph_run_id = ?"]
        params: list[object] = [graph_run_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if to_node is not None:
            clauses.append("to_node = ?")
            params.append(to_node)
        query = (
            "SELECT * FROM graph_handoffs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, handoff_id"
        )
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [_graph_handoff_from_row(row) for row in rows]

    def consume_graph_handoffs(
        self,
        handoff_ids: list[str],
        node_run_id: str,
    ) -> list[GraphHandoffRecord]:
        if not handoff_ids:
            return []
        consumed: list[GraphHandoffRecord] = []
        with self._connect() as db:
            for handoff_id in handoff_ids:
                row = db.execute(
                    "SELECT * FROM graph_handoffs WHERE handoff_id = ?",
                    (handoff_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"graph handoff not found: {handoff_id}")
                record = _graph_handoff_from_row(row).with_consumption(node_run_id)
                db.execute(
                    """
                    UPDATE graph_handoffs
                    SET status = ?, consumed_at = ?, consumed_by_node_run_id = ?
                    WHERE handoff_id = ?
                    """,
                    (
                        record.status.value,
                        record.consumed_at,
                        record.consumed_by_node_run_id,
                        handoff_id,
                    ),
                )
                self._append_event_in_tx(
                    db,
                    record.mission_id,
                    EventType.GRAPH_HANDOFF_CONSUMED,
                    {"handoff_id": handoff_id, "node_run_id": node_run_id},
                )
                consumed.append(record)
        return consumed

    def bind_graph_node_job(
        self,
        graph_run_id: str,
        node_id: str,
        run_index: int,
        job_id: str,
    ) -> str:
        graph_run = self.get_graph_run(graph_run_id)
        if graph_run is None:
            raise KeyError(f"graph run not found: {graph_run_id}")
        with self._connect() as db:
            db.execute(
                "INSERT INTO graph_node_jobs VALUES (?, ?, ?, ?, ?)",
                (graph_run_id, node_id, run_index, job_id, utc_now_iso()),
            )
            self._append_event_in_tx(
                db,
                graph_run.mission_id,
                EventType.GRAPH_NODE_JOB_BOUND,
                {
                    "graph_run_id": graph_run_id,
                    "node_id": node_id,
                    "run_index": run_index,
                    "job_id": job_id,
                },
            )
        return job_id

    def get_graph_node_job(self, graph_run_id: str, node_id: str, run_index: int) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT job_id FROM graph_node_jobs
                WHERE graph_run_id = ? AND node_id = ? AND run_index = ?
                """,
                (graph_run_id, node_id, run_index),
            ).fetchone()
        return None if row is None else str(row["job_id"])

    def unbind_graph_node_job(self, graph_run_id: str, node_id: str, run_index: int) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM graph_node_jobs WHERE graph_run_id = ? AND node_id = ? AND run_index = ?",
                (graph_run_id, node_id, run_index),
            )

    def list_graph_bound_job_ids(self, graph_run_id: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT job_id FROM graph_node_jobs WHERE graph_run_id = ? ORDER BY created_at, job_id",
                (graph_run_id,),
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def get_graph_intervention(self, intervention_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM graph_interventions WHERE intervention_id = ?",
                (intervention_id,),
            ).fetchone()
        return None if row is None else _graph_intervention_from_row(row)

    def list_graph_interventions(self, graph_run_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM graph_interventions
                WHERE graph_run_id = ?
                ORDER BY created_at, intervention_id
                """,
                (graph_run_id,),
            ).fetchall()
        return [_graph_intervention_from_row(row) for row in rows]

    def intervene_graph(
        self,
        *,
        graph_run_id: str,
        action: str,
        node_id: str,
        expected_event_cursor: int,
        idempotency_key: str,
        reason: str,
    ) -> dict:
        request = {
            "graph_run_id": graph_run_id,
            "action": action,
            "node_id": node_id,
            "expected_cursor": expected_event_cursor,
            "idempotency_key": idempotency_key,
            "reason": reason,
        }
        if action not in {"retry-node", "cancel-branch"}:
            return _intervention_error_response(
                "invalid_action",
                f"unsupported graph intervention action: {action}",
                action=action,
            )
        if expected_event_cursor < 0:
            return _intervention_error_response(
                "invalid_event_cursor",
                "expected event cursor must be non-negative",
                expected=expected_event_cursor,
            )
        if not idempotency_key.strip():
            return _intervention_error_response(
                "invalid_idempotency_key",
                "idempotency key must not be empty",
            )
        if not reason.strip():
            return _intervention_error_response(
                "invalid_reason",
                "intervention reason must not be empty",
            )

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """
                SELECT * FROM graph_interventions
                WHERE graph_run_id = ? AND idempotency_key = ?
                """,
                (graph_run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                existing_record = _graph_intervention_from_row(existing)
                if _same_intervention_request(existing_record, request):
                    return _intervention_record_response(
                        existing_record,
                        idempotent_replay=True,
                    )
                return _intervention_error_response(
                    "idempotency_key_conflict",
                    "idempotency key is already bound to a different request",
                    existing_intervention_id=existing_record["intervention_id"],
                    existing_request={
                        key: existing_record[key]
                        for key in (
                            "graph_run_id",
                            "action",
                            "node_id",
                            "expected_cursor",
                            "idempotency_key",
                            "reason",
                        )
                    },
                    requested=request,
                )

            graph_row = db.execute(
                "SELECT * FROM graph_runs WHERE graph_run_id = ?",
                (graph_run_id,),
            ).fetchone()
            if graph_row is None:
                return _intervention_error_response(
                    "graph_run_not_found",
                    f"graph run not found: {graph_run_id}",
                    graph_run_id=graph_run_id,
                )
            mission_row = db.execute(
                "SELECT * FROM missions WHERE mission_id = ?",
                (graph_row["mission_id"],),
            ).fetchone()
            if mission_row is None:
                return _intervention_error_response(
                    "mission_not_found",
                    f"mission not found: {graph_row['mission_id']}",
                    mission_id=graph_row["mission_id"],
                )

            mission_cursor = int(
                db.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) AS cursor
                    FROM events WHERE mission_id = ?
                    """,
                    (graph_row["mission_id"],),
                ).fetchone()["cursor"]
            )
            control_cursor = _graph_control_event_cursor_in_tx(
                db,
                graph_run_id=graph_run_id,
                mission_id=graph_row["mission_id"],
            )
            intervention_id = str(uuid4())
            now = utc_now_iso()
            if (
                expected_event_cursor < control_cursor
                or expected_event_cursor > mission_cursor
            ):
                conflict_reason = (
                    "behind_graph_control_state"
                    if expected_event_cursor < control_cursor
                    else "ahead_of_mission"
                )
                error = {
                    "code": "stale_event_cursor",
                    "message": "event cursor does not cover the current graph control state",
                    "details": {
                        "expected": expected_event_cursor,
                        "actual": control_cursor,
                        "control_cursor": control_cursor,
                        "mission_cursor": mission_cursor,
                        "cursor_scope": "graph_control",
                        "conflict_reason": conflict_reason,
                    },
                }
                self._insert_graph_intervention_in_tx(
                    db,
                    intervention_id=intervention_id,
                    mission_id=graph_row["mission_id"],
                    request=request,
                    status="rejected",
                    error=error,
                    now=now,
                )
                row = db.execute(
                    "SELECT * FROM graph_interventions WHERE intervention_id = ?",
                    (intervention_id,),
                ).fetchone()
                return _intervention_record_response(
                    _graph_intervention_from_row(row),
                    idempotent_replay=False,
                )

            self._insert_graph_intervention_in_tx(
                db,
                intervention_id=intervention_id,
                mission_id=graph_row["mission_id"],
                request=request,
                status="created",
                now=now,
            )
            self._append_event_in_tx(
                db,
                graph_row["mission_id"],
                EventType.GRAPH_INTERVENTION_CREATED,
                {
                    "graph_run_id": graph_run_id,
                    "intervention_id": intervention_id,
                    "action": action,
                    "node_id": node_id,
                    "idempotency_key": idempotency_key,
                    "expected_cursor": expected_event_cursor,
                },
            )

            db.execute("SAVEPOINT graph_intervention_apply")
            try:
                snapshot, node = self._validate_intervention_target_in_tx(
                    db,
                    graph_row=graph_row,
                    mission_row=mission_row,
                    node_id=node_id,
                )
                if action == "retry-node":
                    result = self._retry_node_intervention_in_tx(
                        db,
                        graph_row=graph_row,
                        snapshot=snapshot,
                        node=node,
                        intervention_id=intervention_id,
                        reason=reason,
                    )
                else:
                    result = self._cancel_branch_intervention_in_tx(
                        db,
                        graph_row=graph_row,
                        snapshot=snapshot,
                        node=node,
                        intervention_id=intervention_id,
                        reason=reason,
                    )
            except _GraphInterventionRejected as exc:
                db.execute("ROLLBACK TO graph_intervention_apply")
                db.execute("RELEASE graph_intervention_apply")
                rejected_event = self._append_event_in_tx(
                    db,
                    graph_row["mission_id"],
                    EventType.GRAPH_INTERVENTION_REJECTED,
                    {
                        "graph_run_id": graph_run_id,
                        "intervention_id": intervention_id,
                        "action": action,
                        "node_id": node_id,
                        "error": exc.error,
                    },
                )
                error = {
                    **exc.error,
                    "details": {
                        **exc.error.get("details", {}),
                        "event_cursor": rejected_event.sequence,
                    },
                }
                self._finish_graph_intervention_in_tx(
                    db,
                    intervention_id,
                    status="rejected",
                    error=error,
                )
            else:
                db.execute("RELEASE graph_intervention_apply")
                applied_event = self._append_event_in_tx(
                    db,
                    graph_row["mission_id"],
                    EventType.GRAPH_INTERVENTION_APPLIED,
                    {
                        "graph_run_id": graph_run_id,
                        "intervention_id": intervention_id,
                        "action": action,
                        "node_id": node_id,
                        "result": result,
                    },
                )
                result = {**result, "event_cursor": applied_event.sequence}
                self._finish_graph_intervention_in_tx(
                    db,
                    intervention_id,
                    status="applied",
                    result=result,
                )

            row = db.execute(
                "SELECT * FROM graph_interventions WHERE intervention_id = ?",
                (intervention_id,),
            ).fetchone()
            return _intervention_record_response(
                _graph_intervention_from_row(row),
                idempotent_replay=False,
            )

    @staticmethod
    def _insert_graph_intervention_in_tx(
        db: sqlite3.Connection,
        *,
        intervention_id: str,
        mission_id: str,
        request: dict,
        status: str,
        now: str,
        result: dict | None = None,
        error: dict | None = None,
    ) -> None:
        db.execute(
            """
            INSERT INTO graph_interventions (
                intervention_id, graph_run_id, mission_id, action, node_id,
                expected_cursor, idempotency_key, reason, status, result_json,
                error_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intervention_id,
                request["graph_run_id"],
                mission_id,
                request["action"],
                request["node_id"],
                request["expected_cursor"],
                request["idempotency_key"],
                request["reason"],
                status,
                _dump(result) if result is not None else None,
                _dump(error) if error is not None else None,
                now,
                now,
            ),
        )

    @staticmethod
    def _finish_graph_intervention_in_tx(
        db: sqlite3.Connection,
        intervention_id: str,
        *,
        status: str,
        result: dict | None = None,
        error: dict | None = None,
    ) -> None:
        db.execute(
            """
            UPDATE graph_interventions
            SET status = ?, result_json = ?, error_json = ?, updated_at = ?
            WHERE intervention_id = ?
            """,
            (
                status,
                _dump(result) if result is not None else None,
                _dump(error) if error is not None else None,
                utc_now_iso(),
                intervention_id,
            ),
        )

    def _validate_intervention_target_in_tx(
        self,
        db: sqlite3.Connection,
        *,
        graph_row: sqlite3.Row,
        mission_row: sqlite3.Row,
        node_id: str,
    ) -> tuple[dict, dict]:
        if graph_row["status"] != GraphRunStatus.RUNNING.value:
            raise _GraphInterventionRejected(
                "graph_not_running",
                "graph intervention requires a running graph",
                status=graph_row["status"],
            )
        if mission_row["state"] in {
            MissionState.COMPLETED.value,
            MissionState.FAILED.value,
            MissionState.CANCELED.value,
        }:
            raise _GraphInterventionRejected(
                "mission_terminal",
                "graph intervention requires a non-terminal mission",
                state=mission_row["state"],
            )
        snapshot = dict(_load(graph_row["template_snapshot_json"], {}))
        nodes = [item for item in snapshot.get("nodes", []) if isinstance(item, dict)]
        node = next((item for item in nodes if item.get("id") == node_id), None)
        if node is None:
            raise _GraphInterventionRejected(
                "node_not_found",
                f"node is not present in graph snapshot: {node_id}",
                node_id=node_id,
            )
        return snapshot, node

    def _retry_node_intervention_in_tx(
        self,
        db: sqlite3.Connection,
        *,
        graph_row: sqlite3.Row,
        snapshot: dict,
        node: dict,
        intervention_id: str,
        reason: str,
    ) -> dict:
        graph_run_id = graph_row["graph_run_id"]
        node_id = str(node["id"])
        node_rows = db.execute(
            """
            SELECT * FROM node_runs
            WHERE graph_run_id = ? AND node_id = ?
            ORDER BY sequence, created_at, node_run_id
            """,
            (graph_run_id, node_id),
        ).fetchall()
        if not node_rows:
            raise _GraphInterventionRejected(
                "retry_target_not_failed",
                "retry target has no failed node run",
                node_id=node_id,
            )
        active_rows = [
            row
            for row in node_rows
            if row["status"]
            in {
                NodeRunStatus.CREATED.value,
                NodeRunStatus.RUNNING.value,
                NodeRunStatus.WAITING_APPROVAL.value,
            }
        ]
        if active_rows:
            raise _GraphInterventionRejected(
                "target_node_active",
                "retry target has an active node run",
                node_run_ids=[row["node_run_id"] for row in active_rows],
            )
        latest = node_rows[-1]
        if latest["status"] != NodeRunStatus.FAILED.value:
            raise _GraphInterventionRejected(
                "retry_target_not_failed",
                "latest target node run is not failed",
                node_run_id=latest["node_run_id"],
                status=latest["status"],
            )
        active_jobs = self._active_graph_node_jobs_in_tx(db, graph_run_id, node_id)
        if active_jobs:
            raise _GraphInterventionRejected(
                "target_job_active",
                "retry target has an active bound job or lease",
                jobs=active_jobs,
            )

        edges = [item for item in snapshot.get("edges", []) if isinstance(item, dict)]
        failure_edge_ids = {
            str(edge.get("id"))
            for edge in edges
            if edge.get("from") == node_id
            and edge.get("on") in {"failed", "canceled", "rejected"}
        }
        handoff_rows = db.execute(
            """
            SELECT * FROM graph_handoffs
            WHERE graph_run_id = ? AND source_node_run_id = ?
            ORDER BY created_at, handoff_id
            """,
            (graph_run_id, latest["node_run_id"]),
        ).fetchall()
        failure_handoffs = [
            row
            for row in handoff_rows
            if row["edge_id"] in failure_edge_ids
            or row["outcome"] in {"failed", "canceled", "rejected"}
            or row["outcome"] == latest["edge_decision"]
        ]
        consumed_failure = [
            row for row in failure_handoffs if row["status"] != GraphHandoffStatus.PENDING.value
        ]
        if consumed_failure:
            raise _GraphInterventionRejected(
                "failure_branch_consumed",
                "failed node branch has already been consumed downstream",
                handoff_ids=[row["handoff_id"] for row in consumed_failure],
            )

        consumed_handoff_ids: list[str] = []
        for row in failure_handoffs:
            self._consume_graph_handoff_in_tx(
                db,
                row,
                consumed_by_node_run_id=f"intervention:{intervention_id}",
                graph_run_id=graph_run_id,
            )
            consumed_handoff_ids.append(row["handoff_id"])

        run_count = len(node_rows)
        max_runs = max(int(node.get("max_runs", 1)), run_count + 1)
        node["max_runs"] = max_runs
        incoming_edges = [edge for edge in edges if edge.get("to") == node_id]
        original_inputs = db.execute(
            """
            SELECT * FROM graph_handoffs
            WHERE graph_run_id = ? AND consumed_by_node_run_id = ? AND to_node = ?
            ORDER BY created_at, handoff_id
            """,
            (graph_run_id, latest["node_run_id"], node_id),
        ).fetchall()
        selected_edges = incoming_edges
        if node.get("join", "any") != "all" and incoming_edges:
            original_edge_ids = {row["edge_id"] for row in original_inputs}
            selected_edges = [
                next(
                    (
                        edge
                        for edge in incoming_edges
                        if str(edge.get("id")) in original_edge_ids
                    ),
                    incoming_edges[0],
                )
            ]

        input_payload = dict(_load(latest["input_payload_json"], {}))
        input_payload.pop("_handoffs", None)
        synthetic_handoff_ids: list[str] = []
        for edge in selected_edges:
            edge_id = str(edge.get("id"))
            original = next(
                (row for row in original_inputs if row["edge_id"] == edge_id),
                None,
            )
            handoff_id = str(uuid4())
            self._create_graph_handoff_in_tx(
                db,
                handoff_id=handoff_id,
                graph_run_id=graph_run_id,
                mission_id=graph_row["mission_id"],
                edge_id=edge_id,
                source_node_run_id=(
                    original["source_node_run_id"]
                    if original is not None
                    else latest["node_run_id"]
                ),
                from_node=str(edge.get("from")),
                to_node=node_id,
                outcome=str(edge.get("on", "")),
                payload=input_payload,
            )
            synthetic_handoff_ids.append(handoff_id)

        now = utc_now_iso()
        db.execute(
            """
            UPDATE graph_runs
            SET status = ?, current_node_id = ?, template_snapshot_json = ?,
                updated_at = ?, completed_at = NULL
            WHERE graph_run_id = ?
            """,
            (
                GraphRunStatus.RUNNING.value,
                node_id,
                _dump(snapshot),
                now,
                graph_run_id,
            ),
        )
        return {
            "action": "retry-node",
            "node_id": node_id,
            "failed_node_run_id": latest["node_run_id"],
            "consumed_failure_handoff_ids": consumed_handoff_ids,
            "synthetic_handoff_ids": synthetic_handoff_ids,
            "max_runs": max_runs,
            "reason": reason,
        }

    def _cancel_branch_intervention_in_tx(
        self,
        db: sqlite3.Connection,
        *,
        graph_row: sqlite3.Row,
        snapshot: dict,
        node: dict,
        intervention_id: str,
        reason: str,
    ) -> dict:
        graph_run_id = graph_row["graph_run_id"]
        node_id = str(node["id"])
        edges = [item for item in snapshot.get("edges", []) if isinstance(item, dict)]
        incoming_edges = [edge for edge in edges if edge.get("to") == node_id]
        if not incoming_edges:
            raise _GraphInterventionRejected(
                "root_cancel_forbidden",
                "root nodes cannot be canceled as a pending branch",
                node_id=node_id,
            )
        existing_runs = db.execute(
            "SELECT node_run_id, status FROM node_runs WHERE graph_run_id = ? AND node_id = ?",
            (graph_run_id, node_id),
        ).fetchall()
        if existing_runs:
            raise _GraphInterventionRejected(
                "cancel_target_already_run",
                "cancel target already has a node run",
                node_runs=[
                    {"node_run_id": row["node_run_id"], "status": row["status"]}
                    for row in existing_runs
                ],
            )
        active_jobs = self._active_graph_node_jobs_in_tx(db, graph_run_id, node_id)
        if active_jobs:
            raise _GraphInterventionRejected(
                "target_job_active",
                "cancel target has an active bound job or lease",
                jobs=active_jobs,
            )
        pending = db.execute(
            """
            SELECT * FROM graph_handoffs
            WHERE graph_run_id = ? AND to_node = ? AND status = ?
            ORDER BY created_at, handoff_id
            """,
            (graph_run_id, node_id, GraphHandoffStatus.PENDING.value),
        ).fetchall()
        if not pending:
            raise _GraphInterventionRejected(
                "cancel_target_not_pending",
                "cancel target has no pending inbound handoff",
                node_id=node_id,
            )
        unsafe = _unsafe_join_cancel_details(snapshot, node_id)
        if unsafe is not None:
            raise _GraphInterventionRejected(
                "unsafe_join_cancel",
                "cancel could permanently remove a source required by join=all",
                **unsafe,
            )

        input_payload = _merge_handoff_rows(pending)
        canceled_payload = {
            key: value for key, value in input_payload.items() if key != "_handoffs"
        }
        canceled_payload["_intervention"] = {
            "intervention_id": intervention_id,
            "action": "cancel-branch",
            "reason": reason,
        }
        node_run_id = str(uuid4())
        next_sequence = int(
            db.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM node_runs WHERE graph_run_id = ?
                """,
                (graph_run_id,),
            ).fetchone()["next_sequence"]
        )
        self._create_skipped_node_run_in_tx(
            db,
            node_run_id=node_run_id,
            graph_run_id=graph_run_id,
            mission_id=graph_row["mission_id"],
            node=node,
            sequence=next_sequence,
            input_payload=input_payload,
            output_payload=canceled_payload,
            intervention_id=intervention_id,
            reason=reason,
        )
        consumed_handoff_ids: list[str] = []
        for row in pending:
            self._consume_graph_handoff_in_tx(
                db,
                row,
                consumed_by_node_run_id=node_run_id,
                graph_run_id=graph_run_id,
            )
            consumed_handoff_ids.append(row["handoff_id"])

        downstream_handoff_ids: list[str] = []
        for edge in edges:
            if edge.get("from") != node_id or edge.get("on") != "canceled":
                continue
            handoff_id = str(uuid4())
            to_node = str(edge.get("to"))
            self._create_graph_handoff_in_tx(
                db,
                handoff_id=handoff_id,
                graph_run_id=graph_run_id,
                mission_id=graph_row["mission_id"],
                edge_id=str(edge.get("id")),
                source_node_run_id=node_run_id,
                from_node=node_id,
                to_node=to_node,
                outcome="canceled",
                payload=canceled_payload,
            )
            downstream_handoff_ids.append(handoff_id)

        pending_nodes = [
            row["to_node"]
            for row in db.execute(
                """
                SELECT DISTINCT to_node FROM graph_handoffs
                WHERE graph_run_id = ? AND status = ?
                ORDER BY to_node
                """,
                (graph_run_id, GraphHandoffStatus.PENDING.value),
            ).fetchall()
        ]

        db.execute(
            """
            UPDATE graph_runs
            SET status = ?, current_node_id = ?, updated_at = ?
            WHERE graph_run_id = ?
            """,
            (
                GraphRunStatus.RUNNING.value,
                ",".join(pending_nodes) or None,
                utc_now_iso(),
                graph_run_id,
            ),
        )
        return {
            "action": "cancel-branch",
            "node_id": node_id,
            "skipped_node_run_id": node_run_id,
            "consumed_handoff_ids": consumed_handoff_ids,
            "downstream_handoff_ids": downstream_handoff_ids,
            "reason": reason,
        }

    @staticmethod
    def _active_graph_node_jobs_in_tx(
        db: sqlite3.Connection,
        graph_run_id: str,
        node_id: str,
    ) -> list[dict]:
        rows = db.execute(
            """
            SELECT jobs.job_id, jobs.status
            FROM graph_node_jobs
            JOIN jobs ON jobs.job_id = graph_node_jobs.job_id
            WHERE graph_node_jobs.graph_run_id = ?
              AND graph_node_jobs.node_id = ?
              AND (
                  jobs.status IN (?, ?)
                  OR EXISTS (
                      SELECT 1 FROM worker_leases
                      WHERE worker_leases.job_id = jobs.job_id
                        AND worker_leases.released_at IS NULL
                  )
              )
            ORDER BY jobs.job_id
            """,
            (
                graph_run_id,
                node_id,
                JobStatus.QUEUED.value,
                JobStatus.LEASED.value,
            ),
        ).fetchall()
        active: list[dict] = []
        for row in rows:
            leases = db.execute(
                """
                SELECT lease_id FROM worker_leases
                WHERE job_id = ? AND released_at IS NULL
                ORDER BY acquired_at, lease_id
                """,
                (row["job_id"],),
            ).fetchall()
            active.append(
                {
                    "job_id": row["job_id"],
                    "status": row["status"],
                    "lease_ids": [lease["lease_id"] for lease in leases],
                }
            )
        return active

    def _consume_graph_handoff_in_tx(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        consumed_by_node_run_id: str,
        graph_run_id: str,
    ) -> None:
        now = utc_now_iso()
        cursor = db.execute(
            """
            UPDATE graph_handoffs
            SET status = ?, consumed_at = ?, consumed_by_node_run_id = ?
            WHERE handoff_id = ? AND status = ?
            """,
            (
                GraphHandoffStatus.CONSUMED.value,
                now,
                consumed_by_node_run_id,
                row["handoff_id"],
                GraphHandoffStatus.PENDING.value,
            ),
        )
        if cursor.rowcount != 1:
            raise _GraphInterventionRejected(
                "handoff_state_conflict",
                "pending handoff changed while applying intervention",
                handoff_id=row["handoff_id"],
            )
        self._append_event_in_tx(
            db,
            row["mission_id"],
            EventType.GRAPH_HANDOFF_CONSUMED,
            {
                "graph_run_id": graph_run_id,
                "handoff_id": row["handoff_id"],
                "node_run_id": consumed_by_node_run_id,
            },
        )

    def _create_graph_handoff_in_tx(
        self,
        db: sqlite3.Connection,
        *,
        handoff_id: str,
        graph_run_id: str,
        mission_id: str,
        edge_id: str,
        source_node_run_id: str,
        from_node: str,
        to_node: str,
        outcome: str,
        payload: dict,
    ) -> None:
        created_at = utc_now_iso()
        db.execute(
            """
            INSERT INTO graph_handoffs (
                handoff_id, graph_run_id, mission_id, edge_id, source_node_run_id,
                from_node, to_node, outcome, payload_json, status, created_at,
                consumed_at, consumed_by_node_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                handoff_id,
                graph_run_id,
                mission_id,
                edge_id,
                source_node_run_id,
                from_node,
                to_node,
                outcome,
                _dump(payload),
                GraphHandoffStatus.PENDING.value,
                created_at,
            ),
        )
        self._append_event_in_tx(
            db,
            mission_id,
            EventType.GRAPH_HANDOFF_CREATED,
            {
                "graph_run_id": graph_run_id,
                "handoff_id": handoff_id,
                "edge_id": edge_id,
                "synthetic": True,
            },
        )

    def _create_skipped_node_run_in_tx(
        self,
        db: sqlite3.Connection,
        *,
        node_run_id: str,
        graph_run_id: str,
        mission_id: str,
        node: dict,
        sequence: int,
        input_payload: dict,
        output_payload: dict,
        intervention_id: str,
        reason: str,
    ) -> None:
        now = utc_now_iso()
        error = {
            "error_class": "intervention_canceled",
            "message": reason,
            "intervention_id": intervention_id,
            "action": "cancel-branch",
        }
        db.execute(
            """
            INSERT INTO node_runs (
                node_run_id, graph_run_id, mission_id, node_id, node_role, node_kind,
                sequence, status, input_payload_json, output_payload_json, artifact_refs_json,
                created_at, updated_at, started_at, completed_at, evidence_package_id,
                edge_decision, error_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)
            """,
            (
                node_run_id,
                graph_run_id,
                mission_id,
                str(node["id"]),
                str(node.get("role", "")),
                str(node.get("kind", "")),
                sequence,
                NodeRunStatus.SKIPPED.value,
                _dump(input_payload),
                _dump(output_payload),
                _dump([]),
                now,
                now,
                now,
                "canceled",
                _dump(error),
            ),
        )
        self._append_event_in_tx(
            db,
            mission_id,
            EventType.NODE_RUN_CREATED,
            {
                "graph_run_id": graph_run_id,
                "node_run_id": node_run_id,
                "node_id": node["id"],
                "status": NodeRunStatus.SKIPPED.value,
                "intervention_id": intervention_id,
            },
        )
        self._append_event_in_tx(
            db,
            mission_id,
            EventType.NODE_RUN_UPDATED,
            {
                "graph_run_id": graph_run_id,
                "node_run_id": node_run_id,
                "status": NodeRunStatus.SKIPPED.value,
                "edge_decision": "canceled",
                "intervention_id": intervention_id,
            },
        )

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

    def update_node_run_status(
        self,
        node_run_id: str,
        status: NodeRunStatus,
        *,
        output_payload: dict | None = None,
        edge_decision: str | None = None,
        error: dict | None = None,
    ) -> NodeRunRecord:
        updated = self.get_node_run(node_run_id).with_progress(
            status,
            output_payload=output_payload,
            edge_decision=edge_decision,
            error=error,
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE node_runs
                SET status = ?, output_payload_json = ?, updated_at = ?, started_at = ?,
                    completed_at = NULL, edge_decision = ?, error_json = ?
                WHERE node_run_id = ?
                """,
                (
                    updated.status.value,
                    _dump(updated.output_payload),
                    updated.updated_at,
                    updated.started_at,
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
            if record.lease_id is None:
                lease_rows = db.execute(
                    """
                    SELECT lease_id FROM worker_leases
                    WHERE job_id = ? AND worker_id = ? AND released_at IS NULL
                    ORDER BY acquired_at, lease_id
                    """,
                    (record.job_id, record.worker_id),
                ).fetchall()
                if len(lease_rows) == 1:
                    record = replace(record, lease_id=lease_rows[0]["lease_id"])
                elif len(lease_rows) > 1:
                    raise RuntimeError(
                        f"multiple active leases for job attempt: {record.job_id}"
                    )
            else:
                lease_row = db.execute(
                    """
                    SELECT lease_id FROM worker_leases
                    WHERE lease_id = ? AND job_id = ? AND worker_id = ?
                      AND released_at IS NULL
                    """,
                    (record.lease_id, record.job_id, record.worker_id),
                ).fetchone()
                if lease_row is None:
                    self._raise_job_execution_conflict_in_tx(
                        db,
                        record.job_id,
                        "create_attempt",
                        record.lease_id,
                    )
            db.execute(
                """
                INSERT INTO job_attempts (
                    attempt_id, mission_id, job_id, attempt_number, worker_id, status,
                    started_at, updated_at, completed_at, error_json, retry_decision,
                    metadata_json, lease_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _dump(record.metadata),
                    record.lease_id,
                ),
            )
            self._append_event_in_tx(
                db,
                record.mission_id,
                EventType.JOB_ATTEMPT_CREATED,
                {
                    "attempt_id": record.attempt_id,
                    "job_id": record.job_id,
                    "lease_id": record.lease_id,
                },
            )
        return record

    def complete_job_attempt(
        self,
        attempt_id: str,
        status: JobAttemptStatus,
        error: dict | None = None,
        retry_decision: str | None = None,
        metadata: dict | None = None,
    ) -> JobAttemptRecord:
        attempt = self.get_job_attempt(attempt_id).with_completion(
            status,
            error=error,
            retry_decision=retry_decision,
            metadata=metadata,
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE job_attempts
                SET status = ?, updated_at = ?, completed_at = ?, error_json = ?,
                    retry_decision = ?, metadata_json = ?
                WHERE attempt_id = ?
                """,
                (
                    attempt.status.value,
                    attempt.updated_at,
                    attempt.completed_at,
                    _dump(attempt.error) if attempt.error is not None else None,
                    attempt.retry_decision,
                    _dump(attempt.metadata),
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

    def append_event(
        self,
        mission_id: str,
        event_type: EventType,
        payload: dict,
    ) -> EventRecord:
        with self._connect() as db:
            return self._append_event_in_tx(
                db,
                mission_id,
                event_type,
                payload,
            )

    def _append_event_in_tx(self, db: sqlite3.Connection, mission_id: str, event_type: EventType, payload: dict) -> EventRecord:
        row = db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE mission_id = ?", (mission_id,)).fetchone()
        sequence = int(row["next_sequence"])
        event = EventRecord(str(uuid4()), mission_id, event_type, sequence, utc_now_iso(), payload)
        db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (event.event_id, event.mission_id, event.event_type.value, event.sequence, event.created_at, _dump(event.payload)),
        )
        return event


_GRAPH_CONTROL_EVENT_TYPES = {
    EventType.GRAPH_RUN_CREATED.value,
    EventType.GRAPH_RUN_UPDATED.value,
    EventType.GRAPH_STEP_ADVANCED.value,
    EventType.NODE_RUN_CREATED.value,
    EventType.NODE_RUN_UPDATED.value,
    EventType.GRAPH_HANDOFF_CREATED.value,
    EventType.GRAPH_HANDOFF_CONSUMED.value,
    EventType.GRAPH_NODE_JOB_BOUND.value,
    EventType.JOB_CREATED.value,
    EventType.JOB_LEASED.value,
    EventType.JOB_SUCCEEDED.value,
    EventType.JOB_FAILED.value,
    EventType.JOB_REQUEUED.value,
    EventType.JOB_CANCELED.value,
    EventType.JOB_ATTEMPT_CREATED.value,
    EventType.JOB_ATTEMPT_UPDATED.value,
    EventType.GRAPH_INTERVENTION_CREATED.value,
    EventType.GRAPH_INTERVENTION_APPLIED.value,
    EventType.GRAPH_INTERVENTION_REJECTED.value,
}

_GRAPH_CONTROL_ID_FIELDS = {
    "graph_run_id": "graph",
    "node_run_id": "node",
    "handoff_id": "handoff",
    "job_id": "job",
    "attempt_id": "attempt",
    "intervention_id": "intervention",
}

_GRAPH_CONTROL_IDS_FIELDS = {
    "graph_run_ids": "graph",
    "node_run_ids": "node",
    "handoff_ids": "handoff",
    "job_ids": "job",
    "attempt_ids": "attempt",
    "intervention_ids": "intervention",
}


def _graph_control_event_cursor_in_tx(
    db: sqlite3.Connection,
    *,
    graph_run_id: str,
    mission_id: str,
) -> int:
    references: dict[str, set[str]] = {
        "graph": {graph_run_id},
        "node": {
            str(row["node_run_id"])
            for row in db.execute(
                "SELECT node_run_id FROM node_runs WHERE graph_run_id = ?",
                (graph_run_id,),
            ).fetchall()
        },
        "handoff": {
            str(row["handoff_id"])
            for row in db.execute(
                "SELECT handoff_id FROM graph_handoffs WHERE graph_run_id = ?",
                (graph_run_id,),
            ).fetchall()
        },
        "job": {
            str(row["job_id"])
            for row in db.execute(
                "SELECT job_id FROM graph_node_jobs WHERE graph_run_id = ?",
                (graph_run_id,),
            ).fetchall()
        },
        "attempt": set(),
        "intervention": {
            str(row["intervention_id"])
            for row in db.execute(
                """
                SELECT intervention_id FROM graph_interventions
                WHERE graph_run_id = ?
                """,
                (graph_run_id,),
            ).fetchall()
        },
    }
    if references["job"]:
        placeholders = ",".join("?" for _ in references["job"])
        references["attempt"] = {
            str(row["attempt_id"])
            for row in db.execute(
                f"SELECT attempt_id FROM job_attempts WHERE job_id IN ({placeholders})",
                tuple(sorted(references["job"])),
            ).fetchall()
        }

    cursor = 0
    rows = db.execute(
        """
        SELECT event_type, sequence, payload_json FROM events
        WHERE mission_id = ? ORDER BY sequence
        """,
        (mission_id,),
    ).fetchall()
    for row in rows:
        if row["event_type"] not in _GRAPH_CONTROL_EVENT_TYPES:
            continue
        payload = _load(row["payload_json"], {})
        if _payload_references_graph_control(payload, references):
            cursor = int(row["sequence"])
    return cursor


def _payload_references_graph_control(
    value: object,
    references: dict[str, set[str]],
) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            scope = _GRAPH_CONTROL_ID_FIELDS.get(key)
            if scope is not None and str(child) in references[scope]:
                return True
            scope = _GRAPH_CONTROL_IDS_FIELDS.get(key)
            if scope is not None:
                members = child if isinstance(child, list) else [child]
                if any(str(member) in references[scope] for member in members):
                    return True
            if _payload_references_graph_control(child, references):
                return True
    elif isinstance(value, list):
        return any(
            _payload_references_graph_control(child, references) for child in value
        )
    return False


def _graph_intervention_from_row(row: sqlite3.Row) -> dict:
    return {
        "intervention_id": row["intervention_id"],
        "graph_run_id": row["graph_run_id"],
        "mission_id": row["mission_id"],
        "action": row["action"],
        "node_id": row["node_id"],
        "expected_cursor": row["expected_cursor"],
        "idempotency_key": row["idempotency_key"],
        "reason": row["reason"],
        "status": row["status"],
        "result": _load(row["result_json"], None),
        "error": _load(row["error_json"], None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _same_intervention_request(record: dict, request: dict) -> bool:
    return all(
        record[key] == request[key]
        for key in (
            "graph_run_id",
            "action",
            "node_id",
            "expected_cursor",
            "idempotency_key",
            "reason",
        )
    )


def _intervention_record_response(
    record: dict,
    *,
    idempotent_replay: bool,
) -> dict:
    response = {
        "ok": record["status"] == "applied",
        "idempotent_replay": idempotent_replay,
        "intervention": record,
    }
    if record.get("result") is not None:
        response["result"] = record["result"]
    if record.get("error") is not None:
        response["error"] = record["error"]
    return response


def _intervention_error_response(code: str, message: str, **details: object) -> dict:
    return {
        "ok": False,
        "idempotent_replay": False,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }


def _merge_handoff_rows(rows: list[sqlite3.Row]) -> dict:
    provenance: dict[str, dict] = {}
    values_by_key: dict[str, list[object]] = {}
    presence_by_key: dict[str, int] = {}
    for row in rows:
        payload = dict(_load(row["payload_json"], {}))
        provenance_key = str(row["from_node"])
        suffix = 2
        while provenance_key in provenance:
            provenance_key = f"{row['from_node']}#{suffix}"
            suffix += 1
        provenance[provenance_key] = payload
        for key, value in payload.items():
            if key == "_handoffs":
                continue
            values_by_key.setdefault(key, []).append(value)
            presence_by_key[key] = presence_by_key.get(key, 0) + 1

    merged: dict = {"_handoffs": provenance}
    for key, values in values_by_key.items():
        if presence_by_key[key] == 1 or all(value == values[0] for value in values[1:]):
            merged[key] = values[0]
    return merged


def _unsafe_join_cancel_details(snapshot: dict, node_id: str) -> dict | None:
    nodes = [item for item in snapshot.get("nodes", []) if isinstance(item, dict)]
    join_all = {
        str(node.get("id")) for node in nodes if node.get("join", "any") == "all"
    }
    if not join_all:
        return None
    edges = [item for item in snapshot.get("edges", []) if isinstance(item, dict)]
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(str(edge.get("from")), set()).add(str(edge.get("to")))

    canceled_destinations = {
        str(edge.get("to"))
        for edge in edges
        if edge.get("from") == node_id and edge.get("on") == "canceled"
    }
    normal_destinations = {
        str(edge.get("to"))
        for edge in edges
        if edge.get("from") == node_id and edge.get("on") != "canceled"
    }
    missing_routes: dict[str, list[str]] = {}
    for destination in sorted(normal_destinations):
        reachable_joins = _reachable_targets(adjacency, destination, join_all)
        if reachable_joins and destination not in canceled_destinations:
            missing_routes[destination] = sorted(reachable_joins)
    if not missing_routes:
        return None
    return {
        "node_id": node_id,
        "missing_canceled_routes": missing_routes,
        "join_node_ids": sorted(
            {join_id for join_ids in missing_routes.values() for join_id in join_ids}
        ),
    }


def _reachable_targets(
    adjacency: dict[str, set[str]],
    start: str,
    targets: set[str],
) -> set[str]:
    found: set[str] = set()
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if current in targets:
            found.add(current)
        pending.extend(adjacency.get(current, set()) - visited)
    return found


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
        template_snapshot=dict(_load(row["template_snapshot_json"], {})),
        initial_payload=dict(_load(row["initial_payload_json"], {})),
        step_count=row["step_count"],
        max_steps=row["max_steps"],
    )


def _graph_handoff_from_row(row: sqlite3.Row) -> GraphHandoffRecord:
    return GraphHandoffRecord(
        handoff_id=row["handoff_id"],
        graph_run_id=row["graph_run_id"],
        mission_id=row["mission_id"],
        edge_id=row["edge_id"],
        source_node_run_id=row["source_node_run_id"],
        from_node=row["from_node"],
        to_node=row["to_node"],
        outcome=row["outcome"],
        payload=dict(_load(row["payload_json"], {})),
        status=GraphHandoffStatus(row["status"]),
        created_at=row["created_at"],
        consumed_at=row["consumed_at"],
        consumed_by_node_run_id=row["consumed_by_node_run_id"],
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
        metadata=dict(_load(row["metadata_json"], {})),
        lease_id=row["lease_id"],
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
