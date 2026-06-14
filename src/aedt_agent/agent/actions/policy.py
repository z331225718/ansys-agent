from __future__ import annotations

from aedt_agent.agent.actions.contracts import ActionDecision


def decide_action_outcome(comparison: dict) -> ActionDecision:
    status = str(comparison.get("status") or "")
    if status == "improved":
        return ActionDecision.ACCEPT
    if status in {"regressed", "unchanged"}:
        return ActionDecision.ROLLBACK
    if status == "mixed":
        return ActionDecision.REVIEW
    raise ValueError(f"unsupported comparison status: {status}")
