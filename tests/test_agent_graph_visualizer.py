from __future__ import annotations

from aedt_agent.agent.graph_visualizer import render_graph_live, render_graph_mermaid


def test_render_graph_live_shows_all_nodes():
    template = {
        "template_id": "test",
        "nodes": [
            {"id": "planner", "role": "planner", "kind": "llm"},
            {"id": "worker", "role": "worker", "kind": "worker", "capability": "x"},
            {"id": "scorecard", "role": "scorecard", "kind": "program"},
        ],
        "edges": [
            {"id": "p-w", "from": "planner", "to": "worker", "on": "succeeded"},
            {"id": "w-s", "from": "worker", "to": "scorecard", "on": "succeeded"},
        ],
    }
    node_runs = [
        {"node_id": "planner", "status": "succeeded", "sequence": 1, "edge_decision": "succeeded"},
        {"node_id": "worker", "status": "running", "sequence": 2},
    ]
    handoffs = [
        {"edge_id": "p-w", "from_node": "planner", "to_node": "worker", "outcome": "succeeded", "status": "consumed"},
    ]

    result = render_graph_live(template, node_runs, handoffs)
    assert "planner" in result
    assert "worker" in result
    assert "scorecard" in result
    assert "succeeded" in result
    assert "running" in result


def test_render_graph_live_shows_skipped_and_failed():
    template = {
        "template_id": "test",
        "nodes": [
            {"id": "bad", "role": "worker", "kind": "worker", "capability": "x"},
        ],
        "edges": [],
    }
    node_runs = [{"node_id": "bad", "status": "skipped", "sequence": 1, "edge_decision": "skipped"}]
    result = render_graph_live(template, node_runs, [])
    assert "skipped" in result
    assert "bad" in result


def test_render_graph_mermaid_generates_valid_syntax():
    template = {
        "template_id": "test",
        "nodes": [{"id": "a", "role": "planner", "kind": "llm"}],
        "edges": [],
    }
    result = render_graph_mermaid(template, [], [])
    assert "```mermaid" in result
    assert "flowchart TD" in result
    assert "a" in result


def test_render_graph_mermaid_with_conditional_edge():
    template = {
        "template_id": "test",
        "nodes": [
            {"id": "a", "role": "planner", "kind": "llm"},
            {"id": "b", "role": "scorecard", "kind": "program"},
        ],
        "edges": [{"id": "a-b", "from": "a", "to": "b", "on": "passed", "if": "score >= 0.8"}],
    }
    result = render_graph_mermaid(template, [], [])
    assert "score >= 0.8" in result
