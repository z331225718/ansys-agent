from __future__ import annotations

from uuid import uuid4

from aedt_agent.agent.mission import ApprovalDecision, ApprovalRequest, MissionState


class ApprovalService:
    def __init__(self, store):
        self.store = store

    def request_approval(self, mission_id: str, reason: str, options: list[dict]) -> ApprovalRequest:
        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        if mission.state == MissionState.CREATED:
            self.store.update_mission_state(mission_id, MissionState.PLANNING)
        approval = ApprovalRequest.create(str(uuid4()), mission_id, reason, options)
        created = self.store.create_approval(approval)
        self.store.update_mission_state(mission_id, MissionState.WAITING_APPROVAL)
        return created

    def approve(self, approval_id: str, selected_option_id: str, comment: str | None = None) -> ApprovalRequest:
        approval = self.store.resolve_approval(approval_id, ApprovalDecision.APPROVED, selected_option_id, comment)
        self.store.update_mission_state(approval.mission_id, MissionState.WAITING_WORKER)
        return approval

    def reject(self, approval_id: str, comment: str | None = None) -> ApprovalRequest:
        approval = self.store.resolve_approval(approval_id, ApprovalDecision.REJECTED, None, comment)
        self.store.update_mission_state(approval.mission_id, MissionState.FAILED)
        return approval
