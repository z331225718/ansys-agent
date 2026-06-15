from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.mission import ErrorClass, JobStatus
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.infrastructure.harness import (
    HarnessWorkspacePolicy,
    LocalProcessHarness,
    ResourceGate,
)


def _runtime(tmp_path: Path, monkeypatch) -> tuple[AgentRuntime, InMemoryWorkerRegistry]:
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    harness = LocalProcessHarness(
        HarnessWorkspacePolicy(tmp_path / "harness"),
        resource_gate=ResourceGate(
            max_concurrent_cpu=2,
            max_concurrent_aedt=1,
            max_concurrent_license_jobs=1,
        ),
    )
    registry = InMemoryWorkerRegistry(harness=harness, heartbeat_interval_seconds=1)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry), registry


def test_runtime_executes_local_process_job_and_persists_harness_artifacts(tmp_path, monkeypatch):
    runtime, registry = _runtime(tmp_path, monkeypatch)
    registry.register_process(
        "fake.echo",
        "tests.fixtures.process_workers:echo_worker",
        allowed_env=("PYTHONPATH",),
    )
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "echo:1", {"value": 2})

    result = runtime.execute_job(job.job_id, "worker-1")

    attempt = runtime.store.list_job_attempts(job.job_id)[0]
    manifests = runtime.store.list_artifact_manifests(mission.mission_id)
    assert result.status == JobStatus.SUCCEEDED
    assert result.output_payload == {"value": 3}
    assert attempt.metadata["harness_run_id"]
    assert Path(attempt.metadata["workspace"]).is_dir()
    assert {"request.json", "result.json", "stdout.log", "stderr.log"} <= {
        Path(item.path).name for item in manifests
    }


def test_runtime_requeues_retryable_harness_timeout(tmp_path, monkeypatch):
    runtime, registry = _runtime(tmp_path, monkeypatch)
    registry.register_process(
        "fake.slow",
        "tests.fixtures.process_workers:sleep_worker",
        allowed_env=("PYTHONPATH",),
    )
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(
        mission.mission_id,
        "fake.slow",
        "slow:1",
        {"sleep_seconds": 60},
        timeout_seconds=1,
        retry_limit=1,
    )

    result = runtime.execute_job(job.job_id, "worker-1")

    attempt = runtime.store.list_job_attempts(job.job_id)[0]
    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == ErrorClass.TIMEOUT
    assert runtime.get_job(job.job_id).status == JobStatus.QUEUED
    assert attempt.retry_decision == "retry_available"
    assert attempt.metadata["termination_reason"] == "wall_timeout"
