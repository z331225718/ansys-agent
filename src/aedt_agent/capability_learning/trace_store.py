from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any
from uuid import uuid4


class TraceStateError(RuntimeError):
    pass


_TRANSITIONS = {
    "proposed": {"validated", "rejected", "failed"},
    "validated": {"previewed", "rejected", "failed"},
    "previewed": {"approved", "applied", "rejected", "expired", "failed"},
    "approved": {"applied", "expired", "failed"},
    "applied": {"verified", "rolled_back", "rollback_failed", "failed"},
}
_TERMINAL = {"verified", "rolled_back", "rollback_failed", "failed", "rejected", "expired"}
_SENSITIVE_KEY = re.compile(
    r"(approval.*token|token|secret|api[_-]?key|authorization|password|credential|session[_-]?key|^env$)",
    re.I,
)
_SENSITIVE_VALUE = re.compile(r"(?:sk-[A-Za-z0-9._-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,})", re.I)


class CapabilityTraceStore:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        signing_key: bytes | None = None,
        signing_key_file: str | Path | None = None,
    ) -> None:
        explicit_root = root is not None
        configured = root or os.environ.get("AEDT_AGENT_TRACE_ROOT") or Path.cwd() / ".aedt-agent" / "capability-traces"
        self.root = Path(configured).resolve()
        self._signing_key, self.signing_key_path = _resolve_signing_key(
            self.root,
            explicit_root=explicit_root,
            signing_key=signing_key,
            signing_key_file=signing_key_file,
        )
        self._signing_key_id = hashlib.sha256(self._signing_key).hexdigest()[:16]
        self._lock = threading.RLock()

    def create(
        self,
        *,
        candidate_id: str,
        intent: str,
        plan: dict[str, Any],
        environment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace_id = "trace-" + uuid4().hex
        trace_dir = self.root / trace_id
        with self._lock:
            trace_dir.mkdir(parents=True, exist_ok=False)
            header = {
                "schema_version": 1,
                "trace_id": trace_id,
                "candidate_id": candidate_id,
                "created_at": _utc_now(),
                "intent": intent,
                "plan": _redact(plan),
                "environment": _redact(environment or {}),
            }
            _atomic_json(trace_dir / "header.json", header)
            self._append_event(
                trace_dir,
                1,
                "proposed",
                "candidate_proposed",
                {"candidate_id": candidate_id},
                previous_event_digest=None,
            )
        return self.get(trace_id)

    def transition(
        self,
        trace_id: str,
        state: str,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            trace_dir = self._trace_dir(trace_id)
            current = self._events(trace_dir)
            previous = current[-1]["state"]
            if previous in _TERMINAL:
                raise TraceStateError(f"sealed trace cannot transition from {previous}")
            if state not in _TRANSITIONS.get(previous, set()):
                raise TraceStateError(f"invalid trace transition: {previous} -> {state}")
            self._append_event(
                trace_dir,
                len(current) + 1,
                state,
                event,
                payload or {},
                previous_event_digest=current[-1]["event_digest"],
            )
            if state in _TERMINAL:
                self._seal(trace_dir)
        return self.get(trace_id)

    def get(self, trace_id: str) -> dict[str, Any]:
        with self._lock:
            trace_dir = self._trace_dir(trace_id)
            sealed = trace_dir / "trace.json"
            if sealed.is_file():
                value = json.loads(sealed.read_text(encoding="utf-8"))
                self._verify_seal(trace_dir, value)
                return value
            value = self._snapshot(trace_dir, sealed=False)
            if value["state"] in _TERMINAL:
                self._seal(trace_dir)
                value = json.loads(sealed.read_text(encoding="utf-8"))
                self._verify_seal(trace_dir, value)
            return value

    def list(self, *, limit: int = 50) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 to 200")
        if not self.root.is_dir():
            return {"traces": [], "count": 0}
        traces = []
        for path in sorted(self.root.glob("trace-*"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
            try:
                value = self.get(path.name)
            except (OSError, ValueError, TraceStateError):
                continue
            traces.append(
                {
                    "trace_id": value["trace_id"],
                    "candidate_id": value["candidate_id"],
                    "state": value["state"],
                    "sealed": value["sealed"],
                    "created_at": value["created_at"],
                    "seal_digest": value.get("seal_digest"),
                }
            )
        return {"traces": traces, "count": len(traces)}

    def export(self, trace_id: str) -> dict[str, Any]:
        value = self.get(trace_id)
        return json.loads(json.dumps(_redact(value), ensure_ascii=True))

    def _seal(self, trace_dir: Path) -> None:
        snapshot = self._snapshot(trace_dir, sealed=True)
        event_bytes = (trace_dir / "events.jsonl").read_bytes()
        snapshot["event_log_digest"] = hashlib.sha256(event_bytes).hexdigest()
        snapshot["authentication"] = {
            "scheme": "hmac-sha256",
            "key_id": self._signing_key_id,
        }
        snapshot["seal_digest"] = _digest(_seal_content(snapshot))
        snapshot["seal_hmac"] = _hmac_digest(
            self._signing_key,
            {key: value for key, value in snapshot.items() if key != "seal_hmac"},
        )
        _atomic_json(
            trace_dir / "manifest.json",
            {
                "schema_version": 2,
                "trace_id": snapshot["trace_id"],
                "state": snapshot["state"],
                "event_log_digest": snapshot["event_log_digest"],
                "seal_digest": snapshot["seal_digest"],
                "authentication": snapshot["authentication"],
                "seal_hmac": snapshot["seal_hmac"],
                "sealed_at": _utc_now(),
            },
        )
        _atomic_json(trace_dir / "trace.json", snapshot)

    def _verify_seal(self, trace_dir: Path, snapshot: dict[str, Any]) -> None:
        event_log_digest = hashlib.sha256((trace_dir / "events.jsonl").read_bytes()).hexdigest()
        if not secrets.compare_digest(str(snapshot.get("event_log_digest", "")), event_log_digest):
            raise TraceStateError("sealed trace event log digest is invalid")
        authentication = snapshot.get("authentication")
        if authentication != {"scheme": "hmac-sha256", "key_id": self._signing_key_id}:
            raise TraceStateError("sealed trace authentication metadata is invalid")
        expected = _digest(_seal_content(snapshot))
        if not secrets.compare_digest(str(snapshot.get("seal_digest", "")), expected):
            raise TraceStateError("sealed trace digest is invalid")
        expected_hmac = _hmac_digest(
            self._signing_key,
            {key: value for key, value in snapshot.items() if key != "seal_hmac"},
        )
        if not secrets.compare_digest(str(snapshot.get("seal_hmac", "")), expected_hmac):
            raise TraceStateError("sealed trace server authentication is invalid")
        manifest = json.loads((trace_dir / "manifest.json").read_text(encoding="utf-8"))
        for key in ("trace_id", "state", "event_log_digest", "seal_digest", "authentication", "seal_hmac"):
            if manifest.get(key) != snapshot.get(key):
                raise TraceStateError(f"sealed trace manifest mismatch: {key}")

    def _snapshot(self, trace_dir: Path, *, sealed: bool) -> dict[str, Any]:
        header = json.loads((trace_dir / "header.json").read_text(encoding="utf-8"))
        events = self._events(trace_dir)
        return {
            **header,
            "state": events[-1]["state"],
            "sealed": sealed,
            "events": events,
        }

    def _append_event(
        self,
        trace_dir: Path,
        sequence: int,
        state: str,
        event: str,
        payload: dict[str, Any],
        *,
        previous_event_digest: str | None,
    ) -> None:
        record = {
            "sequence": sequence,
            "timestamp": _utc_now(),
            "state": state,
            "event": event,
            "payload": _redact(payload),
            "previous_event_digest": previous_event_digest,
        }
        record["event_digest"] = _digest(record)
        line = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        with (trace_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

    def _events(self, trace_dir: Path) -> list[dict[str, Any]]:
        events = []
        with (trace_dir / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    events.append(json.loads(line))
        if not events or [item["sequence"] for item in events] != list(range(1, len(events) + 1)):
            raise TraceStateError("trace event sequence is invalid")
        previous_digest = None
        for item in events:
            if item.get("previous_event_digest") != previous_digest:
                raise TraceStateError("trace event chain is invalid")
            event_digest = item.get("event_digest")
            expected = _digest({key: value for key, value in item.items() if key != "event_digest"})
            if not isinstance(event_digest, str) or not secrets.compare_digest(event_digest, expected):
                raise TraceStateError("trace event digest is invalid")
            previous_digest = event_digest
        return events

    def _trace_dir(self, trace_id: str) -> Path:
        if not isinstance(trace_id, str) or not re.fullmatch(r"trace-[0-9a-f]{32}", trace_id):
            raise ValueError("invalid trace id")
        path = (self.root / trace_id).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("trace path escaped the trace root") from exc
        if not path.is_dir():
            raise KeyError(f"unknown capability trace: {trace_id}")
        return path


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            text = str(key)
            result[text] = "[REDACTED]" if _SENSITIVE_KEY.search(text) else _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub("[REDACTED]", value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hmac_digest(key: bytes, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def _seal_content(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key not in {"seal_digest", "seal_hmac"}}


def _resolve_signing_key(
    root: Path,
    *,
    explicit_root: bool,
    signing_key: bytes | None,
    signing_key_file: str | Path | None,
) -> tuple[bytes, Path | None]:
    if signing_key is not None:
        if len(signing_key) < 32:
            raise ValueError("trace signing key must be at least 32 bytes")
        return bytes(signing_key), None

    environment_key = os.environ.get("AEDT_AGENT_TRACE_SIGNING_KEY")
    if environment_key:
        encoded = environment_key.encode("utf-8")
        if len(encoded) < 32:
            raise ValueError("AEDT_AGENT_TRACE_SIGNING_KEY must be at least 32 bytes")
        return encoded, None

    if signing_key_file is not None:
        key_path = Path(signing_key_file).expanduser().resolve()
    elif explicit_root:
        key_path = (root.parent / ".capability-trace-signing-key").resolve()
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
        key_path = (base / "AnsysAgent" / "secrets" / "capability-trace-signing-key").resolve()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        encoded_key = key_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        encoded_key = secrets.token_hex(32)
        try:
            descriptor = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            encoded_key = key_path.read_text(encoding="ascii").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
                stream.write(encoded_key + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
    try:
        key = bytes.fromhex(encoded_key)
    except ValueError as exc:
        raise ValueError("trace signing key file is invalid") from exc
    if len(key) < 32:
        raise ValueError("trace signing key file must contain at least 32 bytes")
    return key, key_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
