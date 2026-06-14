from __future__ import annotations

from dataclasses import replace

import pytest

from aedt_agent.agent.actions import (
    ActionDecision,
    ActionRecord,
    ActionStatus,
    ActionValidationError,
    decide_action_outcome,
    validate_action,
)


def _action(**overrides):
    values = {
        "action_id": "action-1",
        "mission_id": "mission-1",
        "target": {"layer": "ART03", "region_ref": "via-transition-1", "shape": "circle"},
        "parameters": {
            "variable": "r_cut_ART03",
            "old_value_mil": 13.95,
            "new_value_mil": 15.0,
            "delta_mil": 1.05,
        },
        "constraints": {"min_value_mil": 10.0, "max_value_mil": 20.0, "max_abs_delta_mil": 2.0},
        "reason": {
            "evidence_package_id": "evidence-1",
            "failure_window_ghz": {"start": 17.8, "stop": 18.2},
            "summary": "18GHz RL 失败",
        },
        "adapter_mode": "recorded",
        "adapter_input": {
            "before_touchstone": "before.s2p",
            "before_tdr": "before.csv",
            "after_touchstone": "after.s2p",
            "after_tdr": "after.csv",
        },
    }
    values.update(overrides)
    return ActionRecord.create(**values)


def test_action_digest_is_stable_and_json_ready():
    first = _action()
    second = _action()

    assert first.digest == second.digest
    payload = first.to_json_dict()
    assert payload["action_type"] == "adjust_layout_void"
    assert payload["version"] == 1
    assert payload["status"] == "proposed"
    assert len(payload["digest"]) == 64
    assert payload["adapter_input"]["before_touchstone"] == "before.s2p"
    assert "0.0,0.05,0.1" not in str(payload)


def test_action_digest_changes_when_engineering_content_changes():
    original = _action()
    modified = _action(
        parameters={
            "variable": "r_cut_ART03",
            "old_value_mil": 13.95,
            "new_value_mil": 15.5,
            "delta_mil": 1.55,
        }
    )

    assert original.digest != modified.digest


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target", {"layer": "", "region_ref": "r", "shape": "circle"}, "layer is required"),
        ("target", {"layer": "ART03", "region_ref": "r", "shape": "ellipse"}, "unsupported void shape"),
        (
            "parameters",
            {"variable": "r", "old_value_mil": 13.0, "new_value_mil": 13.0, "delta_mil": 0.0},
            "delta_mil must not be zero",
        ),
        (
            "parameters",
            {"variable": "r", "old_value_mil": 13.0, "new_value_mil": 17.0, "delta_mil": 4.0},
            "max_abs_delta_mil",
        ),
        (
            "parameters",
            {"variable": "r", "old_value_mil": 13.0, "new_value_mil": 14.0, "delta_mil": 0.5},
            "delta_mil does not match",
        ),
    ],
)
def test_action_validation_rejects_unsafe_payload(field, value, message):
    action = _action(**{field: value})

    with pytest.raises(ActionValidationError, match=message):
        validate_action(action)


def test_action_validation_accepts_supported_recorded_action():
    action = _action()

    assert validate_action(action) is action


@pytest.mark.parametrize(
    ("comparison_status", "expected"),
    [
        ("improved", ActionDecision.ACCEPT),
        ("regressed", ActionDecision.ROLLBACK),
        ("unchanged", ActionDecision.ROLLBACK),
        ("mixed", ActionDecision.REVIEW),
    ],
)
def test_action_outcome_policy_is_deterministic(comparison_status, expected):
    assert decide_action_outcome({"status": comparison_status}) == expected


def test_runtime_fields_do_not_change_action_digest():
    action = _action()
    updated = replace(action, status=ActionStatus.APPROVED, approval_id="approval-1")

    assert updated.digest == action.digest
