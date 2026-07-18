from __future__ import annotations

import threading
from pathlib import Path

import pytest

from aedt_agent.agent.event_replay import replay_graph_run
from aedt_agent.agent.graph_executors import GraphNodeExecutorRegistry
from aedt_agent.agent.graph_interventions import intervene_graph
from aedt_agent.agent.graph_runner import advance_graph, create_graph_run
from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.mission import (
    GraphHandoffRecord,
    GraphRunRecord,
    GraphRunStatus,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path, workers: dict[str, object] | None = None) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    for capability, worker in (workers or {}).items():
        registry.register(capability, worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def _template(nodes: list[dict], edges: list[dict]):
    return graph_template_from_mapping(
        {
            "id": "intervention-test",
            "version": 1,
            "nodes": nodes,
            "edges": edges,
            "handoffs": {},
        }
    )


def _cursor(runtime: AgentRuntime, graph_run_id: str) -> int:
    return replay_graph_run(runtime, graph_run_id)["event_cursor"]


def _state(runtime: AgentRuntime, graph_run_id: str) -> dict:
    graph_run = runtime.store.get_graph_run(graph_run_id)
    return {
        "graph_run": graph_run.to_json_dict(),
        "node_runs": [
            row.to_json_dict() for row in runtime.store.list_node_runs(graph_run_id)
        ],
        "handoffs": [
            row.to_json_dict()
            for row in runtime.store.list_graph_handoffs(graph_run_id)
        ],
        "jobs": [
            runtime.store.get_job(job_id).to_json_dict()
            for job_id in runtime.store.list_graph_bound_job_ids(graph_run_id)
        ],
    }


def _start_fanout(runtime: AgentRuntime, *, with_join: bool = False):
    mission = runtime.create_mission("fanout", [], [])
    nodes = [
        {"id": "source", "role": "planner", "kind": "program", "handler": "source"},
        {
            "id": "left",
            "role": "worker",
            "kind": "worker",
            "capability": "fake.left",
        },
        {
            "id": "right",
            "role": "worker",
            "kind": "worker",
            "capability": "fake.right",
        },
    ]
    edges = [
        {"id": "source-left", "from": "source", "to": "left", "on": "succeeded"},
        {"id": "source-right", "from": "source", "to": "right", "on": "succeeded"},
    ]
    if with_join:
        nodes.append(
            {
                "id": "join",
                "role": "aggregate",
                "kind": "program",
                "handler": "join",
                "join": "all",
            }
        )
        edges.extend(
            [
                {"id": "left-join", "from": "left", "to": "join", "on": "succeeded"},
                {"id": "right-join", "from": "right", "to": "join", "on": "succeeded"},
            ]
        )
    template = _template(nodes, edges)
    handlers = GraphNodeExecutorRegistry()
    handlers.register(
        "source",
        lambda context: {
            "status": "succeeded",
            "outcome": "succeeded",
            "output_payload": {"seed": 1},
        },
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template)
    report = advance_graph(runtime, graph_run.graph_run_id, registry=handlers)
    assert report["status"] == "running"
    return mission, template, graph_run


def _create_pending_graph_in_mission(
    runtime: AgentRuntime,
    mission_id: str,
    graph_run_id: str,
):
    mission = runtime.get_mission(mission_id)
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "program", "handler": "source"},
            {
                "id": "target",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.target",
            },
            {
                "id": "other",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.other",
            },
            {
                "id": "observer",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.observer",
            },
        ],
        [
            {"id": "source-target", "from": "source", "to": "target", "on": "succeeded"},
            {"id": "source-other", "from": "source", "to": "other", "on": "succeeded"},
        ],
    )
    graph_run = runtime.store.create_graph_run(
        GraphRunRecord.create(
            graph_run_id,
            mission_id,
            template.template_id,
            template.version,
            mission.plan_version,
            template_snapshot=template.to_json_dict(),
        )
    )
    runtime.store.update_graph_run_status(graph_run_id, GraphRunStatus.RUNNING)
    source_run = runtime.store.create_node_run(
        NodeRunRecord.create(
            f"source-{graph_run_id}",
            graph_run_id,
            mission_id,
            "source",
            "planner",
            "program",
            1,
            {},
        )
    )
    runtime.store.complete_node_run(
        source_run.node_run_id,
        NodeRunStatus.SUCCEEDED,
        {"seed": graph_run_id},
        [],
        edge_decision="succeeded",
    )
    for target in ("target", "other"):
        runtime.store.create_graph_handoff(
            GraphHandoffRecord.create(
                f"{graph_run_id}-{target}-handoff",
                graph_run_id,
                mission_id,
                f"source-{target}",
                source_run.node_run_id,
                "source",
                target,
                "succeeded",
                {"seed": graph_run_id},
            )
        )
    runtime.store.update_graph_run_status(
        graph_run_id,
        GraphRunStatus.RUNNING,
        current_node_id="other,target",
    )
    return graph_run, source_run


