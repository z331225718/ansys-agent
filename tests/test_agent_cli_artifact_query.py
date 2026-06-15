from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from aedt_agent.agent.mission import ArtifactManifest
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def _run(
    tmp_path: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.agent.cli",
            "--db",
            str(tmp_path / "mission.db"),
            *args,
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def _registered_touchstone(tmp_path: Path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("query S parameters", [], [])
    artifact = tmp_path / "dense.s2p"
    lines = ["# GHz S MA R 50"]
    for index in range(41):
        frequency = 17.0 + index * 0.05
        magnitude = 0.5 if frequency == 18.0 else 0.05
        lines.append(
            f"{frequency:.2f} {magnitude} 0 0.9 0 0.9 0 0.05 0"
        )
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = ArtifactManifest.create(
        "artifact-1",
        mission.mission_id,
        "job",
        "job-1",
        str(artifact),
        "touchstone",
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        artifact.stat().st_size,
    )
    store.create_artifact_manifest(manifest)
    return mission, manifest


def test_cli_queries_registered_artifact_with_bounded_points(tmp_path):
    mission, artifact = _registered_touchstone(tmp_path)

    result = _run(
        tmp_path,
        "mission",
        "artifact-query",
        "--mission-id",
        mission.mission_id,
        "--artifact-ref",
        artifact.path,
        "--frequency",
        "17",
        "19",
        "--max-points",
        "8",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["point_count"] <= 8
    assert any(
        point["frequency_ghz"] == 18.0
        for point in payload["points"]
    )


def test_cli_artifact_query_rejects_unregistered_path(tmp_path):
    mission, _ = _registered_touchstone(tmp_path)
    outside = tmp_path / "outside.s2p"
    outside.write_text("# GHz S MA R 50\n", encoding="utf-8")

    result = _run(
        tmp_path,
        "mission",
        "artifact-query",
        "--mission-id",
        mission.mission_id,
        "--artifact-ref",
        str(outside),
        "--frequency",
        "0",
        "1",
    )

    assert result.returncode != 0
    assert "artifact is not registered" in result.stderr
