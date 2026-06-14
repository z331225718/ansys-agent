from __future__ import annotations

from dataclasses import replace

import pytest

from aedt_agent.agent.actions import (
    ActionRecord,
    ActionStatus,
    assert_action_approved,
    approve_action,
    reject_action,
    request_action_approval,
)
from aedt_agent.agent.mission import ApprovalDecision, MissionRecord, MissionState
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def _action():
    return ActionRecord.create(
        "action-1",
        "mission-1",
        {"layer": "ART03", "region_ref": "via-1", "shape": "circle"},
        {"variable": "r_cut", "old_value_mil": 13.0, "new_value_mil": 14.0, "delta_mil": 1.0},
        {"min_value_mil": 10.0, "max_value_mil": 20.0, "max_abs_delta_mil": 2.0},
        {"evidence_package_id": "evidence-1", "summary": "RL fail"},
        "recorded",
        {
            "before_touchstone": "before.s2p",
            "before_tdr": "before.csv",
            "after_touchstone": "after.s2p",
            "after_tdr": "after.csv",
        },
    )


def _store(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "优化 BRD", [], []))
    store.create_action(_action())
    return store


def test_request_action_approval_binds_action_id_and_digest(tmp_path):
    store = _store(tmp_path)

    approval = request_action_approval(store, "action-1")

    action = store.get_action("action-1")
    option = approval.options[0]
    assert action.status == ActionStatus.WAITING_APPROVAL
    assert action.approval_id == approval.approval_id
    assert store.get_mission("mission-1").state == MissionState.WAITING_APPROVAL
    assert option["id"] == "approve-action"
    assert option["action_id"] == action.action_id
    assert option["action_digest"] == action.digest


def test_approve_action_requires_matching_digest(tmp_path):
    store = _store(tmp_path)
    approval = request_action_approval(store, "action-1")

    approved = approve_action(store, approval.approval_id, "action-1", _action().digest, comment="批准")

    assert approved.status == ActionStatus.APPROVED
    assert store.get_approval(approval.approval_id).decision == ApprovalDecision.APPROVED
    assert store.get_mission("mission-1").state == MissionState.WAITING_WORKER
    assert_action_approved(store, approved)


def test_approve_action_rejects_digest_mismatch(tmp_path):
    store = _store(tmp_path)
    approval = request_action_approval(store, "action-1")

    with pytest.raises(ValueError, match="action digest mismatch"):
        approve_action(store, approval.approval_id, "action-1", "0" * 64)


def test_assert_action_approved_rejects_modified_action(tmp_path):
    store = _store(tmp_path)
    approval = request_action_approval(store, "action-1")
    approved = approve_action(store, approval.approval_id, "action-1", _action().digest)
    modified = replace(approved, digest="f" * 64)

    with pytest.raises(ValueError, match="approved action digest does not match"):
        assert_action_approved(store, modified)


def test_reject_action_marks_action_rejected(tmp_path):
    store = _store(tmp_path)
    approval = request_action_approval(store, "action-1")

    rejected = reject_action(store, approval.approval_id, "action-1", comment="风险过高")

    assert rejected.status == ActionStatus.REJECTED
    assert store.get_approval(approval.approval_id).decision == ApprovalDecision.REJECTED
