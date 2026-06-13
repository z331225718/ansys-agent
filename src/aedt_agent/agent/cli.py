from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import MissionState
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aedt-agent")
    parser.add_argument("--db", type=Path, default=Path(".aedt-agent/missions.db"))
    subparsers = parser.add_subparsers(dest="group", required=True)

    mission = subparsers.add_parser("mission", help="Manage persistent engineering missions.")
    mission_commands = mission.add_subparsers(dest="mission_command", required=True)

    create = mission_commands.add_parser("create")
    create.add_argument("--goal", required=True)
    create.add_argument("--criterion", action="append", default=[])

    run = mission_commands.add_parser("run")
    run.add_argument("--mission-id", required=True)

    status = mission_commands.add_parser("status")
    status.add_argument("--mission-id", required=True)

    resume = mission_commands.add_parser("resume")
    resume.add_argument("--mission-id", required=True)

    approve = mission_commands.add_parser("approve")
    approve.add_argument("--mission-id", required=True)
    approve.add_argument("--approval-id", required=False)
    approve.add_argument("--option-id", required=False)
    approve.add_argument("--comment", required=False)

    cancel = mission_commands.add_parser("cancel")
    cancel.add_argument("--mission-id", required=True)

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = AgentRuntime(SQLiteMissionStore(args.db))

    if args.group == "mission" and args.mission_command == "create":
        criteria = [_parse_criterion(value) for value in args.criterion]
        mission = runtime.create_mission(args.goal, criteria, [])
        _print_json(mission.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "status":
        mission = runtime.get_mission(args.mission_id)
        payload: dict[str, Any] = mission.to_json_dict()
        payload["events"] = [event.to_json_dict() for event in runtime.list_events(args.mission_id)]
        _print_json(payload)
        return 0

    if args.group == "mission" and args.mission_command == "cancel":
        mission = runtime.store.update_mission_state(args.mission_id, MissionState.CANCELED)
        _print_json(mission.to_json_dict())
        return 0

    _print_json(
        {
            "command": f"{args.group}.{args.mission_command}",
            "message": "该 Mission 命令面已安装，但具体执行循环将在 BRD Worker 阶段启用。",
            "status": "runtime_command_not_enabled",
        }
    )
    return 2


def _parse_criterion(value: str) -> dict[str, Any]:
    for op in (">=", "<=", "==", ">", "<"):
        if op in value:
            metric, raw = value.split(op, 1)
            return {"metric": metric.strip(), "op": op, "value": _parse_number(raw.strip())}
    return {"metric": value, "op": "exists", "value": True}


def _parse_number(value: str) -> float | str:
    try:
        return float(value)
    except ValueError:
        return value


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
