from __future__ import annotations

import pytest

from aedt_agent.agent.mission import MissionState
from aedt_agent.agent.orchestrator.state_machine import InvalidMissionTransition, assert_transition, can_transition


def test_allowed_mission_transitions():
    assert can_transition(MissionState.CREATED, MissionState.PLANNING)
    assert can_transition(MissionState.PLANNING, MissionState.WAITING_WORKER)
    assert can_transition(MissionState.WAITING_WORKER, MissionState.EVALUATING)
    assert can_transition(MissionState.EVALUATING, MissionState.WAITING_APPROVAL)
    assert can_transition(MissionState.WAITING_APPROVAL, MissionState.WAITING_WORKER)
    assert can_transition(MissionState.EVALUATING, MissionState.COMPLETED)
    assert can_transition(MissionState.WAITING_WORKER, MissionState.FAILED)
    assert can_transition(MissionState.PLANNING, MissionState.CANCELED)


def test_terminal_states_do_not_transition():
    assert not can_transition(MissionState.COMPLETED, MissionState.PLANNING)
    assert not can_transition(MissionState.FAILED, MissionState.WAITING_WORKER)
    assert not can_transition(MissionState.CANCELED, MissionState.WAITING_APPROVAL)


def test_invalid_transition_raises_clear_error():
    with pytest.raises(InvalidMissionTransition) as error:
        assert_transition(MissionState.CREATED, MissionState.COMPLETED)

    assert "created -> completed" in str(error.value)
