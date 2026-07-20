from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import subprocess
from typing import Any, Callable
from uuid import uuid4

from aedt_agent.desktop.launcher import (
    AedtDesktopContext,
    _DESKTOP_API_MEMORY_MCP_TOOLS,
    _DESKTOP_ASSISTANT_MCP_TOOLS,
    _DESKTOP_CLAUDE_BUILTIN_TOOLS,
    _DESKTOP_CLAUDE_DENIED_TOOLS,
    _claude_process_environment as _desktop_claude_process_environment,
    _claude_settings as _desktop_claude_settings,
)
from aedt_agent.knowledge.api_memory import AnsysApiMemory
from aedt_agent.live.manager import LiveAedtSessionManager


class LinuxLaunchError(RuntimeError):
    pass


class LinuxClaudeLauncher:
    """Prepare and launch a local Linux Claude/MCP harness for one AEDT port."""

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        python_executable: str | Path | None = None,
        claude_executable: str | Path | None = None,
        context_loader: Callable[[int, str], AedtDesktopContext] | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
        api_memory_factory: Callable[[], Any] | None = None,
    ) -> None:
        if os.name == "nt":
            raise LinuxLaunchError("Linux launcher cannot run on Windows")
        self.project_root = _project_root(project_root)
        self.python_executable = _required_file(
            python_executable or self.project_root / ".venv" / "bin" / "python",
            "project Python interpreter",
        )
        claude = claude_executable or shutil.which("claude")
        if not claude:
            raise LinuxLaunchError("Claude Code executable was not found on PATH")
        self.claude_executable = _required_file(claude, "Claude Code executable")
        self.context_loader = context_loader or _load_live_context
        self.process_factory = process_factory
        self.api_memory_factory = api_memory_factory or AnsysApiMemory

    def launch(self, *, port: int, version: str = "2026.1") -> dict[str, Any]:
        context = self.context_loader(_valid_port(port), version)
        session = self.prepare(context)
        environment = _claude_process_environment()
        environment["AEDT_AGENT_APPROVAL_KEY"] = secrets.token_urlsafe(32)
        process = self.process_factory(
            ["bash", session["launch_script"]],
            cwd=str(self.project_root),
            env=environment,
            start_new_session=True,
        )
        return {
            "launched": True,
            "shell_pid": getattr(process, "pid", None),
            "context": asdict(context),
            **session,
        }

    def prepare(
        self,
        context: AedtDesktopContext,
        *,
        approval_port: int | None = None,
        api_memory_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        session_dir = self.project_root / ".aedt-agent" / "linux" / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        mcp_path = session_dir / "mcp.json"
        context_path = session_dir / "context.md"
        settings_path = session_dir / "claude-settings.json"
        launch_path = session_dir / "launch-claude.sh"
        metadata_path = session_dir / "session.json"
        approval_port = approval_port or _available_loopback_port()
        approval_url = f"http://127.0.0.1:{approval_port}"
        approval_socket = _approval_socket_path(session_id)
        api_memory_status = api_memory_status or self._prepare_api_memory()
        api_memory = _api_memory_metadata(api_memory_status)

        mcp_servers: dict[str, Any] = {
            "ansys-assistant": {
                "command": str(self.python_executable),
                "args": ["-m", "aedt_agent.interactive.server"],
                "env": {
                    "PYTHONPATH": str(self.project_root / "src"),
                    "FASTMCP_CHECK_FOR_UPDATES": "off",
                    "AEDT_AGENT_EXPECTED_PORT": str(context.port),
                    "AEDT_AGENT_EXPECTED_PROJECT": context.project_name,
                    "AEDT_AGENT_EXPECTED_DESIGN": context.design_name,
                    "AEDT_AGENT_EXPECTED_VERSION": context.version,
                    "AEDT_AGENT_DESKTOP_STRICT": "1",
                    "AEDT_AGENT_APPROVAL_URL": approval_url,
                    "AEDT_AGENT_APPROVAL_KEY": "${AEDT_AGENT_APPROVAL_KEY}",
                },
            }
        }
        if api_memory["ready"]:
            mcp_servers["ansys-api-memory"] = {
                "command": str(self.python_executable),
                "args": ["-m", "aedt_agent.knowledge.server"],
                "env": {
                    "PYTHONPATH": str(self.project_root / "src"),
                    "FASTMCP_CHECK_FOR_UPDATES": "off",
                },
            }
        mcp_path.write_text(json.dumps({"mcpServers": mcp_servers}, ensure_ascii=True, indent=2), encoding="utf-8")
        context_path.write_text(
            _linux_system_context(context, api_memory=api_memory, approval_socket=approval_socket),
            encoding="utf-8",
        )
        settings_path.write_text(json.dumps(_claude_settings(), ensure_ascii=True, indent=2), encoding="utf-8")
        launch_path.write_text(
            _shell_script(
                python_executable=self.python_executable,
                claude_executable=self.claude_executable,
                mcp_path=mcp_path,
                context_path=context_path,
                settings_path=settings_path,
                approval_port=approval_port,
                approval_socket=approval_socket,
                mcp_server_names=tuple(mcp_servers),
            ),
            encoding="utf-8",
        )
        launch_path.chmod(0o700)
        metadata = {
            "schema_version": 1,
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "platform": "linux",
            "context": asdict(context),
            "project_root": str(self.project_root),
            "mcp_config": str(mcp_path),
            "system_context": str(context_path),
            "claude_settings": str(settings_path),
            "launch_script": str(launch_path),
            "approval_url": approval_url,
            "approval_socket": str(approval_socket),
            "api_memory": api_memory,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
        return {
            "linux_session_id": session_id,
            "session_directory": str(session_dir),
            "mcp_config": str(mcp_path),
            "system_context": str(context_path),
            "claude_settings": str(settings_path),
            "launch_script": str(launch_path),
            "metadata": str(metadata_path),
            "approval_url": approval_url,
            "approval_socket": str(approval_socket),
            "api_memory": api_memory,
        }

    def _prepare_api_memory(self) -> dict[str, Any]:
        try:
            memory = self.api_memory_factory()
            status = memory.status()
            if isinstance(status, dict) and status.get("ready") is True:
                return {**status, "startup_action": "status_current"}
            memory.prepare()
            status = memory.status()
            if isinstance(status, dict):
                return {**status, "startup_action": "prepared"}
            raise LinuxLaunchError("Ansys API memory status was not an object")
        except Exception as exc:
            return {"status": "unavailable", "ready": False, "startup_action": "prepare_failed", "error": str(exc)[:1000]}


def _load_live_context(port: int, version: str) -> AedtDesktopContext:
    manager = LiveAedtSessionManager()
    session_id = None
    try:
        opened = manager.attach(port=port, version=version)
        session_id = opened["live_session_id"]
        info = manager.project_info(session_id)
        project_name = str(info.get("active_project") or "").strip()
        if not project_name:
            raise LinuxLaunchError("the selected AEDT session has no active project")
        return AedtDesktopContext(
            port=port,
            version=version,
            pid=_optional_int(info.get("pid") or opened.get("probe", {}).get("pid")),
            project_name=project_name,
            design_name=str(info.get("active_design") or ""),
            design_type=str(info.get("design_type") or ""),
        )
    finally:
        if session_id is not None:
            try:
                manager.release(session_id)
            except Exception:
                pass
        manager.close()


def _linux_system_context(context: AedtDesktopContext, *, api_memory: dict[str, Any], approval_socket: Path) -> str:
    knowledge_rule = (
        "The read-only `ansys-api-memory` MCP is ready. Use it only after a real Runtime Harness capability miss."
        if api_memory.get("ready") is True
        else "API memory is unavailable. Report unknown operations as unsupported."
    )
    return f"""# AEDT Linux session context

This is a Linux-local AEDT session. It is bound to one explicit local gRPC port.

- Exact gRPC target port: `{context.port}`
- AEDT version: `{context.version}`
- AEDT process id: `{context.pid or ''}`
- Active project: `{context.project_name}`
- Active design: `{context.design_name}`
- Design type: `{context.design_type}`

Rules:

1. First call `attach_live_aedt_session(port={context.port}, version=\"{context.version}\")`, then call `get_live_aedt_project_info` and verify project `{context.project_name}`.
2. Attach exactly once unless that tool reports the session invalid. Never discover another process, start AEDT, or connect to a remote host.
3. Never prepend an AEDT internal prefix such as `0;` to the design name.
4. For HFSS 3D Layout, use layout inventory/edit tools only, not HFSS 3D geometry tools.
5. {knowledge_rule}
6. API memory is knowledge, not permission. It cannot bypass Runtime validation, preview, approval, or readback.
7. Reads run through registered tools. Every edit, solve, cancel, export, or save needs preview, then external approval, then apply and readback.
8. After a preview, tell the user to use a second Linux terminal and run `ansys-agent-linux approvals --socket {approval_socket}`. Do not invent an approval token or ask the user to paste one.
9. Do not run or request arbitrary Python, shell, raw COM, `eval`, `exec`, or generated AEDT scripts.
"""


def _shell_script(*, python_executable: Path, claude_executable: Path, mcp_path: Path, context_path: Path, settings_path: Path, approval_port: int, approval_socket: Path, mcp_server_names: tuple[str, ...]) -> str:
    allowed = list(_DESKTOP_CLAUDE_BUILTIN_TOOLS)
    for server_name in mcp_server_names:
        source = _DESKTOP_ASSISTANT_MCP_TOOLS if server_name == "ansys-assistant" else _DESKTOP_API_MEMORY_MCP_TOOLS
        allowed.extend(f"mcp__{server_name}__{name}" for name in source)
    claude_args = [
        "--bare", "--mcp-config", str(mcp_path), "--strict-mcp-config",
        "--append-system-prompt-file", str(context_path), "--settings", str(settings_path),
        "--setting-sources=", "--tools", ",".join(_DESKTOP_CLAUDE_BUILTIN_TOOLS),
        "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(_DESKTOP_CLAUDE_DENIED_TOOLS),
        "--disable-slash-commands", "--no-chrome", "--permission-mode", "manual",
    ]
    quoted = " ".join(shlex.quote(item) for item in claude_args)
    return "\n".join([
        "#!/usr/bin/env bash", "set -euo pipefail", "export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", "export DISABLE_AUTOUPDATER=1",
        f"{shlex.quote(str(python_executable))} -m aedt_agent.linux.approval_host --port {approval_port} --socket {shlex.quote(str(approval_socket))} &",
        "approval_pid=$!",
        "cleanup() { kill \"$approval_pid\" 2>/dev/null || true; wait \"$approval_pid\" 2>/dev/null || true; }",
        "trap cleanup EXIT INT TERM",
        f"{shlex.quote(str(claude_executable))} {quoted}",
        "",
    ])


def _claude_settings() -> dict[str, Any]:
    return _desktop_claude_settings()


def _claude_process_environment() -> dict[str, str]:
    return _desktop_claude_process_environment()


def _api_memory_metadata(status: dict[str, Any]) -> dict[str, Any]:
    return {"status": str(status.get("status") or "unavailable"), "ready": status.get("ready") is True, "manifest_digest": str(status.get("manifest", {}).get("manifest_digest") or "")}


def _project_root(value: str | Path | None) -> Path:
    root = Path(value or Path(__file__).resolve().parents[3]).expanduser().resolve()
    if not (root / "src" / "aedt_agent").is_dir():
        raise LinuxLaunchError(f"project root is invalid: {root}")
    return root


def _required_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise LinuxLaunchError(f"{label} does not exist: {path}")
    return path


def _valid_port(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise LinuxLaunchError("port must be an integer from 1 to 65535")
    return value


def _optional_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _approval_socket_path(session_id: str) -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/aedt-agent-{os.getuid()}"))
    return runtime / "aedt-agent" / f"{session_id}.sock"
