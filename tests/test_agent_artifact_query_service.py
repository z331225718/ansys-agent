from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aedt_agent.agent.evaluation import ArtifactQueryService
from aedt_agent.agent.mission import ArtifactManifest, EventType
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def _fixture(tmp_path: Path):
    store = SQLiteMissionStore(tmp_path / "missions.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("query artifact", [], [])
    artifact_path = tmp_path / "channel.s2p"
    artifact_path.write_text(
        "# GHz S MA R 50\n"
        "17 0.05 0 0.9 0\n"
        "18 0.5 0 0.8 0\n"
        "19 0.05 0 0.9 0\n",
        encoding="utf-8",
    )
    artifact = ArtifactManifest.create(
        "artifact-1",
        mission.mission_id,
        "job",
        "job-1",
        str(artifact_path),
        "touchstone",
        hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        artifact_path.stat().st_size,
    )
    store.create_artifact_manifest(artifact)
    return store, mission, artifact


def test_query_service_rejects_unregistered_artifact(tmp_path):
    store, mission, _ = _fixture(tmp_path)
    outside = tmp_path / "outside.s2p"
    outside.write_text("# GHz S MA R 50\n", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact is not registered"):
        ArtifactQueryService(store).query_sparameter(
            mission.mission_id,
            str(outside),
            0,
            1,
        )


def test_query_service_records_bounded_query_event(tmp_path):
    store, mission, artifact = _fixture(tmp_path)

    result = ArtifactQueryService(store).query_sparameter(
        mission.mission_id,
        artifact.path,
        17,
        19,
        max_points=8,
    )

    event = store.list_events(mission.mission_id)[-1]
    assert event.event_type == EventType.ARTIFACT_QUERY_COMPLETED
    assert event.payload["artifact_id"] == artifact.artifact_id
    assert event.payload["point_count"] == result["point_count"]
    assert "points" not in event.payload
