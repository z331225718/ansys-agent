from __future__ import annotations

import os
from typing import Any

from aedt_agent.aedt_access.contracts import AedtAccess
from aedt_agent.aedt_access.direct_grpc import DirectGrpcAccess
from aedt_agent.aedt_access.legacy_broker import LegacyBrokerAccess


def create_aedt_access(
    mode: str | None = None,
    *,
    backend_factory=None,
    desktop_factory=None,
    registry=None,
) -> AedtAccess:
    """Create an AEDT access provider based on requested mode or environment.

    Supported modes:
    - "direct_grpc" (default): In-process direct connection to the AEDT gRPC port.
    - "legacy_broker": Deprecated child-process broker connection.
    """
    effective_mode = mode or os.environ.get("AEDT_ACCESS_MODE", "").strip().lower() or "direct_grpc"
    if effective_mode in {"direct", "direct_grpc", "official", "grpc"}:
        return DirectGrpcAccess(
            backend_factory=backend_factory,
            desktop_factory=desktop_factory,
        )
    if effective_mode in {"legacy", "legacy_broker", "broker"}:
        return LegacyBrokerAccess(registry=registry)
    raise ValueError(f"Unknown AEDT access mode: {effective_mode}. Supported: direct_grpc, legacy_broker")
