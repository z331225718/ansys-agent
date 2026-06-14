from __future__ import annotations

from aedt_agent.agent.actions.contracts import ActionStatus


class InvalidActionTransition(ValueError):
    """Raised when an action attempts an unsupported lifecycle transition."""


_TRANSITIONS = {
    ActionStatus.PROPOSED: {ActionStatus.WAITING_APPROVAL, ActionStatus.FAILED},
    ActionStatus.WAITING_APPROVAL: {ActionStatus.APPROVED, ActionStatus.REJECTED},
    ActionStatus.APPROVED: {ActionStatus.APPLYING, ActionStatus.FAILED},
    ActionStatus.APPLYING: {ActionStatus.APPLIED, ActionStatus.FAILED},
    ActionStatus.APPLIED: {
        ActionStatus.ACCEPTED,
        ActionStatus.ROLLED_BACK,
        ActionStatus.WAITING_APPROVAL,
        ActionStatus.FAILED,
    },
    ActionStatus.ACCEPTED: set(),
    ActionStatus.ROLLED_BACK: set(),
    ActionStatus.REJECTED: set(),
    ActionStatus.FAILED: set(),
}


def assert_action_transition(current: ActionStatus, target: ActionStatus) -> None:
    if current == target:
        return
    if target not in _TRANSITIONS[current]:
        raise InvalidActionTransition(f"invalid action transition: {current.value} -> {target.value}")
