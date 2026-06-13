from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def test_pyproject_exposes_new_and_v0_console_scripts():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"]["aedt-agent"] == "aedt_agent.agent.cli:main"
    assert project["project"]["scripts"]["aedt-agent-v0"] == "aedt_agent.v0.cli:main"


def test_new_cli_exposes_mission_command_surface(tmp_path, capsys):
    from aedt_agent.agent.cli import run

    exit_code = run(["--db", str(tmp_path / "mission.db"), "mission", "create", "--goal", "mission-test"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["state"] == "created"
    assert payload["user_goal"] == "mission-test"


def test_root_cli_module_executes_agent_cli(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.cli",
            "--db",
            str(tmp_path / "mission.db"),
            "mission",
            "create",
            "--goal",
            "mission-test",
        ],
        check=False,
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["state"] == "created"


def test_root_cli_module_points_to_new_agent_cli():
    from aedt_agent import cli
    from aedt_agent.agent import cli as agent_cli

    assert cli.run is agent_cli.run
    assert cli.main is agent_cli.main
