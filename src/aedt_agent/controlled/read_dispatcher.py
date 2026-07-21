from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_SCHEMA_VERSION = "controlled-aedt-read/v1"
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_OBJECT_SOURCES = {
    "via": "vias",
    "component": "components",
    "pin": "pins",
    "net": "nets",
    "line": "lines",
    "polygon": "polygons",
    "rectangle": "rectangles",
    "circle": "circles",
    "port": "excitations",
}


class ControlledProgramError(ValueError):
    pass


def read_program_schema() -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "mode": "read_only",
        "max_steps": 32,
        "max_objects_per_step": 50,
        "object_kinds": sorted(_OBJECT_SOURCES),
        "operations": ["list_objects", "read_attribute"],
        "forbidden": ["python", "shell", "COM", "method_call", "file", "network"],
    }


def validate_read_program(value: Any, *, product: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlledProgramError("program must be an object")
    if value.get("schema_version") != _SCHEMA_VERSION:
        raise ControlledProgramError(f"program.schema_version must be {_SCHEMA_VERSION}")
    if value.get("mode") != "read_only":
        raise ControlledProgramError("only read_only programs are currently supported")
    if str(value.get("product") or "").casefold() != product.casefold():
        raise ControlledProgramError("program.product does not match the live AEDT product")
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 32:
        raise ControlledProgramError("program.steps must contain from 1 to 32 steps")
    normalized = []
    ids = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ControlledProgramError("program step must be an object")
        step_id = _identifier(step.get("id"), "step.id")
        if step_id in ids:
            raise ControlledProgramError("program step ids must be unique")
        ids.add(step_id)
        op = str(step.get("op") or "")
        kind = str(step.get("object_kind") or "").casefold()
        if op not in {"list_objects", "read_attribute"} or kind not in _OBJECT_SOURCES:
            raise ControlledProgramError("program step operation or object_kind is not supported")
        entry = {"id": step_id, "op": op, "object_kind": kind}
        if op == "read_attribute":
            entry["name"] = _exact_name(step.get("name"))
            entry["attribute"] = _identifier(step.get("attribute"), "step.attribute")
        normalized.append(entry)
    normalized_program = {
        "schema_version": _SCHEMA_VERSION,
        "mode": "read_only",
        "product": product.casefold(),
        "steps": normalized,
    }
    encoded = json.dumps(normalized_program, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"program": normalized_program, "program_hash": hashlib.sha256(encoded).hexdigest()}


def execute_read_program(app: Any, validation: dict[str, Any]) -> dict[str, Any]:
    results = []
    for step in validation["program"]["steps"]:
        source = _OBJECT_SOURCES[step["object_kind"]]
        try:
            collection = getattr(app.modeler, source)
            records = dict(collection or {}) if isinstance(collection, dict) else {}
        except Exception:
            records = {}
            source_status = "unavailable"
        else:
            source_status = "ok" if records else "unavailable"
        if step["op"] == "list_objects":
            names = sorted(str(name) for name in records)[:50]
            results.append({"id": step["id"], "status": source_status, "count": len(names), "names": names})
            continue
        obj = records.get(step["name"])
        if obj is None:
            results.append({"id": step["id"], "status": "not_found", "name": step["name"]})
            continue
        try:
            value = getattr(obj, step["attribute"])
            if callable(value):
                raise TypeError("methods are not readable through the controlled dispatcher")
            result = {"id": step["id"], "status": "ok", "name": step["name"], "attribute": step["attribute"], "value": _json_value(value)}
        except Exception:
            result = {"id": step["id"], "status": "read_failed", "name": step["name"], "attribute": step["attribute"]}
        results.append(result)
    payload = {"program_hash": validation["program_hash"], "results": results}
    if len(json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")) > 256 * 1024:
        raise ControlledProgramError("controlled read response exceeds 256 KiB")
    payload["response_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _identifier(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_IDENTIFIER.fullmatch(text) or text.startswith("_"): 
        raise ControlledProgramError(f"{field} must be a public identifier")
    return text


def _exact_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128 or any(char in text for char in "\x00\r\n"):
        raise ControlledProgramError("step.name must be one exact AEDT object name")
    return text


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in list(value)[:100]]
    return str(value)[:4096]
