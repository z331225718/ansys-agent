from pathlib import Path

from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.templates import WorkflowTemplate, WorkflowTemplateCatalog, load_workflow_templates
from aedt_agent.workflow.validator import WorkflowValidator


def _validator() -> WorkflowValidator:
    return WorkflowValidator(NodeCatalog.from_directory(Path("nodes/catalog")))


def test_template_catalog_loads_three_starter_templates():
    catalog = load_workflow_templates(Path("workflow_templates"))

    assert set(catalog.templates) == {
        "microstrip_sparameter",
        "radiation_airbox_setup",
        "wave_port_setup",
    }
    assert catalog.get("wave_port_setup").workflow.workflow_id == "wave_port_setup_v1"


def test_templates_export_ui_safe_summary():
    catalog = WorkflowTemplateCatalog.from_directory(Path("workflow_templates"))

    payload = catalog.to_ui_dict()

    assert payload["version"] == "0.1.0"
    assert len(payload["templates"]) == 3
    assert all("workflow" not in item for item in payload["templates"])
    assert all(item["node_count"] > 0 for item in payload["templates"])
    assert all(item["parameters"] is not None for item in payload["templates"])


def test_all_templates_pass_workflow_validator():
    validator = _validator()
    catalog = WorkflowTemplateCatalog.from_directory(Path("workflow_templates"))

    results = {template.template_id: validator.validate(template.workflow) for template in catalog.list_templates()}

    assert all(result.passed for result in results.values()), {
        template_id: [issue.to_dict() for issue in result.errors]
        for template_id, result in results.items()
        if not result.passed
    }


def test_template_instantiation_overrides_parameter_defaults():
    template = WorkflowTemplate.from_file(Path("workflow_templates/microstrip_sparameter.json"))

    workflow = template.instantiate({"frequency": "5GHz", "sweep_stop": "20GHz"})

    defaults = {parameter.name: parameter.default for parameter in workflow.parameters}
    assert defaults["frequency"] == "5GHz"
    assert defaults["sweep_stop"] == "20GHz"
    assert workflow.metadata["template_id"] == "microstrip_sparameter"
    assert _validator().validate(workflow).passed is True


def test_microstrip_template_uses_pec_and_trace_width_lumped_port_sheets():
    template = WorkflowTemplate.from_file(Path("workflow_templates/microstrip_sparameter.json"))
    nodes = {node.id: node for node in template.workflow.nodes}

    geometry = nodes["trace"].inputs["geometry"]
    port_sheet_1 = next(item for item in geometry if item["name"] == "PortSheet1")
    port_sheet_2 = next(item for item in geometry if item["name"] == "PortSheet2")

    assert port_sheet_1["origin"] == [-20, -1, 0]
    assert port_sheet_1["size"] == [2, 1.6]
    assert port_sheet_2["origin"] == [20, -1, 0]
    assert port_sheet_2["size"] == [2, 1.6]
    assert nodes["lumped_port_1"].node_id == "create_port"
    assert nodes["lumped_port_1"].inputs["port_type"] == "lumped"
    assert nodes["lumped_port_2"].node_id == "create_port"
    assert nodes["lumped_port_2"].inputs["port_type"] == "lumped"
    assert nodes["ground_pec"].inputs == {
        "assignment": "Ground",
        "boundary_type": "Perfect_E",
        "name": "GroundPerfectE",
    }
    assert nodes["trace_pec"].inputs == {
        "assignment": "Trace",
        "boundary_type": "Perfect_E",
        "name": "TracePerfectE",
    }


def test_template_full_dict_includes_workflow_for_chat_planner():
    template = WorkflowTemplate.from_file(Path("workflow_templates/wave_port_setup.json"))

    payload = template.to_dict()

    assert payload["template_id"] == "wave_port_setup"
    assert payload["workflow"]["workflow_id"] == "wave_port_setup_v1"
    assert "port_created" in payload["validation_checks"]
