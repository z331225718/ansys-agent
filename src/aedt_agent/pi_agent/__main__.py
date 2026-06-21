from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aedt_agent.pi_agent.case_config import load_case_config
from aedt_agent.pi_agent.supervisor import PiAgentSupervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m aedt_agent.pi_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "run", "status"):
        child = subparsers.add_parser(command)
        child.add_argument("--case", required=True, type=Path)
        if command in {"preflight", "run"}:
            child.add_argument(
                "--no-check-paths",
                action="store_true",
                help="Validate contracts only; do not require machine-local AEDT paths.",
            )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    case = load_case_config(
        args.case,
        no_check_paths=getattr(args, "no_check_paths", None),
    )
    supervisor = PiAgentSupervisor(case)
    if args.command == "preflight":
        report = supervisor.preflight()
        _print_json(report)
        return 0 if report["status"] == "passed" else 1
    if args.command == "run":
        report = supervisor.run()
        _print_json(report)
        return 1 if report["status"] == "preflight_failed" else _status_exit_code(str(report["status"]))
    if args.command == "status":
        _print_json(supervisor.status())
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def _status_exit_code(status: str) -> int:
    return 2 if status in {"failed", "canceled"} else 0


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
