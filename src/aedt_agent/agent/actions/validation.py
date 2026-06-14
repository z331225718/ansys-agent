from __future__ import annotations

import math

from aedt_agent.agent.actions.contracts import ActionRecord


class ActionValidationError(ValueError):
    """Raised when a controlled engineering action violates its schema."""


def validate_action(action: ActionRecord) -> ActionRecord:
    if action.action_type != "adjust_layout_void":
        raise ActionValidationError(f"unsupported action_type: {action.action_type}")
    if action.version != 1:
        raise ActionValidationError(f"unsupported action version: {action.version}")

    layer = str(action.target.get("layer") or "")
    region_ref = str(action.target.get("region_ref") or "")
    shape = str(action.target.get("shape") or "")
    if not layer:
        raise ActionValidationError("layer is required")
    if not region_ref:
        raise ActionValidationError("region_ref is required")
    if shape not in {"circle", "rectangle"}:
        raise ActionValidationError(f"unsupported void shape: {shape}")

    variable = str(action.parameters.get("variable") or "")
    if not variable:
        raise ActionValidationError("variable is required")
    old_value = _finite(action.parameters, "old_value_mil")
    new_value = _finite(action.parameters, "new_value_mil")
    delta = _finite(action.parameters, "delta_mil")
    minimum = _finite(action.constraints, "min_value_mil")
    maximum = _finite(action.constraints, "max_value_mil")
    max_delta = _finite(action.constraints, "max_abs_delta_mil")

    if abs(delta) <= 1e-12:
        raise ActionValidationError("delta_mil must not be zero")
    if not math.isclose(new_value - old_value, delta, abs_tol=1e-6):
        raise ActionValidationError("delta_mil does not match new_value_mil - old_value_mil")
    if not minimum <= new_value <= maximum:
        raise ActionValidationError("new_value_mil is outside allowed range")
    if abs(delta) > max_delta:
        raise ActionValidationError("delta_mil exceeds max_abs_delta_mil")
    if action.adapter_mode not in {"recorded", "real_aedt"}:
        raise ActionValidationError(f"unsupported adapter_mode: {action.adapter_mode}")
    if action.adapter_mode == "recorded":
        for key in ("before_touchstone", "before_tdr", "after_touchstone", "after_tdr"):
            if not str(action.adapter_input.get(key) or ""):
                raise ActionValidationError(f"{key} is required for recorded adapter")
    return action


def _finite(payload: dict, key: str) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ActionValidationError(f"{key} must be a finite number") from exc
    if not math.isfinite(value):
        raise ActionValidationError(f"{key} must be a finite number")
    return value
