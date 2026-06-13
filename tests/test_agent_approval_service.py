from __future__ import annotations

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.mission import ApprovalDecision, MissionState
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def test_approval_request_moves_mission_to_waiting_approval(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    service = ApprovalService(store)
    mission = store.create_mission(__import__("aedt_agent.agent.mission", fromlist=["MissionRecord"]).MissionRecord.create("mission-1", "goal", [], []))

    approval = service.request_approval(
        mission_id=mission.mission_id,
        reason="端口候选不唯一",
        options=[{"id": "p1", "label": "TX0-GND"}],
    )

    assert approval.decision == ApprovalDecision.PENDING
    assert store.get_mission(mission.mission_id).state == MissionState.WAITING_APPROVAL


def test_approve_and_reject_are_audited(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    service = ApprovalService(store)
    store.create_mission(__import__("aedt_agent.agent.mission", fromlist=["MissionRecord"]).MissionRecord.create("mission-1", "goal", [], []))
    approval = service.request_approval("mission-1", "选择端口", [{"id": "p1", "label": "P1"}])

    resolved = service.approve(approval.approval_id, selected_option_id="p1", comment="确认")

    assert resolved.decision == ApprovalDecision.APPROVED
    assert resolved.selected_option_id == "p1"
    assert store.get_mission("mission-1").state == MissionState.WAITING_WORKER
    assert any(event.event_type.value == "approval_resolved" for event in store.list_events("mission-1"))


def test_reject_moves_mission_to_failed(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    service = ApprovalService(store)
    store.create_mission(__import__("aedt_agent.agent.mission", fromlist=["MissionRecord"]).MissionRecord.create("mission-1", "goal", [], []))
    approval = service.request_approval("mission-1", "模型不可接受", [{"id": "repair", "label": "修复"}])

    rejected = service.reject(approval.approval_id, comment="模型边界错误")

    assert rejected.decision == ApprovalDecision.REJECTED
    assert store.get_mission("mission-1").state == MissionState.FAILED
