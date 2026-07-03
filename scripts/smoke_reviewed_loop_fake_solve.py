from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from aedt_agent.agent.loop_runner import run_loop_from_config
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.policies import ExecutionProfile
from aedt_agent.agent.workers import (
    BRD_CANDIDATE_INVENTORY_CAPABILITY,
    BRD_CHANNEL_SCORE_CAPABILITY,
    BRD_GEOMETRY_VALIDATE_CAPABILITY,
    BRD_ITERATION_QUALIFY_CAPABILITY,
    BRD_MODEL_EDIT_CAPABILITY,
    BRD_OPTIMIZATION_PROGRESS_CAPABILITY,
    BRD_OPTIMIZATION_REPORT_CAPABILITY,
    BRD_REAL_SOLVE_CAPABILITY,
    BRD_TDR_EXPORT_CAPABILITY,
    BRD_TOUCHSTONE_EXPORT_CAPABILITY,
    InMemoryWorkerRegistry,
    run_brd_channel_score_worker,
    run_brd_geometry_validate_worker,
    run_brd_iteration_qualify_worker,
    run_brd_optimization_progress_worker,
    run_brd_optimization_report_worker,
    run_brd_tdr_export_worker,
    run_brd_touchstone_export_worker,
)
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.infrastructure.harness import (
    HarnessWorkspacePolicy,
    LocalProcessHarness,
    ResourceGate,
)


DEFAULT_SOURCE_PROJECT = Path(
    r"C:\Users\z3312\code\Cadence-spb-sipi-toolbox"
    r"\brd\102-006060501_R01_0610-3-s19"
    r"\102-006060501_R01_0610-3-s19.aedt"
)
DEFAULT_RUN_ROOT = Path(".aedt-agent/reviewed-loop-fake-solve-smoke")
LLM_ENV_PREFIXES = ("AEDT_AGENT_LLM_",)
LLM_ENV_NAMES = {"OPENAI_API_KEY"}


def main() -> int:
    args = _parser().parse_args()
    source_project = args.source_project.resolve()
    run_root = args.run_root.resolve()
    if not source_project.is_file():
        raise FileNotFoundError(f"source project not found: {source_project}")
    if args.reset:
        _reset_run_root(run_root)
    elif run_root.exists() and any(run_root.iterdir()):
        raise FileExistsError(
            f"run root is not empty: {run_root}; pass --reset to reuse it"
        )
    run_root.mkdir(parents=True, exist_ok=True)
    if not args.use_llm:
        _disable_llm_for_smoke()

    inventory_path = run_root / "candidate_action_inventory.json"
    _write_inventory_seed(inventory_path)
    profile = _load_local_profile(args.profile)
    runtime = _build_runtime(run_root, profile)
    config = _loop_config(
        source_project=source_project,
        run_root=run_root,
        inventory_path=inventory_path,
    )
    report = run_loop_from_config(
        runtime,
        config,
        worker_id="fake-solve-loop-smoke",
        max_workers=1,
        poll_interval_seconds=5,
    )
    summary = _summary(report, run_root)
    summary_path = run_root / "smoke_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "succeeded" else 2


