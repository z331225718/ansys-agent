from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from aedt_agent.agent.orchestrator.loop_contracts import LoopDecisionType, MissionLoopRecord
from aedt_agent.agent.policies import ExecutionProfile
from aedt_agent.agent.policies.mission_budget import evaluate_mission_budget


def _loop(**overrides):
    record = MissionLoopRecord.create("loop-1", "mission-1", ExecutionProfile.safe_recorded())
    return replace(record, **overrides)


def test_iteration_budget_stops_before_next_advance():
    loop = _loop(iteration_count=12)

    decision = evaluate_mission_budget(loop, {"job_attempts": 3}, now=datetime.now(UTC))

    assert decision is not None
    assert decision.decision == LoopDecisionType.BUDGET_EXHAUSTED
    assert decision.reason == "max_iterations reached"


def test_job_attempt_budget_uses_real_attempt_count():
    loop = _loop()

    decision = evaluate_mission_budget(loop, {"job_attempts": 16}, now=datetime.now(UTC))

    assert decision is not None
    assert decision.decision == LoopDecisionType.BUDGET_EXHAUSTED
    assert decision.reason == "max_job_attempts reached"


def test_wall_time_budget_is_deterministic():
    now = datetime.now(UTC)
    loop = _loop(started_at=(now - timedelta(seconds=3601)).isoformat())

    decision = evaluate_mission_budget(loop, {"job_attempts": 0}, now=now)

    assert decision is not None
    assert decision.reason == "max_wall_seconds reached"


def test_no_improvement_and_duplicate_action_limits_have_distinct_decisions():
    no_improvement = _loop(consecutive_no_improvement=3)
    duplicate = _loop(duplicate_action_count=2)

    no_improvement_decision = evaluate_mission_budget(no_improvement, {}, now=datetime.now(UTC))
    duplicate_decision = evaluate_mission_budget(duplicate, {}, now=datetime.now(UTC))

    assert no_improvement_decision.decision == LoopDecisionType.STOPPED_NO_IMPROVEMENT
    assert duplicate_decision.decision == LoopDecisionType.STOPPED_DUPLICATE_ACTION


def test_budget_allows_work_when_all_usage_is_below_limits():
    decision = evaluate_mission_budget(
        _loop(iteration_count=1, job_attempt_count=1),
        {"job_attempts": 1, "evidence_query_calls": 2, "evidence_tokens": 1200},
        now=datetime.now(UTC),
    )

    assert decision is None
