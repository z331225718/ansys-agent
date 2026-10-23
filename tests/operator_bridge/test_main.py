from __future__ import annotations

import io
import json
import os
import sys
from types import SimpleNamespace

from aedt_agent.operator_api import OperatorContractError, OperatorSchemaCatalog
from aedt_agent.agent.runtime_factory import create_runtime_without_workers
from aedt_agent.operator_bridge import __main__ as bridge_main


def test_r3_reserved_operation_names_fail_at_the_wire_schema_boundary() -> None:
    catalog = OperatorSchemaCatalog.load()
    for operation in ("approval.submit", "graph.stop.commit"):
        request = {
            "schema_version": "1.0",
            "type": "request",
            "request_id": "00000000-0000-4000-8000-000000000015",
            "trace_id": "00000000-0000-4000-8000-000000000016",
            "operation": operation,
            "sent_at": "2026-07-26T08:00:00Z",
            "arguments": {},
        }
        try:
            catalog.validate_envelope(request)
        except OperatorContractError as error:
            assert error.code == "invalid_argument"
            assert "not valid under any" in str(error)
        else:
            raise AssertionError(f"R3 operation unexpectedly passed v1 envelope validation: {operation}")


def test_production_bridge_closes_fd3_without_a_response_for_r3_frame(tmp_path, monkeypatch) -> None:
    database = tmp_path / "mission.db"
    create_runtime_without_workers(database)
    read_fd, write_fd = os.pipe()
    request = {
        "schema_version": "1.0", "type": "request",
        "request_id": "00000000-0000-4000-8000-000000000015",
        "trace_id": "00000000-0000-4000-8000-000000000016",
        "operation": "approval.submit", "sent_at": "2026-07-26T08:00:00Z", "arguments": {},
    }
    monkeypatch.setattr(sys, "argv", ["operator-bridge", "--db", str(database), "--response-fd", str(write_fd)])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode("utf-8") + b"\n")))
    monkeypatch.setattr(bridge_main, "_prepare_process_stdio", lambda: None)
    try:
        assert bridge_main.main() == 2
        os.close(write_fd)
        assert os.read(read_fd, 1) == b""
    finally:
        os.close(read_fd)
