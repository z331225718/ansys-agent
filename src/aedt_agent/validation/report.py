from __future__ import annotations

from aedt_agent.validation.rules import ModelValidationResult


def validation_summary(result: ModelValidationResult) -> str:
    total = len(result.checks)
    failed = len([check for check in result.checks if not check.passed])
    if failed == 0:
        return f"Validation passed ({total}/{total} checks)."
    return f"Validation failed ({total - failed}/{total} checks passed, {failed} failed)."
