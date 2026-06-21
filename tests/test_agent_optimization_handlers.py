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
