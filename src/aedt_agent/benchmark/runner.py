from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from aedt_agent.benchmark.context_builder import build_context
from aedt_agent.benchmark.generator import CodeGenerator
from aedt_agent.benchmark.go_nogo import compute_go_nogo
from aedt_agent.benchmark.graders import check_allowed_api_usage, check_restricted_python, check_syntax
from aedt_agent.benchmark.models import BenchmarkTask, load_tasks
from aedt_agent.benchmark.prompt_templates import build_prompt
from aedt_agent.benchmark.node_readiness import compute_node_readiness
from aedt_agent.benchmark.semantic_lite import check_semantic_lite
from aedt_agent.knowledge.sqlite_provider import SQLiteKnowledgeProvider
from aedt_agent.nodes.registry import NodeRegistry


GROUP_DIRS = {"A": "group_a", "B": "group_b", "C": "group_c"}


def run_offline_benchmark(
    tasks_dir: Path,
    generated_dir: Path,
    node_catalog_dir: Path,
    report_path: Path,
    generator: CodeGenerator | None = None,
    db_path: Path | None = None,
    groups: list[str] | None = None,
    model_name: str | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    reuse_existing_candidates: bool = True,
) -> dict:
    tasks = load_tasks(tasks_dir)
    registry = NodeRegistry.from_directory(node_catalog_dir)
    selected_groups = groups or list(GROUP_DIRS.keys())
    total_runs = len(tasks) * len(selected_groups)
    provider = (
        SQLiteKnowledgeProvider(db_path)
        if generator is not None and db_path is not None
        else None
    )
    report: dict[str, dict] = {"tasks": {}}
    current_run = 0

    for task in tasks:
        whitelist = registry.api_whitelist(task.allowed_nodes)
        task_result = {"metadata": {"allowed_nodes": task.allowed_nodes, "level": task.level}}
        for group in selected_groups:
            current_run += 1
            if progress_callback is not None:
                progress_callback(
                    {
                        "task_id": task.task_id,
                        "group": group,
                        "current": current_run,
                        "total": total_runs,
                    }
                )
            dirname = GROUP_DIRS[group]
            group_dir = generated_dir / dirname
            if generator is not None and provider is not None:
                code = _generate_candidate_code(
                    task,
                    group,
                    group_dir,
                    generator,
                    provider,
                    registry,
                    reuse_existing_candidates=reuse_existing_candidates,
                )
                generation_mode = "online"
            else:
                code = _load_candidate_code(task, group_dir)
                generation_mode = "replay"
            syntax = check_syntax(code)
            restricted = check_restricted_python(code) if syntax.passed else None
            api = check_allowed_api_usage(code, whitelist) if syntax.passed else None
            semantic = check_semantic_lite(code, task, api_semantics=[], traps=[]) if syntax.passed else None
            passed = (
                syntax.passed
                and (restricted.passed if restricted else False)
                and (api.passed if api else False)
                and (semantic.passed if semantic else False)
            )
            task_result[group] = {
                "syntax_pass": syntax.passed,
                "security_pass": restricted.passed if restricted else False,
                "api_pass": api.passed if api else False,
                "semantic_lite_pass": semantic.passed if semantic else False,
                "passed": passed,
                "generation_mode": generation_mode,
                "model": model_name or "",
            }
        report["tasks"][task.task_id] = task_result

    report["go_nogo"] = compute_go_nogo(report)
    report["node_readiness"] = compute_node_readiness(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _load_candidate_code(task: BenchmarkTask, group_dir: Path) -> str:
    candidate = group_dir / f"{task.task_id}.py"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    reference = Path(task.reference_script)
    if reference.exists():
        return reference.read_text(encoding="utf-8")
    return ""


def _generate_candidate_code(
    task: BenchmarkTask,
    group: str,
    group_dir: Path,
    generator: CodeGenerator,
    provider: SQLiteKnowledgeProvider,
    registry: NodeRegistry,
    reuse_existing_candidates: bool = True,
) -> str:
    candidate_path = group_dir / f"{task.task_id}.py"
    if reuse_existing_candidates and candidate_path.exists():
        existing = candidate_path.read_text(encoding="utf-8")
        if existing.strip():
            return existing
    context = build_context(group=group, task=task, provider=provider, registry=registry)
    prompt = build_prompt(group=group, requirement=task.requirement, context=context)
    code = generator.generate(prompt, filename=f"{task.task_id}.py")
    group_dir.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")
    return code
