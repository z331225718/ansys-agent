from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_runner import (
    advance_graph,
    create_graph_run,
    resume_graph,
    run_graph,
)
from aedt_agent.agent.graph_template import (
    graph_template_from_mapping,
    load_graph_template,
)
from aedt_agent.agent.mission import (
    EventType,
    JobAttemptRecord,
    JobAttemptStatus,
    JobStatus,
    MissionState,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import (
    BRD_CHANNEL_SCORE_CAPABILITY,
    BRD_REAL_SOLVE_CAPABILITY,
    InMemoryWorkerRegistry,
    WorkerContext,
    build_brd_real_solve_job_input,
    run_brd_channel_score_worker,
)
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
from tests.fixtures.fake_real_solve import (
    run_fake_real_solve_worker,
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
    capability: str = "fake.echo",
    entrypoint: str = (
        "tests.fixtures.process_workers:echo_worker"
    ),
    input_payload: dict | None = None,
) -> Path:
    policy = HarnessWorkspacePolicy(root)
    workspace = policy.create_attempt(mission_id, job_id, attempt_id)
    request = HarnessRequest.create(
        harness_run_id="run-1",
        mission_id=mission_id,
        job_id=job_id,
        attempt_id=attempt_id,
        worker_id="worker-1",
        capability=capability,
        entrypoint=entrypoint,
        timeout_seconds=30,
        heartbeat_interval_seconds=1,
        input_payload=input_payload or {"value": 1},
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


@pytest.mark.parametrize(
    ("completed_result", "expected_operation", "forbidden_event"),
    [
        (True, "complete", EventType.JOB_SUCCEEDED),
        (False, "fail", EventType.JOB_FAILED),
    ],
)
def test_harness_recovery_audits_stale_fenced_attempt_once(
    tmp_path,
    completed_result,
    expected_operation,
    forbidden_event,
):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "harness"))
    runtime = AgentRuntime(store, registry=InMemoryWorkerRegistry(harness=harness))
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "echo:stale", {})
    expired_at = datetime.now(UTC) - timedelta(seconds=5)
    old_lease = store.acquire_job_lease(
        job.job_id,
        "worker-1",
        lease_seconds=1,
        now=expired_at,
    )
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-stale-fenced",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
            lease_id=old_lease.lease_id,
        )
    )
    result = None
    if completed_result:
        result = HarnessResult.create(
            harness_run_id="run-1",
            job_id=job.job_id,
            status=HarnessStatus.SUCCEEDED,
            output_payload={"writer": "old"},
            exit_code=0,
        )
    _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
        result=result,
    )
    assert store.recover_expired_leases(datetime.now(UTC)) == [job.job_id]
    new_lease = store.acquire_job_lease(
        job.job_id,
        "worker-new",
        lease_seconds=60,
    )

    first_report = runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=FakeProcessController(set()),
    )

    audited = store.get_job_attempt(attempt.attempt_id)
    assert first_report["stale_fenced_attempt_ids"] == [attempt.attempt_id]
    assert first_report["adopted_completed_attempt_ids"] == []
    assert audited.status == JobAttemptStatus.CANCELED
    assert audited.retry_decision == "stale_fenced"
    assert audited.error["code"] == "stale_fenced"
    assert audited.error["details"]["operation"] == expected_operation
    assert audited.error["details"]["lease_id"] == old_lease.lease_id
    assert store.get_job(job.job_id).status == JobStatus.LEASED
    assert [lease.lease_id for lease in store.list_active_job_leases(job.job_id)] == [
        new_lease.lease_id
    ]
    assert not any(
        event.event_type == forbidden_event
        for event in store.list_events(mission.mission_id)
    )
    attempt_updates_before = sum(
        event.event_type == EventType.JOB_ATTEMPT_UPDATED
        for event in store.list_events(mission.mission_id)
    )

    second_report = runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=FakeProcessController(set()),
    )

    attempt_updates_after = sum(
        event.event_type == EventType.JOB_ATTEMPT_UPDATED
        for event in store.list_events(mission.mission_id)
    )
    assert second_report["stale_fenced_attempt_ids"] == []
    assert attempt_updates_after == attempt_updates_before

    committed = store.complete_job(
        job.job_id,
        {"writer": "new"},
        [],
        lease_id=new_lease.lease_id,
    )
    assert committed.status == JobStatus.SUCCEEDED


