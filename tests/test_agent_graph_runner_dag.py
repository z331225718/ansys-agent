from __future__ import annotations

import threading
from pathlib import Path

import aedt_agent.agent.graph_runner as graph_runner_module
from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_executors import GraphNodeExecutorRegistry
from aedt_agent.agent.graph_runner import (
    advance_graph,
    create_graph_run,
    graph_status,
    resume_graph,
    run_graph,
)
from aedt_agent.agent.graph_template import graph_template_from_mapping, load_graph_template
from aedt_agent.agent.mission import GraphRunStatus, MissionState, NodeRunRecord, NodeRunStatus
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.agent.workers import WorkerExecutionResult
from aedt_agent.agent.mission import ErrorClass, JobError, JobStatus
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path, workers: dict[str, object]) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    for capability, worker in workers.items():
        registry.register(capability, worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def _template(nodes, edges, handoffs=None):
    return graph_template_from_mapping(
        {
            "id": "test_graph",
            "version": 1,
            "nodes": nodes,
            "edges": edges,
            "handoffs": handoffs or {},
        }
    )


def test_graph_runs_serial_planner_validator_worker_to_terminal(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.echo": lambda job, context: {"value": job.input_payload["value"] + 1}},
    )
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm"},
            {"id": "validator", "role": "validator", "kind": "program"},
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.echo"},
        ],
        [
            {"id": "plan-valid", "from": "planner", "to": "validator", "on": "succeeded"},
            {"id": "valid-work", "from": "validator", "to": "worker", "on": "succeeded"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={"value": 4})

    assert report["status"] == "succeeded"
    assert [run["node_id"] for run in report["node_runs"]] == ["planner", "validator", "worker"]
    assert report["node_runs"][-1]["output_payload"]["value"] == 5
    assert len(report["handoffs"]) == 2


def test_graph_activates_only_edge_matching_node_outcome(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.branch": lambda job, context: {"edge_outcome": "branch_a", "value": 1}},
    )
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.branch"},
            {"id": "a", "role": "aggregate", "kind": "program", "handler": "sink"},
            {"id": "b", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "to-a", "from": "worker", "to": "a", "on": "branch_a"},
            {"id": "to-b", "from": "worker", "to": "b", "on": "branch_b"},
        ],
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register(
        "sink",
        lambda context: {
            "status": "succeeded",
            "outcome": "succeeded",
            "output_payload": {"sink": context.node.node_id},
        },
    )

    report = run_graph(
        runtime,
        mission.mission_id,
        template,
        initial_payload={"value": 1},
        registry=handlers,
    )

    assert report["status"] == "succeeded"
    assert [run["node_id"] for run in report["node_runs"]] == ["worker", "a"]
    assert [handoff["edge_id"] for handoff in report["handoffs"]] == ["to-a"]


def test_failed_node_without_matching_edge_fails_graph(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.invalid": lambda job, context: (_ for _ in ()).throw(ValueError("bad input"))},
    )
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [{"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.invalid"}],
        [],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={})

    assert report["status"] == "failed"
    assert report["graph_run"]["error"]["code"] == "unhandled_node_outcome"


def test_parallel_workers_run_in_same_wave_and_join_all(tmp_path):
    barrier = threading.Barrier(2)

    def parallel_worker(job, context):
        barrier.wait(timeout=3)
        return {"value": job.capability}

    runtime = _runtime(
        tmp_path,
        {"fake.left": parallel_worker, "fake.right": parallel_worker},
    )
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "llm"},
            {"id": "left", "role": "worker", "kind": "worker", "capability": "fake.left"},
            {"id": "right", "role": "worker", "kind": "worker", "capability": "fake.right"},
            {
                "id": "join",
                "role": "aggregate",
                "kind": "program",
                "handler": "aggregate",
                "join": "all",
                "after": ["left", "right"],
            },
        ],
        [
            {"id": "source-left", "from": "source", "to": "left", "on": "succeeded"},
            {"id": "source-right", "from": "source", "to": "right", "on": "succeeded"},
            {"id": "left-join", "from": "left", "to": "join", "on": "succeeded"},
            {"id": "right-join", "from": "right", "to": "join", "on": "succeeded"},
        ],
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register(
        "aggregate",
        lambda context: {
            "status": "succeeded",
            "outcome": "succeeded",
            "output_payload": {
                "sources": sorted(context.input_payload["_handoffs"]),
            },
        },
    )

    report = run_graph(
        runtime,
        mission.mission_id,
        template,
        initial_payload={"seed": 1},
        registry=handlers,
        max_workers=2,
    )

    assert report["status"] == "succeeded"
    join_run = [run for run in report["node_runs"] if run["node_id"] == "join"][0]
    assert join_run["output_payload"]["sources"] == ["left", "right"]
    assert report["graph_run"]["step_count"] == 3


