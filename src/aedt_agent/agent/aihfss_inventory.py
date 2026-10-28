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
from pathlib import Path
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


def _component_records(app: Any, max_items: int) -> tuple[list[dict[str, Any]], str]:
    """Components with their footprint box, placement and pins (mm)."""
    records: list[dict[str, Any]] = []
    try:
        components = app.modeler.components
        names = list(components.keys())
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:240]
    for name in names[:max_items]:
        try:
            component = components[name]
            units = _units_of(component)
            location = getattr(component, "location", None) or []
            bbox = getattr(component, "bounding_box", None) or []
            x = _to_mm(location[0], units) if len(location) >= 2 else None
            y = _to_mm(location[1], units) if len(location) >= 2 else None
            box = None
            if len(bbox) >= 4:
                values = [_to_mm(value, units) for value in bbox[:4]]
                if all(value is not None for value in values):
                    box = {
                        "x1": round(values[0], 4), "y1": round(values[1], 4),
                        "x2": round(values[2], 4), "y2": round(values[3], 4),
                        "width": round(abs(values[2] - values[0]), 4),
                        "height": round(abs(values[3] - values[1]), 4),
                    }
            pins: list[dict[str, Any]] = []
            try:
                component_pins = getattr(component, "pins", None) or {}
                pin_items = component_pins.items() if hasattr(component_pins, "items") else []
                for pin_name, pin in list(pin_items)[:200]:
                    pin_units = _units_of(pin)
                    pin_location = getattr(pin, "location", None) or []
                    pin_x = _to_mm(pin_location[0], pin_units) if len(pin_location) >= 2 else None
                    pin_y = _to_mm(pin_location[1], pin_units) if len(pin_location) >= 2 else None
                    pins.append({
                        "name": str(pin_name),
                        "net": str(getattr(pin, "net_name", "") or ""),
                        "x_mm": round(pin_x, 4) if pin_x is not None else None,
                        "y_mm": round(pin_y, 4) if pin_y is not None else None,
                        "hole_diameter_mm": _length_expression_mm(getattr(pin, "holediam", None)),
                        "layer": str(getattr(pin, "placement_layer", "") or ""),
                    })
            except Exception:  # noqa: BLE001 - pins are optional detail
                pass
            records.append({
                "name": str(name),
                "part": str(getattr(component, "part", "") or ""),
                "layer": str(getattr(component, "placement_layer", "") or ""),
                "angle": str(getattr(component, "angle", "") or ""),
                "x_mm": round(x, 4) if x is not None else None,
                "y_mm": round(y, 4) if y is not None else None,
                "bbox": box,
                "pin_count": len(pins),
                "pins": pins,
            })
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
    components, component_error = _component_records(app, max_items)
    unavailable = [
        {"section": section, "reason": reason}
        for section, reason in (
            ("stackup", stackup_error),
            ("traces", trace_error),
            ("vias", via_error),
            ("polygons", polygon_error),
            ("components", component_error),
        )
        if reason
    ]
    bounds = _bounds(traces, vias, polygons)
    for component in components:
        box = component.get("bbox") or {}
        if isinstance(box.get("x1"), (int, float)):
            for key, value in (("min_x", box["x1"]), ("min_y", box["y1"]), ("max_x", box["x2"]), ("max_y", box["y2"])):
                bounds[key] = round(min(bounds[key], value) if key.startswith("min") else max(bounds[key], value), 4)
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
        "components": components,
        "bounds": bounds,
        "counts": {
            "stackup_layers": len(stackup),
            "traces": len(traces),
            "vias": len(vias),
            "polygons": len(polygons),
            "components": len(components),
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
        artifacts = [str(target)]
        plot_info: dict[str, Any] = {"ok": False, "reason": "not attempted"}
        try:
            plot_info = render_stackup_plot(inventory, artifact_dir / STACKUP_PLOT_FILE)
        except Exception as exc:  # noqa: BLE001 - the figure is optional
            plot_info = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
        if plot_info.get("ok") and plot_info.get("path"):
            artifacts.append(str(plot_info["path"]))
            meta = artifact_dir / STACKUP_PLOT_META_FILE
            meta.write_text(json.dumps(plot_info, ensure_ascii=False, indent=1), encoding="utf-8")
            artifacts.append(str(meta))
        output = {
            "status": "inventoried",
            "design": inventory.get("design"),
            "project": inventory.get("project"),
            "counts": inventory.get("counts"),
            "bounds": inventory.get("bounds"),
            "inventory_path": str(target),
            "stackup_plot": plot_info,
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
            "artifact_refs": artifacts,
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

STACKUP_PLOT_FILE = "stackup_plot.png"
STACKUP_PLOT_META_FILE = "stackup_plot.meta.json"


def _scaled_heights(layers: list[dict[str, Any]]) -> list[float]:
    """PyEDB-style thickness scaling: max thickness becomes 3x the min one.

    Thin copper (0.03 mm) next to thick dielectric (0.09 mm) is nearly invisible
    at true scale, so the official plot compresses the range before drawing.
    """
    values = [float(layer["thickness_mm"]) for layer in layers]
    low = min(values)
    high = max(values)
    if high <= low:
        return values
    target_high = low * 3.0
    return [low + (value - low) * (target_high - low) / (high - low) for value in values]


FILL_TINTS = ["#eaede8", "#e7ebef", "#efeae4", "#e6ecec", "#eceee6"]


def _fill_tint(material: str) -> str:
    """A very light tint for the dielectric that fills a signal layer."""
    key = str(material or "").strip().lower()
    if not key:
        return FILL_TINTS[0]
    return FILL_TINTS[sum(ord(char) for char in key) % len(FILL_TINTS)]


PALETTE = [
    "#4f9c8f",  # teal
    "#8f7fc4",  # purple
    "#d97a7a",  # red
    "#d9a45c",  # orange
    "#b9b06a",  # olive
    "#7f9fc4",  # blue
    "#9c8f6a",  # sand
    "#6a9c7f",  # green
]


def render_stackup_plot(
    inventory: dict[str, Any],
    output_path: Path | str,
    *,
    size: tuple[int, int] = (1400, 1000),
) -> dict[str, Any]:
    """Draw the stackup figure in the style of PyEDB edb.stackup.plot().

    The official plot needs an EDB session (and therefore an EDB license); this
    draws the same content from the layer records we read out of AEDT, matching
    its look: white canvas, centered title, dielectric names on the left, metal
    and via names on the right, and a two-column legend with thickness and
    elevation.
    """
    layers = [
        layer
        for layer in inventory.get("stackup") or []
        if isinstance(layer.get("elevation_mm"), (int, float))
        and isinstance(layer.get("thickness_mm"), (int, float))
        and layer.get("thickness_mm", 0) > 0
    ]
    if not layers:
        return {"ok": False, "reason": "no stackup layers with thickness"}
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as exc:  # noqa: BLE001 - plotting is optional
        return {"ok": False, "reason": f"matplotlib unavailable: {type(exc).__name__}: {exc}"[:200]}

    layers.sort(key=lambda layer: layer["elevation_mm"] + layer["thickness_mm"], reverse=True)
    heights = _scaled_heights(layers)
    ink = "#2b2b2b"
    dielectric_fill = "#b9bdb7"
    fig, ax = plt.subplots(figsize=(size[0] / 100, size[1] / 100), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    legend_items: list[tuple[str, str]] = []
    cursor = 0.0
    metal_index = 0
    for layer, height in zip(layers, heights):
        is_signal = str(layer.get("type") or "") == "signal"
        name = str(layer.get("name") or "")
        y0 = cursor
        y1 = cursor + height
        cursor = y1
        if is_signal:
            color = PALETTE[metal_index % len(PALETTE)]
            metal_index += 1
            tint = _fill_tint(str(layer.get("fill_material") or ""))
            # Dielectric fill on both sides keeps the copper strip centred.
            ax.add_patch(Rectangle((0.0, y0), 1.0, height, facecolor=tint, edgecolor="#ffffff", linewidth=0.6))
            ax.add_patch(
                Rectangle(
                    (0.32, y0),
                    0.36,
                    height,
                    facecolor=color,
                    edgecolor="#ffffff",
                    linewidth=0.6,
                )
            )
            ax.text(1.015, y0 + height / 2, name, ha="left", va="center", color=ink, fontsize=10)
        else:
            ax.add_patch(Rectangle((0.0, y0), 1.0, height, facecolor=dielectric_fill, edgecolor="#ffffff", linewidth=0.6))
            ax.text(-0.015, y0 + height / 2, name, ha="right", va="center", color=ink, fontsize=10)
        elevation_mm = float(layer["elevation_mm"])
        thickness_um = float(layer["thickness_mm"]) * 1000.0
        legend_items.append(
            (
                color if is_signal else dielectric_fill,
                f"{name}, {layer.get('material') or ''}, thick: {thickness_um:.3f}um, elev: {elevation_mm * 1000.0:.3f}um",
            )
        )
    ax.set_xlim(-0.24, 1.30)
    ax.set_ylim(cursor * 1.02, -cursor * 0.02)  # inverted: first (top) layer at the top
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("Stackup", color="#1c1c1c", fontsize=20, pad=16)
    ax.add_patch(
        Rectangle(
            (0.60, 1.006),
            0.035,
            0.018,
            transform=ax.transAxes,
            facecolor=FILL_TINTS[0],
            edgecolor="#9a9a9a",
            linewidth=0.4,
            clip_on=False,
        )
    )
    ax.text(0.645, 1.012, "light = dielectric fill", transform=ax.transAxes, ha="left", va="bottom", color="#6a6a6a", fontsize=8)
    half = (len(legend_items) + 1) // 2
    step = 0.115 / max(half, 1)
    swatch_w, swatch_h = 0.014, 0.009
    for column, rows in enumerate((legend_items[:half], legend_items[half:])):
        x = 0.06 if column == 0 else 0.52
        for row_index, (color, row) in enumerate(rows):
            y = 0.155 - row_index * step
            fig.add_artist(
                Rectangle(
                    (x, y - swatch_h / 2),
                    swatch_w,
                    swatch_h,
                    transform=fig.transFigure,
                    facecolor=color,
                    edgecolor="#9a9a9a",
                    linewidth=0.4,
                    clip_on=False,
                )
            )
            fig.text(
                x + swatch_w + 0.007,
                y,
                row,
                color="#4a4a4a",
                fontsize=7.5,
                family="monospace",
                va="center",
            )
    fig.subplots_adjust(left=0.10, right=0.92, top=0.90, bottom=0.30)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, facecolor="white", dpi=110)
    plt.close(fig)
    total = sum(float(layer["thickness_mm"]) for layer in layers)
    return {
        "ok": True,
        "engine": "matplotlib",
        "style": "pyedb-like",
        "path": str(target),
        "bytes": target.stat().st_size if target.is_file() else 0,
        "layers": len(layers),
        "board_mm": round(total, 4),
    }