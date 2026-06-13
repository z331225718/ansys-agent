from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aedt_agent.agent.mission import EngineeringConstraint, JobStatus, MissionState
from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def test_runtime_creates_restartable_mission(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)

    mission = runtime.create_mission(
        user_goal="构建 local cut",
        acceptance_criteria=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        constraints=[EngineeringConstraint("max_jobs", 4)],
    )

    restarted = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"))
    loaded = restarted.get_mission(mission.mission_id)

    assert loaded.user_goal == "构建 local cut"
    assert loaded.state == MissionState.CREATED


def test_runtime_prevents_duplicate_job_creation(tmp_path):
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"))
    mission = runtime.create_mission("goal", [], [])

    first = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {"x": 1})
    second = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {"x": 1})

    assert second.job_id == first.job_id
    assert len(runtime.list_jobs(mission.mission_id)) == 1


def test_runtime_executes_job_once_and_records_checkpoint(tmp_path):
    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", lambda job, context: {"value": job.input_payload["x"], "artifact_refs": ["artifact.json"]})
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {"x": 7})

    result = runtime.execute_next_job(mission.mission_id, worker_id="worker-1")

    assert result.job_id == job.job_id
    assert result.status == JobStatus.SUCCEEDED
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert any(event.event_type.value == "checkpoint_created" for event in runtime.list_events(mission.mission_id))


def test_expired_worker_lease_can_be_recovered(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {})
    expired = datetime.now(UTC) - timedelta(seconds=5)

    lease = store.acquire_job_lease(job.job_id, worker_id="worker-old", lease_seconds=1, now=expired)
    recovered = runtime.recover_expired_leases(now=datetime.now(UTC))

    assert lease.released_at is None
    assert recovered == [job.job_id]
    assert runtime.get_job(job.job_id).status == JobStatus.QUEUED
