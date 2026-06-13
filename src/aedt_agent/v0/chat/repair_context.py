from __future__ import annotations

from typing import Any


def summarize_repair_context(context: dict[str, Any]) -> str:
    reason = str(context.get("reason", "unknown"))
    if reason == "workflow_validation_failed":
        errors = context.get("errors", [])
        return f"Workflow validation failed with {len(errors)} error(s)."
    if reason == "workflow_step_failed":
        step_id = context.get("failed_step_id", "")
        error_type = context.get("error_type", "")
        error_message = context.get("error_message", "")
        return f"Workflow step failed: {step_id} ({error_type}: {error_message})."
    if reason == "model_validation_failed":
        failed = context.get("failed_checks", [])
        return f"Model validation failed with {len(failed)} failed check(s)."
    return f"Repair context reason: {reason}."