def test_failed_tester_routes_back_to_coder_until_success(tmp_path):
    tester_calls = 0

    def tester(job, context):
        nonlocal tester_calls
        tester_calls += 1
        return {"edge_outcome": "failed" if tester_calls == 1 else "succeeded"}

    runtime = _runtime(
        tmp_path,
        {
            "fake.coder": lambda job, context: {"revision": job.input_payload.get("revision", 0) + 1},
            "fake.tester": tester,
        },
    )
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "llm"},
            {
                "id": "coder",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.coder",
                "max_runs": 2,
            },
            {
                "id": "tester",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.tester",
                "max_runs": 2,
            },
        ],
        [
            {
                "id": "source-coder",
                "from": "source",
                "to": "coder",
                "on": "succeeded",
            },
            {
                "id": "coder-tester",
                "from": "coder",
                "to": "tester",
                "on": "succeeded",
                "max_traversals": 2,
            },
            {
                "id": "tester-coder",
                "from": "tester",
                "to": "coder",
                "on": "failed",
                "max_traversals": 1,
            },
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={"revision": 0})

    assert report["status"] == "succeeded"
    assert [run["node_id"] for run in report["node_runs"]] == [
        "source",
        "coder",
        "tester",
        "coder",
        "tester",
    ]


def test_edge_traversal_limit_fails_graph(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "fake.coder": lambda job, context: {"revision": 1},
            "fake.tester": lambda job, context: {"edge_outcome": "failed"},
        },
    )
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "llm"},
            {"id": "coder", "role": "worker", "kind": "worker", "capability": "fake.coder", "max_runs": 3},
            {"id": "tester", "role": "worker", "kind": "worker", "capability": "fake.tester", "max_runs": 3},
        ],
        [
            {"id": "source-coder", "from": "source", "to": "coder", "on": "succeeded"},
            {"id": "coder-tester", "from": "coder", "to": "tester", "on": "succeeded", "max_traversals": 3},
            {"id": "tester-coder", "from": "tester", "to": "coder", "on": "failed", "max_traversals": 1},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={})

    assert report["status"] == "failed"
    assert report["graph_run"]["error"]["code"] == "edge_traversal_limit"


def test_graph_max_steps_fails_before_next_wave(tmp_path):
    runtime = _runtime(tmp_path, {"fake.worker": lambda job, context: {}})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "llm"},
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.worker"},
        ],
        [{"id": "source-worker", "from": "source", "to": "worker", "on": "succeeded"}],
    )

    report = run_graph(
        runtime,
        mission.mission_id,
        template,
        initial_payload={},
        max_steps=1,
    )

    assert report["status"] == "failed"
    assert report["graph_run"]["error"]["code"] == "graph_step_limit"


def test_pending_handoff_with_unsatisfied_after_is_deadlock(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "llm"},
            {"id": "blocker", "role": "aggregate", "kind": "program", "handler": "sink"},
            {
                "id": "target",
                "role": "aggregate",
                "kind": "program",
                "handler": "sink",
                "after": ["blocker"],
            },
        ],
        [
            {"id": "source-target", "from": "source", "to": "target", "on": "succeeded"},
            {"id": "source-blocker", "from": "source", "to": "blocker", "on": "alternate"},
        ],
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register(
        "sink",
        lambda context: {
            "status": "succeeded",
            "outcome": "succeeded",
            "output_payload": {},
        },
    )

    report = run_graph(
        runtime,
        mission.mission_id,
        template,
        initial_payload={},
        registry=handlers,
    )

    assert report["status"] == "failed"
    assert report["graph_run"]["error"]["code"] == "graph_deadlock"


