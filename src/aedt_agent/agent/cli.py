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
    create.add_argument("--brd-local-cut", action="store_true")
    create.add_argument("--layout-file")
    create.add_argument("--signal-net", action="append", default=[])
    create.add_argument("--reference-net", action="append", default=[])
    create.add_argument("--bbox")
    create.add_argument("--artifact-dir")
    create.add_argument("--adapter-mode", choices=["deterministic", "real_build"], default="deterministic")
    create.add_argument("--stackup-xml")
    create.add_argument("--recorded-analysis", type=Path)
    create.add_argument("--aedt-version", default="2026.1")
    create.add_argument("--edb-backend", choices=["auto", "grpc", "dotnet"], default="auto")
    create.add_argument("--cadence-launcher", default="")
    create.add_argument("--ansysem-root", default="")
    create.add_argument("--awp-root", default="")
    mode = create.add_mutually_exclusive_group()
    mode.add_argument("--graphical", dest="non_graphical", action="store_false")
    mode.add_argument("--non-graphical", dest="non_graphical", action="store_true")
    create.set_defaults(non_graphical=False)

    plan = mission_commands.add_parser("plan")
    plan.add_argument("--template", required=True)

    run = mission_commands.add_parser("run")
    run.add_argument("--mission-id", required=True)

    status = mission_commands.add_parser("status")
    status.add_argument("--mission-id", required=True)

    scorecard = mission_commands.add_parser("scorecard")
    scorecard.add_argument("--mission-id", required=True)
    scorecard.add_argument("--template", default="")

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
        if args.brd_local_cut:
            from aedt_agent.agent.workers import BRD_LOCAL_CUT_BUILD_CAPABILITY, build_brd_local_cut_job_input

            artifact_dir = Path(args.artifact_dir) if args.artifact_dir else args.db.parent / mission.mission_id
            recorded_layout_settings = _recorded_layout_settings_from_analysis(args.recorded_analysis)
            runtime.create_job(
                mission.mission_id,
                BRD_LOCAL_CUT_BUILD_CAPABILITY,
                "brd-local-cut:0",
                build_brd_local_cut_job_input(
                    layout_file=args.layout_file,
                    signal_nets=args.signal_net,
                    reference_nets=args.reference_net or ["GND"],
                    local_cut_region=_parse_bbox(args.bbox),
                    artifact_dir=artifact_dir,
                    target_metrics=criteria,
                    adapter_mode=args.adapter_mode,
                    stackup_xml=args.stackup_xml,
                    recorded_layout_settings=recorded_layout_settings,
                    aedt={
                        "version": args.aedt_version,
                        "non_graphical": args.non_graphical,
                        "edb_backend": args.edb_backend,
                        "cadence_launcher": args.cadence_launcher,
                        "ansysem_root": args.ansysem_root,
                        "awp_root": args.awp_root,
                    },
                ),
            )
        _print_json(mission.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "plan":
        from aedt_agent.agent.graph_template import load_graph_template

        template = load_graph_template(args.template)
        _print_json(template.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "run":
        from aedt_agent.agent.workers import BRD_LOCAL_CUT_BUILD_CAPABILITY, InMemoryWorkerRegistry, run_brd_local_cut_worker

        registry = InMemoryWorkerRegistry()
        registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
        runtime = AgentRuntime(SQLiteMissionStore(args.db), registry=registry)
        result = runtime.execute_next_job(args.mission_id, worker_id="cli")
        _print_json(
            {
                "job_id": result.job_id,
                "status": result.status.value,
                "output_payload": result.output_payload,
                "artifact_refs": result.artifact_refs,
            }
        )
        return 0 if result.status.value == "succeeded" else 2

    if args.group == "mission" and args.mission_command == "status":
        mission = runtime.get_mission(args.mission_id)
        payload: dict[str, Any] = mission.to_json_dict()
        payload["events"] = [event.to_json_dict() for event in runtime.list_events(args.mission_id)]
        payload["jobs"] = [job.to_json_dict() for job in runtime.list_jobs(args.mission_id)]
        _print_json(payload)
        return 0

    if args.group == "mission" and args.mission_command == "scorecard":
        from aedt_agent.agent.scorecard import score_mission

        report = score_mission(runtime, args.mission_id, template_id=args.template)
        _print_json(report)
        return 0 if report["status"] == "passed" else 2

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


def _recorded_layout_settings_from_analysis(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    params: dict[str, Any] = {}
    merge_recorded_layout_settings(params, data)
    return {
        "hfss_extents": dict(params.get("recorded_hfss_extents") or {}),
        "design_options": dict(params.get("recorded_design_options") or {}),
        "setup_options": dict(params.get("recorded_setup_options") or {}),
        "setup_advanced_settings": dict(params.get("recorded_setup_advanced_settings") or {}),
        "setup_curve_approximation": dict(params.get("recorded_setup_curve_approximation") or {}),
        "sweep_options": dict(params.get("recorded_sweep_options") or {}),
    }


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


def _parse_bbox(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    unit, x_min, y_min, x_max, y_max = [item.strip() for item in value.split(",", 4)]
    return {
        "type": "bbox",
        "unit": unit,
        "x_min": float(x_min),
        "y_min": float(y_min),
        "x_max": float(x_max),
        "y_max": float(y_max),
    }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
