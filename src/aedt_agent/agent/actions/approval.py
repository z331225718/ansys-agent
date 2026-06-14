from __future__ import annotations

from aedt_agent.agent.actions.contracts import ActionRecord, ActionStatus
from aedt_agent.agent.actions.validation import validate_action
from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.mission import ApprovalDecision


def request_action_approval(store, action_id: str):
    action = validate_action(store.get_action(action_id))
    if action.status != ActionStatus.PROPOSED:
        raise ValueError(f"action is not proposed: {action.status.value}")
    option = {
        "id": "approve-action",
        "label": "批准受控 void 调整",
        "action_id": action.action_id,
        "action_digest": action.digest,
    }
    approval = ApprovalService(store).request_approval(
        action.mission_id,
        "controlled_action_approval",
        [option],
    )
    store.update_action(action.with_status(ActionStatus.WAITING_APPROVAL, approval_id=approval.approval_id))
    return approval


def approve_action(
    store,
    approval_id: str,
    action_id: str,
    action_digest: str,
    comment: str | None = None,
) -> ActionRecord:
    action = store.get_action(action_id)
    if action.approval_id != approval_id:
        raise ValueError("approval is not bound to action")
    if action.digest != action_digest:
        raise ValueError("action digest mismatch")
    approval = store.get_approval(approval_id)
    option = _approval_option(approval, action_id)
    if option.get("action_digest") != action_digest:
        raise ValueError("approval option digest mismatch")
    ApprovalService(store).approve(approval_id, selected_option_id="approve-action", comment=comment)
    return store.update_action(action.with_status(ActionStatus.APPROVED))


def reject_action(store, approval_id: str, action_id: str, comment: str | None = None) -> ActionRecord:
    action = store.get_action(action_id)
    if action.approval_id != approval_id:
        raise ValueError("approval is not bound to action")
    ApprovalService(store).reject(approval_id, comment=comment)
    return store.update_action(action.with_status(ActionStatus.REJECTED))


def assert_action_approved(store, action: ActionRecord) -> None:
    if action.status != ActionStatus.APPROVED:
        raise ValueError(f"action is not approved: {action.status.value}")
    if not action.approval_id:
        raise ValueError("action approval_id is missing")
    approval = store.get_approval(action.approval_id)
    if approval.decision != ApprovalDecision.APPROVED:
        raise ValueError("action approval is not approved")
    if approval.selected_option_id != "approve-action":
        raise ValueError("action approval selected option is invalid")
    option = _approval_option(approval, action.action_id)
    if option.get("action_digest") != action.digest:
        raise ValueError("approved action digest does not match current action")


def _approval_option(approval, action_id: str) -> dict:
    for option in approval.options:
        if option.get("id") == "approve-action" and option.get("action_id") == action_id:
            return dict(option)
    raise ValueError("approval option is not bound to action")