def test_approval_gate_resumes_same_graph_run_after_restart(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "llm"},
            {"id": "gate", "role": "approval_gate", "kind": "human_gate"},
        ],
        [{"id": "source-gate", "from": "source", "to": "gate", "on": "succeeded"}],
    )

    waiting = run_graph(runtime, mission.mission_id, template, initial_payload={"value": 1})
    approval = runtime.store.list_approvals(mission.mission_id)[0]
    ApprovalService(runtime.store).approve(approval.approval_id, "approve")

    restarted = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"))
    completed = resume_graph(restarted, waiting["graph_run"]["graph_run_id"])

    assert waiting["status"] == "waiting_approval"
    assert completed["status"] == "succeeded"
    assert completed["graph_run"]["graph_run_id"] == waiting["graph_run"]["graph_run_id"]
    gate_runs = [run for run in completed["node_runs"] if run["node_id"] == "gate"]
    assert len(gate_runs) == 1
    assert gate_runs[0]["status"] == "succeeded"


def test_graph_run_uses_persisted_template_snapshot(tmp_path):
    runtime = _runtime(tmp_path, {"fake.worker": lambda job, context: {"ok": True}})
    mission = runtime.create_mission("goal", [], [])
    path = tmp_path / "graph.yaml"
    path.write_text(
        """
id: snapshot
version: 1
nodes:
  - {id: worker, role: worker, kind: worker, capability: fake.worker}
edges: []
handoffs: {}
""".strip(),
        encoding="utf-8",
    )
    template = load_graph_template(path)
    graph_run = create_graph_run(runtime, mission.mission_id, template, initial_payload={})
    path.write_text("not: a valid graph", encoding="utf-8")

    advance_graph(runtime, graph_run.graph_run_id)
    report = graph_status(runtime, graph_run.graph_run_id)

    assert report["status"] == "succeeded"
    assert report["node_runs"][0]["node_id"] == "worker"


def test_graph_with_persisted_running_node_does_not_report_success(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [{"id": "planner", "role": "planner", "kind": "llm"}],
        [],
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template, initial_payload={})
    node_run = runtime.store.create_node_run(
        NodeRunRecord.create(
            node_run_id="interrupted-node",
            graph_run_id=graph_run.graph_run_id,
            mission_id=mission.mission_id,
            node_id="planner",
            node_role="planner",
            node_kind="llm",
            sequence=1,
            input_payload={},
        )
    )
    runtime.store.update_node_run_status(node_run.node_run_id, NodeRunStatus.RUNNING)

    report = advance_graph(runtime, graph_run.graph_run_id)

    assert report["status"] == "running"
    assert report["graph_run"]["current_node_id"] == "planner"
    assert len(report["node_runs"]) == 1
    assert runtime.get_mission(mission.mission_id).state != MissionState.COMPLETED


def test_run_until_blocked_returns_when_running_graph_makes_no_progress(monkeypatch):
    calls = 0
    report = {
        "status": "running",
        "graph_run": {"step_count": 1, "current_node_id": "worker"},
        "node_runs": [{"node_run_id": "nr1", "status": "running"}],
        "handoffs": [],
    }

    def fake_advance(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise AssertionError("run loop did not stop after detecting no progress")
        return report

    monkeypatch.setattr(graph_runner_module, "advance_graph", fake_advance)

    result = graph_runner_module._run_until_blocked(
        object(),
        "graph-1",
        worker_id="worker",
        max_workers=1,
        registry=None,
    )

    assert result == report
    assert calls == 2


def test_canceled_worker_job_fails_graph_instead_of_following_success_path(tmp_path):
    class CancelingRegistry:
        def execute(self, job, context, *, attempt_id=None, cancel_requested=None):
            return WorkerExecutionResult(
                job.job_id,
                JobStatus.CANCELED,
                {},
                [],
                JobError(ErrorClass.CANCELED, "mission canceled", False),
            )

    runtime = AgentRuntime(
        SQLiteMissionStore(tmp_path / "mission.db"),
        registry=CancelingRegistry(),
    )
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {
                "id": "worker",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.cancel",
            }
        ],
        [],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={})

    assert report["status"] == "failed"
    assert report["graph_run"]["error"]["code"] == "unhandled_node_outcome"
    assert report["node_runs"][0]["edge_decision"] == "canceled"


