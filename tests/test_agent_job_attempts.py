from __future__ import annotations

import hashlib

from aedt_agent.agent.mission import JobStatus
from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def test_successful_worker_execution_creates_succeeded_attempt(tmp_path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"ok": true}', encoding="utf-8")
    registry = InMemoryWorkerRegistry()
    registry.register("fake.artifact", lambda job, context: {"artifact_refs": [str(artifact)], "value": 7})
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.artifact", "step-1", {})

    result = runtime.execute_next_job(mission.mission_id, worker_id="worker-1")

    attempts = runtime.store.list_job_attempts(job.job_id)
    assert result.status == JobStatus.SUCCEEDED
    assert len(attempts) == 1
    assert attempts[0].status.value == "succeeded"
    assert attempts[0].attempt_number == 1
    assert attempts[0].worker_id == "worker-1"
    assert attempts[0].retry_decision == "none"


def test_failed_worker_execution_creates_failed_attempt_with_retry_decision(tmp_path):
    def fail_with_license(job, context):
        raise RuntimeError("license unavailable")

    registry = InMemoryWorkerRegistry()
    registry.register("fake.license", fail_with_license)
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.license", "step-1", {}, retry_limit=2)

    result = runtime.execute_next_job(mission.mission_id, worker_id="worker-1")

    attempts = runtime.store.list_job_attempts(job.job_id)
    assert result.status == JobStatus.FAILED
    assert len(attempts) == 1
    assert attempts[0].status.value == "failed"
    assert attempts[0].error is not None
    assert attempts[0].error["error_class"] == "license_unavailable"
    assert attempts[0].retry_decision == "retry_available"


def test_worker_artifact_refs_become_artifact_manifests(tmp_path):
    artifact = tmp_path / "model.aedt"
    content = b"aedt model"
    artifact.write_bytes(content)
    registry = InMemoryWorkerRegistry()
    registry.register("fake.artifact", lambda job, context: {"artifact_refs": [str(artifact)]})
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.artifact", "step-1", {})

    runtime.execute_next_job(mission.mission_id, worker_id="worker-1")

    manifests = runtime.store.list_artifact_manifests(mission.mission_id)
    assert len(manifests) == 1
    assert manifests[0].producer_kind == "job"
    assert manifests[0].producer_id == job.job_id
    assert manifests[0].path == str(artifact)
    assert manifests[0].kind == "aedt_project"
    assert manifests[0].sha256 == hashlib.sha256(content).hexdigest()
    assert manifests[0].size_bytes == len(content)
