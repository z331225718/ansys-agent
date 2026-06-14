from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.graph_template import GraphTemplateError, load_graph_template, resolve_template_path
from aedt_agent.agent.handoff import HandoffValidationError, validate_handoff


def test_load_brd_local_cut_graph_template_from_yaml():
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))

    assert template.template_id == "brd_local_cut_build"
    assert [node.node_id for node in template.nodes] == [
        "planner",
        "input_validator",
        "real_build_worker",
        "model_review_scorecard",
        "approval_gate",
    ]
    assert template.node("real_build_worker").capability == "brd.local_cut.build"
    assert template.handoffs["validated_brd_local_cut_request"].required_fields == [
        "layout_file",
        "signal_nets",
        "reference_nets",
        "local_cut_region",
        "artifact_dir",
        "adapter_mode",
    ]


def test_graph_template_rejects_edges_to_unknown_nodes(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
id: bad
version: 1
nodes:
  - id: only
    role: worker
    kind: worker
edges:
  - from: only
    to: missing
    on: succeeded
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(GraphTemplateError, match="unknown node"):
        load_graph_template(path)


def test_validate_handoff_rejects_missing_required_fields():
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))

    with pytest.raises(HandoffValidationError, match="missing required fields"):
        validate_handoff(template.handoffs["brd_local_cut_request"], {"layout_file": "case.brd"})


def test_validate_handoff_returns_payload_when_complete():
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    payload = {
        "layout_file": "case.brd",
        "signal_nets": ["TX_P", "TX_N"],
        "reference_nets": ["GND"],
        "local_cut_region": {"type": "bbox"},
    }

    assert validate_handoff(template.handoffs["brd_local_cut_request"], payload) == payload
