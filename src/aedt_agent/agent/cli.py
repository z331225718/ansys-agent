from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any


MISSION_COMMANDS = ("create", "run", "status", "resume", "approve", "cancel")
RUNTIME_UNAVAILABLE_MESSAGE = "Mission Runtime 尚未安装；当前版本只完成 Agent-First 架构迁移。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aedt-agent")
    subparsers = parser.add_subparsers(dest="group", required=True)

    mission = subparsers.add_parser("mission", help="Manage persistent engineering missions.")
    mission_commands = mission.add_subparsers(dest="mission_command", required=True)

    for command in MISSION_COMMANDS:
        command_parser = mission_commands.add_parser(command)
        if command in {"status", "resume", "approve", "cancel"}:
            command_parser.add_argument("--mission-id", required=True)

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload: dict[str, Any] = {
        "command": f"{args.group}.{args.mission_command}",
        "message": RUNTIME_UNAVAILABLE_MESSAGE,
        "status": "runtime_unavailable",
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
