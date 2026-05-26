from __future__ import annotations

from typing import Any

from aedt_agent.workflow.executor import WorkflowRunResult, WorkflowStepRun


WORKFLOW_ID = "import_brd_cutout_sparam_tdr_v1"

_STEP_TO_NODE = {
    "import_layout_file": "import_layout_file",
    "select_layout_nets": "select_layout_nets",
    "create_layout_cutout": "create_layout_cutout",
    "configure_layout_stackup": "configure_layout_stackup",
    "locate_layout_port_candidates": "locate_layout_port_candidates",
    "create_layout_ports": "create_layout_ports",
    "create_layout_setup": "create_layout_setup",
}


def import_cutout_summary_to_workflow_run(summary: dict[str, Any]) -> WorkflowRunResult:
    steps = [_summary_step_to_workflow_step(step) for step in summary.get("steps", []) if isinstance(step, dict)]
    outputs = {
        "layout_file": summary.get("layout_file", ""),
        "edb_path": summary.get("edb_path", ""),
        "aedt_project": summary.get("aedt_project", ""),
        "touchstone": summary.get("touchstone", ""),
        "tdr": summary.get("tdr", ""),
        "signal_nets": list(summary.get("signal_nets") or []),
        "reference_nets": list(summary.get("reference_nets") or []),
        "solve_skipped": summary.get("layout_solve", {}).get("status") == "skipped"
        if isinstance(summary.get("layout_solve"), dict)
        else True,
    }
    return WorkflowRunResult(
        workflow_id=WORKFLOW_ID,
        status=str(summary.get("status") or "failed"),
        validation={
            "passed": True,
            "errors": [],
            "warnings": [{"message": "BRD workflow run is converted from model-build summary."}],
        },
        model_validation={},
        model_facts={},
        steps=steps,
        outputs=outputs,
        repair_context={},
    )


def _summary_step_to_workflow_step(step: dict[str, Any]) -> WorkflowStepRun:
    step_id = str(step.get("id") or step.get("step_id") or step.get("node_id") or "unknown_step")
    status = str(step.get("status") or "failed")
    return WorkflowStepRun(
        step_id=step_id,
        node_id=_STEP_TO_NODE.get(step_id, step_id),
        inputs={},
        status=status,
        output={key: value for key, value in step.items() if key not in {"id", "step_id", "node_id", "label", "status"}},
        snapshot_summary={"label": step.get("label", step_id)},
        error_type=str(step.get("error_type") or ""),
        error_message=str(step.get("error") or step.get("error_message") or ""),
        elapsed_seconds=float(step.get("elapsed_seconds") or 0.0),
    )
