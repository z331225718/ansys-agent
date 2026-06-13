from __future__ import annotations

from aedt_agent.agent.mission import MissionState


class InvalidMissionTransition(ValueError):
    """Raised when a Mission state transition is not allowed."""


ALLOWED_TRANSITIONS: dict[MissionState, set[MissionState]] = {
    MissionState.CREATED: {MissionState.PLANNING, MissionState.CANCELED},
    MissionState.PLANNING: {
        MissionState.WAITING_WORKER,
        MissionState.WAITING_APPROVAL,
        MissionState.FAILED,
        MissionState.CANCELED,
    },
    MissionState.WAITING_WORKER: {
        MissionState.EVALUATING,
        MissionState.WAITING_APPROVAL,
        MissionState.FAILED,
        MissionState.CANCELED,
    },
    MissionState.WAITING_APPROVAL: {
        MissionState.WAITING_WORKER,
        MissionState.PLANNING,
        MissionState.FAILED,
        MissionState.CANCELED,
    },
    MissionState.EVALUATING: {
        MissionState.WAITING_WORKER,
        MissionState.WAITING_APPROVAL,
        MissionState.COMPLETED,
        MissionState.FAILED,
        MissionState.CANCELED,
    },
    MissionState.COMPLETED: set(),
    MissionState.FAILED: set(),
    MissionState.CANCELED: set(),
}


def can_transition(current: MissionState, target: MissionState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: MissionState, target: MissionState) -> None:
    if not can_transition(current, target):
        raise InvalidMissionTransition(f"Invalid Mission transition: {current.value} -> {target.value}")
