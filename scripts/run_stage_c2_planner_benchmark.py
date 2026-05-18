from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.planner_benchmark import run_planner_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Stage C.2 planner benchmark.")
    parser.add_argument("--planner-mode", default="deterministic", choices=["deterministic", "llm"])
    parser.add_argument("--output-html", default="benchmarks/reports/stage_c2_planner_benchmark.html")
    parser.add_argument("--output-json", default="benchmarks/reports/stage_c2_planner_benchmark.json")
    args = parser.parse_args()

    report = run_planner_benchmark(
        REPO_ROOT,
        output_html=Path(args.output_html),
        output_json=Path(args.output_json),
        planner_mode=args.planner_mode,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
