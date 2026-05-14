from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from aedt_agent.benchmark.aedt_executor import AttemptExecutor
from aedt_agent.benchmark.generator import CodeGenerator
from aedt_agent.benchmark.harness_generator import HarnessGeneration, HarnessGenerationError
from aedt_agent.benchmark.models import BenchmarkTask, load_tasks
from aedt_agent.benchmark.task_sets import STAGE_A_V2_TASK_IDS
from aedt_agent.benchmark.v2_models import AttemptResult, GroupRunResult, compute_group_metrics


GROUP_DIRS = {"A": "group_a", "B": "group_b"}


def run_aedt_benchmark_v2(
    tasks_dir: Path,
    run_dir: Path,
    generator: CodeGenerator,
    executor: AttemptExecutor,
    groups: list[str] | None = None,
    task_ids: list[str] | None = None,
    max_attempts: int = 3,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    selected_groups = groups or ["A", "B"]
    selected_task_ids = task_ids or STAGE_A_V2_TASK_IDS
    tasks_by_id = {task.task_id: task for task in load_tasks(tasks_dir)}
    tasks = [tasks_by_id[task_id] for task_id in selected_task_ids]
    report: dict = {
        "version": "stage_a_harness_v1",
        "max_attempts": max_attempts,
        "method": "Group A has no tools; Group B uses harness-configured GitNexus/PyAEDT tools.",
        "tasks": {},
        "groups": {},
    }
    total = len(tasks) * len(selected_groups)
    current = 0

    for task in tasks:
        task_report = {"metadata": {"level": task.level, "validation_script": task.validation_script}}
        for group in selected_groups:
            current += 1
            if progress_callback:
                progress_callback({"current": current, "total": total, "task_id": task.task_id, "group": group})
            group_result = _run_group_attempts(
                task=task,
                group=group,
                task_run_dir=run_dir / task.task_id / group,
                generator=generator,
                executor=executor,
                max_attempts=max_attempts,
                progress_callback=progress_callback,
            )
            task_report[group] = group_result.to_dict()
        report["tasks"][task.task_id] = task_report

    for group in selected_groups:
        report["groups"][group] = compute_group_metrics(
            [task_data[group] for task_data in report["tasks"].values() if group in task_data]
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _run_group_attempts(
    task: BenchmarkTask,
    group: str,
    task_run_dir: Path,
    generator: CodeGenerator,
    executor: AttemptExecutor,
    max_attempts: int,
    progress_callback: Callable[[dict], None] | None = None,
) -> GroupRunResult:
    task_run_dir.mkdir(parents=True, exist_ok=True)
    result = GroupRunResult(group=group)
    previous_code = ""
    previous_log = ""

    for attempt in range(1, max_attempts + 1):
        if progress_callback:
            progress_callback(
                {
                    "phase": "attempt_start",
                    "task_id": task.task_id,
                    "group": group,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                }
            )
        prompt = _build_attempt_prompt(task, group, attempt, previous_code, previous_log)
        prompt_path = task_run_dir / f"attempt_{attempt}_prompt.txt"
        code_path = task_run_dir / f"attempt_{attempt}_code.py"
        exec_log_path = task_run_dir / f"attempt_{attempt}_exec.log"
        validation_log_path = task_run_dir / f"attempt_{attempt}_validation.log"
        prompt_path.write_text(prompt, encoding="utf-8")

        start = time.monotonic()
        generation_ok = True
        failure_type = ""
        error_summary = ""
        harness_generation: HarnessGeneration | None = None
        try:
            if hasattr(generator, "generate_attempt"):
                harness_generation = generator.generate_attempt(
                    prompt,
                    task_id=task.task_id,
                    group=group,
                    attempt=attempt,
                    artifact_dir=task_run_dir,
                    filename=f"{task.task_id}_{group}_attempt_{attempt}.py",
                    previous_code=previous_code,
                    previous_log=previous_log,
                )
                code = harness_generation.code
            else:
                code = generator.generate(prompt, filename=f"{task.task_id}_{group}_attempt_{attempt}.py")
            code_path.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")
        except Exception as exc:
            generation_ok = False
            code = ""
            failure_type = "generation_error"
            error_summary = str(exc)
            if isinstance(exc, HarnessGenerationError):
                harness_generation = exc.generation
            exec_result = {"execution_ok": False, "validation_ok": False, "failure_type": failure_type, "log": error_summary}
        else:
            exec_result = executor.execute(code_path, Path(task.validation_script), task_run_dir)

        elapsed = time.monotonic() - start
        log_text = str(exec_result.get("log", ""))
        exec_log_path.write_text(log_text, encoding="utf-8")
        validation_log_path.write_text(json.dumps(exec_result.get("validation_result", {}), indent=2), encoding="utf-8")
        attempt_result = AttemptResult(
            attempt=attempt,
            code_path=str(code_path),
            prompt_path=str(prompt_path),
            exec_log_path=str(exec_log_path),
            validation_log_path=str(validation_log_path),
            generation_ok=generation_ok,
            execution_ok=bool(exec_result.get("execution_ok")),
            validation_ok=bool(exec_result.get("validation_ok")),
            failure_type=str(exec_result.get("failure_type") or failure_type),
            error_summary=_summarize_log(log_text or error_summary),
            elapsed_seconds=elapsed,
            harness_stdout_path=harness_generation.stdout_path if harness_generation else "",
            harness_stderr_path=harness_generation.stderr_path if harness_generation else "",
            transcript_path=harness_generation.transcript_path if harness_generation else "",
            tool_usage_path=harness_generation.tool_usage_path if harness_generation else "",
            tool_usage=harness_generation.tool_usage if harness_generation else {},
        )
        result.attempts.append(attempt_result)
        if progress_callback:
            progress_callback(
                {
                    "phase": "attempt_end",
                    "task_id": task.task_id,
                    "group": group,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "final_pass": attempt_result.final_pass,
                    "execution_ok": attempt_result.execution_ok,
                    "validation_ok": attempt_result.validation_ok,
                    "failure_type": attempt_result.failure_type,
                }
            )
        if attempt_result.final_pass:
            break
        previous_code = code
        previous_log = log_text or error_summary
    (task_run_dir / "summary.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


def _build_attempt_prompt(
    task: BenchmarkTask,
    group: str,
    attempt: int,
    previous_code: str,
    previous_log: str,
) -> str:
    parts = [
        f"Group {group} PyAEDT benchmark task.",
        "Generate executable Python code only. Do not include markdown fences, prose, explanations, bullet lists, or conclusions.",
        "Use the existing `app` object provided by the benchmark harness.",
        "The AEDT design starts empty except for `app`; create any prerequisite geometry, materials, setups, boundaries, and ports needed by the requirement.",
        "Prefer simple deterministic geometry in millimeters so the script can run in a fresh non-graphical HFSS design.",
        "Do not import pyaedt, ansys.aedt.core, Hfss, or Desktop.",
        "Do not create or release a Desktop session; the harness owns the AEDT session.",
        f"Requirement:\n{task.requirement}",
    ]
    if group == "A":
        parts.append("You do not have access to external tools or official documentation. Use only the task description and prior error log.")
    if group == "B":
        parts.append(
            "Before writing code, use the available GitNexus/PyAEDT tools to inspect the official PyAEDT API and examples. "
            "Prefer GitNexus query/context results and official examples over memory. "
            "Only inspect the official PyAEDT source tree and PyAEDT examples directories exposed by the harness. "
            "Do not read generated benchmark candidates or prior benchmark outputs as references. "
            "Keep retrieval focused: use at most 2 GitNexus query calls and at most 2 GitNexus context calls, then write runnable code immediately. "
            "Use PyAEDT signatures exactly as verified from official sources: create_box(origin, sizes, ...), "
            "create_rectangle(orientation, origin, sizes, ...), and iterate object.faces as FacePrimitive objects "
            "with face.id and face.center rather than indexing faces as a dict. "
            "Always set app.solution_type = \"Modal\" before creating HFSS ports. "
            "Default to a minimal executable model: do not create analysis setups, sweeps, solves, or exports unless the requirement explicitly asks for them. "
            "For dipoles, patch/probe feeds, microstrip-like traces, missing-ground traps, and other non-waveguide feeds, prefer a dedicated sheet plus "
            "app.lumped_port(assignment=sheet.name, create_port_sheet=False, integration_line=[point0, point1], impedance=50, name=\"Port1\"). "
            "Use wave_port only for explicit waveguide/wave-port requirements. For wave ports, prefer an explicit port sheet or explicit face id, "
            "an explicit integration_line, and avoid applying PEC to the port face. "
            "Do not pass FacePrimitive IDs with create_pec_cap=True, and do not use object-name wave_port calls for planar microstrip/patch ports. "
            "If this is a repair attempt, first fix the traceback line with the smallest code change; do not rewrite unrelated geometry or add explanatory text."
        )
    if attempt > 1:
        parts.append("Previous attempt failed. Fix the code using the AEDT/PyAEDT error log.")
        parts.append("Previous code:\n" + previous_code)
        parts.append("Error log:\n" + previous_log)
    return "\n\n".join(parts)


def _summarize_log(log: str, limit: int = 500) -> str:
    clean = " ".join(log.split())
    return clean[:limit]
