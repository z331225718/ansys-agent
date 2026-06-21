from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_pi_agent_cli_preflight_outputs_json():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.pi_agent",
            "preflight",
            "--case",
            "config/cases/reviewed_brd.example.json",
            "--no-check-paths",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["execution_profile"]["simulation_runner"] == "local_cli"


def test_pi_agent_cli_status_handles_missing_database(tmp_path: Path):
    case_file = tmp_path / "case.json"
    case_file.write_text(
        json.dumps(
            {
                "case_id": "not-started",
                "db_path": str(tmp_path / "missing.db"),
                "loop_config": "config/optimization_loops/reviewed_brd_remote.example.json",
                "execution_profile": "config/execution_profiles/local_real_aedt.example.json",
                "max_workers": 1,
                "poll_interval_seconds": 30,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.pi_agent",
            "status",
            "--case",
            str(case_file),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_started"
