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
from aedt_agent.agent.mission import MissionState, NodeRunRecord, NodeRunStatus
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


# ---------------------------------------------------------------------------
# fan-out tests
# ---------------------------------------------------------------------------


def test_fan_out_edge_outcome_activates_all_outgoing_edges(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.fan": lambda job, context: {"edge_outcome": "fan_out", "value": 42}},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {"node": ctx.node.node_id}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "worker", "kind": "worker", "capability": "fake.fan"},
            {"id": "branch_a", "role": "aggregate", "kind": "program", "handler": "sink"},
            {"id": "branch_b", "role": "aggregate", "kind": "program", "handler": "sink"},
            {"id": "branch_c", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "s-a", "from": "source", "to": "branch_a", "on": "fan_out"},
            {"id": "s-b", "from": "source", "to": "branch_b", "on": "fan_out"},
            {"id": "s-c", "from": "source", "to": "branch_c", "on": "fan_out"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert node_ids == {"source", "branch_a", "branch_b", "branch_c"}


def test_fan_out_node_flag_activates_all_outgoing_edges(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.source": lambda job, context: {"value": 1}},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "worker", "kind": "worker", "capability": "fake.source", "fan_out": True},
            {"id": "left", "role": "aggregate", "kind": "program", "handler": "sink"},
            {"id": "right", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "s-l", "from": "source", "to": "left", "on": "succeeded"},
            {"id": "s-r", "from": "source", "to": "right", "on": "failed"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert node_ids == {"source", "left", "right"}


def test_fan_out_with_join_all_converges(tmp_path):
    results = []

    def collector(job, context):
        results.append(job.input_payload.get("value"))
        total = sum(results)
        return {"total": total, "edge_outcome": "succeeded" if total >= 3 else "pending"}

    runtime = _runtime(
        tmp_path,
        {"fake.worker": collector},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("final", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {"done": True}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "llm", "fan_out": True},
            {"id": "w1", "role": "worker", "kind": "worker", "capability": "fake.worker"},
            {"id": "w2", "role": "worker", "kind": "worker", "capability": "fake.worker"},
            {"id": "final", "role": "aggregate", "kind": "program", "handler": "final", "join": "all"},
        ],
        [
            {"id": "s-w1", "from": "source", "to": "w1", "on": "succeeded"},
            {"id": "s-w2", "from": "source", "to": "w2", "on": "succeeded"},
            {"id": "w1-f", "from": "w1", "to": "final", "on": "succeeded"},
            {"id": "w2-f", "from": "w2", "to": "final", "on": "succeeded"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={"value": 1}, registry=handlers)

    assert report["status"] == "succeeded"
    assert len(results) == 2


# ---------------------------------------------------------------------------
# conditional edge tests
# ---------------------------------------------------------------------------


def test_conditional_edge_true_activates(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.worker": lambda job, context: {"score": 0.9}},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("passed", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {"label": "pass"}})
    handlers.register("failed", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {"label": "fail"}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.worker"},
            {"id": "pass_branch", "role": "aggregate", "kind": "program", "handler": "passed"},
            {"id": "fail_branch", "role": "aggregate", "kind": "program", "handler": "failed"},
        ],
        [
            {"id": "w-p", "from": "worker", "to": "pass_branch", "on": "succeeded", "if": "score >= 0.8"},
            {"id": "w-f", "from": "worker", "to": "fail_branch", "on": "succeeded", "if": "score < 0.8"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert "pass_branch" in node_ids
    assert "fail_branch" not in node_ids


def test_conditional_edge_false_skips(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.worker": lambda job, context: {"score": 0.5}},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("passed", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})
    handlers.register("failed", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.worker"},
            {"id": "pass_branch", "role": "aggregate", "kind": "program", "handler": "passed"},
            {"id": "fail_branch", "role": "aggregate", "kind": "program", "handler": "failed"},
        ],
        [
            {"id": "w-p", "from": "worker", "to": "pass_branch", "on": "succeeded", "if": "score >= 0.8"},
            {"id": "w-f", "from": "worker", "to": "fail_branch", "on": "succeeded", "if": "score < 0.8"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert "pass_branch" not in node_ids
    assert "fail_branch" in node_ids


def test_conditional_edge_with_has_operator(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.worker": lambda job, context: {"value": 1, "extra": "present"}},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("with_extra", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})
    handlers.register("without", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.worker"},
            {"id": "with_extra", "role": "aggregate", "kind": "program", "handler": "with_extra"},
            {"id": "without", "role": "aggregate", "kind": "program", "handler": "without"},
        ],
        [
            {"id": "w-yes", "from": "worker", "to": "with_extra", "on": "succeeded", "if": "has(extra)"},
            {"id": "w-no", "from": "worker", "to": "without", "on": "succeeded", "if": "has(missing)"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert "with_extra" in node_ids
    assert "without" not in node_ids


def test_conditional_edge_and_combination(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.worker": lambda job, context: {"score": 0.9, "rl_margin": 5}},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("approved", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})
    handlers.register("rejected", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "fake.worker"},
            {"id": "approved", "role": "aggregate", "kind": "program", "handler": "approved"},
            {"id": "rejected", "role": "aggregate", "kind": "program", "handler": "rejected"},
        ],
        [
            {"id": "w-a", "from": "worker", "to": "approved", "on": "succeeded", "if": "score >= 0.8 and rl_margin > 3"},
            {"id": "w-r", "from": "worker", "to": "rejected", "on": "succeeded", "if": "score < 0.8"},
        ],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={}, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert "approved" in node_ids
    assert "rejected" not in node_ids


def test_edge_condition_unknown_operator_is_fail_closed(tmp_path):
    """Unknown condition operators should fail-closed (return False), not pass through."""
    from aedt_agent.agent.graph_runner import _evaluate_edge_condition

    assert _evaluate_edge_condition("score ~= 0.8", {"score": 1.0}) is False
    assert _evaluate_edge_condition("garbage", {"score": 1.0}) is False
    assert _evaluate_edge_condition("score > 0.5", {"score": 1.0}) is True  # known ops still work


# ---------------------------------------------------------------------------
# dynamic node expand tests
# ---------------------------------------------------------------------------


def test_expand_node_creates_dynamic_downstream(tmp_path):
    runtime = _runtime(
        tmp_path,
        {"fake.dynamic": lambda job, context: {"value": 1}},
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register("dynamic_sink", lambda ctx: {"status": "succeeded", "outcome": "succeeded", "output_payload": {"from": ctx.node.node_id}})

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm", "expand": True},
        ],
        [],
        handoffs={},
    )

    create_graph_run(
        runtime, mission.mission_id, template,
        initial_payload={
            "expand_nodes": [
                {"id": "dynamic_worker", "role": "worker", "kind": "worker", "capability": "fake.dynamic"},
                {"id": "dynamic_sink", "role": "aggregate", "kind": "program", "handler": "dynamic_sink"},
            ],
            "expand_edges": [
                {"id": "dw-ds", "from": "dynamic_worker", "to": "dynamic_sink", "on": "succeeded"},
            ],
        },
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "expand_nodes": [
            {"id": "dynamic_worker", "role": "worker", "kind": "worker", "capability": "fake.dynamic"},
            {"id": "dynamic_sink", "role": "aggregate", "kind": "program", "handler": "dynamic_sink"},
        ],
        "expand_edges": [
            {"id": "p-dw", "from": "planner", "to": "dynamic_worker", "on": "succeeded"},
            {"id": "dw-ds", "from": "dynamic_worker", "to": "dynamic_sink", "on": "succeeded"},
        ],
    }, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert "dynamic_worker" in node_ids
    assert "dynamic_sink" in node_ids


def test_expand_without_flag_ignores_expand_payload(tmp_path):
    runtime = _runtime(tmp_path, {})
    handlers = GraphNodeExecutorRegistry()

    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm"},
        ],
        [],
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "expand_nodes": [{"id": "should_not_appear", "role": "worker", "kind": "worker", "capability": "x"}],
        "expand_edges": [],
    }, registry=handlers)

    assert report["status"] == "succeeded"
    node_ids = {r["node_id"] for r in report["node_runs"]}
    assert "should_not_appear" not in node_ids


# ---------------------------------------------------------------------------
# multi-channel demo template test
# ---------------------------------------------------------------------------


def test_brd_multi_channel_demo_template_loads_and_runs_scenario(tmp_path):
    """Verify the demo template parses and runs through all nodes with fan-out."""
    results_per_channel = {}

    def channel_scorer(job, context):
        ch = job.input_payload.get("channel_id", "unknown")
        score = job.input_payload.get("base_score", 0.5) + 0.3
        results_per_channel[ch] = score
        return {"status": "passed", "score": score, "channel_id": ch, "evidence_summary": {"scored": True}}

    runtime = _runtime(
        tmp_path,
        {
            "brd.local_cut.build": lambda job, context: {
                "status": "built", "project_path": str(tmp_path / "proj.aedt"),
                "artifact_refs": [], "edge_outcome": "fan_out",
            },
            "brd.channel.score": channel_scorer,
        },
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register(
        "aggregate_scorecard_handler",
        lambda ctx: {
            "status": "succeeded",
            "outcome": "passed",
            "output_payload": {
                "status": "passed",
                "checks": {"ch1": "ok", "ch2": "ok"},
                "overall_score": 0.9,
            },
        },
    )

    mission = runtime.create_mission("demo", [], [])
    template = load_graph_template("brd_multi_channel_demo")

    # Give fan-out seed: build → score both channels
    report = run_graph(
        runtime, mission.mission_id, template,
        initial_payload={"plan": "demo", "target_spec": {}, "channel_id": "ch1", "base_score": 0.5},
        registry=handlers,
    )

    assert report["status"] in ("succeeded", "waiting_approval", "failed")
    node_ids = {r["node_id"] for r in report["node_runs"]}
    # At minimum the planner and build_worker should have run
    assert "planner" in node_ids
    assert "build_worker" in node_ids


# ---------------------------------------------------------------------------
# planner: BRD local-cut request generation
# ---------------------------------------------------------------------------


def test_planner_generates_brd_local_cut_request_from_minimal_input(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm", "output_schema": "brd_local_cut_request"},
        ],
        [],
        handoffs={"brd_local_cut_request": {"required_fields": [
            "layout_file", "signal_nets", "reference_nets", "local_cut_region",
        ]}},
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "layout_file": str(tmp_path / "board.brd"),
        "signal_nets": ["CLK0"],
        "local_cut_region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
    })

    assert report["status"] == "succeeded"
    planner_output = report["node_runs"][0]["output_payload"]
    assert planner_output["reference_nets"] == ["GND"]
    assert planner_output["adapter_mode"] == "real_build"
    assert "artifact_dir" in planner_output
    assert "plan_summary" in planner_output
    assert "CLK0" in planner_output["plan_summary"]


def test_planner_preserves_user_overrides(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm", "output_schema": "brd_local_cut_request"},
        ],
        [],
        handoffs={"brd_local_cut_request": {"required_fields": [
            "layout_file", "signal_nets", "reference_nets", "local_cut_region",
        ]}},
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "layout_file": str(tmp_path / "board.brd"),
        "signal_nets": ["NET1", "NET2"],
        "reference_nets": ["AGND"],
        "local_cut_region": {"x1": 5, "y1": 5, "x2": 15, "y2": 15},
        "adapter_mode": "deterministic",
        "artifact_dir": str(tmp_path / "custom"),
    })

    planner_output = report["node_runs"][0]["output_payload"]
    assert planner_output["signal_nets"] == ["NET1", "NET2"]
    assert planner_output["reference_nets"] == ["AGND"]
    assert planner_output["adapter_mode"] == "deterministic"
    assert planner_output["artifact_dir"] == str(tmp_path / "custom")


def test_planner_adds_artifact_dir_when_missing(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm", "output_schema": "brd_local_cut_request"},
        ],
        [],
        handoffs={"brd_local_cut_request": {"required_fields": [
            "layout_file", "signal_nets", "reference_nets", "local_cut_region",
        ]}},
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "layout_file": str(tmp_path / "board.brd"),
        "signal_nets": ["CLK0"],
        "local_cut_region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
    })

    planner_output = report["node_runs"][0]["output_payload"]
    assert "artifact_dir" in planner_output
    assert planner_output["artifact_dir"] != ""


# ---------------------------------------------------------------------------
# validator: BRD request semantic validation
# ---------------------------------------------------------------------------


def test_validator_accepts_valid_brd_request(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    layout = tmp_path / "board.brd"
    layout.write_text("fake")
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm", "output_schema": "brd_local_cut_request"},
            {"id": "validator", "role": "validator", "kind": "program",
             "input_schema": "brd_local_cut_request", "output_schema": "validated_brd_local_cut_request"},
        ],
        [{"id": "p-v", "from": "planner", "to": "validator", "on": "succeeded"}],
        handoffs={
            "brd_local_cut_request": {"required_fields": ["layout_file", "signal_nets", "reference_nets", "local_cut_region"]},
            "validated_brd_local_cut_request": {"required_fields": ["layout_file", "signal_nets", "reference_nets", "local_cut_region"]},
        },
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "layout_file": str(layout),
        "signal_nets": ["CLK0", "CLK1"],
        "reference_nets": ["GND"],
        "local_cut_region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
        "uniform_line_port_hint": {"count": 2},
    })

    assert report["status"] == "succeeded"
    assert report["node_runs"][1]["edge_decision"] == "succeeded"


def test_validator_flags_missing_layout_file(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm", "output_schema": "brd_local_cut_request"},
            {"id": "validator", "role": "validator", "kind": "program",
             "input_schema": "brd_local_cut_request", "output_schema": "validated_brd_local_cut_request"},
        ],
        [{"id": "p-v", "from": "planner", "to": "validator", "on": "succeeded"}],
        handoffs={
            "brd_local_cut_request": {"required_fields": ["layout_file", "signal_nets", "reference_nets", "local_cut_region"]},
            "validated_brd_local_cut_request": {"required_fields": ["layout_file", "signal_nets", "reference_nets", "local_cut_region"]},
        },
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "layout_file": str(tmp_path / "nonexistent.brd"),
        "signal_nets": ["CLK0"],
        "reference_nets": ["GND"],
        "local_cut_region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
    })

    assert report["status"] == "succeeded"
    # Validator emits approval_required due to missing file
    assert report["node_runs"][1]["edge_decision"] == "approval_required"


def test_validator_flags_empty_signal_nets(tmp_path):
    runtime = _runtime(tmp_path, {})
    mission = runtime.create_mission("goal", [], [])
    layout = tmp_path / "board.brd"
    layout.write_text("fake")
    template = _template(
        [
            {"id": "planner", "role": "planner", "kind": "llm", "output_schema": "brd_local_cut_request"},
            {"id": "validator", "role": "validator", "kind": "program",
             "input_schema": "brd_local_cut_request", "output_schema": "validated_brd_local_cut_request"},
        ],
        [{"id": "p-v", "from": "planner", "to": "validator", "on": "succeeded"}],
        handoffs={
            "brd_local_cut_request": {"required_fields": ["layout_file", "signal_nets", "reference_nets", "local_cut_region"]},
            "validated_brd_local_cut_request": {"required_fields": ["layout_file", "signal_nets", "reference_nets", "local_cut_region"]},
        },
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={
        "layout_file": str(layout),
        "signal_nets": [],
        "reference_nets": ["GND"],
        "local_cut_region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
    })

    assert report["node_runs"][1]["edge_decision"] == "approval_required"
