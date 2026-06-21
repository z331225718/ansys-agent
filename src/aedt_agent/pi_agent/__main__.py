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
    for command in ("preflight", "run", "status", "resume", "stop", "web", "chat", "cli"):
        child = subparsers.add_parser(command)
        child.add_argument("--case", required=True, type=Path)
        if command in {"preflight", "run"}:
            child.add_argument(
                "--no-check-paths",
                action="store_true",
                help="Validate contracts only; do not require machine-local AEDT paths.",
            )
        if command in {"resume", "stop"}:
            child.add_argument("--graph-run-id", default="")
        if command == "stop":
            child.add_argument("--reason", default="pi agent stop")
        if command in {"chat", "cli"}:
            child.add_argument(
                "--once",
                default="",
                help="Handle one natural-language request and exit.",
            )

    init = subparsers.add_parser("init")
    init.add_argument("--case", required=True, type=Path)
    init.add_argument("--target-case", default="")
    init.add_argument("--force", action="store_true")

    approve = subparsers.add_parser("approve")
    approve.add_argument("--case", required=True, type=Path)
    approve.add_argument("--approval-id", required=True)
    approve.add_argument("--option-id", default="approve")
    approve.add_argument("--comment", default=None)
    approve.add_argument("--resume", action="store_true")
    approve.add_argument("--graph-run-id", default="")

    reject = subparsers.add_parser("reject")
    reject.add_argument("--case", required=True, type=Path)
    reject.add_argument("--approval-id", required=True)
    reject.add_argument("--comment", default=None)
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
    if args.command == "init":
        _print_json(
            supervisor.init(
                target_case=args.target_case or None,
                force=bool(args.force),
            )
        )
        return 0
    if args.command == "run":
        report = supervisor.run()
        _print_json(report)
        return 1 if report["status"] == "preflight_failed" else _status_exit_code(str(report["status"]))
    if args.command == "status":
        _print_json(supervisor.status())
        return 0
    if args.command == "resume":
        report = supervisor.resume(graph_run_id=args.graph_run_id)
        _print_json(report)
        return _status_exit_code(str(report["status"]))
    if args.command == "approve":
        _print_json(
            supervisor.approve(
                approval_id=args.approval_id,
                option_id=args.option_id,
                comment=args.comment,
                resume=bool(args.resume),
                graph_run_id=args.graph_run_id,
            )
        )
        return 0
    if args.command == "reject":
        _print_json(supervisor.reject(approval_id=args.approval_id, comment=args.comment))
        return 0
    if args.command == "stop":
        _print_json(supervisor.stop(graph_run_id=args.graph_run_id, reason=args.reason))
        return 0
    if args.command == "web":
        supervisor.web()
        return 0
    if args.command in {"chat", "cli"}:
        from aedt_agent.pi_agent.chat import run_chat

        return run_chat(supervisor, once=args.once)
    raise AssertionError(f"unhandled command: {args.command}")


def _status_exit_code(status: str) -> int:
    return 2 if status in {"failed", "canceled"} else 0


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
