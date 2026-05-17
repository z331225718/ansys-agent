import json
from pathlib import Path

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionStatus
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.nodes.registry import NodeRegistry
from aedt_agent.workflow.executor import WorkflowExecutor
from aedt_agent.workflow.models import Workflow, WorkflowEdge, WorkflowNode, WorkflowOutput, WorkflowParameter
from aedt_agent.workflow.validator import WorkflowValidator


def _workflow_executor(tmp_path):
    manager = SessionManager(lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    node_executor = NodeExecutor(
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
        session_manager=manager,
        queue=ExecutionQueue(timeout_seconds=1),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
    )
    validator = WorkflowValidator(NodeCatalog.from_directory(Path("nodes/catalog")))
    return manager, WorkflowExecutor(node_executor, validator)


def test_workflow_executor_runs_valid_workflow_with_refs(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="wave_port",
        name="Wave Port",
        nodes=[
            WorkflowNode(
                id="box",
                node_id="create_conductor_or_geometry_group",
                inputs={"geometry": [{"kind": "box", "origin": [0, 0, 0], "size": [1, 1, 1], "name": "metal"}]},
            ),
            WorkflowNode(id="face", node_id="select_face", inputs={"object_name": {"$ref": "box.output.object_name"}}),
            WorkflowNode(id="port", node_id="create_port", inputs={"assignment": {"$ref": "face.output.selected_face_id"}, "port_type": "wave"}),
        ],
        edges=[
            WorkflowEdge(source="box.output.object_name", target="face.inputs.object_name"),
            WorkflowEdge(source="face.output.selected_face_id", target="port.inputs.assignment"),
        ],
        outputs=[WorkflowOutput(name="port", source="port.output.port_name")],
    )

    result = executor.execute(session.ref.session_id, workflow, artifact_path=tmp_path / "workflow_run.json")

    state = manager.snapshot(session.ref.session_id)
    artifact = json.loads((tmp_path / "workflow_run.json").read_text(encoding="utf-8"))
    assert result.succeeded is True
    assert result.outputs["port"] == "Port1"
    assert len(result.steps) == 3
    assert result.steps[-1].snapshot_summary["port_count"] == 1
    assert state["ports"]["Port1"]["type"] == "wave"
    assert artifact["status"] == "succeeded"
    assert (tmp_path / "validation.json").exists()
    assert (tmp_path / "report.html").exists()


def test_workflow_executor_writes_model_validation_artifact(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="validated",
        name="Validated",
        nodes=[
            WorkflowNode(
                id="substrate",
                node_id="create_substrate",
                inputs={"origin": [0, 0, 0], "size": [1, 1, 1], "name": "Substrate", "material": "FR4_epoxy"},
            )
        ],
        validation=[
            {"rule": "object_exists", "target": "Substrate"},
            {"rule": "material_assigned", "target": "Substrate", "expected": "FR4_epoxy"},
        ],
    )

    result = executor.execute(session.ref.session_id, workflow, artifact_path=tmp_path / "workflow_run.json")
    validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))

    assert result.status == ExecutionStatus.SUCCEEDED.value
    assert result.model_validation["passed"] is True
    assert validation["model"]["summary"] == "Validation passed (2/2 checks)."
    assert validation["model_facts"]["summary"]["object_count"] == 1


def test_workflow_executor_fails_when_model_validation_fails(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="invalid_model",
        name="Invalid Model",
        nodes=[
            WorkflowNode(
                id="substrate",
                node_id="create_substrate",
                inputs={"origin": [0, 0, 0], "size": [1, 1, 1], "name": "Substrate", "material": "FR4_epoxy"},
            )
        ],
        validation=[{"rule": "object_exists", "target": "Missing"}],
    )

    result = executor.execute(session.ref.session_id, workflow)

    assert result.status == ExecutionStatus.FAILED.value
    assert result.model_validation["passed"] is False
    assert result.repair_context["reason"] == "model_validation_failed"


def test_workflow_executor_rejects_invalid_workflow_without_running_nodes(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="bad",
        name="Bad",
        nodes=[WorkflowNode(id="setup", node_id="create_setup", inputs={})],
    )

    result = executor.execute(session.ref.session_id, workflow)

    assert result.status == ExecutionStatus.REJECTED.value
    assert result.steps == []
    assert result.repair_context["reason"] == "workflow_validation_failed"
    assert manager.snapshot(session.ref.session_id)["setups"] == {}


def test_workflow_executor_stops_on_failed_node(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="failed_node",
        name="Failed Node",
        nodes=[
            WorkflowNode(id="face", node_id="select_face", inputs={"object_name": "MissingObject"}),
            WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": "1GHz"}),
        ],
    )

    result = executor.execute(session.ref.session_id, workflow)

    assert result.status == ExecutionStatus.FAILED.value
    assert len(result.steps) == 1
    assert result.steps[0].step_id == "face"
    assert result.repair_context["failed_step_id"] == "face"
    assert manager.snapshot(session.ref.session_id)["setups"] == {}


def test_workflow_executor_applies_edges_when_input_is_missing(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="edge_injection",
        name="Edge Injection",
        nodes=[
            WorkflowNode(
                id="box",
                node_id="create_conductor_or_geometry_group",
                inputs={"geometry": [{"kind": "box", "origin": [0, 0, 0], "size": [1, 1, 1], "name": "metal"}]},
            ),
            WorkflowNode(id="face", node_id="select_face", inputs={}),
        ],
        edges=[WorkflowEdge(source="box.output.object_name", target="face.inputs.object_name")],
    )

    result = executor.execute(session.ref.session_id, workflow)

    assert result.succeeded is True
    assert result.steps[1].inputs["object_name"] == "metal"


def test_workflow_executor_resolves_parameter_refs(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="parameter_ref",
        name="Parameter Ref",
        parameters=[WorkflowParameter(name="frequency", type="string", default="2.4GHz")],
        nodes=[WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": {"$ref": "parameters.frequency"}})],
        outputs=[WorkflowOutput(name="setup", source="setup.output.setup_name")],
    )

    result = executor.execute(session.ref.session_id, workflow, parameters={"frequency": "5GHz"})

    state = manager.snapshot(session.ref.session_id)
    assert result.succeeded is True
    assert result.steps[0].inputs["frequency"] == "5GHz"
    assert result.outputs["setup"] == "Setup1"
    assert state["setups"]["Setup1"]["Frequency"] == "5GHz"


def test_workflow_executor_can_resume_from_step_with_initial_outputs(tmp_path):
    manager, executor = _workflow_executor(tmp_path)
    session = manager.create_session("p1", "d1")
    workflow = Workflow(
        workflow_id="resume",
        name="Resume",
        nodes=[
            WorkflowNode(
                id="box",
                node_id="create_conductor_or_geometry_group",
                inputs={"geometry": [{"kind": "box", "origin": [0, 0, 0], "size": [1, 1, 1], "name": "metal"}]},
            ),
            WorkflowNode(id="face", node_id="select_face", inputs={"object_name": {"$ref": "box.output.object_name"}}),
        ],
    )

    result = executor.execute(
        session.ref.session_id,
        workflow,
        start_at_step_id="face",
        initial_step_outputs={"box": {"object_name": "metal"}},
    )

    assert result.status == ExecutionStatus.FAILED.value
    assert len(result.steps) == 1
    assert result.steps[0].step_id == "face"
    assert "MissingObject" not in result.repair_context["error_message"]
