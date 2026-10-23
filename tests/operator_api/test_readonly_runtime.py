from __future__ import annotations

import hashlib
import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest

from aedt_agent.agent.runtime_factory import (
    create_readonly_runtime_without_workers,
    create_runtime_without_workers,
)
from aedt_agent.operator_api import OperatorService


def test_readonly_runtime_does_not_change_database_or_sidecars(tmp_path: Path) -> None:
    db_path = tmp_path / "mission #1.db"
    mission_id = _seed_static_fixture(db_path)
    _checkpoint_static_fixture(db_path)
    before = _database_file_hashes(db_path)

    readonly = create_readonly_runtime_without_workers(db_path)
    service = OperatorService(readonly)
    assert service.artifact_query_bounded(mission_id, query="artifact", limit=10)["data"]["artifacts"] == []
    assert readonly.store.list_missions(limit=10)[0].mission_id == mission_id

    assert _database_file_hashes(db_path) == before


def test_readonly_runtime_never_creates_a_missing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "absent.db"

    with pytest.raises(FileNotFoundError):
        create_readonly_runtime_without_workers(db_path)

    assert not db_path.exists()


def test_readonly_store_rejects_writes_at_the_sqlite_boundary(tmp_path: Path) -> None:
    db_path = tmp_path / "mission.db"
    create_runtime_without_workers(db_path).create_mission("existing", [], [])
    readonly = create_readonly_runtime_without_workers(db_path)

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        readonly.create_mission("must fail", [], [])


def _database_file_hashes(db_path: Path) -> dict[str, str]:
    files = [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), Path(f"{db_path}-journal")]
    return {
        file.name: hashlib.sha256(file.read_bytes()).hexdigest()
        for file in files
        if file.exists()
    }


def _checkpoint_static_fixture(db_path: Path) -> None:
    database = sqlite3.connect(db_path, isolation_level=None)
    try:
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        database.close()
    database = sqlite3.connect(db_path, isolation_level=None)
    try:
        database.execute("PRAGMA journal_mode=DELETE")
    finally:
        database.close()


def _seed_static_fixture(db_path: Path) -> str:
    program = (
        "from pathlib import Path\n"
        "from aedt_agent.agent.runtime_factory import create_runtime_without_workers\n"
        f"runtime = create_runtime_without_workers(Path({str(db_path)!r}))\n"
        "print(runtime.create_mission('readonly observation', [], []).mission_id)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