def test_retry_node_prepares_one_new_run_and_replay_audits_it(tmp_path):
    calls = 0

    def flaky(job, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("first attempt fails")
        return {"attempt": calls}

    runtime = _runtime(tmp_path, {"fake.flaky": flaky})
    mission = runtime.create_mission("retry", [], [])
    template = _template(
        [
            {
                "id": "target",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.flaky",
            },
            {"id": "failure", "role": "aggregate", "kind": "program", "handler": "sink"},
            {"id": "success", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "target-failure", "from": "target", "to": "failure", "on": "failed"},
            {"id": "target-success", "from": "target", "to": "success", "on": "succeeded"},
        ],
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template)
    failed = advance_graph(runtime, graph_run.graph_run_id)
    assert failed["status"] == "running"
    assert failed["node_runs"][0]["status"] == "failed"

    result = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="retry-node",
        node_id="target",
        expected_event_cursor=_cursor(runtime, graph_run.graph_run_id),
        idempotency_key="retry-target-1",
        reason="transient worker failure",
    )

    assert result["ok"] is True
    assert result["intervention"]["status"] == "applied"
    assert result["result"]["synthetic_handoff_ids"] == []
    snapshot = runtime.store.get_graph_run(graph_run.graph_run_id).template_snapshot
    target = next(node for node in snapshot["nodes"] if node["id"] == "target")
    assert target["max_runs"] == 2
    failure_handoff = next(
        row
        for row in runtime.store.list_graph_handoffs(graph_run.graph_run_id)
        if row.edge_id == "target-failure"
    )
    assert failure_handoff.status.value == "consumed"
    assert failure_handoff.consumed_by_node_run_id.startswith("intervention:")

    retried = advance_graph(runtime, graph_run.graph_run_id)
    target_runs = [row for row in retried["node_runs"] if row["node_id"] == "target"]
    assert calls == 2
    assert [row["status"] for row in target_runs] == ["failed", "succeeded"]
    assert {row["node_id"] for row in retried["node_runs"]} == {"target"}

    replay = replay_graph_run(runtime, graph_run.graph_run_id)
    intervention_events = [
        event for event in replay["events"] if event["event_type"].startswith("graph_intervention_")
    ]
    assert [event["event_type"] for event in intervention_events] == [
        "graph_intervention_created",
        "graph_intervention_applied",
    ]
    assert all(event["scope"] == "graph" for event in intervention_events)


