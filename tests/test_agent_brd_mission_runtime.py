from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.mission import ApprovalDecision, JobStatus, MissionState
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    InMemoryWorkerRegistry,
    build_brd_local_cut_job_input,
    run_brd_local_cut_worker,
)
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def _payload(tmp_path: Path, *, port_status: str = "ready") -> dict:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    return build_brd_local_cut_job_input(
        layout_file=layout_file,
        signal_nets=["56G_TX0_P", "56G_TX0_N"],
        reference_nets=["GND"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        artifact_dir=tmp_path / "artifacts",
        target_metrics=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        port_candidates={"status": port_status, "candidates": [{"id": "p1", "label": "TX0-GND"}]},
    )


def test_brd_mission_reaches_model_review_checkpoint(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("构建 local cut", [], [])
    job = runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))

    result = runtime.execute_next_job(mission.mission_id, "worker-1")

    assert result.status == JobStatus.SUCCEEDED
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert runtime.get_mission(mission.mission_id).state == MissionState.EVALUATING
    events = [event.event_type.value for event in runtime.list_events(mission.mission_id)]
    assert "checkpoint_created" in events


def test_ambiguous_ports_move_mission_to_approval_and_resume_without_duplicate_job(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("构建 local cut", [], [])
    job = runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path, port_status="ambiguous"))

    result = runtime.execute_next_job(mission.mission_id, "worker-1")

    assert result.status == JobStatus.SUCCEEDED
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert runtime.get_mission(mission.mission_id).state == MissionState.WAITING_APPROVAL

    approval_events = [event for event in runtime.list_events(mission.mission_id) if event.event_type.value == "approval_requested"]
    approval_id = approval_events[-1].payload["approval_id"]
    approved = ApprovalService(runtime.store).approve(approval_id, selected_option_id="p1", comment="确认端口")

    assert approved.decision == ApprovalDecision.APPROVED
    assert runtime.get_mission(mission.mission_id).state == MissionState.WAITING_WORKER
    assert len(runtime.list_jobs(mission.mission_id)) == 1
