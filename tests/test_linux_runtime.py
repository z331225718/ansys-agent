from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from aedt_agent.desktop.launcher import AedtDesktopContext
from aedt_agent.linux.approval_host import LinuxApprovalStore, UnixApprovalBroker, request
from aedt_agent.linux.launcher import LinuxClaudeLauncher, LinuxLaunchError
from aedt_agent.live.discovery import _command_port


def _preview() -> dict[str, object]:
    return {"preview_id": "preview-1", "snapshot_digest": "abc123"}


def test_linux_approval_store_is_one_use_and_digest_bound():
    store = LinuxApprovalStore(ttl_seconds=60)
    registered = store.register({"action": "layout.edit", "resource_id": "r1", "digest": "digest", "preview": _preview()})

    assert registered["status"] == "pending"
    assert store.decide("r1", approved=True)["status"] == "approved"
    token = store.poll("r1")["approval_token"]
    assert store.verify({"action": "layout.edit", "resource_id": "r1", "digest": "digest", "token": token})
    assert not store.verify({"action": "layout.edit", "resource_id": "r1", "digest": "digest", "token": token})


def test_aedt_discovery_parses_secure_grpc_host_port_argument():
    assert _command_port(["ansysedt", "-grpcsrv", "127.0.0.1:50051"]) == 50051


@pytest.mark.skipif(os.name == "nt", reason="Unix sockets require POSIX")
def test_linux_approval_socket_is_owner_only_and_decides(tmp_path: Path):
    socket_path = tmp_path / "private" / "approval.sock"
    store = LinuxApprovalStore(ttl_seconds=60)
    broker = UnixApprovalBroker(socket_path, store)
    broker.serve_in_thread()
    try:
        store.register({"action": "hfss.edit", "resource_id": "r1", "digest": "digest", "preview": _preview()})
        listed = request(socket_path, {"command": "list"})
        assert listed[0]["resource_id"] == "r1"
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
        assert request(socket_path, {"command": "approve", "resource_id": "r1"})["status"] == "approved"
    finally:
        broker.close()
    assert not socket_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="Linux launcher cannot run on Windows")
def test_linux_launcher_prepares_restricted_session(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src" / "aedt_agent").mkdir(parents=True)
    python = root / ".venv" / "bin" / "python"
    claude = root / "bin" / "claude"
    python.parent.mkdir(parents=True)
    claude.parent.mkdir(parents=True)
    python.touch()
    claude.touch()
    context = AedtDesktopContext(50051, "2026.1", 17, "project", "layout", "HFSS 3D Layout Design")
    launcher = LinuxClaudeLauncher(
        project_root=root,
        python_executable=python,
        claude_executable=claude,
        context_loader=lambda port, version: context,
        api_memory_factory=lambda: None,
    )

    result = launcher.prepare(context, approval_port=51234, api_memory_status={"status": "ready", "ready": True})

    mcp = json.loads(Path(result["mcp_config"]).read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["ansys-assistant"]["env"]["AEDT_AGENT_EXPECTED_PORT"] == "50051"
    script = Path(result["launch_script"]).read_text(encoding="utf-8")
    assert "aedt_agent.linux.approval_host" in script
    assert "exec " not in script
    context_text = Path(result["system_context"]).read_text(encoding="utf-8")
    assert "Never prepend" in context_text
    assert result["approval_socket"].endswith(".sock")


def test_linux_launcher_fails_closed_on_windows():
    if os.name != "nt":
        pytest.skip("Windows-only platform guard")
    with pytest.raises(LinuxLaunchError, match="cannot run on Windows"):
        LinuxClaudeLauncher(project_root=Path.cwd())


def test_linux_release_scripts_keep_native_binary_and_verification_contract():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "scripts" / "linux" / "Install-AnsysAgentLinux.sh").read_text(encoding="utf-8")
    builder = (root / "scripts" / "linux" / "New-AnsysAgentLinuxBundle.sh").read_text(encoding="utf-8")

    assert "sha256sum --strict --check SHA256SUMS" in installer
    assert 'target.get("os") != "linux"' in installer
    assert "codebase-memory-mcp" in installer
    assert "'setuptools>=69' wheel" in installer
    assert "--codebase-memory-binary" in builder
    assert '"platform": "linux-x86_64"' in builder
