from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.graph_template import GraphTemplateError, load_graph_template, resolve_template_path
from aedt_agent.agent.handoff import HandoffValidationError, validate_handoff


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "graph.yaml"
    path.write_text(content.strip(), encoding="utf-8")
    return path


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


def test_real_solve_graph_template_has_approval_before_solve():
    template = load_graph_template("brd_real_solve_evidence")

    assert [node.node_id for node in template.nodes] == [
        "model_validator",
        "model_approval_gate",
        "real_solve_worker",
        "channel_score_worker",
        "real_solve_scorecard",
    ]
    assert (
        template.node("real_solve_worker").capability
        == "brd.local_cut.solve"
    )
    assert (
        template.node("channel_score_worker").capability
        == "brd.channel.score"
    )


def test_reviewed_model_loop_template_uses_real_workers_and_report():
    template = load_graph_template("brd_reviewed_model_optimize_loop")

    assert [node.node_id for node in template.nodes] == [
        "prepare_working_project",
        "real_solve_worker",
        "touchstone_export_worker",
        "tdr_export_worker",
        "channel_score_worker",
        "iteration_qualifier_worker",
        "optimization_decider",
        "iteration_qualification_approval_gate",
        "action_approval_gate",
        "geometry_validator_worker",
        "model_edit_worker",
        "prepare_next_solve",
        "optimization_report",
    ]
    assert template.node("real_solve_worker").capability == "brd.local_cut.solve"
    assert template.node("touchstone_export_worker").capability == "brd.touchstone.export"
    assert template.node("tdr_export_worker").capability == "brd.tdr.export"
    assert template.node("channel_score_worker").capability == "brd.channel.score"
    assert template.node("iteration_qualifier_worker").capability == "brd.iteration.qualify"
    assert template.node("geometry_validator_worker").capability == "brd.geometry.validate"
    assert template.node("model_edit_worker").capability == "brd.model.edit"
    assert template.node("optimization_decider").kind == "agent"
    assert template.node("optimization_decider").system_prompt == "optimization_decider_prompt"
    assert template.node("optimization_decider").handler == "brd.optimization.decide_next_action"
    assert template.node("channel_score_worker").input_schema == "tdr_export_result"
    assert "tdr_observation_port" in template.handoffs["next_action"].required_fields
    assert template.handoffs["scorecard_report"].required_fields == [
        "status",
        "checks",
        "optimization_history_csv",
        "optimization_history_rows",
    ]


def test_via_optimize_demo_keeps_llm_decisions_and_worker_execution_separate():
    template = load_graph_template("via_optimize_demo")

    assert [node.node_id for node in template.nodes] == [
        "optimization_planner",
        "via_parameter_proposer",
        "proposal_approval_gate",
        "local_cut_build_worker",
        "real_solve_worker",
        "channel_score_worker",
        "optimization_decider",
        "optimization_scorecard",
    ]
    assert template.node("via_parameter_proposer").kind == "agent"
    assert template.node("optimization_decider").kind == "agent"
    assert template.node("local_cut_build_worker").capability == "brd.local_cut.build"
    assert template.node("real_solve_worker").capability == "brd.local_cut.solve"
    assert template.node("channel_score_worker").capability == "brd.channel.score"
    assert template.handoffs["bounded_channel_evidence"].required_fields == [
        "status",
        "touchstone_kind",
        "return_loss_trace",
        "insertion_loss_trace",
        "sdd11_worst_db",
        "sdd11_worst_frequency_ghz",
        "sdd21_worst_db_in_band",
        "tdr_observation_port",
        "tdr_peak_deviation_ohm",
        "tdr_anomaly_window",
        "tdr_proximity_mse_ohm2",
        "tdr_proximity_rmse_ohm",
        "tdr_flatness_msd_ohm2",
        "tdr_flatness_rms_step_ohm",
        "rl_violation_sum_db",
        "rl_violation_max_db",
        "rl_violation_point_count",
        "optimization_objective",
        "plot_artifacts",
        "pass_fail_reason",
        "artifact_refs",
    ]


@pytest.mark.parametrize(
    ("template_id", "expected_edge_ids"),
    [
        (
            "brd_local_cut_build",
            [
                "planner-to-validator",
                "validator-to-build",
                "validator-to-approval",
                "build-to-scorecard",
                "build-to-approval",
                "scorecard-to-approval",
                "scorecard-failed-to-approval",
            ],
        ),
        (
            "brd_local_cut_solve_evidence",
            [
                "planner-to-validator",
                "validator-to-score-worker",
                "score-worker-to-scorecard",
                "scorecard-to-approval",
            ],
        ),
        (
            "brd_recorded_void_action",
            [
                "validator-to-approval",
                "approval-to-action-worker",
                "action-worker-to-scorecard",
            ],
        ),
        (
            "brd_real_solve_evidence",
            [
                "validate-to-approval",
                "approval-to-solve",
                "solve-to-score",
                "score-to-scorecard",
            ],
        ),
    ],
)
def test_builtin_graph_templates_use_explicit_stable_edges(template_id, expected_edge_ids):
    template = load_graph_template(resolve_template_path(template_id))

    assert [edge.edge_id for edge in template.edges] == expected_edge_ids
    assert all(node.max_runs == 1 for node in template.nodes)


