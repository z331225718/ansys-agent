from pathlib import Path

from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.models import Workflow, WorkflowEdge, WorkflowNode, WorkflowParameter
from aedt_agent.workflow.validator import WorkflowValidator


def _validator() -> WorkflowValidator:
    return WorkflowValidator(NodeCatalog.from_directory(Path("nodes/catalog")))


def test_validator_accepts_valid_wave_port_workflow():
    workflow = Workflow(
        workflow_id="wave_port",
        name="Wave Port",
        nodes=[
            WorkflowNode(
                id="box",
                node_id="create_conductor_or_geometry_group",
                inputs={"geometry": [{"kind": "box", "origin": [0, 0, 0], "size": [1, 1, 1]}]},
            ),
            WorkflowNode(id="face", node_id="select_face", inputs={"object_name": {"$ref": "box.output.object_name"}}),
            WorkflowNode(id="port", node_id="create_port", inputs={"assignment": {"$ref": "face.output.selected_face_id"}, "port_type": "wave"}),
        ],
        edges=[
            WorkflowEdge(source="box.output.object_name", target="face.inputs.object_name"),
            WorkflowEdge(source="face.output.selected_face_id", target="port.inputs.assignment"),
        ],
    )

    result = _validator().validate(workflow)

    assert result.passed is True
    assert result.errors == []


def test_validator_rejects_unknown_node_and_bad_input():
    workflow = Workflow(
        workflow_id="bad",
        name="Bad",
        nodes=[WorkflowNode(id="bad_node", node_id="missing_node", inputs={"unknown": True})],
    )

    result = _validator().validate(workflow)

    assert result.passed is False
    assert result.errors[0].code == "unknown_node"


def test_validator_rejects_missing_required_input():
    workflow = Workflow(
        workflow_id="missing_input",
        name="Missing Input",
        nodes=[WorkflowNode(id="setup", node_id="create_setup", inputs={})],
    )

    result = _validator().validate(workflow)

    assert result.passed is False
    assert any(issue.code == "missing_input" and issue.field == "frequency" for issue in result.errors)


def test_validator_rejects_unknown_edge_references():
    workflow = Workflow(
        workflow_id="bad_edge",
        name="Bad Edge",
        nodes=[WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": {"$ref": "parameters.frequency"}})],
        edges=[WorkflowEdge(source="parameters.frequency", target="setup.inputs.frequency")],
    )

    result = _validator().validate(workflow)

    assert result.passed is False
    assert any(issue.code == "unknown_parameter_ref" for issue in result.errors)


def test_validator_rejects_dependency_order_errors():
    workflow = Workflow(
        workflow_id="bad_order",
        name="Bad Order",
        nodes=[
            WorkflowNode(id="port", node_id="create_port", inputs={"assignment": {"$ref": "face.output.selected_face_id"}, "port_type": "wave"}),
            WorkflowNode(id="face", node_id="select_face", inputs={"object_name": "Box1"}),
        ],
        edges=[WorkflowEdge(source="face.output.selected_face_id", target="port.inputs.assignment")],
    )

    result = _validator().validate(workflow)

    assert result.passed is False
    assert any(issue.code == "dependency_order" for issue in result.errors)


def test_validator_warns_on_parameter_default_out_of_range():
    workflow = Workflow(
        workflow_id="param_warning",
        name="Param Warning",
        parameters=[WorkflowParameter(name="passes", type="integer", default=20, minimum=1, maximum=10)],
        nodes=[WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": "2GHz"})],
    )

    result = _validator().validate(workflow)

    assert result.passed is True
    assert any(issue.code == "parameter_default_above_max" for issue in result.warnings)


def test_validator_accepts_interpolating_sweep_type_alias():
    workflow = Workflow(
        workflow_id="sweep",
        name="Sweep",
        nodes=[
            WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": "2.4GHz"}),
            WorkflowNode(id="sweep", node_id="create_sweep_or_export", inputs={"setup": "Setup1", "type": "Interpolating"}),
        ],
    )

    result = _validator().validate(workflow)

    assert result.passed is True


def test_validator_rejects_unsupported_sweep_type():
    workflow = Workflow(
        workflow_id="sweep",
        name="Sweep",
        nodes=[
            WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": "2.4GHz"}),
            WorkflowNode(id="sweep", node_id="create_sweep_or_export", inputs={"setup": "Setup1", "sweep_type": "adaptive"}),
        ],
    )

    result = _validator().validate(workflow)

    assert result.passed is False
    assert any(issue.code == "unsupported_sweep_type" for issue in result.errors)
