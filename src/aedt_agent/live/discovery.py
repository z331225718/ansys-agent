from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable


def list_aedt_sessions(
    *,
    process_iter: Callable[[list[str]], Iterable[Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("psutil is required for live AEDT discovery") from exc
    iterator = process_iter or psutil.process_iter
    sessions = []
    for process in iterator(["pid", "name", "exe", "create_time", "cmdline"]):
        try:
            info = process.info
            if str(info.get("name") or "").lower() not in _aedt_process_names():
                continue
            ports = []
            for connection in process.net_connections(kind="tcp"):
                status = str(getattr(connection, "status", ""))
                address = getattr(connection, "laddr", None)
                if status == "LISTEN" and address:
                    port = int(address.port if hasattr(address, "port") else address[1])
                    if port > 0:
                        ports.append(port)
            command = [str(item) for item in info.get("cmdline") or []]
            grpc_port = _command_port(command)
            executable = info.get("exe")
            sessions.append(
                {
                    "pid": int(info["pid"]),
                    "ports": sorted(set(ports)),
                    "grpc_port": grpc_port,
                    "executable": executable,
                    "version": _version_from_path(executable),
                    "create_time": info.get("create_time"),
                }
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return sorted(sessions, key=lambda item: item["pid"])


def _command_port(command: list[str]) -> int | None:
    for index, item in enumerate(command[:-1]):
        if item.lower() == "-grpcsrv":
            try:
                value = command[index + 1]
                port = int(value.rsplit(":", 1)[-1])
            except ValueError:
                return None
            return port if 0 < port <= 65535 else None
    return None


def _aedt_process_names() -> set[str]:
    return {"ansysedt.exe"} if os.name == "nt" else {"ansysedt", "ansysedt.exe"}


def _version_from_path(executable: Any) -> str | None:
    if not isinstance(executable, str):
        return None
    match = re.search(r"(?:^|[\\/])v(\d{2})(\d)(?:[\\/]|$)", str(Path(executable)), flags=re.IGNORECASE)
    return None if match is None else f"20{match.group(1)}.{match.group(2)}"
