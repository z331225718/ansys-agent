from __future__ import annotations

from dataclasses import replace

from aedt_agent.agent.mission import JobStatus, MissionState
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.orchestrator.loop_contracts import LoopDecisionType, MissionLoopStatus
from aedt_agent.agent.orchestrator.mission_loop import MissionLoopController
from aedt_agent.agent.policies import ExecutionProfile
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path, capability, worker):
    registry = InMemoryWorkerRegistry()
    registry.register(capability, worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def test_controller_completes_mission_and_writes_final_outcome(tmp_path):
    runtime = _runtime(tmp_path, "fake.ok", lambda job, context: {"value": 7})
    mission = runtime.create_mission("goal", [], [])
    runtime.create_job(mission.mission_id, "fake.ok", "step-1", {})

    decision = MissionLoopController(runtime).advance(mission.mission_id, worker_id="loop-1")

    completed = runtime.get_mission(mission.mission_id)
    loop = runtime.store.get_mission_loop(mission.mission_id)
    assert decision.decision == LoopDecisionType.COMPLETED
    assert completed.state == MissionState.COMPLETED
    assert completed.final_outcome["code"] == "completed"
    assert loop.status == MissionLoopStatus.COMPLETED
    assert loop.iteration_count == 1
    assert loop.job_attempt_count == 1


def test_controller_retries_same_job_then_completes(tmp_path):
    calls = 0

    def flaky(job, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("license unavailable")
        return {"value": 9}

    runtime = _runtime(tmp_path, "fake.flaky", flaky)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.flaky", "step-1", {}, retry_limit=1)
    controller = MissionLoopController(runtime)

    first = controller.advance(mission.mission_id, worker_id="loop-1")
    second = controller.advance(mission.mission_id, worker_id="loop-2")

    assert first.decision == LoopDecisionType.RETRY_JOB
    assert second.decision == LoopDecisionType.COMPLETED
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert len(runtime.store.list_job_attempts(job.job_id)) == 2
    assert runtime.store.get_mission_loop(mission.mission_id).iteration_count == 2


def test_controller_fails_mission_on_non_retryable_error(tmp_path):
    runtime = _runtime(
        tmp_path,
        "fake.invalid",
        lambda job, context: (_ for _ in ()).throw(ValueError("invalid geometry")),
    )
    mission = runtime.create_mission("goal", [], [])
    runtime.create_job(mission.mission_id, "fake.invalid", "step-1", {})

    decision = MissionLoopController(runtime).advance(mission.mission_id)

    failed = runtime.get_mission(mission.mission_id)
    assert decision.decision == LoopDecisionType.FAILED
    assert failed.state == MissionState.FAILED
    assert failed.final_outcome["code"] == "job_failed"


def test_controller_does_not_execute_while_waiting_for_approval(tmp_path):
    calls = 0

    def worker(job, context):
        nonlocal calls
        calls += 1
        return {}

    runtime = _runtime(tmp_path, "fake.ok", worker)
    mission = runtime.create_mission("goal", [], [])
    runtime.create_job(mission.mission_id, "fake.ok", "step-1", {})
    runtime.store.update_mission_state(mission.mission_id, MissionState.PLANNING)
    runtime.store.update_mission_state(mission.mission_id, MissionState.WAITING_APPROVAL)

    decision = MissionLoopController(runtime).advance(mission.mission_id)

    assert decision.decision == LoopDecisionType.WAITING_APPROVAL
    assert calls == 0
    assert runtime.store.get_mission_loop(mission.mission_id).iteration_count == 0


def test_attempt_budget_exhaustion_prevents_unbounded_retry(tmp_path):
    runtime = _runtime(
        tmp_path,
        "fake.license",
        lambda job, context: (_ for _ in ()).throw(RuntimeError("license unavailable")),
    )
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.license", "step-1", {}, retry_limit=5)
    profile = replace(ExecutionProfile.safe_recorded(), max_job_attempts=1)

    decision = MissionLoopController(runtime, profile=profile).advance(mission.mission_id)

    assert decision.decision == LoopDecisionType.BUDGET_EXHAUSTED
    assert runtime.get_mission(mission.mission_id).final_outcome["reason"] == "max_job_attempts reached"
    assert len(runtime.store.list_job_attempts(job.job_id)) == 1


def test_safe_recorded_profile_blocks_real_build_job(tmp_path):
    runtime = _runtime(tmp_path, "brd.build", lambda job, context: {"unexpected": True})
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(
        mission.mission_id,
        "brd.build",
        "step-1",
        {"adapter_mode": "real_build"},
    )

    decision = MissionLoopController(runtime).advance(mission.mission_id)

    assert decision.decision == LoopDecisionType.FAILED
    assert runtime.get_job(job.job_id).status == JobStatus.QUEUED
    assert runtime.get_mission(mission.mission_id).final_outcome["code"] == "real_aedt_disabled"


def test_controller_resumes_existing_loop_after_store_restart(tmp_path):
    runtime = _runtime(tmp_path, "fake.ok", lambda job, context: {})
    mission = runtime.create_mission("goal", [], [])
    runtime.create_job(mission.mission_id, "fake.ok", "step-1", {})
    controller = MissionLoopController(runtime)
    loop = controller.get_or_create_loop(mission.mission_id)

    restarted = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=runtime.registry)
    resumed = MissionLoopController(restarted).get_or_create_loop(mission.mission_id)

    assert resumed.loop_id == loop.loop_id
    assert resumed.started_at == loop.started_at
