from __future__ import annotations

from aedt_agent.agent.mission import (
    EngineeringConstraint,
    EventType,
    JobStatus,
    MissionRecord,
    MissionState,
)
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


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


def test_job_completion_persists_output_and_error(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    job = store.create_job("mission-1", "fake.build", "k1", {}, 30, 1)

    succeeded = store.complete_job(job.job_id, output_payload={"result": "ok"}, artifact_refs=["a.json"])

    assert succeeded.status == JobStatus.SUCCEEDED
    assert succeeded.output_payload == {"result": "ok"}
    assert succeeded.artifact_refs == ["a.json"]
    assert store.get_job(job.job_id).status == JobStatus.SUCCEEDED
