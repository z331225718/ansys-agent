from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
from typing import Any, Callable
from uuid import uuid4

from aedt_agent.knowledge.api_memory import AnsysApiMemory
from aedt_agent.live.manager import LiveAedtSessionManager


_DESKTOP_CLAUDE_BUILTIN_TOOLS = ("AskUserQuestion",)
_DESKTOP_CLAUDE_DENIED_TOOLS = (
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Glob",
    "Grep",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
    "TaskOutput",
    "KillShell",
    "LSP",
    "Skill",
)
_DESKTOP_ASSISTANT_MCP_TOOLS = (
    "list_ansys_capabilities",
    "list_ansys_capabilities_v2",
    "list_ansys_workflows",
    "inspect_ansys_workflow",
    "preview_ansys_workflow_start",
    "apply_ansys_workflow_start",
    "get_ansys_workflow_status",
    "preview_ansys_workflow_advance",
    "apply_ansys_workflow_advance",
    "attach_live_aedt_session",
    "release_live_aedt_session",
    "get_live_aedt_project_info",
    "preview_live_project_save",
    "apply_live_project_save",
    "get_live_hfss_design_inventory",
    "get_live_aedt_setup_inventory",
    "get_live_aedt_solution_inventory",
    "get_live_hfss_geometry_inventory",
    "get_live_hfss_material_inventory",
    "preview_live_hfss_material_assign",
    "apply_live_hfss_material_assign",
    "get_live_hfss_mesh_inventory",
    "preview_live_hfss_length_mesh_create",
    "apply_live_hfss_length_mesh_create",
    "preview_live_hfss_geometry_create",
    "apply_live_hfss_geometry_create",
    "preview_live_hfss_geometry_boundary_create",
    "apply_live_hfss_geometry_boundary_create",
    "preview_live_hfss_setup_create",
    "apply_live_hfss_setup_create",
    "preview_live_hfss_setup_update",
    "apply_live_hfss_setup_update",
    "preview_live_frequency_sweep_create",
    "apply_live_frequency_sweep_create",
    "preview_live_hfss_setup_sweep_create",
    "apply_live_hfss_setup_sweep_create",
    "preview_live_hfss_report_create",
    "apply_live_hfss_report_create",
    "preview_live_hfss_boundary_create",
    "apply_live_hfss_boundary_create",
    "preview_live_hfss_analysis_start",
    "apply_live_hfss_analysis_start",
    "get_live_hfss_analysis_status",
    "preview_live_hfss_analysis_cancel",
    "apply_live_hfss_analysis_cancel",
    "preview_live_hfss_results_export",
    "apply_live_hfss_results_export",
    "list_live_layout_paths",
    "get_live_layout_routing_inventory",
    "get_live_layout_technology_inventory",
    "get_live_layout_connectivity_inventory",
    "get_live_layout_port_candidate_inventory",
    "preview_live_layout_component_ports_create",
    "apply_live_layout_component_ports_create",
    "get_live_layout_edge_port_candidate_inventory",
    "preview_live_layout_edge_ports_create",
    "apply_live_layout_edge_ports_create",
    "get_live_layout_object_inventory",
    "get_live_layout_object_property_inventory",
    "preview_live_layout_object_property_update",
    "apply_live_layout_object_property_update",
    "get_live_aedt_variable_inventory",
    "preview_live_aedt_variable_upsert",
    "apply_live_aedt_variable_upsert",
    "preview_live_parameterize_path_width",
    "apply_live_parameterize_path_width",
    "wait_for_live_approval",
    "get_ansys_operation_plan_schema",
    "propose_ansys_operation",
    "validate_ansys_operation",
    "preview_exploratory_operation",
    "apply_exploratory_operation",
    "capture_capability_trace",
    "promote_ansys_capability",
)
_DESKTOP_API_MEMORY_MCP_TOOLS = (
    "get_ansys_api_memory_status",
    "search_ansys_api",
    "inspect_ansys_symbol",
    "trace_ansys_call",
    "search_ansys_source",
    "find_ansys_example",
)
_CLAUDE_USER_ENV_ALLOWLIST = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "API_TIMEOUT_MS",
)


