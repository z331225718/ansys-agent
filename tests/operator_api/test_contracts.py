from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.operator_api import OperatorContractError, OperatorSchemaCatalog, decode_ndjson_frame


SCHEMA_ROOT = Path("schemas/operator/v1")


@pytest.fixture(scope="module")
def catalog() -> OperatorSchemaCatalog:
    return OperatorSchemaCatalog.load(SCHEMA_ROOT)


def _json_files(directory: str) -> list[Path]:
    return sorted((SCHEMA_ROOT / "fixtures" / directory).glob("*.json"))


@pytest.mark.parametrize("path", _json_files("valid"))
def test_valid_request_fixtures_are_accepted(catalog: OperatorSchemaCatalog, path: Path) -> None:
    catalog.validate_request(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", _json_files("invalid"))
def test_invalid_request_fixtures_are_rejected(catalog: OperatorSchemaCatalog, path: Path) -> None:
    with pytest.raises(OperatorContractError):
        catalog.validate_request(json.loads(path.read_text(encoding="utf-8")))


def test_schema_bundle_digest_is_stable() -> None:
    assert OperatorSchemaCatalog.load(SCHEMA_ROOT).bundle_digest == OperatorSchemaCatalog.load(SCHEMA_ROOT).bundle_digest


def test_committed_manifest_matches_schema_bundle(catalog: OperatorSchemaCatalog) -> None:
    manifest = json.loads((SCHEMA_ROOT / "bundle-manifest.json").read_text(encoding="utf-8"))

    assert manifest == catalog.manifest()


def test_valid_wire_fixture_is_accepted(catalog: OperatorSchemaCatalog) -> None:
    frame = (SCHEMA_ROOT / "fixtures/wire/system.handshake.ndjson").read_bytes()

    assert catalog.validate_wire_request(frame)["operation"] == "system.handshake"


def test_non_json_wire_fixture_is_fatal(catalog: OperatorSchemaCatalog) -> None:
    frame = (SCHEMA_ROOT / "fixtures/wire/non-json.ndjson").read_bytes()

    with pytest.raises(OperatorContractError, match="valid UTF-8 JSON") as error:
        catalog.validate_wire_request(frame)

    assert error.value.code == "protocol_error"


@pytest.mark.parametrize(
    "frame",
    [
        b'{"x":1,"x":2}\n',
        b'{"value":NaN}\n',
        b'\xff\n',
        b'\n',
        b'{}',
    ],
)
def test_wire_parser_fails_closed_for_ambiguous_or_invalid_frames(frame: bytes) -> None:
    with pytest.raises(OperatorContractError) as error:
        decode_ndjson_frame(frame)

    assert error.value.code == "protocol_error"


def test_response_requires_null_error_when_ok(catalog: OperatorSchemaCatalog) -> None:
    response = {
        "schema_version": "1.0",
        "type": "response",
        "request_id": "00000000-0000-4000-8000-000000000015",
        "trace_id": "00000000-0000-4000-8000-000000000016",
        "operation": "graph.status",
        "ok": True,
        "completed_at": "2026-07-26T08:00:00Z",
        "resource": None,
        "data": {"status": "running"},
        "error": None,
    }

    catalog.validate_envelope(response)
    response["error"] = {
        "code": "internal",
        "message": "must not be present on success",
        "retryable": False,
        "details": {},
    }
    with pytest.raises(OperatorContractError):
        catalog.validate_envelope(response)


def test_failed_response_requires_a_structured_error(catalog: OperatorSchemaCatalog) -> None:
    response = {
        "schema_version": "1.0",
        "type": "response",
        "request_id": "00000000-0000-4000-8000-000000000017",
        "trace_id": "00000000-0000-4000-8000-000000000018",
        "operation": "graph.status",
        "ok": False,
        "completed_at": "2026-07-26T08:00:00Z",
        "resource": None,
        "data": None,
        "error": None,
    }

    with pytest.raises(OperatorContractError):
        catalog.validate_envelope(response)


def test_success_response_data_is_checked_against_its_operation_contract(catalog: OperatorSchemaCatalog) -> None:
    response = {
        "schema_version": "1.0", "type": "response",
        "request_id": "00000000-0000-4000-8000-000000000018",
        "trace_id": "00000000-0000-4000-8000-000000000019",
        "operation": "system.handshake", "ok": True,
        "completed_at": "2026-07-26T08:00:00Z", "resource": None,
            "data": {
                "schema_version": "1.0", "schema_bundle_digest": "sha256:" + "0" * 64,
                "runtime_pid": 1,
                "runtime_argv_digest": "sha256:" + "0" * 64,
                "capabilities": ["system.handshake"],
            "limits": {"max_protocol_frame_bytes": 1, "max_tool_result_bytes": 1},
        },
        "error": None,
    }
    catalog.validate_terminal_response(response)
    response["data"]["unexpected"] = True
    with pytest.raises(OperatorContractError, match="invalid success data"):
        catalog.validate_terminal_response(response)
