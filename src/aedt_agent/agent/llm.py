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
    def from_env(
        cls,
        prefix: str = "AEDT_AGENT_",
        *,
        profile: str = "",
    ) -> "LlmConfig":
        profile_key = _profile_env_key(profile)
        profile_prefixes = (
            (f"{prefix}LLM_{profile_key}_", f"{prefix}{profile_key}_LLM_")
            if profile_key
            else ()
        )
        explicit = {
            "model": _env_any(profile_prefixes, "MODEL", f"{prefix}LLM_MODEL", "LLM_MODEL"),
            "api_key": _env_any(profile_prefixes, "API_KEY", f"{prefix}LLM_API_KEY", "OPENAI_API_KEY"),
            "base_url": _env_any(profile_prefixes, "BASE_URL", f"{prefix}LLM_BASE_URL", "OPENAI_BASE_URL"),
        }
        cfg = cls(
            model=_env_first(
                profile_prefixes,
                "MODEL",
                os.getenv(f"{prefix}LLM_MODEL", os.getenv("LLM_MODEL", "gpt-4.1-mini")),
            ),
            api_key=_env_first(
                profile_prefixes,
                "API_KEY",
                os.getenv(f"{prefix}LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            ),
            base_url=_env_first(
                profile_prefixes,
                "BASE_URL",
                os.getenv(f"{prefix}LLM_BASE_URL", os.getenv("OPENAI_BASE_URL", "")),
            ),
            temperature=float(
                _env_first(
                    profile_prefixes,
                    "TEMPERATURE",
                    os.getenv(f"{prefix}LLM_TEMPERATURE", "0.3"),
                )
            ),
            max_tokens=int(
                _env_first(
                    profile_prefixes,
                    "MAX_TOKENS",
                    os.getenv(f"{prefix}LLM_MAX_TOKENS", "2048"),
                )
            ),
        )
        # Merge web-saved config (web takes precedence over env defaults, env vars still win)
        if not cfg.api_key or not cfg.base_url:
            try:
                from pathlib import Path as _Path
                import json as _json
                web_cfg_path = _Path(".aedt-agent/llm-config.json")
                if web_cfg_path.exists():
                    saved = _json.loads(web_cfg_path.read_text(encoding="utf-8"))
                    cfg = cls(
                        model=(
                            cfg.model
                            if explicit["model"]
                            else str(saved.get("model") or cfg.model)
                        ),
                        api_key=(
                            cfg.api_key
                            if explicit["api_key"]
                            else cfg.api_key or str(saved.get("api_key") or "")
                        ),
                        base_url=(
                            cfg.base_url
                            if explicit["base_url"]
                            else cfg.base_url or str(saved.get("base_url") or "")
                        ),
                        temperature=cfg.temperature,
                        max_tokens=cfg.max_tokens,
                    )
            except Exception:
                pass
        return cfg


def _profile_env_key(profile: str) -> str:
    key = "".join(
        char.upper() if char.isalnum() else "_"
        for char in str(profile or "").strip()
    )
    return "_".join(part for part in key.split("_") if part)


def _env_first(
    prefixes: tuple[str, ...],
    suffix: str,
    default: str,
) -> str:
    for profile_prefix in prefixes:
        value = os.getenv(f"{profile_prefix}{suffix}")
        if value not in {None, ""}:
            return str(value)
    return default


def _env_any(
    prefixes: tuple[str, ...],
    suffix: str,
    *names: str,
) -> bool:
    profile_names = [f"{profile_prefix}{suffix}" for profile_prefix in prefixes]
    return any(os.getenv(name) not in {None, ""} for name in (*profile_names, *names))


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

    # Transient server errors and gateway hiccups are common during long
    # optimization loops; keep the retry budget bounded but less brittle.
    max_retries = 6
    base_backoff = 2.0
    last_error = None
    for attempt in range(max_retries):
        try:
            return _http_post(url, body, config)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1 and _is_retryable(e):
                import time as _time
                _time.sleep(base_backoff * (2 ** attempt))
                continue
            raise
    raise last_error  # type: ignore[misc]


def _is_retryable(exc: Exception) -> bool:
    """Return True for transient network/timeout errors only."""
    try:
        import httpx
        if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)):
            return True
    except ImportError:
        pass
    import urllib.error
    if isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError, OSError)):
        return True
    # 429, 502, 503, 504 are transient; httpx raises HTTPStatusError
    try:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            return code in (429, 502, 503, 504)
    except ImportError:
        pass
    return False


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
