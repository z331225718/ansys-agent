"""Controlled engineering action contracts and policies."""

from aedt_agent.agent.actions.approval import (
    approve_action,
    assert_action_approved,
    reject_action,
    request_action_approval,
)
from aedt_agent.agent.actions.adapters import RealAedtActionAdapter, RecordedActionAdapter
from aedt_agent.agent.actions.contracts import (
    ActionDecision,
    ActionExecutionRecord,
    ActionExecutionStatus,
    ActionRecord,
    ActionStatus,
)
from aedt_agent.agent.actions.policy import decide_action_outcome
from aedt_agent.agent.actions.executor import execute_approved_action
from aedt_agent.agent.actions.state_machine import InvalidActionTransition, assert_action_transition
from aedt_agent.agent.actions.validation import ActionValidationError, validate_action

__all__ = [
    "ActionDecision",
    "ActionExecutionRecord",
    "ActionExecutionStatus",
    "ActionRecord",
    "ActionStatus",
    "ActionValidationError",
    "RealAedtActionAdapter",
    "RecordedActionAdapter",
    "InvalidActionTransition",
    "approve_action",
    "assert_action_approved",
    "assert_action_transition",
    "decide_action_outcome",
    "execute_approved_action",
    "reject_action",
    "request_action_approval",
    "validate_action",
]
