# Stage C.5 Bbox Local Cut Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the first build-only Stage C.5 local cut optimization cell using a user-supplied bbox, with no LLM-generated cut region and no default solve.

**Architecture:** Add a small local-cut model layer for bbox validation and polygon conversion, then reuse the existing PyEDB/Hfss3dLayout build path where possible. The new runner must fail closed: if bbox local cut or uniform-line port selection is ambiguous, it writes candidate reports and stops instead of silently falling back to whole-channel cutout or wrong ports.

**Tech Stack:** Python 3.12, pathlib, JSON, pytest, PyEDB, PyAEDT Hfss3dLayout, existing `aedt_agent.demo.import_cutout` and `aedt_agent.layout.ports` modules.

---

## Scope

This plan implements build-only local cut cells:

- Validate user-supplied bbox.
- Convert bbox to a closed polygon.
- Pass bbox/polygon intent through request, action plan, summary, and CLI artifacts.
- Support fake mode for tests and smoke output.
- Add a PyEDB local-cut adapter point that attempts bbox/polygon cutout and refuses whole-channel fallback.
- Add uniform-line edge candidate reporting near a bbox side.
- Create uniform-line edge ports only when the candidate is unambiguous.
- Keep `solve_enabled=false` by default.

This plan does not:

- Implement automatic LLM bbox generation.
- Run large AEDT solves.
- Implement multi-iteration optimization.
- Guarantee every board topology can auto-select the uniform-line port.

## Files

- Create `src/aedt_agent/layout/local_cut.py`
  Bbox dataclass-free model helpers, validation, unit conversion metadata, bbox-to-polygon conversion, local cut summary helpers.

- Modify `src/aedt_agent/demo/import_cutout.py`
  Extend `ImportCutoutRequest` with `local_cut_region`, `local_cut_polygon`, and `uniform_line_port_hint`; use local cutout path when bbox is present; write bbox into summary.

- Modify `src/aedt_agent/layout/ports.py`
  Add uniform-line edge candidate scoring near a bbox side and add an action strategy for `uniform_line_edge_port`.

- Create `scripts/run_stage_c5_local_cut_build.py`
  New CLI for local cut cell build-only runs. It requires `local_cut_region`.

- Create `src/aedt_agent/layout/recorded_settings.py`
  Shared helper that copies recorded HFSS 3D Layout setup, sweep, extent, and design settings into build parameters.

- Modify `scripts/run_stage_c5_recorded_build.py`
  Import the shared recorded-settings helper instead of owning a private copy.

- Create `tests/test_local_cut.py`
  Unit tests for bbox validation and polygon conversion.

- Create `tests/test_recorded_settings.py`
  Unit tests for recorded-settings helper reuse.

- Create `tests/test_stage_c5_local_cut_build.py`
  CLI and integration-style fake tests for local cut summary/action records.

- Modify `tests/test_import_cutout_demo.py`
  Extend fake PyEDB/Hfss3dLayout coverage for local cut request propagation and no whole-channel fallback.

- Modify `docs/superpowers/specs/2026-05-31-stage-c5-local-cut-optimization-cell-design.md`
  Add the `run_stage_c5_local_cut_build.py` command and output artifact names.

## Task 1: Bbox Model And Validation

**Files:**
- Create: `src/aedt_agent/layout/local_cut.py`
- Create: `tests/test_local_cut.py`

- [ ] **Step 1: Write failing bbox validation tests**

Add `tests/test_local_cut.py`:

