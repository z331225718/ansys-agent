from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from aedt_agent.agent.mission import (
    ApprovalDecision,
    ApprovalRequest,
    CheckpointRecord,
    EngineeringConstraint,
    ErrorClass,
    EventRecord,
    EventType,
    JobError,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
    WorkerLease,
    utc_now_iso,
)


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
