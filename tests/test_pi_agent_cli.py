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


def test_pi_agent_cli_once_status_is_human_readable(tmp_path: Path):
    case_file = tmp_path / "case.json"
    case_file.write_text(
        json.dumps(
            {
                "case_id": "chat-case",
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
            "cli",
            "--case",
            str(case_file),
            "--once",
            "看状态",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "状态：not_started" in result.stdout
    assert "建议命令：" in result.stdout


def test_pi_agent_cli_init_outputs_created_files(tmp_path: Path):
    profile = tmp_path / "local_real_aedt.example.json"
    profile.write_text(
        Path("config/execution_profiles/local_real_aedt.example.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    loop = tmp_path / "reviewed_brd_remote.example.json"
    loop.write_text(
        Path("config/optimization_loops/reviewed_brd_remote.example.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    case_file = tmp_path / "reviewed_brd.example.json"
    case_file.write_text(
        json.dumps(
            {
                "case_id": "cli-init",
                "db_path": str(tmp_path / "missions.db"),
                "loop_config": str(loop),
                "execution_profile": str(profile),
                "max_workers": 1,
                "poll_interval_seconds": 30,
                "check_paths": False,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.pi_agent",
            "init",
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
    assert payload["status"] == "initialized"
    assert (tmp_path / "reviewed_brd.local.json").is_file()
