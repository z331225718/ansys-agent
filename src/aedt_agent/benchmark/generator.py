from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Protocol
from urllib import error, request


class CodeGenerator(Protocol):
    def generate(self, context: str, filename: str | None = None) -> str: ...


class DefaultCodeGenerator:
    def generate(self, context: str, filename: str | None = None) -> str:
        raise NotImplementedError("No default generator backend configured")


class FileGenerator:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def generate(self, context: str, filename: str | None = None) -> str:
        if not filename:
            raise ValueError("filename is required for FileGenerator")
        path = self.base_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        for group in ("_A_attempt_", "_B_attempt_"):
            if group in filename:
                fallback = self.base_dir / f"{filename.split(group, 1)[0]}.py"
                if fallback.exists():
                    return fallback.read_text(encoding="utf-8")
        return path.read_text(encoding="utf-8")


class OpenAIGenerator:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
        temperature: float = 0.0,
        max_retries: int = 2,
        retry_delay: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def generate(self, context: str, filename: str | None = None) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You generate only PyAEDT Python code for HFSS tasks. "
                        "Return code only, no markdown fences or prose."
                    ),
                },
                {"role": "user", "content": context},
            ],
        }
        req = request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with request.urlopen(req, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return _extract_code_from_chat_completion(body)
            except (TimeoutError, socket.timeout, error.URLError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(self.retry_delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("OpenAI-compatible generation failed without an exception")


class AnthropicGenerator:
    def generate(self, context: str, filename: str | None = None) -> str:
        raise NotImplementedError("Anthropic generator is not wired in this offline benchmark")


def create_generator_from_env() -> CodeGenerator:
    backend = os.getenv("AEDT_AGENT_GENERATOR", "").lower()
    if backend == "file":
        base_dir = Path(os.getenv("AEDT_AGENT_FILE_GENERATOR_DIR", "."))
        return FileGenerator(base_dir)
    if backend == "openai":
        return OpenAIGenerator(
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", ""),
            timeout=int(os.getenv("OPENAI_TIMEOUT", "60")),
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
            retry_delay=float(os.getenv("OPENAI_RETRY_DELAY", "2")),
        )
    if backend == "anthropic":
        return AnthropicGenerator()
    return DefaultCodeGenerator()


def _extract_code_from_chat_completion(payload: dict) -> str:
    choices = payload.get("choices", [])
    if not choices:
        raise ValueError("OpenAI-compatible response did not include choices")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        content = "".join(text_parts)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenAI-compatible response did not include text content")
    return _strip_code_fences(content)


def _strip_code_fences(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped
