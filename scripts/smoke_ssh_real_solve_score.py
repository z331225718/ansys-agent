from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.agent.workers.simulation_runner import (  # noqa: E402
    SshCliRunner,
    SshCliRunnerConfig,
)
from aedt_agent.infrastructure.harness import (  # noqa: E402
    HarnessRequest,
    HarnessStatus,
    HarnessWorkspacePolicy,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a real brd.local_cut.solve job over SSH, then score its "
            "Touchstone/TDR artifacts with brd.channel.score."
        )
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument(
        "--identity-file",
        default=str(Path.home() / ".ssh" / "ansys_agent_ed25519"),
    )
    parser.add_argument("--remote-root", default=r"D:\aedt-agent-runs")
    parser.add_argument("--remote-repo", default=r"D:\ansys-agent")
    parser.add_argument("--remote-python", default="python")
    parser.add_argument("--ssh-exe", default="ssh")
    parser.add_argument("--scp-exe", default="scp")
    parser.add_argument("--run-id", default="real-solve-score-smoke")
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--setup-name", default="Setup1")
    parser.add_argument("--sweep-name", default="Sweep1")
    parser.add_argument("--tdr-expression", default="TDRZt(Diff1,Diff1)")
    parser.add_argument("--tdr-observation-port", default="Diff1")
    parser.add_argument("--expected-port-count", type=int, default=4)
    parser.add_argument("--touchstone-name", default="")
    parser.add_argument("--sparameter-mode", default="differential")
    parser.add_argument(
        "--project-copy-mode",
        choices=("checkpoint_copy", "working_project"),
        default="working_project",
        help=(
            "Use working_project for the reviewed remote copy so repeated "
            "solve/edit iterations do not create new AEDT project bundles."
        ),
    )
    parser.add_argument("--aedt-version", default="2026.1")
    parser.add_argument("--frequency-start-ghz", type=float, default=0.0)
    parser.add_argument("--frequency-stop-ghz", type=float, default=67.0)
    parser.add_argument("--rl-target-db", type=float, default=-20.0)
    parser.add_argument("--tdr-target-ohm", type=float, default=100.0)
    parser.add_argument("--tdr-tolerance-ohm", type=float, default=5.0)
    parser.add_argument("--bucket-count", type=int, default=128)
    parser.add_argument("--solve-timeout-seconds", type=int, default=1800)
    parser.add_argument("--score-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--non-graphical",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--skip-analyze",
        action="store_true",
        help="Skip analyze_setup and export from existing AEDT results.",
    )
    parser.add_argument(
        "--keep-local-temp",
        action="store_true",
        help="Keep fetched manifest/evidence and local harness request files.",
    )
    args = parser.parse_args()

    local_tmp = Path(tempfile.mkdtemp(prefix="ansys_agent_real_solve_score_"))
    try:
        config = SshCliRunnerConfig(
            host=args.host,
            user=args.user,
            identity_file=args.identity_file,
            remote_root=args.remote_root,
            python=args.remote_python,
            repo_root=args.remote_repo,
            ssh_exe=args.ssh_exe,
            scp_exe=args.scp_exe,
        )
        os.environ.setdefault("PYTHONPATH", "src")
        runner = SshCliRunner(
            HarnessWorkspacePolicy(local_tmp / "harness"),
            config,
        )

        solve = _submit_solve(runner, args, local_tmp)
        fetched_solve_manifest = None
        if solve.status == HarnessStatus.SUCCEEDED:
            solve_manifest = solve.output_payload.get("solve_manifest")
            if isinstance(solve_manifest, str) and solve_manifest:
                fetched_solve_manifest = (
                    local_tmp / "fetched_solve_manifest.json"
                )
                runner.fetch(solve_manifest, fetched_solve_manifest)

        score = None
        fetched_score_evidence = None
        if solve.status == HarnessStatus.SUCCEEDED:
            score = _submit_score(runner, args, solve, local_tmp)
            evidence_artifact = score.output_payload.get("evidence_artifact")
            if (
                score.status == HarnessStatus.SUCCEEDED
                and isinstance(evidence_artifact, str)
                and evidence_artifact
            ):
                fetched_score_evidence = (
                    local_tmp / "fetched_brd_channel_score_evidence.json"
                )
                runner.fetch(evidence_artifact, fetched_score_evidence)

        report = _build_report(
            solve=solve,
            score=score,
            fetched_solve_manifest=fetched_solve_manifest,
            fetched_score_evidence=fetched_score_evidence,
            local_tmp=local_tmp,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if solve.status != HarnessStatus.SUCCEEDED or (
            score is not None and score.status != HarnessStatus.SUCCEEDED
        ):
            raise SystemExit(1)
    finally:
        if args.keep_local_temp:
            print(f"local temp kept: {local_tmp}", file=sys.stderr)
        else:
            shutil.rmtree(local_tmp, ignore_errors=True)


def _submit_solve(
    runner: SshCliRunner,
    args: argparse.Namespace,
    local_tmp: Path,
):
    request = HarnessRequest.create(
        harness_run_id=f"run-{args.run_id}-solve",
        mission_id=f"mission-{args.run_id}",
        job_id="real-solve-job",
        attempt_id="attempt-1",
        worker_id="remote-worker-1",
        capability="brd.local_cut.solve",
        entrypoint=(
            "aedt_agent.agent.workers.brd_real_solve:"
            "run_brd_real_solve_worker"
        ),
        timeout_seconds=args.solve_timeout_seconds,
        heartbeat_interval_seconds=180,
        input_payload={
            "project_path": args.project_path,
            "setup_name": args.setup_name,
            "sweep_name": args.sweep_name,
            "solution_name": f"{args.setup_name} : {args.sweep_name}",
            "touchstone_name": args.touchstone_name
            or f"channel.s{args.expected_port_count}p",
            "tdr_report_name": "AgentTDR",
            "tdr_expression": args.tdr_expression,
            "expected_port_count": args.expected_port_count,
            "frequency_start_ghz": args.frequency_start_ghz,
            "frequency_stop_ghz": args.frequency_stop_ghz,
            "rl_target_db": args.rl_target_db,
            "tdr_target_ohm": args.tdr_target_ohm,
            "tdr_tolerance_ohm": args.tdr_tolerance_ohm,
            "run_analyze": not args.skip_analyze,
            "tdr_differential_pairs": args.sparameter_mode
            in {"auto", "differential", "diff", "mixed_mode"},
            "tdr_observation_port": args.tdr_observation_port,
            "project_copy_mode": args.project_copy_mode,
            "aedt": {
                "version": args.aedt_version,
                "non_graphical": args.non_graphical,
            },
        },
        workspace=str(
            local_tmp
            / "harness"
            / f"mission-{args.run_id}"
            / "real-solve-job"
            / "attempt-1"
        ),
    )
    return runner.submit(
        request,
        allowed_env=(
            "PYTHONPATH",
            "AWP_ROOT261",
            "ANSYSEM_ROOT261",
            "LM_LICENSE_FILE",
            "ANSYSLMD_LICENSE_FILE",
            "CDSROOT",
            "CDS_LIC_FILE",
        ),
        resource_classes=("license", "aedt"),
        cancel_requested=None,
    )


def _submit_score(
    runner: SshCliRunner,
    args: argparse.Namespace,
    solve,
    local_tmp: Path,
):
    solve_output = solve.output_payload
    artifact_dir = _remote_join(
        args.remote_root,
        f"mission-{args.run_id}",
        "channel-score-job",
        "score-artifacts",
    )
    request = HarnessRequest.create(
        harness_run_id=f"run-{args.run_id}-score",
        mission_id=f"mission-{args.run_id}",
        job_id="channel-score-job",
        attempt_id="attempt-1",
        worker_id="remote-worker-1",
        capability="brd.channel.score",
        entrypoint=(
            "aedt_agent.agent.workers.brd_channel_score:"
            "run_brd_channel_score_worker"
        ),
        timeout_seconds=args.score_timeout_seconds,
        heartbeat_interval_seconds=30,
        input_payload={
            "touchstone_path": str(solve_output["touchstone_path"]),
            "tdr_path": str(solve_output["tdr_path"]),
            "artifact_dir": artifact_dir,
            "frequency_start_ghz": args.frequency_start_ghz,
            "frequency_stop_ghz": args.frequency_stop_ghz,
            "rl_target_db": args.rl_target_db,
            "tdr_target_ohm": args.tdr_target_ohm,
            "tdr_tolerance_ohm": args.tdr_tolerance_ohm,
            "sparameter_mode": args.sparameter_mode,
            "tdr_observation_port": args.tdr_observation_port,
            "bucket_count": args.bucket_count,
        },
        workspace=str(
            local_tmp
            / "harness"
            / f"mission-{args.run_id}"
            / "channel-score-job"
            / "attempt-1"
        ),
    )
    return runner.submit(
        request,
        allowed_env=("PYTHONPATH",),
        resource_classes=("cpu",),
        cancel_requested=None,
    )


def _build_report(
    *,
    solve,
    score,
    fetched_solve_manifest: Path | None,
    fetched_score_evidence: Path | None,
    local_tmp: Path,
) -> dict[str, Any]:
    solve_output = solve.output_payload
    score_output = score.output_payload if score is not None else {}
    score_payload = score_output.get("score", {})
    score_summary = score_output.get("evidence_summary", {})
    report = {
        "solve_harness_status": solve.status.value,
        "solve_exit_code": solve.exit_code,
        "solve_worker_status": solve_output.get("status"),
        "touchstone_path": solve_output.get("touchstone_path"),
        "tdr_path": solve_output.get("tdr_path"),
        "solve_manifest": solve_output.get("solve_manifest"),
        "solve_raw_sparameters": solve_output.get(
            "evidence_summary",
            {},
        ).get("raw_sparameters"),
        "solve_raw_tdr": solve_output.get("evidence_summary", {}).get(
            "raw_tdr"
        ),
        "score_harness_status": score.status.value if score else "",
        "score_exit_code": score.exit_code if score else None,
        "score_worker_status": score_output.get("status"),
        "score_status": score_payload.get("status"),
        "rl_worst_db": score_payload.get("rl_worst_db"),
        "rl_worst_frequency_ghz": score_payload.get(
            "rl_worst_frequency_ghz"
        ),
        "tdr_peak_deviation_ohm": score_payload.get(
            "tdr_peak_deviation_ohm"
        ),
        "tdr_tolerance_ohm": score_payload.get("tdr_tolerance_ohm"),
        "tdr_observation_port": score_payload.get("tdr_observation_port"),
        "return_loss_trace": score_payload.get("return_loss_trace"),
        "insertion_loss_trace": score_payload.get("insertion_loss_trace"),
        "insertion_worst_db_in_band": score_payload.get(
            "insertion_worst_db_in_band"
        ),
        "plot_artifacts": score_payload.get("plot_artifacts", {}),
        "score_raw_sparameters": score_summary.get("raw_sparameters"),
        "score_raw_tdr": score_summary.get("raw_tdr"),
        "fetched_solve_manifest": (
            str(fetched_solve_manifest) if fetched_solve_manifest else ""
        ),
        "fetched_score_evidence": (
            str(fetched_score_evidence) if fetched_score_evidence else ""
        ),
        "local_temp": str(local_tmp),
    }
    if solve.error is not None:
        report["solve_error"] = solve.error.to_json_dict()
    if score is not None and score.error is not None:
        report["score_error"] = score.error.to_json_dict()
    return report


def _remote_join(root: str, *parts: str) -> str:
    separator = "\\" if "\\" in root or ":" in root else "/"
    value = root.rstrip("\\/")
    for part in parts:
        value = f"{value}{separator}{str(part).strip('\\/')}"
    return value


if __name__ == "__main__":
    main()
