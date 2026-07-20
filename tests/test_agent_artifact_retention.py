from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aedt_agent.agent.artifact_retention import prune_mission_artifacts
from aedt_agent.agent.mission import (
    ArtifactManifest,
    EventType,
    MissionState,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path) -> tuple[AgentRuntime, str]:
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"))
    mission = runtime.create_mission("artifact retention", [], [])
    return runtime, mission.mission_id


def _complete_mission(runtime: AgentRuntime, mission_id: str) -> None:
    runtime.store.update_mission_state(mission_id, MissionState.PLANNING)
    runtime.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)
    runtime.store.update_mission_state(mission_id, MissionState.EVALUATING)
    runtime.store.update_mission_state(mission_id, MissionState.COMPLETED)


def _register(
    runtime: AgentRuntime,
    mission_id: str,
    path: Path,
    *,
    policy: str = "mission",
    pinned: bool = False,
    age_hours: float = 48.0,
    sha256: str | None = None,
    size_bytes: int | None = None,
) -> ArtifactManifest:
    digest = (
        hashlib.sha256(path.read_bytes()).hexdigest()
        if sha256 is None and path.is_file()
        else ("missing" if sha256 is None else sha256)
    )
    size = (
        path.stat().st_size
        if size_bytes is None and path.is_file()
        else (0 if size_bytes is None else size_bytes)
    )
    manifest = ArtifactManifest(
        artifact_id=str(uuid4()),
        mission_id=mission_id,
        producer_kind="job",
        producer_id="job-1",
        path=str(path),
        kind="artifact",
        sha256=digest,
        size_bytes=size,
        created_at=(datetime.now(UTC) - timedelta(hours=age_hours)).isoformat(),
        metadata={"pinned": pinned},
        retention_policy=policy,
    )
    return runtime.store.create_artifact_manifest(manifest)


