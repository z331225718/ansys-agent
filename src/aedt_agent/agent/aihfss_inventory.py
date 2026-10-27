"""Structured geometry inventory for the AI-HFSS workbench model preview.

The workbench used to show "几何预览未接入" because the only geometry it could
see was whatever a loop context happened to carry. This module turns a LIVE
HFSS 3D Layout design into a compact, renderable description:

* stackup layers (name/type/material/thickness/elevation, normalized to mm);
* traces (net, layer, width, polyline segments);
* vias (net, position, hole diameter, start/stop layer);
* polygons (net, layer, void flag, outline segments).

Everything is read-only and bounded. Coordinates are normalized to millimetres
so the browser never has to guess units, and every section reports its own
failure instead of failing the whole inventory.

Schema: aihfss-model-inventory/v1.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA = "aihfss-model-inventory/v1"
DEFAULT_MAX_ITEMS = 400
MIL_TO_MM = 0.0254


def _to_mm(value: Any, units: str) -> float | None:
    """Convert one length to millimetres; returns None when unusable."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    unit = (units or "").strip().lower()
    if unit in {"mm", "millimeter", "millimeters"}:
        return number
    if unit in {"mil", "mils", "thou"}:
        return number * MIL_TO_MM
    if unit in {"meter", "meters", "m"}:
        return number * 1000.0
    if unit in {"um", "micron", "microns"}:
        return number / 1000.0
    if unit in {"cm"}:
        return number * 10.0
    if unit in {"in", "inch", "inches"}:
        return number * 25.4
    return None


def _length_expression_mm(value: Any) -> float | None:
    """Parse an AEDT length expression such as 7.5mil or 0.2mm."""
    text = str(value or "").strip()
    if text == "":
        return None
    split = len(text)
    while split > 0 and (text[split - 1].isalpha() or text[split - 1] == " "):
        split -= 1
    number_text = text[:split].strip()
    unit = text[split:].strip()
    return _to_mm(number_text, unit)


def _edges(record: Any, units: str) -> list[list[list[float]]]:
    """Normalize an edges attribute into millimetre segments.

    AEDT reports edge coordinates in millimetres for 3D Layout primitives
    even when object_units says mil (verified against center_line: an edge
    at 43.73 pairs with a 1720 mil centre line). location and expressions
    such as holediam follow object_units, so only those are converted.
    """
    try:
        raw = list(getattr(record, "edges", None) or [])
    except Exception:  # noqa: BLE001 - a missing edge list is not fatal
        return []
    segments: list[list[list[float]]] = []
    for segment in raw:
        try:
            points = list(segment)
            if len(points) < 2:
                continue
            start = points[0]
            stop = points[1]
            x0 = float(start[0])
            y0 = float(start[1])
            x1 = float(stop[0])
            y1 = float(stop[1])
            if None in (x0, y0, x1, y1):
                continue
            segments.append([[round(x0, 4), round(y0, 4)], [round(x1, 4), round(y1, 4)]])
        except Exception:  # noqa: BLE001 - skip malformed segments
            continue
    return segments


def _units_of(record: Any) -> str:
    try:
        return str(getattr(record, "object_units", "") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def _stackup_records(app: Any, max_items: int) -> tuple[list[dict[str, Any]], str]:
    """Reuse the live backend stackup reader, then normalize to millimetres."""
    try:
        from aedt_agent.live.backend import _layout_stackup_records

        layers, error = _layout_stackup_records(app, max_items=max_items)
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:240]
    normalized: list[dict[str, Any]] = []
    for layer in layers or []:
        thickness_mm = _to_mm(layer.get("thickness"), layer.get("thickness_units") or "meter")
        elevation_source = layer.get("lower_elevation")
        # The stackup reader reports elevations in mil for 3D Layout designs.
        elevation_mm = float(elevation_source) * MIL_TO_MM if isinstance(elevation_source, (int, float)) else None
        normalized.append(
            {
                "order": layer.get("order"),
                "name": str(layer.get("name") or ""),
                "type": str(layer.get("type") or ""),
                "material": str(layer.get("material") or ""),
                "fill_material": str(layer.get("fill_material") or ""),
                "thickness_mm": round(thickness_mm, 5) if thickness_mm is not None else None,
                "elevation_mm": round(elevation_mm, 4) if elevation_mm is not None else None,
                "is_negative": bool(layer.get("is_negative")),
            }
        )
    normalized.sort(key=lambda item: item["order"] if isinstance(item["order"], int) else 0)
    return normalized, error or ""


