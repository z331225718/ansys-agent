from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Callable


class ApprovalTokenError(ValueError):
    pass


class HmacApprovalAuthority:
    """Issue and verify short-lived, one-use approvals outside the MCP tool surface."""

    def __init__(self, secret: str | bytes, *, clock: Callable[[], float] = time.time) -> None:
        encoded = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(encoded) < 32:
            raise ApprovalTokenError("approval secret must be at least 32 bytes")
        self._secret = encoded
        self._clock = clock
        self._consumed: set[str] = set()

    @classmethod
    def from_environment(cls, name: str = "AEDT_AGENT_APPROVAL_SECRET") -> HmacApprovalAuthority | None:
        secret = os.getenv(name)
        return None if not secret else cls(secret)

    def issue(self, *, action: str, resource_id: str, digest: str, ttl_seconds: int = 300) -> str:
        if not action or not resource_id or not digest:
            raise ApprovalTokenError("action, resource_id, and digest are required")
        if ttl_seconds < 1 or ttl_seconds > 3600:
            raise ApprovalTokenError("approval ttl must be between 1 and 3600 seconds")
        claims = {
            "v": 1,
            "action": action,
            "resource_id": resource_id,
            "digest": digest,
            "exp": int(self._clock()) + ttl_seconds,
            "nonce": secrets.token_urlsafe(18),
        }
        payload = _encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = _encode(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}"

    def verify(self, action: str, resource_id: str, digest: str, token: str) -> bool:
        try:
            payload, signature = token.split(".", 1)
            expected = _encode(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return False
            claims: dict[str, Any] = json.loads(_decode(payload))
            nonce = str(claims["nonce"])
            if nonce in self._consumed:
                return False
            if int(claims["v"]) != 1 or int(claims["exp"]) < int(self._clock()):
                return False
            if claims["action"] != action or claims["resource_id"] != resource_id or claims["digest"] != digest:
                return False
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        self._consumed.add(nonce)
        return True


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
