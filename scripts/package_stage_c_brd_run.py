from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.layout.acceptance import write_brd_acceptance_summary
from aedt_agent.reporting.stage_c_brd_report import render_brd_acceptance_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Package an existing Stage C BRD/MCM run directory.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--allow-failed", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    summary = write_brd_acceptance_summary(run_dir)
    html = render_brd_acceptance_html(summary)
    (run_dir / "acceptance_report.html").write_text(html, encoding="utf-8")
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Stage C BRD acceptance: {summary['status']}")
        print(f"Report: {run_dir / 'acceptance_report.html'}")
    raise SystemExit(0 if summary["status"] == "succeeded" or args.allow_failed else 1)


if __name__ == "__main__":
    main()
