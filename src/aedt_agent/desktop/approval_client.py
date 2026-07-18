from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class DesktopApprovalClient:
    def __init__(self, base_url: str, session_key: str, *, timeout: float = 5.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("desktop approval URL must use loopback HTTP")
        if len(session_key) < 24:
            raise ValueError("desktop approval session key must contain at least 24 characters")
        self.base_url = base_url.rstrip("/")
        self.session_key = session_key
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> DesktopApprovalClient | None:
        url = os.environ.get("AEDT_AGENT_APPROVAL_URL", "").strip()
        key = os.environ.get("AEDT_AGENT_APPROVAL_KEY", "")
        return cls(url, key) if url and key else None

    def register(self, action: str, resource_id: str, digest: str, preview: dict[str, Any]) -> dict[str, Any]:
        return self._post(
            "/register",
            {"action": action, "resource_id": resource_id, "digest": digest, "preview": preview},
        )

    def poll(self, resource_id: str, *, timeout_seconds: float = 0) -> dict[str, Any]:
        return self._post("/poll", {"resource_id": resource_id, "timeout_seconds": timeout_seconds}, timeout=max(self.timeout, timeout_seconds + 2))

    def verify(self, action: str, resource_id: str, digest: str, token: str) -> bool:
        try:
            result = self._post(
                "/verify",
                {"action": action, "resource_id": resource_id, "digest": digest, "token": token},
            )
        except Exception:
            return False
        return result.get("approved") is True

    def _post(self, path: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json", "X-Ansys-Agent-Key": self.session_key},
            method="POST",
        )
        with urlopen(request, timeout=timeout or self.timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("desktop approval host returned a non-object response")
        return value
