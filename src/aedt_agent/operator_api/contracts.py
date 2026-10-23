from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

MAX_PROTOCOL_FRAME_BYTES = 1_048_576


class OperatorContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def decode_ndjson_frame(frame: bytes) -> dict[str, Any]:
    if not frame.endswith(b"\n") or len(frame) > MAX_PROTOCOL_FRAME_BYTES:
        raise OperatorContractError("protocol_error", "frame must be one bounded LF-terminated NDJSON record")
    try:
        text = frame[:-1].decode("utf-8")
    except UnicodeDecodeError as error:
        raise OperatorContractError("protocol_error", "frame must be valid UTF-8 JSON") from error
    if not text or "\r" in text:
        raise OperatorContractError("protocol_error", "frame must contain exactly one JSON object")
    try:
        value = json.loads(text, parse_constant=_reject_json_constant, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as error:
        raise OperatorContractError("protocol_error", "frame must be valid UTF-8 JSON without duplicate keys") from error
    if not isinstance(value, dict):
        raise OperatorContractError("protocol_error", "frame must be a JSON object")
    return value


class OperatorSchemaCatalog:
    def __init__(self, schema_root: Path, schemas: dict[str, dict[str, Any]], bundle_digest: str):
        self.schema_root = schema_root
        self.schemas = schemas
        self.bundle_digest = bundle_digest
        resources = [(schema_id, Resource.from_contents(schema)) for schema_id, schema in schemas.items()]
        self.registry = Registry().with_resources(resources)
        self.validators = {
            schema_id: Draft202012Validator(schema, registry=self.registry, format_checker=FormatChecker())
            for schema_id, schema in schemas.items()
        }

    @classmethod
    def load(cls, schema_root: Path | str = Path("schemas/operator/v1")) -> "OperatorSchemaCatalog":
        root = Path(schema_root)
        if not root.is_dir():
            raise FileNotFoundError(f"operator schema directory not found: {root}")
        files = sorted(path for path in root.rglob("*.schema.json") if path.is_file())
        if not files:
            raise FileNotFoundError(f"no operator schemas found under: {root}")
        schemas: dict[str, dict[str, Any]] = {}
        digest_source: list[bytes] = []
        for path in files:
            raw = path.read_bytes()
            schema = json.loads(raw)
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id.startswith("urn:aedt-agent:operator:v1:"):
                raise OperatorContractError("protocol_error", f"invalid schema id in {path}")
            if schema_id in schemas:
                raise OperatorContractError("protocol_error", f"duplicate schema id: {schema_id}")
            schemas[schema_id] = schema
            digest_source.extend((path.relative_to(root).as_posix().encode("utf-8"), b"\0", raw, b"\0"))
        return cls(root, schemas, "sha256:" + hashlib.sha256(b"".join(digest_source)).hexdigest())

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "bundle_digest": self.bundle_digest,
            "schemas": sorted(
                path.relative_to(self.schema_root).as_posix()
                for path in self.schema_root.rglob("*.schema.json")
            ),
        }

    def validate_wire_request(self, frame: bytes) -> dict[str, Any]:
        request = decode_ndjson_frame(frame)
        self.validate_request(request)
        return request

    def validate_envelope(self, value: dict[str, Any]) -> None:
        self._validate("urn:aedt-agent:operator:v1:envelope", value)

    def validate_request(self, request: dict[str, Any]) -> None:
        self.validate_envelope(request)
        if request.get("type") != "request":
            raise OperatorContractError("invalid_argument", "expected a request envelope")
        operation = request.get("operation")
        if not isinstance(operation, str):
            raise OperatorContractError("invalid_argument", "request operation is missing")
        schema_id = f"urn:aedt-agent:operator:v1:operation:{operation}"
        if schema_id not in self.schemas:
            raise OperatorContractError("unsupported_operation", f"unsupported operation: {operation}")
        self._validate(schema_id, request.get("arguments"))

    def validate_success_data(self, operation: str, data: Any) -> None:
        schema_id = f"urn:aedt-agent:operator:v1:operation:{operation}"
        schema = self.schemas.get(schema_id)
        if schema is None:
            raise OperatorContractError("unsupported_operation", f"unsupported operation: {operation}")
        success_schema = schema.get("$defs", {}).get("successData")
        if not isinstance(success_schema, dict):
            raise OperatorContractError("protocol_error", f"operation has no success result contract: {operation}")
        validator = Draft202012Validator(success_schema, registry=self.registry, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(data), key=lambda item: item.json_path)
        if errors:
            raise OperatorContractError("protocol_error", f"invalid success data for {operation}: {errors[0].message}")

    def validate_terminal_response(self, response: dict[str, Any]) -> None:
        self.validate_envelope(response)
        if response.get("type") == "response" and response.get("ok") is True:
            operation = response.get("operation")
            if not isinstance(operation, str):
                raise OperatorContractError("protocol_error", "successful response has no operation")
            self.validate_success_data(operation, response.get("data"))

    def _validate(self, schema_id: str, value: Any) -> None:
        validator = self.validators.get(schema_id)
        if validator is None:
            raise OperatorContractError("protocol_error", f"missing registered schema: {schema_id}")
        errors = sorted(validator.iter_errors(value), key=lambda item: item.json_path)
        if errors:
            raise OperatorContractError("invalid_argument", errors[0].message)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result
