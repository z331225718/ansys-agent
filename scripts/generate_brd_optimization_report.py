from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.reporting.brd_optimization_report import (  # noqa: E402
    build_brd_optimization_summary,
    render_brd_optimization_report_html,
    write_brd_optimization_history_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a BRD differential via optimization report from "
            "score evidence and model edit manifests."
        )
    )
    parser.add_argument(
        "--score-evidence",
        action="append",
        default=[],
        help="Path to brd_channel_score_evidence.json. Repeat per round.",
    )
    parser.add_argument(
        "--solve-result",
        action="append",
        default=[],
        help="Path to real-solve result.json. Repeat per round.",
    )
    parser.add_argument(
        "--solve-manifest",
        action="append",
        default=[],
        help="Path to solve_manifest.json. Repeat per round.",
    )
    parser.add_argument(
        "--model-edit-manifest",
        action="append",
        default=[],
        help="Path to model_edit_manifest.json. Repeat per edit.",
    )
    parser.add_argument("--output-html", required=True)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-history-csv", default="")
    args = parser.parse_args()
    if not (
        args.score_evidence
        or args.solve_result
        or args.solve_manifest
        or args.model_edit_manifest
    ):
        parser.error(
            "provide at least one --score-evidence, --solve-result, "
            "--solve-manifest, or --model-edit-manifest"
        )

    summary = build_brd_optimization_summary(
        score_evidence_paths=args.score_evidence,
        model_edit_manifest_paths=args.model_edit_manifest,
        solve_result_paths=args.solve_result,
        solve_manifest_paths=args.solve_manifest,
    )
    if args.output_history_csv:
        history_csv = write_brd_optimization_history_csv(
            summary,
            args.output_history_csv,
        )
        summary["optimization_history_csv"] = str(history_csv)
    output_html = Path(args.output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(
        render_brd_optimization_report_html(summary),
        encoding="utf-8",
    )
    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    print(f"BRD optimization report: {output_html}")


if __name__ == "__main__":
    main()
