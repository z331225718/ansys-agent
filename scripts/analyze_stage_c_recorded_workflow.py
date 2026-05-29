from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.layout.recorded_workflow import analyze_recorded_workflow
from aedt_agent.reporting.recorded_workflow_report import render_recorded_workflow_html


def main() -> None:
    args = _parse_args()
    analysis = analyze_recorded_workflow(args.source)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    args.output_html.write_text(render_recorded_workflow_html(analysis), encoding="utf-8")

    print(f"Stage C.5 recorded workflow analysis: {args.output_json}")
    print(f"Stage C.5 recorded workflow report: {args.output_html}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a recorded AEDT BRD workflow into Stage C.5 bridge artifacts.")
    parser.add_argument("--source", required=True, type=Path, help="Recorded AEDT Python script to analyze.")
    parser.add_argument("--output-json", required=True, type=Path, help="Path for the structured JSON analysis.")
    parser.add_argument("--output-html", required=True, type=Path, help="Path for the Chinese HTML analysis report.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
