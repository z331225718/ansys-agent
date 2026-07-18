from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


DEFAULT_HANDOFF_LIMITS = {
    "max_depth": 16,
    "max_nodes": 10_000,
    "max_serialized_bytes": 1024 * 1024,
}

_SCHEMA_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "items",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
}
_ROOT_METADATA_KEYWORDS = {"id", "required_fields", "limits"}
_SUPPORTED_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
_PROPERTY_PATH_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HandoffValidationError(ValueError):
    """Raised when a graph node handoff payload does not match its schema."""


class HandoffSchemaDefinitionError(ValueError):
    """Raised when a handoff schema definition is malformed."""


@dataclass(frozen=True)
class HandoffSchema:
    schema_id: str
    required_fields: list[str]
    schema: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required_fields = _required_names(
            self.schema_id,
            self.required_fields,
            "$.required_fields",
        )
        if not isinstance(self.schema, Mapping):
            raise _definition_error(self.schema_id, "$", "schema must be a mapping")
        schema = copy.deepcopy(dict(self.schema))
        _validate_schema_definition(self.schema_id, schema, "$")

        schema_required = list(schema.get("required", []))
        merged_required = _deduplicate([*required_fields, *schema_required])
        if "required" in schema:
            schema["required"] = list(merged_required)

        limits = _validated_limits(self.schema_id, self.limits)
        object.__setattr__(self, "required_fields", merged_required)
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "limits", limits)

    def to_json_dict(self) -> dict[str, Any]:
        result = {
            "id": self.schema_id,
            "required_fields": list(self.required_fields),
        }
        result.update(copy.deepcopy(self.schema))
        if self.limits:
            result["limits"] = dict(self.limits)
        return result


def handoff_schema_from_mapping(
    schema_id: str,
    value: Mapping[str, Any],
) -> HandoffSchema:
    """Build and validate a handoff schema from its YAML/JSON representation."""

    for keyword in value:
        if not isinstance(keyword, str) or keyword not in _SCHEMA_KEYWORDS | _ROOT_METADATA_KEYWORDS:
            path = _property_path("$", keyword)
            raise _definition_error(schema_id, path, f"unknown keyword: {keyword!r}")

    serialized_id = value.get("id")
    if serialized_id is not None:
        if not isinstance(serialized_id, str):
            raise _definition_error(schema_id, "$.id", "id must be a string")
        if serialized_id != schema_id:
            raise _definition_error(
                schema_id,
                "$.id",
                f"id must match the handoff mapping key {schema_id!r}",
            )

    required_fields = value.get("required_fields", [])
    if required_fields is None:
        required_fields = []
    schema = {key: copy.deepcopy(item) for key, item in value.items() if key in _SCHEMA_KEYWORDS}
    limits = value.get("limits", {})
    return HandoffSchema(
        schema_id=schema_id,
        required_fields=required_fields,
        schema=schema,
        limits=limits,
    )


def validate_handoff(schema: HandoffSchema, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        _raise_validation(schema.schema_id, "type", "$", "root payload must be an object")

    limits = _effective_limits(schema.limits)
    _validate_payload_limits(schema.schema_id, payload, limits)
    _validate_value(
        schema.schema_id,
        schema.schema,
        payload,
        "$",
        root_required=schema.required_fields,
    )
    return payload


def _validate_schema_definition(schema_id: str, schema: Mapping[str, Any], path: str) -> None:
    for keyword in schema:
        if not isinstance(keyword, str) or keyword not in _SCHEMA_KEYWORDS:
            keyword_path = _property_path(path, keyword)
            raise _definition_error(schema_id, keyword_path, f"unknown keyword: {keyword!r}")

    declared_type = schema.get("type")
    if declared_type is not None:
        if not isinstance(declared_type, str):
            raise _definition_error(schema_id, f"{path}.type", "type must be a string")
        if declared_type not in _SUPPORTED_TYPES:
            raise _definition_error(
                schema_id,
                f"{path}.type",
                f"unsupported type: {declared_type!r}",
            )

    _validate_keyword_compatibility(schema_id, schema, path, declared_type)

    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, Mapping):
            raise _definition_error(schema_id, f"{path}.properties", "properties must be a mapping")
        for name, child_schema in properties.items():
            child_path = _property_path(f"{path}.properties", name)
            if not isinstance(name, str):
                raise _definition_error(schema_id, child_path, "property names must be strings")
            if not isinstance(child_schema, Mapping):
                raise _definition_error(schema_id, child_path, "property schema must be a mapping")
            _validate_schema_definition(schema_id, child_schema, child_path)

    if "required" in schema:
        _required_names(schema_id, schema["required"], f"{path}.required")

    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise _definition_error(
            schema_id,
            f"{path}.additionalProperties",
            "additionalProperties must be a boolean",
        )

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise _definition_error(schema_id, f"{path}.enum", "enum must be a non-empty list")
        for index, item in enumerate(enum):
            if not _is_json_value(item):
                raise _definition_error(
                    schema_id,
                    f"{path}.enum[{index}]",
                    "enum values must be JSON-compatible",
                )

    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, Mapping):
            raise _definition_error(schema_id, f"{path}.items", "items must be a mapping")
        _validate_schema_definition(schema_id, items, f"{path}.items")

    for keyword in (
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
    ):
        if keyword in schema:
            _non_negative_schema_int(schema_id, schema[keyword], f"{path}.{keyword}")

    for keyword in ("minimum", "maximum"):
        if keyword in schema:
            _finite_schema_number(schema_id, schema[keyword], f"{path}.{keyword}")

    for minimum, maximum in (
        ("minLength", "maxLength"),
        ("minimum", "maximum"),
        ("minItems", "maxItems"),
        ("minProperties", "maxProperties"),
    ):
        if minimum in schema and maximum in schema and schema[minimum] > schema[maximum]:
            raise _definition_error(
                schema_id,
                f"{path}.{minimum}",
                f"{minimum} must not exceed {maximum}",
            )


