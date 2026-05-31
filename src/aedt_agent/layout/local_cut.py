from __future__ import annotations

from typing import Any, Mapping


SUPPORTED_UNITS = {"mil", "mm", "um", "m"}


def parse_local_cut_region(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("local_cut_region is required")
    if value.get("type") != "bbox":
        raise ValueError("local_cut_region.type must be bbox")
    unit = str(value.get("unit") or "")
    if unit not in SUPPORTED_UNITS:
        raise ValueError(f"local_cut_region.unit must be one of {sorted(SUPPORTED_UNITS)}")
    output = {
        "type": "bbox",
        "unit": unit,
        "x_min": _number(value, "x_min"),
        "y_min": _number(value, "y_min"),
        "x_max": _number(value, "x_max"),
        "y_max": _number(value, "y_max"),
    }
    if output["x_min"] >= output["x_max"]:
        raise ValueError("local_cut_region.x_min must be less than x_max")
    if output["y_min"] >= output["y_max"]:
        raise ValueError("local_cut_region.y_min must be less than y_max")
    return output


def bbox_to_polygon(region: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_local_cut_region(region)
    return {
        "type": "polygon",
        "unit": parsed["unit"],
        "points": [
            [parsed["x_min"], parsed["y_min"]],
            [parsed["x_max"], parsed["y_min"]],
            [parsed["x_max"], parsed["y_max"]],
            [parsed["x_min"], parsed["y_max"]],
            [parsed["x_min"], parsed["y_min"]],
        ],
    }


def local_cut_summary(region: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_local_cut_region(region)
    return {"local_cut_region": parsed, "local_cut_polygon": bbox_to_polygon(parsed)}


def _number(value: Mapping[str, Any], key: str) -> float:
    if key not in value:
        raise ValueError(f"local_cut_region.{key} is required")
    try:
        return float(value[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"local_cut_region.{key} must be numeric") from exc
