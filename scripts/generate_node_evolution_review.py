from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_AVAILABLE_TESTS = {
    "test_node_catalog.py",
    "test_workflow_validator.py",
    "test_inspector_validation.py",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a Chinese node-evolution proposal review report.")
    parser.add_argument("--source", default="benchmarks/reports/stage_c_node_evolution_report.json")
    parser.add_argument("--output-html", default="benchmarks/reports/stage_c_node_evolution_review.html")
    parser.add_argument("--output-json", default="benchmarks/reports/stage_c_node_evolution_review.json")
    parser.add_argument("--available-test", action="append", default=[], help="Available test filename. Repeatable.")
    parser.add_argument("--workflow-validator-passed", action="store_true")
    parser.add_argument("--real-aedt-smoke-passed", action="store_true")
    parser.add_argument("--benchmark-regression-passed", action="store_true")
    args = parser.parse_args()

    report = _read_json(Path(args.source))
    available_tests = DEFAULT_AVAILABLE_TESTS | set(args.available_test)
    review = build_review_report(
        report,
        source=str(args.source),
        available_tests=available_tests,
        workflow_validator_passed=args.workflow_validator_passed,
        real_aedt_smoke_passed=args.real_aedt_smoke_passed,
        benchmark_regression_passed=args.benchmark_regression_passed,
    )

    output_json = Path(args.output_json)
    output_html = Path(args.output_html)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_html.write_text(render_review_html(review), encoding="utf-8")
    print(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True))


def build_review_report(
    report: dict[str, Any],
    *,
    source: str,
    available_tests: set[str],
    workflow_validator_passed: bool,
    real_aedt_smoke_passed: bool,
    benchmark_regression_passed: bool,
) -> dict[str, Any]:
    proposals = [_review_proposal(item, available_tests, workflow_validator_passed, real_aedt_smoke_passed, benchmark_regression_passed) for item in report.get("proposals", [])]
    evidence = report.get("evidence", [])
    return {
        "title": "Stage C 节点进化 Proposal 审核报告",
        "source": source,
        "summary": {
            "source_count": report.get("source_count", 0),
            "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
            "proposal_count": len(proposals),
            "candidate_count": len([item for item in proposals if item["gate_status"] == "candidate_ready"]),
            "blocked_count": len([item for item in proposals if item["gate_status"] != "candidate_ready"]),
            "top_actions": _counts(item.get("recommended_action", "") for item in proposals),
            "risk_levels": _counts(item.get("risk_level", "") for item in proposals),
        },
        "available_tests": sorted(available_tests),
        "gate_inputs": {
            "workflow_validator_passed": workflow_validator_passed,
            "real_aedt_smoke_passed": real_aedt_smoke_passed,
            "benchmark_regression_passed": benchmark_regression_passed,
        },
        "proposals": proposals,
    }


def _review_proposal(
    proposal: dict[str, Any],
    available_tests: set[str],
    workflow_validator_passed: bool,
    real_aedt_smoke_passed: bool,
    benchmark_regression_passed: bool,
) -> dict[str, Any]:
    required_tests = [str(item) for item in proposal.get("required_tests", [])]
    blockers: list[str] = []
    missing_tests = sorted(set(required_tests) - available_tests - {"real_aedt_smoke_or_manual_gate", "benchmark_regression"})
    if missing_tests:
        blockers.append("缺少必需测试：" + ", ".join(missing_tests))
    if not workflow_validator_passed:
        blockers.append("候选 workflow/schema 还没有通过 validator gate")
    if "real_aedt_smoke_or_manual_gate" in required_tests and not real_aedt_smoke_passed:
        blockers.append("需要真实 AEDT smoke 或人工 manual gate")
    if "benchmark_regression" in required_tests and not benchmark_regression_passed:
        blockers.append("需要 benchmark regression 证据")
    blockers.append("stable 发布必须人工审核")
    gate_status = "candidate_ready" if blockers == ["stable 发布必须人工审核"] else "needs_review"
    metadata = proposal.get("candidate_node_metadata", {}) if isinstance(proposal.get("candidate_node_metadata"), dict) else {}
    evidence = proposal.get("evidence", []) if isinstance(proposal.get("evidence"), list) else []
    return {
        "proposal_id": proposal.get("proposal_id", ""),
        "candidate_node_id": metadata.get("node_id", ""),
        "recommended_action": proposal.get("recommended_action", ""),
        "risk_level": proposal.get("risk_level", ""),
        "review_status": proposal.get("review_status", ""),
        "gate_status": gate_status,
        "blockers": blockers,
        "problem_pattern": proposal.get("problem_pattern", ""),
        "affected_tasks": proposal.get("affected_tasks", []),
        "required_tests": required_tests,
        "evidence": evidence,
        "evidence_summary": [item.get("summary", "") for item in evidence if isinstance(item, dict)],
        "notes": proposal.get("notes", []),
    }


