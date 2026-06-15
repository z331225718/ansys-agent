from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


HARNESS_PROTOCOL_VERSION = 1
JsonDict = dict[str, Any]


class HarnessProtocolError(ValueError):
    """Raised when a harness protocol payload is malformed or unsupported."""


class HarnessStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class HarnessError:
    error_class: str
    message: str
    retryable: bool
    details: JsonDict = field(default_factory=dict)

    @classmethod
    def from_json_dict(cls, payload: object) -> "HarnessError":
        data = _mapping(payload, "error")
        error_class = _required_string(data, "error_class")
        message = _required_string(data, "message")
        retryable = data.get("retryable")
        if not isinstance(retryable, bool):
            raise HarnessProtocolError("error.retryable must be boolean")
        details = _json_mapping(data.get("details", {}), "error.details")
        return cls(error_class, message, retryable, details)

    def to_json_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class HarnessRequest:
    protocol_version: int
    harness_run_id: str
    mission_id: str
    job_id: str
    attempt_id: str
    worker_id: str
    capability: str
    entrypoint: str
    timeout_seconds: int
    heartbeat_interval_seconds: int
    input_payload: JsonDict
    workspace: str

    @classmethod
    def create(
        cls,
        *,
        harness_run_id: str,
        mission_id: str,
        job_id: str,
        attempt_id: str,
        worker_id: str,
        capability: str,
        entrypoint: str,
        timeout_seconds: int,
        heartbeat_interval_seconds: int,
        input_payload: JsonDict,
        workspace: str,
    ) -> "HarnessRequest":
        return cls.from_json_dict(
            {
                "protocol_version": HARNESS_PROTOCOL_VERSION,
                "harness_run_id": harness_run_id,
                "mission_id": mission_id,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "capability": capability,
                "entrypoint": entrypoint,
                "timeout_seconds": timeout_seconds,
                "heartbeat_interval_seconds": heartbeat_interval_seconds,
                "input_payload": input_payload,
                "workspace": workspace,
            }
        )

    @classmethod
    def from_json_dict(cls, payload: object) -> "HarnessRequest":
        data = _mapping(payload, "harness request")
        _validate_protocol_version(data)
        return cls(
            protocol_version=HARNESS_PROTOCOL_VERSION,
            harness_run_id=_required_string(data, "harness_run_id"),
            mission_id=_required_string(data, "mission_id"),
            job_id=_required_string(data, "job_id"),
            attempt_id=_required_string(data, "attempt_id"),
            worker_id=_required_string(data, "worker_id"),
            capability=_required_string(data, "capability"),
            entrypoint=_required_string(data, "entrypoint"),
            timeout_seconds=_positive_int(data, "timeout_seconds"),
            heartbeat_interval_seconds=_positive_int(data, "heartbeat_interval_seconds"),
            input_payload=_json_mapping(data.get("input_payload", {}), "input_payload"),
            workspace=_required_string(data, "workspace"),
        )

    def to_json_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class HarnessResult:
    protocol_version: int
    harness_run_id: str
    job_id: str
    status: HarnessStatus
    output_payload: JsonDict
    artifact_refs: list[str]
    error: HarnessError | None
    started_at: str
    completed_at: str
    exit_code: int | None = None
    termination_reason: str = ""
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        harness_run_id: str,
        job_id: str,
        status: HarnessStatus,
        output_payload: JsonDict | None = None,
        artifact_refs: list[str] | None = None,
        error: HarnessError | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        exit_code: int | None = None,
        termination_reason: str = "",
        metadata: JsonDict | None = None,
    ) -> "HarnessResult":
        now = _utc_now()
        return cls(
            protocol_version=HARNESS_PROTOCOL_VERSION,
            harness_run_id=_non_empty(harness_run_id, "harness_run_id"),
            job_id=_non_empty(job_id, "job_id"),
            status=status,
            output_payload=dict(output_payload or {}),
            artifact_refs=[str(item) for item in (artifact_refs or [])],
            error=error,
            started_at=started_at or now,
            completed_at=completed_at or now,
            exit_code=exit_code,
            termination_reason=str(termination_reason),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_json_dict(cls, payload: object) -> "HarnessResult":
        data = _mapping(payload, "harness result")
        _validate_protocol_version(data)
        try:
            status = HarnessStatus(str(data.get("status") or ""))
        except ValueError as exc:
            raise HarnessProtocolError(f"unsupported harness result status: {data.get('status')}") from exc
        raw_error = data.get("error")
        exit_code = data.get("exit_code")
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise HarnessProtocolError("exit_code must be an integer or null")
        artifact_refs = data.get("artifact_refs", [])
        if not isinstance(artifact_refs, list) or not all(isinstance(item, str) for item in artifact_refs):
            raise HarnessProtocolError("artifact_refs must be a list of strings")
        return cls(
            protocol_version=HARNESS_PROTOCOL_VERSION,
            harness_run_id=_required_string(data, "harness_run_id"),
            job_id=_required_string(data, "job_id"),
            status=status,
            output_payload=_json_mapping(data.get("output_payload", {}), "output_payload"),
            artifact_refs=list(artifact_refs),
            error=None if raw_error is None else HarnessError.from_json_dict(raw_error),
            started_at=_required_string(data, "started_at"),
            completed_at=_required_string(data, "completed_at"),
            exit_code=exit_code,
            termination_reason=str(data.get("termination_reason") or ""),
            metadata=_json_mapping(data.get("metadata", {}), "metadata"),
        )

    def assert_identity(self, harness_run_id: str, job_id: str) -> None:
        if self.harness_run_id != harness_run_id:
            raise HarnessProtocolError(
                f"harness_run_id mismatch: expected {harness_run_id}, got {self.harness_run_id}"
            )
        if self.job_id != job_id:
            raise HarnessProtocolError(f"job_id mismatch: expected {job_id}, got {self.job_id}")

    def to_json_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["error"] = None if self.error is None else self.error.to_json_dict()
        return payload


def _validate_protocol_version(data: JsonDict) -> None:
    version = data.get("protocol_version")
    if version != HARNESS_PROTOCOL_VERSION:
        raise HarnessProtocolError(
            f"unsupported protocol_version: expected {HARNESS_PROTOCOL_VERSION}, got {version}"
        )


def _mapping(value: object, field_name: str) -> JsonDict:
    if not isinstance(value, dict):
        raise HarnessProtocolError(f"{field_name} must be a JSON object")
    return dict(value)


def _json_mapping(value: object, field_name: str) -> JsonDict:
    data = _mapping(value, field_name)
    _validate_json_value(data, field_name)
    return data


def _validate_json_value(value: object, field_name: str) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, field_name)
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise HarnessProtocolError(f"{field_name} keys must be strings")
        for item in value.values():
            _validate_json_value(item, field_name)
        return
    raise HarnessProtocolError(f"{field_name} must contain only JSON-compatible values")


def _required_string(data: JsonDict, key: str) -> str:
    return _non_empty(data.get(key), key)


def _non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessProtocolError(f"{field_name} is required")
    return value


def _positive_int(data: JsonDict, key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise HarnessProtocolError(f"{key} must be a positive integer")
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
