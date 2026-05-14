from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from uuid import uuid4

from aedt_agent.mcp.types import SessionRef


class AedtAdapter(Protocol):
    def health_check(self) -> bool: ...

    def execute_node_callable(self, fn: Callable[[Any], dict[str, Any] | None]) -> dict[str, Any]: ...

    def snapshot_state(self) -> dict[str, Any]: ...

    def release(self) -> None: ...


@dataclass
class ManagedSession:
    ref: SessionRef
    adapter: AedtAdapter


class SessionManager:
    def __init__(self, adapter_factory: Callable[[str, str], AedtAdapter]):
        self._adapter_factory = adapter_factory
        self._sessions: dict[str, ManagedSession] = {}

    def create_session(self, project_id: str, design_id: str) -> ManagedSession:
        session_id = f"session-{uuid4().hex}"
        session = ManagedSession(
            ref=SessionRef(session_id=session_id, project_id=project_id, design_id=design_id),
            adapter=self._adapter_factory(project_id, design_id),
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> ManagedSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown session_id: {session_id}") from exc

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self.get_session(session_id).adapter.snapshot_state()

    def release_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        session.adapter.release()
        self._sessions.pop(session_id, None)
