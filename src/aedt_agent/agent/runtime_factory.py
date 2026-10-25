from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def create_runtime_without_workers(db_path: Path | str) -> AgentRuntime:
    """Create the existing authoritative runtime without registering side-effect workers."""
    return AgentRuntime(SQLiteMissionStore(db_path))


def create_readonly_runtime_without_workers(db_path: Path | str) -> AgentRuntime:
    """Open an existing mission database through SQLite's OS-enforced read-only mode."""
    return AgentRuntime(SQLiteMissionStore.open_read_only(db_path))
