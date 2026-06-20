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
