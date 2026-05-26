from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from aedt_agent.layout.workflow_run import WORKFLOW_ID
from aedt_agent.workflow.executor import WorkflowRunResult, WorkflowStepRun


class BrdWorkflowProgressWriter:
    def __init__(
        self,
        artifact_path: Path,
        *,
        layout_file: str = "",
        signal_nets: list[str] | None = None,
        reference_nets: list[str] | None = None,
    ) -> None:
        self.artifact_path = artifact_path
        self.outputs: dict[str, Any] = {
            "layout_file": layout_file,
            "signal_nets": list(signal_nets or []),
            "reference_nets": list(reference_nets or []),
            "solve_skipped": True,
        }
        self.steps: list[WorkflowStepRun] = []
        self._started: dict[str, float] = {}

    def step_running(self, step_id: str, label: str, output: dict[str, Any] | None = None) -> None:
        self._started.setdefault(step_id, time.time())
        self._upsert_step(step_id, label, "running", output or {})
        self._write("running")

    def step_succeeded(self, step_id: str, label: str, output: dict[str, Any] | None = None) -> None:
        self._upsert_step(step_id, label, "succeeded", output or {})
        self.outputs.update(output or {})
        self._write("running")

    def step_failed(self, step_id: str, label: str, error_type: str, error_message: str) -> None:
        self._upsert_step(step_id, label, "failed", {}, error_type=error_type, error_message=error_message)
        self._write("failed", repair_context={"failed_step_id": step_id, "error_message": error_message})

    def finish_succeeded(self, outputs: dict[str, Any] | None = None) -> None:
        self.outputs.update(outputs or {})
        self._write("succeeded")

    def finish_failed(self, error_type: str, error_message: str) -> None:
        self._write("failed", repair_context={"error_type": error_type, "error_message": error_message})

    def _upsert_step(
        self,
        step_id: str,
        label: str,
        status: str,
        output: dict[str, Any],
        *,
        error_type: str = "",
        error_message: str = "",
    ) -> None:
        elapsed = round(time.time() - self._started.get(step_id, time.time()), 3)
        step = WorkflowStepRun(
            step_id=step_id,
            node_id=step_id,
            inputs={},
            status=status,
            output=output,
            snapshot_summary={"label": label},
            error_type=error_type,
            error_message=error_message,
            elapsed_seconds=elapsed,
        )
        for index, existing in enumerate(self.steps):
            if existing.step_id == step_id:
                self.steps[index] = step
                return
        self.steps.append(step)

    def _write(self, status: str, *, repair_context: dict[str, Any] | None = None) -> None:
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        WorkflowRunResult(
            workflow_id=WORKFLOW_ID,
            status=status,
            validation={"passed": True, "errors": [], "warnings": []},
            model_validation={},
            model_facts={},
            steps=list(self.steps),
            outputs=dict(self.outputs),
            repair_context=repair_context or {},
        ).write_json(self.artifact_path)
