from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.v0.benchmark.report_html_stage_b import write_html_report_stage_b


DROP_ARTIFACT_KEYS = {
    "code_path",
    "prompt_path",
    "exec_log_path",
    "validation_log_path",
    "harness_stdout_path",
    "harness_stderr_path",
    "transcript_path",
    "tool_usage_path",
}


def build_stage_b_presentation_report(
    group_b_report: dict[str, Any],
    group_c_report: dict[str, Any],
    *,
    repo_root: Path | None = None,
    group_b_source: str = "",
    group_c_source: str = "",
) -> dict[str, Any]:
    """Build a presentation-safe Stage B B/C comparison report."""
    report = {
        "version": "stage_b_node_v1_presentation_10task",
        "max_attempts": max(group_b_report.get("max_attempts", 3), group_c_report.get("max_attempts", 3)),
        "method": (
            "B 组使用 GitNexus + 官方源码/示例检索后的自由 Python 代码；C 组使用同一 harness 生成 JSON node plan，"
            "并由本地受控节点在真实 AEDT 2026.1 non-graphical 中执行。两组均最多三次修复，"
            "判定来自真实 AEDT 执行和 validation script。"
        ),
        "run_sources": {
            "group_b": _source_label(group_b_source, repo_root=repo_root),
            "group_c": _source_label(group_c_source, repo_root=repo_root),
        },
        "groups": _scrub(
            {
                "B": group_b_report["groups"]["B"],
                "C": group_c_report["groups"]["C"],
            },
            repo_root=repo_root,
        ),
        "tasks": {},
        "free_code_execution_count": group_c_report.get("free_code_execution_count", 0),
    }
    task_ids = sorted(set(group_b_report.get("tasks", {})) | set(group_c_report.get("tasks", {})))
    for task_id in task_ids:
        task: dict[str, Any] = {}
        if task_id in group_b_report.get("tasks", {}):
            source_task = group_b_report["tasks"][task_id]
            task["metadata"] = source_task.get("metadata", {})
            if "B" in source_task:
                task["B"] = _scrub(source_task["B"], repo_root=repo_root)
        if task_id in group_c_report.get("tasks", {}):
            source_task = group_c_report["tasks"][task_id]
            task.setdefault("metadata", source_task.get("metadata", {}))
            if "C" in source_task:
                task["C"] = _scrub(source_task["C"], repo_root=repo_root)
        report["tasks"][task_id] = task
    return report


def build_stage_b_presentation_files(
    *,
    group_b_report_path: Path,
    group_c_report_path: Path,
    output_json: Path,
    output_html: Path,
    repo_root: Path | None = None,
    model_name: str = "",
) -> dict[str, Any]:
    group_b_report = _load_json(group_b_report_path)
    group_c_report = _load_json(group_c_report_path)
    report = build_stage_b_presentation_report(
        group_b_report,
        group_c_report,
        repo_root=repo_root,
        group_b_source=str(group_b_report_path),
        group_c_source=str(group_c_report_path),
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_html_report_stage_b(report, output_html, model_name=model_name)
    return report


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scrub(value: Any, *, repo_root: Path | None = None) -> Any:
    if isinstance(value, dict):
        return {key: _scrub(item, repo_root=repo_root) for key, item in value.items() if key not in DROP_ARTIFACT_KEYS}
    if isinstance(value, list):
        return [_scrub(item, repo_root=repo_root) for item in value]
    if isinstance(value, str):
        return _scrub_text(value, repo_root=repo_root)
    return value


def _scrub_text(text: str, *, repo_root: Path | None = None) -> str:
    scrubbed = text
    if repo_root is not None:
        root = str(repo_root)
        scrubbed = scrubbed.replace(root + "/", "<repo>/").replace(root, "<repo>")
    scrubbed = scrubbed.replace("/home/zzmjay/Ansoft", "<aedt-user-project>")
    return scrubbed


def _source_label(path_text: str, *, repo_root: Path | None = None) -> str:
    if not path_text:
        return ""
    path = Path(path_text)
    if repo_root is not None:
        try:
            return str(path.relative_to(repo_root))
        except ValueError:
            pass
    return _scrub_text(path_text, repo_root=repo_root)
