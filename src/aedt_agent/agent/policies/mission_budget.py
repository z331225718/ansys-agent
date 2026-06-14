from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aedt_agent.agent.orchestrator.loop_contracts import LoopDecision, LoopDecisionType, MissionLoopRecord


def evaluate_mission_budget(
    loop: MissionLoopRecord,
    usage: dict[str, Any],
    *,
    now: datetime | None = None,
) -> LoopDecision | None:
    current = now or datetime.now(UTC)
    profile = loop.profile
    normalized_usage = {
        "iterations": loop.iteration_count,
        "job_attempts": int(usage.get("job_attempts", loop.job_attempt_count)),
        "evidence_query_calls": int(usage.get("evidence_query_calls", loop.evidence_query_calls)),
        "evidence_tokens": int(usage.get("evidence_tokens", loop.evidence_tokens)),
        "consecutive_no_improvement": loop.consecutive_no_improvement,
        "duplicate_actions": loop.duplicate_action_count,
        "wall_seconds": max(0, int((current - datetime.fromisoformat(loop.started_at)).total_seconds())),
    }
    limits = mission_budget_limits(loop)

    checks = (
        ("iterations", profile.max_iterations, "max_iterations reached"),
        ("job_attempts", profile.max_job_attempts, "max_job_attempts reached"),
        ("wall_seconds", profile.max_wall_seconds, "max_wall_seconds reached"),
        ("evidence_query_calls", profile.max_evidence_query_calls, "max_evidence_query_calls reached"),
        ("evidence_tokens", profile.max_evidence_tokens, "max_evidence_tokens reached"),
    )
    for usage_key, limit, reason in checks:
        if normalized_usage[usage_key] >= limit:
            return LoopDecision(LoopDecisionType.BUDGET_EXHAUSTED, reason, normalized_usage, limits)
    if loop.consecutive_no_improvement >= profile.max_consecutive_no_improvement:
        return LoopDecision(
            LoopDecisionType.STOPPED_NO_IMPROVEMENT,
            "max_consecutive_no_improvement reached",
            normalized_usage,
            limits,
        )
    if loop.duplicate_action_count >= profile.max_duplicate_actions:
        return LoopDecision(
            LoopDecisionType.STOPPED_DUPLICATE_ACTION,
            "max_duplicate_actions reached",
            normalized_usage,
            limits,
        )
    return None


def mission_budget_limits(loop: MissionLoopRecord) -> dict[str, Any]:
    profile = loop.profile
    return {
        "max_iterations": profile.max_iterations,
        "max_job_attempts": profile.max_job_attempts,
        "max_wall_seconds": profile.max_wall_seconds,
        "max_evidence_query_calls": profile.max_evidence_query_calls,
        "max_evidence_tokens": profile.max_evidence_tokens,
        "max_consecutive_no_improvement": profile.max_consecutive_no_improvement,
        "max_duplicate_actions": profile.max_duplicate_actions,
    }
