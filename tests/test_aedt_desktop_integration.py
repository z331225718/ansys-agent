from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

import pytest

from aedt_agent.desktop.installer import install_extension
from aedt_agent.desktop.installer import uninstall_extension
from aedt_agent.desktop.approval_client import DesktopApprovalClient
from aedt_agent.desktop.approval_host import ApprovalHost
from aedt_agent.desktop.approval_host import DesktopApprovalStore
from aedt_agent.desktop.launcher import AedtDesktopContext
from aedt_agent.desktop.launcher import ClaudeDesktopLauncher


def _project(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "ansys-agent"
    (root / "src" / "aedt_agent").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='aedt-agent'\n", encoding="ascii")
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    claude = tmp_path / "claude.exe"
    claude.write_bytes(b"")
    git_bash = tmp_path / "Git" / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.write_bytes(b"")
    return root, python, claude, git_bash


class _PreparingApiMemory:
    def __init__(self) -> None:
        self.prepared = False
        self.calls = []

    def status(self):
        self.calls.append("status")
        if not self.prepared:
            return {"status": "missing", "ready": False}
        return {
            "status": "ready",
            "ready": True,
            "manifest": {
                "manifest_digest": "manifest-abc123",
                "backend": {"name": "codebase-memory-mcp", "version": "0.9.0"},
                "packages": [
                    {
                        "key": "pyaedt",
                        "distribution": "pyaedt",
                        "version": "1.0.1",
                        "source_digest": "source-pyaedt",
                        "project": "ansys-pyaedt-test",
                    },
                    {
                        "key": "pyedb",
                        "distribution": "pyedb",
                        "version": "0.77.0",
                        "source_digest": "source-pyedb",
                        "project": "ansys-pyedb-test",
                    },
                ],
            },
        }

    def prepare(self):
        self.calls.append("prepare")
        self.prepared = True
        return {"status": "ready"}


def test_launcher_generates_session_scoped_mcp_and_visible_git_bash(
    tmp_path: Path,
    monkeypatch,
):
    root, python, claude, git_bash = _project(tmp_path)
    claude_config = tmp_path / "claude-config"
    claude_config.mkdir()
    (claude_config / "settings.json").write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "https://gateway.example.invalid",
                    "ANTHROPIC_MODEL": "deepseek-v4-flash",
                    "ANTHROPIC_AUTH_TOKEN": "settings-only-secret-token",
                    "API_TIMEOUT_MS": "3000000",
                    "UNRELATED_SETTING": "must-not-be-copied",
                },
                "hooks": {"PreToolUse": [{"command": "must-not-run"}]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config))
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_AUTH_TOKEN", "API_TIMEOUT_MS"):
        monkeypatch.delenv(name, raising=False)
    processes = []
    context = AedtDesktopContext(
        port=50061,
        version="2026.1",
        pid=42,
        project_name="Board Project",
        design_name="Layout1",
        design_type="HFSS 3D Layout Design",
    )
    api_memory = _PreparingApiMemory()
    launcher = ClaudeDesktopLauncher(
        project_root=root,
        python_executable=python,
        claude_executable=claude,
        git_bash_executable=git_bash,
        context_loader=lambda port, version: context,
        process_factory=lambda command, **kwargs: processes.append((command, kwargs)) or SimpleNamespace(pid=99),
        api_memory_factory=lambda: api_memory,
    )

    result = launcher.launch(port=50061)

    config = json.loads(Path(result["mcp_config"]).read_text(encoding="utf-8"))
    server = config["mcpServers"]["ansys-assistant"]
    assert server["command"] == str(python.resolve())
    assert server["args"] == ["-m", "aedt_agent.interactive.server"]
    assert server["env"]["AEDT_AGENT_EXPECTED_PORT"] == "50061"
    assert server["env"]["AEDT_AGENT_EXPECTED_PROJECT"] == "Board Project"
    assert server["env"]["AEDT_AGENT_EXPECTED_DESIGN"] == "Layout1"
    assert server["env"]["AEDT_AGENT_EXPECTED_VERSION"] == "2026.1"
    assert server["env"]["AEDT_AGENT_DESKTOP_STRICT"] == "1"
    assert server["env"]["AEDT_AGENT_APPROVAL_KEY"] == "${AEDT_AGENT_APPROVAL_KEY}"
    assert server["env"]["AEDT_AGENT_APPROVAL_URL"].startswith("http://127.0.0.1:")
    assert "APPROVAL_SECRET" not in json.dumps(config)
    knowledge = config["mcpServers"]["ansys-api-memory"]
    assert knowledge["command"] == str(python.resolve())
    assert knowledge["args"] == ["-m", "aedt_agent.knowledge.server"]
    assert knowledge["env"]["PYTHONPATH"] == str(root / "src")
    assert api_memory.calls == ["status", "prepare", "status"]
    assert result["api_memory"]["ready"] is True
    assert result["api_memory"]["manifest_digest"] == "manifest-abc123"
    metadata = json.loads(Path(result["metadata"]).read_text(encoding="utf-8"))
    assert metadata["api_memory"]["server_enabled"] is True
    assert [item["key"] for item in metadata["api_memory"]["packages"]] == ["pyaedt", "pyedb"]
    settings = json.loads(Path(result["claude_settings"]).read_text(encoding="utf-8"))
    assert settings == {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "env": {
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        },
    }
    assert metadata["claude_settings"] == result["claude_settings"]
    system_context = Path(result["system_context"]).read_text(encoding="utf-8")
    assert "attach_live_aedt_session(port=50061" in system_context
    assert "wait_for_live_approval" in system_context
    assert "Harness-first" in system_context
    assert "profile=\"via_target/v1\"" in system_context
    assert "ansys-operation-plan/v1" in system_context
    assert "raw COM" in system_context
    assert "Never auto-promote" in system_context
    launch_script = Path(result["launch_script"]).read_text(encoding="utf-8")
    assert Path(result["launch_script"]).name == "launch-claude.sh"
    assert result["powershell_script"] == result["launch_script"]
    assert "#!/usr/bin/env bash" in launch_script
    assert "set -euo pipefail" in launch_script
    assert "--bare" not in launch_script
    assert "--disable-slash-commands" not in launch_script
    assert "--settings" in launch_script
    assert "--setting-sources=" in launch_script
    assert "--setting-sources ''" not in launch_script
    assert "--strict-mcp-config" in launch_script
    assert "'--tools' 'AskUserQuestion'" in launch_script
    assert "'--allowedTools' 'AskUserQuestion,mcp__ansys-assistant__list_ansys_capabilities," in launch_script
    assert "mcp__ansys-assistant__attach_live_aedt_session" in launch_script
    assert "mcp__ansys-assistant__apply_live_parameterize_path_width" in launch_script
    assert "mcp__ansys-assistant__promote_ansys_capability,mcp__ansys-api-memory__get_ansys_api_memory_status" in launch_script
    assert "mcp__ansys-api-memory__find_ansys_example'" in launch_script
    assert "mcp__ansys-assistant__*" not in launch_script
    assert "mcp__ansys-api-memory__*" not in launch_script
    assert "mcp__ansys-assistant__open_layout_session" not in launch_script
    assert "'--disallowedTools' 'Bash,Edit,Write,Read,Glob,Grep,NotebookEdit" in launch_script
    assert "Task,TaskOutput,KillShell,LSP,Skill'" in launch_script
    assert "Computer" not in launch_script
    assert "Chrome" not in launch_script
    assert "--no-chrome" in launch_script
    assert "--dangerously-skip-permissions" not in launch_script
    assert "'--permission-mode'" in launch_script
    assert "'manual'" in launch_script
    assert "export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1'" in launch_script
    assert "export DISABLE_AUTOUPDATER='1'" in launch_script
    assert "aedt_agent.desktop.approval_host" in launch_script
    assert "--parent-pid \"$BASHPID\"" in launch_script
    assert "/shutdown" in launch_script
    assert "MSYS2_ARG_CONV_EXCL='*'" in launch_script
    assert processes[0][0][:3] == [str(git_bash.resolve()), "--noprofile", "--norc"]
    assert len(processes[0][1]["env"]["AEDT_AGENT_APPROVAL_KEY"]) >= 32
    assert processes[0][1]["env"]["ANTHROPIC_BASE_URL"] == "https://gateway.example.invalid"
    assert processes[0][1]["env"]["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert processes[0][1]["env"]["ANTHROPIC_AUTH_TOKEN"] == "settings-only-secret-token"
    assert processes[0][1]["env"]["API_TIMEOUT_MS"] == "3000000"
    assert "UNRELATED_SETTING" not in processes[0][1]["env"]
    for path in Path(result["session_directory"]).iterdir():
        assert "settings-only-secret-token" not in path.read_text(encoding="utf-8-sig")
    assert result["shell_pid"] == 99
    assert result["powershell_pid"] == 99
    assert result["launcher"] == {
        "kind": "git_bash",
        "executable": str(git_bash.resolve()),
        "script": result["launch_script"],
        "pid": 99,
    }
    metadata = json.loads(Path(result["metadata"]).read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 2
    assert metadata["launch_protocol_version"] == 2
    assert metadata["launcher"] == result["launcher"]


def test_api_memory_prepare_failure_keeps_runtime_harness_available(tmp_path: Path):
    root, python, claude, git_bash = _project(tmp_path)
    processes = []
    context = AedtDesktopContext(
        port=50061,
        version="2026.1",
        pid=42,
        project_name="Board Project",
        design_name="Layout1",
        design_type="HFSS 3D Layout Design",
    )

    class FailingApiMemory:
        def status(self):
            return {"status": "stale", "ready": False}

        def prepare(self):
            raise RuntimeError("index backend unavailable")

    launcher = ClaudeDesktopLauncher(
        project_root=root,
        python_executable=python,
        claude_executable=claude,
        git_bash_executable=git_bash,
        context_loader=lambda port, version: context,
        process_factory=lambda command, **kwargs: processes.append((command, kwargs)) or SimpleNamespace(pid=101),
        api_memory_factory=FailingApiMemory,
    )

    result = launcher.launch(port=50061)

    config = json.loads(Path(result["mcp_config"]).read_text(encoding="utf-8"))
    assert set(config["mcpServers"]) == {"ansys-assistant"}
    assert result["launched"] is True
    assert result["powershell_pid"] == 101
    assert result["api_memory"] == {
        "status": "stale",
        "ready": False,
        "server_enabled": False,
        "startup_action": "prepare_failed",
        "packages": [],
        "error": "RuntimeError: index backend unavailable",
    }
    metadata = json.loads(Path(result["metadata"]).read_text(encoding="utf-8"))
    assert metadata["api_memory"]["server_enabled"] is False
    system_context = Path(result["system_context"]).read_text(encoding="utf-8")
    assert "unknown operations as unsupported" in system_context
    assert "Keep known Runtime Harness tools available" in system_context
    launch_script = Path(result["launch_script"]).read_text(encoding="utf-8")
    assert "'--allowedTools' 'AskUserQuestion,mcp__ansys-assistant__list_ansys_capabilities," in launch_script
    assert "mcp__ansys-assistant__promote_ansys_capability'" in launch_script
    assert "mcp__ansys-assistant__*" not in launch_script
    assert "mcp__ansys-api-memory__" not in launch_script


def test_installer_uses_official_pyaedt_menu_api_and_preserves_aedt(tmp_path: Path):
    calls = []

    class ODesktop:
        refreshed = 0

        def RefreshToolkitUI(self):
            self.refreshed += 1

    class Desktop:
        personallib = str(tmp_path / "PersonalLib")
        odesktop = ODesktop()

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.releases = []

        def release_desktop(self, **kwargs):
            self.releases.append(kwargs)
            return True

    desktops = []

    def factory(**kwargs):
        desktop = Desktop(**kwargs)
        desktops.append(desktop)
        return desktop

    installed = install_extension(
        port=50061,
        desktop_factory=factory,
        add_script=lambda **kwargs: calls.append(kwargs) or True,
    )
    assert installed["installed"] is True
    assert calls[0]["name"] == "Ansys Agent"
    assert calls[0]["product"] == "Project"
    assert Path(calls[0]["script_file"]).name == "aedt_extension_entry.py"
    assert calls[0]["copy_to_personal_lib"] is True
    assert desktops[0].releases == [{"close_projects": False, "close_on_exit": False}]

    removed = uninstall_extension(
        port=50061,
        desktop_factory=factory,
        remove_script=lambda **kwargs: calls.append(kwargs) or True,
    )
    assert removed["uninstalled"] is True
    assert calls[-1]["name"] == "Ansys Agent"
    assert calls[-1]["product"] == "Project"
    assert desktops[-1].releases == [{"close_projects": False, "close_on_exit": False}]


def test_launcher_rejects_invalid_target_port(tmp_path: Path):
    root, python, claude, git_bash = _project(tmp_path)
    launcher = ClaudeDesktopLauncher(
        project_root=root,
        python_executable=python,
        claude_executable=claude,
        git_bash_executable=git_bash,
        context_loader=lambda port, version: pytest.fail("context loader must not run"),
    )
    with pytest.raises(Exception, match="port must be an integer"):
        launcher.launch(port=0)


def test_generated_git_bash_script_has_valid_bash_syntax(tmp_path: Path):
    installed_bash = Path(os.environ.get("ProgramFiles", "")) / "Git" / "bin" / "bash.exe"
    if not installed_bash.is_file():
        pytest.skip("Git for Windows is unavailable")
    root, python, claude, _ = _project(tmp_path)
    context = AedtDesktopContext(
        port=50061,
        version="2026.1",
        pid=42,
        project_name="Board Project",
        design_name="Layout1",
        design_type="HFSS 3D Layout Design",
    )
    launcher = ClaudeDesktopLauncher(
        project_root=root,
        python_executable=python,
        claude_executable=claude,
        git_bash_executable=installed_bash,
        api_memory_factory=lambda: _PreparingApiMemory(),
    )
    prepared = launcher.prepare(context, approval_port=50062)
    checked = subprocess.run(
        [str(installed_bash), "--noprofile", "--norc", "-n", prepared["launch_script"]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr


def test_approval_host_requires_native_decision_and_token_is_one_use():
    key = "approval-session-key-at-least-24"
    store = DesktopApprovalStore(prompt=lambda record: record.action == "project.save")
    host = ApprovalHost("127.0.0.1", 0, key, store)
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    client = DesktopApprovalClient(f"http://127.0.0.1:{host.port}", key)
    try:
        registered = client.register(
            "project.save",
            "session:preview-1",
            "digest-1",
            {"project_name": "Board", "preview_id": "preview-1"},
        )
        assert registered["status"] in {"pending", "approved"}
        decision = client.poll("session:preview-1", timeout_seconds=2)
        assert decision["status"] == "approved"
        token = decision["approval_token"]
        assert client.verify("project.save", "session:preview-1", "digest-1", token) is True
        assert client.verify("project.save", "session:preview-1", "digest-1", token) is False

        client.register(
            "hfss.analysis.start",
            "session:preview-2",
            "digest-2",
            {"setup_name": "Setup1"},
        )
        rejected = client.poll("session:preview-2", timeout_seconds=2)
        assert rejected["status"] == "rejected"
        assert "approval_token" not in rejected
    finally:
        client._post("/shutdown", {})
        thread.join(timeout=3)


def test_desktop_approval_expires_before_use():
    now = [1000.0]
    store = DesktopApprovalStore(
        prompt=lambda record: True,
        ttl_seconds=300,
        clock=lambda: now[0],
    )
    store.register(
        {
            "action": "project.save",
            "resource_id": "session:preview-expiring",
            "digest": "digest-expiring",
            "preview": {"project_name": "Board"},
        }
    )
    approved = store.poll("session:preview-expiring", timeout_seconds=2)
    assert approved["status"] == "approved"
    now[0] = 1301.0
    expired = store.poll("session:preview-expiring")
    assert expired["status"] == "expired"
    assert "approval_token" not in expired
    assert store.verify(
        {
            "action": "project.save",
            "resource_id": "session:preview-expiring",
            "digest": "digest-expiring",
            "token": approved["approval_token"],
        }
    ) is False


def test_desktop_approval_allows_only_one_active_native_decision():
    decision_started = threading.Event()
    release_decision = threading.Event()

    def prompt(record):
        decision_started.set()
        release_decision.wait(timeout=2)
        return False

    store = DesktopApprovalStore(prompt=prompt)
    store.register(
        {
            "action": "project.save",
            "resource_id": "session:preview-active",
            "digest": "digest-active",
            "preview": {"project_name": "Board"},
        }
    )
    assert decision_started.wait(timeout=1)
    with pytest.raises(ValueError, match="still active"):
        store.register(
            {
                "action": "hfss.analysis.start",
                "resource_id": "session:preview-spam",
                "digest": "digest-spam",
                "preview": {"setup_name": "Setup1"},
            }
        )
    release_decision.set()
    assert store.poll("session:preview-active", timeout_seconds=2)["status"] == "rejected"
