"""Structured model inventory: normalization, bounds and the graph node."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent.aihfss_inventory import (
    HANDLER_LAYOUT_INVENTORY,
    SCHEMA,
    _bounds,
    _edges,
    _length_expression_mm,
    _layout_inventory_node,
    _to_mm,
    build_layout_model_inventory,
    register_aihfss_inventory_handlers,
)


class _FakeLine:
    net_name = "MEZZ6_IO0_SD1_SW3_S1_DP10"
    placement_layer = "TOP"
    width = "7.5mil"
    object_units = "mil"
    edges = [[[43.33, 365.76], [43.73, 365.76]], [[43.73, 365.76], [43.73, 366.10]]]


class _FakeVia:
    net_name = "GND"
    location = [1743.38, 14456.85]
    holediam = "4.92mil"
    start_layer = "L2_GND"
    stop_layer = "L3"
    is_pin = False
    object_units = "mil"


class _FakePolygon:
    net_name = "GND"
    placement_layer = "L2_GND"
    is_void = False
    object_units = "mil"
    edges = [[[42.28, 365.01], [44.65, 365.01]], [[44.65, 365.01], [44.65, 367.84]]]


class _FakeModeler:
    line_names = ["line__35"]
    lines = {"line__35": _FakeLine()}
    vias = {"via_289": _FakeVia()}
    polygons = {"poly__218": _FakePolygon()}


class _FakeApp:
    design_name = "demo_design"
    project_name = "demo_project"
    modeler = _FakeModeler()


class _FakeContext:
    def __init__(self, payload: dict) -> None:
        self.input_payload = payload


def test_length_conversion_handles_the_units_aedt_reports():
    assert _to_mm(7.5, "mil") == pytest.approx(0.1905)
    assert _to_mm(0.04572, "meter") == pytest.approx(45.72)
    assert _to_mm(2.0, "mm") == 2.0
    assert _length_expression_mm("7.5mil") == pytest.approx(0.1905)
    assert _length_expression_mm("0.2mm") == pytest.approx(0.2)
    assert _length_expression_mm("not-a-length") is None


def test_edges_are_millimetres_even_when_object_units_say_mil():
    """Verified against the live design: an edge at 43.73 pairs with 1720 mil."""
    segments = _edges(_FakeLine(), "mil")
    assert segments == [[[43.33, 365.76], [43.73, 365.76]], [[43.73, 365.76], [43.73, 366.1]]]


def test_bounds_cover_traces_vias_and_polygons():
    traces = [{"segments": [[[1.0, 2.0], [3.0, 4.0]]]}]
    vias = [{"x_mm": 5.0, "y_mm": -1.0}]
    polygons = [{"segments": [[[0.0, 0.0], [-2.0, 6.0]]]}]
    assert _bounds(traces, vias, polygons) == {"min_x": -2.0, "min_y": -1.0, "max_x": 5.0, "max_y": 6.0}


def test_inventory_normalizes_geometry_and_reports_missing_sections():
    inventory = build_layout_model_inventory(_FakeApp())
    assert inventory["schema"] == SCHEMA
    assert inventory["units"] == "mm"
    assert inventory["design"] == "demo_design"
    assert inventory["counts"]["traces"] == 1
    assert inventory["counts"]["vias"] == 1
    assert inventory["counts"]["polygons"] == 1
    assert inventory["counts"]["components"] == 0
    via = inventory["vias"][0]
    assert via["x_mm"] == pytest.approx(44.2819, abs=1e-3)
    assert via["hole_diameter_mm"] == pytest.approx(0.125, abs=1e-4)
    assert via["start_layer"] == "L2_GND" and via["stop_layer"] == "L3"
    trace = inventory["traces"][0]
    assert trace["width_mm"] == pytest.approx(0.1905)
    assert trace["segments"][0][0] == [43.33, 365.76]
    # The fake app has no stackup reader, so the section must be reported, not faked.
    reported = {item["section"] for item in inventory["unavailable_sections"]}
    assert "stackup" in reported and "components" in reported
    assert inventory["read_only"] is True and inventory["design_unchanged"] is True


def test_handler_registers_under_the_expected_id():
    class _Registry:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def register(self, handler_id, handler) -> None:
            self.handlers[handler_id] = handler

    registry = _Registry()
    register_aihfss_inventory_handlers(registry)
    assert HANDLER_LAYOUT_INVENTORY in registry.handlers


def test_node_writes_the_inventory_artifact_and_loop_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import aedt_agent.agent.aihfss_inventory as module

    canned = build_layout_model_inventory(_FakeApp())
    monkeypatch.setattr(module, "inventory_from_port", lambda port, design=None: canned)
    result = _layout_inventory_node(_FakeContext({"artifact_dir": str(tmp_path), "aedt_port": 50051}))
    assert result["status"] == "succeeded"
    assert result["outcome"] == "layout_inventory"
    target = tmp_path / "model_inventory.json"
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8"))["schema"] == SCHEMA
    assert result["output_payload"]["loop_context"]["model_inventory"]["counts"]["vias"] == 1
    assert str(target) in result["artifact_refs"]


def test_node_reports_a_failure_instead_of_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import aedt_agent.agent.aihfss_inventory as module

    def _boom(port, design=None):
        raise RuntimeError("no live session")

    monkeypatch.setattr(module, "inventory_from_port", _boom)
    result = _layout_inventory_node(_FakeContext({"artifact_dir": str(tmp_path), "aedt_port": 50051}))
    assert result["status"] == "failed"
    assert result["error"]["code"] == "aihfss/inventory"
    assert "no live session" in result["output_payload"]["error"]