from __future__ import annotations

from types import SimpleNamespace

import pytest

from aedt_agent.exploration.executor import apply_preview, build_preview
from aedt_agent.exploration.validator import OperationValidator


class Line3dLayout:
    def __init__(self, *, name="trace1", width="0.1mm"):
        self.name = name
        self.width = width


class Desktop:
    def __init__(self):
        self.call_count = 0

    def get_available_toolkits(self):
        self.call_count += 1
        return ["toolkit-a"]


def _validation(value="trace_w"):
    plan = {
        "schema_version": "ansys-operation-plan/v1",
        "intent": "Change one selected line width",
        "target": {"product": "hfss3dlayout", "project_name": "Board", "design_name": "Layout1"},
        "risk": "reversible_edit",
        "evidence": [
            {
                "package": "pyaedt",
                "package_version": "1.3.0",
                "project": "pyaedt-graph",
                "symbol": "Line3dLayout.width",
                "source_path": "object_3d_layout.py",
                "snippet_digest": "a" * 64,
                "query_id": "query-source-proof",
            }
        ],
        "steps": [{"id": "set-width", "op": "set_attr", "path": "modeler.lines.trace1.width", "value": value}],
        "readback": [{"id": "check", "path": "modeler.lines.trace1.width", "operator": "equals", "expected": value}],
        "rollback": ["set-width"],
    }
    return OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"}).validate(plan)


def _app(width="0.1mm"):
    line = Line3dLayout(width=width)
    return SimpleNamespace(modeler=SimpleNamespace(lines={"trace1": line})), line


def _call_validation():
    plan = {
        "schema_version": "ansys-operation-plan/v1",
        "intent": "List locally available Desktop toolkits",
        "target": {"product": "desktop", "project_name": "Board", "design_name": "Layout1"},
        "risk": "read_only",
        "evidence": [
            {
                "package": "pyaedt",
                "package_version": "1.3.0",
                "project": "pyaedt-graph",
                "symbol": "ansys.aedt.core.desktop.Desktop.get_available_toolkits",
                "source_path": "desktop.py",
                "snippet_digest": "b" * 64,
                "query_id": "query-safe-call-proof",
            }
        ],
        "steps": [
            {
                "id": "list-toolkits",
                "op": "call",
                "path": "get_available_toolkits",
                "args": [],
                "kwargs": {},
            }
        ],
        "readback": [],
        "rollback": [],
    }
    return OperationValidator(package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"}).validate(plan)


def test_exploration_preview_is_read_only_and_apply_verifies():
    app, line = _app()
    public, state = build_preview(
        app,
        _validation(),
        target_identity={"port": 50061, "project_name": "Board", "design_name": "Layout1"},
    )
    assert line.width == "0.1mm"
    assert public["approval_required"] is True
    assert public["mutations"][0]["old_value"] == "0.1mm"
    result = apply_preview(app, state)
    assert result["status"] == "verified"
    assert line.width == "trace_w"


def test_safe_call_is_not_invoked_by_preview_and_runs_once_during_apply():
    app = Desktop()
    public, state = build_preview(
        app,
        _call_validation(),
        target_identity={"port": 50061, "project_name": "Board", "design_name": "Layout1"},
    )

    assert public["approval_required"] is False
    assert app.call_count == 0
    result = apply_preview(app, state)
    assert result["status"] == "verified"
    assert app.call_count == 1


def test_preview_rejects_same_member_evidence_from_wrong_runtime_class():
    validation = _validation()
    for binding in validation["evidence_bindings"]["steps"]["set-width"]:
        binding["owner"] = "UnrelatedObject"

    app, _ = _app()
    with pytest.raises(ValueError, match="does not match runtime owner Line3dLayout"):
        build_preview(
            app,
            validation,
            target_identity={"port": 50061, "project_name": "Board", "design_name": "Layout1"},
        )


def test_exploration_apply_rejects_stale_preview_without_mutation():
    app, line = _app()
    _, state = build_preview(
        app,
        _validation(),
        target_identity={"port": 50061, "project_name": "Board", "design_name": "Layout1"},
    )
    line.width = "0.2mm"
    result = apply_preview(app, state)
    assert result["status"] == "stale_preview"
    assert result["mutation_applied"] is False
    assert line.width == "0.2mm"


def test_exploration_readback_failure_rolls_back_server_snapshot():
    app, line = _app()
    validation = _validation("trace_w")
    validation["plan"]["readback"][0]["expected"] = "different_value"
    _, state = build_preview(
        app,
        validation,
        target_identity={"port": 50061, "project_name": "Board", "design_name": "Layout1"},
    )
    result = apply_preview(app, state)
    assert result["status"] == "rolled_back"
    assert result["rollback"]["verified"] is True
    assert line.width == "0.1mm"
