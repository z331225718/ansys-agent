from __future__ import annotations

from aedt_agent.agent.actions import (
    ActionDecision,
    ActionExecutionRecord,
    ActionExecutionStatus,
    ActionRecord,
    ActionStatus,
)
from aedt_agent.agent.mission import MissionRecord
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def _action():
    return ActionRecord.create(
        action_id="action-1",
        mission_id="mission-1",
        target={"layer": "ART03", "region_ref": "via-1", "shape": "circle"},
        parameters={"variable": "r_cut", "old_value_mil": 13.0, "new_value_mil": 14.0, "delta_mil": 1.0},
        constraints={"min_value_mil": 10.0, "max_value_mil": 20.0, "max_abs_delta_mil": 2.0},
        reason={"evidence_package_id": "evidence-1", "summary": "RL fail"},
        adapter_mode="recorded",
        adapter_input={
            "before_touchstone": "before.s2p",
            "before_tdr": "before.csv",
            "after_touchstone": "after.s2p",
            "after_tdr": "after.csv",
        },
    )


def _store(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "优化 BRD", [], []))
    return store


def test_action_survives_store_restart(tmp_path):
    store = _store(tmp_path)
    action = _action()

    store.create_action(action)

    reopened = SQLiteMissionStore(tmp_path / "mission.db")
    loaded = reopened.get_action(action.action_id)
    assert loaded == action
    assert reopened.list_actions("mission-1") == [action]


def test_action_status_comparison_and_decision_are_persisted(tmp_path):
    store = _store(tmp_path)
    action = store.create_action(_action())
    updated = action.with_status(
        ActionStatus.ROLLED_BACK,
        approval_id="approval-1",
        comparison={"status": "regressed"},
        decision=ActionDecision.ROLLBACK,
    )

    store.update_action(updated)

    loaded = store.get_action(action.action_id)
    assert loaded.status == ActionStatus.ROLLED_BACK
    assert loaded.approval_id == "approval-1"
    assert loaded.comparison == {"status": "regressed"}
    assert loaded.decision == ActionDecision.ROLLBACK


def test_action_execution_survives_store_restart(tmp_path):
    store = _store(tmp_path)
    store.create_action(_action())
    execution = ActionExecutionRecord.create(
        execution_id="execution-1",
        action_id="action-1",
        mission_id="mission-1",
        adapter_mode="recorded",
        before_artifact_refs=["before.s2p", "before.csv"],
        after_artifact_refs=["after.s2p", "after.csv"],
    )

    store.create_action_execution(execution)
    store.complete_action_execution(
        execution.execution_id,
        ActionExecutionStatus.SUCCEEDED,
        result={"comparison": {"status": "improved"}},
    )

    reopened = SQLiteMissionStore(tmp_path / "mission.db")
    loaded = reopened.get_action_execution(execution.execution_id)
    assert loaded.status == ActionExecutionStatus.SUCCEEDED
    assert loaded.result["comparison"]["status"] == "improved"
    assert reopened.list_action_executions("action-1") == [loaded]


def test_action_events_have_monotonic_sequence(tmp_path):
    store = _store(tmp_path)
    action = store.create_action(_action())
    store.update_action(action.with_status(ActionStatus.WAITING_APPROVAL, approval_id="approval-1"))
    execution = ActionExecutionRecord.create(
        "execution-1",
        "action-1",
        "mission-1",
        "recorded",
        ["before.s2p"],
        ["after.s2p"],
    )
    store.create_action_execution(execution)
    store.complete_action_execution(execution.execution_id, ActionExecutionStatus.FAILED, error={"message": "failed"})

    events = store.list_events("mission-1")
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert [event.event_type.value for event in events][1:] == [
        "action_created",
        "action_updated",
        "action_execution_created",
        "action_execution_updated",
    ]