```python
import pytest

from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region


def test_parse_local_cut_region_accepts_bbox_and_preserves_unit():
    region = parse_local_cut_region(
        {
            "type": "bbox",
            "unit": "mil",
            "x_min": 5400.0,
            "y_min": 1100.0,
            "x_max": 6200.0,
            "y_max": 1500.0,
        }
    )

    assert region == {
        "type": "bbox",
        "unit": "mil",
        "x_min": 5400.0,
        "y_min": 1100.0,
        "x_max": 6200.0,
        "y_max": 1500.0,
    }


def test_parse_local_cut_region_rejects_missing_unit():
    with pytest.raises(ValueError, match="unit"):
        parse_local_cut_region({"type": "bbox", "x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1})


def test_parse_local_cut_region_rejects_inverted_bbox():
    with pytest.raises(ValueError, match="x_min must be less than x_max"):
        parse_local_cut_region({"type": "bbox", "unit": "mil", "x_min": 10, "y_min": 0, "x_max": 1, "y_max": 1})


def test_bbox_to_polygon_returns_closed_clockwise_points():
    region = parse_local_cut_region({"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4})

    polygon = bbox_to_polygon(region)

    assert polygon == {
        "type": "polygon",
        "unit": "mil",
        "points": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0], [1.0, 2.0]],
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_local_cut.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'aedt_agent.layout.local_cut'`.

- [ ] **Step 3: Implement local cut helpers**

Create `src/aedt_agent/layout/local_cut.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_local_cut.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/aedt_agent/layout/local_cut.py tests/test_local_cut.py
git commit -m "feat: add local cut bbox model"
```

## Task 2: Request And Summary Propagation

**Files:**
- Modify: `src/aedt_agent/demo/import_cutout.py`
- Modify: `tests/test_import_cutout_demo.py`

- [ ] **Step 1: Write failing request propagation test**

In `tests/test_import_cutout_demo.py`, add this test near existing request tests:

```python
def test_build_import_cutout_request_accepts_local_cut_region(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")

    request = build_import_cutout_request(
        {
            "layout_file": str(layout_file),
            "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
            "uniform_line_port_hint": {"side": "right", "layer": "ART03", "port_type": "edge"},
        }
    )

    assert request.local_cut_region["unit"] == "mil"
    assert request.local_cut_polygon["points"] == [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0], [1.0, 2.0]]
    assert request.uniform_line_port_hint == {"side": "right", "layer": "ART03", "port_type": "edge"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_import_cutout_demo.py::test_build_import_cutout_request_accepts_local_cut_region -q
```

Expected: FAIL with `AttributeError: 'ImportCutoutRequest' object has no attribute 'local_cut_region'`.

- [ ] **Step 3: Extend request model**

Modify `src/aedt_agent/demo/import_cutout.py`:

```python
from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region
```

Add fields to `ImportCutoutRequest`:

```python
    local_cut_region: dict[str, Any] = field(default_factory=dict)
    local_cut_polygon: dict[str, Any] = field(default_factory=dict)
    uniform_line_port_hint: dict[str, Any] = field(default_factory=dict)
```

Inside `build_import_cutout_request()` before returning:

```python
    local_cut_region = {}
    local_cut_polygon = {}
    if parameters.get("local_cut_region"):
        local_cut_region = parse_local_cut_region(parameters.get("local_cut_region"))
        local_cut_polygon = bbox_to_polygon(local_cut_region)
```

Pass these to `ImportCutoutRequest`:

```python
        local_cut_region=local_cut_region,
        local_cut_polygon=local_cut_polygon,
        uniform_line_port_hint=_mapping_parameter(parameters.get("uniform_line_port_hint")),
```

- [ ] **Step 4: Add local cut summary output**

In `run_fake_import_cutout()` summary and real `summary` in `import_brd_with_pyedb_cutout()`, add:

```python
            "local_cut_region": dict(request.local_cut_region),
            "local_cut_polygon": dict(request.local_cut_polygon),
            "uniform_line_port_hint": dict(request.uniform_line_port_hint),
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_import_cutout_demo.py::test_build_import_cutout_request_accepts_local_cut_region tests/test_stage_c5_recorded_build_runner.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/aedt_agent/demo/import_cutout.py tests/test_import_cutout_demo.py
git commit -m "feat: propagate local cut request metadata"
```

## Task 3: Local Cutout Adapter Point

**Files:**
- Modify: `src/aedt_agent/demo/import_cutout.py`
- Modify: `tests/test_import_cutout_demo.py`

