from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aedt_agent.agent.cli", "--db", str(tmp_path / "mission.db"), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_creates_and_reads_restartable_mission(tmp_path):
    created = _run_cli(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "构建 local cut",
        "--criterion",
        "s21_db_at_56g>=-8",
    )

    assert created.returncode == 0
    created_payload = json.loads(created.stdout)
    mission_id = created_payload["mission_id"]
    assert created_payload["state"] == "created"

    status = _run_cli(tmp_path, "mission", "status", "--mission-id", mission_id)

    assert status.returncode == 0
    status_payload = json.loads(status.stdout)
    assert status_payload["mission_id"] == mission_id
    assert status_payload["user_goal"] == "构建 local cut"


def test_cli_cancel_changes_state_and_audits_event(tmp_path):
    created = _run_cli(tmp_path, "mission", "create", "--goal", "goal")
    mission_id = json.loads(created.stdout)["mission_id"]

    canceled = _run_cli(tmp_path, "mission", "cancel", "--mission-id", mission_id)

    assert canceled.returncode == 0
    assert json.loads(canceled.stdout)["state"] == "canceled"


def test_runtime_with_workers_uses_ssh_runner_from_profile(tmp_path):
    from aedt_agent.agent.cli import _runtime_with_workers
    from aedt_agent.agent.policies import ExecutionProfile
    from aedt_agent.agent.workers.simulation_runner import SshCliRunner

    profile = replace(
        ExecutionProfile.safe_recorded(),
        simulation_runner="ssh_remote",
        ssh_host="192.168.71.51",
        ssh_user="z3312",
        ssh_identity_file=r"C:\Users\z3312\.ssh\ansys_agent_ed25519",
        ssh_remote_root=r"D:\aedt-agent-runs",
        ssh_python="python",
        ssh_repo_root=r"D:\ansys-agent",
    )

    runtime = _runtime_with_workers(tmp_path / "mission.db", profile=profile)

    runner = runtime.registry.process_runner
    assert isinstance(runner, SshCliRunner)
    assert runner.config.host == "192.168.71.51"
    assert runner.config.user == "z3312"
    assert runner.config.remote_root == r"D:\aedt-agent-runs"


def test_runtime_registers_model_edit_process_worker(tmp_path):
    from aedt_agent.agent.cli import _runtime_with_workers
    from aedt_agent.agent.workers import (
        BRD_CHANNEL_SCORE_CAPABILITY,
        BRD_MODEL_EDIT_CAPABILITY,
        BRD_TDR_EXPORT_CAPABILITY,
        BRD_TOUCHSTONE_EXPORT_CAPABILITY,
    )

    runtime = _runtime_with_workers(tmp_path / "mission.db")
    registration = runtime.registry._registrations[BRD_MODEL_EDIT_CAPABILITY]

    assert registration.execution_mode == "local_process"
    assert registration.requires_real_aedt is True
    assert registration.resource_classes == ("license", "aedt")
    assert (
        runtime.registry._registrations[
            BRD_CHANNEL_SCORE_CAPABILITY
        ].execution_mode
        == "local_process"
    )
    assert (
        runtime.registry._registrations[
            BRD_TOUCHSTONE_EXPORT_CAPABILITY
        ].execution_mode
        == "local_process"
    )
    assert (
        runtime.registry._registrations[
            BRD_TDR_EXPORT_CAPABILITY
        ].execution_mode
        == "local_process"
    )
