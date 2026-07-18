from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from aedt_agent.capability_learning.promoter import CapabilityPromoter, PromotionError
from aedt_agent.capability_learning.trace_store import CapabilityTraceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m aedt_agent.capability_learning")
    subparsers = parser.add_subparsers(dest="command", required=True)
    promote = subparsers.add_parser("promote", help="generate a review-only capability candidate")
    promote.add_argument("--trace-id", required=True)
    promote.add_argument(
        "--target-kind",
        default="auto",
        choices=("auto", "harness", "skill", "workflow"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "promote":
        raise AssertionError(f"unhandled command: {args.command}")
    store = CapabilityTraceStore()
    try:
        result = CapabilityPromoter(store).promote(
            args.trace_id,
            target_kind=args.target_kind,
        )
    except (PromotionError, ValueError) as exc:
        payload = {
            "ok": False,
            "error": {
                "code": getattr(exc, "code", "invalid_request"),
                "message": str(exc),
            },
        }
        print(json.dumps(payload, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "candidate": result.to_dict()}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