- [ ] **Step 1: Write failing local cutout behavior test**

Add a test in `tests/test_import_cutout_demo.py` using the existing `FakeEdb` pattern:

```python
def test_real_import_cutout_uses_bbox_polygon_for_local_cut(monkeypatch, tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    calls = []

    class FakeEdb:
        def __init__(self, edbpath, version=None, grpc=None):
            self.edbpath = str(Path(edbpath).with_suffix(".aedb"))
            self.nets = type("Nets", (), {"nets": {"GND": object(), "SIG_P": object(), "SIG_N": object()}})()
            self.excitation_manager = type("ExcitationManager", (), {})()
            calls.append(("edb_open", Path(edbpath).name))

        def cutout(self, **kwargs):
            Path(kwargs["output_aedb_path"]).mkdir(parents=True)
            calls.append(("cutout", kwargs))
            return [1, 2, 3]

        def close(self):
            calls.append(("edb_close", None))

    monkeypatch.setattr(import_cutout, "_edb_class", lambda: FakeEdb, raising=False)
    monkeypatch.setattr(import_cutout, "_write_layout_port_candidate_report", lambda *args, **kwargs: {"status": "needs_user_hint", "port_action_plan": {"status": "needs_user_hint", "port_actions": []}, "candidate_count": 0})
    monkeypatch.setattr(import_cutout, "_apply_edb_port_actions_to_cutout", lambda *args, **kwargs: {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []})
    monkeypatch.setattr(import_cutout, "_open_cutout_in_hfss3dlayout", lambda *args, **kwargs: (str(tmp_path / "case.aedt"), False, {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []}, {"setup_name": "Setup1"}, {"status": "skipped"}, {}))

    request = build_import_cutout_request(
        {
            "layout_file": str(layout_file),
            "signal_nets": "SIG_*",
            "reference_nets": "GND",
            "artifact_dir": str(tmp_path / "run"),
            "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        }
    )

    result = run_real_import_cutout(request, aedt_version="2026.1")

    cutout_call = next(call for call in calls if call[0] == "cutout")[1]
    assert cutout_call["signal_nets"] == ["SIG_N", "SIG_P"]
    assert cutout_call["reference_nets"] == ["GND"]
    assert cutout_call["extent_type"] == "Polygon"
    assert cutout_call["custom_extent"] == [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0], [1.0, 2.0]]
    assert result["local_cut_region"]["unit"] == "mil"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_import_cutout_demo.py::test_real_import_cutout_uses_bbox_polygon_for_local_cut -q
```

Expected: FAIL because `extent_type` remains current request extent type and `custom_extent` is absent.

- [ ] **Step 3: Implement cutout kwargs builder**

In `src/aedt_agent/demo/import_cutout.py`, add:

```python
def _cutout_kwargs(request: ImportCutoutRequest, signal_nets: list[str], reference_nets: list[str], output_aedb: Path, threads: int) -> dict[str, Any]:
    kwargs = {
        "signal_nets": signal_nets,
        "reference_nets": reference_nets,
        "extent_type": request.extent_type,
        "expansion_size": request.expansion_size,
        "output_aedb_path": str(output_aedb),
        "use_pyaedt_cutout": True,
        "number_of_threads": threads,
        "open_cutout_at_end": False,
    }
    if request.local_cut_polygon:
        kwargs["extent_type"] = "Polygon"
        kwargs["custom_extent"] = request.local_cut_polygon["points"]
    return kwargs
```

Replace the current inline `edb.cutout` keyword block with:

```python
            extent_points = edb.cutout(**_cutout_kwargs(request, signal_nets, reference_nets, cutout_aedb, threads))
```

- [ ] **Step 4: Ensure fail-closed behavior**

In the `except Exception as exc` block that catches `edb.cutout` failures, do not retry without bbox. Add to the emitted progress payload:

