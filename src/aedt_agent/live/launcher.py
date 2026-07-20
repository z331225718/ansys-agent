from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time
from typing import Any, Callable

from aedt_agent.live.target import AedtTarget


class AedtLaunchError(RuntimeError):
    pass


def resolve_aedt_executable(
    *,
    version: str = "2026.1",
    install_dir: str | Path | None = None,
) -> Path:
    code = _version_code(version)
    candidates: list[Path] = []
    if install_dir:
        candidates.append(Path(install_dir))
    for name in ("AEDT_INSTALL_DIR", f"ANSYSEM_ROOT{code}"):
        configured = os.getenv(name)
        if configured:
            candidates.append(Path(configured))
    program_files = os.getenv("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "ANSYS Inc" / f"v{code}" / "AnsysEM")

    executable_names = ("ansysedt.exe",) if os.name == "nt" else ("ansysedt", "ansysedt.exe")
    for candidate in candidates:
        for executable_name in executable_names:
            locations = [candidate] if candidate.name.lower() == executable_name else [candidate / executable_name]
            if os.name != "nt":
                locations.append(candidate / "Linux64" / executable_name)
            for executable in locations:
                if executable.is_file():
                    return executable.resolve()
    raise AedtLaunchError(
        f"cannot locate ansysedt for AEDT {version}; set AEDT_INSTALL_DIR or ANSYSEM_ROOT{code}"
    )


class AedtLauncher:
    def __init__(
        self,
        *,
        process_factory: Callable[..., Any] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        choose_port: Callable[[], int] | None = None,
        port_is_free: Callable[[int], bool] | None = None,
        port_is_open: Callable[[int], bool] | None = None,
        readiness_probe: Callable[[int], bool] | None = None,
        grpc_server_argument_factory: Callable[[int], str] | None = None,
    ) -> None:
        self._process_factory = process_factory
        self._monotonic = monotonic
        self._sleep = sleep
        self._choose_port = choose_port or _choose_free_port
        self._port_is_free = port_is_free or _port_is_free
        # ``port_is_open`` remains as a compatibility injection point for callers
        # that supplied an AEDT-aware probe before ``readiness_probe`` was named.
        self._readiness_probe = readiness_probe or port_is_open or _pyaedt_grpc_session_ready
        self._grpc_server_argument_factory = grpc_server_argument_factory or _pyaedt_grpc_server_argument

    def launch(
        self,
        *,
        probe: Callable[[AedtTarget, float], dict[str, Any]],
        version: str = "2026.1",
        port: int = 0,
        install_dir: str | Path | None = None,
        non_graphical: bool = False,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        if type(port) is not int or port < 0 or port > 65535:
            raise AedtLaunchError(f"invalid gRPC port: {port!r}")
        if isinstance(timeout, bool) or float(timeout) <= 0:
            raise AedtLaunchError("launch timeout must be positive")
        selected_port = self._choose_port() if port == 0 else port
        if not self._port_is_free(selected_port):
            raise AedtLaunchError(f"gRPC port {selected_port} is already in use")

        executable = resolve_aedt_executable(version=version, install_dir=install_dir)
        legacy_grpc_args = _uses_pre_grpc_args()
        try:
            server_argument = str(self._grpc_server_argument_factory(selected_port)).strip()
        except AedtLaunchError:
            raise
        except Exception as exc:
            raise AedtLaunchError(f"could not derive PyAEDT gRPC server arguments: {exc}") from exc
        if not server_argument:
            raise AedtLaunchError("PyAEDT returned an empty gRPC server argument")
        command = [str(executable), "-grpcsrv", server_argument]
        if non_graphical:
            command.append("-ng")
        process_kwargs: dict[str, Any] = {"cwd": str(executable.parent)}
        if os.name == "nt":
            process_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            process_kwargs["start_new_session"] = True
        process = self._process_factory(command, **process_kwargs)
        deadline = self._monotonic() + float(timeout)
        target = AedtTarget("port", selected_port)
        try:
            while self._monotonic() < deadline:
                returncode = process.poll()
                if returncode is not None:
                    raise AedtLaunchError(f"AEDT exited with code {returncode} before gRPC was ready")
                try:
                    ready = bool(self._readiness_probe(selected_port))
                except AedtLaunchError:
                    raise
                except Exception:
                    ready = False
                if ready:
                    try:
                        remaining = max(0.1, min(5.0, deadline - self._monotonic()))
                        info = probe(target, remaining)
                        pid = info.get("pid") if isinstance(info, dict) else None
                        reported_port = info.get("port") if isinstance(info, dict) else None
                        connected_port = (
                            reported_port
                            if type(reported_port) is int and 0 < reported_port <= 65535
                            else selected_port
                        )
                        return {
                            "pid": pid if type(pid) is int and pid > 0 else int(process.pid),
                            "port": connected_port,
                            "requested_port": selected_port,
                            "version": version,
                            "connection_mode": "grpc",
                            "grpc_argument_mode": "legacy" if legacy_grpc_args else "pyaedt",
                            "non_graphical": bool(non_graphical),
                            "owned_by_assistant": True,
                        }
                    except Exception:
                        pass
                self._sleep(0.25)
        except Exception:
            _terminate_process(process)
            raise
        _terminate_process(process)
        raise AedtLaunchError(f"timed out after {float(timeout):g}s waiting for AEDT gRPC port {selected_port}")


def _uses_pre_grpc_args() -> bool:
    return os.environ.get("PYAEDT_USE_PRE_GRPC_ARGS", "False") == "True"


def _pyaedt_grpc_server_argument(
    port: int,
    *,
    server_args_factory: Callable[[str | None, int], Any] | None = None,
) -> str:
    if _uses_pre_grpc_args():
        return str(port)
    if server_args_factory is None:
        try:
            from ansys.aedt.core.desktop import _get_grpcsrv_args
        except ImportError as exc:
            raise AedtLaunchError(
                "PyAEDT is required to derive secure AEDT gRPC server arguments"
            ) from exc
        server_args_factory = _get_grpcsrv_args
    try:
        argument = str(server_args_factory("127.0.0.1", port)).strip()
    except Exception as exc:
        raise AedtLaunchError(f"PyAEDT could not derive gRPC server arguments: {exc}") from exc
    if not argument:
        raise AedtLaunchError("PyAEDT returned an empty gRPC server argument")
    return argument


def _pyaedt_grpc_session_ready(
    port: int,
    *,
    session_active: Callable[[int], Any] | None = None,
) -> bool:
    if session_active is None:
        try:
            from ansys.aedt.core.generic.general_methods import is_grpc_session_active
        except ImportError as exc:
            raise AedtLaunchError("PyAEDT is required to detect AEDT gRPC readiness") from exc
        session_active = is_grpc_session_active
    try:
        return bool(session_active(port))
    except Exception:
        return False


def _version_code(version: str) -> str:
    match = re.fullmatch(r"20(\d{2})\.(\d+)", str(version).strip())
    if not match:
        raise AedtLaunchError(f"unsupported AEDT version format: {version!r}")
    return f"{match.group(1)}{match.group(2)}"


def _choose_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _port_is_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (AttributeError, OSError):
            process.terminate()
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (AttributeError, OSError):
                process.kill()
        else:
            process.kill()
        process.wait(timeout=5)
