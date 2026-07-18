from __future__ import annotations

from io import StringIO
import inspect
import sys
import threading
from types import SimpleNamespace

import pytest

from aedt_agent.live.backend import AedtVersionMismatchError, LiveAedtBackend
from aedt_agent.live.broker import AedtBrokerRegistry, LiveAedtError
from aedt_agent.live.manager import LiveAedtSessionManager
from aedt_agent.live.target import AedtTarget
from aedt_agent.live import worker


class _Desktop:
    aedt_process_id = 42
    port = 50061
    project_list = ["Board"]

    def __init__(self, reported_version: str, **kwargs):
        self.kwargs = kwargs
        self.aedt_version_id = kwargs["version"]
        self.odesktop = SimpleNamespace(GetVersion=lambda: reported_version)
        self.releases = []

    def release_desktop(self, **kwargs):
        self.releases.append(kwargs)
        return True


def test_backend_uses_connected_desktop_version_and_rejects_mismatch():
    matching = _Desktop("2024.2.0", version="2024.2")
    backend = LiveAedtBackend(
        version="2024.2",
        desktop_factory=lambda **kwargs: matching,
    )
    probe = backend.execute(AedtTarget("port", 50061), "ping", {})
    assert matching.kwargs["version"] == "2024.2"
    assert probe["version"] == "2024.2"
    assert probe["requested_version"] == "2024.2"
    assert probe["version_verified"] is True

    mismatching = _Desktop("2025.1.0", version="2024.2")
    wrong_backend = LiveAedtBackend(
        version="2024.2",
        desktop_factory=lambda **kwargs: mismatching,
    )
    with pytest.raises(AedtVersionMismatchError, match="reports version 2025.1") as error:
        wrong_backend.execute(AedtTarget("port", 50061), "ping", {})
    assert error.value.code == "version_mismatch"
    assert mismatching.releases == [{"close_projects": False, "close_on_exit": False}]


def test_backend_skips_unparseable_version_candidates_and_marks_unknown_unverified():
    prefixed = _Desktop("Ansys Electronics Desktop Version 2024.2.0", version="2024.2")
    backend = LiveAedtBackend(
        version="2024.2",
        desktop_factory=lambda **kwargs: prefixed,
    )
    probe = backend.execute(AedtTarget("port", 50061), "ping", {})
    assert probe["version"] == "2024.2"
    assert probe["version_verified"] is True

    prefixed_mismatch = _Desktop("Ansys Electronics Desktop Version 2025.1.0", version="2024.2")
    mismatch_backend = LiveAedtBackend(
        version="2024.2",
        desktop_factory=lambda **kwargs: prefixed_mismatch,
    )
    with pytest.raises(AedtVersionMismatchError):
        mismatch_backend.execute(AedtTarget("port", 50061), "ping", {})

    unknown = _Desktop("not-a-release", version="2024.2")
    unknown.aedt_version_id = "also-not-a-release"
    unknown.desktop_version = None
    unknown_backend = LiveAedtBackend(
        version="2024.2",
        desktop_factory=lambda **kwargs: unknown,
    )
    unknown_probe = unknown_backend.execute(AedtTarget("port", 50061), "ping", {})
    assert unknown_probe["version"] == "2024.2"
    assert unknown_probe["version_verified"] is False


class _VersionRegistry:
    def __init__(self, *, reported_version: str | None = None, verified: bool = True):
        self.reported_version = reported_version
        self.verified = verified
        self.targets: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str, str]] = []

    def execute(self, target, command, arguments, *, version="2026.1", **kwargs):
        self.calls.append((command, target.key, version))
        if command == "ping":
            self.targets.add((target.key, version))
            return {
                "connected": True,
                "pid": 42,
                "port": 50061,
                "version": self.reported_version or version,
                "requested_version": version,
                "version_verified": self.verified,
            }
        return {"command": command, "version": version}

    def has_target(self, target, *, version="2026.1"):
        return (target.key, version) in self.targets

    def release(self, target, *, version="2026.1"):
        self.calls.append(("release", target.key, version))
        self.targets.discard((target.key, version))
        return {"released": True}

    def close(self):
        self.targets.clear()


