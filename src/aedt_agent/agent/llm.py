"""Lightweight LLM adapter — zero-dependency OpenAI-compatible client."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LlmConfig:
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 2048

    @classmethod
    def from_env(cls, prefix: str = "AEDT_AGENT_") -> "LlmConfig":
        return cls(
            model=os.getenv(f"{prefix}LLM_MODEL", os.getenv("LLM_MODEL", "gpt-4.1-mini")),
            api_key=os.getenv(f"{prefix}LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            base_url=os.getenv(f"{prefix}LLM_BASE_URL", os.getenv("OPENAI_BASE_URL", "")),
            temperature=float(os.getenv(f"{prefix}LLM_TEMPERATURE", "0.3")),
            max_tokens=int(os.getenv(f"{prefix}LLM_MAX_TOKENS", "2048")),
        )


def llm_complete(
    system: str,
    user: str,
    *,
    config: LlmConfig | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    """Call an OpenAI-compatible LLM and return the response text.

    Requires: pip install httpx (or just uses urllib as fallback).
    """
    if config is None:
        config = LlmConfig.from_env()

    if not config.api_key:
        raise RuntimeError(
            "LLM API key not set. Use env AEDT_AGENT_LLM_API_KEY or OPENAI_API_KEY."
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if response_format:
        body["response_format"] = response_format

    url = (config.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"

    max_retries = 3
    last_error = None
    for attempt in range(max_retries):
        try:
            return _http_post(url, body, config)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                import time as _time
                delay = 2 ** attempt  # 1s, 2s, 4s
                _time.sleep(delay)
                continue
    raise last_error  # type: ignore[misc]


def _http_post(url: str, body: dict, config: LlmConfig) -> str:
    """HTTP POST with httpx (preferred) or urllib fallback."""
    try:
        import httpx
        resp = httpx.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
    except ImportError:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def llm_complete_json(
    system: str,
    user: str,
    *,
    config: LlmConfig | None = None,
) -> dict[str, Any]:
    """Call LLM and parse response as JSON."""
    text = llm_complete(
        system,
        user,
        config=config,
        response_format={"type": "json_object"},
    )
    return json.loads(text)