```python
                local_cut_region=request.local_cut_region,
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_import_cutout_demo.py::test_real_import_cutout_uses_bbox_polygon_for_local_cut tests/test_import_cutout_demo.py::test_real_import_cutout_uses_pyedb_cutout_before_hfss3dlayout -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/aedt_agent/demo/import_cutout.py tests/test_import_cutout_demo.py
git commit -m "feat: use bbox polygon for local cutout"
```

## Task 4: Uniform-Line Edge Candidate Reporting

**Files:**
- Modify: `src/aedt_agent/layout/ports.py`
- Create: `tests/test_layout_ports.py`

- [ ] **Step 1: Write failing candidate scoring tests**

Create `tests/test_layout_ports.py`:

```python
from aedt_agent.layout.ports import find_uniform_line_edge_candidates


class Primitive:
    def __init__(self, name, net_name, layer, edges):
        self.name = name
        self.net_name = net_name
        self.layer = layer
        self.edges = edges


def test_find_uniform_line_edge_candidates_prefers_bbox_side_and_layer():
    primitives = [
        Primitive("sig_right", "SIG_P", "ART03", [[[9.8, 2.0], [9.8, 4.0]]]),
        Primitive("sig_left", "SIG_P", "ART03", [[[1.1, 2.0], [1.1, 4.0]]]),
        Primitive("other_layer", "SIG_P", "ART04", [[[9.9, 2.0], [9.9, 4.0]]]),
    ]

    report = find_uniform_line_edge_candidates(
        primitives,
        signal_nets=["SIG_P"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 1, "x_max": 10, "y_max": 5},
        hint={"side": "right", "layer": "ART03", "port_type": "edge"},
    )

    assert report["status"] == "ready"
    assert report["candidates"][0]["primitive"] == "sig_right"
    assert report["candidates"][0]["edge_number"] == 0
    assert report["candidates"][0]["distance_to_side"] == 0.2


def test_find_uniform_line_edge_candidates_reports_ambiguous_candidates():
    primitives = [
        Primitive("sig_a", "SIG_P", "ART03", [[[9.8, 2.0], [9.8, 4.0]]]),
        Primitive("sig_b", "SIG_P", "ART03", [[[9.81, 2.0], [9.81, 4.0]]]),
    ]

    report = find_uniform_line_edge_candidates(
        primitives,
        signal_nets=["SIG_P"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 1, "x_max": 10, "y_max": 5},
        hint={"side": "right", "layer": "ART03", "port_type": "edge"},
    )

    assert report["status"] == "ambiguous"
    assert len(report["candidates"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_ports.py -q
```

Expected: FAIL because `find_uniform_line_edge_candidates` does not exist.

- [ ] **Step 3: Implement candidate reporter**

In `src/aedt_agent/layout/ports.py`, add public function:

```python
def find_uniform_line_edge_candidates(
    primitives: list[Any],
    *,
    signal_nets: list[str],
    local_cut_region: dict[str, Any],
    hint: dict[str, Any],
) -> dict[str, Any]:
    from aedt_agent.layout.local_cut import parse_local_cut_region

    region = parse_local_cut_region(local_cut_region)
    side = str(hint.get("side") or "right")
    layer = str(hint.get("layer") or "")
    signals = {net.casefold() for net in signal_nets}
    candidates: list[dict[str, Any]] = []
    for primitive in primitives:
        if str(getattr(primitive, "net_name", "")).casefold() not in signals:
            continue
        if layer and str(getattr(primitive, "layer", "")) != layer:
            continue
        for edge_number, edge in enumerate(getattr(primitive, "edges", []) or []):
            midpoint = _edge_midpoint(edge)
            distance = _distance_to_bbox_side(midpoint, region, side)
            candidates.append(
                {
                    "primitive": str(getattr(primitive, "name", "")),
                    "edge_number": edge_number,
                    "net": str(getattr(primitive, "net_name", "")),
                    "layer": str(getattr(primitive, "layer", "")),
                    "side": side,
                    "midpoint": midpoint,
                    "distance_to_side": round(distance, 6),
                }
            )
    candidates.sort(key=lambda item: (item["distance_to_side"], item["primitive"], item["edge_number"]))
    if not candidates:
        status = "needs_user_hint"
    elif len(candidates) >= 2 and abs(candidates[0]["distance_to_side"] - candidates[1]["distance_to_side"]) <= 0.05:
        status = "ambiguous"
    else:
        status = "ready"
    return {"status": status, "candidates": candidates}
```

