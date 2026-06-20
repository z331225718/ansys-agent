from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.agent.workers.simulation_runner import (  # noqa: E402
    OpenSshTransport,
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
        description="Run brd.channel.score through the SSH simulation runner."
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
    parser.add_argument("--run-id", default="channel-score-smoke")
    parser.add_argument("--frequency-stop-ghz", type=float, default=67.0)
    parser.add_argument("--bucket-count", type=int, default=8)
    parser.add_argument(
        "--keep-local-temp",
        action="store_true",
        help="Keep the generated local smoke files and fetched evidence.",
    )
    args = parser.parse_args()

    local_tmp = Path(tempfile.mkdtemp(prefix="ansys_agent_channel_score_"))
    try:
        touchstone, tdr = _write_fixture_files(local_tmp)
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
        remote_input_dir = _remote_join(args.remote_root, args.run_id, "input")
        remote_artifact_dir = _remote_join(args.remote_root, args.run_id, "score-artifacts")
        remote_touchstone = _remote_join(remote_input_dir, "channel.s2p")
        remote_tdr = _remote_join(remote_input_dir, "tdr.csv")

        transport = OpenSshTransport(config)
        transport.mkdir(remote_input_dir)
        transport.mkdir(remote_artifact_dir)
        transport.upload(touchstone, remote_touchstone)
        transport.upload(tdr, remote_tdr)

        local_workspace_root = local_tmp / "harness"
        local_workspace = (
            local_workspace_root
            / "mission-channel-smoke"
            / "score-job"
            / "attempt-1"
        )
        request = HarnessRequest.create(
            harness_run_id=f"run-{args.run_id}",
            mission_id="mission-channel-smoke",
            job_id="score-job",
            attempt_id="attempt-1",
            worker_id="remote-worker-1",
            capability="brd.channel.score",
            entrypoint=(
                "aedt_agent.agent.workers.brd_channel_score:"
                "run_brd_channel_score_worker"
            ),
            timeout_seconds=60,
            heartbeat_interval_seconds=1,
            input_payload={
                "touchstone_path": remote_touchstone,
                "tdr_path": remote_tdr,
                "artifact_dir": remote_artifact_dir,
                "frequency_start_ghz": 0.0,
                "frequency_stop_ghz": args.frequency_stop_ghz,
                "rl_target_db": -20.0,
                "tdr_target_ohm": 100.0,
                "bucket_count": args.bucket_count,
            },
            workspace=str(local_workspace),
        )

        os.environ["PYTHONPATH"] = "src"
        runner = SshCliRunner(HarnessWorkspacePolicy(local_workspace_root), config)
        result = runner.submit(
            request,
            allowed_env=("PYTHONPATH",),
            resource_classes=("cpu",),
            cancel_requested=None,
        )
        fetched_evidence = None
        evidence_ref = result.output_payload.get("evidence_artifact")
        if result.status == HarnessStatus.SUCCEEDED and isinstance(evidence_ref, str):
            fetched_evidence = local_tmp / "fetched_brd_channel_score_evidence.json"
            runner.fetch(evidence_ref, fetched_evidence)

        report = _build_report(result, fetched_evidence, local_tmp)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if result.status != HarnessStatus.SUCCEEDED:
            raise SystemExit(1)
    finally:
        if args.keep_local_temp:
            print(f"local temp kept: {local_tmp}", file=sys.stderr)
        else:
            shutil.rmtree(local_tmp, ignore_errors=True)


def _write_fixture_files(root: Path) -> tuple[Path, Path]:
    touchstone = root / "channel.s2p"
    touchstone.write_text(
        "\n".join(
            [
                "# GHz S MA R 50",
                "0.00 0.05 0 0.90 0 0.90 0 0.05 0",
                "18.00 0.45 0 0.80 0 0.80 0 0.05 0",
                "67.00 0.04 0 0.70 0 0.70 0 0.04 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    tdr = root / "tdr.csv"
    tdr.write_text(
        "time_ps,impedance_ohm\n0,100\n10,104\n20,111\n30,101\n",
        encoding="utf-8",
    )
    return touchstone, tdr


def _build_report(result, fetched_evidence: Path | None, local_tmp: Path) -> dict:
    output = result.output_payload
    score = output.get("score", {})
    summary = output.get("evidence_summary", {})
    report = {
        "harness_status": result.status.value,
        "exit_code": result.exit_code,
        "worker_status": output.get("status"),
        "score_status": score.get("status"),
        "rl_worst_db": score.get("rl_worst_db"),
        "rl_worst_frequency_ghz": score.get("rl_worst_frequency_ghz"),
        "tdr_peak_deviation_ohm": score.get("tdr_peak_deviation_ohm"),
        "raw_sparameters": summary.get("raw_sparameters"),
        "raw_tdr": summary.get("raw_tdr"),
        "remote_artifact_refs": result.artifact_refs,
        "fetched_evidence": str(fetched_evidence) if fetched_evidence else "",
        "local_temp": str(local_tmp),
    }
    if result.error is not None:
        report["error"] = result.error.to_json_dict()
    return report


def _remote_join(root: str, *parts: str) -> str:
    separator = "\\" if "\\" in root or ":" in root else "/"
    value = root.rstrip("\\/")
    for part in parts:
        value = f"{value}{separator}{str(part).strip('\\/')}"
    return value


if __name__ == "__main__":
    main()
