from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aedt_agent.agent.graph_runner import create_graph_run, resume_graph
from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.mission import (
    JobAttemptRecord,
    JobAttemptStatus,
    JobStatus,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.infrastructure.harness import (
    HARNESS_PROTOCOL_VERSION,
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
    HarnessWorkspacePolicy,
    LocalProcessHarness,
)
from aedt_agent.infrastructure.harness.recovery import (
    HarnessRecoveryClassification,
    HarnessRecoveryScanner,
)


class FakeProcessController:
    def __init__(self, alive_pids: set[int]):
        self.alive_pids = set(alive_pids)
        self.terminated_pids: list[int] = []

    def is_alive(self, pid: int) -> bool:
        return pid in self.alive_pids

    def terminate_pid_tree(self, pid: int, grace_seconds: float) -> None:
        self.terminated_pids.append(pid)
        self.alive_pids.discard(pid)


def _write_attempt(
    root: Path,
    *,
    mission_id: str = "mission-1",
    job_id: str = "job-1",
    attempt_id: str = "attempt-1",
    pid: int = 999999,
    heartbeat_age_seconds: int = 120,
    result: HarnessResult | None = None,
) -> Path:
    policy = HarnessWorkspacePolicy(root)
    workspace = policy.create_attempt(mission_id, job_id, attempt_id)
    request = HarnessRequest.create(
        harness_run_id="run-1",
        mission_id=mission_id,
        job_id=job_id,
        attempt_id=attempt_id,
        worker_id="worker-1",
        capability="fake.echo",
        entrypoint="tests.fixtures.process_workers:echo_worker",
        timeout_seconds=30,
        heartbeat_interval_seconds=1,
        input_payload={"value": 1},
        workspace=str(workspace.root),
    )
    workspace.request_path.write_text(
        json.dumps(request.to_json_dict()),
        encoding="utf-8",
    )
    updated_at = datetime.now(UTC) - timedelta(seconds=heartbeat_age_seconds)
    workspace.heartbeat_path.write_text(
        json.dumps(
            {
                "protocol_version": HARNESS_PROTOCOL_VERSION,
                "harness_run_id": "run-1",
                "job_id": job_id,
                "pid": pid,
                "updated_at": updated_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    if result is not None:
        workspace.result_path.write_text(
            json.dumps(result.to_json_dict()),
            encoding="utf-8",
        )
    return workspace.root


def test_recovery_classifies_completed_attempt(tmp_path):
    result = HarnessResult.create(
        harness_run_id="run-1",
        job_id="job-1",
        status=HarnessStatus.SUCCEEDED,
    )
    attempt = _write_attempt(tmp_path, result=result)
    scanner = HarnessRecoveryScanner(
        tmp_path,
        process_controller=FakeProcessController(set()),
        heartbeat_timeout_seconds=30,
    )

    record = scanner.inspect(attempt)

    assert record.classification == HarnessRecoveryClassification.COMPLETED


def test_recovery_classifies_fresh_live_heartbeat_as_active(tmp_path):
    attempt = _write_attempt(tmp_path, pid=123, heartbeat_age_seconds=1)
    scanner = HarnessRecoveryScanner(
        tmp_path,
        process_controller=FakeProcessController({123}),
        heartbeat_timeout_seconds=30,
    )

    assert scanner.inspect(attempt).classification == HarnessRecoveryClassification.ACTIVE


def test_recovery_classifies_old_live_heartbeat_as_stale(tmp_path):
    attempt = _write_attempt(tmp_path, pid=123, heartbeat_age_seconds=120)
    scanner = HarnessRecoveryScanner(
        tmp_path,
        process_controller=FakeProcessController({123}),
        heartbeat_timeout_seconds=30,
    )

    assert scanner.inspect(attempt).classification == HarnessRecoveryClassification.STALE


def test_recovery_classifies_missing_process_as_interrupted(tmp_path):
    attempt = _write_attempt(tmp_path, pid=999999, heartbeat_age_seconds=120)
    scanner = HarnessRecoveryScanner(
        tmp_path,
        process_controller=FakeProcessController(set()),
        heartbeat_timeout_seconds=30,
    )

    assert scanner.inspect(attempt).classification == HarnessRecoveryClassification.INTERRUPTED


def test_recovery_classifies_corrupt_request_as_invalid(tmp_path):
    attempt = tmp_path / "mission/job/attempt"
    attempt.mkdir(parents=True)
    (attempt / "request.json").write_text("{bad", encoding="utf-8")
    scanner = HarnessRecoveryScanner(
        tmp_path,
        process_controller=FakeProcessController(set()),
        heartbeat_timeout_seconds=30,
    )

    assert scanner.inspect(attempt).classification == HarnessRecoveryClassification.INVALID


def test_runtime_recovers_interrupted_harness_attempt_and_requeues_job(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "harness"))
    registry = InMemoryWorkerRegistry(harness=harness)
    runtime = AgentRuntime(store, registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(
        mission.mission_id,
        "fake.echo",
        "echo:1",
        {},
        retry_limit=1,
    )
    store.acquire_job_lease(job.job_id, "worker-1", lease_seconds=60)
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-1",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
        )
    )
    _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
    )

    report = runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=FakeProcessController(set()),
    )

    assert report["interrupted_attempt_ids"] == [attempt.attempt_id]
    assert report["requeued_job_ids"] == [job.job_id]
    assert store.get_job_attempt(attempt.attempt_id).status == JobAttemptStatus.FAILED
    assert store.get_job(job.job_id).status == JobStatus.QUEUED
    assert store.list_active_job_leases(job.job_id) == []


