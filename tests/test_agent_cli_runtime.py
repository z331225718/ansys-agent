from __future__ import annotations

import json
import subprocess
import sys
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
