from __future__ import annotations

from typing import Any

from aedt_agent.agent.actions import execute_approved_action
from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext


BRD_RECORDED_VOID_ACTION_CAPABILITY = "brd.action.execute_recorded"


def build_brd_recorded_void_action_job_input(*, action_id: str) -> dict[str, Any]:
    return {"action_id": action_id}


def run_brd_recorded_void_action_worker(
    job: JobRecord,
    context: WorkerContext,
    *,
    store,
) -> dict[str, Any]:
    action_id = str(job.input_payload.get("action_id") or "")
    if not action_id:
        raise ValueError("action_id is required")
    result = execute_approved_action(store, action_id)
    action = store.get_action(action_id)
    executions = store.list_action_executions(action_id)
    execution = executions[-1]
    artifact_refs = list(dict.fromkeys(execution.before_artifact_refs + execution.after_artifact_refs))
    comparison = dict(result["comparison"])
    return {
        "status": action.status.value,
        "action_id": action.action_id,
        "action_digest": action.digest,
        "decision": result["decision"],
        "comparison": comparison,
        "execution_id": execution.execution_id,
        "worker_id": context.worker_id,
        "evidence_summary": {
            "action_type": action.action_type,
            "action_status": action.status.value,
            "decision": result["decision"],
            "comparison_status": comparison["status"],
            "rl_worst_delta_db": comparison["rl_worst_delta_db"],
            "tdr_peak_deviation_delta_ohm": comparison["tdr_peak_deviation_delta_ohm"],
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
        },
        "artifact_refs": artifact_refs,
    }
