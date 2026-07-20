from __future__ import annotations

import atexit
from dataclasses import dataclass, field
import os
import queue
import signal
import subprocess
import sys
import threading
from typing import Any

from aedt_agent.live.protocol import ProtocolError, WorkerRequest, WorkerResponse
from aedt_agent.live.target import AedtTarget
from aedt_agent.live.versioning import DEFAULT_AEDT_VERSION, normalize_aedt_version


class LiveAedtError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _Broker:
    process: subprocess.Popen
    responses: queue.Queue[str | None]
    version: str
    lock: threading.RLock = field(default_factory=threading.RLock)
    stopping: bool = False


class AedtBrokerRegistry:
    def __init__(
        self,
        *,
        default_timeout: float = 120.0,
        worker_module: str = "aedt_agent.live.worker",
        process_factory=subprocess.Popen,
    ) -> None:
        self.default_timeout = float(default_timeout)
        self.worker_module = worker_module
        self._process_factory = process_factory
        self._brokers: dict[tuple[str, str], _Broker] = {}
        self._guard = threading.RLock()
        atexit.register(self.close)

    def execute(
        self,
        target: AedtTarget,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str = DEFAULT_AEDT_VERSION,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        normalized_version = normalize_aedt_version(version)
        broker = self._broker_for(target, normalized_version)
        request = WorkerRequest.create(command, target, arguments or {}, timeout or self.default_timeout)
        with broker.lock:
            if broker.stopping:
                raise LiveAedtError("broker_unavailable", f"broker is stopping for {target.key}")
            if broker.process.poll() is not None or broker.process.stdin is None:
                self._discard(broker)
                raise LiveAedtError("broker_unavailable", f"broker exited for {target.key}")
            broker.process.stdin.write(request.to_json() + "\n")
            broker.process.stdin.flush()
            try:
                line = broker.responses.get(timeout=request.timeout_seconds)
            except queue.Empty as exc:
                self._stop(broker)
                raise LiveAedtError("backend_timeout", f"request timed out for {target.key}") from exc
            if line is None:
                self._discard(broker)
                raise LiveAedtError("broker_unavailable", f"broker closed for {target.key}")
            try:
                response = WorkerResponse.from_json(line)
            except ProtocolError as exc:
                self._stop(broker)
                raise LiveAedtError("protocol_error", str(exc)) from exc
            if response.request_id != request.request_id:
                self._stop(broker)
                raise LiveAedtError("protocol_error", "response request id mismatch")
            if not response.ok:
                error = response.error or {}
                code = str(error.get("code", "backend_error"))
                if code == "version_mismatch":
                    self._stop(broker)
                raise LiveAedtError(code, str(error.get("message", "worker failed")))
            result = dict(response.result or {})
            if command in {"ping", "project_info"}:
                self._register_aliases(broker, result)
            if command == "release":
                self._stop(broker)
            return result

    def release(
        self,
        target: AedtTarget,
        *,
        version: str = DEFAULT_AEDT_VERSION,
    ) -> dict[str, Any]:
        return self.execute(target, "release", {}, version=version)

    def close(self) -> None:
        with self._guard:
            brokers = list({id(value): value for value in self._brokers.values()}.values())
        for broker in brokers:
            self._stop(broker)

    @property
    def broker_count(self) -> int:
        with self._guard:
            return len(
                {
                    id(value)
                    for value in self._brokers.values()
                    if not value.stopping and value.process.poll() is None
                }
            )

    def has_target(self, target: AedtTarget, *, version: str = DEFAULT_AEDT_VERSION) -> bool:
        with self._guard:
            broker = self._brokers.get(self._key(target, version))
            return broker is not None and not broker.stopping and broker.process.poll() is None

    def _broker_for(self, target: AedtTarget, version: str = DEFAULT_AEDT_VERSION) -> _Broker:
        normalized_version = normalize_aedt_version(version)
        key = self._key(target, normalized_version)
        with self._guard:
            current = self._brokers.get(key)
            if current is not None:
                if current.stopping:
                    return current
                if current.process.poll() is None:
                    return current
            process_kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": sys.stderr,
                "text": True,
                "encoding": "utf-8",
                "errors": "strict",
                "bufsize": 1,
                "env": self._environment(),
            }
            if os.name == "nt":
                process_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                process_kwargs["start_new_session"] = True
            process = self._process_factory(
                [sys.executable, "-m", self.worker_module, "--version", normalized_version],
                **process_kwargs,
            )
            responses: queue.Queue[str | None] = queue.Queue()
            broker = _Broker(process, responses, normalized_version)
            assert process.stdout is not None
            threading.Thread(target=_read_lines, args=(process.stdout, responses), daemon=True).start()
            self._brokers[key] = broker
            return broker

    def _register_aliases(self, broker: _Broker, result: dict[str, Any]) -> None:
        aliases = []
        if type(result.get("pid")) is int and result["pid"] > 0:
            aliases.append(f"pid:{result['pid']}")
        if type(result.get("port")) is int and 0 < result["port"] <= 65535:
            aliases.append(f"port:{result['port']}")
        conflicting_broker = None
        with self._guard:
            for alias in aliases:
                existing = self._brokers.get((broker.version, alias))
                if (
                    existing is not None
                    and existing is not broker
                    and not existing.stopping
                    and existing.process.poll() is None
                ):
                    conflicting_broker = existing
                    break
            if conflicting_broker is not None:
                for key, value in list(self._brokers.items()):
                    if value is broker:
                        self._brokers[key] = conflicting_broker
            for alias in aliases:
                key = (broker.version, alias)
                existing = self._brokers.get(key)
                if conflicting_broker is not None:
                    if (
                        existing is None
                        or existing is broker
                        or existing.stopping
                        or existing.process.poll() is not None
                    ):
                        self._brokers[key] = conflicting_broker
                elif (
                    existing is None
                    or existing is broker
                    or existing.stopping
                    or existing.process.poll() is not None
                ):
                    self._brokers[key] = broker
        if conflicting_broker is not None:
            self._stop(broker)

    @staticmethod
    def _key(target: AedtTarget, version: str) -> tuple[str, str]:
        return normalize_aedt_version(version), target.key

    def _stop(self, broker: _Broker) -> None:
        with self._guard:
            broker.stopping = True
        with broker.lock:
            process = broker.process
            if process.poll() is None:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        _terminate_process_tree(process, signal.SIGKILL)
                        process.wait(timeout=3)
            self._discard(broker)

    def _discard(self, broker: _Broker) -> None:
        with self._guard:
            for key, value in list(self._brokers.items()):
                if value is broker:
                    del self._brokers[key]

    @staticmethod
    def _environment() -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PYTHONSTARTUP", None)
        env.pop("PYTHONINSPECT", None)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env


def _read_lines(stream, responses: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            responses.put(line.rstrip("\r\n"))
    finally:
        responses.put(None)


def _terminate_process_tree(process: Any, sig: int) -> None:
    if os.name != "nt":
        try:
            os.killpg(process.pid, sig)
            return
        except (AttributeError, OSError):
            pass
    if sig == signal.SIGKILL:
        process.kill()
    else:
        process.terminate()