Also add helpers:

```python
def _edge_midpoint(edge: Any) -> list[float]:
    return [(float(edge[0][0]) + float(edge[1][0])) / 2.0, (float(edge[0][1]) + float(edge[1][1])) / 2.0]


def _distance_to_bbox_side(point: list[float], region: dict[str, Any], side: str) -> float:
    if side == "left":
        return abs(point[0] - region["x_min"])
    if side == "right":
        return abs(point[0] - region["x_max"])
    if side == "bottom":
        return abs(point[1] - region["y_min"])
    if side == "top":
        return abs(point[1] - region["y_max"])
    raise ValueError("uniform_line_port_hint.side must be one of left, right, bottom, top")
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_ports.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/aedt_agent/layout/ports.py tests/test_layout_ports.py
git commit -m "feat: report uniform line edge candidates"
```

## Task 5: Recorded Settings Helper And Local Cut Build CLI

**Files:**
- Create: `src/aedt_agent/layout/recorded_settings.py`
- Create: `tests/test_recorded_settings.py`
- Modify: `scripts/run_stage_c5_recorded_build.py`
- Create: `scripts/run_stage_c5_local_cut_build.py`
- Create: `tests/test_stage_c5_local_cut_build.py`
- Modify: `docs/superpowers/specs/2026-05-31-stage-c5-local-cut-optimization-cell-design.md`

- [ ] **Step 1: Write failing recorded settings helper test**

Create `tests/test_recorded_settings.py`:

```python
from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings


def test_merge_recorded_layout_settings_copies_setup_sweep_extents_and_design_options():
    params = {}
    recorded_analysis = {
        "hfss_extents": {"AirHorExt": "3mm"},
        "design_options": {"DesignMode": "Hfss"},
        "setup": {
            "options": {"AdaptiveSettings": {"MaxPasses": 8}},
            "advanced_settings": {"PhiPlusMesher": True},
            "curve_approximation": {"ArcAngle": "30deg", "MaxArcPoints": 8},
        },
        "sweep": {"options": {"UseQ3DForDC": True, "MaxSolutions": 250}},
    }

    merge_recorded_layout_settings(params, recorded_analysis)

    assert params["recorded_hfss_extents"] == {"AirHorExt": "3mm"}
    assert params["recorded_design_options"] == {"DesignMode": "Hfss"}
    assert params["recorded_setup_options"] == {"AdaptiveSettings": {"MaxPasses": 8}}
    assert params["recorded_setup_advanced_settings"] == {"PhiPlusMesher": True}
    assert params["recorded_setup_curve_approximation"] == {"ArcAngle": "30deg", "MaxArcPoints": 8}
    assert params["recorded_sweep_options"] == {"UseQ3DForDC": True, "MaxSolutions": 250}
    assert params["use_q3d_for_dc"] is True
    assert params["interpolation_max_solutions"] == 250
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_recorded_settings.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'aedt_agent.layout.recorded_settings'`.

- [ ] **Step 3: Implement shared recorded settings helper**

Create `src/aedt_agent/layout/recorded_settings.py`:

