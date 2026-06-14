from __future__ import annotations

from uuid import uuid4

from aedt_agent.agent.actions.adapters import RealAedtActionAdapter, RecordedActionAdapter
from aedt_agent.agent.actions.approval import assert_action_approved
from aedt_agent.agent.actions.contracts import (
    ActionDecision,
    ActionExecutionRecord,
    ActionExecutionStatus,
    ActionStatus,
)
from aedt_agent.agent.actions.policy import decide_action_outcome
from aedt_agent.agent.actions.validation import validate_action
from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.mission import MissionState
from aedt_agent.layout.channel_scoring import compare_channel_scores, score_channel_result


def execute_approved_action(store, action_id: str) -> dict:
    action = validate_action(store.get_action(action_id))
    assert_action_approved(store, action)
    adapter = _adapter(action.adapter_mode)
    before_refs = [str(action.adapter_input["before_touchstone"]), str(action.adapter_input["before_tdr"])]
    after_refs = [str(action.adapter_input["after_touchstone"]), str(action.adapter_input["after_tdr"])]
    execution = store.create_action_execution(
        ActionExecutionRecord.create(
            execution_id=str(uuid4()),
            action_id=action.action_id,
            mission_id=action.mission_id,
            adapter_mode=action.adapter_mode,
            before_artifact_refs=before_refs,
            after_artifact_refs=after_refs,
        )
    )
    action = store.update_action(action.with_status(ActionStatus.APPLYING))
    try:
        adapter_result = adapter.apply(action)
        action = store.update_action(action.with_status(ActionStatus.APPLIED))
        before_score = _score(action, before=True)
        after_score = _score(action, before=False)
        comparison = compare_channel_scores(before_score, after_score)
        decision = decide_action_outcome(comparison)
        final_status, accepted_refs, approval_id = _resolve_decision(store, action, decision, comparison, before_refs, after_refs)
        result = {
            "adapter": adapter_result,
            "before_score": before_score,
            "after_score": after_score,
            "comparison": comparison,
            "decision": decision.value,
            "accepted_artifact_refs": accepted_refs,
            "after_artifact_refs_retained": after_refs,
            "final_status": final_status.value,
        }
        store.complete_action_execution(execution.execution_id, ActionExecutionStatus.SUCCEEDED, result=result)
        updated = action.with_status(
            final_status,
            approval_id=approval_id,
            comparison=comparison,
            decision=decision,
        )
        store.update_action(updated)
        if decision != ActionDecision.REVIEW:
            store.update_mission_state(action.mission_id, MissionState.EVALUATING)
        return result
    except Exception as exc:
        error = {"message": str(exc), "error_type": type(exc).__name__}
        store.complete_action_execution(execution.execution_id, ActionExecutionStatus.FAILED, error=error)
        store.update_action(action.with_status(ActionStatus.FAILED, error=error))
        raise


def _score(action, *, before: bool) -> dict:
    prefix = "before" if before else "after"
    return score_channel_result(
        action.adapter_input[f"{prefix}_touchstone"],
        action.adapter_input[f"{prefix}_tdr"],
        frequency_start_ghz=float(action.adapter_input.get("frequency_start_ghz", 0.0)),
        frequency_stop_ghz=float(action.adapter_input.get("frequency_stop_ghz", 67.0)),
        rl_target_db=float(action.adapter_input.get("rl_target_db", -20.0)),
        tdr_target_ohm=float(action.adapter_input.get("tdr_target_ohm", 100.0)),
    )


def _resolve_decision(store, action, decision, comparison, before_refs, after_refs):
    if decision == ActionDecision.ACCEPT:
        return ActionStatus.ACCEPTED, after_refs, action.approval_id
    if decision == ActionDecision.ROLLBACK:
        return ActionStatus.ROLLED_BACK, before_refs, action.approval_id
    approval = ApprovalService(store).request_approval(
        action.mission_id,
        "mixed_action_result_review",
        [
            {
                "id": "accept-action-result",
                "label": "接受混合结果",
                "action_id": action.action_id,
                "action_digest": action.digest,
                "comparison_status": comparison["status"],
            },
            {
                "id": "rollback-action-result",
                "label": "回滚混合结果",
                "action_id": action.action_id,
                "action_digest": action.digest,
                "comparison_status": comparison["status"],
            },
        ],
    )
    return ActionStatus.WAITING_APPROVAL, before_refs, approval.approval_id


def _adapter(adapter_mode: str):
    if adapter_mode == "recorded":
        return RecordedActionAdapter()
    if adapter_mode == "real_aedt":
        return RealAedtActionAdapter()
    raise ValueError(f"unsupported adapter_mode: {adapter_mode}")