def test_retry_join_all_recreates_each_inbound_source_with_clean_payload(tmp_path):
    runtime = _runtime(tmp_path, {"fake.target": lambda job, context: {"retried": True}})
    store = runtime.store
    mission = runtime.create_mission("join retry", [], [])
    template = _template(
        [
            {"id": "left", "role": "planner", "kind": "program", "handler": "left"},
            {"id": "right", "role": "planner", "kind": "program", "handler": "right"},
            {
                "id": "target",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.target",
                "join": "all",
            },
            {"id": "failure", "role": "aggregate", "kind": "program", "handler": "sink"},
        ],
        [
            {"id": "left-target", "from": "left", "to": "target", "on": "succeeded"},
            {"id": "right-target", "from": "right", "to": "target", "on": "succeeded"},
            {"id": "target-failure", "from": "target", "to": "failure", "on": "failed"},
        ],
    )
    graph_run = store.create_graph_run(
        GraphRunRecord.create(
            "join-retry-graph",
            mission.mission_id,
            template.template_id,
            template.version,
            mission.plan_version,
            template_snapshot=template.to_json_dict(),
        )
    )
    store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.RUNNING)
    original_handoffs = []
    for sequence, node_id in enumerate(("left", "right"), start=1):
        node_run = store.create_node_run(
            NodeRunRecord.create(
                f"{node_id}-run",
                graph_run.graph_run_id,
                mission.mission_id,
                node_id,
                "planner",
                "program",
                sequence,
                {},
            )
        )
        store.complete_node_run(
            node_run.node_run_id,
            NodeRunStatus.SUCCEEDED,
            {node_id: True, "shared": 7},
            [],
            edge_decision="succeeded",
        )
        original_handoffs.append(
            store.create_graph_handoff(
                GraphHandoffRecord.create(
                    f"{node_id}-target-handoff",
                    graph_run.graph_run_id,
                    mission.mission_id,
                    f"{node_id}-target",
                    node_run.node_run_id,
                    node_id,
                    "target",
                    "succeeded",
                    {node_id: True, "shared": 7},
                )
            )
        )
    failed_run = store.create_node_run(
        NodeRunRecord.create(
            "join-target-failed",
            graph_run.graph_run_id,
            mission.mission_id,
            "target",
            "worker",
            "worker",
            3,
            {
                "left": True,
                "right": True,
                "shared": 7,
                "_handoffs": {"left": {"shared": 7}, "right": {"shared": 7}},
            },
        )
    )
    store.complete_node_run(
        failed_run.node_run_id,
        NodeRunStatus.FAILED,
        {},
        [],
        edge_decision="failed",
        error={"error_class": "worker_crash", "message": "boom"},
    )
    store.consume_graph_handoffs(
        [row.handoff_id for row in original_handoffs],
        failed_run.node_run_id,
    )
    store.create_graph_handoff(
        GraphHandoffRecord.create(
            "join-target-failure-handoff",
            graph_run.graph_run_id,
            mission.mission_id,
            "target-failure",
            failed_run.node_run_id,
            "target",
            "failure",
            "failed",
            {},
        )
    )

    result = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="retry-node",
        node_id="target",
        expected_event_cursor=_cursor(runtime, graph_run.graph_run_id),
        idempotency_key="retry-join-target",
        reason="retry joined target",
    )

    assert result["ok"] is True
    pending_target = [
        row
        for row in store.list_graph_handoffs(graph_run.graph_run_id)
        if row.to_node == "target" and row.status.value == "pending"
    ]
    assert {row.edge_id for row in pending_target} == {"left-target", "right-target"}
    assert {row.from_node for row in pending_target} == {"left", "right"}
    assert all("_handoffs" not in row.payload for row in pending_target)
    assert all(row.payload["shared"] == 7 for row in pending_target)

    report = advance_graph(runtime, graph_run.graph_run_id)
    target_runs = [row for row in report["node_runs"] if row["node_id"] == "target"]
    assert [row["status"] for row in target_runs] == ["failed", "succeeded"]


