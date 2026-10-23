from __future__ import annotations

import io
import json
import os
from datetime import UTC, datetime
from typing import Any, BinaryIO

from aedt_agent.operator_api.contracts import MAX_PROTOCOL_FRAME_BYTES, OperatorContractError, OperatorSchemaCatalog, decode_ndjson_frame


class BridgeProtocolError(ValueError):
    pass


class DedicatedPipeWriter:
    def __init__(self, fd: int):
        self.fd = fd

    def write(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        offset = 0
        while offset < len(payload):
            written = os.write(self.fd, payload[offset:])
            if written <= 0:
                raise BridgeProtocolError("dedicated response pipe closed during write")
            offset += written


def read_request_frame(stream: BinaryIO) -> dict[str, Any] | None:
    # readline(size) prevents a peer without a newline from forcing unbounded allocation.
    frame = stream.readline(MAX_PROTOCOL_FRAME_BYTES + 1)
    if frame == b"":
        return None
    if len(frame) > MAX_PROTOCOL_FRAME_BYTES:
        raise BridgeProtocolError("protocol frame exceeds the maximum size")
    try:
        return decode_ndjson_frame(frame)
    except OperatorContractError as error:
        raise BridgeProtocolError(str(error)) from error


def response_ok(request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return _response_base(request, ok=True, resource=result.get("resource"), data=result.get("data"), error=None)


def response_error(request: dict[str, Any], code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return _response_base(request, ok=False, resource=None, data=None, error={"code": code, "message": message[:1000], "retryable": retryable, "details": {}})


def validate_terminal_response(catalog: OperatorSchemaCatalog, response: dict[str, Any]) -> None:
    catalog.validate_terminal_response(response)


def _response_base(request: dict[str, Any], *, ok: bool, resource: Any, data: Any, error: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "type": "response", "request_id": request.get("request_id", ""),
        "trace_id": request.get("trace_id", ""), "operation": request.get("operation", ""), "ok": ok,
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "resource": resource, "data": data, "error": error,
    }
