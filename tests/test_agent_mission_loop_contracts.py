from __future__ import annotations

from aedt_agent.agent.orchestrator.loop_contracts import (
    LoopDecision,
    LoopDecisionType,
    MissionLoopRecord,
    MissionLoopStatus,
)
from aedt_agent.agent.policies import ExecutionProfile


def test_mission_loop_record_is_json_ready():
    record = MissionLoopRecord.create(
        loop_id="loop-1",
        mission_id="mission-1",
        profile=ExecutionProfile.safe_recorded(),
    )

    payload = record.to_json_dict()

    assert payload["status"] == "active"
    assert payload["iteration_count"] == 0
    assert payload["job_attempt_count"] == 0
    assert payload["profile"]["allow_real_aedt"] is False
    assert payload["completed_at"] is None


def test_mission_loop_record_tracks_a_persistable_decision():
    record = MissionLoopRecord.create(
        loop_id="loop-1",
        mission_id="mission-1",
        profile=ExecutionProfile.safe_recorded(),
    )
    updated = record.with_decision(
        LoopDecision(
            decision=LoopDecisionType.RETRY_JOB,
            reason="license unavailable",
            usage={"job_attempts": 1},
            limits={"max_job_attempts": 16},
            job_id="job-1",
            retry_after_seconds=5,
        ),
        iteration_increment=1,
        job_attempt_increment=1,
    )

    assert updated.status == MissionLoopStatus.ACTIVE
    assert updated.iteration_count == 1
    assert updated.job_attempt_count == 1
    assert updated.last_decision == LoopDecisionType.RETRY_JOB
    assert updated.last_job_id == "job-1"
    assert updated.to_json_dict()["last_reason"] == "license unavailable"


def test_terminal_loop_decision_sets_completion_timestamp():
    record = MissionLoopRecord.create(
        loop_id="loop-1",
        mission_id="mission-1",
        profile=ExecutionProfile.safe_recorded(),
    )

    completed = record.with_decision(
        LoopDecision(
            decision=LoopDecisionType.COMPLETED,
            reason="all jobs succeeded",
            usage={},
            limits={},
        ),
        status=MissionLoopStatus.COMPLETED,
    )

    assert completed.status == MissionLoopStatus.COMPLETED
    assert completed.completed_at is not None
