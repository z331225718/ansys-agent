from __future__ import annotations

from aedt_agent.agent.mission import MissionRecord, MissionState
from aedt_agent.agent.orchestrator.loop_contracts import (
    LoopDecision,
    LoopDecisionType,
    MissionLoopRecord,
)
from aedt_agent.agent.policies import ExecutionProfile
from aedt_agent.infrastructure import SQLiteMissionStore


def _store_with_mission(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", []))
    return store


def test_mission_loop_persists_across_store_restart(tmp_path):
    store = _store_with_mission(tmp_path)
    loop = MissionLoopRecord.create("loop-1", "mission-1", ExecutionProfile.safe_recorded())

    store.create_mission_loop(loop)
    restarted = SQLiteMissionStore(tmp_path / "mission.db")
    loaded = restarted.get_mission_loop("mission-1")

    assert loaded is not None
    assert loaded.loop_id == "loop-1"
    assert loaded.profile == ExecutionProfile.safe_recorded()
    assert loaded.iteration_count == 0


def test_mission_loop_update_persists_last_decision(tmp_path):
    store = _store_with_mission(tmp_path)
    loop = store.create_mission_loop(
        MissionLoopRecord.create("loop-1", "mission-1", ExecutionProfile.safe_recorded())
    )
    updated = loop.with_decision(
        LoopDecision(
            decision=LoopDecisionType.IDLE,
            reason="no jobs",
            usage={"iterations": 1},
            limits={"max_iterations": 12},
        ),
        iteration_increment=1,
    )

    store.update_mission_loop(updated)
    loaded = store.get_mission_loop("mission-1")

    assert loaded is not None
    assert loaded.iteration_count == 1
    assert loaded.last_decision == LoopDecisionType.IDLE
    assert loaded.last_reason == "no jobs"


def test_final_outcome_persists_with_terminal_mission(tmp_path):
    store = _store_with_mission(tmp_path)
    store.update_mission_state("mission-1", MissionState.PLANNING)
    store.update_mission_state("mission-1", MissionState.FAILED)

    mission = store.set_mission_final_outcome(
        "mission-1",
        {"code": "budget_exhausted", "reason": "max_job_attempts reached"},
    )
    restarted = SQLiteMissionStore(tmp_path / "mission.db")

    assert mission.final_outcome["code"] == "budget_exhausted"
    assert restarted.get_mission("mission-1").final_outcome["reason"] == "max_job_attempts reached"