```python
from __future__ import annotations


def merge_recorded_layout_settings(params: dict[str, object], recorded_analysis: dict[str, object]) -> None:
    setup = recorded_analysis.get("setup") if isinstance(recorded_analysis.get("setup"), dict) else {}
    sweep = recorded_analysis.get("sweep") if isinstance(recorded_analysis.get("sweep"), dict) else {}
    sweep_options = sweep.get("options") if isinstance(sweep.get("options"), dict) else {}
    params["recorded_hfss_extents"] = dict(recorded_analysis.get("hfss_extents") or {})
    params["recorded_design_options"] = dict(recorded_analysis.get("design_options") or {})
    params["recorded_setup_options"] = dict(setup.get("options") or {})
    params["recorded_setup_advanced_settings"] = dict(setup.get("advanced_settings") or {})
    params["recorded_setup_curve_approximation"] = dict(setup.get("curve_approximation") or {})
    params["recorded_sweep_options"] = dict(sweep_options)
    if "UseQ3DForDC" in sweep_options:
        params["use_q3d_for_dc"] = sweep_options["UseQ3DForDC"]
    if "MaxSolutions" in sweep_options:
        params["interpolation_max_solutions"] = sweep_options["MaxSolutions"]
```

Modify `scripts/run_stage_c5_recorded_build.py`:

```python
from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings
```

Replace:

```python
    _merge_recorded_layout_settings(params, recorded_analysis)
```

with:

```python
    merge_recorded_layout_settings(params, recorded_analysis)
```

Delete the private `_merge_recorded_layout_settings()` function from `scripts/run_stage_c5_recorded_build.py`.

- [ ] **Step 4: Run helper and existing recorded runner tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_recorded_settings.py tests/test_stage_c5_recorded_build_runner.py -q
```

Expected: pass.

- [ ] **Step 5: Commit helper refactor**

```bash
git add src/aedt_agent/layout/recorded_settings.py tests/test_recorded_settings.py scripts/run_stage_c5_recorded_build.py
git commit -m "refactor: share recorded layout settings merge"
```

- [ ] **Step 6: Write failing CLI fake test**

Create `tests/test_stage_c5_local_cut_build.py`:

```python
import json
import subprocess
import sys