def test_all_yaml_templates_load_without_error():
    import os
    from pathlib import Path

    templates_dir = (
        Path(__file__).resolve().parents[1] / "docs" / "agent_templates"
    )
    yaml_files = sorted(templates_dir.glob("*.yaml"))
    assert yaml_files, "no YAML templates found"

    for path in yaml_files:
        template = load_graph_template(path)
        assert template.template_id, f"empty template_id in {path.name}"


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


def test_graph_template_loads_join_after_handlers_and_limits(tmp_path):
    template = load_graph_template(
        _write_yaml(
            tmp_path,
            """
id: parallel
version: 1
nodes:
  - id: source
    role: planner
    kind: llm
    output_schema: request
  - id: left
    role: worker
    kind: worker
    capability: fake.left
    input_schema: request
    output_schema: result
  - id: right
    role: worker
    kind: worker
    capability: fake.right
    input_schema: request
    output_schema: result
  - id: join
    role: aggregate
    kind: program
    handler: aggregate
    join: all
    after: [left, right]
    max_runs: 2
edges:
  - id: source-left
    from: source
    to: left
    on: succeeded
  - id: source-right
    from: source
    to: right
    on: succeeded
  - id: left-join
    from: left
    to: join
    on: succeeded
    max_traversals: 2
  - from: right
    to: join
    on: succeeded
handoffs:
  request:
    required_fields: [value]
  result:
    required_fields: [value]
""",
        )
    )

    join = template.node("join")
    assert join.join == "all"
    assert join.after == ["left", "right"]
    assert join.max_runs == 2
    assert join.handler == "aggregate"
    assert template.edges[0].edge_id == "source-left"
    assert template.edges[2].max_traversals == 2
    assert template.edges[3].edge_id == "3:right:join:succeeded"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            """
id: bad
version: 1
nodes:
  - {id: a, role: planner, kind: llm}
  - {id: b, role: scorecard, kind: program}
edges:
  - {id: same, from: a, to: b, on: succeeded}
  - {id: same, from: a, to: b, on: failed}
handoffs: {}
""",
            "duplicate edge ids",
        ),
        (
            """
id: bad
version: 1
nodes:
  - {id: a, role: planner, kind: llm, join: maybe}
edges: []
handoffs: {}
""",
            "unsupported join",
        ),
        (
            """
id: bad
version: 1
nodes:
  - {id: a, role: planner, kind: llm, max_runs: 0}
edges: []
handoffs: {}
""",
            "max_runs",
        ),
        (
            """
id: bad
version: 1
nodes:
  - {id: a, role: planner, kind: llm, after: [missing]}
edges: []
handoffs: {}
""",
            "unknown node",
        ),
        (
            """
id: bad
version: 1
nodes:
  - {id: a, role: worker, kind: worker}
edges: []
handoffs: {}
""",
            "capability",
        ),
        (
            """
id: bad
version: 1
nodes:
  - {id: a, role: custom, kind: program}
edges: []
handoffs: {}
""",
            "handler",
        ),
        (
            """
id: bad
version: 1
nodes:
  - {id: a, role: planner, kind: llm, output_schema: missing}
edges: []
handoffs: {}
""",
            "unknown handoff schema",
        ),
    ],
)
def test_graph_template_rejects_invalid_runtime_contract(tmp_path, content, message):
    with pytest.raises(GraphTemplateError, match=message):
        load_graph_template(_write_yaml(tmp_path, content))


def test_graph_template_rejects_unbounded_cycle(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
id: loop
version: 1
nodes:
  - {id: coder, role: worker, kind: worker, capability: fake.coder, max_runs: 3}
  - {id: tester, role: worker, kind: worker, capability: fake.tester, max_runs: 3}
edges:
  - {from: coder, to: tester, on: succeeded}
  - {from: tester, to: coder, on: failed}
handoffs: {}
""",
    )

    with pytest.raises(GraphTemplateError, match="cycle.*max_traversals"):
        load_graph_template(path)


def test_graph_template_accepts_cycle_with_explicit_traversal_limit(tmp_path):
    template = load_graph_template(
        _write_yaml(
            tmp_path,
            """
id: loop
version: 1
nodes:
  - {id: coder, role: worker, kind: worker, capability: fake.coder, max_runs: 3}
  - {id: tester, role: worker, kind: worker, capability: fake.tester, max_runs: 3}
edges:
  - {id: coder-tester, from: coder, to: tester, on: succeeded}
  - {id: tester-coder, from: tester, to: coder, on: failed, max_traversals: 2}
handoffs: {}
""",
        )
    )

    assert template.edges[1].max_traversals == 2
