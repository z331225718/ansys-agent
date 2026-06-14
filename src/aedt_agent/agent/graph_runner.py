from __future__ import annotations

from typing import Any

from aedt_agent.agent.graph_template import GraphNode, GraphTemplate
from aedt_agent.agent.mission import JobStatus
from aedt_agent.agent.scorecard import score_mission


def run_graph_once(runtime, mission_id: str, template: GraphTemplate, *, worker_id: str = "graph") -> dict[str, Any]:
    """Execute one queued job that is allowed by the graph template, then score it."""

    queued = [job for job in runtime.list_jobs(mission_id) if job.status == JobStatus.QUEUED]
    if not queued:
        raise ValueError(f"no queued job for mission: {mission_id}")
    job = queued[0]
    node = _node_for_capability(template, job.capability)
    if node is None:
        raise ValueError(f"queued job capability is not allowed by graph template: {job.capability}")

    result = runtime.execute_next_job(mission_id, worker_id)
    scorecard = score_mission(runtime, mission_id, template_id=template.template_id)
    status = "passed" if result.status == JobStatus.SUCCEEDED and scorecard["status"] == "passed" else "failed"
    return {
        "status": status,
        "template_id": template.template_id,
        "mission_id": mission_id,
        "executed_node": node.to_json_dict(),
        "executed_job": {
            "job_id": result.job_id,
            "status": result.status.value,
            "artifact_refs": result.artifact_refs,
            "output_payload": result.output_payload,
        },
        "scorecard": scorecard,
    }


def _node_for_capability(template: GraphTemplate, capability: str) -> GraphNode | None:
    for node in template.nodes:
        if node.kind == "worker" and node.capability == capability:
            return node
    return None
