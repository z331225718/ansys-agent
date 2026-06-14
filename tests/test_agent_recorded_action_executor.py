from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.actions import (
    ActionDecision,
    ActionRecord,
    ActionStatus,
    RealAedtActionAdapter,
    approve_action,
    execute_approved_action,
    request_action_approval,
)
from aedt_agent.agent.mission import MissionRecord, MissionState
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def _write_channel(touchstone: Path, tdr: Path, *, reflection_magnitude: float, peak_impedance: float) -> None:
    touchstone.write_text(
        "# GHz S MA R 50\n"
        f"0 0.05 0 0.9 0 0.9 0 0.05 0\n"
        f"18 {reflection_magnitude} 0 0.8 0 0.8 0 0.05 0\n"
        f"67 0.04 0 0.7 0 0.7 0 0.04 0\n",
        encoding="utf-8",
    )
    tdr.write_text(
        f"time_ps,impedance_ohm\n0,100\n10,104\n20,{peak_impedance}\n30,101\n",
        encoding="utf-8",
    )


def _approved_action(tmp_path, *, before_reflection, before_peak, after_reflection, after_peak):
    before_touchstone = tmp_path / "before.s2p"
    before_tdr = tmp_path / "before.csv"
    after_touchstone = tmp_path / "after.s2p"
    after_tdr = tmp_path / "after.csv"
    _write_channel(before_touchstone, before_tdr, reflection_magnitude=before_reflection, peak_impedance=before_peak)
    _write_channel(after_touchstone, after_tdr, reflection_magnitude=after_reflection, peak_impedance=after_peak)
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "优化 BRD void", [], []))
    action = ActionRecord.create(
        "action-1",
        "mission-1",
        {"layer": "ART03", "region_ref": "via-1", "shape": "circle"},
        {"variable": "r_cut", "old_value_mil": 13.0, "new_value_mil": 14.0, "delta_mil": 1.0},
        {"min_value_mil": 10.0, "max_value_mil": 20.0, "max_abs_delta_mil": 2.0},
        {"evidence_package_id": "evidence-1", "summary": "18GHz RL fail"},
        "recorded",
        {
            "before_touchstone": str(before_touchstone),
            "before_tdr": str(before_tdr),
            "after_touchstone": str(after_touchstone),
            "after_tdr": str(after_tdr),
            "frequency_stop_ghz": 67.0,
            "rl_target_db": -20.0,
            "tdr_target_ohm": 100.0,
        },
    )
    store.create_action(action)
    approval = request_action_approval(store, action.action_id)
    approved = approve_action(store, approval.approval_id, action.action_id, action.digest)
    return store, approved, [before_touchstone, before_tdr, after_touchstone, after_tdr]


def test_improved_recorded_action_is_accepted_without_modifying_sources(tmp_path):
    store, action, paths = _approved_action(
        tmp_path,
        before_reflection=0.4,
        before_peak=112.0,
        after_reflection=0.08,
        after_peak=103.0,
    )
    contents = [path.read_bytes() for path in paths]

    result = execute_approved_action(store, action.action_id)

    updated = store.get_action(action.action_id)
    execution = store.list_action_executions(action.action_id)[0]
    assert result["decision"] == "accept"
    assert updated.status == ActionStatus.ACCEPTED
    assert updated.decision == ActionDecision.ACCEPT
    assert execution.result["accepted_artifact_refs"] == [str(paths[2]), str(paths[3])]
    assert [path.read_bytes() for path in paths] == contents


def test_regressed_recorded_action_is_rolled_back_and_keeps_after_audit(tmp_path):
    store, action, paths = _approved_action(
        tmp_path,
        before_reflection=0.08,
        before_peak=103.0,
        after_reflection=0.4,
        after_peak=112.0,
    )

    result = execute_approved_action(store, action.action_id)

    updated = store.get_action(action.action_id)
    execution = store.list_action_executions(action.action_id)[0]
    assert result["decision"] == "rollback"
    assert updated.status == ActionStatus.ROLLED_BACK
    assert execution.result["accepted_artifact_refs"] == [str(paths[0]), str(paths[1])]
    assert execution.after_artifact_refs == [str(paths[2]), str(paths[3])]
    assert all(path.exists() for path in paths)


def test_mixed_recorded_action_waits_for_review(tmp_path):
    store, action, _ = _approved_action(
        tmp_path,
        before_reflection=0.4,
        before_peak=103.0,
        after_reflection=0.08,
        after_peak=112.0,
    )

    result = execute_approved_action(store, action.action_id)

    updated = store.get_action(action.action_id)
    assert result["decision"] == "review"
    assert updated.status == ActionStatus.WAITING_APPROVAL
    assert updated.approval_id != action.approval_id
    assert store.get_mission(action.mission_id).state == MissionState.WAITING_APPROVAL


def test_unchanged_recorded_action_is_rolled_back(tmp_path):
    store, action, _ = _approved_action(
        tmp_path,
        before_reflection=0.2,
        before_peak=108.0,
        after_reflection=0.2,
        after_peak=108.0,
    )

    result = execute_approved_action(store, action.action_id)

    assert result["comparison"]["status"] == "unchanged"
    assert result["decision"] == "rollback"
    assert store.get_action(action.action_id).status == ActionStatus.ROLLED_BACK


def test_real_aedt_action_adapter_fails_closed(tmp_path):
    store, action, _ = _approved_action(
        tmp_path,
        before_reflection=0.4,
        before_peak=112.0,
        after_reflection=0.08,
        after_peak=103.0,
    )

    with pytest.raises(RuntimeError, match="real_aedt action adapter is not enabled"):
        RealAedtActionAdapter().apply(action)