def render_review_html(review: dict[str, Any]) -> str:
    summary = review["summary"]
    proposal_rows = "\n".join(_proposal_row(item) for item in review["proposals"])
    actions = "、".join(f"{key}: {value}" for key, value in summary["top_actions"].items())
    risks = "、".join(f"{key}: {value}" for key, value in summary["risk_levels"].items())
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Stage C 节点进化 Proposal 审核报告</title>"
        "<style>body{font-family:Arial,'Noto Sans SC',sans-serif;margin:0;background:#f8fafc;color:#111827}"
        "main{max-width:1220px;margin:0 auto;padding:36px 24px}h1{font-size:32px;margin:0 0 8px}.lead{line-height:1.7;font-size:17px}"
        ".metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:22px 0}.metric{background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:16px}"
        ".metric strong{display:block;font-size:26px}table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d1d5db}"
        "th,td{border-bottom:1px solid #e5e7eb;padding:10px;text-align:left;vertical-align:top}th{background:#eef2ff}"
        ".blocked{color:#b91c1c;font-weight:700}.ready{color:#047857;font-weight:700}code{background:#eef2ff;padding:2px 5px;border-radius:4px}"
        "ul{margin:6px 0 0 18px;padding:0}@media(max-width:820px){.metrics{grid-template-columns:1fr}main{padding:24px 14px}}</style></head><body><main>"
        "<h1>Stage C 节点进化 Proposal 审核报告</h1>"
        "<p class=\"lead\">本报告把 benchmark/audit 中反复出现的失败模式和高频节点组合转成候选 proposal。它只用于受控审核，不会自动发布 stable 节点。</p>"
        "<div class=\"metrics\">"
        f"<div class=\"metric\"><strong>{summary['evidence_count']}</strong>证据条目</div>"
        f"<div class=\"metric\"><strong>{summary['proposal_count']}</strong>候选 proposal</div>"
        f"<div class=\"metric\"><strong>{summary['blocked_count']}</strong>仍需审核/补证据</div>"
        f"<div class=\"metric\"><strong>{summary['candidate_count']}</strong>candidate-ready</div>"
        "</div>"
        f"<p>动作分布：{_escape(actions)}</p><p>风险分布：{_escape(risks)}</p>"
        "<table><thead><tr><th>候选</th><th>建议动作</th><th>证据</th><th>Gate 状态</th><th>还缺什么</th></tr></thead>"
        f"<tbody>{proposal_rows}</tbody></table>"
        "</main></body></html>\n"
    )


def _proposal_row(proposal: dict[str, Any]) -> str:
    status_class = "ready" if proposal["gate_status"] == "candidate_ready" else "blocked"
    evidence = _list_html(proposal.get("evidence_summary", []))
    blockers = _list_html(proposal.get("blockers", []))
    tests = ", ".join(proposal.get("required_tests", []))
    return (
        "<tr>"
        f"<td><code>{_escape(proposal['proposal_id'])}</code><br>{_escape(proposal['candidate_node_id'])}<br>风险：{_escape(proposal['risk_level'])}</td>"
        f"<td>{_escape(proposal['recommended_action'])}<br>{_escape(proposal['problem_pattern'])}<br><small>Required tests: {_escape(tests)}</small></td>"
        f"<td>{evidence}</td>"
        f"<td class=\"{status_class}\">{_escape(proposal['gate_status'])}</td>"
        f"<td>{blockers}</td>"
        "</tr>"
    )


def _list_html(items: list[Any]) -> str:
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{_escape(item)}</li>" for item in items) + "</ul>"


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data


def _escape(value: Any) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


if __name__ == "__main__":
    main()
