from __future__ import annotations

import copy
from collections import UserDict

import pytest

from aedt_agent.agent.graph_template import GraphTemplateError, graph_template_from_mapping
from aedt_agent.agent.handoff import (
    DEFAULT_HANDOFF_LIMITS,
    HandoffSchema,
    HandoffValidationError,
    validate_handoff,
)


def _template_with_handoff(contract: object):
    return graph_template_from_mapping(
        {
            "id": "handoff_contract_test",
            "version": 1,
            "nodes": [
                {
                    "id": "producer",
                    "role": "planner",
                    "kind": "llm",
                    "output_schema": "bounded_result",
                }
            ],
            "edges": [],
            "handoffs": {"bounded_result": contract},
        }
    )


def _schema(contract: dict[str, object]):
    return _template_with_handoff(contract).handoffs["bounded_result"]


def _value_schema(value_schema: dict[str, object]):
    return _schema(
        {
            "type": "object",
            "properties": {"value": value_schema},
            "required": ["value"],
            "additionalProperties": False,
        }
    )


def _nested_contract() -> dict[str, object]:
    return {
        "required_fields": ["request_id", "ports"],
        "type": "object",
        "properties": {
            "request_id": {"type": "string", "minLength": 1, "maxLength": 32},
            "mode": {"type": "string", "enum": ["review", "solve"]},
            "ports": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 16},
                        "impedance": {"type": "number", "minimum": 25, "maximum": 100},
                    },
                    "required": ["name", "impedance"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["ports", "mode"],
        "additionalProperties": False,
    }


def test_legacy_required_fields_shape_and_validation_remain_compatible():
    schema = _schema({"required_fields": ["value"]})
    payload = {"value": object()}

    assert schema.required_fields == ["value"]
    assert schema.to_json_dict() == {"id": "bounded_result", "required_fields": ["value"]}
    assert validate_handoff(schema, payload) is payload

    with pytest.raises(HandoffValidationError, match=r"bounded_result.*missing.*\$\.value"):
        validate_handoff(schema, {})


def test_required_and_required_fields_are_merged_and_deduplicated():
    schema = _schema(
        {
            "required_fields": ["legacy", "shared"],
            "type": "object",
            "required": ["shared", "modern"],
        }
    )

    assert schema.required_fields == ["legacy", "shared", "modern"]
    assert schema.to_json_dict()["required"] == ["legacy", "shared", "modern"]


def test_nested_contract_accepts_mappings_and_tuple_arrays_without_mutation():
    schema = _schema(_nested_contract())
    payload = UserDict(
        {
            "request_id": "req-7",
            "mode": "review",
            "ports": (
                UserDict({"name": "P1", "impedance": 50}),
                UserDict({"name": "P2", "impedance": 50.5}),
            ),
        }
    )
    before = copy.deepcopy(payload)

    assert validate_handoff(schema, payload) is payload
    assert payload == before


def test_nested_missing_field_reports_exact_payload_path():
    schema = _schema(_nested_contract())
    payload = {
        "request_id": "req-7",
        "mode": "solve",
        "ports": [{"name": "P1", "impedance": 50}, {"impedance": 55}],
    }

    with pytest.raises(
        HandoffValidationError,
        match=r"bounded_result.*missing.*\$\.ports\[1\]\.name",
    ):
        validate_handoff(schema, payload)


@pytest.mark.parametrize("declared_type", ["integer", "number"])
def test_bool_does_not_satisfy_numeric_types(declared_type):
    schema = _value_schema({"type": declared_type})

    with pytest.raises(HandoffValidationError, match=r"bounded_result type at \$\.value"):
        validate_handoff(schema, {"value": True})


def test_root_payload_must_remain_a_mapping():
    schema = HandoffSchema("root_object", [])

    with pytest.raises(HandoffValidationError, match=r"root_object type at \$"):
        validate_handoff(schema, [])  # type: ignore[arg-type]


def test_additional_properties_false_reports_the_extra_property_path():
    schema = _value_schema({"type": "integer"})

    with pytest.raises(
        HandoffValidationError,
        match=r"bounded_result additional property at \$\.extra",
    ):
        validate_handoff(schema, {"value": 1, "extra": 2})


