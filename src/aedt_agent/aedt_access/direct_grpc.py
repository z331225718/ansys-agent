from __future__ import annotations

import threading
from typing import Any

from aedt_agent.aedt_access.contracts import AedtAccess
from aedt_agent.live.backend import LiveAedtBackend
from aedt_agent.live.target import AedtTarget
from aedt_agent.live.versioning import DEFAULT_AEDT_VERSION, normalize_aedt_version


class DirectGrpcAccess(AedtAccess):
    """Direct in-process AEDT gRPC connection.

    Connects to a running AEDT session via its gRPC port (such as an instance
    managed or attached by the official PyAEDT-MCP), avoiding subprocess IPC
    overhead and socket serialization bottlenecks.
    """

    def __init__(
        self,
        *,
        backend_factory=None,
        desktop_factory=None,
        hfss_factory=None,
        layout_factory=None,
    ) -> None:
        self._backend_factory = backend_factory or LiveAedtBackend
        self._desktop_factory = desktop_factory
        self._hfss_factory = hfss_factory
        self._layout_factory = layout_factory
        self._backends: dict[tuple[str, str], LiveAedtBackend] = {}
        self._guard = threading.RLock()

    def _backend_for(self, target: AedtTarget, version: str) -> LiveAedtBackend:
        normalized_version = normalize_aedt_version(version)
        key = (target.key, normalized_version)
        with self._guard:
            backend = self._backends.get(key)
            if backend is None:
                backend = self._backend_factory(
                    version=normalized_version,
                    desktop_factory=self._desktop_factory,
                    hfss_factory=self._hfss_factory,
                    layout_factory=self._layout_factory,
                )
                self._backends[key] = backend
            return backend

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
        backend = self._backend_for(target, normalized_version)
        return backend.execute(target, command, arguments or {})

    def release(
        self,
        target: AedtTarget,
        *,
        version: str = DEFAULT_AEDT_VERSION,
    ) -> dict[str, Any]:
        normalized_version = normalize_aedt_version(version)
        key = (target.key, normalized_version)
        with self._guard:
            backend = self._backends.pop(key, None)
            if backend is not None:
                try:
                    return backend.execute(target, "release", {})
                except Exception:
                    pass
        return {"released": True}

    def close(self) -> None:
        with self._guard:
            for (target_key, _version), backend in list(self._backends.items()):
                try:
                    target = AedtTarget.from_values(
                        pid=int(target_key.split(":")[1]) if target_key.startswith("pid:") else None,
                        port=int(target_key.split(":")[1]) if target_key.startswith("port:") else None,
                    )
                    backend.execute(target, "release", {})
                except Exception:
                    pass
            self._backends.clear()

    def has_target(
        self,
        target: AedtTarget,
        *,
        version: str = DEFAULT_AEDT_VERSION,
    ) -> bool:
        normalized_version = normalize_aedt_version(version)
        key = (target.key, normalized_version)
        with self._guard:
            return key in self._backends
