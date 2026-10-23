from __future__ import annotations

import os
from pathlib import Path

import pytest

from aedt_agent.agent.runtime_factory import create_runtime_without_workers
from aedt_agent.operator_api import OperatorSchemaCatalog, OperatorService
from aedt_agent.operator_bridge.dispatcher import BridgeDispatcher
from aedt_agent.operator_bridge.protocol import validate_terminal_response


def _request(operation: str, arguments: dict) -> dict:
    return {
        "schema_version": "1.0",
        "type": "request",
        "request_id": "00000000-0000-4000-8000-000000000013",
        "trace_id": "00000000-0000-4000-8000-000000000014",
        "operation": operation,
        "sent_at": "2026-07-26T08:00:00Z",
        "arguments": arguments,
    }


def _dispatcher(tmp_path: Path) -> tuple[BridgeDispatcher, OperatorSchemaCatalog]:
    catalog = OperatorSchemaCatalog.load()
    runtime = create_runtime_without_workers(tmp_path / "mission.db")
    return BridgeDispatcher(OperatorService(runtime, schema_catalog=catalog), catalog), catalog


def test_dispatcher_routes_handshake_through_explicit_read_only_registry(tmp_path: Path) -> None:
    dispatcher, catalog = _dispatcher(tmp_path)
    response = dispatcher.dispatch(
        _request("system.handshake", {"client": {"name": "test", "version": "0", "schema_version": "1.0"}})
    )

    validate_terminal_response(catalog, response)
    assert response["ok"] is True
    assert response["data"]["schema_bundle_digest"] == catalog.bundle_digest
    assert response["data"]["runtime_pid"] == os.getpid()
    assert "mission.start" not in response["data"]["capabilities"]


def test_dispatcher_turns_invalid_arguments_into_a_terminal_error_response(tmp_path: Path) -> None:
    dispatcher, catalog = _dispatcher(tmp_path)
    response = dispatcher.dispatch(_request("graph.status", {}))

    validate_terminal_response(catalog, response)
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        (
            "mission.start",
            {
                "preflight_snapshot_id": "sha256:" + "0" * 64,
                "initial_payload": {},
                "write_guard": {
                    "idempotency_key": "write-guard-key-0001",
                    "expected_event_cursor": "0",
                    "expected_state_digest": "sha256:" + "0" * 64,
                    "reason": "must remain disabled in shadow mode",
                },
            },
        ),
        (
            "graph.advance",
            {
                "graph_run_id": "recorded-graph-001",
                "write_guard": {
                    "idempotency_key": "write-guard-key-0002",
                    "expected_event_cursor": "0",
                    "expected_state_digest": "sha256:" + "0" * 64,
                    "reason": "must remain disabled in shadow mode",
                },
            },
        ),
        (
            "intervention.apply",
            {
                "graph_run_id": "recorded-graph-001",
                "intervention": {"kind": "retry"},
                "write_guard": {
                    "idempotency_key": "write-guard-key-0003",
                    "expected_event_cursor": "0",
                    "expected_state_digest": "sha256:" + "0" * 64,
                    "reason": "must remain disabled in shadow mode",
                },
            },
        ),
        (
            "graph.stop.prepare",
            {
                "graph_run_id": "recorded-graph-001",
                "reason": "must remain disabled in shadow mode",
                "write_guard": {
                    "idempotency_key": "write-guard-key-0004",
                    "expected_event_cursor": "0",
                    "expected_state_digest": "sha256:" + "0" * 64,
                    "reason": "must remain disabled in shadow mode",
                },
            },
        ),
    ],
)
def test_dispatcher_rejects_every_well_formed_write_request_in_read_only_mode(
    tmp_path: Path, operation: str, arguments: dict
) -> None:
    dispatcher, catalog = _dispatcher(tmp_path)
    response = dispatcher.dispatch(
        _request(
            operation,
            arguments,
        )
    )

    validate_terminal_response(catalog, response)
    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported_operation"