def test_runtime_does_not_terminate_stale_attempt_without_explicit_flag(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "harness"))
    runtime = AgentRuntime(store, registry=InMemoryWorkerRegistry(harness=harness))
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "echo:1", {})
    _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        pid=123,
    )
    controller = FakeProcessController({123})

    report = runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=controller,
    )

    assert report["stale_attempt_ids"] == ["attempt-1"]
    assert controller.terminated_pids == []


def test_recovered_graph_worker_retries_same_node_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "harness"))
    registry = InMemoryWorkerRegistry(harness=harness, heartbeat_interval_seconds=1)
    registry.register_process(
        "fake.echo",
        "tests.fixtures.process_workers:echo_worker",
        allowed_env=("PYTHONPATH",),
    )
    runtime = AgentRuntime(store, registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(
        mission.mission_id,
        "fake.echo",
        "echo:1",
        {"value": 2},
        retry_limit=1,
    )
    template = graph_template_from_mapping(
        {
            "id": "recover-worker",
            "version": 1,
            "nodes": [
                {
                    "id": "worker",
                    "role": "worker",
                    "kind": "worker",
                    "capability": "fake.echo",
                }
            ],
            "edges": [],
            "handoffs": {},
        }
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template, initial_payload={"value": 2})
    node_run = store.create_node_run(
        NodeRunRecord.create(
            node_run_id="node-run-1",
            graph_run_id=graph_run.graph_run_id,
            mission_id=mission.mission_id,
            node_id="worker",
            node_role="worker",
            node_kind="worker",
            sequence=1,
            input_payload={"value": 2},
        )
    )
    store.update_node_run_status(node_run.node_run_id, NodeRunStatus.RUNNING)
    store.bind_graph_node_job(graph_run.graph_run_id, "worker", 1, job.job_id)
    store.acquire_job_lease(job.job_id, "worker-1", lease_seconds=60)
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-1",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
        )
    )
    _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
    )

    runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=FakeProcessController(set()),
    )
    report = resume_graph(runtime, graph_run.graph_run_id)

    assert report["status"] == "succeeded"
    assert len(report["node_runs"]) == 1
    assert report["node_runs"][0]["node_run_id"] == node_run.node_run_id
    assert report["node_runs"][0]["output_payload"]["value"] == 3
    assert [attempt.attempt_number for attempt in store.list_job_attempts(job.job_id)] == [1, 2]
