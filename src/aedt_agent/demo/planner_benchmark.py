from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.demo.service import DemoService


DEFAULT_TASKS = [
    {"task_id": "microstrip", "request": "create a microstrip s-parameter simulation at 5GHz"},
    {"task_id": "wave_port", "request": "create a wave port setup"},
    {"task_id": "radiation_airbox", "request": "create a radiation airbox for an antenna"},
    {"task_id": "setup_only", "request": "create an hfss setup at 2.4GHz"},
    {"task_id": "ambiguous", "request": "make an electromagnetic simulation"},
]


def run_planner_benchmark(
    repo_root: Path,
    *,
    output_html: Path = Path("benchmarks/reports/stage_c2_planner_benchmark.html"),
    output_json: Path = Path("benchmarks/reports/stage_c2_planner_benchmark.json"),
    planner_mode: str = "deterministic",
) -> dict[str, Any]:
    service = DemoService(repo_root)
    results = []
    for task in DEFAULT_TASKS:
        plan = service.plan({"user_request": task["request"], "planner_mode": planner_mode})
        valid = not plan.get("validation_errors") and bool(plan.get("generated_workflow"))
        results.append(
            {
                "task_id": task["task_id"],
                "request": task["request"],
                "valid_workflow": valid,
                "planner_mode": plan.get("planner_mode", ""),
                "selected_template": plan.get("selected_template"),
                "repair_count": int(plan.get("repair_count", 0)),
                "missing_information": plan.get("missing_information", []),
                "validation_errors": plan.get("validation_errors", []),
            }
        )
    report = {
        "title": "Stage C.2 Planner Benchmark",
        "summary": {
            "task_count": len(results),
            "valid_workflow_count": len([item for item in results if item["valid_workflow"]]),
            "success_rate": len([item for item in results if item["valid_workflow"]]) / len(results),
            "average_repair_count": sum(item["repair_count"] for item in results) / len(results),
        },
        "results": results,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_html.write_text(render_planner_benchmark_html(report), encoding="utf-8")
    return report


def render_planner_benchmark_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    rows = "\n".join(_result_row(item) for item in report["results"])
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Stage C.2 Planner Benchmark</title>"
        "<style>body{font-family:Arial,'Noto Sans SC',sans-serif;margin:0;background:#f8fafc;color:#111827}"
        "main{max-width:1120px;margin:0 auto;padding:32px 20px}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}"
        ".metric{background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:14px}.metric strong{display:block;font-size:26px}"
        "table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d1d5db}th,td{border-bottom:1px solid #e5e7eb;padding:10px;text-align:left;vertical-align:top}"
        "th{background:#eef2ff}.ok{color:#047857;font-weight:700}.fail{color:#b91c1c;font-weight:700}code{background:#eef2ff;padding:2px 5px;border-radius:4px}</style></head>"
        "<body><main><h1>Stage C.2 Planner Benchmark</h1>"
        "<p>该报告验证主模型/确定性 planner 是否能在受控边界内生成 workflow JSON，并通过 backend validator。</p>"
        "<div class=\"metrics\">"
        f"<div class=\"metric\"><strong>{summary['valid_workflow_count']}/{summary['task_count']}</strong>valid workflows</div>"
        f"<div class=\"metric\"><strong>{summary['success_rate']:.0%}</strong>success rate</div>"
        f"<div class=\"metric\"><strong>{summary['average_repair_count']:.2f}</strong>avg repair attempts</div>"
        "</div><table><thead><tr><th>Task</th><th>Request</th><th>Status</th><th>Template / Missing</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></main></body></html>\n"
    )


def _result_row(item: dict[str, Any]) -> str:
    status = "valid" if item["valid_workflow"] else "needs input"
    klass = "ok" if item["valid_workflow"] else "fail"
    details = item.get("selected_template") or ", ".join(item.get("missing_information", [])) or "none"
    return (
        "<tr>"
        f"<td><code>{_escape(item['task_id'])}</code><br>{_escape(item['planner_mode'])}</td>"
        f"<td>{_escape(item['request'])}</td>"
        f"<td class=\"{klass}\">{status}<br>repair: {item['repair_count']}</td>"
        f"<td>{_escape(details)}</td>"
        "</tr>"
    )


def _escape(value: Any) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