def _trace_records(app: Any, max_items: int) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    try:
        names = list(app.modeler.line_names)
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:240]
    for name in names[:max_items]:
        try:
            line = app.modeler.lines[name]
            units = _units_of(line)
            records.append(
                {
                    "name": str(name),
                    "net": str(getattr(line, "net_name", "") or ""),
                    "layer": str(getattr(line, "placement_layer", "") or ""),
                    "width_mm": _length_expression_mm(getattr(line, "width", "")),
                    "segments": _edges(line, units),
                }
            )
        except Exception:  # noqa: BLE001 - one unreadable trace must not stop the rest
            continue
    return records, ""


def _via_records(app: Any, max_items: int) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    try:
        vias = app.modeler.vias
        names = list(vias.keys())
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:240]
    for name in names[:max_items]:
        try:
            via = vias[name]
            units = _units_of(via)
            location = getattr(via, "location", None) or []
            x = _to_mm(location[0], units) if len(location) >= 2 else None
            y = _to_mm(location[1], units) if len(location) >= 2 else None
            hole = _length_expression_mm(getattr(via, "holediam", None))
            records.append(
                {
                    "name": str(name),
                    "net": str(getattr(via, "net_name", "") or ""),
                    "x_mm": round(x, 4) if x is not None else None,
                    "y_mm": round(y, 4) if y is not None else None,
                    "hole_diameter_mm": round(hole, 4) if hole is not None else None,
                    "start_layer": str(getattr(via, "start_layer", "") or ""),
                    "stop_layer": str(getattr(via, "stop_layer", "") or ""),
                    "is_pin": bool(getattr(via, "is_pin", False)),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return records, ""


def _polygon_records(app: Any, max_items: int) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    try:
        polygons = app.modeler.polygons
        names = list(polygons.keys())
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:240]
    for name in names[:max_items]:
        try:
            polygon = polygons[name]
            units = _units_of(polygon)
            records.append(
                {
                    "name": str(name),
                    "net": str(getattr(polygon, "net_name", "") or ""),
                    "layer": str(getattr(polygon, "placement_layer", "") or ""),
                    "is_void": bool(getattr(polygon, "is_void", False)),
                    "segments": _edges(polygon, units),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return records, ""


def _bounds(traces: list[dict[str, Any]], vias: list[dict[str, Any]], polygons: list[dict[str, Any]]) -> dict[str, float]:
    xs: list[float] = []
    ys: list[float] = []
    for trace in traces:
        for segment in trace.get("segments") or []:
            xs.extend([segment[0][0], segment[1][0]])
            ys.extend([segment[0][1], segment[1][1]])
    for polygon in polygons:
        for segment in polygon.get("segments") or []:
            xs.extend([segment[0][0], segment[1][0]])
            ys.extend([segment[0][1], segment[1][1]])
    for via in vias:
        if isinstance(via.get("x_mm"), (int, float)) and isinstance(via.get("y_mm"), (int, float)):
            xs.append(float(via["x_mm"]))
            ys.append(float(via["y_mm"]))
    if not xs or not ys:
        return {"min_x": 0.0, "min_y": 0.0, "max_x": 0.0, "max_y": 0.0}
    return {
        "min_x": round(min(xs), 4),
        "min_y": round(min(ys), 4),
        "max_x": round(max(xs), 4),
        "max_y": round(max(ys), 4),
    }


def build_layout_model_inventory(app: Any, *, max_items: int = DEFAULT_MAX_ITEMS) -> dict[str, Any]:
    """Build the renderable inventory for one live layout design."""
    stackup, stackup_error = _stackup_records(app, max_items)
    traces, trace_error = _trace_records(app, max_items)
    vias, via_error = _via_records(app, max_items)
    polygons, polygon_error = _polygon_records(app, max_items)
    unavailable = [
        {"section": section, "reason": reason}
        for section, reason in (
            ("stackup", stackup_error),
            ("traces", trace_error),
            ("vias", via_error),
            ("polygons", polygon_error),
        )
        if reason
    ]
    bounds = _bounds(traces, vias, polygons)
    return {
        "schema": SCHEMA,
        "provenance": "aedt_live_inventory",
        "units": "mm",
        "design": str(getattr(app, "design_name", "") or ""),
        "project": str(getattr(app, "project_name", "") or ""),
        "stackup": stackup,
        "traces": traces,
        "vias": vias,
        "polygons": polygons,
        "bounds": bounds,
        "counts": {
            "stackup_layers": len(stackup),
            "traces": len(traces),
            "vias": len(vias),
            "polygons": len(polygons),
        },
        "unavailable_sections": unavailable,
        "max_items": max_items,
        "read_only": True,
        "design_unchanged": True,
    }


def inventory_from_port(port: int, *, version: str = "2026.1", design: str | None = None) -> dict[str, Any]:
    """Attach to a running AEDT session by gRPC port and inventory its layout."""
    from ansys.aedt.core import Desktop, Hfss3dLayout  # type: ignore

    desktop = Desktop(version=version, new_desktop=False, non_graphical=False, port=port)
    try:
        app = Hfss3dLayout(design=design, new_desktop=False, non_graphical=False)
        return build_layout_model_inventory(app)
    finally:
        try:
            desktop.release_desktop(close_projects=False, close_on_exit=False)
        except Exception:  # noqa: BLE001 - never close the engineer session
            pass

# ---------------------------------------------------------------------------
# Graph node: produce the inventory inside a workbench task.
# ---------------------------------------------------------------------------

HANDLER_LAYOUT_INVENTORY = "aihfss.inventory.layout_model"
INVENTORY_FILE = "model_inventory.json"


def register_aihfss_inventory_handlers(registry: Any) -> None:
    """Register the read-only layout inventory handler."""
    registry.register(HANDLER_LAYOUT_INVENTORY, _layout_inventory_node)


def _layout_inventory_node(context: Any) -> dict[str, Any]:
    """Read the open layout (or one by port) and emit a renderable inventory."""
    import json
    from pathlib import Path

    from aedt_agent.agent.graph_executors import GraphNodeExecutionResult  # noqa: F401
    from aedt_agent.agent.mission import NodeRunStatus

    payload = dict(context.input_payload or {})
    artifact_dir = Path(str(payload.get("artifact_dir") or ".")).expanduser()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    port = int(payload.get("aedt_port") or 0)
    design = str(payload.get("design_name") or "").strip() or None
    try:
        if port:
            inventory = inventory_from_port(port, design=design)
        else:
            from ansys.aedt.core import Hfss3dLayout  # type: ignore

            app = Hfss3dLayout(design=design, new_desktop=False, non_graphical=False)
            inventory = build_layout_model_inventory(app)
        target = artifact_dir / INVENTORY_FILE
        target.write_text(json.dumps(inventory, ensure_ascii=False, indent=1), encoding="utf-8")
        output = {
            "status": "inventoried",
            "design": inventory.get("design"),
            "project": inventory.get("project"),
            "counts": inventory.get("counts"),
            "bounds": inventory.get("bounds"),
            "inventory_path": str(target),
            "unavailable_sections": inventory.get("unavailable_sections"),
            "loop_context": {
                "model_inventory": inventory,
                "model_inventory_path": str(target),
            },
        }
        return {
            "status": NodeRunStatus.SUCCEEDED.value,
            "outcome": "layout_inventory",
            "output_payload": output,
            "artifact_refs": [str(target)],
        }
    except Exception as exc:  # noqa: BLE001 - bounded failure for the task view
        message = type(exc).__name__ + ": " + str(exc)[:400]
        return {
            "status": NodeRunStatus.FAILED.value,
            "outcome": "layout_inventory_failed",
            "output_payload": {"error": message},
            "artifact_refs": [],
            "error": {"code": "aihfss/inventory", "message": message},
        }
