from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_extras_pin_current_ansys_clients_with_dotnet_backend() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    assert "pyaedt==1.3.0" in extras["aedt"]
    assert "pyedb[dotnet]==0.80.2" in extras["aedt"]
    assert "pyaedt==1.3.0" in extras["desktop"]
    assert "pyedb[dotnet]==0.80.2" in extras["desktop"]


def test_online_upgrade_script_refreshes_runtime_and_api_memory() -> None:
    script = (ROOT / "scripts" / "online" / "Update-AnsysAgentDependencies.ps1").read_text(
        encoding="utf-8"
    )

    assert '$expectedPyAedt = "1.3.0"' in script
    assert '$expectedPyEdb = "0.80.2"' in script
    assert '"--editable", $editableTarget' in script
    assert '"-m", "pip", "check"' in script
    assert '"aedt_agent.knowledge.api_memory_cli", "prepare", "--force"' in script
    assert "Remove-SafeTemporaryFile" in script
