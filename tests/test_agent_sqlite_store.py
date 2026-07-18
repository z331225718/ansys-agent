from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aedt_agent.agent.mission import (
    EngineeringConstraint,
    ErrorClass,
    EventType,
    JobError,
    JobStatus,
    MissionRecord,
    MissionState,
)
from aedt_agent.infrastructure.sqlite_mission_store import (
    JobExecutionConflictError,
    SQLiteMissionStore,
)


def test_mission_survives_store_restart(tmp_path):
    db_path = tmp_path / "mission.db"
    store = SQLiteMissionStore(db_path)
    mission = MissionRecord.create(
        mission_id="mission-1",
        user_goal="构建 BRD local cut",
        acceptance_criteria=[{"metric": "s11_db_max", "op": "<=", "value": -10}],
        constraints=[EngineeringConstraint(name="max_jobs", value=5)],
    )

    store.create_mission(mission)

    reopened = SQLiteMissionStore(db_path)
    loaded = reopened.get_mission("mission-1")

    assert loaded is not None
    assert loaded.user_goal == "构建 BRD local cut"
    assert loaded.constraints[0].name == "max_jobs"
    assert reopened.list_events("mission-1")[0].event_type == EventType.MISSION_CREATED


def test_job_creation_is_idempotent_per_mission_and_key(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))

    first = store.create_job(
        mission_id="mission-1",
        capability="fake.build",
        idempotency_key="mission-1:build:0",
        input_payload={"x": 1},
        timeout_seconds=30,
        retry_limit=1,
    )
    second = store.create_job(
        mission_id="mission-1",
        capability="fake.build",
        idempotency_key="mission-1:build:0",
        input_payload={"x": 1},
        timeout_seconds=30,
        retry_limit=1,
    )

    assert second.job_id == first.job_id
    assert store.list_jobs("mission-1") == [first]
    assert [event.event_type for event in store.list_events("mission-1")].count(EventType.JOB_CREATED) == 1


