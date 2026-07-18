from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore
from aedt_agent.interactive.workflows import AssistantWorkflowManager


class _Live:
    def __init__(self) -> None:
        self.authorized: list[tuple[str, str, str]] = []
        self.analysis_statuses: list[dict] = []
        self.export_root: Path | None = None
        self.export_spec: dict = {}

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

    def layout_routing_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "path_count": 2,
            "nets": ["N1"],
            "layers": ["L1"],
            "design_unchanged": True,
        }

    def layout_object_inventory(self, session_id: str, **kwargs) -> dict:
        return {"categories": {}, "unavailable_categories": [], "design_unchanged": True}

    def variable_inventory(self, session_id: str, **kwargs) -> dict:
        return {"count": 1, "variables": [], "design_unchanged": True}

    def setup_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "setup_count": 1,
            "setups": [{"name": "SetupL", "sweeps": ["Sweep1"]}],
            "design_unchanged": True,
        }

    def list_layout_paths(self, session_id: str, **kwargs) -> dict:
        return {
            "count": 2,
            "paths": [
                {"name": "line1", "width_expression": "4.3mil"},
                {"name": "line2", "width_expression": "4.3mil"},
            ],
        }

    def preview_layout_width(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "width-preview-1",
            "target_count": 2,
            "approval_request": {"action": "layout.path_width.parameterize"},
        }

    def apply_layout_width(self, session_id: str, *, preview_id: str, approval_token: str) -> dict:
        assert preview_id == "width-preview-1"
        assert approval_token == "operation-approved"
        return {
            "status": "verified",
            "target_count": 2,
            "verified_count": 2,
            "project_saved": False,
        }

    def preview_hfss_analysis_start(self, session_id: str, **kwargs) -> dict:
        assert kwargs["product"] == "layout"
        return {
            "preview_id": "solve-preview-1",
            "approval_request": {"action": "hfss.analysis.start"},
        }

    def apply_hfss_analysis_start(self, session_id: str, *, preview_id: str, approval_token: str) -> dict:
        assert preview_id == "solve-preview-1"
        assert approval_token == "solve-approved"
        return {
            "status": "submitted",
            "started": True,
            "blocking": False,
            "run_id": "run-1",
            "resources": {"cores": 4, "tasks": 1, "gpus": 0},
            "project_saved": False,
        }

    def hfss_analysis_status(self, session_id: str, **kwargs) -> dict:
        if self.analysis_statuses:
            return self.analysis_statuses.pop(0)
        return {"running": True, "latest_run": {"run_id": "run-1"}}

    def preview_hfss_export(self, session_id: str, **kwargs) -> dict:
        assert kwargs["product"] == "layout"
        self.export_spec = {
            **kwargs,
            "artifact_name": kwargs["artifact_name"] or kwargs["report_name"] or kwargs["setup_name"],
        }
        return {
            "preview_id": "export-preview-1",
            **self.export_spec,
            "approval_request": {"action": "hfss.results.export"},
            "approval_required": True,
            "project_unchanged": True,
        }

    def apply_hfss_export(self, session_id: str, *, preview_id: str, approval_token: str) -> dict:
        assert preview_id == "export-preview-1"
        assert approval_token == "export-approved"
        assert self.export_root is not None
        output = self.export_root / preview_id
        output.mkdir(parents=True)
        artifact_path = output / f"{self.export_spec['artifact_name']}.s2p"
        artifact_path.write_text("# Hz S RI R 50\n", encoding="ascii")
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        artifact = {"path": str(artifact_path), "sha256": digest, "bytes": artifact_path.stat().st_size}
        spec = {
            "product": "layout",
            "export_kind": self.export_spec["export_kind"],
            "setup_name": self.export_spec["setup_name"],
            "sweep_name": self.export_spec["sweep_name"],
            "report_name": self.export_spec["report_name"],
            "artifact_name": self.export_spec["artifact_name"],
        }
        manifest = {
            "project_name": "demo",
            "design_name": "layout",
            "spec": spec,
            "artifact": artifact,
        }
        manifest_path = output / f"{artifact_path.name}.evidence.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return {
            "status": "verified",
            "product": "layout",
            "artifact": artifact,
            "manifest_path": str(manifest_path),
            "project_unchanged": True,
            "project_saved": False,
        }


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


