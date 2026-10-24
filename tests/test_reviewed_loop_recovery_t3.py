from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.mission import (
    ApprovalRequest,
    GraphHandoffRecord,
    GraphRunRecord,
    GraphRunStatus,
    MissionRecord,
    MissionState,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.interactive.workflows import AssistantWorkflowManager, _default_runtime_factory


GRAPH_ID = "graph-reviewed"
MISSION_ID = "mission-reviewed"
NODE_ID = "node-reviewed"
BINDING = {"port": 50051, "project": "reviewed.aedt", "design": "layout"}


class Live:
    def workflow_binding(self, _session_id):
        return dict(BINDING)

    def register_guarded_preview(self, _session_id, *, action, result):
        return {**result, "action": action}

    def authorize_guarded_preview(self, *_args, **_kwargs):
        return None


def _seed(db_path: Path) -> str:
    store = SQLiteMissionStore(db_path)
    store.create_mission(
        MissionRecord.create(
            MISSION_ID,
            "reviewed loop",
            [
                {"id": "approve", "label": "Approve"},
                {"kind": "assistant_workflow_binding", "target_binding": BINDING},
            ],
        )
    )
    approval = ApprovalService(store).request_approval(
        MISSION_ID,
        "reviewed model gate",
        [{"id": "approve", "label": "Approve"}],
    )
    store.create_graph_run(
        GraphRunRecord.create(
            GRAPH_ID,
            MISSION_ID,
            "brd_reviewed_model_optimize_loop",
            1,
            1,
            initial_payload={"run_root": str(db_path.parent / "run")},
        )
    )
    store.create_node_run(
        NodeRunRecord.create(
            NODE_ID,
            GRAPH_ID,
            MISSION_ID,
            "worker",
            "worker",
            "worker",
            1,
            {},
        )
    )
    store.update_node_run_status(
        NODE_ID,
        NodeRunStatus.WAITING_APPROVAL,
        output_payload={"approval_id": approval.approval_id},
        edge_decision="waiting_approval",
    )
    store.update_graph_run_status(
        GRAPH_ID,
        GraphRunStatus.WAITING_APPROVAL,
        current_node_id="worker",
    )
    store.create_graph_handoff(
        GraphHandoffRecord.create(
            "handoff-reviewed",
            GRAPH_ID,
            MISSION_ID,
            "edge-reviewed",
            NODE_ID,
            "worker",
            "decider",
            "waiting_approval",
            {"kind": "reviewed_payload", "run_root": str(db_path.parent / "run")},
        )
    )
    return approval.approval_id


def _factory(paths: list[Path]):
    def factory(db_path: Path):
        paths.append(Path(db_path))
        return AgentRuntime(SQLiteMissionStore(db_path))

    return factory


def _counts(store: SQLiteMissionStore) -> dict[str, int]:
    return {
        "missions": len(store.list_missions()),
        "graphs": len(store.list_graph_runs(MISSION_ID)),
        "nodes": len(store.list_node_runs(GRAPH_ID)),
        "handoffs": len(store.list_graph_handoffs(GRAPH_ID)),
    }


def test_reviewed_loop_status_survives_runtime_and_manager_restart(tmp_path):
    db = tmp_path / "missions.db"
    approval_id = _seed(db)
    run_root = tmp_path / "run"
    working = run_root / "working" / "reviewed.aedt"
    working.parent.mkdir(parents=True)
    working.write_text("working-copy", encoding="utf-8")
    working_hash = hashlib.sha256(working.read_bytes()).hexdigest()

    paths: list[Path] = []
    manager1 = AssistantWorkflowManager(
        live_manager=Live(), db_path=db, runtime_factory=_factory(paths)
    )
    first = manager1.status(GRAPH_ID)
    second = manager1.status(GRAPH_ID)
    assert first == second
    assert paths and paths[0].resolve() == db.resolve()
    assert first["status"] == "waiting_approval"
    assert first["graph_run"]["step_count"] == 0
    assert first["node_runs"][0]["status"] == "waiting_approval"
    assert first["handoffs"][0]["handoff_id"] == "handoff-reviewed"
    assert first["handoffs"][0]["status"] == "pending"
    assert SQLiteMissionStore(db).list_approvals(MISSION_ID)[0].approval_id == approval_id

    store = SQLiteMissionStore(db)
    before = _counts(store)

    # Simulate a fresh CLI runtime and a fresh Assistant manager over the same DB.
    manager1 = None
    manager2 = AssistantWorkflowManager(
        live_manager=Live(), db_path=db, runtime_factory=_factory(paths)
    )
    third = manager2.status(GRAPH_ID)
    assert third == first
    after = _counts(SQLiteMissionStore(db))
    assert after == before
    assert working.exists()
    assert hashlib.sha256(working.read_bytes()).hexdigest() == working_hash

    # Human gate is still a CLI-owned approval; Assistant.status was read-only.
    approval = ApprovalService(SQLiteMissionStore(db)).approve(approval_id, "approve")
    assert approval.decision.value == "approved"
    assert SQLiteMissionStore(db).get_mission(MISSION_ID).state != MissionState.WAITING_APPROVAL


def test_workflow_db_env_and_unknown_graph_are_fail_closed(tmp_path, monkeypatch):
    db = tmp_path / "missions.db"
    _seed(db)
    paths: list[Path] = []
    monkeypatch.setenv("AEDT_AGENT_WORKFLOW_DB", str(db))
    manager = AssistantWorkflowManager(live_manager=Live(), runtime_factory=_factory(paths))
    assert manager.status(GRAPH_ID)["status"] == "waiting_approval"
    assert paths[-1].resolve() == db.resolve()

    other = AssistantWorkflowManager(
        live_manager=Live(), db_path=tmp_path / "other.db", runtime_factory=_factory(paths)
    )
    with pytest.raises(KeyError):
        other.status(GRAPH_ID)


def test_restart_invalidates_in_memory_preview_token(tmp_path):
    db = tmp_path / "missions.db"
    _seed(db)
    manager1 = AssistantWorkflowManager(
        live_manager=Live(),
        db_path=db,
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    preview = manager1.preview_advance("live-1", graph_run_id=GRAPH_ID)
    assert preview["preview_id"].startswith("workflow-preview-")

    manager2 = AssistantWorkflowManager(
        live_manager=Live(),
        db_path=db,
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    with pytest.raises(Exception, match="session-bound preview"):
        manager2.apply_advance(
            "live-1",
            preview_id=preview["preview_id"],
            approval_token="old-token",
        )

def test_default_assistant_runtime_uses_exact_db_and_local_real_profile(tmp_path, monkeypatch):
    import shutil

    import aedt_agent.agent.cli as cli

    profile_path = tmp_path / "local_real_aedt.json"
    shutil.copy2("config/execution_profiles/local_real_aedt.example.json", profile_path)
    db_path = tmp_path / "missions.db"
    captured = {}
    monkeypatch.setenv("AEDT_AGENT_WORKFLOW_PROFILE", str(profile_path))

    def fake_runtime(db, profile=None):
        captured["db"] = db
        captured["profile"] = profile
        return "runtime"

    monkeypatch.setattr(cli, "_runtime_with_workers", fake_runtime)
    assert _default_runtime_factory(db_path) == "runtime"
    assert captured["db"] == db_path
    assert captured["profile"].profile_id == "local-real-aedt"
    assert captured["profile"].profile_id != "safe-recorded"