def test_cancel_pending_branch_skips_worker_and_independent_branch_continues(tmp_path):
    calls = {"left": 0, "right": 0}

    def left(job, context):
        calls["left"] += 1
        return {"side": "left"}

    def right(job, context):
        calls["right"] += 1
        return {"side": "right"}

    runtime = _runtime(tmp_path, {"fake.left": left, "fake.right": right})
    _, _, graph_run = _start_fanout(runtime)

    canceled = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="cancel-branch",
        node_id="left",
        expected_event_cursor=_cursor(runtime, graph_run.graph_run_id),
        idempotency_key="cancel-left-1",
        reason="left branch no longer needed",
    )

    assert canceled["ok"] is True
    left_run = next(
        row
        for row in runtime.store.list_node_runs(graph_run.graph_run_id)
        if row.node_id == "left"
    )
    assert left_run.status == NodeRunStatus.SKIPPED
    assert left_run.edge_decision == "canceled"
    assert left_run.error["intervention_id"] == canceled["intervention"]["intervention_id"]
    assert calls == {"left": 0, "right": 0}

    report = advance_graph(runtime, graph_run.graph_run_id)
    assert report["status"] == "succeeded"
    assert calls == {"left": 0, "right": 1}
    assert [row["node_id"] for row in report["node_runs"]] == [
        "source",
        "left",
        "right",
    ]


def test_cancel_branch_creates_explicit_canceled_route_without_advancing_it(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("cancel route", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "program", "handler": "source"},
            {
                "id": "target",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.target",
            },
            {
                "id": "fallback",
                "role": "aggregate",
                "kind": "program",
                "handler": "fallback",
            },
        ],
        [
            {"id": "source-target", "from": "source", "to": "target", "on": "succeeded"},
            {"id": "target-fallback", "from": "target", "to": "fallback", "on": "canceled"},
        ],
    )
    handlers = GraphNodeExecutorRegistry()
    handlers.register(
        "source",
        lambda context: {
            "status": "succeeded",
            "outcome": "succeeded",
            "output_payload": {"seed": 1},
        },
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template)
    advance_graph(runtime, graph_run.graph_run_id, registry=handlers)

    result = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="cancel-branch",
        node_id="target",
        expected_event_cursor=_cursor(runtime, graph_run.graph_run_id),
        idempotency_key="cancel-with-route",
        reason="use explicit fallback",
    )

    assert result["ok"] is True
    handoffs = runtime.store.list_graph_handoffs(graph_run.graph_run_id)
    canceled = next(row for row in handoffs if row.edge_id == "target-fallback")
    assert canceled.status.value == "pending"
    assert canceled.outcome == "canceled"
    assert canceled.to_node == "fallback"
    assert all(row.node_id != "fallback" for row in runtime.store.list_node_runs(graph_run.graph_run_id))


def test_unsafe_join_cancel_is_rejected_without_graph_state_side_effects(tmp_path):
    runtime = _runtime(tmp_path)
    _, _, graph_run = _start_fanout(runtime, with_join=True)
    before = _state(runtime, graph_run.graph_run_id)

    rejected = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="cancel-branch",
        node_id="left",
        expected_event_cursor=_cursor(runtime, graph_run.graph_run_id),
        idempotency_key="unsafe-left",
        reason="try unsafe cancel",
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "unsafe_join_cancel"
    assert rejected["error"]["details"]["join_node_ids"] == ["join"]
    assert _state(runtime, graph_run.graph_run_id) == before
    assert runtime.store.list_graph_interventions(graph_run.graph_run_id)[0]["status"] == "rejected"