def test_manager_keeps_same_target_versions_isolated_during_reuse_and_release():
    registry = _VersionRegistry()
    manager = LiveAedtSessionManager(registry=registry)
    first_242 = manager.attach(port=50061, version="2024.2")
    second_242 = manager.attach(port=50061, version="2024.2.0")
    first_261 = manager.attach(port=50061, version="2026.1")

    assert first_242["reused_broker"] is False
    assert second_242["reused_broker"] is True
    assert first_261["reused_broker"] is False
    assert registry.targets == {("port:50061", "2024.2"), ("port:50061", "2026.1")}

    manager.release(first_242["live_session_id"])
    with pytest.raises(LiveAedtError) as released_alias:
        manager.project_info(second_242["live_session_id"])
    assert released_alias.value.code == "session_not_found"
    assert manager.project_info(first_261["live_session_id"])["version"] == "2026.1"
    assert registry.targets == {("port:50061", "2026.1")}


def test_manager_release_invalidates_pid_and_port_sessions_for_same_broker():
    registry = _VersionRegistry()
    manager = LiveAedtSessionManager(registry=registry)
    by_port = manager.attach(port=50061, version="2024.2")
    by_pid = manager.attach(pid=42, version="2024.2")

    manager.release(by_port["live_session_id"])

    with pytest.raises(LiveAedtError) as stale_alias:
        manager.project_info(by_pid["live_session_id"])
    assert stale_alias.value.code == "session_not_found"


def test_strict_manager_rejects_requested_actual_and_unverified_version_conflicts():
    expected = LiveAedtSessionManager(
        registry=_VersionRegistry(),
        required_version="2024 R2",
        strict_desktop=True,
    )
    with pytest.raises(LiveAedtError) as forbidden:
        expected.attach(port=50061, version="2026.1")
    assert forbidden.value.code == "version_forbidden"

    wrong_registry = _VersionRegistry(reported_version="2025.1")
    wrong = LiveAedtSessionManager(
        registry=wrong_registry,
        required_version="2024.2",
        strict_desktop=True,
    )
    with pytest.raises(LiveAedtError) as mismatch:
        wrong.attach(port=50061, version="2024.2")
    assert mismatch.value.code == "version_mismatch"
    assert wrong_registry.calls[-1] == ("release", "port:50061", "2024.2")

    unverified = LiveAedtSessionManager(
        registry=_VersionRegistry(verified=False),
        required_version="2024.2",
        strict_desktop=True,
    )
    with pytest.raises(LiveAedtError) as missing_evidence:
        unverified.attach(port=50061, version="2024.2")
    assert missing_evidence.value.code == "version_unverified"

    with pytest.raises(ValueError, match="required_version"):
        LiveAedtSessionManager(registry=_VersionRegistry(), strict_desktop=True)


def test_strict_attach_failure_does_not_release_a_reused_broker():
    registry = _VersionRegistry(verified=False)
    registry.targets.add(("port:50061", "2024.2"))
    manager = LiveAedtSessionManager(
        registry=registry,
        required_version="2024.2",
        strict_desktop=True,
    )

    with pytest.raises(LiveAedtError) as missing_evidence:
        manager.attach(port=50061, version="2024.2")

    assert missing_evidence.value.code == "version_unverified"
    assert ("port:50061", "2024.2") in registry.targets
    assert not any(call[0] == "release" for call in registry.calls)


class _Process:
    def __init__(self):
        self.stdin = StringIO()
        self.stdout = StringIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 0