def test_run_stage_c5_local_cut_build_fake_requires_and_records_bbox(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(
        json.dumps(
            {
                "layout_file": str(layout_file),
                "signal_nets": "SIG_*",
                "reference_nets": "GND",
                "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
                "uniform_line_port_hint": {"side": "right", "layer": "ART03", "port_type": "edge"},
            }
        ),
        encoding="utf-8",
    )
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps({"setup": {}, "sweep": {}, "hfss_extents": {}, "design_options": {}}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_stage_c5_local_cut_build.py",
            "--adapter",
            "fake",
            "--params",
            str(params),
            "--recorded-analysis",
            str(analysis),
            "--run-dir",
            str(tmp_path / "run"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads((tmp_path / "run" / "stage_c5_local_cut_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "succeeded"
    assert summary["local_cut_region"]["unit"] == "mil"
    assert summary["local_cut_polygon"]["points"][0] == [1.0, 2.0]
    assert summary["layout_solve"]["status"] == "skipped"
```

- [ ] **Step 7: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c5_local_cut_build.py -q
```

Expected: FAIL because script does not exist.

- [ ] **Step 8: Implement CLI**

Create `scripts/run_stage_c5_local_cut_build.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.import_cutout import build_import_cutout_request, run_fake_import_cutout, run_real_import_cutout
from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region
from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings


def main() -> None:
    args = _parse_args()
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    params = _read_json_object(args.params)
    recorded_analysis = _read_json_object(args.recorded_analysis)
    region = parse_local_cut_region(params.get("local_cut_region"))
    params["local_cut_region"] = region
    params["artifact_dir"] = str(run_dir)
    params["solve_enabled"] = False
    merge_recorded_layout_settings(params, recorded_analysis)
    request = build_import_cutout_request(params)
    if args.adapter == "fake":
        summary = run_fake_import_cutout(request)
        summary.setdefault("layout_solve", {"status": "skipped", "reason": "model_build_only"})
    else:
        summary = run_real_import_cutout(
            request,
            aedt_version=args.aedt_version,
            cadence_launcher=args.cadence_launcher,
            ansysem_root=args.ansysem_root,
            awp_root=args.awp_root,
            non_graphical=args.non_graphical,
        )
    summary["stage_c5_mode"] = "local_cut_build_only"
    summary["local_cut_region"] = region
    summary["local_cut_polygon"] = bbox_to_polygon(region)
    (run_dir / "stage_c5_local_cut_params.json").write_text(json.dumps(params, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "stage_c5_local_cut_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Stage C.5 local cut build: {summary.get('status')}")
    print(f"Summary: {run_dir / 'stage_c5_local_cut_summary.json'}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage C.5 local cut build-only workflow.")
    parser.add_argument("--adapter", choices=["real", "fake"], default="real")
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--recorded-analysis", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--aedt-version", default="2026.1")
    parser.add_argument("--cadence-launcher", default="")
    parser.add_argument("--ansysem-root", default="")
    parser.add_argument("--awp-root", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--non-graphical", dest="non_graphical", action="store_true")
    mode.add_argument("--graphical", dest="non_graphical", action="store_false")
    parser.set_defaults(non_graphical=False)
    return parser.parse_args()


def _read_json_object(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Update spec command**

In `docs/superpowers/specs/2026-05-31-stage-c5-local-cut-optimization-cell-design.md`, add:

```bash
.venv/bin/python scripts/run_stage_c5_local_cut_build.py \
  --adapter real \
  --params D:/runs/stage_c5_local_cut_params.json \
  --recorded-analysis D:/runs/recorded_workflow_analysis.json \
  --run-dir D:/runs/stage_c5_local_cut_build \
  --graphical
```

- [ ] **Step 10: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_recorded_settings.py tests/test_stage_c5_local_cut_build.py tests/test_stage_c5_recorded_build_runner.py -q
```

Expected: pass.

- [ ] **Step 11: Commit**

```bash
git add scripts/run_stage_c5_local_cut_build.py tests/test_stage_c5_local_cut_build.py docs/superpowers/specs/2026-05-31-stage-c5-local-cut-optimization-cell-design.md
git commit -m "feat: add Stage C5 local cut build CLI"
```

## Task 6: Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused test suite**

```bash
.venv/bin/python -m pytest tests/test_local_cut.py tests/test_layout_ports.py tests/test_stage_c5_local_cut_build.py tests/test_import_cutout_demo.py::test_real_import_cutout_uses_bbox_polygon_for_local_cut -q
```

Expected: all pass.

Also run the helper reuse test:

```bash
.venv/bin/python -m pytest tests/test_recorded_settings.py tests/test_stage_c5_recorded_build_runner.py -q
```

Expected: all pass.

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all pass.

- [ ] **Step 3: Run contract stabilization check**

```bash
.venv/bin/python scripts/check_contract_stabilization.py
```

Expected: exits 0 and reports no default layout nodes.

- [ ] **Step 4: Run whitespace check**

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Optional real build-only smoke**

Only run after fake tests pass and when AEDT is available:

```bash
.venv/bin/python scripts/run_stage_c5_local_cut_build.py \
  --adapter real \
  --params /home/zzmjay/work/brd/stage_c5_local_cut_params.json \
  --recorded-analysis /home/zzmjay/work/brd/stage_c5_build_arc_ext_20260531_223516/stage_c5_recorded_analysis.json \
  --run-dir /home/zzmjay/work/brd/stage_c5_local_cut_build_manual \
  --aedt-version 2026.1 \
  --cadence-launcher /home/zzmjay/code/start_aedt_cadence.sh \
  --graphical
```

Expected: local cut summary is written, solve remains skipped. If bbox cutout fails, do not remove failure; preserve logs and report the reason.

## Done Criteria

- Local bbox is mandatory and validated.
- Bbox polygon is generated and preserved in params, action records, and summary.
- PyEDB local cut path attempts polygon/bbox cutout and never silently falls back to whole-channel cutout.
- Uniform-line edge candidates can be reported near a bbox side.
- Local cut CLI can run fake build-only and write summary artifacts.
- Existing recorded build and import-cutout tests still pass.
