from __future__ import annotations

from datetime import UTC, datetime

from aedt_agent.agent.mission import (
    ApprovalDecision,
    ApprovalRequest,
    CheckpointRecord,
    EngineeringConstraint,
    EventRecord,
    EventType,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
    WorkerLease,
    utc_now_iso,
)


def test_mission_record_is_json_ready_and_text_only_by_default():
    mission = MissionRecord.create(
        mission_id="mission-1",
        user_goal="优化 56G 通道插损",
        acceptance_criteria=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        constraints=[EngineeringConstraint(name="max_iterations", value=3)],
    )

    payload = mission.to_json_dict()

    assert payload["mission_id"] == "mission-1"
    assert payload["state"] == "created"
    assert payload["vision_required"] is False
    assert payload["acceptance_criteria"][0]["metric"] == "s21_db_at_56g"
    assert payload["constraints"][0] == {"name": "max_iterations", "value": 3}


def test_job_record_carries_idempotency_and_structured_io():
    job = JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability="fake.build_model",
        idempotency_key="mission-1:build:0",
        input_payload={"layout_file": "case.brd"},
        timeout_seconds=120,
        retry_limit=2,
    )

    payload = job.to_json_dict()

    assert payload["status"] == "queued"
    assert payload["idempotency_key"] == "mission-1:build:0"
    assert payload["input_payload"] == {"layout_file": "case.brd"}
    assert payload["output_payload"] == {}
    assert payload["error"] is None


def test_event_checkpoint_approval_and_lease_are_json_ready():
    created_at = datetime(2026, 6, 13, 12, 0, tzinfo=UTC).isoformat()
    event = EventRecord(
        event_id="event-1",
        mission_id="mission-1",
        event_type=EventType.MISSION_CREATED,
        sequence=1,
        created_at=created_at,
        payload={"state": "created"},
    )
    checkpoint = CheckpointRecord(
        checkpoint_id="checkpoint-1",
        mission_id="mission-1",
        job_id="job-1",
        created_at=created_at,
        artifact_refs=["artifacts/model.aedt"],
        payload={"model_state": "built"},
    )
    approval = ApprovalRequest.create(
        approval_id="approval-1",
        mission_id="mission-1",
        reason="端口候选不唯一",
        options=[{"id": "p1", "label": "TX0-GND"}],
    )
    lease = WorkerLease(
        lease_id="lease-1",
        job_id="job-1",
        worker_id="worker-1",
        acquired_at=created_at,
        expires_at=created_at,
        released_at=None,
    )

    assert event.to_json_dict()["event_type"] == "mission_created"
    assert checkpoint.to_json_dict()["artifact_refs"] == ["artifacts/model.aedt"]
    assert approval.to_json_dict()["decision"] == "pending"
    assert approval.decision == ApprovalDecision.PENDING
    assert lease.to_json_dict()["worker_id"] == "worker-1"


def test_utc_now_iso_is_timezone_aware():
    value = utc_now_iso()

    assert value.endswith("+00:00")
