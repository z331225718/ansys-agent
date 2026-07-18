from __future__ import annotations

import atexit
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
from multiprocessing.connection import Client, Connection
from typing import Any

from aedt_agent.interactive.contracts import PathSelector
from aedt_agent.interactive.layout import LayoutSessionError, LayoutSessionManager


class ProcessLayoutSessionManager:
    """Run PyEDB in a dedicated process so it cannot corrupt MCP stdio."""

    def __init__(self, *, timeout_seconds: float = 300.0) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self._connection: Connection | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        atexit.register(self.shutdown)

    def open_session(
        self,
        project_path: str,
        *,
        writable: bool = False,
        workspace: str | None = None,
        version: str = "2026.1",
        edb_backend: str = "auto",
    ) -> dict[str, Any]:
        return self._request(
            "open",
            {
                "project_path": project_path,
                "writable": writable,
                "workspace": workspace,
                "version": version,
                "edb_backend": edb_backend,
            },
        )

    def close_session(self, session_id: str) -> dict[str, Any]:
        return self._request("close", {"session_id": session_id})

    def list_paths(self, session_id: str, selector: PathSelector | None = None) -> dict[str, Any]:
        return self._request(
            "list_paths",
            {
                "session_id": session_id,
                "selector": (selector or PathSelector()).to_dict(),
            },
        )

    def preview_parameterize_width(
        self,
        session_id: str,
        *,
        selector: PathSelector,
        variable_name: str,
        variable_value: Any,
    ) -> dict[str, Any]:
        return self._request(
            "preview",
            {
                "session_id": session_id,
                "selector": selector.to_dict(),
                "variable_name": variable_name,
                "variable_value": variable_value,
            },
        )

    def apply_parameterize_width(self, session_id: str, preview_id: str) -> dict[str, Any]:
        return self._request(
            "apply",
            {"session_id": session_id, "preview_id": preview_id},
        )

    def shutdown(self) -> None:
        process = self._process
        connection = self._connection
        self._process = None
        self._connection = None
        if process is None:
            return
        if connection is not None:
            try:
                connection.send({"command": "shutdown", "payload": {}})
                if connection.poll(5.0):
                    connection.recv()
            except (BrokenPipeError, EOFError, OSError):
                pass
            finally:
                connection.close()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)

    def _request(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_started()
            assert self._connection is not None
            assert self._process is not None
            try:
                self._connection.send({"command": command, "payload": payload})
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._discard_worker()
                raise LayoutSessionError("PyEDB worker process is unavailable") from exc
            deadline = time.monotonic() + self.timeout_seconds
            while not self._connection.poll(0.1):
                if self._process.poll() is not None:
                    return_code = self._process.returncode
                    self._discard_worker()
                    raise LayoutSessionError(
                        f"PyEDB worker exited unexpectedly with code {return_code}"
                    )
                if time.monotonic() >= deadline:
                    self._discard_worker()
                    raise TimeoutError(f"PyEDB worker timed out while executing {command}")
            try:
                response = self._connection.recv()
            except EOFError as exc:
                return_code = self._process.returncode
                self._discard_worker()
                raise LayoutSessionError(
                    f"PyEDB worker exited unexpectedly with code {return_code}"
                ) from exc
        if response.get("ok"):
            return dict(response.get("result") or {})
        error = response.get("error") or {}
        raise LayoutSessionError(
            f"remote {error.get('type') or 'Error'}: {error.get('message') or 'unknown PyEDB worker error'}"
        )

    def _ensure_started(self) -> None:
        if self._process is not None and self._process.poll() is None and self._connection is not None:
            return
        if self._process is not None or self._connection is not None:
            self._discard_worker()
        host = "127.0.0.1"
        port = _available_local_port()
        authkey = secrets.token_bytes(32)
        command = [
            sys.executable,
            "-m",
            "aedt_agent.interactive.backend_service",
            "--host",
            host,
            "--port",
            str(port),
        ]
        environment = os.environ.copy()
        environment["AEDT_AGENT_LAYOUT_WORKER_AUTHKEY"] = authkey.hex()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=sys.stderr,
            stderr=sys.stderr,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._process = process
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LayoutSessionError(
                    f"PyEDB worker failed to start with code {process.returncode}"
                )
            try:
                self._connection = Client((host, port), authkey=authkey)
                return
            except (ConnectionRefusedError, OSError):
                time.sleep(0.1)
        process.terminate()
        raise TimeoutError("PyEDB worker did not accept its IPC connection")

    def _discard_worker(self) -> None:
        """Drop a failed worker so a later request cannot consume a stale response."""

        process = self._process
        connection = self._connection
        self._process = None
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        else:
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                pass


def serve_layout_worker(connection: Connection) -> None:
    _redirect_worker_stdout_to_stderr()
    manager = LayoutSessionManager()
    try:
        while True:
            try:
                request = connection.recv()
            except (EOFError, OSError):
                break
            command = str(request.get("command") or "")
            payload = dict(request.get("payload") or {})
            if command == "shutdown":
                _close_all_sessions(manager)
                connection.send({"ok": True, "result": {"shutdown": True}})
                break
            try:
                result = _dispatch_worker_request(manager, command, payload)
                connection.send({"ok": True, "result": result})
            except Exception as exc:
                connection.send(
                    {
                        "ok": False,
                        "error": {
                            "type": exc.__class__.__name__,
                            "message": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    }
                )
    finally:
        _close_all_sessions(manager)
        connection.close()


def _dispatch_worker_request(
    manager: LayoutSessionManager,
    command: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if command == "open":
        return manager.open_session(**payload)
    if command == "close":
        return manager.close_session(str(payload["session_id"]))
    if command == "list_paths":
        return manager.list_paths(
            str(payload["session_id"]),
            _selector_from_internal(payload.get("selector")),
        )
    if command == "preview":
        return manager.preview_parameterize_width(
            str(payload["session_id"]),
            selector=_selector_from_internal(payload.get("selector")),
            variable_name=str(payload["variable_name"]),
            variable_value=payload["variable_value"],
        ).to_dict()
    if command == "apply":
        return manager.apply_parameterize_width(
            str(payload["session_id"]),
            str(payload["preview_id"]),
        ).to_dict()
    raise ValueError(f"unsupported PyEDB worker command: {command}")


def _selector_from_internal(payload: dict[str, Any] | None) -> PathSelector:
    data = dict(payload or {})
    return PathSelector(
        target_width_m=data.get("target_width_m"),
        tolerance_m=float(data.get("tolerance_m", 1e-9)),
        nets=tuple(str(value) for value in data.get("nets") or ()),
        layers=tuple(str(value) for value in data.get("layers") or ()),
        primitive_ids=tuple(str(value) for value in data.get("primitive_ids") or ()),
        parameterized=data.get("parameterized"),
    ).validate()


def _close_all_sessions(manager: LayoutSessionManager) -> None:
    for session_id in list(manager._sessions):
        try:
            manager.close_session(session_id)
        except Exception:
            pass


def _redirect_worker_stdout_to_stderr() -> None:
    try:
        sys.stdout.flush()
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        sys.stdout = sys.stderr


def _available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