# ---------------------------------------------------------------------------
# on_failure strategy tests
# ---------------------------------------------------------------------------


def test_on_failure_skip_creates_skipped_edge_and_continues(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.bad": lambda job, context: (_ for _ in ()).throw(ValueError("boom"))},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {"ok": True}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "bad", "role": "worker", "kind": "worker", "capability": "fake.bad", "on_failure": "skip"},
            {"id": "next", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "bad-next", "from": "bad", "to": "next", "on": "skipped"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    bad_run = next(r for r in report["node_runs"] if r["node_id"] == "bad")
    assert bad_run["status"] == "skipped"
    assert bad_run["error"] is not None
    next_run = next(r for r in report["node_runs"] if r["node_id"] == "next")
    assert next_run["status"] == "succeeded"


def test_on_failure_fail_stops_graph(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.bad": lambda job, context: (_ for _ in ()).throw(ValueError("boom"))},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "bad", "role": "worker", "kind": "worker", "capability": "fake.bad"},
            {"id": "next", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "bad-next", "from": "bad", "to": "next", "on": "succeeded"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "failed"
    assert report["graph_run"]["error"]["code"] == "unhandled_node_outcome"


def test_on_failure_retry_succeeds_after_retry(tmp_path):
    attempts = []

    def flaky_worker(job, context):
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("transient error")
        return {"value": "ok"}

    runtime = _runtime(tmp_path, {"fake.flaky": flaky_worker})
    handlers = GraphNodeExecutorRegistry()
    handlers.register("sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {
                "id": "flaky",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.flaky",
                "on_failure": "retry",
                "retry_max_attempts": 3,
                "retry_backoff": "constant",
                "retry_delay_seconds": 0.0,
            },
            {"id": "next", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "flaky-next", "from": "flaky", "to": "next", "on": "succeeded"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    assert len(attempts) == 3


def test_on_failure_retry_exhausted_fails_graph(tmp_path):
    attempts = []

    def always_fail(job, context):
        attempts.append(1)
        raise ValueError("persistent error")

    runtime = _runtime(tmp_path, {"fake.bad": always_fail})
    handlers = GraphNodeExecutorRegistry()
    handlers.register("sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {
                "id": "bad",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.bad",
                "on_failure": "retry",
                "retry_max_attempts": 2,
                "retry_delay_seconds": 0.0,
            },
            {"id": "next", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "bad-next", "from": "bad", "to": "next", "on": "succeeded"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "failed"
    assert len(attempts) == 2


def test_on_failure_fallback_routes_to_alternate_node(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.bad": lambda job, context: (_ for _ in ()).throw(ValueError("boom"))},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("recovery", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {"recovered": True}})
    handlers.register("sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "bad", "role": "worker", "kind": "worker", "capability": "fake.bad", "on_failure": "fallback:recovery"},
            {"id": "recovery", "role": "aggregate", "kind": "program", "handler": "recovery"},
            {"id": "next", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "bad-next", "from": "bad", "to": "next", "on": "succeeded"},
            {"id": "bad-recovery", "from": "bad", "to": "recovery", "on": "failed"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    recovery_run = next(r for r in report["node_runs"] if r["node_id"] == "recovery")
    assert recovery_run["status"] == "succeeded"
    assert recovery_run["output_payload"]["recovered"] is True