def test_default_workflow_catalog_includes_live_monitor_and_export(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "catalog-missions.db",
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )

    descriptors = {item["workflow_id"]: item for item in manager.list_workflows()["workflows"]}

    assert descriptors["layout_live_solve_monitor"]["risk"] == "read_only"
    assert descriptors["layout_live_solve_monitor"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_results_export"]["risk"] == "persistent_write"
    assert descriptors["layout_live_results_export"]["attached_live_session_reuse"] is True
    assert manager.inspect_workflow("layout_live_solve_monitor")["graph"]["edges"][1]["on"] == "running"


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


def test_live_layout_audit_workflow_reuses_bound_session_for_graph_handlers(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "live-missions.db",
        template_ids=("layout_live_audit",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start_preview = manager.preview_start(
        "live-1",
        workflow_id="layout_live_audit",
        goal="Audit the active layout",
        initial_payload={"selector": {"nets": ["N1"]}},
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start_preview["preview_id"],
        approval_token="approved",
    )

    for _ in range(2):
        advance_preview = manager.preview_advance(
            "live-1",
            graph_run_id=started["graph_run_id"],
        )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance_preview["preview_id"],
            approval_token="approved",
        )

    assert report["status"] == "succeeded"
    assert "_assistant_live" not in report["graph_run"]["initial_payload"]
    assert "_assistant_live" not in report["node_runs"][0]["input_payload"]
    assert report["node_runs"][0]["output_payload"]["live_session_reused"] is True
    assert report["node_runs"][1]["output_payload"]["summary"]["path_count"] == 2

    with pytest.raises(ValueError, match="reserved server-owned field"):
        manager.preview_start(
            "live-1",
            workflow_id="layout_live_audit",
            goal="Try to forge a binding",
            initial_payload={"_assistant_live": {"live_session_id": "forged"}},
        )


def test_live_width_workflow_keeps_operation_token_out_of_graph_state(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "width-missions.db",
        template_ids=("layout_live_parameterize_width",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_parameterize_width",
        goal="Parameterize matching path widths",
        initial_payload={
            "selector": {"target_width": "4.3mil"},
            "variable_name": "W_line",
            "variable_value": "4.3mil",
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "width-preview-1"
            with pytest.raises(Exception, match="nested live operation preview"):
                manager.apply_advance(
                    "live-1",
                    preview_id=advance["preview_id"],
                    approval_token="approved",
                )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="operation-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    serialized = str(report)
    assert "operation-approved" not in serialized
    assert report["node_runs"][-1]["output_payload"]["summary"]["verified_count"] == 2


def test_live_layout_solve_workflow_validates_setup_and_starts_non_blocking(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "solve-missions.db",
        template_ids=("layout_live_solve_start",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_solve_start",
        goal="Start the approved live layout solve",
        initial_payload={
            "setup_name": "SetupL",
            "sweep_name": "Sweep1",
            "cores": 4,
            "tasks": 1,
            "gpus": 0,
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="solve-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["run_id"] == "run-1"
    assert "solve-approved" not in str(report)


def test_live_layout_monitor_workflow_uses_bounded_graph_loop(tmp_path: Path):
    live = _Live()
    live.analysis_statuses = [
        {"product": "layout", "running": True, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "running"}},
        {"product": "layout", "running": True, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "running"}},
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}},
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "monitor-missions.db",
        template_ids=("layout_live_solve_monitor",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_solve_monitor",
        goal="Monitor the approved live layout solve",
        initial_payload={"setup_name": "SetupL"},
        max_steps=16,
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for _ in range(5):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
        )

    assert report is not None and report["status"] == "succeeded"
    poll_runs = [item for item in report["node_runs"] if item["node_id"] == "poll_analysis"]
    assert len(poll_runs) == 3
    assert [item["edge_decision"] for item in poll_runs] == ["running", "running", "stopped"]
    assert all("_handoffs" not in item["output_payload"] for item in poll_runs)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["poll_count"] == 3
    assert scorecard["summary"]["solve_success_verified"] is False


def test_live_layout_results_export_workflow_writes_verified_artifacts(tmp_path: Path):
    live = _Live()
    live.export_root = tmp_path / "exports"
    live.analysis_statuses = [
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}}
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "export-missions.db",
        template_ids=("layout_live_results_export",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_results_export",
        goal="Export approved live layout Touchstone evidence",
        initial_payload={
            "export_kind": "touchstone",
            "setup_name": "SetupL",
            "sweep_name": "Sweep1",
        },
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "export-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="export-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    scorecard = report["node_runs"][-1]
    assert scorecard["output_payload"]["status"] == "passed"
    assert scorecard["output_payload"]["summary"]["artifact_path"].endswith("SetupL.s2p")
    assert len(scorecard["artifact_refs"]) == 2
    assert "export-approved" not in str(report)
