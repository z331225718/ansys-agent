from __future__ import annotations

from typing import Any, Mapping


def build_void_fallback_payload(recorded_analysis: Mapping[str, Any]) -> dict[str, Any]:
    variable = _first_variable_name(recorded_analysis)
    operations = []
    for item in recorded_analysis.get("voids", []) or []:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        layer = str(item.get("layer") or "")
        if kind not in {"circle", "rectangle"}:
            continue
        api = "oEditor.CreateCircleVoid" if kind == "circle" else "oEditor.CreateRectangleVoid"
        operations.append({"api": api, "layer": layer, "kind": kind, "variable": variable})
    return {
        "status": "ready" if operations and variable else "needs_user_hint",
        "variable": variable,
        "operations": operations,
        "note": "Raw AEDT void commands are represented as data and must be executed only by a controlled fallback runner.",
    }


def _first_variable_name(recorded_analysis: Mapping[str, Any]) -> str:
    for item in recorded_analysis.get("optimization_variables", []) or []:
        if isinstance(item, Mapping) and item.get("name"):
            return str(item["name"])
    return ""
