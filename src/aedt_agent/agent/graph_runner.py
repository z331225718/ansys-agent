from __future__ import annotations

from typing import Any
from uuid import uuid4

from aedt_agent.agent.graph_template import GraphNode, GraphTemplate
from aedt_agent.agent.mission import EvidencePackage, GraphRunRecord, GraphRunStatus, JobStatus, NodeRunRecord, NodeRunStatus
from aedt_agent.agent.scorecard import score_mission


def run_graph_once(runtime, mission_id: str, template: GraphTemplate, *, worker_id: str = "graph") -> dict[str, Any]:
    """Execute one queued job that is allowed by the graph template, then score it."""

    return run_graph_sequential(runtime, mission_id, template, worker_id=worker_id)


def run_graph_sequential(runtime, mission_id: str, template: GraphTemplate, *, worker_id: str = "graph") -> dict[str, Any]:
    """Execute the first ready worker node and persist graph/node/evidence records."""

    mission = runtime.get_mission(mission_id)
    graph_run = runtime.store.create_graph_run(
        GraphRunRecord.create(
            graph_run_id=str(uuid4()),
            mission_id=mission_id,
            template_id=template.template_id,
            template_version=template.version,
            plan_version=mission.plan_version,
        )
    )
    graph_run = runtime.store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.RUNNING)

    queued = [job for job in runtime.list_jobs(mission_id) if job.status == JobStatus.QUEUED]
    if not queued:
        error = {"message": "no queued job"}
        runtime.store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.FAILED, error=error)
        return {
            "status": "failed",
            "template_id": template.template_id,
            "mission_id": mission_id,
            "graph_run": runtime.store.get_graph_run(graph_run.graph_run_id).to_json_dict(),
            "error": error,
        }
    job = queued[0]
    node = _node_for_capability(template, job.capability)
    if node is None:
        runtime.store.update_graph_run_status(
            graph_run.graph_run_id,
            GraphRunStatus.FAILED,
            error={"message": f"queued job capability is not allowed by graph template: {job.capability}"},
        )
        raise ValueError(f"queued job capability is not allowed by graph template: {job.capability}")

    node_run = runtime.store.create_node_run(
        NodeRunRecord.create(
            node_run_id=str(uuid4()),
            graph_run_id=graph_run.graph_run_id,
            mission_id=mission_id,
            node_id=node.node_id,
            node_role=node.role,
            node_kind=node.kind,
            sequence=1,
            input_payload=job.input_payload,
        )
    )
    runtime.store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.RUNNING, current_node_id=node.node_id)
    result = runtime.execute_next_job(mission_id, worker_id)
    scorecard = score_mission(runtime, mission_id, template_id=template.template_id)
    evidence = runtime.store.create_evidence_package(
        EvidencePackage.create(
            evidence_package_id=str(uuid4()),
            mission_id=mission_id,
            producer_kind="node",
            producer_id=node_run.node_run_id,
            summary={"scorecard": scorecard},
            artifact_refs=result.artifact_refs,
            token_budget={"raw_trace_policy": "artifact_only"},
        )
    )
    status = "passed" if result.status == JobStatus.SUCCEEDED and scorecard["status"] == "passed" else "failed"
    node_status = NodeRunStatus.SUCCEEDED if status == "passed" else NodeRunStatus.FAILED
    runtime.store.complete_node_run(
        node_run.node_run_id,
        node_status,
        output_payload=result.output_payload,
        artifact_refs=result.artifact_refs,
        evidence_package_id=evidence.evidence_package_id,
        edge_decision="succeeded" if result.status == JobStatus.SUCCEEDED else "failed",
        error=None if result.error is None else result.error.to_json_dict(),
    )
    graph_status = GraphRunStatus.SUCCEEDED if status == "passed" else GraphRunStatus.FAILED
    runtime.store.update_graph_run_status(
        graph_run.graph_run_id,
        graph_status,
        current_node_id=node.node_id,
        error=None if status == "passed" else {"scorecard_status": scorecard["status"]},
    )
    return {
        "status": status,
        "template_id": template.template_id,
        "mission_id": mission_id,
        "graph_run": runtime.store.get_graph_run(graph_run.graph_run_id).to_json_dict(),
        "executed_node": node.to_json_dict(),
        "executed_job": {
            "job_id": result.job_id,
            "status": result.status.value,
            "artifact_refs": result.artifact_refs,
            "output_payload": result.output_payload,
        },
        "node_runs": [node_run.to_json_dict() for node_run in runtime.store.list_node_runs(graph_run.graph_run_id)],
        "evidence_package": evidence.to_json_dict(),
        "scorecard": scorecard,
    }


def _node_for_capability(template: GraphTemplate, capability: str) -> GraphNode | None:
    for node in template.nodes:
        if node.kind == "worker" and node.capability == capability:
            return node
    return None
