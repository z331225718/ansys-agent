from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.validation.inspector import inspect_aedt_model
from aedt_agent.validation.report import validation_summary
from aedt_agent.validation.rules import validate_model_facts, validation_repair_context


def _snapshot():
    adapter = FakeAedtAdapter("p1", "d1")
    app = adapter.app
    box = app.modeler.create_box([0, 0, 0], [1, 1, 1], name="Box1", material="copper")
    app.assign_material(box.name, "copper")
    app.wave_port(box.faces[0].id, name="Port1")
    app.assign_radiation_boundary_to_objects("Box1", name="Radiation")
    app.create_setup(name="Setup1", Frequency="2.4GHz")
    app.create_linear_count_sweep("Setup1", name="Sweep1")
    return adapter.snapshot_state()


def test_inspector_normalizes_snapshot_to_model_facts():
    facts = inspect_aedt_model(_snapshot())

    assert facts.project_id == "p1"
    assert facts.design_id == "d1"
    assert facts.materials["Box1"] == "copper"
    assert facts.faces["Box1"]
    assert "Port1" in facts.ports
    assert facts.to_dict()["summary"]["object_count"] == 1


def test_validation_rules_pass_for_valid_snapshot():
    facts = inspect_aedt_model(_snapshot())

    result = validate_model_facts(
        facts,
        [
            {"rule": "object_exists", "target": "Box1"},
            {"rule": "material_assigned", "target": "Box1", "expected": "copper"},
            {"rule": "port_exists", "target": "Port1"},
            {"rule": "port_assignment_valid", "target": "Port1"},
            {"rule": "boundary_exists", "target": "Radiation"},
            {"rule": "setup_exists", "target": "Setup1"},
            {"rule": "sweep_exists", "target": "Sweep1"},
            {"rule": "sweep_attached_to_setup", "target": "Sweep1", "setup": "Setup1"},
            {"rule": "airbox_radiation_relation_valid", "target": "Radiation"},
        ],
    )

    assert result.passed is True
    assert validation_summary(result) == "Validation passed (9/9 checks)."


def test_validation_rules_report_failures_and_repair_context():
    facts = inspect_aedt_model(_snapshot())

    result = validate_model_facts(
        facts,
        [
            {"rule": "object_exists", "target": "Missing"},
            {"rule": "material_assigned", "target": "Box1", "expected": "FR4_epoxy"},
        ],
    )

    repair = validation_repair_context(result)
    assert result.passed is False
    assert len(repair["failed_checks"]) == 2
    assert validation_summary(result) == "Validation failed (0/2 checks passed, 2 failed)."


def test_validation_rejects_unsupported_rule():
    facts = inspect_aedt_model(_snapshot())

    result = validate_model_facts(facts, [{"rule": "not_a_rule", "target": "Box1"}])

    assert result.passed is False
    assert result.checks[0].message == "unsupported validation rule: not_a_rule"
