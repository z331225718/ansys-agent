from pathlib import Path
import sqlite3

from aedt_agent.knowledge.build_sqlite import build_api_semantics_db
from aedt_agent.knowledge.build_sqlite import main as build_sqlite_main
from aedt_agent.knowledge.models import ApiSemantic, CommonTrap, WorkflowCase
from aedt_agent.knowledge.sqlite_provider import SQLiteKnowledgeProvider


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


def test_api_semantic_from_dict_copies_params_containers():
    data = {
        "fqname": "Hfss.modeler.create_box",
        "category": "geometry",
        "params": [{"name": "origin", "metadata": {"units": ["mm"]}}],
    }

    item = ApiSemantic.from_dict(data)
    data["params"][0]["name"] = "changed"
    data["params"][0]["metadata"]["units"].append("cm")

    assert item.params == [{"name": "origin", "metadata": {"units": ["mm"]}}]


def test_workflow_case_from_dict_copies_parameters_container():
    data = {
        "case_id": "hfss_patch_antenna",
        "task_type": "antenna",
        "natural_language_task": "Create patch antenna",
        "workflow_steps": [],
        "api_used": [],
        "parameters": {"frequency": {"value": "2.4GHz", "sweeps": ["nominal"]}},
        "reference_script": "benchmarks/reference_scripts/hfss_patch_antenna.py",
        "validation_script": "benchmarks/validation_scripts/validate_hfss_patch_antenna.py",
        "expected_state": {},
        "known_traps": [],
    }

    case = WorkflowCase.from_dict(data)
    data["parameters"]["frequency"]["value"] = "5GHz"
    data["parameters"]["frequency"]["sweeps"].append("changed")

    assert case.parameters == {"frequency": {"value": "2.4GHz", "sweeps": ["nominal"]}}


def test_sqlite_provider_search_returns_results(tmp_path):
    db_path = tmp_path / "test.sqlite"
    build_api_semantics_db(
        Path("knowledge/api_semantics/api_semantics.schema.sql"),
        Path("knowledge/api_semantics/api_semantics.seed.jsonl"),
        db_path,
    )
    provider = SQLiteKnowledgeProvider(db_path)

    results = provider.search_api("create_box", limit=5)

    assert len(results) >= 1
    assert results[0].fqname == "Hfss.modeler.create_box"


def test_sqlite_provider_lists_workflow_cases():
    provider = SQLiteKnowledgeProvider(
        db_path=Path("nonexistent.sqlite"),
        workflow_cases_dir=Path("knowledge/workflow_cases"),
        common_traps_dir=Path("knowledge/common_traps"),
    )

    cases = provider.list_workflow_cases()

    assert len(cases) >= 3
    assert any(c.case_id == "hfss_patch_antenna" for c in cases)


def test_sqlite_provider_lists_common_traps_filtered():
    provider = SQLiteKnowledgeProvider(
        db_path=Path("nonexistent.sqlite"),
        workflow_cases_dir=Path("knowledge/workflow_cases"),
        common_traps_dir=Path("knowledge/common_traps"),
    )

    traps = provider.list_common_traps(filter_ids=["airbox_too_small"])

    assert len(traps) >= 1
    assert traps[0].trap_id == "airbox_too_small"


def test_build_sqlite_cli_rebuilds_default_db(monkeypatch, tmp_path):
    db_path = tmp_path / "api_semantics.sqlite"

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_sqlite",
            "--schema",
            "knowledge/api_semantics/api_semantics.schema.sql",
            "--seed",
            "knowledge/api_semantics/api_semantics.seed.jsonl",
            "--db",
            str(db_path),
        ],
    )

    build_sqlite_main()

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("select count(*) from api_semantics").fetchone()[0]
        create_box = conn.execute(
            "select constraints_json from api_semantics where fqname = ?",
            ("Hfss.modeler.create_box",),
        ).fetchone()
        toggle = conn.execute(
            "select fqname from api_semantics where fqname = ?",
            ("Hfss3dLayout.oeditor.ToggleViaPin",),
        ).fetchone()

    assert count >= 75
    assert create_box is not None
    assert "sizes must be positive" in create_box[0]
    assert toggle is not None
