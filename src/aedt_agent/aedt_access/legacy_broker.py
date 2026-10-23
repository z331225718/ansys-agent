from __future__ import annotations

import warnings
from typing import Any

from aedt_agent.aedt_access.contracts import AedtAccess
from aedt_agent.live.broker import AedtBrokerRegistry
from aedt_agent.live.target import AedtTarget
from aedt_agent.live.versioning import DEFAULT_AEDT_VERSION


class LegacyBrokerAccess(AedtAccess):
    """Subprocess IPC broker connection (legacy mode).

    .. deprecated:: 0.2.1
        This broker access layer is frozen and retained strictly for backwards
        compatibility with legacy offline AEDT 2024 R2 server deployments.
        New deployments and live sessions should use :class:`DirectGrpcAccess`
        pointing at the official PyAEDT-MCP gRPC endpoint.
    """

    def __init__(self, registry: AedtBrokerRegistry | None = None) -> None:
        warnings.warn(
            "LegacyBrokerAccess is frozen and deprecated. Use DirectGrpcAccess for official PyAEDT-MCP sessions.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.registry = registry or AedtBrokerRegistry()

    def execute(
        self,
        target: AedtTarget,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str = DEFAULT_AEDT_VERSION,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.registry.execute(
            target,
            command,
            arguments or {},
            version=version,
            timeout=timeout,
        )

    def release(
        self,
        target: AedtTarget,
        *,
        version: str = DEFAULT_AEDT_VERSION,
    ) -> dict[str, Any]:
        return self.registry.release(target, version=version)

    def close(self) -> None:
        self.registry.close()

    def has_target(
        self,
        target: AedtTarget,
        *,
        version: str = DEFAULT_AEDT_VERSION,
    ) -> bool:
        return self.registry.has_target(target, version=version)
