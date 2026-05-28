from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.layout.channel_scoring import score_channel_result
from aedt_agent.reporting.channel_scoring_report import render_channel_score_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Stage C.4 channel Touchstone/TDR results offline.")
    parser.add_argument("--touchstone", required=True)
    parser.add_argument("--tdr", required=True)
    parser.add_argument("--frequency-stop-ghz", type=float, default=26.56)
    parser.add_argument("--rl-target-db", type=float, default=-20.0)
    parser.add_argument("--tdr-target-ohm", type=float, default=100.0)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-html", required=True)
    args = parser.parse_args()
    score = score_channel_result(
        Path(args.touchstone),
        Path(args.tdr),
        frequency_stop_ghz=args.frequency_stop_ghz,
        rl_target_db=args.rl_target_db,
        tdr_target_ohm=args.tdr_target_ohm,
    )
    Path(args.output_json).write_text(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.output_html).write_text(render_channel_score_html(score), encoding="utf-8")
    print(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if score["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
