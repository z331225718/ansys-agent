from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


DEFAULT_RUN_DIRS = [
    "benchmarks/runs/stage_c_real_microstrip_smoke",
    "benchmarks/runs/stage_c_real_wave_port_smoke",
    "benchmarks/runs/stage_c_real_radiation_airbox_smoke",
]


@dataclass(frozen=True)
class SmokeRun:
    run_dir: Path
    template: str
    status: str
    adapter: str
    step_count: int
    validation_summary: str
    validation_passed: bool
    check_count: int
    failed_check_count: int
    nodes: list[str]
    artifacts: list[str]
    coverage: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "template": self.template,
            "status": self.status,
            "adapter": self.adapter,
            "step_count": self.step_count,
            "validation_summary": self.validation_summary,
            "validation_passed": self.validation_passed,
            "check_count": self.check_count,
            "failed_check_count": self.failed_check_count,
            "nodes": self.nodes,
            "artifacts": self.artifacts,
            "coverage": self.coverage,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Chinese dashboard for Stage C real AEDT smoke runs.")
    parser.add_argument("--run-dir", action="append", help="Smoke run directory. Repeatable. Defaults to the 3 real Stage C smoke runs.")
    parser.add_argument("--output-html", default="benchmarks/reports/stage_c_real_smoke_dashboard.html")
    parser.add_argument("--output-json", default="benchmarks/reports/stage_c_real_smoke_dashboard.json")
    args = parser.parse_args()

    run_dirs = [Path(item) for item in (args.run_dir or DEFAULT_RUN_DIRS)]
    runs = [load_smoke_run(path) for path in run_dirs]
    dashboard = build_dashboard(runs)

    output_json = Path(args.output_json)
    output_html = Path(args.output_html)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_html.write_text(render_html(dashboard), encoding="utf-8")
    print(json.dumps(dashboard, ensure_ascii=False, indent=2, sort_keys=True))


def load_smoke_run(run_dir: Path) -> SmokeRun:
    summary = _read_json(run_dir / "smoke_summary.json")
    workflow = _read_json(run_dir / "workflow_run.json")
    validation = summary.get("model_validation") if isinstance(summary.get("model_validation"), dict) else {}
    checks = validation.get("checks", []) if isinstance(validation.get("checks"), list) else []
    failed_checks = validation.get("failed_checks", []) if isinstance(validation.get("failed_checks"), list) else []
    nodes = [str(step.get("node_id", "")) for step in workflow.get("steps", []) if isinstance(step, dict)]
    return SmokeRun(
        run_dir=run_dir,
        template=str(summary.get("template", workflow.get("workflow_id", ""))),
        status=str(summary.get("status", workflow.get("status", ""))),
        adapter=str(summary.get("adapter", "")),
        step_count=int(summary.get("step_count", len(nodes)) or 0),
        validation_summary=str(validation.get("summary", "")),
        validation_passed=bool(validation.get("passed", False)),
        check_count=len(checks),
        failed_check_count=len(failed_checks),
        nodes=nodes,
        artifacts=[str(item) for item in summary.get("artifacts", []) if isinstance(item, str)],
        coverage=_coverage_from_nodes(nodes),
    )


def build_dashboard(runs: list[SmokeRun]) -> dict[str, Any]:
    succeeded = [run for run in runs if run.status == "succeeded" and run.validation_passed]
    coverage = sorted({item for run in runs for item in run.coverage})
    return {
        "title": "Stage C 真实 AEDT Smoke Dashboard",
        "summary": {
            "run_count": len(runs),
            "succeeded_count": len(succeeded),
            "success_rate": len(succeeded) / len(runs) if runs else 0.0,
            "coverage": coverage,
        },
        "runs": [run.to_dict() for run in runs],
    }


def render_html(dashboard: dict[str, Any]) -> str:
    summary = dashboard["summary"]
    rows = "\n".join(_run_row(run) for run in dashboard["runs"])
    coverage = "、".join(summary["coverage"])
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Stage C 真实 AEDT Smoke Dashboard</title>"
        "<style>body{font-family:Arial,'Noto Sans SC',sans-serif;margin:0;background:#f8fafc;color:#111827}"
        "main{max-width:1180px;margin:0 auto;padding:36px 24px}h1{font-size:32px;margin:0 0 8px}"
        ".lead{line-height:1.7;font-size:17px}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:22px 0}"
        ".metric{background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:16px}.metric strong{display:block;font-size:28px}"
        "table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d1d5db}th,td{border-bottom:1px solid #e5e7eb;padding:10px;text-align:left;vertical-align:top}"
        "th{background:#eef2ff}.ok{color:#047857;font-weight:700}.fail{color:#b91c1c;font-weight:700}code{background:#eef2ff;padding:2px 5px;border-radius:4px}"
        "@media(max-width:760px){.metrics{grid-template-columns:1fr}main{padding:24px 14px}}</style></head><body><main>"
        "<h1>Stage C 真实 AEDT Smoke Dashboard</h1>"
        "<p class=\"lead\">本页汇总 Stage C 受控节点 workflow 在真实 AEDT 2026.1 non-graphical 下的 smoke 结果。判据来自 workflow artifact 中的模型事实 validation，不是只看脚本是否报错。</p>"
        "<div class=\"metrics\">"
        f"<div class=\"metric\"><strong>{summary['succeeded_count']}/{summary['run_count']}</strong>真实 smoke 通过</div>"
        f"<div class=\"metric\"><strong>{summary['success_rate']:.0%}</strong>validation success rate</div>"
        f"<div class=\"metric\"><strong>{_escape(coverage)}</strong>节点能力覆盖</div>"
        "</div>"
        "<table><thead><tr><th>模板</th><th>状态</th><th>节点</th><th>Validation</th><th>产物</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</main></body></html>\n"
    )


def _run_row(run: dict[str, Any]) -> str:
    status_class = "ok" if run["status"] == "succeeded" and run["validation_passed"] else "fail"
    artifacts = "<br>".join(
        f"<code>{_escape(str(Path(run['run_dir']) / artifact))}</code>" for artifact in run["artifacts"]
    )
    return (
        "<tr>"
        f"<td>{_escape(run['template'])}<br><code>{_escape(run['run_dir'])}</code></td>"
        f"<td class=\"{status_class}\">{_escape(run['status'])}</td>"
        f"<td>{_escape(', '.join(run['nodes']))}</td>"
        f"<td>{_escape(run['validation_summary'])}<br>{run['check_count']} checks, {run['failed_check_count']} failed</td>"
        f"<td>{artifacts}</td>"
        "</tr>"
    )


def _coverage_from_nodes(nodes: list[str]) -> list[str]:
    mapping = {
        "create_substrate": "geometry",
        "create_conductor_or_geometry_group": "geometry",
        "create_setup": "setup",
        "create_sweep_or_export": "sweep",
        "select_face": "selection",
        "create_port": "port",
        "create_airbox": "airbox",
        "assign_boundary": "boundary",
    }
    return sorted({mapping[node] for node in nodes if node in mapping})


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
