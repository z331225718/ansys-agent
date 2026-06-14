from __future__ import annotations

from aedt_agent.agent.graph_scheduler import merge_handoff_payloads, ready_nodes
from aedt_agent.agent.graph_template import GraphEdge, GraphNode, GraphTemplate
from aedt_agent.agent.mission import (
    GraphHandoffRecord,
    NodeRunRecord,
    NodeRunStatus,
)


def _template(nodes, edges):
    return GraphTemplate("test", 1, "", nodes, edges, {})


def _node(node_id, *, join="any", after=None, max_runs=1):
    return GraphNode(
        node_id=node_id,
        role="worker",
        kind="worker",
        capability=f"fake.{node_id}",
        join=join,
        after=after or [],
        max_runs=max_runs,
    )


def _edge(edge_id, source, target, *, outcome="succeeded", after=None):
    return GraphEdge(edge_id, source, target, outcome, after or [], 1)


def _node_run(node_id, sequence, status=NodeRunStatus.SUCCEEDED):
    record = NodeRunRecord.create(
        f"run-{sequence}",
        "graph-1",
        "mission-1",
        node_id,
        "worker",
        "worker",
        sequence,
        {},
    )
    if status == NodeRunStatus.CREATED:
        return record
    if status == NodeRunStatus.RUNNING:
        return record.with_status(status)
    return record.with_completion(status, {}, [])


def _handoff(handoff_id, edge_id, source, target, payload):
    return GraphHandoffRecord.create(
        handoff_id,
        "graph-1",
        "mission-1",
        edge_id,
        f"run-{source}",
        source,
        target,
        "succeeded",
        payload,
    )


def test_root_node_is_ready_only_before_first_run():
    source = _node("source")
    template = _template([source], [])

    first = ready_nodes(template, [], [])
    after_run = ready_nodes(template, [_node_run("source", 1)], [])

    assert [item.node.node_id for item in first] == ["source"]
    assert after_run == []
    assert first[0].run_index == 1
    assert first[0].input_payload == {}


def test_join_any_is_ready_with_one_matching_handoff():
    source = _node("source")
    target = _node("target", join="any")
    template = _template([source, target], [_edge("source-target", "source", "target")])
    handoff = _handoff("h1", "source-target", "source", "target", {"value": 1})

    ready = ready_nodes(template, [_node_run("source", 1)], [handoff])

    assert [item.node.node_id for item in ready] == ["target"]
    assert ready[0].handoff_ids == ["h1"]
    assert ready[0].input_payload["value"] == 1


def test_join_all_waits_for_every_inbound_source():
    left = _node("left")
    right = _node("right")
    target = _node("target", join="all")
    edges = [
        _edge("left-target", "left", "target"),
        _edge("right-target", "right", "target"),
    ]
    template = _template([left, right, target], edges)
    left_handoff = _handoff("h1", "left-target", "left", "target", {"left": 1})
    right_handoff = _handoff("h2", "right-target", "right", "target", {"right": 2})
    runs = [_node_run("left", 1), _node_run("right", 2)]

    waiting = ready_nodes(template, runs, [left_handoff])
    ready = ready_nodes(template, runs, [left_handoff, right_handoff])

    assert waiting == []
    assert [item.node.node_id for item in ready] == ["target"]
    assert ready[0].handoff_ids == ["h1", "h2"]


def test_after_blocks_until_named_node_is_terminal():
    source = _node("source")
    tester = _node("tester")
    target = _node("target", after=["tester"])
    template = _template(
        [source, tester, target],
        [_edge("source-target", "source", "target")],
    )
    handoff = _handoff("h1", "source-target", "source", "target", {"value": 1})

    blocked = ready_nodes(
        template,
        [_node_run("source", 1), _node_run("tester", 2, NodeRunStatus.RUNNING)],
        [handoff],
    )
    ready = ready_nodes(
        template,
        [_node_run("source", 1), _node_run("tester", 2, NodeRunStatus.SUCCEEDED)],
        [handoff],
    )

    assert blocked == []
    assert [item.node.node_id for item in ready] == ["target"]


def test_merge_single_handoff_promotes_payload_and_keeps_provenance():
    handoff = _handoff("h1", "source-target", "source", "target", {"value": 1})

    payload = merge_handoff_payloads([handoff])

    assert payload["value"] == 1
    assert payload["_handoffs"] == {"source": {"value": 1}}


def test_merge_multiple_handoffs_does_not_promote_conflicting_fields():
    left = _handoff("h1", "left-target", "left", "target", {"value": 1, "left": True})
    right = _handoff("h2", "right-target", "right", "target", {"value": 2, "right": True})

    payload = merge_handoff_payloads([left, right])

    assert "value" not in payload
    assert payload["left"] is True
    assert payload["right"] is True
    assert payload["_handoffs"]["left"]["value"] == 1
    assert payload["_handoffs"]["right"]["value"] == 2


def test_node_max_runs_prevents_unbounded_reentry():
    source = _node("source")
    target = _node("target", max_runs=1)
    template = _template([source, target], [_edge("source-target", "source", "target")])
    handoff = _handoff("h1", "source-target", "source", "target", {"value": 1})

    ready = ready_nodes(
        template,
        [_node_run("source", 1), _node_run("target", 2)],
        [handoff],
    )

    assert ready == []


def test_waiting_approval_node_is_not_scheduled_again():
    source = _node("source")
    target = _node("target", max_runs=2)
    template = _template([source, target], [_edge("source-target", "source", "target")])
    handoff = _handoff("h1", "source-target", "source", "target", {"value": 1})

    ready = ready_nodes(
        template,
        [
            _node_run("source", 1),
            _node_run("target", 2, NodeRunStatus.WAITING_APPROVAL),
        ],
        [handoff],
    )

    assert ready == []
