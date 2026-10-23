from __future__ import annotations

import io
import json
import os

import pytest

from aedt_agent.operator_api import OperatorSchemaCatalog
from aedt_agent.operator_bridge.protocol import (
    BridgeProtocolError,
    DedicatedPipeWriter,
    read_request_frame,
    response_ok,
    validate_terminal_response,
)


def test_dedicated_writer_emits_one_strict_ndjson_frame() -> None:
    read_fd, write_fd = os.pipe()
    try:
        DedicatedPipeWriter(write_fd).write({"type": "event", "data": {"status": "running"}})
        assert os.read(read_fd, 4096) == b'{"type":"event","data":{"status":"running"}}\n'
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_dedicated_writer_retries_partial_os_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    read_fd, write_fd = os.pipe()
    original_write = os.write
    try:
        def partial_write(fd: int, payload: bytes) -> int:
            return original_write(fd, payload[:3])

        monkeypatch.setattr(os, "write", partial_write)
        DedicatedPipeWriter(write_fd).write({"type": "event", "data": {"status": "running"}})
        assert os.read(read_fd, 4096) == b'{"type":"event","data":{"status":"running"}}\n'
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("frame", [b"native log\n", b"\n", b'{"x":1,"x":2}\n', b"{}"])
def test_reader_fails_closed_for_protocol_noise(frame: bytes) -> None:
    with pytest.raises(BridgeProtocolError):
        read_request_frame(io.BytesIO(frame))


def test_reader_rejects_an_oversized_unterminated_frame() -> None:
    with pytest.raises(BridgeProtocolError, match="maximum size"):
        read_request_frame(io.BytesIO(b"x" * (1_048_577)))


def test_terminal_response_is_validated_against_shared_schema() -> None:
    request = {
        "schema_version": "1.0",
        "type": "request",
        "request_id": "00000000-0000-4000-8000-000000000011",
        "trace_id": "00000000-0000-4000-8000-000000000012",
        "operation": "graph.status",
        "sent_at": "2026-07-26T08:00:00Z",
        "arguments": {"graph_run_id": "graph-001"},
    }
    response = response_ok(request, {"resource": None, "data": {
        "status": "running", "summary": "graph is running", "blocking_node_id": None,
        "reason": "graph_running", "recommended_action": "advance_or_wait", "nodes": [],
        "truncated_nodes": False,
    }})

    validate_terminal_response(OperatorSchemaCatalog.load(), response)
    assert json.loads(json.dumps(response))["request_id"] == request["request_id"]
