from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from aedt_agent.benchmark.node_plan_parser import NodePlanParseError, extract_node_plan
from aedt_agent.benchmark.runner_v2 import run_aedt_benchmark_v2
from aedt_agent.mcp.tools import McpToolKernel
from aedt_agent.mcp.types import ExecutionStatus


def run_stage_b_node_benchmark(
    tasks_dir: Path,
    run_dir: Path,
    group_b_generator,
    group_c_generator,
    group_b_executor,
    kernel: McpToolKernel,
    groups: list[str] | None = None,
    task_ids: list[str] | None = None,
    max_attempts: int = 3,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    selected_groups = groups or ["B", "C"]
    report = {
        "version": "stage_b_node_v1",
        "max_attempts": max_attempts,
        "tasks": {},
        "groups": {},
        "free_code_execution_count": 0,
    }
    if "B" in selected_groups:
        baseline = run_aedt_benchmark_v2(
            tasks_dir=tasks_dir,
            run_dir=run_dir / "baseline_b",
            generator=group_b_generator,
            executor=group_b_executor,
            groups=["B"],
            task_ids=task_ids,
            max_attempts=max_attempts,
            progress_callback=progress_callback,
        )
        report["groups"]["B"] = baseline["groups"]["B"]
        for task_id, task_data in baseline["tasks"].items():
            report["tasks"].setdefault(task_id, {}).update({"metadata": task_data.get("metadata", {}), "B": task_data["B"]})
    if "C" in selected_groups:
        c_report = _run_group_c(tasks_dir, run_dir / "node_c", group_c_generator, kernel, task_ids, max_attempts, progress_callback)
        report["groups"]["C"] = c_report["group_metrics"]
        for task_id, task_data in c_report["tasks"].items():
            report["tasks"].setdefault(task_id, {}).update(task_data)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "stage_b_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _run_group_c(tasks_dir, run_dir, generator, kernel, task_ids, max_attempts, progress_callback):
    from aedt_agent.benchmark.models import load_tasks
    from aedt_agent.benchmark.task_sets import STAGE_A_V2_TASK_IDS
    from aedt_agent.benchmark.stage_b_models import compute_stage_b_metrics

    selected_task_ids = task_ids or STAGE_A_V2_TASK_IDS
    tasks_by_id = {task.task_id: task for task in load_tasks(tasks_dir)}
    run_dir.mkdir(parents=True, exist_ok=True)
    task_results = {}
    metric_inputs = []
    for task_id in selected_task_ids:
        task = tasks_by_id[task_id]
        task_dir = run_dir / task_id / "C"
        task_dir.mkdir(parents=True, exist_ok=True)
        session = kernel.create_session(project_id=f"stage-b-{task_id}", design_id="HFSSDesign1")
        attempts = []
        node_steps = []
        previous_log = ""
        final_pass = False
        success_on_attempt = None
        failure_type = ""
        try:
            for attempt in range(1, max_attempts + 1):
                if progress_callback:
                    progress_callback({"phase": "attempt_start", "task_id": task_id, "group": "C", "attempt": attempt, "max_attempts": max_attempts})
                started_at = time.monotonic()
                prompt = _build_group_c_prompt(task.requirement, previous_log)
                (task_dir / f"attempt_{attempt}_prompt.txt").write_text(prompt, encoding="utf-8")
                try:
                    if hasattr(generator, "generate_text_attempt"):
                        generation = generator.generate_text_attempt(
                            prompt,
                            task_id=task_id,
                            group="C",
                            attempt=attempt,
                            artifact_dir=task_dir,
                            filename=f"{task_id}_C_attempt_{attempt}.json",
                            previous_log=previous_log,
                        )
                        raw = generation.code
                    else:
                        raw = generator.generate(prompt, filename=f"{task_id}_C_attempt_{attempt}.json")
                    (task_dir / f"attempt_{attempt}_plan_raw.txt").write_text(raw, encoding="utf-8")
                    plan = extract_node_plan(raw)
                except (NodePlanParseError, Exception) as exc:
                    failure_type = "generation_error"
                    previous_log = str(exc)
                    attempt_record = _attempt_record(attempt, False, failure_type, previous_log, started_at)
                    attempts.append(attempt_record)
                    continue

                step_results = []
                all_steps_ok = True
                for step in plan.plan:
                    result = kernel.execute_node(step.node_id, step.inputs, session["session_id"])
                    step_record = {"node_id": step.node_id, "inputs": step.inputs, "status": result.status.value, "output": result.output, "error_type": result.error_type, "error_message": result.error_message}
                    node_steps.append(step_record)
                    step_results.append(step_record)
                    if result.status != ExecutionStatus.SUCCEEDED:
                        all_steps_ok = False
                        failure_type = result.error_type or "node_execution_error"
                        previous_log = result.error_message or result.traceback
                        break
                (task_dir / f"attempt_{attempt}_node_results.json").write_text(json.dumps(step_results, indent=2, ensure_ascii=False), encoding="utf-8")
                if all_steps_ok:
                    final_pass = True
                    success_on_attempt = attempt
                    failure_type = ""
                attempts.append(_attempt_record(attempt, all_steps_ok, failure_type, previous_log, started_at))
                if progress_callback:
                    progress_callback({"phase": "attempt_end", "task_id": task_id, "group": "C", "attempt": attempt, "max_attempts": max_attempts, "final_pass": all_steps_ok, "failure_type": failure_type})
                if final_pass:
                    break
        finally:
            kernel.release_session(session["session_id"])
        result_data = {"final_pass": final_pass, "success_on_attempt": success_on_attempt, "attempts": attempts, "node_steps": node_steps, "failure_type": failure_type}
        task_results[task_id] = {"metadata": {"level": task.level, "validation_script": task.validation_script}, "C": result_data}
        metric_inputs.append({"task_id": task_id, **result_data})
    return {"tasks": task_results, "group_metrics": compute_stage_b_metrics(metric_inputs)}


def _build_group_c_prompt(requirement: str, previous_log: str) -> str:
    parts = [
        "Generate a Stage B node plan as JSON only. Do not output Python or markdown.",
        "The JSON schema is: {\"plan\": [{\"node_id\": \"create_substrate\", \"inputs\": {}}]}",
        "Use only supported node IDs from the node catalog.",
        f"Requirement:\n{requirement}",
    ]
    if previous_log:
        parts.append("Previous node plan failed. Use this real error to repair the JSON plan:\n" + previous_log)
    return "\n\n".join(parts)


def _attempt_record(attempt: int, final_pass: bool, failure_type: str, log: str, started_at: float) -> dict:
    return {
        "attempt": attempt,
        "final_pass": final_pass,
        "failure_type": failure_type,
        "error_summary": " ".join(log.split())[:500],
        "elapsed_seconds": time.monotonic() - started_at,
    }
