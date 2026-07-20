from __future__ import annotations

import re
from typing import Any


DEFAULT_AEDT_VERSION = "2026.1"

_AEDT_VERSION_PATTERN = re.compile(
    r"^\s*(20\d{2})(?:\s*[Rr]\s*|\s*\.\s*)(\d+)"
    r"(?:\.\d+)*(?:\s*(?:SV|Student))?\s*$",
)
_REPORTED_AEDT_VERSION_PATTERN = re.compile(
    r"(?<!\d)(20\d{2})(?:\s*[Rr]\s*|\s*\.\s*)(\d+)(?:\.\d+)*(?!\d)",
)


def normalize_aedt_version(value: Any) -> str:
    """Return the AEDT release family used by PyAEDT, for example ``2024.2``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("AEDT version must be a non-empty string")
    match = _AEDT_VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"unsupported AEDT version format: {value!r}")
    return f"{match.group(1)}.{int(match.group(2))}"


def aedt_versions_match(left: Any, right: Any) -> bool:
    try:
        return normalize_aedt_version(left) == normalize_aedt_version(right)
    except ValueError:
        return False


def extract_reported_aedt_version(value: Any) -> str:
    """Normalize a release embedded in a Desktop-reported version string."""
    try:
        return normalize_aedt_version(value)
    except ValueError:
        if not isinstance(value, str):
            raise
    match = _REPORTED_AEDT_VERSION_PATTERN.search(value)
    if match is None:
        raise ValueError(f"unsupported AEDT version format: {value!r}")
    return f"{match.group(1)}.{int(match.group(2))}"