def test_artifact_prune_dry_run_has_zero_side_effects(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "old.bin"
    artifact.write_bytes(b"old")
    _register(runtime, mission_id, artifact)
    _complete_mission(runtime, mission_id)
    event_count = len(runtime.list_events(mission_id))

    report = prune_mission_artifacts(runtime, mission_id, root)

    assert report["status"] == "dry_run"
    assert report["candidate_count"] == 1
    assert report["candidate_bytes"] == 3
    assert report["deleted_count"] == 0
    assert artifact.is_file()
    assert len(runtime.list_events(mission_id)) == event_count


def test_artifact_prune_apply_deletes_file_and_appends_audit_events(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "old.bin"
    artifact.write_bytes(b"delete-me")
    manifest = _register(runtime, mission_id, artifact, policy="transient")
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["status"] == "applied"
    assert report["deleted_count"] == 1
    assert report["deleted_bytes"] == len(b"delete-me")
    assert not artifact.exists()
    assert runtime.store.list_artifact_manifests(mission_id)[0].artifact_id == manifest.artifact_id
    retention_events = [
        event
        for event in runtime.list_events(mission_id)
        if event.event_type
        in {
            EventType.ARTIFACT_RETENTION_PLANNED,
            EventType.ARTIFACT_RETENTION_APPLIED,
            EventType.ARTIFACT_RETENTION_FAILED,
        }
    ]
    assert [event.event_type for event in retention_events] == [
        EventType.ARTIFACT_RETENTION_PLANNED,
        EventType.ARTIFACT_RETENTION_APPLIED,
    ]
    assert retention_events[0].payload["artifact_id"] == manifest.artifact_id
    assert retention_events[0].payload["hash"] == manifest.sha256
    assert retention_events[0].payload["bytes"] == manifest.size_bytes


def test_artifact_prune_delete_failure_appends_failed_event(tmp_path, monkeypatch):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "locked.bin"
    artifact.write_bytes(b"locked")
    _register(runtime, mission_id, artifact)
    _complete_mission(runtime, mission_id)
    original_unlink = Path.unlink

    def fail_selected(path: Path, *args, **kwargs):
        if path == artifact:
            raise PermissionError("locked for test")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["status"] == "partial_failure"
    assert report["deleted_count"] == 0
    assert report["failures"][0]["reason"] == "delete_failed"
    retention_events = [
        event.event_type
        for event in runtime.list_events(mission_id)
        if event.event_type.value.startswith("artifact_retention_")
    ]
    assert retention_events == [
        EventType.ARTIFACT_RETENTION_PLANNED,
        EventType.ARTIFACT_RETENTION_FAILED,
    ]
    assert artifact.is_file()


def test_artifact_prune_rechecks_integrity_after_planned_event(tmp_path, monkeypatch):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "changed.bin"
    artifact.write_bytes(b"stable")
    _register(runtime, mission_id, artifact)
    _complete_mission(runtime, mission_id)
    original_append = runtime.store.append_event

    def append_and_tamper(mission, event_type, payload):
        event = original_append(mission, event_type, payload)
        if event_type == EventType.ARTIFACT_RETENTION_PLANNED:
            artifact.write_bytes(b"change")
        return event

    monkeypatch.setattr(runtime.store, "append_event", append_and_tamper)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["status"] == "partial_failure"
    assert report["deleted_count"] == 0
    assert report["failures"][0]["reason"] == "predelete_verification_failed"
    assert report["failures"][0]["message"] == "sha256_mismatch"
    assert artifact.is_file()


def test_artifact_prune_blocks_nonterminal_mission_without_events(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "old.bin"
    artifact.write_bytes(b"keep")
    _register(runtime, mission_id, artifact)
    event_count = len(runtime.list_events(mission_id))

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "mission_not_terminal"
    assert artifact.is_file()
    assert len(runtime.list_events(mission_id)) == event_count


def test_artifact_prune_respects_keep_pin_age_and_unknown_policy(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    cases = [
        ("keep.bin", "keep", False, 48.0),
        ("pinned.bin", "mission", True, 48.0),
        ("recent.bin", "mission", False, 1.0),
        ("unknown.bin", "forever-ish", False, 48.0),
        ("candidate.bin", "mission", False, 48.0),
    ]
    for name, policy, pinned, age_hours in cases:
        path = root / name
        path.write_bytes(name.encode("ascii"))
        _register(
            runtime,
            mission_id,
            path,
            policy=policy,
            pinned=pinned,
            age_hours=age_hours,
        )
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root)

    assert [Path(item["path"]).name for item in report["candidates"]] == [
        "candidate.bin"
    ]
    assert {item["reason"] for item in report["skipped"]} == {
        "retention_keep",
        "metadata_pinned",
        "younger_than_threshold",
        "retention_policy_unknown",
    }


def test_artifact_prune_keep_manifest_protects_old_duplicate_path(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "shared.bin"
    artifact.write_bytes(b"shared")
    (root / "nested").mkdir()
    _register(runtime, mission_id, artifact, policy="mission")
    _register(runtime, mission_id, root / "nested" / ".." / "shared.bin", policy="keep")
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["candidate_count"] == 0
    assert report["deleted_count"] == 0
    assert artifact.is_file()
    assert {item["reason"] for item in report["skipped"]} == {
        "retention_keep",
        "path_protected_by_manifest",
    }
    assert not any(
        event.event_type.value.startswith("artifact_retention_")
        for event in runtime.list_events(mission_id)
    )


def test_artifact_prune_pinned_or_newer_manifest_protects_old_path(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    pinned = root / "pinned-shared.bin"
    pinned.write_bytes(b"pinned")
    newer = root / "newer-shared.bin"
    newer.write_bytes(b"newer")
    _register(runtime, mission_id, pinned)
    _register(runtime, mission_id, pinned, pinned=True)
    _register(runtime, mission_id, newer)
    _register(runtime, mission_id, newer, age_hours=1.0)
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["candidate_count"] == 0
    assert report["deleted_count"] == 0
    assert pinned.is_file()
    assert newer.is_file()
    reasons = [item["reason"] for item in report["skipped"]]
    assert reasons.count("path_protected_by_manifest") == 2
    assert "metadata_pinned" in reasons
    assert "younger_than_threshold" in reasons


def test_artifact_prune_consistent_duplicate_manifests_delete_once(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "duplicate.bin"
    artifact.write_bytes(b"duplicate")
    (root / "folder").mkdir()
    first = _register(runtime, mission_id, artifact)
    _register(runtime, mission_id, root / "folder" / ".." / "duplicate.bin")
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["candidate_count"] == 1
    assert report["deleted_count"] == 1
    assert not artifact.exists()
    assert [item["reason"] for item in report["skipped"]] == [
        "duplicate_manifest_path"
    ]
    retention_events = [
        event
        for event in runtime.list_events(mission_id)
        if event.event_type.value.startswith("artifact_retention_")
    ]
    assert [event.event_type for event in retention_events] == [
        EventType.ARTIFACT_RETENTION_PLANNED,
        EventType.ARTIFACT_RETENTION_APPLIED,
    ]
    assert retention_events[0].payload["artifact_id"] == first.artifact_id


def test_artifact_prune_conflicting_duplicate_integrity_protects_path(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "conflict.bin"
    artifact.write_bytes(b"conflict")
    _register(runtime, mission_id, artifact)
    _register(runtime, mission_id, artifact, sha256="0" * 64, size_bytes=999)
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["candidate_count"] == 0
    assert report["deleted_count"] == 0
    assert artifact.is_file()
    assert {item["reason"] for item in report["skipped"]} == {
        "size_mismatch",
        "path_protected_by_manifest",
    }


def test_artifact_prune_outside_group_does_not_protect_inside_path(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    inside = root / "inside.bin"
    inside.write_bytes(b"inside")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    _register(runtime, mission_id, inside)
    _register(runtime, mission_id, outside, policy="keep")
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["candidate_count"] == 1
    assert report["deleted_count"] == 1
    assert not inside.exists()
    assert outside.is_file()
    assert [item["reason"] for item in report["skipped"]] == [
        "retention_keep"
    ]


def test_artifact_prune_skips_escape_missing_and_integrity_mismatch(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    missing = root / "missing.bin"
    hash_mismatch = root / "hash.bin"
    hash_mismatch.write_bytes(b"hash")
    hash_missing = root / "hash-missing.bin"
    hash_missing.write_bytes(b"hash")
    size_mismatch = root / "size.bin"
    size_mismatch.write_bytes(b"size")
    directory = root / "directory"
    directory.mkdir()
    _register(runtime, mission_id, outside)
    _register(runtime, mission_id, missing, sha256="abc", size_bytes=1)
    _register(runtime, mission_id, hash_mismatch, sha256="0" * 64)
    _register(runtime, mission_id, hash_missing, sha256="")
    _register(runtime, mission_id, size_mismatch, size_bytes=999)
    _register(runtime, mission_id, directory, sha256="abc", size_bytes=0)
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["candidate_count"] == 0
    assert report["deleted_count"] == 0
    assert {item["reason"] for item in report["skipped"]} == {
        "path_outside_root_lexical",
        "path_missing",
        "sha256_mismatch",
        "sha256_missing",
        "size_mismatch",
        "path_not_regular_file",
    }
    assert outside.is_file()


def test_artifact_prune_skips_symlink_or_reparse_path(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    target = tmp_path / "target.bin"
    target.write_bytes(b"target")
    link = root / "linked.bin"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    _register(runtime, mission_id, link)
    _complete_mission(runtime, mission_id)

    report = prune_mission_artifacts(runtime, mission_id, root, apply=True)

    assert report["candidate_count"] == 0
    assert report["skipped"][0]["reason"] == "path_has_reparse_component"
    assert target.is_file()
    assert link.exists()


def test_artifact_prune_cli_defaults_to_dry_run_and_apply_is_explicit(tmp_path):
    runtime, mission_id = _runtime(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "old.bin"
    artifact.write_bytes(b"cli")
    _register(runtime, mission_id, artifact)
    _complete_mission(runtime, mission_id)
    command = [
        sys.executable,
        "-m",
        "aedt_agent.agent.cli",
        "--db",
        str(tmp_path / "mission.db"),
        "mission",
        "artifact-prune",
        "--mission-id",
        mission_id,
        "--root",
        str(root),
    ]

    dry_run = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=False)
    applied = subprocess.run(
        [*command, "--apply"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert dry_run.returncode == 0
    assert json.loads(dry_run.stdout)["status"] == "dry_run"
    assert applied.returncode == 0
    assert json.loads(applied.stdout)["status"] == "applied"
    assert not artifact.exists()
