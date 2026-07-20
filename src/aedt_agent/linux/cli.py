from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from aedt_agent.linux.approval_host import request
from aedt_agent.linux.launcher import LinuxClaudeLauncher, LinuxLaunchError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ansys-agent-linux", description="Local Linux AEDT assistant harness.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "launch"):
        command = commands.add_parser(name)
        command.add_argument("--port", type=int, required=True)
        command.add_argument("--version", default="2026.1")
        command.add_argument("--project-root")
        command.add_argument("--python")
        command.add_argument("--claude")
    approvals = commands.add_parser("approvals", help="Review and decide a pending Linux AEDT operation.")
    approvals.add_argument("--socket", required=True, type=Path)
    approvals.add_argument("--resource")
    approvals.add_argument("--reject", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.name == "nt":
        print(json.dumps({"status": "failed", "error": "ansys-agent-linux must run on Linux"}), file=sys.stderr)
        return 2
    try:
        if args.command == "approvals":
            return _approve(args)
        launcher = LinuxClaudeLauncher(
            project_root=args.project_root,
            python_executable=args.python,
            claude_executable=args.claude,
        )
        if args.command == "prepare":
            context = launcher.context_loader(args.port, args.version)
            result = launcher.prepare(context)
        else:
            result = launcher.launch(port=args.port, version=args.version)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error_message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def _approve(args: argparse.Namespace) -> int:
    pending = request(args.socket, {"command": "list"})
    if not isinstance(pending, list):
        raise LinuxLaunchError("approval broker returned invalid pending records")
    if not pending:
        print("没有待审批的 AEDT 操作。")
        return 0
    selected = _select_pending(pending, args.resource)
    print(json.dumps(selected, ensure_ascii=False, indent=2))
    action = "reject" if args.reject else "approve"
    answer = input("确认拒绝此操作？[y/N] " if args.reject else "确认批准此操作？[y/N] ").strip().casefold()
    if answer not in {"y", "yes"}:
        print("未作出决定，操作仍保持 pending。")
        return 1
    result = request(args.socket, {"command": action, "resource_id": selected["resource_id"]})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _select_pending(pending: list[dict[str, Any]], resource_id: str | None) -> dict[str, Any]:
    if resource_id:
        for item in pending:
            if item.get("resource_id") == resource_id:
                return item
        raise LinuxLaunchError("指定的 resource 不在待审批列表中")
    if len(pending) != 1:
        raise LinuxLaunchError("存在多个待审批操作，请使用 --resource 精确指定")
    return pending[0]


if __name__ == "__main__":
    raise SystemExit(main())
