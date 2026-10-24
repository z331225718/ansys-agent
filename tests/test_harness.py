from __future__ import annotations

import pytest

from aedt_agent.harness import (
    HfssGeometryHarness,
    HfssMaterialHarness,
    LayoutAntipadHarness,
    LayoutViaHarness,
    LiveBackendError,
)
from aedt_agent.harness.common import bounded_float, bounded_integer, digest, required
from aedt_agent.harness.variables import variable_name, variable_product
from aedt_agent.live.target import AedtTarget


def test_common_helpers():
    assert digest({"k": "v"}) == digest({"k": "v"})
    assert required({"field": "val"}, "field") == "val"
    with pytest.raises(LiveBackendError, match="must be a non-empty string"):
        required({}, "missing")

    assert bounded_integer(10, "f", minimum=1, maximum=20) == 10
    with pytest.raises(LiveBackendError):
        bounded_integer(25, "f", minimum=1, maximum=20)

    assert bounded_float(3.14, "f", minimum=0.0, maximum=10.0) == 3.14
    with pytest.raises(LiveBackendError):
        bounded_float(-1.0, "f", minimum=0.0, maximum=10.0)


def test_variable_helpers():
    assert variable_product({"product": "hfss"}) == "hfss"
    assert variable_product({"product": "layout"}) == "layout"
    with pytest.raises(LiveBackendError, match="must be hfss or layout"):
        variable_product({"product": "unknown"})

    assert variable_name({"variable_name": "w_trace"}) == "w_trace"
    assert variable_name({"variable_name": "$proj_w"}) == "$proj_w"
    with pytest.raises(LiveBackendError, match="valid AEDT identifier"):
        variable_name({"variable_name": "123invalid"})


class DummyBackend:
    def __init__(self):
        self.calls = []

    def _layout_via_create_preview(self, target, args):
        self.calls.append(("via_create_preview", target, args))
        return {"preview_id": "via-preview-1"}

    def _layout_antipad_circle_create_preview(self, target, args):
        self.calls.append(("antipad_preview", target, args))
        return {"preview_id": "antipad-preview-1"}

    def _hfss_material_create_preview(self, target, args):
        self.calls.append(("mat_preview", target, args))
        return {"preview_id": "mat-preview-1"}

    def _hfss_geometry_create_preview(self, target, args):
        self.calls.append(("geom_preview", target, args))
        return {"preview_id": "geom-preview-1"}


def test_harness_delegation():
    backend = DummyBackend()
    target = AedtTarget.from_values(port=50001)

    res1 = LayoutViaHarness.create_preview(backend, target, {"name": "v1"})
    assert res1["preview_id"] == "via-preview-1"

    res2 = LayoutAntipadHarness.create_preview(backend, target, {"name": "ap1"})
    assert res2["preview_id"] == "antipad-preview-1"

    res3 = HfssMaterialHarness.preview_create(backend, target, {"name": "copper"})
    assert res3["preview_id"] == "mat-preview-1"

    res4 = HfssGeometryHarness.preview_create(backend, target, {"name": "box1"})
    assert res4["preview_id"] == "geom-preview-1"

    assert len(backend.calls) == 4