def test_other_graph_events_in_same_mission_do_not_stale_refreshed_local_cursor(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("two graphs", [], [])
    graph_a, _ = _create_pending_graph_in_mission(
        runtime,
        mission.mission_id,
        "graph-a",
    )
    cursor_before_graph_b = _cursor(runtime, graph_a.graph_run_id)

    _create_pending_graph_in_mission(runtime, mission.mission_id, "graph-b")
    refreshed_graph_a_cursor = _cursor(runtime, graph_a.graph_run_id)
    mission_cursor = runtime.store.list_events(mission.mission_id)[-1].sequence

    assert refreshed_graph_a_cursor == cursor_before_graph_b
    assert mission_cursor > refreshed_graph_a_cursor
    result = intervene_graph(
        runtime,
        graph_run_id=graph_a.graph_run_id,
        action="cancel-branch",
        node_id="target",
        expected_event_cursor=refreshed_graph_a_cursor,
        idempotency_key="graph-a-after-graph-b",
        reason="unrelated graph must not block control",
    )

    assert result["ok"] is True
    assert result["intervention"]["status"] == "applied"


@pytest.mark.parametrize("event_kind", ["node", "handoff", "job", "intervention"])
def test_target_graph_control_event_after_cursor_is_stale(tmp_path, event_kind):
    runtime = _runtime(tmp_path)
    store = runtime.store
    mission = runtime.create_mission("control cursor", [], [])
    graph_run, source_run = _create_pending_graph_in_mission(
        runtime,
        mission.mission_id,
        "control-graph",
    )
    cursor = _cursor(runtime, graph_run.graph_run_id)

    if event_kind == "node":
        store.create_node_run(
            NodeRunRecord.create(
                "observer-control-run",
                graph_run.graph_run_id,
                mission.mission_id,
                "observer",
                "worker",
                "worker",
                2,
                {},
            )
        )
    elif event_kind == "handoff":
        store.create_graph_handoff(
            GraphHandoffRecord.create(
                "extra-control-handoff",
                graph_run.graph_run_id,
                mission.mission_id,
                "source-target",
                source_run.node_run_id,
                "source",
                "target",
                "succeeded",
                {"extra": True},
            )
        )
    elif event_kind == "job":
        job = store.create_job(
            mission.mission_id,
            "fake.observer",
            "observer-control-job",
            {},
            30,
            0,
        )
        store.bind_graph_node_job(
            graph_run.graph_run_id,
            "observer",
            1,
            job.job_id,
        )
    else:
        first = intervene_graph(
            runtime,
            graph_run_id=graph_run.graph_run_id,
            action="cancel-branch",
            node_id="other",
            expected_event_cursor=cursor,
            idempotency_key="first-control-intervention",
            reason="create graph-local intervention event",
        )
        assert first["ok"] is True

    rejected = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="cancel-branch",
        node_id="target",
        expected_event_cursor=cursor,
        idempotency_key=f"stale-after-{event_kind}",
        reason="old graph cursor must be rejected",
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "stale_event_cursor"
    details = rejected["error"]["details"]
    assert details["expected"] == cursor
    assert details["actual"] == details["control_cursor"]
    assert details["control_cursor"] > cursor
    assert details["mission_cursor"] >= details["control_cursor"]
    assert details["cursor_scope"] == "graph_control"
    assert details["conflict_reason"] == "behind_graph_control_state"


def test_stale_cursor_is_audited_without_events_or_graph_state_changes(tmp_path):
    runtime = _runtime(tmp_path)
    _, _, graph_run = _start_fanout(runtime)
    cursor = _cursor(runtime, graph_run.graph_run_id)
    event_count = len(runtime.store.list_events(graph_run.mission_id))
    before = _state(runtime, graph_run.graph_run_id)

    rejected = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="cancel-branch",
        node_id="left",
        expected_event_cursor=cursor - 1,
        idempotency_key="stale-left",
        reason="stale request",
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "stale_event_cursor"
    assert rejected["error"]["details"] == {
        "expected": cursor - 1,
        "actual": cursor,
        "control_cursor": cursor,
        "mission_cursor": cursor,
        "cursor_scope": "graph_control",
        "conflict_reason": "behind_graph_control_state",
    }
    future = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="cancel-branch",
        node_id="left",
        expected_event_cursor=cursor + 1,
        idempotency_key="future-left",
        reason="future request",
    )
    assert future["error"]["code"] == "stale_event_cursor"
    assert future["error"]["details"]["control_cursor"] == cursor
    assert future["error"]["details"]["mission_cursor"] == cursor
    assert future["error"]["details"]["conflict_reason"] == "ahead_of_mission"
    assert len(runtime.store.list_events(graph_run.mission_id)) == event_count
    assert _state(runtime, graph_run.graph_run_id) == before
    assert [
        row["status"]
        for row in runtime.store.list_graph_interventions(graph_run.graph_run_id)
    ] == ["rejected", "rejected"]


