from pathlib import Path

from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.templates import WorkflowTemplate, WorkflowTemplateCatalog, load_workflow_templates
from aedt_agent.workflow.validator import WorkflowValidator


def _validator(*, include_experimental: bool = False) -> WorkflowValidator:
    return WorkflowValidator(NodeCatalog.from_directory(Path("nodes/catalog"), include_experimental=include_experimental))


def test_template_catalog_loads_starter_templates():
    catalog = load_workflow_templates(Path("workflow_templates"))

    assert set(catalog.templates) == {
        "dipole_antenna_s11_farfield",
        "import_brd_cutout_sparam_tdr",
        "microstrip_sparameter",
        "radiation_airbox_setup",
        "wave_port_setup",
    }
    assert catalog.get("wave_port_setup").workflow.workflow_id == "wave_port_setup_v1"


def test_templates_export_ui_safe_summary():
    catalog = WorkflowTemplateCatalog.from_directory(Path("workflow_templates"))

    payload = catalog.to_ui_dict()

    assert payload["version"] == "0.1.0"
    assert len(payload["templates"]) == 5
    assert all("workflow" not in item for item in payload["templates"])
    assert all(item["node_count"] > 0 for item in payload["templates"])
    assert all(item["parameters"] is not None for item in payload["templates"])


def test_all_templates_pass_workflow_validator():
    validator = _validator(include_experimental=True)
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


def test_dipole_template_reuses_common_nodes_and_keeps_real_smoke_stable():
    template = WorkflowTemplate.from_file(Path("workflow_templates/dipole_antenna_s11_farfield.json"))
    node_ids = [node.node_id for node in template.workflow.nodes]

    assert node_ids == [
        "create_conductor_or_geometry_group",
        "create_airbox",
        "assign_boundary",
        "create_port",
        "create_setup",
        "create_sweep_or_export",
        "create_farfield_setup",
        "solve_setup",
        "create_sparameter_report",
    ]
    assert "create_substrate" not in node_ids
    assert "create_dipole_antenna" not in node_ids
    assert "create_antenna_report" not in node_ids
    assert _validator(include_experimental=True).validate(template.workflow).passed is True


def test_dipole_template_derives_arm_length_from_frequency():
    template = WorkflowTemplate.from_file(Path("workflow_templates/dipole_antenna_s11_farfield.json"))

    workflow = template.instantiate({"frequency": "2.5GHz"})
    defaults = {parameter.name: parameter.default for parameter in workflow.parameters}
    geometry = workflow.node_by_id("dipole_geometry").inputs["geometry"]
    left_arm = geometry[0]
    right_arm = geometry[1]

    assert defaults["frequency"] == "2.5GHz"
    assert defaults["dipole_arm_length_mm"] == 28.48
    assert defaults["airbox_padding_mm"] == 29.979
    assert defaults["left_arm_origin"] == [-28.98, 0, 0]
    assert defaults["right_arm_origin"] == [0.5, 0, 0]
    assert workflow.node_by_id("airbox").inputs["padding"] == {"$ref": "parameters.airbox_padding_mm"}
    assert left_arm["height"] == {"$ref": "parameters.dipole_arm_length_mm"}
    assert right_arm["height"] == {"$ref": "parameters.dipole_arm_length_mm"}
    assert _validator().validate(workflow).passed is True


def test_dipole_template_allows_llm_to_override_airbox_padding_rule():
    template = WorkflowTemplate.from_file(Path("workflow_templates/dipole_antenna_s11_farfield.json"))

    workflow = template.instantiate({"frequency": "2.5GHz", "airbox_padding_mm": 42.0})
    defaults = {parameter.name: parameter.default for parameter in workflow.parameters}

    assert defaults["airbox_padding_mm"] == 42.0
    assert workflow.node_by_id("airbox").inputs["padding"] == {"$ref": "parameters.airbox_padding_mm"}
    assert _validator().validate(workflow).passed is True


