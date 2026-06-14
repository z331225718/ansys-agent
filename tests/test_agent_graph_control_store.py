from __future__ import annotations

from aedt_agent.agent.mission import (
    ArtifactManifest,
    EvidencePackage,
    GraphHandoffRecord,
    GraphHandoffStatus,
    GraphRunRecord,
    NodeRunRecord,
    NodeRunStatus,
    MissionRecord,
)
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def _store_with_mission(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "构建 BRD graph", [], []))
    return store


def test_graph_run_survives_store_restart(tmp_path):
    store = _store_with_mission(tmp_path)
    graph_run = GraphRunRecord.create(
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        template_id="brd_local_cut_build",
        template_version=1,
        plan_version=1,
    )

    store.create_graph_run(graph_run)

    reopened = SQLiteMissionStore(tmp_path / "mission.db")
    loaded = reopened.get_graph_run("graph-run-1")

    assert loaded is not None
    assert loaded.template_id == "brd_local_cut_build"
    assert loaded.status.value == "created"
    assert reopened.list_graph_runs("mission-1") == [loaded]


def test_graph_run_snapshot_and_step_count_survive_restart(tmp_path):
    store = _store_with_mission(tmp_path)
    graph_run = GraphRunRecord.create(
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        template_id="parallel",
        template_version=1,
        plan_version=1,
        template_snapshot={"id": "parallel", "nodes": []},
        initial_payload={"value": 3},
        max_steps=8,
    )
    store.create_graph_run(graph_run)

    advanced = store.increment_graph_step("graph-run-1")
    reopened = SQLiteMissionStore(tmp_path / "mission.db")
    loaded = reopened.get_graph_run("graph-run-1")

    assert advanced.step_count == 1
    assert loaded.template_snapshot == {"id": "parallel", "nodes": []}
    assert loaded.initial_payload == {"value": 3}
    assert loaded.step_count == 1
    assert loaded.max_steps == 8


def test_graph_handoff_can_be_created_consumed_and_reloaded(tmp_path):
    store = _store_with_mission(tmp_path)
    store.create_graph_run(GraphRunRecord.create("graph-run-1", "mission-1", "parallel", 1, 1))
    handoff = GraphHandoffRecord.create(
        handoff_id="handoff-1",
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        edge_id="source-worker",
        source_node_run_id="node-run-1",
        from_node="source",
        to_node="worker",
        outcome="succeeded",
        payload={"value": 1},
    )

    store.create_graph_handoff(handoff)
    consumed = store.consume_graph_handoffs(["handoff-1"], "node-run-2")
    reopened = SQLiteMissionStore(tmp_path / "mission.db")

    assert consumed[0].status == GraphHandoffStatus.CONSUMED
    assert reopened.list_graph_handoffs("graph-run-1")[0].consumed_by_node_run_id == "node-run-2"


def test_graph_node_job_binding_is_stable(tmp_path):
    store = _store_with_mission(tmp_path)
    store.create_graph_run(GraphRunRecord.create("graph-run-1", "mission-1", "parallel", 1, 1))
    job = store.create_job("mission-1", "fake.worker", "worker-1", {}, 30, 0)

    store.bind_graph_node_job("graph-run-1", "worker", 1, job.job_id)

    assert store.get_graph_node_job("graph-run-1", "worker", 1) == job.job_id


def test_node_run_completion_persists_handoff_output(tmp_path):
    store = _store_with_mission(tmp_path)
    store.create_graph_run(GraphRunRecord.create("graph-run-1", "mission-1", "brd_local_cut_build", 1, 1))
    node_run = NodeRunRecord.create(
        node_run_id="node-run-1",
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        node_id="real_build_worker",
        node_role="worker",
        node_kind="worker",
        sequence=1,
        input_payload={"layout_file": "case.brd"},
    )

    store.create_node_run(node_run)
    completed = store.complete_node_run(
        "node-run-1",
        status=NodeRunStatus.SUCCEEDED,
        output_payload={"status": "built"},
        artifact_refs=["artifacts/model.aedt"],
        evidence_package_id="evidence-1",
        edge_decision="succeeded",
    )

    assert completed.status == NodeRunStatus.SUCCEEDED
    assert completed.output_payload == {"status": "built"}
    assert completed.artifact_refs == ["artifacts/model.aedt"]
    assert completed.evidence_package_id == "evidence-1"
    assert store.list_node_runs("graph-run-1") == [completed]


def test_artifact_manifest_records_checksum_and_producer(tmp_path):
    store = _store_with_mission(tmp_path)
    artifact = ArtifactManifest.create(
        artifact_id="artifact-1",
        mission_id="mission-1",
        producer_kind="node",
        producer_id="node-run-1",
        path="artifacts/model.aedt",
        kind="aedt_project",
        sha256="b" * 64,
        size_bytes=456,
    )

    store.create_artifact_manifest(artifact)

    loaded = store.list_artifact_manifests("mission-1")
    assert loaded == [artifact]
    assert loaded[0].producer_id == "node-run-1"
    assert loaded[0].sha256 == "b" * 64


def test_evidence_package_persists_summary_artifact_refs_and_budget(tmp_path):
    store = _store_with_mission(tmp_path)
    evidence = EvidencePackage.create(
        evidence_package_id="evidence-1",
        mission_id="mission-1",
        producer_kind="node",
        producer_id="node-run-1",
        summary={"scorecard": {"status": "passed"}},
        artifact_refs=["artifacts/channel.s4p"],
        token_budget={"summary_tokens": 900, "raw_trace_policy": "artifact_only"},
    )

    store.create_evidence_package(evidence)

    assert store.get_evidence_package("evidence-1") == evidence
    assert store.list_evidence_packages("mission-1") == [evidence]


def test_graph_control_records_are_audited_with_monotonic_events(tmp_path):
    store = _store_with_mission(tmp_path)
    store.create_graph_run(GraphRunRecord.create("graph-run-1", "mission-1", "brd_local_cut_build", 1, 1))
    store.create_node_run(
        NodeRunRecord.create(
            "node-run-1",
            "graph-run-1",
            "mission-1",
            "real_build_worker",
            "worker",
            "worker",
            1,
            {},
        )
    )
    store.create_artifact_manifest(
        ArtifactManifest.create("artifact-1", "mission-1", "node", "node-run-1", "artifacts/model.aedt", "aedt_project", "", 0)
    )
    store.create_evidence_package(
        EvidencePackage.create("evidence-1", "mission-1", "node", "node-run-1", {"status": "passed"}, [], {})
    )

    events = store.list_events("mission-1")

    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert [event.event_type.value for event in events] == [
        "mission_created",
        "graph_run_created",
        "node_run_created",
        "artifact_manifest_created",
        "evidence_package_created",
    ]
