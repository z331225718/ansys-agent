from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any
import uuid

from aedt_agent.live.target import AedtTarget, TargetValidationError


class ProtocolError(ValueError):
    pass


def _json_value(value: Any, name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{name} must be JSON-compatible") from exc


def _exact(value: object, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{name} must be an object")
    if set(value) != fields:
        raise ProtocolError(f"{name} fields must be exactly {sorted(fields)}")
    return value


@dataclass(frozen=True)
class WorkerRequest:
    request_id: str
    command: str
    target: AedtTarget
    arguments: dict[str, Any]
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id or not isinstance(self.command, str) or not self.command:
            raise ProtocolError("request_id and command must be non-empty")
        if not isinstance(self.arguments, dict):
            raise ProtocolError("arguments must be an object")
        if type(self.timeout_seconds) not in {int, float} or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ProtocolError("timeout_seconds must be finite and positive")
        _json_value(self.arguments, "arguments")

    @classmethod
    def create(
        cls, command: str, target: AedtTarget, arguments: dict[str, Any], timeout_seconds: float
    ) -> "WorkerRequest":
        return cls(str(uuid.uuid4()), command, target, arguments, float(timeout_seconds))

    def to_json(self) -> str:
        return json.dumps(
            {
                "request_id": self.request_id,
                "command": self.command,
                "target": self.target.to_dict(),
                "arguments": self.arguments,
                "timeout_seconds": self.timeout_seconds,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> "WorkerRequest":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError("invalid request JSON") from exc
        data = _exact(data, {"request_id", "command", "target", "arguments", "timeout_seconds"}, "request")
        try:
            target = AedtTarget.from_dict(data["target"])
        except TargetValidationError as exc:
            raise ProtocolError(str(exc)) from exc
        timeout = data["timeout_seconds"]
        if type(timeout) not in {int, float}:
            raise ProtocolError("timeout_seconds must be numeric")
        return cls(data["request_id"], data["command"], target, data["arguments"], float(timeout))


@dataclass(frozen=True)
class WorkerResponse:
    request_id: str
    ok: bool
    result: Any
    error: dict[str, Any] | None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id or type(self.ok) is not bool:
            raise ProtocolError("invalid response identity")
        if self.ok and self.error is not None:
            raise ProtocolError("successful response cannot contain error")
        if not self.ok:
            if self.result is not None or not isinstance(self.error, dict):
                raise ProtocolError("failed response requires error and null result")
            if not set(self.error).issubset({"code", "message", "detail"}) or not {"code", "message"}.issubset(self.error):
                raise ProtocolError("error fields are invalid")
            if not isinstance(self.error.get("code"), str) or not isinstance(self.error.get("message"), str):
                raise ProtocolError("error requires code and message")
        _json_value(self.result if self.ok else self.error, "response")

    @classmethod
    def success(cls, request_id: str, result: Any) -> "WorkerResponse":
        return cls(request_id, True, result, None)

    @classmethod
    def failure(cls, request_id: str, code: str, message: str) -> "WorkerResponse":
        return cls(request_id, False, None, {"code": code, "message": message})

    def to_json(self) -> str:
        return json.dumps(
            {"request_id": self.request_id, "ok": self.ok, "result": self.result, "error": self.error},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> "WorkerResponse":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError("invalid response JSON") from exc
        data = _exact(data, {"request_id", "ok", "result", "error"}, "response")
        return cls(data["request_id"], data["ok"], data["result"], data["error"])
