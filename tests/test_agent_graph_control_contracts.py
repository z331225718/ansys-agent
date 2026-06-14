from __future__ import annotations

from aedt_agent.agent.mission import (
    ArtifactManifest,
    EvidencePackage,
    GraphRunRecord,
    GraphRunStatus,
    JobAttemptRecord,
    JobAttemptStatus,
    NodeRunRecord,
    NodeRunStatus,
)


def test_graph_run_record_is_json_ready():
    graph_run = GraphRunRecord.create(
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        template_id="brd_local_cut_build",
        template_version=1,
        plan_version=2,
    )

    payload = graph_run.to_json_dict()

    assert payload["graph_run_id"] == "graph-run-1"
    assert payload["mission_id"] == "mission-1"
    assert payload["template_id"] == "brd_local_cut_build"
    assert payload["template_version"] == 1
    assert payload["plan_version"] == 2
    assert payload["status"] == "created"
    assert payload["started_at"] is None
    assert payload["completed_at"] is None


def test_node_run_record_captures_handoff_and_edge_decision():
    node_run = NodeRunRecord.create(
        node_run_id="node-run-1",
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        node_id="real_build_worker",
        node_role="worker",
        node_kind="worker",
        sequence=3,
        input_payload={"layout_file": "case.brd"},
    )
    completed = node_run.with_completion(
        status=NodeRunStatus.SUCCEEDED,
        output_payload={"status": "built"},
        artifact_refs=["artifacts/model.aedt"],
        evidence_package_id="evidence-1",
        edge_decision="succeeded",
    )

    payload = completed.to_json_dict()

    assert payload["status"] == "succeeded"
    assert payload["output_payload"] == {"status": "built"}
    assert payload["artifact_refs"] == ["artifacts/model.aedt"]
    assert payload["evidence_package_id"] == "evidence-1"
    assert payload["edge_decision"] == "succeeded"
    assert payload["completed_at"] is not None


def test_artifact_manifest_records_provenance_and_checksum():
    artifact = ArtifactManifest.create(
        artifact_id="artifact-1",
        mission_id="mission-1",
        producer_kind="node",
        producer_id="node-run-1",
        path="artifacts/model.aedt",
        kind="aedt_project",
        sha256="a" * 64,
        size_bytes=123,
    )

    payload = artifact.to_json_dict()

    assert payload["producer_kind"] == "node"
    assert payload["producer_id"] == "node-run-1"
    assert payload["kind"] == "aedt_project"
    assert payload["sha256"] == "a" * 64
    assert payload["retention_policy"] == "mission"


def test_evidence_package_keeps_raw_data_as_artifact_refs():
    evidence = EvidencePackage.create(
        evidence_package_id="evidence-1",
        mission_id="mission-1",
        producer_kind="node",
        producer_id="node-run-1",
        summary={"spectral_summary": {"sample_count": 1341}},
        artifact_refs=["artifacts/channel.s4p"],
        token_budget={"summary_tokens": 1200, "raw_trace_policy": "artifact_only"},
    )

    payload = evidence.to_json_dict()

    assert payload["summary"]["spectral_summary"]["sample_count"] == 1341
    assert payload["artifact_refs"] == ["artifacts/channel.s4p"]
    assert payload["token_budget"]["raw_trace_policy"] == "artifact_only"
    assert "0.0,0.1,0.2" not in str(payload["summary"])


def test_job_attempt_record_captures_retry_decision():
    attempt = JobAttemptRecord.create(
        attempt_id="attempt-1",
        mission_id="mission-1",
        job_id="job-1",
        attempt_number=1,
        worker_id="worker-1",
    ).with_completion(
        status=JobAttemptStatus.FAILED,
        error={"error_class": "license_unavailable", "retryable": True},
        retry_decision="retry_with_backoff",
    )

    payload = attempt.to_json_dict()

    assert payload["status"] == "failed"
    assert payload["attempt_number"] == 1
    assert payload["retry_decision"] == "retry_with_backoff"
    assert payload["error"]["retryable"] is True
