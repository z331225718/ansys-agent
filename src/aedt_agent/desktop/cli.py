from __future__ import annotations

import argparse
import json
from typing import Any

from aedt_agent.desktop.installer import install_extension
from aedt_agent.desktop.installer import select_live_port
from aedt_agent.desktop.installer import uninstall_extension
from aedt_agent.desktop.launcher import ClaudeDesktopLauncher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ansys-agent-desktop")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "uninstall", "launch"):
        command = commands.add_parser(name)
        command.add_argument("--port", type=int)
        command.add_argument("--version", default="2026.1")
    commands.choices["install"].add_argument("--personal-lib")
    commands.choices["launch"].add_argument("--project-root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: dict[str, Any]
    if args.command == "install":
        result = install_extension(
            port=args.port,
            version=args.version,
            personal_lib=args.personal_lib,
        )
    elif args.command == "uninstall":
        result = uninstall_extension(port=args.port, version=args.version)
    else:
        port = select_live_port(args.port)
        result = ClaudeDesktopLauncher(project_root=args.project_root).launch(port=port, version=args.version)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