def test_recovered_success_does_not_commit_job_for_failed_mission(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "harness"))
    runtime = AgentRuntime(store, registry=InMemoryWorkerRegistry(harness=harness))
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "echo:failed", {})
    lease = store.acquire_job_lease(
        job.job_id,
        "worker-1",
        lease_seconds=60,
    )
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-failed-mission",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
            lease_id=lease.lease_id,
        )
    )
    result = HarnessResult.create(
        harness_run_id="run-1",
        job_id=job.job_id,
        status=HarnessStatus.SUCCEEDED,
        output_payload={"writer": "worker-1"},
        exit_code=0,
    )
    _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
        result=result,
    )
    store.update_mission_state(mission.mission_id, MissionState.WAITING_WORKER)
    store.update_mission_state(mission.mission_id, MissionState.FAILED)

    with pytest.raises(ValueError, match="mission is not ready for worker execution"):
        runtime.recover_harness_attempts(
            mission.mission_id,
            process_controller=FakeProcessController(set()),
        )

    current_job = store.get_job(job.job_id)
    current_attempt = store.get_job_attempt(attempt.attempt_id)
    assert current_job.status == JobStatus.LEASED
    assert current_job.output_payload == {}
    assert current_attempt.status == JobAttemptStatus.RUNNING
    assert [item.lease_id for item in store.list_active_job_leases(job.job_id)] == [
        lease.lease_id
    ]
    assert not any(
        event.event_type in {
            EventType.JOB_SUCCEEDED,
            EventType.JOB_ATTEMPT_UPDATED,
        }
        for event in store.list_events(mission.mission_id)
    )


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


def test_runtime_terminates_stale_attempt_and_registers_protocol_artifacts(
    tmp_path,
):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(
        HarnessWorkspacePolicy(tmp_path / "harness")
    )
    runtime = AgentRuntime(
        store,
        registry=InMemoryWorkerRegistry(harness=harness),
    )
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(
        mission.mission_id,
        BRD_REAL_SOLVE_CAPABILITY,
        "real-solve:stale",
        {},
        retry_limit=0,
    )
    store.acquire_job_lease(
        job.job_id,
        "worker-1",
        lease_seconds=60,
    )
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-stale",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
        )
    )
    workspace = _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
        pid=123,
        capability=BRD_REAL_SOLVE_CAPABILITY,
        entrypoint=(
            "tests.fixtures.process_workers:"
            "spawn_child_worker"
        ),
        input_payload={
            "pid_path": str(tmp_path / "stale-child.pid")
        },
    )
    (workspace / "stdout.log").write_text("", encoding="utf-8")
    (workspace / "stderr.log").write_text("", encoding="utf-8")
    controller = FakeProcessController({123})

    report = runtime.recover_harness_attempts(
        mission.mission_id,
        terminate_stale=True,
        process_controller=controller,
    )

    assert report["terminated_pids"] == [123]
    assert controller.is_alive(123) is False
    manifests = store.list_artifact_manifests(mission.mission_id)
    assert {
        "request.json",
        "result.json",
        "stdout.log",
        "stderr.log",
    } <= {Path(item.path).name for item in manifests}


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


def test_recovery_adopts_completed_child_result_without_rerunning_worker(tmp_path, monkeypatch):
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
        "echo:completed",
        {"value": 2},
        retry_limit=1,
    )
    template = graph_template_from_mapping(
        {
            "id": "recover-completed-worker",
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
            node_run_id="node-run-completed",
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
            "attempt-completed",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
        )
    )
    completed_result = HarnessResult.create(
        harness_run_id="run-1",
        job_id=job.job_id,
        status=HarnessStatus.SUCCEEDED,
        output_payload={"value": 3},
        exit_code=0,
    )
    _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
        result=completed_result,
    )

    recovery = runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=FakeProcessController(set()),
    )
    report = resume_graph(runtime, graph_run.graph_run_id)

    assert recovery["adopted_completed_attempt_ids"] == [attempt.attempt_id]
    assert store.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert store.get_job_attempt(attempt.attempt_id).status == JobAttemptStatus.SUCCEEDED
    assert report["status"] == "succeeded"
    assert len(report["node_runs"]) == 1
    assert report["node_runs"][0]["node_run_id"] == node_run.node_run_id
    assert report["node_runs"][0]["output_payload"]["value"] == 3
    assert [item.attempt_number for item in store.list_job_attempts(job.job_id)] == [1]


