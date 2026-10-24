from __future__ import annotations

import hashlib
import json
import re
from typing import Any

class LiveBackendError(RuntimeError):
    code = "backend_error"



def digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def required(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise LiveBackendError(f"{name} must be a non-empty string")
    return value.strip()


def safe_attribute(owner: Any, attribute: str) -> Any:
    try:
        value = getattr(owner, attribute)
        if callable(value):
            value = value()
        return value
    except Exception:
        return None


def simulation_running(app: Any) -> bool:
    owner = app if hasattr(app, "are_there_simulations_running") else getattr(app, "desktop_class", app)
    attribute = getattr(owner, "are_there_simulations_running", None)
    if attribute is None:
        return False
    try:
        return bool(attribute() if callable(attribute) else attribute)
    except Exception:
        return False


def bounded_integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise LiveBackendError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def normalized_expression(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()

def bounded_float(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveBackendError(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < minimum or normalized > maximum:
        raise LiveBackendError(f"{field} must be between {minimum} and {maximum}")
    return normalized


SAFE_AEDT_OBJECT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_. -]{0,127}")
SAFE_AEDT_MATERIAL_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. +()-]{0,127}")
SAFE_AEDT_LAYER_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. +()-]{0,127}")
SAFE_AEDT_EXPRESSION = re.compile(r"[A-Za-z0-9_$\+\-*/^().,% \t]{1,128}")