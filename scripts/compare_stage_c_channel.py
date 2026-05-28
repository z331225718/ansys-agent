from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.layout.channel_scoring import compare_channel_scores


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Stage C.4 before/after channel scores.")
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    comparison = compare_channel_scores(before, after)
    Path(args.output).write_text(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if comparison["status"] in {"improved", "unchanged"} else 1)


if __name__ == "__main__":
    main()
