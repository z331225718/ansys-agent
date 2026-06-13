from __future__ import annotations

import json
import re
from typing import Any


GITNEXUS_QUERY_PATTERNS = (
    "gitnexus query",
    "gitnexus.query",
    "mcp__gitnexus__query",
    '"query"',
)
GITNEXUS_CONTEXT_PATTERNS = (
    "gitnexus context",
    "gitnexus.context",
    "mcp__gitnexus__context",
    '"context"',
)


def analyze_tool_usage(transcript: str, code: str = "") -> dict[str, Any]:
    text = transcript or ""
    lower = text.lower()
    names = _tool_call_names(text)
    query_count = _count_patterns(lower, GITNEXUS_QUERY_PATTERNS)
    context_count = _count_patterns(lower, GITNEXUS_CONTEXT_PATTERNS)
    used_tools = bool(names or query_count or context_count or "tool_use" in lower or "mcp" in lower)
    return {
        "used_tools": used_tools,
        "gitnexus_query_count": query_count,
        "gitnexus_context_count": context_count,
        "tool_call_names": names,
        "retrieval_before_code": _retrieval_before_code(lower, code),
    }


def _count_patterns(text: str, patterns: tuple[str, ...]) -> int:
    return sum(text.count(pattern) for pattern in patterns)


def _tool_call_names(transcript: str) -> list[str]:
    names: list[str] = []
    for line in transcript.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        _collect_tool_names(payload, names)
    if names:
        return _dedupe(names)
    matches = re.findall(r"(?:mcp__)?gitnexus(?:[._-][a-z_]+)?", transcript, flags=re.IGNORECASE)
    return _dedupe(match.lower().replace("__", ".").replace("_", ".") for match in matches)


def _collect_tool_names(value: Any, names: list[str]) -> None:
    if isinstance(value, dict):
        if value.get("type") in {"tool_use", "tool_result"} and isinstance(value.get("name"), str):
            names.append(value["name"])
        if isinstance(value.get("tool_name"), str):
            names.append(value["tool_name"])
        if isinstance(value.get("server_name"), str) and isinstance(value.get("name"), str):
            names.append(f"{value['server_name']}.{value['name']}")
        for child in value.values():
            _collect_tool_names(child, names)
    elif isinstance(value, list):
        for child in value:
            _collect_tool_names(child, names)


def _retrieval_before_code(transcript: str, code: str) -> bool:
    retrieval_positions = [
        position
        for marker in ("gitnexus", "mcp__gitnexus", "tool_use", "tool_result")
        if (position := transcript.find(marker)) >= 0
    ]
    if not retrieval_positions:
        return False
    code_positions = [
        position
        for marker in ("```python", "\napp.", "app.", "create_box", "wave_port")
        if (position := transcript.find(marker)) >= 0
    ]
    if not code_positions and code:
        first_line = next((line.strip().lower() for line in code.splitlines() if line.strip()), "")
        if first_line:
            found = transcript.find(first_line)
            if found >= 0:
                code_positions.append(found)
    return not code_positions or min(retrieval_positions) < min(code_positions)


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
