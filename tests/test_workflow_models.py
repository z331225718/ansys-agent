import json

from aedt_agent.workflow.models import (
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowOutput,
    WorkflowParameter,
    workflow_node_ref,
    workflow_parameter_ref,
)


def test_workflow_round_trips_to_json():
    workflow = Workflow(
        workflow_id="microstrip_sparameter_v1",
        name="Microstrip S-Parameter",
        parameters=[
            WorkflowParameter(
                name="substrate_thickness",
                type="number",
                default=1.6,
                unit="mm",
                minimum=0.1,
                maximum=5.0,
                label="Substrate thickness",
            )
        ],
        nodes=[
            WorkflowNode(id="substrate", node_id="create_substrate", inputs={"size": [50, 50, {"$ref": "parameters.substrate_thickness"}]}),
            WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": "2.4GHz"}),
        ],
        edges=[WorkflowEdge(source="parameters.substrate_thickness", target="substrate.inputs.size.2")],
        outputs=[WorkflowOutput(name="setup", source="setup.output.setup_name")],
    )

    decoded = Workflow.from_json(workflow.to_json())

    assert decoded.workflow_id == "microstrip_sparameter_v1"
    assert decoded.parameters[0].unit == "mm"
    assert decoded.node_by_id("setup").node_id == "create_setup"
    assert decoded.edges[0].target == "substrate.inputs.size.2"
    assert json.loads(decoded.to_json())["nodes"][0]["id"] == "substrate"


def test_workflow_accepts_parameter_mapping_shape():
    workflow = Workflow.from_dict(
        {
            "workflow_id": "simple",
            "name": "Simple",
            "parameters": {
                "frequency": {"type": "string", "default": "2.4GHz", "unit": "GHz"},
                "passes": 6,
            },
            "nodes": [],
        }
    )

    assert workflow.parameters[0].name == "frequency"
    assert workflow.parameters[0].default == "2.4GHz"
    assert workflow.parameters[1].name == "passes"
    assert workflow.parameters[1].default == 6


def test_workflow_converts_stage_b_node_plan_refs_to_edges():
    workflow = Workflow.from_stage_b_node_plan(
        workflow_id="wave_port",
        name="Wave Port",
        node_plan=[
            {"id": "box", "node_id": "create_conductor_or_geometry_group", "inputs": {"geometry": []}},
            {"id": "face", "node_id": "select_face", "inputs": {"object_name": {"$ref": "box.output.object_name"}}},
            {"id": "port", "node_id": "create_port", "inputs": {"assignment": {"$ref": "face.output.selected_face_id"}, "port_type": "wave"}},
        ],
    )

    assert [node.id for node in workflow.nodes] == ["box", "face", "port"]
    assert WorkflowEdge(source="box.output.object_name", target="face.inputs.object_name") in workflow.edges
    assert WorkflowEdge(source="face.output.selected_face_id", target="port.inputs.assignment") in workflow.edges


def test_reference_helpers_use_stable_string_format():
    assert workflow_parameter_ref("frequency") == "parameters.frequency"
    assert workflow_node_ref("setup", "setup_name") == "setup.output.setup_name"
