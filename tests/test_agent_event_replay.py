from __future__ import annotations

import json

from aedt_agent.agent.event_replay import replay_graph_run, render_replay_text
from aedt_agent.agent.graph_runner import graph_status
from aedt_agent.agent.mission import (
    ApprovalRequest,
    ArtifactManifest,
    EventType,
    EvidencePackage,
    GraphRunRecord,
    GraphRunStatus,
    JobAttemptRecord,
    JobAttemptStatus,
    MissionRecord,
    MissionState,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path) -> AgentRuntime:
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "event replay", [], []))
    return AgentRuntime(store)


def _create_graph(runtime: AgentRuntime, graph_run_id: str, node_run_id: str):
    store = runtime.store
    store.create_graph_run(
        GraphRunRecord.create(
            graph_run_id,
            "mission-1",
            "replay_test",
            1,
            1,
            initial_payload={"graph_run_id": graph_run_id},
        )
    )
    store.update_graph_run_status(graph_run_id, GraphRunStatus.RUNNING)
    return store.create_node_run(
        NodeRunRecord.create(
            node_run_id,
            graph_run_id,
            "mission-1",
            "worker",
            "worker",
            "worker",
            1,
            {},
        )
    )


def test_replay_isolates_two_graph_runs_and_includes_owned_entities(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.store
    _create_graph(runtime, "graph-a", "node-a")
    _create_graph(runtime, "graph-b", "node-b")

    job_a = store.create_job("mission-1", "fake.worker", "job-a", {}, 30, 0)
    job_b = store.create_job("mission-1", "fake.worker", "job-b", {}, 30, 0)
    store.bind_graph_node_job("graph-a", "worker", 1, job_a.job_id)
    store.bind_graph_node_job("graph-b", "worker", 1, job_b.job_id)
    attempt_a = store.create_job_attempt(
        JobAttemptRecord.create("attempt-a", "mission-1", job_a.job_id, 1, "test")
    )
    store.complete_job_attempt(attempt_a.attempt_id, JobAttemptStatus.SUCCEEDED)
    store.complete_job(job_a.job_id, {"status": "ok"}, ["artifact-a"])

    store.create_artifact_manifest(
        ArtifactManifest.create(
            "artifact-a",
            "mission-1",
            "node",
            "node-a",
            "artifacts/a.json",
            "report",
            "a" * 64,
            1,
        )
    )
    store.create_artifact_manifest(
        ArtifactManifest.create(
            "artifact-b",
            "mission-1",
            "node",
            "node-b",
            "artifacts/b.json",
            "report",
            "b" * 64,
            1,
        )
    )
    store.create_evidence_package(
        EvidencePackage.create(
            "evidence-a",
            "mission-1",
            "node",
            "node-a",
            {"status": "passed"},
            ["artifact-a"],
            {},
        )
    )
    store.create_evidence_package(
        EvidencePackage.create(
            "evidence-b",
            "mission-1",
            "node",
            "node-b",
            {"status": "passed"},
            ["artifact-b"],
            {},
        )
    )
    store.complete_node_run(
        "node-a",
        NodeRunStatus.SUCCEEDED,
        {"status": "ok", "artifact_id": "artifact-a"},
        ["artifact-a"],
        evidence_package_id="evidence-a",
        edge_decision="succeeded",
    )
    store.complete_node_run(
        "node-b",
        NodeRunStatus.SUCCEEDED,
        {"status": "ok", "artifact_id": "artifact-b"},
        ["artifact-b"],
        evidence_package_id="evidence-b",
        edge_decision="succeeded",
    )
    store.update_graph_run_status("graph-a", GraphRunStatus.SUCCEEDED)
    store.update_graph_run_status("graph-b", GraphRunStatus.SUCCEEDED)
    store.update_mission_state("mission-1", MissionState.PLANNING)
    store.append_event(
        "mission-1",
        EventType.GRAPH_RUN_UPDATED,
        {"graph_run_id": "graph-a", "status": "succeeded", "details": "X" * 5000},
    )

    replay = replay_graph_run(runtime, "graph-a")

    serialized = json.dumps(replay, sort_keys=True)
    assert replay["status"] == "succeeded"
    assert replay["event_cursor"] == replay["events"][-1]["sequence"]
    assert replay["entity_counts"] == {
        "graph": 1,
        "node": 1,
        "handoff": 0,
        "job": 1,
        "attempt": 1,
        "artifact": 1,
        "evidence": 1,
        "approval": 0,
        "events": len(replay["events"]),
    }
    assert [event["sequence"] for event in replay["events"]] == sorted(
        event["sequence"] for event in replay["events"]
    )
    assert "graph-b" not in serialized
    assert "node-b" not in serialized
    assert job_b.job_id not in serialized
    assert "artifact-b" not in serialized
    assert "evidence-b" not in serialized
    assert "mission_state_changed" not in {
        event["event_type"] for event in replay["events"]
    }
    assert {event["scope"] for event in replay["events"]} >= {
        "graph",
        "node",
        "job",
        "attempt",
        "artifact",
        "evidence",
    }

    rendered = render_replay_text(replay)
    assert "scope=attempt job_attempt_updated" in rendered
    assert "X" * 100 not in rendered
    assert "payload=" not in rendered

    supervision = graph_status(runtime, "graph-a")["supervision"]
    assert supervision == graph_status(runtime, "graph-a")["supervision"]
    assert supervision["reason"] == "graph_succeeded"
    assert supervision["recommended_action"] == "none"
    assert supervision["event_cursor"] == replay["event_cursor"]


def test_waiting_approval_replay_and_supervision_name_approval_and_node(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.store
    _create_graph(runtime, "graph-waiting", "node-waiting")
    store.create_approval(
        ApprovalRequest.create(
            "approval-waiting",
            "mission-1",
            "review model",
            [{"id": "approve", "label": "Approve"}],
        )
    )
    store.update_node_run_status(
        "node-waiting",
        NodeRunStatus.WAITING_APPROVAL,
        output_payload={"approval_id": "approval-waiting"},
        edge_decision="waiting_approval",
    )
    store.update_graph_run_status(
        "graph-waiting",
        GraphRunStatus.WAITING_APPROVAL,
        current_node_id="worker",
    )

    replay = replay_graph_run(runtime, "graph-waiting")
    supervision = graph_status(runtime, "graph-waiting")["supervision"]

    assert any(event["scope"] == "approval" for event in replay["events"])
    assert replay["entity_counts"]["approval"] == 1
    assert supervision["blocking_node_id"] == "worker"
    assert supervision["reason"] == "waiting_approval:approval-waiting"
    assert supervision["recommended_action"] == "resolve_approval"
    assert "approval-waiting" in supervision["summary"]


def test_failed_graph_supervision_uses_latest_failed_node_and_graph_error(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.store
    _create_graph(runtime, "graph-failed", "node-failed")
    store.complete_node_run(
        "node-failed",
        NodeRunStatus.FAILED,
        {},
        [],
        edge_decision="failed",
        error={"error_class": "worker_crash", "message": "boom"},
    )
    store.update_graph_run_status(
        "graph-failed",
        GraphRunStatus.FAILED,
        current_node_id="worker",
        error={"code": "unhandled_node_outcome", "message": "worker failed"},
    )

    first = graph_status(runtime, "graph-failed")["supervision"]
    second = graph_status(runtime, "graph-failed")["supervision"]

    assert first == second
    assert first["blocking_node_id"] == "worker"
    assert first["reason"] == "unhandled_node_outcome"
    assert first["recommended_action"] == "inspect_or_takeover"
    assert "worker" in first["summary"]
    assert first["counts"]["node"] == 1
