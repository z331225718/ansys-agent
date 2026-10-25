from __future__ import annotations

import pytest

from aedt_agent.aedt_access import (
    AedtAccess,
    DirectGrpcAccess,
    LegacyBrokerAccess,
    create_aedt_access,
)
from aedt_agent.live.manager import LiveAedtSessionManager
from aedt_agent.live.target import AedtTarget


class FakeBackend:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def execute(self, target, command, arguments):
        self.calls.append((target, command, arguments))
        if command == "ping":
            return {"connected": True, "pid": 1234, "port": target.value}
        if command == "release":
            return {"released": True}
        return {"status": "ok"}


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def execute(self, target, command, arguments, *, version="2026.1", timeout=None):
        self.calls.append((target, command, arguments))
        return {"result": "legacy_ok"}

    def release(self, target, *, version="2026.1"):
        return {"released": True}

    def close(self):
        pass

    def has_target(self, target, *, version="2026.1"):
        return True


def test_factory_creates_direct_grpc_by_default(monkeypatch):
    monkeypatch.delenv("AEDT_ACCESS_MODE", raising=False)
    access = create_aedt_access()
    assert isinstance(access, DirectGrpcAccess)
    assert isinstance(access, AedtAccess)


def test_factory_creates_legacy_broker():
    with pytest.deprecated_call():
        access = create_aedt_access(mode="legacy_broker")
    assert isinstance(access, LegacyBrokerAccess)
    assert isinstance(access, AedtAccess)


def test_factory_invalid_mode_raises():
    with pytest.raises(ValueError, match="Unknown AEDT access mode"):
        create_aedt_access(mode="unsupported_mode")


def test_direct_grpc_access_execution():
    fake = FakeBackend()
    access = DirectGrpcAccess(backend_factory=lambda **kwargs: fake)
    target = AedtTarget.from_values(port=50001)

    assert access.has_target(target) is False
    res = access.execute(target, "ping", {})
    assert res["connected"] is True
    assert access.has_target(target) is True

    rel = access.release(target)
    assert rel["released"] is True
    assert access.has_target(target) is False


def test_direct_grpc_access_close():
    fake = FakeBackend()
    access = DirectGrpcAccess(backend_factory=lambda **kwargs: fake)
    target = AedtTarget.from_values(port=50002)

    access.execute(target, "ping", {})
    assert access.has_target(target) is True
    access.close()
    assert access.has_target(target) is False


def test_legacy_broker_access():
    reg = FakeRegistry()
    access = LegacyBrokerAccess(registry=reg)
    target = AedtTarget.from_values(port=50003)

    res = access.execute(target, "custom_cmd", {"k": "v"})
    assert res == {"result": "legacy_ok"}
    assert reg.calls == [(target, "custom_cmd", {"k": "v"})]
    assert access.has_target(target) is True
    assert access.release(target) == {"released": True}


def test_manager_accepts_access_parameter():
    fake = FakeBackend()
    access = DirectGrpcAccess(backend_factory=lambda **kwargs: fake)
    manager = LiveAedtSessionManager(access=access)
    assert manager.access is access
    assert manager.registry is access
