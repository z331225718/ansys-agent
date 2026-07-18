from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore
from aedt_agent.interactive.workflows import AssistantWorkflowManager


class _Live:
    def __init__(self) -> None:
        self.authorized: list[tuple[str, str, str]] = []

    def workflow_binding(self, session_id: str) -> dict:
        assert session_id == "live-1"
        return {
            "version": "2024.2",
            "pid": 123,
            "port": 50051,
            "active_project": "demo",
            "active_design": "layout",
        }

    def register_guarded_preview(self, session_id: str, *, action: str, result: dict) -> dict:
        return {
            **result,
            "approval_source": "external_host_only",
            "approval_request": {"action": action},
        }

    def authorize_guarded_preview(
        self,
        session_id: str,
        *,
        action: str,
        preview_id: str,
        approval_token: str,
    ) -> None:
        assert approval_token == "approved"
        self.authorized.append((session_id, action, preview_id))


def _manager(tmp_path: Path) -> AssistantWorkflowManager:
    return AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "missions.db",
        template_ids=("brd_local_cut_build",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )


def test_workflow_catalog_exposes_existing_graph_without_mutating_it(tmp_path: Path):
    manager = _manager(tmp_path)

    catalog = manager.list_workflows()
    inspected = manager.inspect_workflow("brd_local_cut_build")

    assert catalog["execution_model"] == "guarded_graph_step"
    assert catalog["workflows"][0]["workflow_id"] == "brd_local_cut_build"
    assert "brd.local_cut.build" in inspected["worker_capabilities"]
    assert inspected["graph"]["template_id"] == "brd_local_cut_build"


def test_workflow_start_requires_preview_and_creates_graph_without_executing(tmp_path: Path):
    manager = _manager(tmp_path)
    preview = manager.preview_start(
        "live-1",
        workflow_id="brd_local_cut_build",
        goal="Build a reviewed local cut",
        initial_payload={
            "layout_file": "board.aedb",
            "signal_nets": ["D0"],
            "reference_nets": ["GND"],
            "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10},
        },
    )

    started = manager.apply_start(
        "live-1",
        preview_id=preview["preview_id"],
        approval_token="approved",
    )
    status = manager.status(started["graph_run_id"])

    assert started["execution_started"] is False
    assert status["status"] == "running"
    assert status["graph_run"]["step_count"] == 0
    assert status["node_runs"] == []


def test_workflow_advance_is_target_bound_and_one_step_per_approval(tmp_path: Path):
    manager = _manager(tmp_path)
    start_preview = manager.preview_start(
        "live-1",
        workflow_id="brd_local_cut_build",
        goal="Build a reviewed local cut",
        initial_payload={
            "layout_file": "board.aedb",
            "signal_nets": ["D0"],
            "reference_nets": ["GND"],
            "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10},
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start_preview["preview_id"],
        approval_token="approved",
    )
    advance_preview = manager.preview_advance(
        "live-1",
        graph_run_id=started["graph_run_id"],
    )

    status = manager.apply_advance(
        "live-1",
        preview_id=advance_preview["preview_id"],
        approval_token="approved",
    )

    assert status["graph_run"]["step_count"] == 1
    assert len(status["node_runs"]) == 1