def test_state_change_and_checkpoint_are_audited(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    job = store.create_job("mission-1", "fake.build", "k1", {}, 30, 1)

    updated = store.update_mission_state("mission-1", MissionState.WAITING_WORKER)
    checkpoint = store.create_checkpoint(
        mission_id="mission-1",
        job_id=job.job_id,
        artifact_refs=["artifacts/model.aedt"],
        payload={"ok": True},
    )

    assert updated.state == MissionState.WAITING_WORKER
    assert checkpoint.artifact_refs == ["artifacts/model.aedt"]
    assert [event.sequence for event in store.list_events("mission-1")] == [1, 2, 3, 4]


def _inject_competing_state_update(monkeypatch, store, competing_state):
    original_connect = store._connect
    competitor = SQLiteMissionStore(store.db_path)
    race_injected = False

    class RacingCursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def fetchone(self):
            nonlocal race_injected
            row = self._cursor.fetchone()
            assert self._cursor.fetchone() is None
            race_injected = True
            competitor.update_mission_state("mission-1", competing_state)
            return row

    class RacingConnection:
        def __init__(self):
            self._connection = original_connect()

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def execute(self, sql, parameters=()):
            cursor = self._connection.execute(sql, parameters)
            if not race_injected and sql.startswith("SELECT state FROM missions"):
                return RacingCursor(cursor)
            return cursor

    monkeypatch.setattr(store, "_connect", RacingConnection)


def test_same_target_mission_state_race_converges_without_duplicate_event(tmp_path, monkeypatch):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    store.update_mission_state("mission-1", MissionState.WAITING_WORKER)
    _inject_competing_state_update(monkeypatch, store, MissionState.EVALUATING)

    updated = store.update_mission_state("mission-1", MissionState.EVALUATING)

    assert updated.state == MissionState.EVALUATING
    state_events = [
        event.payload["state"]
        for event in store.list_events("mission-1")
        if event.event_type == EventType.MISSION_STATE_CHANGED
    ]
    assert state_events == ["waiting_worker", "evaluating"]


def test_different_target_mission_state_race_remains_a_conflict(tmp_path, monkeypatch):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    store.update_mission_state("mission-1", MissionState.WAITING_WORKER)
    _inject_competing_state_update(monkeypatch, store, MissionState.WAITING_APPROVAL)

    with pytest.raises(RuntimeError, match="concurrent modification detected"):
        store.update_mission_state("mission-1", MissionState.EVALUATING)

    assert store.get_mission("mission-1").state == MissionState.WAITING_APPROVAL


def test_job_completion_persists_output_and_error(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    job = store.create_job("mission-1", "fake.build", "k1", {}, 30, 1)

    succeeded = store.complete_job(job.job_id, output_payload={"result": "ok"}, artifact_refs=["a.json"])

    assert succeeded.status == JobStatus.SUCCEEDED
    assert succeeded.output_payload == {"result": "ok"}
    assert succeeded.artifact_refs == ["a.json"]
    assert store.get_job(job.job_id).status == JobStatus.SUCCEEDED


@pytest.mark.parametrize("stale_operation", ["complete", "fail", "cancel"])
def test_stale_lease_cannot_commit_after_recovery_and_redispatch(
    tmp_path,
    stale_operation,
):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    job = store.create_job("mission-1", "fake.build", "k1", {}, 30, 1)
    expired_at = datetime.now(UTC) - timedelta(seconds=5)
    old_lease = store.acquire_job_lease(
        job.job_id,
        "worker-old",
        lease_seconds=1,
        now=expired_at,
    )
    assert store.recover_expired_leases(datetime.now(UTC)) == [job.job_id]
    new_lease = store.acquire_job_lease(
        job.job_id,
        "worker-new",
        lease_seconds=60,
    )
    error = JobError(
        ErrorClass.WORKER_CRASH,
        "late stale result",
        retryable=True,
    )

    with pytest.raises(JobExecutionConflictError) as exc_info:
        if stale_operation == "complete":
            store.complete_job(
                job.job_id,
                {"writer": "old"},
                ["old.json"],
                lease_id=old_lease.lease_id,
            )
        elif stale_operation == "fail":
            store.fail_job(job.job_id, error, lease_id=old_lease.lease_id)
        else:
            store.cancel_job(job.job_id, error, lease_id=old_lease.lease_id)

    conflict = exc_info.value
    assert conflict.operation == stale_operation
    assert conflict.lease_id == old_lease.lease_id
    assert conflict.current_status == JobStatus.LEASED.value
    assert conflict.active_lease_ids == [new_lease.lease_id]
    current = store.get_job(job.job_id)
    assert current.status == JobStatus.LEASED
    assert current.output_payload == {}
    assert current.artifact_refs == []
    assert current.error is None
    terminal_events = {
        EventType.JOB_SUCCEEDED,
        EventType.JOB_FAILED,
        EventType.JOB_CANCELED,
    }
    assert not any(
        event.event_type in terminal_events
        for event in store.list_events("mission-1")
    )

    succeeded = store.complete_job(
        job.job_id,
        {"writer": "new"},
        ["new.json"],
        lease_id=new_lease.lease_id,
    )

    assert succeeded.status == JobStatus.SUCCEEDED
    assert succeeded.output_payload == {"writer": "new"}
    succeeded_events = [
        event
        for event in store.list_events("mission-1")
        if event.event_type == EventType.JOB_SUCCEEDED
    ]
    assert len(succeeded_events) == 1
    assert succeeded_events[0].payload["lease_id"] == new_lease.lease_id


def test_unleased_completion_cannot_bypass_a_recovered_execution_fence(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    job = store.create_job("mission-1", "fake.build", "k1", {}, 30, 1)
    expired_at = datetime.now(UTC) - timedelta(seconds=5)
    store.acquire_job_lease(
        job.job_id,
        "worker-old",
        lease_seconds=1,
        now=expired_at,
    )
    store.recover_expired_leases(datetime.now(UTC))

    with pytest.raises(JobExecutionConflictError):
        store.complete_job(job.job_id, {"writer": "unknown"}, [])

    assert store.get_job(job.job_id).status == JobStatus.QUEUED
    assert not any(
        event.event_type == EventType.JOB_SUCCEEDED
        for event in store.list_events("mission-1")
    )
