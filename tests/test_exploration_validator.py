from __future__ import annotations

import pytest

from aedt_agent.exploration.contracts import ExplorationError
from aedt_agent.exploration.tool_contracts import operation_plan_schema
from aedt_agent.exploration.validator import OperationValidator


def _evidence(symbol="Line3dLayout.width", version="1.3.0"):
    return {
        "package": "pyaedt",
        "package_version": version,
        "project": "ansys-pyaedt-1.3.0-hash",
        "symbol": symbol,
        "source_path": "modeler/pcb/object_3d_layout.py",
        "snippet_digest": "a" * 64,
        "query_id": "query-1234567890",
    }


def _plan(*, risk="reversible_edit", steps=None, readback=None, rollback=None, evidence=None):
    return {
        "schema_version": "ansys-operation-plan/v1",
        "intent": "Set a bounded property using current-version source evidence",
        "target": {
            "product": "hfss3dlayout",
            "project_name": "Board",
            "design_name": "Layout1",
        },
        "risk": risk,
        "evidence": evidence or [_evidence()],
        "steps": steps or [{"id": "set-width", "op": "set_attr", "path": "modeler.selected_line.width", "value": "trace_w"}],
        "readback": readback if readback is not None else [
            {"id": "width-check", "path": "modeler.selected_line.width", "operator": "equals", "expected": "trace_w"}
        ],
        "rollback": rollback if rollback is not None else ["set-width"],
    }


def test_validator_accepts_versioned_reversible_property_edit():
    validator = OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"})
    report = validator.validate(_plan())
    assert report["status"] == "validated"
    assert report["mutation_count"] == 1
    assert report["rollback_strategy"] == "server_snapshot"
    assert len(report["plan_digest"]) == 64


def test_operation_plan_tool_schema_exposes_exact_shape_and_copyable_example():
    schema = operation_plan_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "schema_version",
        "intent",
        "target",
        "risk",
        "evidence",
        "steps",
        "readback",
        "rollback",
    ]
    assert schema["properties"]["steps"]["items"]["oneOf"][0]["properties"]["op"] == {
        "const": "read_attr"
    }
    assert schema["example_read_only"]["evidence"] == [
        "COPY inspect_ansys_symbol.operation_evidence OBJECT HERE"
    ]


def test_validator_accepts_evidenced_read_only_getter_call():
    validator = OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"})
    plan = _plan(
        risk="read_only",
        evidence=[_evidence("Desktop.get_available_toolkits")],
        steps=[
            {
                "id": "lookup",
                "op": "call",
                "path": "get_available_toolkits",
                "args": [],
                "kwargs": {},
            }
        ],
        readback=[],
        rollback=[],
    )
    report = validator.validate(plan)
    assert report["risk"] == "read_only"
    assert report["evidence_bindings"]["steps"]["lookup"][0]["owner"] == "Desktop"


def test_validator_rejects_getter_prefix_that_is_not_in_exact_safe_call_allowlist():
    validator = OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"})
    plan = _plan(
        risk="read_only",
        evidence=[_evidence("Modeler.get_object_from_name")],
        steps=[
            {
                "id": "lookup",
                "op": "call",
                "path": "modeler.get_object_from_name",
                "args": ["trace1"],
                "kwargs": {},
            }
        ],
        readback=[],
        rollback=[],
    )

    with pytest.raises(ExplorationError) as error:
        validator.validate(plan)
    assert error.value.code == "operation_unclassified"


def test_validator_rejects_allowlisted_method_for_an_unaudited_package_version():
    validator = OperationValidator(package_versions={"pyaedt": "1.3.1", "pyedb": "0.80.2"})
    plan = _plan(
        risk="read_only",
        evidence=[_evidence("Desktop.get_available_toolkits", version="1.3.1")],
        steps=[
            {
                "id": "lookup",
                "op": "call",
                "path": "get_available_toolkits",
                "args": [],
                "kwargs": {},
            }
        ],
        readback=[],
        rollback=[],
    )

    with pytest.raises(ExplorationError) as error:
        validator.validate(plan)
    assert error.value.code == "operation_unclassified"


def test_validator_requires_evidence_for_every_readback_path():
    validator = OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"})
    plan = _plan(
        risk="read_only",
        evidence=[_evidence("Line3dLayout.name")],
        steps=[{"id": "read-name", "op": "read_attr", "path": "modeler.selected_line.name"}],
        readback=[
            {
                "id": "unevidenced-width",
                "path": "modeler.selected_line.width",
                "operator": "truthy",
                "expected": True,
            }
        ],
        rollback=[],
    )

    with pytest.raises(ExplorationError) as error:
        validator.validate(plan)
    assert error.value.code == "evidence_required"


def test_validator_rejects_pyedb_evidence_for_live_pyaedt_target():
    validator = OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"})
    pyedb_evidence = _evidence("Line3dLayout.width", version="0.80.2")
    pyedb_evidence.update(
        {
            "package": "pyedb",
            "project": "ansys-pyedb-0.80.2-hash",
            "source_path": "dotnet/edb_core/cell/primitive/path.py",
        }
    )

    with pytest.raises(ExplorationError) as error:
        validator.validate(_plan(evidence=[pyedb_evidence]))
    assert error.value.code == "evidence_package_mismatch"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda plan: plan["steps"][0].update(path="odesktop.QuitApplication"), "path_forbidden"),
        (lambda plan: plan["steps"][0].update(path="modeler.__class__"), "path_forbidden"),
        (lambda plan: plan.update(evidence=[]), "evidence_required"),
        (lambda plan: plan["evidence"][0].update(package_version="0.0.1"), "evidence_stale"),
        (lambda plan: plan.update(readback=[]), "readback_required"),
        (lambda plan: plan.update(rollback=[]), "rollback_required"),
    ],
)
def test_validator_fails_closed_for_unsafe_or_unverifiable_plans(mutate, code):
    validator = OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"})
    plan = _plan()
    mutate(plan)
    with pytest.raises(ExplorationError) as error:
        validator.validate(plan)
    assert error.value.code == code


def test_validator_rejects_unclassified_mutating_method_even_with_source_evidence():
    validator = OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"})
    plan = _plan(
        evidence=[_evidence("Modeler.create_box")],
        steps=[
            {
                "id": "create",
                "op": "call",
                "path": "modeler.create_box",
                "args": [[0, 0, 0], [1, 1, 1]],
                "kwargs": {},
            }
        ],
        readback=[],
        rollback=["create"],
    )
    with pytest.raises(ExplorationError) as error:
        validator.validate(plan)
    assert error.value.code == "operation_unclassified"
