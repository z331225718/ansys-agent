from __future__ import annotations

from typing import Any

from aedt_agent.agent.actions.contracts import ActionRecord
from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext


BRD_ACTION_PROPOSE_CAPABILITY = "brd.action.propose"


CANDIDATE_ACTIONS = [
    {
        "action_type": "void.adjust_layer",
        "label": "调整反焊盘尺寸",
        "description": "增大或减小特定层的反焊盘(void)直径",
        "parameters": {
            "layer": {"type": "string", "description": "目标层名"},
            "delta_mil": {"type": "number", "description": "直径变化量(mil)"},
        },
    },
    {
        "action_type": "void.adjust_clearance",
        "label": "调整铜皮间距",
        "description": "增大或减小过孔到铜皮的间距",
        "parameters": {
            "delta_mil": {"type": "number", "description": "间距变化量(mil)"},
        },
    },
    {
        "action_type": "trace.widen",
        "label": "加宽走线",
        "description": "在指定区间加宽差分走线",
        "parameters": {
            "net": {"type": "string", "description": "目标网络名"},
            "width_delta_mil": {"type": "number", "description": "宽度增量(mil)"},
        },
    },
]


def build_action_propose_job_input(
    *,
    current_score: dict[str, Any] | None = None,
    target_metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "current_score": current_score or {},
        "target_metrics": target_metrics or [],
    }


def run_action_propose_worker(job: JobRecord, context: WorkerContext) -> dict[str, Any]:
    current = job.input_payload.get("current_score") or {}
    targets = job.input_payload.get("target_metrics") or []

    # Generate candidate actions based on current score vs targets
    candidates = []
    rl_db = float(current.get("rl_worst_db", -100))
    tdr_dev = float(current.get("tdr_peak_deviation_ohm", 0))

    if rl_db > -20:  # RL too high → try void adjustment
        candidates.append(dict(CANDIDATE_ACTIONS[0]))
    if tdr_dev > 5:  # TDR deviation too high → try clearance
        candidates.append(dict(CANDIDATE_ACTIONS[1]))
    if not candidates:
        candidates.append(dict(CANDIDATE_ACTIONS[2]))  # fallback: widen trace

    return {
        "status": "proposed",
        "candidates": candidates,
        "current_score": current,
        "target_metrics": targets,
        "evidence_summary": {
            "candidate_count": len(candidates),
            "current_rl_db": rl_db,
            "current_tdr_dev_ohm": tdr_dev,
        },
    }
