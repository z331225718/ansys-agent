from types import SimpleNamespace

import pytest

from aedt_agent.controlled import ControlledProgramError
from aedt_agent.controlled import execute_read_program
from aedt_agent.controlled import read_program_schema
from aedt_agent.controlled import validate_read_program


def _app():
    return SimpleNamespace(
        modeler=SimpleNamespace(
            vias={"V1": SimpleNamespace(net_name="SIG", width="0.2mm")},
            components={},
            pins={},
            nets={},
            lines={},
            polygons={},
            rectangles={},
            circles={},
            excitations={},
        )
    )


def test_controlled_read_program_reads_public_property_and_bounds_surface():
    schema = read_program_schema()
    assert schema["schema_version"] == "controlled-aedt-read/v1"
    assert "via" in schema["object_kinds"]
    program = {
        "schema_version": "controlled-aedt-read/v1",
        "mode": "read_only",
        "product": "layout",
        "steps": [
            {"id": "vias", "op": "list_objects", "object_kind": "via"},
            {"id": "net", "op": "read_attribute", "object_kind": "via", "name": "V1", "attribute": "net_name"},
        ],
    }
    result = execute_read_program(_app(), validate_read_program(program, product="layout"))
    assert result["results"] == [
        {"id": "vias", "status": "ok", "count": 1, "names": ["V1"]},
        {"id": "net", "status": "ok", "name": "V1", "attribute": "net_name", "value": "SIG"},
    ]
    assert result["program_hash"]
    assert result["response_digest"]


@pytest.mark.parametrize(
    "program",
    [
        {"schema_version": "controlled-aedt-read/v1", "mode": "write", "product": "layout", "steps": []},
        {"schema_version": "controlled-aedt-read/v1", "mode": "read_only", "product": "layout", "steps": [{"id": "x", "op": "call", "object_kind": "via"}]},
        {"schema_version": "controlled-aedt-read/v1", "mode": "read_only", "product": "layout", "steps": [{"id": "x", "op": "read_attribute", "object_kind": "via", "name": "V1", "attribute": "__class__"}]},
    ],
)
def test_controlled_read_program_rejects_code_and_reflection_shapes(program):
    with pytest.raises(ControlledProgramError):
        validate_read_program(program, product="layout")
