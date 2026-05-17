from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_LINKS = [
    {
        "title": "Stage C 阶段性报告",
        "kind": "overview",
        "path": "benchmarks/reports/aedt_agent_stage_c_progress_report.html",
        "description": "说明 agent 架构、Stage A/B/C 差异、当前能力和边界。",
    },
    {
        "title": "真实 AEDT Smoke Dashboard",
        "kind": "real_aedt",
        "path": "benchmarks/reports/stage_c_real_smoke_dashboard.html",
        "description": "汇总 3 个真实 AEDT 2026.1 non-graphical smoke 和模型事实 validation。",
    },
    {
        "title": "节点进化 Proposal 审核报告",
        "kind": "node_evolution",
        "path": "benchmarks/reports/stage_c_node_evolution_review.html",
        "description": "展示从 benchmark/audit 证据生成的候选节点 proposal 和审核 gate。",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a lightweight Chinese Stage C demo index page.")
    parser.add_argument("--output-html", default="benchmarks/reports/stage_c_demo_index.html")
    parser.add_argument("--output-json", default="benchmarks/reports/stage_c_demo_index.json")
    args = parser.parse_args()

    index = build_demo_index()
    output_html = Path(args.output_html)
    output_json = Path(args.output_json)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_html.write_text(render_index_html(index), encoding="utf-8")
    print(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))


def build_demo_index() -> dict[str, Any]:
    smoke = _read_optional_json(Path("benchmarks/reports/stage_c_real_smoke_dashboard.json"))
    evolution = _read_optional_json(Path("benchmarks/reports/stage_c_node_evolution_review.json"))
    smoke_summary = smoke.get("summary", {}) if isinstance(smoke.get("summary"), dict) else {}
    evolution_summary = evolution.get("summary", {}) if isinstance(evolution.get("summary"), dict) else {}
    return {
        "title": "AEDT Agent Stage C Demo Index",
        "summary": {
            "real_smoke": {
                "run_count": smoke_summary.get("run_count", 0),
                "succeeded_count": smoke_summary.get("succeeded_count", 0),
                "success_rate": smoke_summary.get("success_rate", 0.0),
                "coverage": smoke_summary.get("coverage", []),
            },
            "node_evolution": {
                "evidence_count": evolution_summary.get("evidence_count", 0),
                "proposal_count": evolution_summary.get("proposal_count", 0),
                "blocked_count": evolution_summary.get("blocked_count", 0),
                "candidate_count": evolution_summary.get("candidate_count", 0),
            },
        },
        "links": DEFAULT_LINKS,
        "artifact_links": [
            {"title": "真实 smoke JSON", "path": "benchmarks/reports/stage_c_real_smoke_dashboard.json"},
            {"title": "节点进化审核 JSON", "path": "benchmarks/reports/stage_c_node_evolution_review.json"},
            {"title": "聊天规划样例", "path": "benchmarks/reports/stage_c_chat_plan_sample.json"},
        ],
    }


def render_index_html(index: dict[str, Any]) -> str:
    summary = index["summary"]
    smoke = summary["real_smoke"]
    evolution = summary["node_evolution"]
    cards = "\n".join(_link_card(link) for link in index["links"])
    artifact_rows = "\n".join(_artifact_row(link) for link in index["artifact_links"])
    coverage = "、".join(str(item) for item in smoke.get("coverage", []))
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>AEDT Agent Stage C Demo Index</title>"
        "<style>body{font-family:Arial,'Noto Sans SC',sans-serif;margin:0;background:#f8fafc;color:#111827}"
        "main{max-width:1120px;margin:0 auto;padding:36px 24px}h1{font-size:32px;margin:0 0 8px}.lead{font-size:18px;line-height:1.7}"
        ".metrics,.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:22px 0}.metric,.card{background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:16px}"
        ".metric strong{display:block;font-size:28px}.card a{font-size:20px;font-weight:700;color:#1d4ed8;text-decoration:none}.card p{line-height:1.6}"
        "table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d1d5db}th,td{border-bottom:1px solid #e5e7eb;padding:10px;text-align:left}"
        "th{background:#eef2ff}code{background:#eef2ff;padding:2px 5px;border-radius:4px}@media(max-width:760px){.metrics,.cards{grid-template-columns:1fr}main{padding:24px 14px}}</style>"
        "</head><body><main><h1>AEDT Agent Stage C Demo Index</h1>"
        "<p class=\"lead\">这里是 Stage C 当前可展示产物的统一入口。重点展示节点化 agent 架构、真实 AEDT smoke 结果，以及节点进化 proposal 的受控审核机制。</p>"
        "<div class=\"metrics\">"
        f"<div class=\"metric\"><strong>{smoke.get('succeeded_count', 0)}/{smoke.get('run_count', 0)}</strong>真实 AEDT smoke 通过</div>"
        f"<div class=\"metric\"><strong>{_escape(coverage)}</strong>节点能力覆盖</div>"
        f"<div class=\"metric\"><strong>{evolution.get('proposal_count', 0)}</strong>节点进化 proposal</div>"
        "</div>"
        f"<div class=\"cards\">{cards}</div>"
        "<h2>JSON Artifacts</h2><table><thead><tr><th>类型</th><th>路径</th></tr></thead>"
        f"<tbody>{artifact_rows}</tbody></table>"
        "</main></body></html>\n"
    )


def _link_card(link: dict[str, str]) -> str:
    return (
        "<div class=\"card\">"
        f"<a href=\"{_relative_report_href(link['path'])}\">{_escape(link['title'])}</a>"
        f"<p>{_escape(link['description'])}</p>"
        f"<code>{_escape(link['path'])}</code>"
        "</div>"
    )


def _artifact_row(link: dict[str, str]) -> str:
    return f"<tr><td>{_escape(link['title'])}</td><td><code>{_escape(link['path'])}</code></td></tr>"


def _relative_report_href(path: str) -> str:
    prefix = "benchmarks/reports/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _escape(value: Any) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


if __name__ == "__main__":
    main()