def test_idempotent_replay_does_not_repeat_side_effects_and_key_conflict_rejects(tmp_path):
    runtime = _runtime(tmp_path)
    _, _, graph_run = _start_fanout(runtime)
    cursor = _cursor(runtime, graph_run.graph_run_id)
    request = {
        "graph_run_id": graph_run.graph_run_id,
        "action": "cancel-branch",
        "node_id": "left",
        "expected_event_cursor": cursor,
        "idempotency_key": "cancel-left-once",
        "reason": "one logical request",
    }

    first = intervene_graph(runtime, **request)
    after_first = _state(runtime, graph_run.graph_run_id)
    event_count = len(runtime.store.list_events(graph_run.mission_id))
    second = intervene_graph(runtime, **request)
    conflict = intervene_graph(runtime, **{**request, "reason": "different request"})

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["idempotent_replay"] is True
    assert second["intervention"]["intervention_id"] == first["intervention"]["intervention_id"]
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "idempotency_key_conflict"
    assert _state(runtime, graph_run.graph_run_id) == after_first
    assert len(runtime.store.list_events(graph_run.mission_id)) == event_count
    assert len(runtime.store.list_graph_interventions(graph_run.graph_run_id)) == 1


def test_concurrent_interventions_with_one_cursor_apply_at_most_once(tmp_path):
    runtime = _runtime(tmp_path)
    _, _, graph_run = _start_fanout(runtime)
    cursor = _cursor(runtime, graph_run.graph_run_id)
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def cancel(key: str) -> None:
        barrier.wait(timeout=3)
        results.append(
            intervene_graph(
                runtime,
                graph_run_id=graph_run.graph_run_id,
                action="cancel-branch",
                node_id="left",
                expected_event_cursor=cursor,
                idempotency_key=key,
                reason="concurrent cancel",
            )
        )

    threads = [
        threading.Thread(target=cancel, args=("concurrent-a",)),
        threading.Thread(target=cancel, args=("concurrent-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert sum(result["ok"] for result in results) == 1
    rejected = next(result for result in results if not result["ok"])
    assert rejected["error"]["code"] == "stale_event_cursor"
    left_runs = [
        row
        for row in runtime.store.list_node_runs(graph_run.graph_run_id)
        if row.node_id == "left"
    ]
    assert len(left_runs) == 1
    assert left_runs[0].status == NodeRunStatus.SKIPPED


def test_running_retry_and_leased_cancel_targets_are_rejected(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.store
    mission = runtime.create_mission("active targets", [], [])
    template = _template(
        [
            {"id": "source", "role": "planner", "kind": "program", "handler": "source"},
            {
                "id": "target",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.target",
            },
        ],
        [{"id": "source-target", "from": "source", "to": "target", "on": "succeeded"}],
    )
    graph_run = store.create_graph_run(
        GraphRunRecord.create(
            "active-graph",
            mission.mission_id,
            template.template_id,
            template.version,
            mission.plan_version,
            template_snapshot=template.to_json_dict(),
        )
    )
    store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.RUNNING)
    running = store.create_node_run(
        NodeRunRecord.create(
            "running-target",
            graph_run.graph_run_id,
            mission.mission_id,
            "target",
            "worker",
            "worker",
            1,
            {},
        )
    )
    store.update_node_run_status(running.node_run_id, NodeRunStatus.RUNNING)
    running_rejected = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="retry-node",
        node_id="target",
        expected_event_cursor=_cursor(runtime, graph_run.graph_run_id),
        idempotency_key="running-target",
        reason="must reject running target",
    )
    assert running_rejected["error"]["code"] == "target_node_active"

    lease_mission = runtime.create_mission("leased target", [], [])
    leased_graph = store.create_graph_run(
        GraphRunRecord.create(
            "leased-graph",
            lease_mission.mission_id,
            template.template_id,
            template.version,
            lease_mission.plan_version,
            template_snapshot=template.to_json_dict(),
        )
    )
    store.update_graph_run_status(leased_graph.graph_run_id, GraphRunStatus.RUNNING)
    source_run = store.create_node_run(
        NodeRunRecord.create(
            "leased-source-run",
            leased_graph.graph_run_id,
            lease_mission.mission_id,
            "source",
            "planner",
            "program",
            1,
            {},
        )
    )
    store.complete_node_run(
        source_run.node_run_id,
        NodeRunStatus.SUCCEEDED,
        {"seed": 1},
        [],
        edge_decision="succeeded",
    )
    store.create_graph_handoff(
        GraphHandoffRecord.create(
            "leased-target-handoff",
            leased_graph.graph_run_id,
            lease_mission.mission_id,
            "source-target",
            source_run.node_run_id,
            "source",
            "target",
            "succeeded",
            {"seed": 1},
        )
    )
    job = store.create_job(
        lease_mission.mission_id,
        "fake.target",
        "leased-target-job",
        {},
        30,
        0,
    )
    store.bind_graph_node_job(leased_graph.graph_run_id, "target", 1, job.job_id)
    lease = store.acquire_job_lease(job.job_id, "worker-a", 60)

    leased_rejected = intervene_graph(
        runtime,
        graph_run_id=leased_graph.graph_run_id,
        action="cancel-branch",
        node_id="target",
        expected_event_cursor=_cursor(runtime, leased_graph.graph_run_id),
        idempotency_key="leased-target",
        reason="must reject leased target",
    )

    assert leased_rejected["error"]["code"] == "target_job_active"
    assert [row.lease_id for row in store.list_active_job_leases(job.job_id)] == [lease.lease_id]
    assert store.get_job(job.job_id).status.value == "leased"


def test_intervention_does_not_touch_unrelated_graph_or_branch_leases(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.store
    foreign_mission = runtime.create_mission("foreign graph", [], [])
    foreign_template = _template(
        [
            {
                "id": "foreign",
                "role": "worker",
                "kind": "worker",
                "capability": "fake.foreign",
            }
        ],
        [],
    )
    foreign_graph = store.create_graph_run(
        GraphRunRecord.create(
            "foreign-graph",
            foreign_mission.mission_id,
            foreign_template.template_id,
            foreign_template.version,
            foreign_mission.plan_version,
            template_snapshot=foreign_template.to_json_dict(),
        )
    )
    store.update_graph_run_status(foreign_graph.graph_run_id, GraphRunStatus.RUNNING)
    foreign_job = store.create_job(
        foreign_mission.mission_id,
        "fake.foreign",
        "foreign-job",
        {},
        30,
        0,
    )
    store.bind_graph_node_job(foreign_graph.graph_run_id, "foreign", 1, foreign_job.job_id)
    foreign_lease = store.acquire_job_lease(foreign_job.job_id, "foreign-worker", 60)

    _, _, graph_run = _start_fanout(runtime)
    branch_job = store.create_job(
        graph_run.mission_id,
        "fake.right",
        "independent-right-job",
        {},
        30,
        0,
    )
    store.bind_graph_node_job(graph_run.graph_run_id, "right", 1, branch_job.job_id)
    branch_lease = store.acquire_job_lease(branch_job.job_id, "right-worker", 60)

    result = intervene_graph(
        runtime,
        graph_run_id=graph_run.graph_run_id,
        action="cancel-branch",
        node_id="left",
        expected_event_cursor=_cursor(runtime, graph_run.graph_run_id),
        idempotency_key="cancel-left-with-other-leases",
        reason="cancel only left",
    )

    assert result["ok"] is True
    assert [row.lease_id for row in store.list_active_job_leases(foreign_job.job_id)] == [
        foreign_lease.lease_id
    ]
    assert [row.lease_id for row in store.list_active_job_leases(branch_job.job_id)] == [
        branch_lease.lease_id
    ]
    assert store.get_job(foreign_job.job_id).status.value == "leased"
    assert store.get_job(branch_job.job_id).status.value == "leased"