def _validate_keyword_compatibility(
    schema_id: str,
    schema: Mapping[str, Any],
    path: str,
    declared_type: object,
) -> None:
    if declared_type is None:
        return
    keyword_types = {
        "properties": {"object"},
        "required": {"object"},
        "additionalProperties": {"object"},
        "minProperties": {"object"},
        "maxProperties": {"object"},
        "items": {"array"},
        "minItems": {"array"},
        "maxItems": {"array"},
        "minLength": {"string"},
        "maxLength": {"string"},
        "minimum": {"number", "integer"},
        "maximum": {"number", "integer"},
    }
    for keyword, allowed_types in keyword_types.items():
        if keyword in schema and declared_type not in allowed_types:
            raise _definition_error(
                schema_id,
                f"{path}.{keyword}",
                f"{keyword} is not valid for type {declared_type!r}",
            )


def _validated_limits(schema_id: str, value: object) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _definition_error(schema_id, "$.limits", "limits must be a mapping")
    limits: dict[str, int] = {}
    for name, limit in value.items():
        path = _property_path("$.limits", name)
        if name not in DEFAULT_HANDOFF_LIMITS:
            raise _definition_error(schema_id, path, f"unknown limit: {name!r}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise _definition_error(schema_id, path, "limit must be a positive integer")
        hard_limit = DEFAULT_HANDOFF_LIMITS[name]
        if limit > hard_limit:
            raise _definition_error(
                schema_id,
                path,
                f"limit may only tighten the hard maximum of {hard_limit}",
            )
        limits[name] = limit
    return limits


def _effective_limits(declared: Mapping[str, int]) -> dict[str, int]:
    return {
        name: min(hard_limit, declared.get(name, hard_limit))
        for name, hard_limit in DEFAULT_HANDOFF_LIMITS.items()
    }


def _validate_payload_limits(
    schema_id: str,
    payload: Mapping[str, Any],
    limits: Mapping[str, int],
) -> None:
    max_depth = limits["max_depth"]
    max_nodes = limits["max_nodes"]
    stack: list[tuple[Any, str, int, frozenset[int]]] = [(payload, "$", 1, frozenset())]
    node_count = 0

    while stack:
        value, path, depth, ancestors = stack.pop()
        node_count += 1
        if node_count > max_nodes:
            _raise_validation(
                schema_id,
                "nodes",
                path,
                f"payload exceeds max_nodes={max_nodes}",
            )

        if not _is_container(value):
            continue
        if depth > max_depth:
            _raise_validation(
                schema_id,
                "depth",
                path,
                f"payload exceeds max_depth={max_depth}",
            )
        identity = id(value)
        if identity in ancestors:
            _raise_validation(schema_id, "depth", path, "payload contains a container cycle")
        child_ancestors = ancestors | {identity}

        if isinstance(value, Mapping):
            children = [
                (item, _property_path(path, name))
                for name, item in value.items()
            ]
        else:
            children = [
                (item, f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
        for child, child_path in reversed(children):
            child_depth = depth + 1 if _is_container(child) else depth
            stack.append((child, child_path, child_depth, child_ancestors))

    normalized = _normalize_for_size(payload, set())
    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    max_bytes = limits["max_serialized_bytes"]
    if len(serialized) > max_bytes:
        _raise_validation(
            schema_id,
            "size",
            "$",
            f"serialized payload is {len(serialized)} bytes; max_serialized_bytes={max_bytes}",
        )


def _normalize_for_size(value: Any, active: set[int]) -> Any:
    if not _is_container(value):
        return value
    identity = id(value)
    if identity in active:
        return "<cycle>"
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            return {str(key): _normalize_for_size(item, active) for key, item in value.items()}
        return [_normalize_for_size(item, active) for item in value]
    finally:
        active.remove(identity)


def _validate_value(
    schema_id: str,
    definition: Mapping[str, Any],
    value: Any,
    path: str,
    *,
    root_required: list[str] | None = None,
) -> None:
    expected_type = definition.get("type")
    if expected_type is not None and not _matches_type(expected_type, value):
        _raise_validation(
            schema_id,
            "type",
            path,
            f"expected {expected_type}, got {_value_type(value)}",
        )

    if "enum" in definition and not any(_enum_equal(value, option) for option in definition["enum"]):
        _raise_validation(schema_id, "enum", path, "value is not one of the allowed enum values")

    if isinstance(value, Mapping):
        required = root_required if root_required is not None else definition.get("required", [])
        for name in required:
            if name not in value:
                _raise_validation(
                    schema_id,
                    "missing required fields",
                    _property_path(path, name),
                    "required property is missing",
                )

        property_count = len(value)
        _validate_lower_bound(schema_id, path, "minProperties", property_count, definition)
        _validate_upper_bound(schema_id, path, "maxProperties", property_count, definition)

        properties = definition.get("properties", {})
        if definition.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    _raise_validation(
                        schema_id,
                        "additional property",
                        _property_path(path, name),
                        "property is not allowed",
                    )

        for name, child_definition in properties.items():
            if name in value:
                _validate_value(
                    schema_id,
                    child_definition,
                    value[name],
                    _property_path(path, name),
                )

    if isinstance(value, (list, tuple)):
        _validate_lower_bound(schema_id, path, "minItems", len(value), definition)
        _validate_upper_bound(schema_id, path, "maxItems", len(value), definition)
        items = definition.get("items")
        if items is not None:
            for index, item in enumerate(value):
                _validate_value(schema_id, items, item, f"{path}[{index}]")

    if isinstance(value, str):
        _validate_lower_bound(schema_id, path, "minLength", len(value), definition)
        _validate_upper_bound(schema_id, path, "maxLength", len(value), definition)

    if _is_number(value):
        if "minimum" in definition and value < definition["minimum"]:
            _raise_validation(
                schema_id,
                "bounds",
                path,
                f"value must be >= {definition['minimum']}",
            )
        if "maximum" in definition and value > definition["maximum"]:
            _raise_validation(
                schema_id,
                "bounds",
                path,
                f"value must be <= {definition['maximum']}",
            )


def _validate_lower_bound(
    schema_id: str,
    path: str,
    keyword: str,
    actual: int,
    definition: Mapping[str, Any],
) -> None:
    if keyword in definition and actual < definition[keyword]:
        _raise_validation(
            schema_id,
            "bounds",
            path,
            f"{keyword}={definition[keyword]}, got {actual}",
        )


def _validate_upper_bound(
    schema_id: str,
    path: str,
    keyword: str,
    actual: int,
    definition: Mapping[str, Any],
) -> None:
    if keyword in definition and actual > definition[keyword]:
        _raise_validation(
            schema_id,
            "bounds",
            path,
            f"{keyword}={definition[keyword]}, got {actual}",
        )


def _matches_type(expected_type: str, value: Any) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, (list, tuple))
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return _is_number(value)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return value is None


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _enum_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if _is_number(left) and _is_number(right):
        return left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return len(left) == len(right) and all(
            key in right and _enum_equal(item, right[key]) for key, item in left.items()
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _enum_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return type(left) is type(right) and left == right


def _required_names(schema_id: str, value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        raise _definition_error(schema_id, path, "required fields must be a list")
    for index, name in enumerate(value):
        if not isinstance(name, str):
            raise _definition_error(schema_id, f"{path}[{index}]", "required field must be a string")
    return _deduplicate(value)


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _non_negative_schema_int(schema_id: str, value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _definition_error(schema_id, path, "value must be a non-negative integer")
    return value


def _finite_schema_number(schema_id: str, value: object, path: str) -> int | float:
    if not _is_number(value) or not math.isfinite(value):
        raise _definition_error(schema_id, path, "value must be a finite number")
    return value


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_container(value: object) -> bool:
    return isinstance(value, (Mapping, list, tuple))


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool)) or _is_number(value):
        return not isinstance(value, float) or math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _property_path(path: str, name: object) -> str:
    if isinstance(name, str) and _PROPERTY_PATH_PART.fullmatch(name):
        return f"{path}.{name}"
    if isinstance(name, str):
        return f"{path}[{json.dumps(name, ensure_ascii=False)}]"
    return f"{path}[{name!r}]"


def _definition_error(schema_id: str, path: str, detail: str) -> HandoffSchemaDefinitionError:
    return HandoffSchemaDefinitionError(f"handoff schema {schema_id} invalid at {path}: {detail}")


def _raise_validation(schema_id: str, category: str, path: str, detail: str) -> None:
    raise HandoffValidationError(f"handoff {schema_id} {category} at {path}: {detail}")
