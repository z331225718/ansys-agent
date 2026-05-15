from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.benchmark.stage_b_presentation import build_stage_b_presentation_files


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="build_stage_b_report.py")
    parser.add_argument(
        "--group-b-report",
        default="benchmarks/runs/stage_b_b_10task_after_node_fixes/stage_b_report.json",
        help="Path to the B-only Stage B report JSON.",
    )
    parser.add_argument(
        "--group-c-report",
        default="benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json",
        help="Path to the C-only Stage B report JSON.",
    )
    parser.add_argument(
        "--output-html",
        default="benchmarks/reports/stage_b_10task_compare.html",
        help="Presentation HTML output path.",
    )
    parser.add_argument(
        "--output-json",
        default="benchmarks/reports/stage_b_10task_compare.json",
        help="Presentation JSON output path.",
    )
    parser.add_argument(
        "--model-name",
        default="deepseek-v4-flash / AEDT 2026.1",
        help="Model or harness label shown in the HTML report.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = build_stage_b_presentation_files(
        group_b_report_path=_resolve(args.group_b_report),
        group_c_report_path=_resolve(args.group_c_report),
        output_json=_resolve(args.output_json),
        output_html=_resolve(args.output_html),
        repo_root=REPO_ROOT,
        model_name=args.model_name,
    )
    print(f"Stage B presentation JSON written to: {_resolve(args.output_json)}")
    print(f"Stage B presentation HTML written to: {_resolve(args.output_html)}")
    print(f"Group metrics: {report['groups']}")


def _resolve(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / path


if __name__ == "__main__":
    main()
