from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import stat
import threading
import time
from typing import Any

from aedt_agent.desktop.approval_host import ApprovalHost, ApprovalRecord, _preview_summary, _required


class LinuxApprovalStore:
    """A fail-closed approval store controlled only by a same-user Unix socket."""

    def __init__(self, *, ttl_seconds: int = 300, clock=time.time) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = threading.RLock()

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _required(payload, "action")
        resource_id = _required(payload, "resource_id")
        digest = _required(payload, "digest")
        preview = payload.get("preview")
        if not isinstance(preview, dict):
            raise ValueError("preview must be an object")
        with self._lock:
            current = self._records.get(resource_id)
            if current is not None:
                if current.action != action or current.digest != digest:
                    raise ValueError("approval resource was already registered with different content")
                return self._public(current, include_token=False)
            self._expire_all()
            if any(item.state in {"pending", "approved"} for item in self._records.values()):
                raise ValueError("another Linux approval is still active")
            now = self.clock()
            record = ApprovalRecord(action, resource_id, digest, preview, now, now + self.ttl_seconds)
            self._records[resource_id] = record
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

    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            self._expire_all()
            return [
                {
                    **self._public(item, include_token=False),
                    "preview": _preview_summary(item.preview),
                }
                for item in self._records.values()
                if item.state == "pending"
            ]

    def decide(self, resource_id: str, *, approved: bool) -> dict[str, Any]:
        with self._lock:
            record = self._record(resource_id)
            self._expire(record)
            if record.state != "pending":
                raise ValueError(f"approval is already {record.state}")
            record.state = "approved" if approved else "rejected"
            record.token = secrets.token_urlsafe(32) if approved else None
            record.decided_at = datetime.now(timezone.utc).isoformat()
            record.event.set()
            return self._public(record, include_token=False)

    def _record(self, resource_id: str) -> ApprovalRecord:
        try:
            return self._records[resource_id]
        except KeyError as exc:
            raise ValueError("unknown approval resource") from exc

    def _expire_all(self) -> None:
        for record in self._records.values():
            self._expire(record)

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


class UnixApprovalBroker:
    def __init__(self, socket_path: str | Path, store: LinuxApprovalStore) -> None:
        if os.name == "nt":
            raise RuntimeError("Linux approval broker requires a POSIX Unix socket")
        self.socket_path = Path(socket_path)
        self.store = store
        self._prepare_socket_path()
        broker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    line = self.rfile.readline(65537)
                    if len(line) > 65536:
                        raise ValueError("request is too large")
                    payload = json.loads(line.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("request must be an object")
                    result = broker._dispatch(payload)
                    response = {"ok": True, "result": result}
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    response = {"ok": False, "error": str(exc)}
                self.wfile.write(json.dumps(response, ensure_ascii=True).encode("utf-8") + b"\n")

        self.server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), Handler)
        os.chmod(self.socket_path, stat.S_IRUSR | stat.S_IWUSR)

    def serve_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.server.serve_forever, name="aedt-linux-approval", daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _prepare_socket_path(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        if not self.socket_path.exists() and not self.socket_path.is_symlink():
            return
        details = self.socket_path.lstat()
        if not stat.S_ISSOCK(details.st_mode) or details.st_uid != os.getuid():
            raise RuntimeError("refusing to replace a non-owned approval socket")
        self.socket_path.unlink()

    def _dispatch(self, payload: dict[str, Any]) -> Any:
        command = _required(payload, "command")
        if command == "list":
            return self.store.list_pending()
        if command == "approve":
            return self.store.decide(_required(payload, "resource_id"), approved=True)
        if command == "reject":
            return self.store.decide(_required(payload, "resource_id"), approved=False)
        raise ValueError("command must be list, approve, or reject")


def request(socket_path: str | Path, payload: dict[str, Any]) -> Any:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(5)
        stream.connect(str(socket_path))
        stream.sendall(json.dumps(payload, ensure_ascii=True).encode("utf-8") + b"\n")
        chunks: list[bytes] = []
        while True:
            chunk = stream.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    response = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError(str(response.get("error") if isinstance(response, dict) else "invalid response"))
    return response.get("result")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ansys-agent-linux-approval-host")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--ttl", type=int, default=300)
    args = parser.parse_args(argv)
    if os.name == "nt":
        parser.error("Linux approval host cannot run on Windows")
    key = os.environ.get("AEDT_AGENT_APPROVAL_KEY", "")
    store = LinuxApprovalStore(ttl_seconds=args.ttl)
    broker = UnixApprovalBroker(args.socket, store)
    broker.serve_in_thread()
    try:
        ApprovalHost("127.0.0.1", args.port, key, store=store).serve_forever()
    finally:
        broker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
