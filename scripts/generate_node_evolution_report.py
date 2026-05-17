from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.evolution.miner import mine_evolution_evidence
from aedt_agent.evolution.proposer import build_evolution_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate controlled node evolution proposals from benchmark/audit evidence.")
    parser.add_argument("--source", action="append", required=True, help="Stage B report JSON or node audit JSONL. Repeatable.")
    parser.add_argument("--output", default="benchmarks/reports/stage_c_node_evolution_report.json")
    args = parser.parse_args()

    paths = [Path(source) for source in args.source]
    evidence = mine_evolution_evidence(paths)
    report = build_evolution_report(evidence, source_count=len(paths))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
