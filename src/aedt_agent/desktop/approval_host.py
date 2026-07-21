from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
import time
from typing import Any, Callable


@dataclass
class ApprovalRecord:
    action: str
    resource_id: str
    digest: str
    preview: dict[str, Any]
    created_at: float
    expires_at: float
    state: str = "pending"
    token: str | None = None
    used: bool = False
    decided_at: str | None = None
    event: threading.Event = field(default_factory=threading.Event, repr=False)


class DesktopApprovalStore:
    def __init__(
        self,
        *,
        prompt: Callable[[ApprovalRecord], bool] | None = None,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.prompt = prompt or windows_approval_prompt
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = threading.RLock()
        self._prompt_lock = threading.Lock()

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _required(payload, "action")
        resource_id = _required(payload, "resource_id")
        digest = _required(payload, "digest")
        preview = payload.get("preview")
        if not isinstance(preview, dict):
            raise ValueError("preview must be an object")
        with self._lock:
            existing = self._records.get(resource_id)
            if existing is not None:
                if existing.action != action or existing.digest != digest:
                    raise ValueError("approval resource was already registered with different content")
                return self._public(existing, include_token=False)
            now = self.clock()
            for record in self._records.values():
                self._expire(record)
            if any(record.state in {"pending", "approved"} for record in self._records.values()):
                raise ValueError("another Desktop approval is still active")
            record = ApprovalRecord(action, resource_id, digest, preview, now, now + self.ttl_seconds)
            self._records[resource_id] = record
        threading.Thread(target=self._decide, args=(record,), name="aedt-approval-dialog", daemon=True).start()
        return self._public(record, include_token=False)

    def poll(self, resource_id: str, *, timeout_seconds: float = 0) -> dict[str, Any]:
        if not 0 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be from 0 to 120")
        record = self._record(resource_id)
        if timeout_seconds and record.state == "pending":
            record.event.wait(timeout_seconds)
        with self._lock:
            self._expire(record)
            return self._public(record, include_token=True)

    def verify(self, payload: dict[str, Any]) -> bool:
        action = _required(payload, "action")
        resource_id = _required(payload, "resource_id")
        digest = _required(payload, "digest")
        token = _required(payload, "token")
        with self._lock:
            record = self._record(resource_id)
            self._expire(record)
            valid = (
                record.state == "approved"
                and not record.used
                and record.action == action
                and record.digest == digest
                and record.token is not None
                and secrets.compare_digest(record.token, token)
            )
            if valid:
                record.used = True
                record.state = "used"
                record.event.set()
            return valid

    def _decide(self, record: ApprovalRecord) -> None:
        try:
            # Keep native dialogs ordered so concurrent previews cannot cover one another.
            with self._prompt_lock:
                approved = bool(self.prompt(record))
        except Exception:
            approved = False
        with self._lock:
            if record.state != "pending":
                return
            if self.clock() >= record.expires_at:
                record.state = "expired"
            elif approved:
                record.state = "approved"
                record.token = secrets.token_urlsafe(32)
            else:
                record.state = "rejected"
            record.decided_at = datetime.now(timezone.utc).isoformat()
            record.event.set()

    def _record(self, resource_id: str) -> ApprovalRecord:
        with self._lock:
            try:
                return self._records[resource_id]
            except KeyError as exc:
                raise ValueError("unknown approval resource") from exc

    def _expire(self, record: ApprovalRecord) -> None:
        if record.state in {"pending", "approved"} and self.clock() >= record.expires_at:
            record.state = "expired"
            record.token = None
            record.event.set()

    @staticmethod
    def _public(record: ApprovalRecord, *, include_token: bool) -> dict[str, Any]:
        result = {
            "action": record.action,
            "resource_id": record.resource_id,
            "digest": record.digest,
            "status": record.state,
            "decided_at": record.decided_at,
        }
        if include_token and record.state == "approved":
            result["approval_token"] = record.token
        return result


class ApprovalHost:
    def __init__(self, host: str, port: int, session_key: str, store: DesktopApprovalStore | None = None) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("approval host must bind to loopback")
        if len(session_key) < 24:
            raise ValueError("approval session key must contain at least 24 characters")
        self.session_key = session_key
        self.store = store or DesktopApprovalStore()
        approval_host = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not self._authorized():
                    return
                if self.path != "/health":
                    self._json(404, {"error": "not_found"})
                    return
                self._json(200, {"status": "ok"})

            def do_POST(self):
                if not self._authorized():
                    return
                try:
                    payload = self._payload()
                    if self.path == "/register":
                        result = approval_host.store.register(payload)
                    elif self.path == "/poll":
                        result = approval_host.store.poll(
                            _required(payload, "resource_id"),
                            timeout_seconds=float(payload.get("timeout_seconds", 0)),
                        )
                    elif self.path == "/verify":
                        result = {"approved": approval_host.store.verify(payload)}
                    elif self.path == "/shutdown":
                        result = {"shutting_down": True}
                        threading.Thread(target=approval_host.server.shutdown, daemon=True).start()
                    else:
                        self._json(404, {"error": "not_found"})
                        return
                    self._json(200, result)
                except (TypeError, ValueError) as exc:
                    self._json(400, {"error": "invalid_request", "message": str(exc)})

            def log_message(self, format, *args):
                return

            def _authorized(self) -> bool:
                supplied = self.headers.get("X-Ansys-Agent-Key", "")
                if not secrets.compare_digest(supplied, approval_host.session_key):
                    self._json(403, {"error": "forbidden"})
                    return False
                return True

            def _payload(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1024 * 1024:
                    raise ValueError("request is too large")
                value = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                if not isinstance(value, dict):
                    raise ValueError("request body must be an object")
                return value

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer((host, port), Handler)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def serve_forever(self) -> None:
        self.server.serve_forever(poll_interval=0.2)
        self.server.server_close()


def windows_approval_prompt(record: ApprovalRecord) -> bool:
    import ctypes

    details = _preview_summary(record.preview)
    message = (
        "Claude Code requested an Ansys operation.\n\n"
        f"Action: {record.action}\n"
        f"Resource: {record.resource_id}\n"
        f"Digest: {record.digest[:16]}...\n\n"
        f"Preview:\n{details}\n\n"
        "Approve this one operation?"
    )
    flags = 0x00000004 | 0x00000030 | 0x00040000 | 0x00010000
    return ctypes.windll.user32.MessageBoxW(0, message, "Ansys Agent Approval", flags) == 6


def _preview_summary(preview: dict[str, Any]) -> str:
    omitted = {"approval_request", "approval_poll", "release_required", "approval_source"}
    value = {key: item for key, item in preview.items() if key not in omitted}
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return text[:5000] + ("\n..." if len(text) > 5000 else "")


def _required(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aedt-agent-approval-host")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    key = os.environ.get("AEDT_AGENT_APPROVAL_KEY", "")
    host = ApprovalHost(args.host, args.port, key)
    # In Git Bash, $BASHPID is an MSYS process id rather than a Windows process id.
    # The child Python process can obtain its real Windows parent id directly.
    _stop_when_parent_exits(host, args.parent_pid or os.getppid())
    host.serve_forever()
    return 0


def _stop_when_parent_exits(host: ApprovalHost, parent_pid: int) -> None:
    """Avoid a loopback approval service surviving a forcibly closed terminal."""

    def monitor() -> None:
        while _process_is_alive(parent_pid):
            time.sleep(1)
        host.server.shutdown()

    threading.Thread(target=monitor, name="aedt-approval-parent-watch", daemon=True).start()


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
                exit_code.value == 259
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