def run_fake_reviewed_solve(job, context) -> dict[str, Any]:
    payload = dict(job.input_payload)
    loop_context = dict(payload.get("loop_context") or {})
    round_index = int(loop_context.get("round_index") or 1)
    run_root = Path(
        str(
            loop_context.get("report_dir")
            or Path(str(payload["project_path"])).parent
        )
    ).parent
    artifact_dir = run_root / "fake_solve" / f"round-{round_index}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    touchstone = artifact_dir / "channel.s4p"
    tdr = artifact_dir / "ChannelTDR.csv"
    manifest = artifact_dir / "solve_manifest.json"

    if round_index == 1:
        _write_s4p(touchstone, reflection_magnitude=0.40)
        _write_tdr(tdr, impedances=(90.0, 84.0, 88.0, 90.0))
        scenario = "first_round_not_passed"
    else:
        _write_s4p(touchstone, reflection_magnitude=0.05)
        _write_tdr(tdr, impedances=(90.0, 91.0, 90.0, 90.0))
        scenario = "second_round_passed"

    project_path = Path(str(payload["project_path"]))
    manifest_payload = {
        "version": 1,
        "capability": BRD_REAL_SOLVE_CAPABILITY,
        "summary": {
            "status": "succeeded",
            "adapter": "fake_reviewed_round_middleware",
            "round_index": round_index,
            "scenario": scenario,
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
        },
        "outputs": {
            "solved_project": _artifact_record(project_path),
            "touchstone": _artifact_record(touchstone),
            "tdr": _artifact_record(tdr),
        },
    }
    manifest.write_text(
        json.dumps(
            manifest_payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _append_unique(loop_context, "solve_manifest_paths", str(manifest))
    loop_context["last_solve_manifest_path"] = str(manifest)
    loop_context["last_solved_project_path"] = str(project_path)
    refs = [str(project_path), str(touchstone), str(tdr), str(manifest)]
    return {
        **payload,
        "status": "succeeded",
        "project_path": str(project_path),
        "solved_project": str(project_path),
        "touchstone_path": str(touchstone),
        "tdr_path": str(tdr),
        "solve_manifest": str(manifest),
        "artifact_dir": str(artifact_dir),
        "loop_context": loop_context,
        "solve_summary": manifest_payload["summary"],
        "evidence_summary": {
            **manifest_payload["summary"],
            "artifact_refs": refs,
        },
        "artifact_refs": refs,
    }


def _build_runtime(
    run_root: Path,
    profile: ExecutionProfile,
) -> AgentRuntime:
    store = SQLiteMissionStore(run_root / "missions.db")
    harness = LocalProcessHarness(
        HarnessWorkspacePolicy(run_root / "harness"),
        resource_gate=ResourceGate(
            max_concurrent_cpu=2,
            max_concurrent_aedt=1,
            max_concurrent_license_jobs=1,
        ),
        heartbeat_timeout_seconds=max(
            profile.heartbeat_timeout_seconds,
            600,
        ),
        termination_grace_seconds=profile.termination_grace_seconds,
    )
    registry = InMemoryWorkerRegistry(
        harness=harness,
        heartbeat_interval_seconds=profile.heartbeat_interval_seconds,
        default_allowed_env=tuple(profile.allowed_env),
        allow_real_aedt=True,
    )
    registry.register(BRD_REAL_SOLVE_CAPABILITY, run_fake_reviewed_solve)
    registry.register(
        BRD_TOUCHSTONE_EXPORT_CAPABILITY,
        run_brd_touchstone_export_worker,
    )
    registry.register(BRD_TDR_EXPORT_CAPABILITY, run_brd_tdr_export_worker)
    registry.register(BRD_CHANNEL_SCORE_CAPABILITY, run_brd_channel_score_worker)
    registry.register(
        BRD_ITERATION_QUALIFY_CAPABILITY,
        run_brd_iteration_qualify_worker,
    )
    registry.register(
        BRD_GEOMETRY_VALIDATE_CAPABILITY,
        run_brd_geometry_validate_worker,
    )
    registry.register(
        BRD_OPTIMIZATION_PROGRESS_CAPABILITY,
        run_brd_optimization_progress_worker,
    )
    registry.register(
        BRD_OPTIMIZATION_REPORT_CAPABILITY,
        run_brd_optimization_report_worker,
    )
    aedt_overrides = {
        "aedt": {
            "version": profile.aedt_version,
            "non_graphical": profile.aedt_non_graphical,
        }
    }
    registry.register_process(
        BRD_CANDIDATE_INVENTORY_CAPABILITY,
        (
            "aedt_agent.agent.workers.brd_candidate_inventory:"
            "run_brd_candidate_inventory_worker"
        ),
        resource_classes=("license", "aedt"),
        requires_real_aedt=True,
        input_overrides=aedt_overrides,
    )
    registry.register_process(
        BRD_MODEL_EDIT_CAPABILITY,
        "aedt_agent.agent.workers.brd_model_edit:run_brd_model_edit_worker",
        resource_classes=("license", "aedt"),
        requires_real_aedt=True,
        input_overrides=aedt_overrides,
    )
    return AgentRuntime(
        store,
        registry=registry,
        default_job_timeout_seconds=1800,
    )


def _loop_config(
    *,
    source_project: Path,
    run_root: Path,
    inventory_path: Path,
) -> dict[str, Any]:
    return {
        "goal": (
            "Smoke the reviewed BRD optimization loop with a failed fake "
            "first round, real model edit, and passing fake second round."
        ),
        "template_id": "brd_reviewed_model_optimize_loop",
        "run_root": str(run_root),
        "source_project_path": str(source_project),
        "working_project_path": str(
            run_root / "working" / source_project.name
        ),
        "reset_working_project": True,
        "report_dir": str(run_root / "optimization_progress"),
        "max_rounds": 2,
        "max_steps": 64,
        "poll_interval_seconds": 5,
        "setup_name": "Setup1",
        "sweep_name": "Sweep1",
        "tdr_expression": "TDRZ(Diff1)",
        "tdr_report_name": "ChannelTDR",
        "tdr_observation_port": "Diff1",
        "tdr_differential_pairs": True,
        "tdr_port_orientation_evidence": (
            "Diff1 is treated as the reviewed solder-ball-side observation "
            "port for this smoke only."
        ),
        "expected_port_count": 4,
        "touchstone_name": "channel.s4p",
        "sparameter_mode": "differential",
        "run_analyze": True,
        "export_tdr": True,
        "frequency_start_ghz": 0.0,
        "frequency_stop_ghz": 28.0,
        "rl_target_db": -17.0,
        "tdr_target_ohm": 90.0,
        "tdr_tolerance_ohm": 9.0,
        "geometry_constraints": {
            "anti_pad": {"max_radius_mil": 22},
            "non_functional_pad": {
                "min_radius_mil": 7.875,
                "max_radius_mil": 10,
            },
        },
        "candidate_action_inventory_path": str(inventory_path),
        "candidate_actions": [],
    }


def _write_inventory_seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": "fake_solve_smoke_scope_seed",
                "tdr_observation_port": "Diff1",
                "tdr_port_orientation_evidence": (
                    "Diff1 is treated as the reviewed solder-ball-side "
                    "observation port for this smoke only."
                ),
                "anti_pad_shape_layers": ["L2_GND"],
                "non_functional_pad_layers": [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_s4p(path: Path, *, reflection_magnitude: float) -> None:
    rows = []
    for frequency, transmission in ((1.0, 0.90), (14.0, 0.82), (28.0, 0.75)):
        values = [
            reflection_magnitude, 0, 0.02, 0, 0, 0, 0, 0,
            0.02, 0, reflection_magnitude, 0, 0, 0, 0, 0,
            transmission, 0, 0, 0, 0.02, 0, 0, 0,
            0, 0, transmission, 0, 0, 0, 0.02, 0,
        ]
        rows.append(
            f"{frequency:g} " + " ".join(f"{value:g}" for value in values)
        )
    path.write_text(
        "# GHz S MA R 50\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def _write_tdr(path: Path, *, impedances: tuple[float, ...]) -> None:
    rows = ["time_ps,impedance_ohm"]
    rows.extend(
        f"{index * 10},{impedance:g}"
        for index, impedance in enumerate(impedances)
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _artifact_record(path: Path) -> dict[str, Any]:
    if path.is_dir():
        raise ValueError(f"fake solve artifact must be a file: {path}")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _append_unique(payload: dict[str, Any], key: str, value: str) -> None:
    values = list(payload.get(key) or [])
    if value and value not in values:
        values.append(value)
    payload[key] = values


def _load_local_profile(path: Path) -> ExecutionProfile:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return ExecutionProfile.from_json_dict(payload)


def _disable_llm_for_smoke() -> None:
    for name in list(os.environ):
        if name in LLM_ENV_NAMES or name.startswith(LLM_ENV_PREFIXES):
            os.environ.pop(name, None)


def _reset_run_root(run_root: Path) -> None:
    resolved = run_root.resolve()
    workspace = Path.cwd().resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            f"refusing to reset run root outside workspace: {resolved}"
        ) from exc
    if resolved.exists():
        shutil.rmtree(resolved)


def _summary(report: dict[str, Any], run_root: Path) -> dict[str, Any]:
    node_runs = list(report.get("node_runs") or [])
    compact_runs = [
        {
            "node_id": item.get("node_id"),
            "sequence": item.get("sequence"),
            "status": item.get("status"),
            "edge_decision": item.get("edge_decision"),
            "error": item.get("error"),
        }
        for item in node_runs
    ]
    model_edits = [
        item for item in node_runs
        if item.get("node_id") == "model_edit_worker"
    ]
    score_runs = [
        item for item in node_runs
        if item.get("node_id") == "channel_score_worker"
    ]
    return {
        "status": report.get("status"),
        "mission_id": report.get("mission_id"),
        "graph_run_id": report.get("graph_run_id"),
        "run_root": str(run_root),
        "node_runs": compact_runs,
        "round_scores": [
            {
                "status": (item.get("output_payload") or {})
                .get("score", {})
                .get("status"),
                "rl_worst_db": (item.get("output_payload") or {})
                .get("score", {})
                .get("rl_worst_db"),
                "tdr_peak_deviation_ohm": (item.get("output_payload") or {})
                .get("score", {})
                .get("tdr_peak_deviation_ohm"),
            }
            for item in score_runs
        ],
        "model_edit_manifests": [
            (item.get("output_payload") or {}).get("model_edit_manifest")
            for item in model_edits
        ],
        "optimization_history_csv": str(
            run_root / "optimization_progress" / "optimization_history.csv"
        ),
        "optimization_report_html": str(
            run_root / "optimization_progress" / "optimization_progress.html"
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the reviewed BRD loop with fake round solve artifacts, real "
            "scoring, real geometry validation, and real model editing."
        )
    )
    parser.add_argument(
        "--source-project",
        type=Path,
        default=DEFAULT_SOURCE_PROJECT,
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=DEFAULT_RUN_ROOT,
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("config/execution_profiles/local_real_aedt.example.json"),
    )
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--use-llm", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