def test_broker_registry_keys_workers_and_aliases_by_target_and_version():
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        return _Process()

    registry = AedtBrokerRegistry(process_factory=start)
    target = AedtTarget("port", 50061)
    broker_242 = registry._broker_for(target, "2024.2")
    assert registry._broker_for(target, "2024.2.0") is broker_242
    broker_261 = registry._broker_for(target, "2026.1")

    assert broker_261 is not broker_242
    assert registry.broker_count == 2
    assert commands[0][-2:] == ["--version", "2024.2"]
    assert commands[1][-2:] == ["--version", "2026.1"]

    registry._register_aliases(broker_242, {"pid": 42, "port": 50061})
    assert registry.has_target(AedtTarget("pid", 42), version="2024.2") is True
    assert registry.has_target(AedtTarget("pid", 42), version="2026.1") is False

    registry._stop(broker_242)
    assert registry.has_target(target, version="2024.2") is False
    assert registry.has_target(target, version="2026.1") is True
    assert registry.broker_count == 1
    registry.close()


def test_broker_registry_collapses_concurrent_pid_and_port_alias_workers():
    registry = AedtBrokerRegistry(process_factory=lambda command, **kwargs: _Process())
    by_port = registry._broker_for(AedtTarget("port", 50061), "2024.2")
    by_pid = registry._broker_for(AedtTarget("pid", 42), "2024.2")
    assert registry.broker_count == 2

    registry._register_aliases(by_port, {"pid": 42, "port": 50061})

    assert by_port.process.poll() == 0
    assert registry.broker_count == 1
    assert registry._broker_for(AedtTarget("port", 50061), "2024.2") is by_pid
    assert registry._broker_for(AedtTarget("pid", 42), "2024.2") is by_pid
    registry.close()


def test_broker_registry_does_not_replace_a_worker_while_it_is_stopping():
    wait_started = threading.Event()
    finish_wait = threading.Event()
    processes = []

    class BlockingProcess(_Process):
        def wait(self, timeout=None):
            wait_started.set()
            assert finish_wait.wait(timeout=1)
            self.returncode = 0
            return 0

    def start(command, **kwargs):
        process = BlockingProcess()
        processes.append(process)
        return process

    registry = AedtBrokerRegistry(process_factory=start)
    target = AedtTarget("port", 50061)
    broker = registry._broker_for(target, "2024.2")
    stopping = threading.Thread(target=registry._stop, args=(broker,))
    stopping.start()
    assert wait_started.wait(timeout=1)

    assert registry._broker_for(target, "2024.2") is broker
    assert len(processes) == 1
    assert registry.broker_count == 0

    finish_wait.set()
    stopping.join(timeout=1)
    assert not stopping.is_alive()
    assert registry.has_target(target, version="2024.2") is False


def test_worker_cli_passes_explicit_normalized_version(monkeypatch):
    seen = []
    monkeypatch.setattr(
        worker,
        "serve",
        lambda input_stream, output_stream, *, version: seen.append(version) or 0,
    )
    assert worker.main(["--version", "2024.2.0"]) == 0
    assert seen == ["2024.2"]


def test_strict_interactive_server_consumes_expected_desktop_version(monkeypatch):
    class _FastMCP:
        def __init__(self, *args, **kwargs):
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    captured = []
    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_FastMCP))
    monkeypatch.setenv("AEDT_AGENT_EXPECTED_PORT", "50061")
    monkeypatch.setenv("AEDT_AGENT_EXPECTED_PROJECT", "Board")
    monkeypatch.setenv("AEDT_AGENT_EXPECTED_DESIGN", "Layout1")
    monkeypatch.setenv("AEDT_AGENT_EXPECTED_VERSION", "2024.2")
    monkeypatch.setenv("AEDT_AGENT_DESKTOP_STRICT", "1")

    from aedt_agent.interactive import server as server_module

    monkeypatch.setattr(
        server_module,
        "LiveAedtSessionManager",
        lambda **kwargs: captured.append(kwargs) or SimpleNamespace(),
    )
    server_module.create_server(kernel=SimpleNamespace())
    assert captured == [
        {
            "required_port": 50061,
            "required_project": "Board",
            "required_design": "Layout1",
            "required_version": "2024.2",
            "strict_desktop": True,
        }
    ]
    server = server_module.create_server(
        kernel=SimpleNamespace(list_capabilities=lambda: {"capabilities": []}),
        live_manager=SimpleNamespace(),
    )
    attach = server.tools["attach_live_aedt_session"]
    assert inspect.signature(attach).parameters["version"].default == "2024.2"
