from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    create.add_argument("--brd-local-cut-model-review", action="store_true")
    create.add_argument("--brd-channel-score", action="store_true")
    create.add_argument("--brd-real-solve", action="store_true")
    create.add_argument("--brd-recorded-void-action", action="store_true")
    create.add_argument("--layout-file")
    create.add_argument("--signal-net", action="append", default=[])
    create.add_argument("--reference-net", action="append", default=[])
    create.add_argument("--bbox")
    create.add_argument("--touchstone")
    create.add_argument("--tdr")
    create.add_argument("--project")
    create.add_argument("--setup", default="Setup1")
    create.add_argument("--sweep", default="Sweep1")
    create.add_argument("--tdr-expression")
    create.add_argument("--expected-port-count", type=int, default=2)
    create.add_argument("--solve-timeout-seconds", type=int, default=7200)
    create.add_argument("--before-touchstone")
    create.add_argument("--before-tdr")
    create.add_argument("--after-touchstone")
    create.add_argument("--after-tdr")
    create.add_argument("--action-layer")
    create.add_argument("--action-region")
    create.add_argument("--action-shape", choices=["circle", "rectangle"], default="circle")
    create.add_argument("--action-variable")
    create.add_argument("--old-value-mil", type=float)
    create.add_argument("--new-value-mil", type=float)
    create.add_argument("--min-value-mil", type=float, default=0.0)
    create.add_argument("--max-value-mil", type=float, default=1000.0)
    create.add_argument("--max-abs-delta-mil", type=float, default=2.0)
    create.add_argument("--artifact-dir")
    create.add_argument("--adapter-mode", choices=["deterministic", "real_build"], default="deterministic")
    create.add_argument("--frequency-start-ghz", type=float, default=0.0)
    create.add_argument("--frequency-stop-ghz", type=float, default=67.0)
    create.add_argument("--rl-target-db", type=float, default=-20.0)
    create.add_argument("--tdr-target-ohm", type=float, default=100.0)
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

    run_graph = mission_commands.add_parser("run-graph")
    run_graph.add_argument("--mission-id", required=True)
    run_graph.add_argument("--template", required=True)
    run_graph.add_argument("--worker-id", default="cli-graph")
    run_graph.add_argument("--max-steps", type=int, default=32)
    run_graph.add_argument("--max-workers", type=int, default=4)
    run_graph.add_argument("--visualize", action="store_true")

    advance_graph_parser = mission_commands.add_parser("advance-graph")
    advance_graph_parser.add_argument("--graph-run-id", required=True)
    advance_graph_parser.add_argument("--worker-id", default="cli-graph-step")
    advance_graph_parser.add_argument("--max-workers", type=int, default=4)
    advance_graph_parser.add_argument("--visualize", action="store_true")

    graph_status_parser = mission_commands.add_parser("graph-status")
    graph_status_parser.add_argument("--graph-run-id", required=True)

    graph_visualize_parser = mission_commands.add_parser("graph-visualize")
    graph_visualize_parser.add_argument("--graph-run-id", required=True)
    graph_visualize_parser.add_argument("--format", choices=["ascii", "mermaid"], default="ascii")

    web_parser = mission_commands.add_parser("web")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8766)
    web_parser.add_argument("--db", type=Path, default=Path(".aedt-agent/missions.db"))

    resume_graph_parser = mission_commands.add_parser("resume-graph")
    resume_graph_parser.add_argument("--graph-run-id", required=True)
    resume_graph_parser.add_argument("--worker-id", default="cli-graph-resume")
    resume_graph_parser.add_argument("--max-workers", type=int, default=4)

    advance = mission_commands.add_parser("advance")
    advance.add_argument("--mission-id", required=True)
    advance.add_argument("--worker-id", default="cli-loop")
    advance.add_argument("--profile", default="safe-recorded")

    loop_status = mission_commands.add_parser("loop-status")
    loop_status.add_argument("--mission-id", required=True)

    status = mission_commands.add_parser("status")
    status.add_argument("--mission-id", required=True)

    scorecard = mission_commands.add_parser("scorecard")
    scorecard.add_argument("--mission-id", required=True)
    scorecard.add_argument("--template", default="")

    events = mission_commands.add_parser("events")
    events.add_argument("--mission-id", required=True)

    graph_runs = mission_commands.add_parser("graph-runs")
    graph_runs.add_argument("--mission-id", required=True)

    node_runs = mission_commands.add_parser("node-runs")
    node_runs.add_argument("--graph-run-id", required=True)

    artifacts = mission_commands.add_parser("artifacts")
    artifacts.add_argument("--mission-id", required=True)

    artifact_query = mission_commands.add_parser("artifact-query")
    artifact_query.add_argument("--mission-id", required=True)
    artifact_query.add_argument("--artifact-ref", required=True)
    artifact_window = artifact_query.add_mutually_exclusive_group(
        required=True
    )
    artifact_window.add_argument(
        "--frequency",
        nargs=2,
        type=float,
    )
    artifact_window.add_argument(
        "--time-ps",
        nargs=2,
        type=float,
    )
    artifact_query.add_argument("--max-points", type=int, default=64)
    artifact_query.add_argument("--target", type=float)

    evidence = mission_commands.add_parser("evidence")
    evidence.add_argument("--mission-id", required=True)

    actions = mission_commands.add_parser("actions")
    actions.add_argument("--mission-id", required=True)

    action_status = mission_commands.add_parser("action-status")
    action_status.add_argument("--action-id", required=True)

    approve_action_parser = mission_commands.add_parser("approve-action")
    approve_action_parser.add_argument("--approval-id", required=True)
    approve_action_parser.add_argument("--action-id", required=True)
    approve_action_parser.add_argument("--action-digest", required=True)
    approve_action_parser.add_argument("--comment")

    resume = mission_commands.add_parser("resume")
    resume.add_argument("--mission-id", required=True)
    resume.add_argument("--worker-id", default="cli-resume")
    resume.add_argument("--profile", default="safe-recorded")

    recover_harness = mission_commands.add_parser("recover-harness")
    recover_harness.add_argument("--mission-id", required=True)
    recover_harness.add_argument("--terminate-stale", action="store_true")

    approve = mission_commands.add_parser("approve")
    approve.add_argument("--mission-id", required=False)
    approve.add_argument("--approval-id", required=True)
    approve.add_argument("--option-id", required=True)
    approve.add_argument("--comment", required=False)

    cancel = mission_commands.add_parser("cancel")
    cancel.add_argument("--mission-id", required=True)

    takeover = mission_commands.add_parser("takeover")
    takeover.add_argument("--graph-run-id", required=True)
    takeover.add_argument("--reason", default="orchestrator takeover")
    takeover.add_argument("--new-template", default="")
    takeover.add_argument("--override-payload", default="")

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
        if args.brd_local_cut_model_review:
            from aedt_agent.agent.graph_runner import run_graph
            from aedt_agent.agent.graph_template import load_graph_template

            artifact_dir = Path(args.artifact_dir) if args.artifact_dir else args.db.parent / mission.mission_id
            template = load_graph_template("brd_local_cut_build")
            report = run_graph(
                runtime, mission.mission_id, template,
                initial_payload={
                    "layout_file": str(args.layout_file),
                    "signal_nets": list(args.signal_net),
                    "reference_nets": list(args.reference_net) or ["GND"],
                    "local_cut_region": _parse_bbox(args.bbox),
                    "artifact_dir": str(artifact_dir),
                    "target_metrics": criteria,
                    "adapter_mode": args.adapter_mode,
                },
            )
            _print_json(report)
            return 0 if report["status"] == "succeeded" else 1
        if args.brd_channel_score:
            from aedt_agent.agent.workers import BRD_CHANNEL_SCORE_CAPABILITY, build_brd_channel_score_job_input

            artifact_dir = Path(args.artifact_dir) if args.artifact_dir else args.db.parent / mission.mission_id
            runtime.create_job(
                mission.mission_id,
                BRD_CHANNEL_SCORE_CAPABILITY,
                "brd-channel-score:0",
                build_brd_channel_score_job_input(
                    touchstone_path=args.touchstone,
                    tdr_path=args.tdr,
                    artifact_dir=artifact_dir,
                    frequency_start_ghz=args.frequency_start_ghz,
                    frequency_stop_ghz=args.frequency_stop_ghz,
                    rl_target_db=args.rl_target_db,
                    tdr_target_ohm=args.tdr_target_ohm,
                ),
            )
        if args.brd_real_solve:
            from aedt_agent.agent.workers import (
                BRD_REAL_SOLVE_CAPABILITY,
                build_brd_real_solve_job_input,
            )

            _require_real_solve_args(args)
            runtime.create_job(
                mission.mission_id,
                BRD_REAL_SOLVE_CAPABILITY,
                (
                    f"brd-real-solve:{Path(args.project).resolve()}:"
                    f"{args.setup}:{args.sweep}"
                ),
                build_brd_real_solve_job_input(
                    project_path=args.project,
                    setup_name=args.setup,
                    sweep_name=args.sweep,
                    tdr_expression=args.tdr_expression,
                    expected_port_count=args.expected_port_count,
                    frequency_start_ghz=args.frequency_start_ghz,
                    frequency_stop_ghz=args.frequency_stop_ghz,
                    rl_target_db=args.rl_target_db,
                    tdr_target_ohm=args.tdr_target_ohm,
                    aedt={
                        "version": args.aedt_version,
                        "non_graphical": args.non_graphical,
                    },
                ),
                timeout_seconds=args.solve_timeout_seconds,
                retry_limit=1,
            )
        if args.brd_recorded_void_action:
            from aedt_agent.agent.actions import ActionRecord, request_action_approval, validate_action
            from aedt_agent.agent.workers import (
                BRD_RECORDED_VOID_ACTION_CAPABILITY,
                build_brd_recorded_void_action_job_input,
            )

            _require_recorded_action_args(args)
            old_value = float(args.old_value_mil)
            new_value = float(args.new_value_mil)
            action = validate_action(
                ActionRecord.create(
                    action_id=str(uuid4()),
                    mission_id=mission.mission_id,
                    target={
                        "layer": args.action_layer,
                        "region_ref": args.action_region,
                        "shape": args.action_shape,
                    },
                    parameters={
                        "variable": args.action_variable,
                        "old_value_mil": old_value,
                        "new_value_mil": new_value,
                        "delta_mil": new_value - old_value,
                    },
                    constraints={
                        "min_value_mil": args.min_value_mil,
                        "max_value_mil": args.max_value_mil,
                        "max_abs_delta_mil": args.max_abs_delta_mil,
                    },
                    reason={
                        "evidence_package_id": "",
                        "summary": "用户提交的 recorded before/after artifacts 用于验证受控 void 调整。",
                    },
                    adapter_mode="recorded",
                    adapter_input={
                        "before_touchstone": args.before_touchstone,
                        "before_tdr": args.before_tdr,
                        "after_touchstone": args.after_touchstone,
                        "after_tdr": args.after_tdr,
                        "frequency_start_ghz": args.frequency_start_ghz,
                        "frequency_stop_ghz": args.frequency_stop_ghz,
                        "rl_target_db": args.rl_target_db,
                        "tdr_target_ohm": args.tdr_target_ohm,
                    },
                )
            )
            runtime.store.create_action(action)
            request_action_approval(runtime.store, action.action_id)
            runtime.create_job(
                mission.mission_id,
                BRD_RECORDED_VOID_ACTION_CAPABILITY,
                f"brd-recorded-void-action:{action.action_id}",
                build_brd_recorded_void_action_job_input(action_id=action.action_id),
            )
        _print_json(mission.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "plan":
        from aedt_agent.agent.graph_template import load_graph_template

        template = load_graph_template(args.template)
        _print_json(template.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "run":
        runtime = _runtime_with_workers(args.db)
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

    if args.group == "mission" and args.mission_command == "run-graph":
        from aedt_agent.agent.graph_runner import run_graph
        from aedt_agent.agent.graph_template import load_graph_template

        runtime = _runtime_with_workers(args.db)
        template = load_graph_template(args.template)
        report = run_graph(
            runtime,
            args.mission_id,
            template,
            max_steps=args.max_steps,
            worker_id=args.worker_id,
            max_workers=args.max_workers,
        )
        _print_json(report)
        return _graph_exit_code(report["status"])

    if args.group == "mission" and args.mission_command == "advance-graph":
        from aedt_agent.agent.graph_runner import advance_graph

        runtime = _runtime_with_workers(args.db)
        report = advance_graph(
            runtime,
            args.graph_run_id,
            worker_id=args.worker_id,
            max_workers=args.max_workers,
            visualize=args.visualize,
        )
        _print_json(report)
        return _graph_exit_code(report["status"])

    if args.group == "mission" and args.mission_command == "graph-status":
        from aedt_agent.agent.graph_runner import graph_status

        _print_json(graph_status(runtime, args.graph_run_id))
        return 0

    if args.group == "mission" and args.mission_command == "graph-visualize":
        from aedt_agent.agent.graph_runner import graph_status
        from aedt_agent.agent.graph_visualizer import render_graph_live, render_graph_mermaid

        status = graph_status(runtime, args.graph_run_id)
        snapshot = status.get("graph_run", {}).get("template_snapshot", {})
        node_runs = status.get("node_runs", [])
        handoffs = status.get("handoffs", [])
        title = f"Graph: {status['graph_run']['graph_run_id'][:12]}…  ({status['status']})"

        if args.format == "mermaid":
            print(render_graph_mermaid(snapshot, node_runs, handoffs))
        else:
            print(render_graph_live(snapshot, node_runs, handoffs, title=title))
        return 0

    if args.group == "mission" and args.mission_command == "resume-graph":
        from aedt_agent.agent.graph_runner import resume_graph

        runtime = _runtime_with_workers(args.db)
        report = resume_graph(
            runtime,
            args.graph_run_id,
            worker_id=args.worker_id,
            max_workers=args.max_workers,
        )
        _print_json(report)
        return _graph_exit_code(report["status"])

    if args.group == "mission" and args.mission_command == "advance":
        from aedt_agent.agent.orchestrator import MissionLoopController

        profile = _load_execution_profile(args.profile)
        runtime = _runtime_with_workers(args.db, profile)
        controller = MissionLoopController(runtime, profile=profile)
        decision = controller.advance(args.mission_id, worker_id=args.worker_id)
        payload = controller.status(args.mission_id)
        payload["decision"] = decision.to_json_dict()
        _print_json(payload)
        return _loop_exit_code(decision.decision.value)

    if args.group == "mission" and args.mission_command == "loop-status":
        from aedt_agent.agent.orchestrator import MissionLoopController

        controller = MissionLoopController(runtime)
        _print_json(controller.status(args.mission_id))
        return 0

    if args.group == "mission" and args.mission_command == "resume":
        from aedt_agent.agent.orchestrator import MissionLoopController

        profile = _load_execution_profile(args.profile)
        runtime = _runtime_with_workers(args.db, profile)
        recovered = runtime.recover_expired_leases()
        controller = MissionLoopController(runtime, profile=profile)
        decision = controller.advance(args.mission_id, worker_id=args.worker_id)
        payload = controller.status(args.mission_id)
        payload["decision"] = decision.to_json_dict()
        payload["recovered_job_ids"] = recovered
        _print_json(payload)
        return _loop_exit_code(decision.decision.value)

    if args.group == "mission" and args.mission_command == "recover-harness":
        runtime = _runtime_with_harness(args.db)
        report = runtime.recover_harness_attempts(
            args.mission_id,
            terminate_stale=args.terminate_stale,
        )
        _print_json(report)
        return 0

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

    if args.group == "mission" and args.mission_command == "events":
        _print_json({"events": [event.to_json_dict() for event in runtime.list_events(args.mission_id)]})
        return 0

    if args.group == "mission" and args.mission_command == "graph-runs":
        _print_json({"graph_runs": [graph_run.to_json_dict() for graph_run in runtime.store.list_graph_runs(args.mission_id)]})
        return 0

    if args.group == "mission" and args.mission_command == "node-runs":
        _print_json({"node_runs": [node_run.to_json_dict() for node_run in runtime.store.list_node_runs(args.graph_run_id)]})
        return 0

    if args.group == "mission" and args.mission_command == "artifacts":
        _print_json({"artifacts": [artifact.to_json_dict() for artifact in runtime.store.list_artifact_manifests(args.mission_id)]})
        return 0

    if args.group == "mission" and args.mission_command == "artifact-query":
        from aedt_agent.agent.evaluation import ArtifactQueryService

        service = ArtifactQueryService(runtime.store)
        if args.frequency is not None:
            result = service.query_sparameter(
                args.mission_id,
                args.artifact_ref,
                args.frequency[0],
                args.frequency[1],
                max_points=args.max_points,
                rl_target_db=(
                    -20.0 if args.target is None else args.target
                ),
            )
        else:
            result = service.query_tdr(
                args.mission_id,
                args.artifact_ref,
                args.time_ps[0],
                args.time_ps[1],
                max_points=args.max_points,
                target_ohm=(
                    100.0 if args.target is None else args.target
                ),
            )
        _print_json(result)
        return 0

    if args.group == "mission" and args.mission_command == "evidence":
        evidence_packages = runtime.store.list_evidence_packages(
            args.mission_id
        )
        evidence_packages.sort(
            key=lambda evidence: (
                0
                if isinstance(
                    evidence.summary.get("scorecard"),
                    dict,
                )
                else 1
            )
        )
        _print_json(
            {
                "evidence_packages": [
                    evidence.to_json_dict()
                    for evidence in evidence_packages
                ]
            }
        )
        return 0

    if args.group == "mission" and args.mission_command == "actions":
        _print_json({"actions": [action.to_json_dict() for action in runtime.store.list_actions(args.mission_id)]})
        return 0

    if args.group == "mission" and args.mission_command == "action-status":
        action = runtime.store.get_action(args.action_id)
        _print_json(
            {
                "action": action.to_json_dict(),
                "executions": [
                    execution.to_json_dict() for execution in runtime.store.list_action_executions(args.action_id)
                ],
            }
        )
        return 0

    if args.group == "mission" and args.mission_command == "approve-action":
        from aedt_agent.agent.actions import approve_action

        action = approve_action(
            runtime.store,
            args.approval_id,
            args.action_id,
            args.action_digest,
            comment=args.comment,
        )
        _print_json(action.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "approve":
        from aedt_agent.agent.approvals import ApprovalService

        approval = ApprovalService(runtime.store).approve(
            args.approval_id,
            selected_option_id=args.option_id,
            comment=args.comment,
        )
        _print_json(approval.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "cancel":
        mission = runtime.store.update_mission_state(args.mission_id, MissionState.CANCELED)
        _print_json(mission.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "takeover":
        from aedt_agent.agent.graph_runner import create_graph_run
        from aedt_agent.agent.graph_template import load_graph_template

        graph_run = runtime.store.get_graph_run(args.graph_run_id)
        if graph_run is None:
            print(f"graph run not found: {args.graph_run_id}", file=sys.stderr)
            return 1
        # Cancel the current graph
        runtime.store.update_graph_run_status(args.graph_run_id, GraphRunStatus.CANCELED,
            error={"code": "orchestrator_takeover", "message": args.reason or "orchestrator takeover"})
        # Create new graph with specified template + payload
        template = load_graph_template(args.new_template or graph_run.template_id)
        payload = dict(graph_run.initial_payload)
        if args.reason:
            payload["_takeover_reason"] = args.reason
        if args.override_payload:
            import json as _json
            try:
                override = _json.loads(args.override_payload)
                payload.update(override)
            except _json.JSONDecodeError:
                print(f"invalid override-payload JSON: {args.override_payload}", file=sys.stderr)
                return 1
        new_graph_run = create_graph_run(runtime, graph_run.mission_id, template, initial_payload=payload)
        _print_json({
            "action": "takeover",
            "canceled_graph_run_id": graph_run.graph_run_id,
            "new_graph_run_id": new_graph_run.graph_run_id,
            "template_id": template.template_id,
        })
        return 0

    if args.group == "mission" and args.mission_command == "web":
        from aedt_agent.agent.web import run_agent_window
        run_agent_window(host=args.host, port=args.port, db_path=args.db)
        return 0

    _print_json(
        {
            "command": f"{args.group}.{args.mission_command}",
            "message": "该 Mission 命令面已安装，但具体执行循环将在 BRD Worker 阶段启用。",
            "status": "runtime_command_not_enabled",
        }
    )
    return 2


def _runtime_with_workers(db_path: Path, profile=None) -> AgentRuntime:
    from aedt_agent.agent.workers import (
        BRD_CHANNEL_SCORE_CAPABILITY,
        BRD_LOCAL_CUT_BUILD_CAPABILITY,
        BRD_RECORDED_VOID_ACTION_CAPABILITY,
        BRD_REAL_SOLVE_CAPABILITY,
        InMemoryWorkerRegistry,
        run_brd_channel_score_worker,
        run_brd_local_cut_worker,
        run_brd_recorded_void_action_worker,
    )
    from aedt_agent.agent.policies import ExecutionProfile
    from aedt_agent.infrastructure.harness import (
        HarnessWorkspacePolicy,
        LocalProcessHarness,
        ResourceGate,
    )

    profile = profile or ExecutionProfile.safe_recorded()
    store = SQLiteMissionStore(db_path)
    harness_root = Path(profile.harness_root)
    if not harness_root.is_absolute():
        harness_root = db_path.parent / harness_root
    harness = LocalProcessHarness(
        HarnessWorkspacePolicy(harness_root),
        resource_gate=ResourceGate(
            max_concurrent_cpu=4,
            max_concurrent_aedt=profile.max_concurrent_aedt,
            max_concurrent_license_jobs=profile.max_concurrent_license_jobs,
        ),
        heartbeat_timeout_seconds=profile.heartbeat_timeout_seconds,
        termination_grace_seconds=profile.termination_grace_seconds,
    )
    registry = InMemoryWorkerRegistry(
        harness=harness,
        heartbeat_interval_seconds=profile.heartbeat_interval_seconds,
        default_allowed_env=tuple(profile.allowed_env),
        allow_real_aedt=profile.allow_real_aedt,
    )
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    registry.register(BRD_CHANNEL_SCORE_CAPABILITY, run_brd_channel_score_worker)
    registry.register(
        BRD_RECORDED_VOID_ACTION_CAPABILITY,
        lambda job, context: run_brd_recorded_void_action_worker(job, context, store=store),
    )
    registry.register_process(
        BRD_REAL_SOLVE_CAPABILITY,
        (
            "aedt_agent.agent.workers.brd_real_solve:"
            "run_brd_real_solve_worker"
        ),
        resource_classes=("license", "aedt"),
        requires_real_aedt=True,
        input_overrides={
            "aedt": {
                "version": profile.aedt_version,
                "non_graphical": profile.aedt_non_graphical,
            }
        },
    )
    return AgentRuntime(store, registry=registry)


def _runtime_with_harness(db_path: Path) -> AgentRuntime:
    return _runtime_with_workers(db_path)


def _load_execution_profile(value: str):
    from aedt_agent.agent.policies import ExecutionProfile

    if value == "safe-recorded":
        return ExecutionProfile.safe_recorded()
    path = Path(value)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return ExecutionProfile.from_json_dict(payload)


def _loop_exit_code(decision: str) -> int:
    if decision in {
        "failed",
        "budget_exhausted",
        "stopped_no_improvement",
        "stopped_duplicate_action",
    }:
        return 2
    return 0


def _graph_exit_code(status: str) -> int:
    return 2 if status in {"failed", "canceled"} else 0


def _require_recorded_action_args(args) -> None:
    required = {
        "before_touchstone": args.before_touchstone,
        "before_tdr": args.before_tdr,
        "after_touchstone": args.after_touchstone,
        "after_tdr": args.after_tdr,
        "action_layer": args.action_layer,
        "action_region": args.action_region,
        "action_variable": args.action_variable,
        "old_value_mil": args.old_value_mil,
        "new_value_mil": args.new_value_mil,
    }
    missing = [name for name, value in required.items() if value is None or value == ""]
    if missing:
        raise ValueError(f"missing recorded action arguments: {', '.join(missing)}")


def _require_real_solve_args(args) -> None:
    required = {
        "project": args.project,
        "tdr_expression": args.tdr_expression,
    }
    missing = [
        name
        for name, value in required.items()
        if value is None or value == ""
    ]
    if missing:
        raise ValueError(
            f"missing real solve arguments: {', '.join(missing)}"
        )
    project = Path(args.project)
    if project.suffix.casefold() != ".aedt" or not project.is_file():
        raise ValueError(
            f"project must be an existing .aedt file: {project}"
        )
    if args.solve_timeout_seconds <= 0:
        raise ValueError("solve_timeout_seconds must be positive")


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
