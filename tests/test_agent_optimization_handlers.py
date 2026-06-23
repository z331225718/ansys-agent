from __future__ import annotations

from aedt_agent.agent.graph_executors import GraphNodeExecutionContext, execute_graph_node
from aedt_agent.agent.graph_template import GraphNode, GraphTemplate
from aedt_agent.agent.handoff import HandoffSchema
from aedt_agent.agent.mission import GraphRunRecord, NodeRunRecord, NodeRunStatus


def test_prepare_working_project_copies_reviewed_bundle(tmp_path):
    source = tmp_path / "source" / "case.aedt"
    source.parent.mkdir()
    source.write_text("project", encoding="utf-8")
    source_edb = source.with_suffix(".aedb")
    source_edb.mkdir()
    (source_edb / "edb.def").write_text("edb", encoding="utf-8")
    working = tmp_path / "run" / "working" / "case.aedt"
    node = GraphNode(
        "prepare",
        "prepare",
        "program",
        handler="brd.optimization.prepare_working_project",
        output_schema="real_solve_request",
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "real_solve_request": HandoffSchema(
                "real_solve_request",
                [
                    "project_path",
                    "setup_name",
                    "sweep_name",
                    "tdr_expression",
                    "expected_port_count",
                    "loop_context",
                ],
            )
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "source_project_path": str(source),
            "working_project_path": str(working),
            "run_root": str(tmp_path / "run"),
            "report_dir": str(tmp_path / "run" / "progress"),
            "reset_working_project": True,
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.output_payload["project_path"] == str(working)
    assert working.read_text(encoding="utf-8") == "project"
    assert (working.with_suffix(".aedb") / "edb.def").read_text(encoding="utf-8") == "edb"
    assert result.output_payload["touchstone_name"] == "channel.s4p"
    assert result.output_payload["sparameter_mode"] == "differential"
    assert result.output_payload["solution_name"] == "Setup1 : Sweep1"
    assert result.output_payload["loop_context"]["solve"]["setup_name"] == "Setup1"
    assert result.output_payload["loop_context"]["solve"]["sweep_name"] == "Sweep1"


def test_prepare_working_project_removes_reused_working_project_lock(tmp_path):
    source = tmp_path / "source" / "case.aedt"
    source.parent.mkdir()
    source.write_text("source project", encoding="utf-8")
    source_lock = source.with_name(f"{source.name}.lock")
    source_lock.write_text("source lock", encoding="utf-8")
    working = tmp_path / "run" / "working" / "case.aedt"
    working.parent.mkdir(parents=True)
    working.write_text("existing working project", encoding="utf-8")
    working_lock = working.with_name(f"{working.name}.lock")
    working_lock.write_text("stale lock", encoding="utf-8")
    node = GraphNode(
        "prepare",
        "prepare",
        "program",
        handler="brd.optimization.prepare_working_project",
        output_schema="real_solve_request",
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "real_solve_request": HandoffSchema(
                "real_solve_request",
                [
                    "project_path",
                    "setup_name",
                    "sweep_name",
                    "tdr_expression",
                    "expected_port_count",
                    "loop_context",
                ],
            )
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "source_project_path": str(source),
            "working_project_path": str(working),
            "run_root": str(tmp_path / "run"),
            "report_dir": str(tmp_path / "run" / "progress"),
            "reset_working_project": False,
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.output_payload["project_path"] == str(working)
    assert working.read_text(encoding="utf-8") == "existing working project"
    assert not working_lock.exists()
    assert source_lock.exists()
    assert result.output_payload["stale_aedt_lock_files_removed"] == [
        str(working_lock.resolve())
    ]


def test_candidate_inventory_builder_expands_shape_backed_layers(tmp_path):
    node = GraphNode(
        "candidate_inventory",
        "prepare",
        "program",
        handler="brd.optimization.build_candidate_actions",
        input_schema="real_solve_request",
        output_schema="real_solve_request",
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "real_solve_request": HandoffSchema(
                "real_solve_request",
                [
                    "project_path",
                    "setup_name",
                    "sweep_name",
                    "tdr_expression",
                    "expected_port_count",
                    "loop_context",
                ],
            )
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "project_path": str(tmp_path / "working" / "case.aedt"),
            "setup_name": "Setup1",
            "sweep_name": "Sweep1",
            "tdr_expression": "TDRZ(Diff1)",
            "expected_port_count": 4,
            "loop_context": {
                "round_index": 1,
                "working_project_path": str(tmp_path / "working" / "case.aedt"),
                "latest_project_path": str(tmp_path / "working" / "case.aedt"),
                "report_dir": str(tmp_path / "progress"),
                "geometry_constraints": {
                    "anti_pad": {"max_radius_mil": 22},
                    "non_functional_pad": {
                        "min_radius_mil": 7.875,
                        "max_radius_mil": 10,
                    },
                },
                "candidate_action_inventory": {
                    "source": "unit_test_inventory",
                    "tdr_observation_port": "Diff1",
                    "tdr_port_orientation_evidence": "reviewed port map",
                    "anti_pad_shape_layers": [
                        {
                            "layer": "L5",
                            "plane_shape_ids": [105],
                            "center_padstack_instance_ids": [501, 502],
                            "bridge_center_padstack_instance_ids": [501, 502],
                            "parasitic_target": "reviewed buried-via pad",
                        }
                    ],
                    "non_functional_pad_layers": [
                        {
                            "layer": "L7",
                            "center_padstack_instance_ids": [701, 702],
                            "signal_nets": ["TX_P", "TX_N"],
                            "parasitic_target": "reviewed via barrel",
                        }
                    ],
                },
            },
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    actions = result.output_payload["loop_context"]["candidate_actions"]
    assert [action["layers"] for action in actions] == [["L5"], ["L7"]]
    assert actions[0]["action_type"] == "anti_pad.enlarge"
    assert actions[0]["plane_shape_ids"] == [105]
    assert actions[0]["parameter_name"] == "l5_void_r"
    assert actions[1]["action_type"] == "non_functional_pad.add_or_enlarge"
    assert actions[1]["parameter_name"] == "l7_nfp_r"
    summary = result.output_payload["candidate_action_inventory_summary"]
    assert summary["generated_action_count"] == 2
    assert summary["candidate_action_count"] == 2


def test_candidate_inventory_builder_loads_reviewed_inventory_file(tmp_path):
    inventory_path = tmp_path / "candidate_action_inventory.json"
    inventory_path.write_text(
        """
{
  "source": "unit_test_inventory_file",
  "tdr_observation_port": "Diff1",
  "tdr_port_orientation_evidence": "reviewed port map",
  "anti_pad_shape_layers": [
    {
      "layer": "L6",
      "plane_shape_ids": [106],
      "center_padstack_instance_ids": [601, 602],
      "bridge_center_padstack_instance_ids": [601, 602],
      "parasitic_target": "reviewed shape-backed layer"
    }
  ],
  "non_functional_pad_layers": []
}
""".strip(),
        encoding="utf-8",
    )
    node = GraphNode(
        "candidate_inventory",
        "prepare",
        "program",
        handler="brd.optimization.build_candidate_actions",
        input_schema="real_solve_request",
        output_schema="real_solve_request",
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "real_solve_request": HandoffSchema(
                "real_solve_request",
                [
                    "project_path",
                    "setup_name",
                    "sweep_name",
                    "tdr_expression",
                    "expected_port_count",
                    "loop_context",
                ],
            )
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "project_path": str(tmp_path / "working" / "case.aedt"),
            "setup_name": "Setup1",
            "sweep_name": "Sweep1",
            "tdr_expression": "TDRZ(Diff1)",
            "expected_port_count": 4,
            "loop_context": {
                "round_index": 1,
                "working_project_path": str(tmp_path / "working" / "case.aedt"),
                "latest_project_path": str(tmp_path / "working" / "case.aedt"),
                "report_dir": str(tmp_path / "progress"),
                "candidate_action_inventory_path": str(inventory_path),
                "geometry_constraints": {
                    "anti_pad": {"max_radius_mil": 22},
                    "non_functional_pad": {
                        "min_radius_mil": 7.875,
                        "max_radius_mil": 10,
                    },
                },
            },
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    actions = result.output_payload["loop_context"]["candidate_actions"]
    assert result.status == NodeRunStatus.SUCCEEDED
    assert len(actions) == 1
    assert actions[0]["layers"] == ["L6"]
    assert actions[0]["plane_shape_ids"] == [106]
    assert result.output_payload["candidate_action_inventory_summary"][
        "inventory_source"
    ] == "unit_test_inventory_file"


def test_agent_decider_falls_back_to_deterministic_handler_without_llm(
    tmp_path,
    monkeypatch,
):
    from aedt_agent.agent import llm as llm_module

    monkeypatch.setattr(
        llm_module.LlmConfig,
        "from_env",
        classmethod(
            lambda cls, prefix="AEDT_AGENT_", *, profile="": cls(
                model="offline",
                api_key="",
            )
        ),
    )
    action = {
        "action_type": "anti_pad.enlarge",
        "expected_effect": "increase_impedance",
        "target_radius": {"value": 22, "unit": "mil"},
        "constraints_checked": ["anti_pad_radius <= 22mil"],
        "tdr_observation_port": "Diff1",
        "tdr_port_orientation_evidence": "reviewed port map",
        "risk": "may over-correct",
        "rollback": "restore l02_void_r",
    }
    node = GraphNode(
        "decide",
        "decision_maker",
        "agent",
        handler="brd.optimization.decide_next_action",
        system_prompt="optimization_decider_prompt",
        input_schema="score_result",
        output_schema="next_action",
        profile="high_reasoning",
        constraints={"deterministic_fallback": True},
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "score_result": HandoffSchema(
                "score_result",
                ["status", "score", "evidence_summary", "loop_context"],
            ),
            "next_action": HandoffSchema(
                "next_action",
                [
                    "decision",
                    "reason",
                    "tdr_observation_port",
                    "tdr_port_orientation_evidence",
                    "constraints_checked",
                    "risk",
                    "rollback",
                    "loop_context",
                ],
            ),
        },
        prompts={
            "optimization_decider_prompt": "Return the next bounded action as JSON."
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "status": "failed",
            "score": {
                "status": "fail",
                "tdr_min_impedance_ohm": 82,
                "tdr_target_ohm": 90,
                "tdr_observation_port": "Diff1",
            },
            "evidence_summary": {
                "status": "fail",
                "tdr_observation_port": "Diff1",
            },
            "loop_context": {
                "round_index": 1,
                "max_rounds": 3,
                "report_dir": str(tmp_path / "progress"),
                "candidate_actions": [action],
            },
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "continue"
    assert result.output_payload["decision"] == "continue"
    assert result.output_payload["selected_action"] == action
    assert result.output_payload["tdr_observation_port"] == "Diff1"
    assert result.output_payload["agent_fallback"]["status"] == "used"


def test_decider_preserves_llm_approval_required_decision(tmp_path, monkeypatch):
    from aedt_agent.agent import llm as llm_module

    monkeypatch.setattr(
        llm_module.LlmConfig,
        "from_env",
        classmethod(
            lambda cls, prefix="AEDT_AGENT_", *, profile="": cls(
                model="test",
                api_key="test-key",
            )
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "llm_complete_json",
        lambda *args, **kwargs: {
            "decision": "approval_required",
            "reason": "Diff1 orientation evidence is insufficient",
        },
    )
    node = GraphNode(
        "decide",
        "decision_maker",
        "program",
        handler="brd.optimization.decide_next_action",
        input_schema="score_result",
        output_schema="next_action",
        profile="high_reasoning",
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "score_result": HandoffSchema(
                "score_result",
                ["status", "score", "evidence_summary", "loop_context"],
            ),
            "next_action": HandoffSchema(
                "next_action",
                [
                    "decision",
                    "reason",
                    "tdr_observation_port",
                    "tdr_port_orientation_evidence",
                    "constraints_checked",
                    "risk",
                    "rollback",
                    "loop_context",
                ],
            ),
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "status": "failed",
            "score": {
                "status": "fail",
                "tdr_target_ohm": 90,
                "tdr_min_impedance_ohm": 80,
                "tdr_observation_port": "Diff1",
            },
            "evidence_summary": {
                "status": "fail",
                "tdr_observation_port": "Diff1",
            },
            "loop_context": {
                "round_index": 1,
                "max_rounds": 3,
                "report_dir": str(tmp_path / "progress"),
                "candidate_actions": [
                    {
                        "action_type": "anti_pad.enlarge",
                        "expected_effect": "increase_impedance",
                    }
                ],
            },
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "approval_required"
    assert result.output_payload["decision"] == "approval_required"
    assert result.output_payload["reason"] == "Diff1 orientation evidence is insufficient"
    assert result.output_payload["tdr_observation_port"] == "Diff1"


def test_decider_accepts_llm_selected_action_from_inventory(tmp_path, monkeypatch):
    from aedt_agent.agent import llm as llm_module

    selected_action = {
        "action_type": "anti_pad.enlarge",
        "layers": ["L5"],
        "plane_shape_ids": [105],
        "center_padstack_instance_ids": [501, 502],
        "bridge_center_padstack_instance_ids": [501, 502],
        "target_radius": {"value": 21, "unit": "mil"},
        "parameter_name": "l5_void_r",
        "bridge_between_vias": True,
        "constraints_checked": ["anti_pad_radius <= 22mil"],
        "expected_effect": "increase_impedance",
        "tdr_observation_port": "Diff1",
        "tdr_port_orientation_evidence": "reviewed port map",
        "risk": "watch for over-correction",
        "rollback": "restore l5_void_r",
    }
    monkeypatch.setattr(
        llm_module.LlmConfig,
        "from_env",
        classmethod(
            lambda cls, prefix="AEDT_AGENT_", *, profile="": cls(
                model="test",
                api_key="test-key",
            )
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "llm_complete_json",
        lambda *args, **kwargs: {
            "decision": "continue",
            "selected_action": selected_action,
            "reason": "TDR low maps to reviewed L5 shape-backed region",
        },
    )
    fallback_action = {
        "action_type": "anti_pad.enlarge",
        "layers": ["L2_GND"],
        "plane_shape_ids": [102],
        "center_padstack_instance_ids": [201, 202],
        "target_radius": {"value": 22, "unit": "mil"},
        "parameter_name": "l02_void_r",
        "expected_effect": "increase_impedance",
    }
    node = GraphNode(
        "decide",
        "decision_maker",
        "agent",
        handler="brd.optimization.decide_next_action",
        input_schema="score_result",
        output_schema="next_action",
        profile="high_reasoning",
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "score_result": HandoffSchema(
                "score_result",
                ["status", "score", "evidence_summary", "loop_context"],
            ),
            "next_action": HandoffSchema(
                "next_action",
                [
                    "decision",
                    "reason",
                    "tdr_observation_port",
                    "tdr_port_orientation_evidence",
                    "constraints_checked",
                    "risk",
                    "rollback",
                    "loop_context",
                ],
            ),
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "status": "failed",
            "score": {
                "status": "fail",
                "tdr_target_ohm": 90,
                "tdr_min_impedance_ohm": 82,
                "tdr_observation_port": "Diff1",
            },
            "evidence_summary": {
                "status": "fail",
                "tdr_observation_port": "Diff1",
            },
            "loop_context": {
                "round_index": 1,
                "max_rounds": 3,
                "report_dir": str(tmp_path / "progress"),
                "candidate_actions": [fallback_action],
                "candidate_action_inventory": {
                    "anti_pad_shape_layers": [
                        {
                            "layer": "L5",
                            "plane_shape_ids": [105],
                            "center_padstack_instance_ids": [501, 502],
                            "bridge_center_padstack_instance_ids": [501, 502],
                        }
                    ]
                },
            },
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "continue"
    assert result.output_payload["selected_action"] == selected_action
    assert result.output_payload["loop_context"]["last_decision_source"] == "llm_proposed"


def test_decider_falls_back_when_llm_selected_action_invents_layer(
    tmp_path,
    monkeypatch,
):
    from aedt_agent.agent import llm as llm_module

    monkeypatch.setattr(
        llm_module.LlmConfig,
        "from_env",
        classmethod(
            lambda cls, prefix="AEDT_AGENT_", *, profile="": cls(
                model="test",
                api_key="test-key",
            )
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "llm_complete_json",
        lambda *args, **kwargs: {
            "decision": "continue",
            "selected_action": {
                "action_type": "anti_pad.enlarge",
                "layers": ["L9"],
                "plane_shape_ids": [999],
                "center_padstack_instance_ids": [901, 902],
                "target_radius": {"value": 22, "unit": "mil"},
                "parameter_name": "l9_void_r",
                "expected_effect": "increase_impedance",
            },
            "reason": "invented layer should not pass",
        },
    )
    fallback_action = {
        "action_type": "anti_pad.enlarge",
        "layers": ["L5"],
        "plane_shape_ids": [105],
        "center_padstack_instance_ids": [501, 502],
        "target_radius": {"value": 22, "unit": "mil"},
        "parameter_name": "l5_void_r",
        "expected_effect": "increase_impedance",
        "tdr_observation_port": "Diff1",
        "tdr_port_orientation_evidence": "reviewed port map",
        "risk": "fallback",
        "rollback": "restore l5_void_r",
    }
    node = GraphNode(
        "decide",
        "decision_maker",
        "agent",
        handler="brd.optimization.decide_next_action",
        input_schema="score_result",
        output_schema="next_action",
        profile="high_reasoning",
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "score_result": HandoffSchema(
                "score_result",
                ["status", "score", "evidence_summary", "loop_context"],
            ),
            "next_action": HandoffSchema(
                "next_action",
                [
                    "decision",
                    "reason",
                    "tdr_observation_port",
                    "tdr_port_orientation_evidence",
                    "constraints_checked",
                    "risk",
                    "rollback",
                    "loop_context",
                ],
            ),
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "status": "failed",
            "score": {
                "status": "fail",
                "tdr_target_ohm": 90,
                "tdr_min_impedance_ohm": 82,
                "tdr_observation_port": "Diff1",
            },
            "evidence_summary": {
                "status": "fail",
                "tdr_observation_port": "Diff1",
            },
            "loop_context": {
                "round_index": 1,
                "max_rounds": 3,
                "report_dir": str(tmp_path / "progress"),
                "candidate_actions": [fallback_action],
                "candidate_action_inventory": {
                    "anti_pad_shape_layers": [
                        {
                            "layer": "L5",
                            "plane_shape_ids": [105],
                            "center_padstack_instance_ids": [501, 502],
                        }
                    ]
                },
            },
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "continue"
    assert result.output_payload["selected_action"] == fallback_action
    assert result.output_payload["loop_context"]["last_decision_source"] == "deterministic"


def test_decider_rejects_approval_required_when_graph_disallows_it(tmp_path, monkeypatch):
    from aedt_agent.agent import llm as llm_module

    monkeypatch.setattr(
        llm_module.LlmConfig,
        "from_env",
        classmethod(
            lambda cls, prefix="AEDT_AGENT_", *, profile="": cls(
                model="test",
                api_key="test-key",
            )
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "llm_complete_json",
        lambda *args, **kwargs: {
            "decision": "approval_required",
            "reason": "Diff1 orientation evidence is insufficient",
        },
    )
    node = GraphNode(
        "decide",
        "decision_maker",
        "program",
        handler="brd.optimization.decide_next_action",
        input_schema="score_result",
        output_schema="next_action",
        profile="high_reasoning",
        constraints={"allowed_decisions": ["continue", "complete", "failed"]},
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "score_result": HandoffSchema(
                "score_result",
                ["status", "score", "evidence_summary", "loop_context"],
            ),
            "next_action": HandoffSchema(
                "next_action",
                [
                    "decision",
                    "reason",
                    "tdr_observation_port",
                    "tdr_port_orientation_evidence",
                    "constraints_checked",
                    "risk",
                    "rollback",
                    "loop_context",
                ],
            ),
        },
    )
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "status": "failed",
            "score": {
                "status": "fail",
                "tdr_target_ohm": 90,
                "tdr_min_impedance_ohm": 80,
                "tdr_observation_port": "Diff1",
            },
            "evidence_summary": {
                "status": "fail",
                "tdr_observation_port": "Diff1",
            },
            "loop_context": {
                "round_index": 1,
                "max_rounds": 3,
                "report_dir": str(tmp_path / "progress"),
                "candidate_actions": [
                    {
                        "action_type": "anti_pad.enlarge",
                        "expected_effect": "increase_impedance",
                    }
                ],
            },
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "failed"
    assert result.output_payload["decision"] == "failed"
    assert "not allowed" in result.output_payload["reason"]


def test_optimization_failure_handler_fails_without_action_gate():
    node = GraphNode(
        "optimization_failure",
        "validator",
        "program",
        handler="brd.optimization.fail_optimization",
        input_schema="model_edit_request",
    )
    template = GraphTemplate("test", 1, "", [node], [], {})
    graph_run = GraphRunRecord.create("graph-1", "mission-1", "test", 1, 1)
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        {},
    )
    context = GraphNodeExecutionContext(
        runtime=None,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload={
            "approval_required": {
                "reason": "geometry validation found a non-executable action",
            }
        },
        run_index=1,
        worker_id="test",
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.FAILED
    assert result.outcome == "failed"
    assert result.error["code"] == "optimization_loop_failed"
    assert "non-executable" in result.output_payload["reason"]
