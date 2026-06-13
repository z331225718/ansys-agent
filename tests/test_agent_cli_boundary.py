from __future__ import annotations

import json
import tomllib
from pathlib import Path

from aedt_agent.agent.cli import run


def test_pyproject_exposes_new_and_v0_console_scripts():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"]["aedt-agent"] == "aedt_agent.agent.cli:main"
    assert project["project"]["scripts"]["aedt-agent-v0"] == "aedt_agent.v0.cli:main"


def test_new_cli_exposes_mission_command_surface(capsys):
    exit_code = run(["mission", "status", "--mission-id", "mission-test"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "command": "mission.status",
        "message": "Mission Runtime 尚未安装；当前版本只完成 Agent-First 架构迁移。",
        "status": "runtime_unavailable",
    }


def test_root_cli_module_points_to_new_agent_cli():
    from aedt_agent import cli
    from aedt_agent.agent import cli as agent_cli

    assert cli.run is agent_cli.run
    assert cli.main is agent_cli.main