class DesktopLaunchError(RuntimeError):
    pass


@dataclass(frozen=True)
class AedtDesktopContext:
    port: int
    version: str
    pid: int | None
    project_name: str
    design_name: str
    design_type: str


class ClaudeDesktopLauncher:
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
        self.project_root = _project_root(project_root)
        self.python_executable = _required_file(
            python_executable or self.project_root / ".venv" / "Scripts" / "python.exe",
            "project Python interpreter",
        )
        claude = claude_executable or shutil.which("claude")
        if not claude:
            raise DesktopLaunchError("Claude Code executable was not found on PATH")
        self.claude_executable = _required_file(claude, "Claude Code executable")
        self.context_loader = context_loader or _load_live_context
        self.process_factory = process_factory
        self.api_memory_factory = api_memory_factory or AnsysApiMemory

    def launch(self, *, port: int, version: str = "2026.1") -> dict[str, Any]:
        context = self.context_loader(_valid_port(port), version)
        api_memory_status = self._prepare_api_memory()
        session = self.prepare(context, api_memory_status=api_memory_status)
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        environment = _claude_process_environment()
        environment["AEDT_AGENT_APPROVAL_KEY"] = secrets.token_urlsafe(32)
        process = self.process_factory(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoExit",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                session["powershell_script"],
            ],
            cwd=str(self.project_root),
            creationflags=creationflags,
            env=environment,
        )
        return {
            "launched": True,
            "powershell_pid": getattr(process, "pid", None),
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
        session_dir = self.project_root / ".aedt-agent" / "desktop" / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        mcp_path = session_dir / "mcp.json"
        context_path = session_dir / "context.md"
        settings_path = session_dir / "claude-settings.json"
        powershell_path = session_dir / "launch-claude.ps1"
        metadata_path = session_dir / "session.json"
        approval_port = approval_port or _available_loopback_port()
        approval_url = f"http://127.0.0.1:{approval_port}"
        if api_memory_status is None:
            api_memory_status = self._prepare_api_memory()
        api_memory = _api_memory_metadata(api_memory_status)

        mcp_servers = {
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
        mcp_config = {"mcpServers": mcp_servers}
        mcp_path.write_text(json.dumps(mcp_config, ensure_ascii=True, indent=2), encoding="utf-8")
        context_path.write_text(_system_context(context, api_memory=api_memory), encoding="utf-8")
        settings_path.write_text(
            json.dumps(_claude_settings(), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        powershell_path.write_text(
            _powershell_script(
                project_root=self.project_root,
                claude_executable=self.claude_executable,
                mcp_path=mcp_path,
                context_path=context_path,
                settings_path=settings_path,
                context=context,
                python_executable=self.python_executable,
                approval_port=approval_port,
                approval_url=approval_url,
                mcp_server_names=tuple(mcp_servers),
            ),
            encoding="utf-8-sig",
        )
        metadata = {
            "schema_version": 1,
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "context": asdict(context),
            "project_root": str(self.project_root),
            "mcp_config": str(mcp_path),
            "system_context": str(context_path),
            "claude_settings": str(settings_path),
            "powershell_script": str(powershell_path),
            "approval_url": approval_url,
            "api_memory": api_memory,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
        return {
            "desktop_session_id": session_id,
            "session_directory": str(session_dir),
            "mcp_config": str(mcp_path),
            "system_context": str(context_path),
            "claude_settings": str(settings_path),
            "powershell_script": str(powershell_path),
            "metadata": str(metadata_path),
            "approval_url": approval_url,
            "api_memory": api_memory,
        }

    def _prepare_api_memory(self) -> dict[str, Any]:
        try:
            memory = self.api_memory_factory()
        except Exception as exc:
            return _unavailable_api_memory("initialize_failed", exc)

        try:
            status = memory.status()
        except Exception:
            status = None
        if isinstance(status, dict) and status.get("ready") is True:
            return {**status, "startup_action": "status_current"}

        try:
            memory.prepare()
        except Exception as exc:
            result = dict(status) if isinstance(status, dict) else {}
            result.update(
                {
                    "status": str(result.get("status") or "unavailable"),
                    "ready": False,
                    "startup_action": "prepare_failed",
                    "error": _bounded_error(exc),
                }
            )
            return result

        try:
            refreshed = memory.status()
        except Exception as exc:
            return _unavailable_api_memory("status_failed_after_prepare", exc)
        if not isinstance(refreshed, dict):
            return {
                "status": "invalid",
                "ready": False,
                "startup_action": "invalid_status_after_prepare",
                "error": "Ansys API memory status was not an object",
            }
        return {**refreshed, "startup_action": "prepared"}


def launch_from_aedt_environment() -> dict[str, Any]:
    try:
        port = int(os.environ["PYAEDT_DESKTOP_PORT"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DesktopLaunchError("PYAEDT_DESKTOP_PORT is missing; launch this entry from AEDT") from exc
    version = os.environ.get("PYAEDT_DESKTOP_VERSION", "2026.1")
    project_root = os.environ.get("AEDT_AGENT_PROJECT_ROOT")
    return ClaudeDesktopLauncher(project_root=project_root).launch(port=port, version=version)


def _load_live_context(port: int, version: str) -> AedtDesktopContext:
    manager = LiveAedtSessionManager()
    session_id = None
    try:
        opened = manager.attach(port=port, version=version)
        session_id = opened["live_session_id"]
        info = manager.project_info(session_id)
        project_name = str(info.get("active_project") or "").strip()
        if not project_name:
            raise DesktopLaunchError("the selected AEDT session has no active project")
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


def _system_context(
    context: AedtDesktopContext,
    *,
    api_memory: dict[str, Any] | None = None,
) -> str:
    api_memory = api_memory or {"ready": False, "status": "not_checked"}
    if api_memory.get("ready") is True:
        manifest_digest = str(api_memory.get("manifest_digest") or "")
        knowledge_rule = (
            "The read-only `ansys-api-memory` MCP is ready"
            + (f" for manifest `{manifest_digest}`" if manifest_digest else "")
            + ". Use it only after a real Runtime Harness capability miss."
        )
    else:
        status = str(api_memory.get("status") or "unavailable")
        knowledge_rule = (
            f"The `ansys-api-memory` MCP is disabled for this session (status: `{status}`). "
            "Keep known Runtime Harness tools available, but report unknown operations as unsupported."
        )
    return f"""# AEDT Desktop session context

You were launched by the Ansys Agent button inside AEDT.

- Exact gRPC target port: `{context.port}`
- AEDT version: `{context.version}`
- AEDT process id: `{context.pid or ''}`
- Active project: `{context.project_name}`
- Active design: `{context.design_name}`
- Design type: `{context.design_type}`
- API memory: `{api_memory.get('status', 'unavailable')}`

Rules:

1. First call `attach_live_aedt_session(port={context.port}, version=\"{context.version}\")`.
2. Then call `get_live_aedt_project_info` and verify the active project is exactly `{context.project_name}`.
3. Attach exactly once unless the tool explicitly reports that the session is invalid. Reuse its `live_session_id`; do not retry a successful attach.
4. Never discover or select another AEDT process, never launch a second AEDT, and never close AEDT or projects.
5. The active design above is its canonical display name. Never prepend an AEDT internal prefix such as `0;`.
6. For `HFSS 3D Layout Design`, use only the layout inventory/edit tools for geometry. Do not call HFSS 3D design or geometry inventory tools.
7. For a `LineWidth=<value>` request, filter `list_live_layout_paths` with `selector.target_width`, then preview parameterization with the same width as the variable value unless the user specifies another value.
8. Use Harness-first routing: strict Workflow, then a registered structured Ansys Assistant Runtime Harness capability, then controlled Exploration, otherwise unsupported.
9. {knowledge_rule}
10. API memory is knowledge only. Source evidence is not permission, cannot drive AEDT, and cannot bypass Runtime validation or Desktop approval.
11. For a genuine capability miss, query API memory and submit an `ansys-operation-plan/v1`; use only the declarative propose/validate/preview/apply path.
12. Never run or request arbitrary Python, shell, `eval`/`exec`, raw COM, or generated AEDT scripts to work around a capability miss.
13. Reads may run directly through registered tools. Every live edit, solve, cancel, export, or save must use its preview/apply contract.
14. Never invent an approval token. After preview, call `wait_for_live_approval` and wait for the native Desktop Host decision.
15. If approval is rejected or expires, do not retry or create another preview unless the user explicitly asks.
16. Do not save the project unless the user explicitly requests save and separately approves the save preview.
17. Never auto-promote a successful exploration, hot-patch the Harness, or modify this repository. Promotion may only create a review candidate for explicit human approval.
18. Release the live session when the task is complete; release must leave AEDT and all projects open.
"""


def _powershell_script(
    *,
    project_root: Path,
    claude_executable: Path,
    mcp_path: Path,
    context_path: Path,
    settings_path: Path,
    context: AedtDesktopContext,
    python_executable: Path,
    approval_port: int,
    approval_url: str,
    mcp_server_names: tuple[str, ...],
) -> str:
    prompt = (
        f"已从 AEDT 打开 Ansys Agent。请先连接端口 {context.port}，核对活动工程 "
        f"{context.project_name} 和设计 {context.design_name or '(none)'}，然后等待我的任务。"
    )
    builtin_tools = ",".join(_DESKTOP_CLAUDE_BUILTIN_TOOLS)
    allowed_mcp_tools = [
        *(f"mcp__ansys-assistant__{name}" for name in _DESKTOP_ASSISTANT_MCP_TOOLS),
    ]
    if "ansys-api-memory" in mcp_server_names:
        allowed_mcp_tools.extend(
            f"mcp__ansys-api-memory__{name}" for name in _DESKTOP_API_MEMORY_MCP_TOOLS
        )
    allowed_tools = ",".join([*_DESKTOP_CLAUDE_BUILTIN_TOOLS, *allowed_mcp_tools])
    denied_tools = ",".join(_DESKTOP_CLAUDE_DENIED_TOOLS)
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"Set-Location -LiteralPath {_ps_literal(str(project_root))}",
            "$env:MCP_TIMEOUT = '30000'",
            "$env:FASTMCP_CHECK_FOR_UPDATES = 'off'",
            "$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = '1'",
            "$env:DISABLE_AUTOUPDATER = '1'",
            "$approvalHeaders = @{ 'X-Ansys-Agent-Key' = $env:AEDT_AGENT_APPROVAL_KEY }",
            "$approvalArgs = @('-m', 'aedt_agent.desktop.approval_host', '--port', " + _ps_literal(str(approval_port)) + ")",
            f"$approvalHost = Start-Process -FilePath {_ps_literal(str(python_executable))} -ArgumentList $approvalArgs -PassThru -WindowStyle Hidden",
            "$approvalReady = $false",
            "for ($attempt = 0; $attempt -lt 50; $attempt++) {",
            "  try {",
            f"    $null = Invoke-RestMethod -Uri {_ps_literal(approval_url + '/health')} -Headers $approvalHeaders -TimeoutSec 1",
            "    $approvalReady = $true",
            "    break",
            "  } catch { Start-Sleep -Milliseconds 100 }",
            "}",
            "if (-not $approvalReady) { Stop-Process -Id $approvalHost.Id -Force -ErrorAction SilentlyContinue; throw 'Ansys Agent approval host failed to start.' }",
            "try {",
            f"  & {_ps_literal(str(claude_executable))} `",
            "    --bare `",
            f"    --settings {_ps_literal(str(settings_path))} `",
            "    --setting-sources= `",
            f"    --mcp-config {_ps_literal(str(mcp_path))} `",
            "    --strict-mcp-config `",
            f"    --tools {_ps_literal(builtin_tools)} `",
            f"    --allowedTools {_ps_literal(allowed_tools)} `",
            f"    --disallowedTools {_ps_literal(denied_tools)} `",
            "    --disable-slash-commands `",
            "    --no-chrome `",
            f"    --append-system-prompt-file {_ps_literal(str(context_path))} `",
            "    --permission-mode manual `",
            f"    {_ps_literal(prompt)}",
            "  if ($LASTEXITCODE -ne 0) { Write-Host \"Claude Code exited with code $LASTEXITCODE\" -ForegroundColor Red }",
            "} finally {",
            "  try {",
            f"    $null = Invoke-RestMethod -Method Post -Uri {_ps_literal(approval_url + '/shutdown')} -Headers $approvalHeaders -ContentType 'application/json' -Body '{{}}' -TimeoutSec 2",
            "  } catch {}",
            "  Wait-Process -Id $approvalHost.Id -Timeout 3 -ErrorAction SilentlyContinue",
            "  Stop-Process -Id $approvalHost.Id -Force -ErrorAction SilentlyContinue",
            "}",
            "",
        ]
    )


def _claude_settings() -> dict[str, Any]:
    """Return the only settings loaded by a Desktop-bound Claude session."""

    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "env": {
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        },
    }


def _claude_process_environment() -> dict[str, str]:
    """Build a bare-mode environment without loading hooks, plugins, or project settings."""

    environment = os.environ.copy()
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    settings_path = (
        Path(configured).expanduser() / "settings.json"
        if configured
        else Path.home() / ".claude" / "settings.json"
    )
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return environment
    user_environment = settings.get("env") if isinstance(settings, dict) else None
    if not isinstance(user_environment, dict):
        return environment
    for name in _CLAUDE_USER_ENV_ALLOWLIST:
        if environment.get(name):
            continue
        value = user_environment.get(name)
        if isinstance(value, str) and value.strip():
            environment[name] = value
    return environment


def _project_root(value: str | Path | None) -> Path:
    if value:
        root = Path(value).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[3]
    if not (root / "pyproject.toml").is_file() or not (root / "src" / "aedt_agent").is_dir():
        raise DesktopLaunchError(f"invalid ansys-agent project root: {root}")
    return root


def _required_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise DesktopLaunchError(f"{label} does not exist: {path}")
    return path


def _valid_port(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise DesktopLaunchError("AEDT gRPC port must be an integer from 1 to 65535")
    return value


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _api_memory_metadata(status: dict[str, Any]) -> dict[str, Any]:
    manifest = status.get("manifest") if isinstance(status.get("manifest"), dict) else {}
    raw_packages = manifest.get("packages") or status.get("packages") or []
    packages = []
    if isinstance(raw_packages, list):
        for item in raw_packages[:8]:
            if not isinstance(item, dict):
                continue
            packages.append(
                {
                    key: str(item[key])
                    for key in ("key", "distribution", "version", "source_digest", "project")
                    if item.get(key) is not None
                }
            )
    status_name = str(status.get("status") or "unknown")[:80]
    ready = status_name == "ready" and status.get("ready") is True
    result: dict[str, Any] = {
        "status": status_name,
        "ready": ready,
        "server_enabled": ready,
        "startup_action": str(status.get("startup_action") or "provided")[:80],
        "packages": packages,
    }
    manifest_digest = manifest.get("manifest_digest") or status.get("manifest_digest")
    if manifest_digest:
        result["manifest_digest"] = str(manifest_digest)
    backend = manifest.get("backend")
    if isinstance(backend, dict):
        result["backend"] = {
            key: str(backend[key])
            for key in ("name", "version")
            if backend.get(key) is not None
        }
    if status.get("error"):
        result["error"] = str(status["error"])[:1000]
    return result


def _unavailable_api_memory(startup_action: str, exc: Exception) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "ready": False,
        "startup_action": startup_action,
        "error": _bounded_error(exc),
    }


def _bounded_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:1000]


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