def test_dipole_template_allows_llm_to_override_arm_length_for_tuning():
    template = WorkflowTemplate.from_file(Path("workflow_templates/dipole_antenna_s11_farfield.json"))

    workflow = template.instantiate({"frequency": "2.5GHz", "dipole_arm_length_mm": 31.0})
    defaults = {parameter.name: parameter.default for parameter in workflow.parameters}

    assert defaults["dipole_arm_length_mm"] == 31.0
    assert defaults["left_arm_origin"] == [-31.5, 0, 0]
    assert defaults["right_arm_origin"] == [0.5, 0, 0]
    assert _validator().validate(workflow).passed is True


def test_import_cutout_template_uses_layout_specific_nodes():
    template = WorkflowTemplate.from_file(Path("workflow_templates/import_brd_cutout_sparam_tdr.json"))
    node_ids = [node.node_id for node in template.workflow.nodes]

    assert node_ids == [
        "import_layout_file",
        "select_layout_nets",
        "create_layout_cutout",
        "configure_layout_stackup",
        "locate_layout_port_candidates",
        "create_layout_ports",
        "create_layout_setup",
    ]
    first_step = template.workflow.nodes[0]
    cutout_step = template.workflow.nodes[2]
    stackup_step = template.workflow.nodes[3]
    port_candidate_step = template.workflow.nodes[4]
    defaults = {parameter.name: parameter.default for parameter in template.workflow.parameters}
    assert defaults["layout_file"] == ""
    assert defaults["stackup_xml"] == ""
    assert defaults["signal_nets"] == "SRDS_3_RX1_*"
    assert defaults["sweep_start"] == "0GHz"
    assert defaults["sweep_stop"] == "67GHz"
    assert defaults["sweep_points"] == 501
    assert defaults["use_q3d_for_dc"] is True
    assert defaults["solderball_diameter"] == "20mil"
    assert defaults["solderball_height"] == "10mil"
    assert first_step.inputs["import_backend"] == "pyedb"
    assert first_step.inputs["edb_backend"] == "auto"
    assert cutout_step.inputs["threads"] == {"$ref": "parameters.cutout_threads"}
    assert stackup_step.inputs["stackup_rule"] == "load_stackup_xml"
    assert stackup_step.inputs["stackup_xml"] == {"$ref": "parameters.stackup_xml"}
    assert port_candidate_step.node_id == "locate_layout_port_candidates"
    ports_step = template.workflow.nodes[5]
    setup_step = template.workflow.nodes[6]
    assert ports_step.inputs["solderball_diameter"] == {"$ref": "parameters.solderball_diameter"}
    assert setup_step.inputs["sweep_start"] == {"$ref": "parameters.sweep_start"}
    assert setup_step.inputs["sweep_stop"] == {"$ref": "parameters.sweep_stop"}
    assert setup_step.inputs["sweep_points"] == {"$ref": "parameters.sweep_points"}
    assert setup_step.inputs["use_q3d_for_dc"] == {"$ref": "parameters.use_q3d_for_dc"}
    assert _validator(include_experimental=True).validate(template.workflow).passed is True


def test_import_cutout_template_declares_model_build_only_limit():
    template = WorkflowTemplate.from_file(Path("workflow_templates/import_brd_cutout_sparam_tdr.json"))

    assert "model-build" in template.tags
    assert any(
        "stops before analyze" in limit.lower() or "without running solve" in limit.lower()
        for limit in template.known_limits
    )
    assert template.workflow.metadata["experimental"] is True


def test_template_full_dict_includes_workflow_for_chat_planner():
    template = WorkflowTemplate.from_file(Path("workflow_templates/wave_port_setup.json"))

    payload = template.to_dict()

    assert payload["template_id"] == "wave_port_setup"
    assert payload["workflow"]["workflow_id"] == "wave_port_setup_v1"
    assert "port_created" in payload["validation_checks"]
