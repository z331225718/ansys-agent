from aedt_agent.knowledge.models import ApiSemantic, CommonTrap, WorkflowCase


def test_api_semantic_from_dict_parses_lists():
    item = ApiSemantic.from_dict(
        {
            "fqname": "Hfss.modeler.create_box",
            "domain": "hfss",
            "category": "geometry",
            "signature": "create_box(origin, sizes, name=None, material=None)",
            "params": [{"name": "origin"}],
            "returns": {"type": "ObjectId"},
            "docstring": "Create a box.",
            "constraints": ["sizes must be positive"],
            "common_errors": ["negative size"],
            "common_traps": ["unit mismatch"],
            "examples_ref": ["hfss_patch_antenna"],
            "source_refs": ["manual"],
            "confidence": "manual",
            "pyaedt_version": "0.0",
            "aedt_version": "2025R2",
            "last_verified_at": "2026-05-08",
        }
    )

    assert item.fqname == "Hfss.modeler.create_box"
    assert item.constraints == ["sizes must be positive"]
    assert item.params[0]["name"] == "origin"


def test_workflow_case_from_dict_has_steps():
    case = WorkflowCase.from_dict(
        {
            "case_id": "hfss_patch_antenna",
            "domain": "hfss",
            "task_type": "antenna",
            "natural_language_task": "Create patch antenna",
            "workflow_steps": ["create_substrate", "create_port"],
            "api_used": ["Hfss.modeler.create_box"],
            "parameters": {"frequency": "2.4GHz"},
            "reference_script": "benchmarks/reference_scripts/hfss_patch_antenna.py",
            "validation_script": "benchmarks/validation_scripts/validate_hfss_patch_antenna.py",
            "expected_state": {"objects": ["substrate"]},
            "known_traps": ["missing_ground_plane"],
            "notes": "Structured case.",
        }
    )

    assert case.workflow_steps == ["create_substrate", "create_port"]
    assert case.parameters["frequency"] == "2.4GHz"


def test_common_trap_from_dict_has_detection():
    trap = CommonTrap.from_dict(
        {
            "trap_id": "airbox_too_small",
            "domain": "hfss",
            "applies_to": ["create_airbox"],
            "symptom": "Radiation result is wrong",
            "root_cause": "Padding too small",
            "why_silent": "Model can solve but boundary is poor",
            "detection": "Check padding against wavelength",
            "prevention": "Use frequency-aware padding",
            "validation_rule": "validate_airbox_padding",
            "source": "manual",
        }
    )

    assert trap.trap_id == "airbox_too_small"
    assert trap.validation_rule == "validate_airbox_padding"