def test_enum_is_enforced_at_the_value_path():
    schema = _value_schema({"type": "string", "enum": ["queued", "done"]})

    with pytest.raises(HandoffValidationError, match=r"bounded_result enum at \$\.value"):
        validate_handoff(schema, {"value": "running"})


@pytest.mark.parametrize(
    ("value_schema", "value"),
    [
        ({"type": "string", "minLength": 2, "maxLength": 4}, "x"),
        ({"type": "string", "minLength": 2, "maxLength": 4}, "abcde"),
        ({"type": "number", "minimum": 1, "maximum": 3}, 0),
        ({"type": "number", "minimum": 1, "maximum": 3}, 4),
        ({"type": "array", "items": {"type": "integer"}, "minItems": 2}, [1]),
        ({"type": "array", "items": {}, "maxItems": 2}, [1, 2, 3]),
        ({"type": "object", "minProperties": 1}, {}),
        ({"type": "object", "maxProperties": 1}, {"a": 1, "b": 2}),
    ],
)
def test_string_numeric_array_and_object_bounds(value_schema, value):
    schema = _value_schema(value_schema)

    with pytest.raises(HandoffValidationError, match=r"bounded_result bounds at \$\.value"):
        validate_handoff(schema, {"value": value})


def test_declared_depth_limit_is_enforced():
    schema = _schema({"type": "object", "limits": {"max_depth": 2}})

    with pytest.raises(HandoffValidationError, match=r"bounded_result depth at \$\.a\.b"):
        validate_handoff(schema, {"a": {"b": {"c": 1}}})


def test_declared_node_limit_is_enforced():
    schema = _schema({"type": "object", "limits": {"max_nodes": 3}})

    with pytest.raises(HandoffValidationError, match=r"bounded_result nodes at \$\.c"):
        validate_handoff(schema, {"a": 1, "b": 2, "c": 3})


def test_declared_serialized_byte_limit_is_enforced():
    schema = _schema({"type": "object", "limits": {"max_serialized_bytes": 32}})

    with pytest.raises(HandoffValidationError, match=r"bounded_result size at \$"):
        validate_handoff(schema, {"value": "x" * 64})


def test_default_serialized_byte_hard_limit_applies_to_legacy_schema():
    schema = HandoffSchema("legacy_bounded", [])

    with pytest.raises(HandoffValidationError, match=r"legacy_bounded size at \$"):
        validate_handoff(
            schema,
            {"value": "x" * (DEFAULT_HANDOFF_LIMITS["max_serialized_bytes"] + 1)},
        )


@pytest.mark.parametrize(
    ("contract", "path"),
    [
        ([], r"\$"),
        ({"type": "object", "unknown": True}, r"\$\.unknown"),
        ({"type": ["object"]}, r"\$\.type"),
        ({"type": "string", "minLength": 4, "maxLength": 2}, r"\$\.minLength"),
        ({"type": "string", "minLength": 1.5}, r"\$\.minLength"),
        ({"type": "object", "properties": []}, r"\$\.properties"),
        ({"type": "object", "properties": {"name": "string"}}, r"\$\.properties\.name"),
        ({"type": "array", "items": []}, r"\$\.items"),
        ({"type": "array", "minimum": 1}, r"\$\.minimum"),
        ({"limits": {"max_nodes": 0}}, r"\$\.limits\.max_nodes"),
        (
            {"limits": {"max_depth": DEFAULT_HANDOFF_LIMITS["max_depth"] + 1}},
            r"\$\.limits\.max_depth",
        ),
        ({"limits": {"unknown": 1}}, r"\$\.limits\.unknown"),
    ],
)
def test_invalid_schema_is_rejected_during_template_loading(contract, path):
    with pytest.raises(
        GraphTemplateError,
        match=rf"handoff schema bounded_result invalid at {path}",
    ):
        _template_with_handoff(contract)


def test_structured_handoff_survives_template_json_round_trip():
    template = _template_with_handoff(
        {
            **_nested_contract(),
            "limits": {
                "max_depth": 8,
                "max_nodes": 200,
                "max_serialized_bytes": 4096,
            },
        }
    )
    serialized = template.to_json_dict()

    reloaded = graph_template_from_mapping(serialized)

    assert reloaded.handoffs["bounded_result"] == template.handoffs["bounded_result"]
    assert reloaded.to_json_dict() == serialized
