from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aedt_agent.live.broker import LiveAedtError
from aedt_agent.live.target import AedtTarget


@dataclass(frozen=True)
class AedtSessionInfo:
    """Metadata describing an attached or discovered AEDT session."""

    session_id: str
    target: AedtTarget
    pid: int | None
    port: int | None
    version: str
    connected: bool = True
    is_official_mcp: bool = False
    project_names: list[str] | None = None
    active_project: str | None = None
    active_design: str | None = None
    design_type: str | None = None


@runtime_checkable
class AedtAccess(Protocol):
    """Abstract interface for communicating with an AEDT session."""

    def execute(
        self,
        target: AedtTarget,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str = "2026.1",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute one operation against the target AEDT session."""
        ...

    def release(
        self,
        target: AedtTarget,
        *,
        version: str = "2026.1",
    ) -> dict[str, Any]:
        """Release wrappers/connections associated with the target without closing AEDT."""
        ...

    def close(self) -> None:
        """Tear down all managed connections/subprocesses."""
        ...

    def has_target(
        self,
        target: AedtTarget,
        *,
        version: str = "2026.1",
    ) -> bool:
        """Check whether the access layer currently holds an active connection for target."""
        ...