def test_recovery_adopts_completed_real_solve_without_second_execution(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(
        HarnessWorkspacePolicy(tmp_path / "harness")
    )
    registry = InMemoryWorkerRegistry(
        harness=harness,
        heartbeat_interval_seconds=1,
        allow_real_aedt=True,
    )
    registry.register_process(
        BRD_REAL_SOLVE_CAPABILITY,
        (
            "tests.fixtures.fake_real_solve:"
            "run_fake_real_solve_worker"
        ),
        resource_classes=("license", "aedt"),
        allowed_env=("PYTHONPATH",),
        requires_real_aedt=True,
    )
    registry.register(
        BRD_CHANNEL_SCORE_CAPABILITY,
        run_brd_channel_score_worker,
    )
    runtime = AgentRuntime(store, registry=registry)
    mission = runtime.create_mission("real solve recovery", [], [])
    project = tmp_path / "approved.aedt"
    project.write_text("approved project", encoding="utf-8")
    payload = build_brd_real_solve_job_input(
        project_path=project,
        setup_name="Setup1",
        sweep_name="Sweep1",
        tdr_expression="TDRZt(P1,P1)",
        expected_port_count=2,
        aedt={"version": "2026.1", "non_graphical": True},
    )
    job = runtime.create_job(
        mission.mission_id,
        BRD_REAL_SOLVE_CAPABILITY,
        "real-solve:recovery",
        payload,
        timeout_seconds=30,
        retry_limit=0,
    )
    template = load_graph_template("brd_real_solve_evidence")
    waiting = run_graph(
        runtime,
        mission.mission_id,
        template,
        initial_payload=payload,
    )
    approval_run = next(
        run
        for run in waiting["node_runs"]
        if run["node_id"] == "model_approval_gate"
    )
    ApprovalService(store).approve(
        approval_run["output_payload"]["approval_id"],
        "approve",
    )
    ready = advance_graph(
        runtime,
        waiting["graph_run"]["graph_run_id"],
    )
    assert ready["graph_run"]["current_node_id"] == (
        "real_solve_worker"
    )
    handoff = next(
        item
        for item in store.list_graph_handoffs(
            waiting["graph_run"]["graph_run_id"]
        )
        if item.to_node == "real_solve_worker"
        and item.status.value == "pending"
    )
    node_run = store.create_node_run(
        NodeRunRecord.create(
            "node-run-real-solve-recovery",
            waiting["graph_run"]["graph_run_id"],
            mission.mission_id,
            "real_solve_worker",
            "worker",
            "worker",
            3,
            dict(handoff.payload),
        )
    )
    store.update_node_run_status(
        node_run.node_run_id,
        NodeRunStatus.RUNNING,
    )
    store.bind_graph_node_job(
        waiting["graph_run"]["graph_run_id"],
        "real_solve_worker",
        1,
        job.job_id,
    )
    store.acquire_job_lease(
        job.job_id,
        "worker-1",
        lease_seconds=60,
    )
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-real-solve-completed",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
        )
    )
    workspace = _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
        capability=BRD_REAL_SOLVE_CAPABILITY,
        entrypoint=(
            "tests.fixtures.fake_real_solve:"
            "run_fake_real_solve_worker"
        ),
        input_payload=payload,
    )
    (workspace / "stdout.log").write_text("", encoding="utf-8")
    (workspace / "stderr.log").write_text("", encoding="utf-8")
    output = run_fake_real_solve_worker(
        job,
        WorkerContext(
            "worker-1",
            workspace=str(workspace),
            artifacts_dir=str(workspace / "artifacts"),
        ),
    )
    artifact_refs = output.pop("artifact_refs")
    completed_result = HarnessResult.create(
        harness_run_id="run-1",
        job_id=job.job_id,
        status=HarnessStatus.SUCCEEDED,
        output_payload=output,
        artifact_refs=artifact_refs,
        exit_code=0,
    )
    (workspace / "result.json").write_text(
        json.dumps(completed_result.to_json_dict()),
        encoding="utf-8",
    )

    recovery = runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=FakeProcessController(set()),
    )
    report = resume_graph(
        runtime,
        waiting["graph_run"]["graph_run_id"],
    )

    assert recovery["adopted_completed_attempt_ids"] == [
        attempt.attempt_id
    ]
    assert report["status"] == "succeeded"
    assert len(store.list_job_attempts(job.job_id)) == 1
    recovered_run = next(
        run
        for run in report["node_runs"]
        if run["node_id"] == "real_solve_worker"
    )
    assert recovered_run["node_run_id"] == node_run.node_run_id


def test_recovery_keeps_canceled_mission_terminal_when_child_result_completed(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "harness"))
    runtime = AgentRuntime(store, registry=InMemoryWorkerRegistry(harness=harness))
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "echo:canceled", {})
    store.acquire_job_lease(job.job_id, "worker-1", lease_seconds=60)
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-canceled-completed",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
        )
    )
    completed_result = HarnessResult.create(
        harness_run_id="run-1",
        job_id=job.job_id,
        status=HarnessStatus.SUCCEEDED,
        output_payload={"value": 3},
        exit_code=0,
    )
    _write_attempt(
        tmp_path / "harness",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
        result=completed_result,
    )
    store.update_mission_state(mission.mission_id, MissionState.CANCELED)

    report = runtime.recover_harness_attempts(
        mission.mission_id,
        process_controller=FakeProcessController(set()),
    )

    assert report["adopted_completed_attempt_ids"] == [attempt.attempt_id]
    assert store.get_mission(mission.mission_id).state == MissionState.CANCELED
    assert store.get_job(job.job_id).status == JobStatus.CANCELED
    assert store.get_job_attempt(attempt.attempt_id).status == JobAttemptStatus.CANCELED
