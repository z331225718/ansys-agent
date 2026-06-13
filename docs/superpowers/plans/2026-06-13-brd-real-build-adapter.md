# BRD Real Build Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `brd.local_cut.build` 增加 `adapter_mode=real_build`，让新 Agent 能通过正式 infrastructure adapter 生成真实 PyEDB/HFSS 3D Layout build-only 工程，但默认不求解。

**Architecture:** 新能力放在 `aedt_agent.infrastructure.brd_real_build`，worker 只调用 adapter 合同，不 import PyEDB/HFSS，也不依赖 `aedt_agent.v0`。单元测试用 fake EDB/HFSS class 覆盖真实调用顺序；真实 AEDT smoke 只在 `RUN_REAL_AEDT=1` 时运行。

**Tech Stack:** Python 3.11+、`dataclasses`、`pathlib`、`json`、现有 `aedt_agent.layout` helpers、现有 Agent Runtime/CLI、`pytest`。

---

## 当前基线

- 设计文档：`docs/superpowers/specs/2026-06-13-brd-real-build-adapter-design.md`
- 当前 worker：`src/aedt_agent/agent/workers/brd_local_cut.py`
- 当前 CLI：`src/aedt_agent/agent/cli.py`
- 旧真实链路只作为参考，不允许新 Agent 直接 import：`src/aedt_agent/v0/demo/import_cutout.py`
- 全量测试当前有 9 个已登记历史失败，本计划不得扩大失败集合。

## 目标文件结构

- Create: `src/aedt_agent/infrastructure/brd_real_build.py`
  定义 build-only request/result/environment/adapter，封装真实 PyEDB/HFSS 3D Layout 调用。

- Modify: `src/aedt_agent/infrastructure/__init__.py`
  导出 real build adapter 合同。

- Modify: `src/aedt_agent/agent/workers/brd_local_cut.py`
  扩展 job input，支持 `adapter_mode=real_build`，复用统一 summary/workflow/evidence 输出。

- Modify: `src/aedt_agent/agent/cli.py`
  扩展 `mission create` 的真实 build-only 参数，并把 recorded analysis 合并进 job payload。

- Create: `tests/test_infrastructure_brd_real_build.py`
  fake EDB/HFSS 单元测试。

- Modify: `tests/test_agent_brd_local_cut_worker.py`
  覆盖 worker 调用 real build adapter 和 solve 禁止逻辑。

- Modify: `tests/test_agent_cli_brd_mission.py`
  覆盖 CLI real_build payload。

- Create: `tests/test_agent_brd_real_build_smoke.py`
  opt-in 真实 AEDT build-only smoke。

---

## Task 1：定义 real build adapter 合同

**Files:**
- Create: `tests/test_infrastructure_brd_real_build.py`
- Create: `src/aedt_agent/infrastructure/brd_real_build.py`
- Modify: `src/aedt_agent/infrastructure/__init__.py`

- [ ] **Step 1：写 adapter 合同失败测试**

Create `tests/test_infrastructure_brd_real_build.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.infrastructure.brd_real_build import (
    BrdRealBuildAdapter,
    BrdRealBuildRequest,
    RealAedtEnvironment,
)


def _request(tmp_path: Path, **overrides) -> BrdRealBuildRequest:
    layout = tmp_path / "case.brd"
    layout.write_text("brd", encoding="utf-8")
    values = {
        "layout_file": layout,
        "artifact_dir": tmp_path / "artifacts",
        "signal_nets": ["56G_TX0_P", "56G_TX0_N"],
        "reference_nets": ["GND"],
        "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        "recorded_layout_settings": {},
        "environment": RealAedtEnvironment(version="2026.1"),
    }
    values.update(overrides)
    return BrdRealBuildRequest(**values)


def test_real_build_request_requires_existing_layout(tmp_path):
    missing = tmp_path / "missing.brd"

    with pytest.raises(FileNotFoundError, match="layout_file not found"):
        BrdRealBuildAdapter().run(_request(tmp_path, layout_file=missing))


def test_real_build_rejects_solve_enabled_in_build_only_phase(tmp_path):
    with pytest.raises(ValueError, match="solve_enabled is not supported"):
        BrdRealBuildAdapter().run(_request(tmp_path, solve_enabled=True))


def test_real_build_request_accepts_graphical_environment(tmp_path):
    request = _request(tmp_path, environment=RealAedtEnvironment(version="2026.1", non_graphical=False))

    assert request.environment.version == "2026.1"
    assert request.environment.non_graphical is False
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_infrastructure_brd_real_build.py -q
```

Expected: FAIL，原因是 `aedt_agent.infrastructure.brd_real_build` 尚不存在。

- [ ] **Step 3：实现 dataclass 合同和基础校验**

Create `src/aedt_agent/infrastructure/brd_real_build.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region


@dataclass(frozen=True)
class RealAedtEnvironment:
    version: str = "2026.1"
    non_graphical: bool = False
    edb_backend: str = "auto"
    cadence_launcher: str = ""
    ansysem_root: str = ""
    awp_root: str = ""


@dataclass(frozen=True)
class BrdRealBuildRequest:
    layout_file: Path
    artifact_dir: Path
    signal_nets: list[str]
    reference_nets: list[str]
    local_cut_region: dict[str, Any]
    recorded_layout_settings: dict[str, Any] = field(default_factory=dict)
    environment: RealAedtEnvironment = field(default_factory=RealAedtEnvironment)
    stackup_xml: Path | None = None
    uniform_line_port_hint: dict[str, Any] = field(default_factory=dict)
    target_metrics: list[dict[str, Any]] = field(default_factory=list)
    approved_port_selection: dict[str, Any] = field(default_factory=dict)
    solve_enabled: bool = False


@dataclass(frozen=True)
class BrdRealBuildResult:
    summary: dict[str, Any]


class BrdRealBuildAdapter:
    def __init__(
        self,
        *,
        edb_factory: Callable[..., Any] | None = None,
        hfss3dlayout_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._edb_factory = edb_factory
        self._hfss3dlayout_factory = hfss3dlayout_factory

    def run(self, request: BrdRealBuildRequest) -> BrdRealBuildResult:
        if request.solve_enabled:
            raise ValueError("solve_enabled is not supported by real_build; run a solve mission after model approval")
        if not request.layout_file.exists():
            raise FileNotFoundError(f"layout_file not found: {request.layout_file}")
        if request.stackup_xml is not None and not request.stackup_xml.exists():
            raise FileNotFoundError(f"stackup_xml not found: {request.stackup_xml}")
        region = parse_local_cut_region(request.local_cut_region)
        request.artifact_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "status": "succeeded",
            "adapter": "real_pyedb_hfss3dlayout_build_only",
            "layout_file": str(request.layout_file),
            "source_edb_path": "",
            "edb_path": str(request.artifact_dir / f"{request.layout_file.stem}_cutout.aedb"),
            "aedt_project": str(request.artifact_dir / f"{request.layout_file.stem}_cutout_hfss.aedt"),
            "signal_nets": list(request.signal_nets),
            "reference_nets": list(request.reference_nets),
            "local_cut_region": region,
            "local_cut_polygon": bbox_to_polygon(region),
            "port_candidates": {"status": "not_evaluated", "candidate_count": 0},
            "port_execution": {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []},
            "layout_setup": {},
            "layout_solve": {"status": "skipped", "reason": "model_review_only"},
            "layout_reports": {},
            "recorded_layout_settings": _recorded_settings_summary(request.recorded_layout_settings),
            "target_metrics": list(request.target_metrics),
            "steps": [],
        }
        return BrdRealBuildResult(summary=summary)

    def _edb_class(self) -> Callable[..., Any]:
        if self._edb_factory is not None:
            return self._edb_factory
        from pyedb import Edb

        return Edb

    def _hfss3dlayout_class(self) -> Callable[..., Any]:
        if self._hfss3dlayout_factory is not None:
            return self._hfss3dlayout_factory
        from ansys.aedt.core import Hfss3dLayout

        return Hfss3dLayout


def _recorded_settings_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "hfss_extents": dict(value.get("hfss_extents") or {}),
        "design_options": dict(value.get("design_options") or {}),
        "setup_options": dict(value.get("setup_options") or {}),
        "setup_advanced_settings": dict(value.get("setup_advanced_settings") or {}),
        "setup_curve_approximation": dict(value.get("setup_curve_approximation") or {}),
        "sweep_options": dict(value.get("sweep_options") or {}),
    }
```

Modify `src/aedt_agent/infrastructure/__init__.py`:

```python
"""Persistence, process, artifact, and AEDT infrastructure adapters."""

from aedt_agent.infrastructure.brd_real_build import (
    BrdRealBuildAdapter,
    BrdRealBuildRequest,
    BrdRealBuildResult,
    RealAedtEnvironment,
)
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore

__all__ = [
    "BrdRealBuildAdapter",
    "BrdRealBuildRequest",
    "BrdRealBuildResult",
    "RealAedtEnvironment",
    "SQLiteMissionStore",
]
```

- [ ] **Step 4：运行 adapter 合同测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_infrastructure_brd_real_build.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 adapter 合同**

```powershell
git add src/aedt_agent/infrastructure/brd_real_build.py src/aedt_agent/infrastructure/__init__.py tests/test_infrastructure_brd_real_build.py
git commit -m "feat: define brd real build adapter contract"
```

---

## Task 2：实现 build-only PyEDB/HFSS 调用顺序

**Files:**
- Modify: `tests/test_infrastructure_brd_real_build.py`
- Modify: `src/aedt_agent/infrastructure/brd_real_build.py`

- [ ] **Step 1：写 fake EDB/HFSS build-only 测试**

Append to `tests/test_infrastructure_brd_real_build.py`:

```python
class FakeNets:
    def __init__(self) -> None:
        self.nets = {"56G_TX0_P": object(), "56G_TX0_N": object(), "GND": object()}


class FakeEdb:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *, edbpath: str, version: str, grpc: bool | None) -> None:
        self.edbpath = edbpath
        self.version = version
        self.grpc = grpc
        self.nets = FakeNets()

    def cutout(self, **kwargs):
        FakeEdb.calls.append(("cutout", kwargs))
        Path(kwargs["output_aedb_path"]).mkdir(parents=True)
        return kwargs["custom_extent"]

    def save(self) -> None:
        FakeEdb.calls.append(("save", {}))

    def close(self) -> None:
        FakeEdb.calls.append(("close", {}))


class FakeEditor:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls

    def ImportStackupXML(self, path: str) -> None:
        self.calls.append(("ImportStackupXML", path))


class FakeDesign:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls

    def EditHfssExtents(self, values: list[object]) -> None:
        self.calls.append(("EditHfssExtents", values))

    def DesignOptions(self, values: list[object], flags: int) -> None:
        self.calls.append(("DesignOptions", {"values": values, "flags": flags}))


class FakeSweep:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.props = {}
        self.calls = calls
        self.name = "Sweep1"

    def update(self) -> None:
        self.calls.append(("sweep_update", dict(self.props)))


class FakeHfss3dLayout:
    calls: list[tuple[str, object]] = []

    def __init__(self, *, project: str, version: str, non_graphical: bool, new_desktop: bool, close_on_exit: bool) -> None:
        self.project_file = str(Path(project).with_suffix(".aedt"))
        self.modeler = type("Modeler", (), {"oeditor": FakeEditor(self.calls)})()
        self.odesign = FakeDesign(self.calls)

    def create_setup(self, *, name: str, props: dict):
        self.calls.append(("create_setup", {"name": name, "props": props}))
        return type("Setup", (), {"name": name})()

    def create_linear_count_sweep(self, setup_name, unit, start, stop, count, *, name, sweep_type, use_q3d_for_dc, interpolation_max_solutions, save_fields):
        self.calls.append(("create_linear_count_sweep", {"setup_name": setup_name, "unit": unit, "start": start, "stop": stop, "count": count}))
        return FakeSweep(self.calls)

    def analyze_setup(self, *args, **kwargs):
        raise AssertionError("build-only adapter must not solve")

    def save_project(self) -> None:
        self.calls.append(("save_project", self.project_file))

    def release_desktop(self, *args, **kwargs) -> None:
        self.calls.append(("release_desktop", kwargs))


def test_real_build_uses_pyedb_cutout_polygon_and_saves_hfss_project(tmp_path):
    FakeEdb.calls = []
    FakeHfss3dLayout.calls = []
    stackup = tmp_path / "stackup.xml"
    stackup.write_text("<stackup />", encoding="utf-8")
    request = _request(
        tmp_path,
        stackup_xml=stackup,
        recorded_layout_settings={
            "hfss_extents": {"AirHorExt": {"Ext": "3mm"}},
            "design_options": {"MeshingMethod": "PhiPlus"},
            "setup_options": {"Extra": True},
            "setup_advanced_settings": {"PhiPlusMesher": True},
            "setup_curve_approximation": {"ArcAngle": "10deg"},
            "sweep_options": {"MaxSolutions": 2500, "UseQ3DForDC": True},
        },
    )
    adapter = BrdRealBuildAdapter(edb_factory=FakeEdb, hfss3dlayout_factory=FakeHfss3dLayout)

    result = adapter.run(request)

    cutout_kwargs = FakeEdb.calls[0][1]
    assert cutout_kwargs["extent_type"] == "Polygon"
    assert cutout_kwargs["custom_extent"][0] == [1.0, 2.0]
    assert cutout_kwargs["signal_nets"] == ["56G_TX0_P", "56G_TX0_N"]
    assert cutout_kwargs["reference_nets"] == ["GND"]
    assert result.summary["layout_solve"] == {"status": "skipped", "reason": "model_review_only"}
    call_names = [name for name, _ in FakeHfss3dLayout.calls]
    assert "ImportStackupXML" in call_names
    assert "EditHfssExtents" in call_names
    assert "DesignOptions" in call_names
    assert "create_setup" in call_names
    assert "create_linear_count_sweep" in call_names
    assert "save_project" in call_names
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_infrastructure_brd_real_build.py::test_real_build_uses_pyedb_cutout_polygon_and_saves_hfss_project -q
```

Expected: FAIL，原因是 adapter 当前只返回 skeleton summary，没有调用 fake EDB/HFSS。

- [ ] **Step 3：实现最小 build-only 调用链**

Modify `src/aedt_agent/infrastructure/brd_real_build.py` by adding the imports below and replacing the existing `BrdRealBuildAdapter.run` method with this body:

```python
import shutil
import tempfile

from aedt_agent.layout.import_cutout import expand_net_patterns
```

```python
def run(self, request: BrdRealBuildRequest) -> BrdRealBuildResult:
    if request.solve_enabled:
        raise ValueError("solve_enabled is not supported by real_build; run a solve mission after model approval")
    if not request.layout_file.exists():
        raise FileNotFoundError(f"layout_file not found: {request.layout_file}")
    if request.stackup_xml is not None and not request.stackup_xml.exists():
        raise FileNotFoundError(f"stackup_xml not found: {request.stackup_xml}")

    region = parse_local_cut_region(request.local_cut_region)
    polygon = bbox_to_polygon(region)
    request.artifact_dir.mkdir(parents=True, exist_ok=True)
    source_dir = Path(tempfile.mkdtemp(prefix=f"{request.layout_file.stem}_source_", dir=request.artifact_dir))
    source_layout = source_dir / request.layout_file.name
    shutil.copy2(request.layout_file, source_layout)
    cutout_aedb = request.artifact_dir / f"{request.layout_file.stem}_cutout.aedb"
    hfss_aedb = request.artifact_dir / f"{request.layout_file.stem}_cutout_hfss.aedb"
    project_path = request.artifact_dir / f"{request.layout_file.stem}_cutout_hfss.aedt"
    if cutout_aedb.exists():
        shutil.rmtree(cutout_aedb)
    if hfss_aedb.exists():
        shutil.rmtree(hfss_aedb)

    edb = self._edb_class()(edbpath=str(source_layout), version=request.environment.version, grpc=_grpc_mode(request.environment.edb_backend))
    try:
        available_nets = sorted(getattr(getattr(edb, "nets", None), "nets", {}).keys())
        signal_nets = expand_net_patterns(request.signal_nets, available_nets, fuzzy=True)
        reference_nets = expand_net_patterns(request.reference_nets, available_nets)
        if not signal_nets:
            raise ValueError(f"no signal nets matched {request.signal_nets}")
        if not reference_nets:
            raise ValueError(f"no reference nets matched {request.reference_nets}")
        extent_points = edb.cutout(
            signal_nets=signal_nets,
            reference_nets=reference_nets,
            extent_type="Polygon",
            expansion_size=0.0,
            output_aedb_path=str(cutout_aedb),
            use_pyaedt_cutout=True,
            number_of_threads=1,
            open_cutout_at_end=False,
            custom_extent=polygon["points"],
        )
        if not hfss_aedb.exists():
            shutil.copytree(cutout_aedb, hfss_aedb)
        app = self._hfss3dlayout_class()(
            project=str(hfss_aedb),
            version=request.environment.version,
            non_graphical=request.environment.non_graphical,
            new_desktop=True,
            close_on_exit=request.environment.non_graphical,
        )
        try:
            stackup_applied = _import_stackup(app, request.stackup_xml)
            recorded = _recorded_settings_summary(request.recorded_layout_settings)
            _apply_recorded_hfss_extents(app, recorded["hfss_extents"])
            _apply_recorded_design_options(app, recorded["design_options"])
            layout_setup = _create_build_only_setup(app, recorded)
            app.save_project()
            project_file = Path(str(getattr(app, "project_file", project_path)))
        finally:
            app.release_desktop(close_projects=False, close_desktop=False)
        summary = _summary(
            request,
            source_edb_path=str(getattr(edb, "edbpath", source_layout)),
            edb_path=cutout_aedb,
            aedt_project=project_file,
            signal_nets=signal_nets,
            reference_nets=reference_nets,
            region=region,
            polygon=polygon,
            stackup_applied=stackup_applied,
            layout_setup=layout_setup,
            recorded_settings=recorded,
            cutout_extent_points=len(extent_points) if extent_points else 0,
        )
        return BrdRealBuildResult(summary=summary)
    finally:
        _close_edb(edb)
```

Also add helper functions in the same file:

```python
def _grpc_mode(value: str) -> bool | None:
    if value == "auto":
        return None
    if value == "grpc":
        return True
    if value == "dotnet":
        return False
    raise ValueError(f"unsupported edb_backend: {value}")


def _import_stackup(app: Any, stackup_xml: Path | None) -> bool:
    if stackup_xml is None:
        return False
    app.modeler.oeditor.ImportStackupXML(str(stackup_xml))
    return True


def _apply_recorded_hfss_extents(app: Any, options: dict[str, Any]) -> None:
    design = getattr(app, "odesign", None)
    if options and design is not None and hasattr(design, "EditHfssExtents"):
        design.EditHfssExtents(_aedt_options_list("HfssExportInfo", options))


def _apply_recorded_design_options(app: Any, options: dict[str, Any]) -> None:
    design = getattr(app, "odesign", None)
    if options and design is not None and hasattr(design, "DesignOptions"):
        design.DesignOptions(_aedt_options_list("options", options), 0)


def _create_build_only_setup(app: Any, recorded: dict[str, Any]) -> dict[str, Any]:
    setup_name = "Setup1"
    sweep_name = "Sweep1"
    props = {"AdaptiveSettings": {"DoAdaptive": True, "SaveFields": False}}
    props.update(recorded["setup_options"])
    if recorded["setup_advanced_settings"]:
        props["AdvancedSettings"] = recorded["setup_advanced_settings"]
    if recorded["setup_curve_approximation"]:
        props["CurveApproximation"] = recorded["setup_curve_approximation"]
    setup = app.create_setup(name=setup_name, props=props)
    setup_name = getattr(setup, "name", setup_name)
    sweep = app.create_linear_count_sweep(
        setup_name,
        "GHz",
        0.0,
        67.0,
        1341,
        name=sweep_name,
        sweep_type="Interpolating",
        use_q3d_for_dc=bool(recorded["sweep_options"].get("UseQ3DForDC", False)),
        interpolation_max_solutions=int(recorded["sweep_options"].get("MaxSolutions", 2500)),
        save_fields=False,
    )
    if hasattr(sweep, "props") and isinstance(sweep.props, dict):
        sweep.props.update(recorded["sweep_options"])
        if hasattr(sweep, "update"):
            sweep.update()
    sweep_name = getattr(sweep, "name", sweep_name)
    return {"setup_name": setup_name, "sweep_name": sweep_name, "mode": "build_only", "sweep_start": "0GHz", "sweep_stop": "67GHz"}


def _aedt_options_list(name: str, options: dict[str, Any]) -> list[Any]:
    output: list[Any] = [f"NAME:{name}"]
    for key, value in options.items():
        output.extend([f"{key}:=", _aedt_option_value(value)])
    return output


def _aedt_option_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    output: list[Any] = []
    for key, item in value.items():
        output.extend([f"{key}:=", item])
    return output


def _close_edb(edb: Any) -> None:
    close = getattr(edb, "close", None)
    if callable(close):
        close()
```

Replace skeleton summary creation with `_summary(...)`:

```python
def _summary(
    request: BrdRealBuildRequest,
    *,
    source_edb_path: str,
    edb_path: Path,
    aedt_project: Path,
    signal_nets: list[str],
    reference_nets: list[str],
    region: dict[str, Any],
    polygon: dict[str, Any],
    stackup_applied: bool,
    layout_setup: dict[str, Any],
    recorded_settings: dict[str, Any],
    cutout_extent_points: int,
) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "adapter": "real_pyedb_hfss3dlayout_build_only",
        "layout_file": str(request.layout_file),
        "source_edb_path": source_edb_path,
        "edb_path": str(edb_path),
        "aedt_project": str(aedt_project),
        "signal_nets": signal_nets,
        "reference_nets": reference_nets,
        "local_cut_region": region,
        "local_cut_polygon": polygon,
        "stackup_xml": str(request.stackup_xml) if request.stackup_xml else "",
        "stackup_applied": stackup_applied,
        "cutout_extent_points": cutout_extent_points,
        "port_candidates": {"status": "not_evaluated", "candidate_count": 0},
        "port_execution": {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []},
        "layout_setup": layout_setup,
        "layout_solve": {"status": "skipped", "reason": "model_review_only"},
        "layout_reports": {},
        "recorded_layout_settings": recorded_settings,
        "target_metrics": list(request.target_metrics),
        "steps": [
            {"id": "import_layout_file", "label": "Open BRD/MCM with PyEDB", "status": "succeeded"},
            {"id": "select_layout_nets", "label": "Select Nets", "status": "succeeded"},
            {"id": "create_layout_cutout", "label": "Create PyEDB Cutout", "status": "succeeded"},
            {"id": "create_layout_setup", "label": "Create Setup/Sweep", "status": "succeeded"},
            {"id": "validate_layout_model", "label": "Save build-only model", "status": "succeeded"},
        ],
    }
```

- [ ] **Step 4：运行 adapter build-only 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_infrastructure_brd_real_build.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 build-only 调用链**

```powershell
git add src/aedt_agent/infrastructure/brd_real_build.py tests/test_infrastructure_brd_real_build.py
git commit -m "feat: build brd local cut model without solving"
```

---

## Task 3：让 BRD worker 接入 `adapter_mode=real_build`

**Files:**
- Modify: `tests/test_agent_brd_local_cut_worker.py`
- Modify: `src/aedt_agent/agent/workers/brd_local_cut.py`

- [ ] **Step 1：写 worker real_build 测试**

Append to `tests/test_agent_brd_local_cut_worker.py`:

```python
class FakeRealBuildAdapter:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return type(
            "Result",
            (),
            {
                "summary": {
                    "status": "succeeded",
                    "adapter": "real_pyedb_hfss3dlayout_build_only",
                    "layout_file": str(request.layout_file),
                    "signal_nets": request.signal_nets,
                    "reference_nets": request.reference_nets,
                    "local_cut_region": request.local_cut_region,
                    "local_cut_polygon": {"type": "polygon", "unit": "mil", "points": [[1.0, 2.0]]},
                    "port_candidates": {"status": "ready", "candidate_count": 1},
                    "port_execution": {"status": "skipped"},
                    "layout_setup": {"setup_name": "Setup1", "sweep_name": "Sweep1"},
                    "layout_solve": {"status": "skipped", "reason": "model_review_only"},
                    "layout_reports": {},
                    "recorded_layout_settings": {},
                    "edb_path": str(request.artifact_dir / "case_cutout.aedb"),
                    "aedt_project": str(request.artifact_dir / "case_cutout_hfss.aedt"),
                    "target_metrics": request.target_metrics,
                    "steps": [],
                }
            },
        )()


def test_brd_local_cut_worker_can_use_real_build_adapter(tmp_path):
    fake_adapter = FakeRealBuildAdapter()
    job = _job(
        tmp_path,
        adapter_mode="real_build",
        recorded_layout_settings={"sweep_options": {"MaxSolutions": 2500}},
        aedt={"version": "2026.1", "non_graphical": False, "edb_backend": "auto"},
    )

    result = run_brd_local_cut_worker(job, WorkerContext("worker-1"), real_build_adapter=fake_adapter)

    summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
    assert result["status"] == "model_review"
    assert summary["adapter"] == "real_pyedb_hfss3dlayout_build_only"
    assert summary["layout_solve"]["status"] == "skipped"
    assert result["evidence_summary"]["raw_sparameters"] == "artifact_only"
    assert fake_adapter.requests[0].environment.version == "2026.1"


def test_brd_local_cut_worker_rejects_real_build_solve_enabled(tmp_path):
    job = _job(tmp_path, adapter_mode="real_build", solve_enabled=True)

    with pytest.raises(ValueError, match="solve_enabled"):
        run_brd_local_cut_worker(job, WorkerContext("worker-1"), real_build_adapter=FakeRealBuildAdapter())
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_local_cut_worker.py::test_brd_local_cut_worker_can_use_real_build_adapter tests\test_agent_brd_local_cut_worker.py::test_brd_local_cut_worker_rejects_real_build_solve_enabled -q
```

Expected: FAIL，原因是 worker 还不支持新增参数和 `real_build_adapter` 注入。

- [ ] **Step 3：扩展 worker input 和 real_build 分支**

Modify `build_brd_local_cut_job_input` signature in `src/aedt_agent/agent/workers/brd_local_cut.py`:

```python
def build_brd_local_cut_job_input(
    *,
    layout_file: str | Path,
    signal_nets: list[str],
    reference_nets: list[str],
    local_cut_region: dict[str, Any] | None,
    artifact_dir: str | Path,
    target_metrics: list[dict[str, Any]] | None = None,
    port_candidates: dict[str, Any] | None = None,
    approved_port_selection: dict[str, Any] | None = None,
    adapter_mode: str = "deterministic",
    stackup_xml: str | Path | None = None,
    recorded_layout_settings: dict[str, Any] | None = None,
    uniform_line_port_hint: dict[str, Any] | None = None,
    aedt: dict[str, Any] | None = None,
    solve_enabled: bool = False,
) -> dict[str, Any]:
    return {
        "adapter_mode": adapter_mode,
        "layout_file": str(layout_file),
        "signal_nets": list(signal_nets),
        "reference_nets": list(reference_nets),
        "local_cut_region": local_cut_region,
        "artifact_dir": str(artifact_dir),
        "target_metrics": list(target_metrics or []),
        "port_candidates": port_candidates or {"status": "ready", "recommended_endpoints": []},
        "approved_port_selection": approved_port_selection or {},
        "stackup_xml": str(stackup_xml) if stackup_xml else "",
        "recorded_layout_settings": dict(recorded_layout_settings or {}),
        "uniform_line_port_hint": dict(uniform_line_port_hint or {}),
        "aedt": dict(aedt or {}),
        "solve_enabled": bool(solve_enabled),
    }
```

Modify `run_brd_local_cut_worker` signature and body:

```python
def run_brd_local_cut_worker(
    job: JobRecord,
    context: WorkerContext,
    *,
    real_build_adapter: Any | None = None,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    region = parse_local_cut_region(payload.get("local_cut_region"))
    artifact_dir = Path(str(payload["artifact_dir"]))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if payload.get("adapter_mode", "deterministic") == "real_build":
        summary = _real_build_summary(payload, region, real_build_adapter)
        approval_required = _approval_required(dict(summary.get("port_candidates") or {}))
    else:
        port_candidates = dict(payload.get("port_candidates") or {})
        approval_required = _approval_required(port_candidates)
        summary = _summary_payload(job, context, payload, region, approval_required)
```

Add helper:

```python
def _real_build_summary(payload: dict[str, Any], region: dict[str, Any], adapter: Any | None) -> dict[str, Any]:
    from aedt_agent.infrastructure import BrdRealBuildAdapter, BrdRealBuildRequest, RealAedtEnvironment

    if payload.get("solve_enabled"):
        raise ValueError("solve_enabled is not supported by brd.local_cut.build real_build")
    aedt = dict(payload.get("aedt") or {})
    request = BrdRealBuildRequest(
        layout_file=Path(str(payload["layout_file"])),
        artifact_dir=Path(str(payload["artifact_dir"])),
        signal_nets=list(payload.get("signal_nets") or []),
        reference_nets=list(payload.get("reference_nets") or []),
        local_cut_region=region,
        stackup_xml=Path(str(payload["stackup_xml"])) if payload.get("stackup_xml") else None,
        recorded_layout_settings=dict(payload.get("recorded_layout_settings") or {}),
        uniform_line_port_hint=dict(payload.get("uniform_line_port_hint") or {}),
        target_metrics=list(payload.get("target_metrics") or []),
        approved_port_selection=dict(payload.get("approved_port_selection") or {}),
        solve_enabled=False,
        environment=RealAedtEnvironment(
            version=str(aedt.get("version") or "2026.1"),
            non_graphical=bool(aedt.get("non_graphical", False)),
            edb_backend=str(aedt.get("edb_backend") or "auto"),
            cadence_launcher=str(aedt.get("cadence_launcher") or ""),
            ansysem_root=str(aedt.get("ansysem_root") or ""),
            awp_root=str(aedt.get("awp_root") or ""),
        ),
    )
    runner = adapter or BrdRealBuildAdapter()
    return dict(runner.run(request).summary)
```

Keep the existing write-output section after summary creation:

```python
summary_path = artifact_dir / "brd_local_cut_summary.json"
workflow_path = artifact_dir / "workflow_run.json"
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
import_cutout_summary_to_workflow_run(summary).write_json(workflow_path)
```

Extend `_bounded_evidence_summary` with adapter and real build fields:

```python
"adapter": summary.get("adapter", ""),
"port_execution_status": summary.get("port_execution", {}).get("status", "unknown"),
"setup_name": summary.get("layout_setup", {}).get("setup_name", ""),
"edb_path": summary.get("edb_path", ""),
```

- [ ] **Step 4：运行 worker 测试和 architecture 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_local_cut_worker.py tests\test_architecture_dependencies.py -q
```

Expected: PASS，且 `test_agent_runtime_does_not_depend_on_v0` 仍通过。

- [ ] **Step 5：提交 worker real_build 接入**

```powershell
git add src/aedt_agent/agent/workers/brd_local_cut.py tests/test_agent_brd_local_cut_worker.py
git commit -m "feat: route brd worker to real build adapter"
```

---

## Task 4：扩展 CLI 创建 real_build Mission

**Files:**
- Modify: `tests/test_agent_cli_brd_mission.py`
- Modify: `src/aedt_agent/agent/cli.py`

- [ ] **Step 1：写 CLI real_build payload 测试**

Append to `tests/test_agent_cli_brd_mission.py`:

```python
def test_cli_create_brd_real_build_payload_with_recorded_analysis(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    stackup = tmp_path / "stackup.xml"
    stackup.write_text("<stackup />", encoding="utf-8")
    recorded = tmp_path / "recorded.json"
    recorded.write_text(
        json.dumps(
            {
                "hfss_extents": {"AirHorExt": {"Ext": "3mm"}},
                "design_options": {"MeshingMethod": "PhiPlus"},
                "setup": {"options": {"AdaptiveSettings": {"MaxPasses": 8}}},
                "sweep": {"options": {"MaxSolutions": 2500, "UseQ3DForDC": True}},
            }
        ),
        encoding="utf-8",
    )

    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "真实 build-only",
        "--brd-local-cut",
        "--adapter-mode",
        "real_build",
        "--layout-file",
        str(layout_file),
        "--stackup-xml",
        str(stackup),
        "--signal-net",
        "56G_TX0_P",
        "--reference-net",
        "GND",
        "--bbox",
        "mil,1,2,3,4",
        "--recorded-analysis",
        str(recorded),
        "--aedt-version",
        "2026.1",
        "--graphical",
    )
    mission_id = json.loads(created.stdout)["mission_id"]
    status = _run(tmp_path, "mission", "status", "--mission-id", mission_id)

    payload = json.loads(status.stdout)["jobs"][0]["input_payload"]
    assert payload["adapter_mode"] == "real_build"
    assert payload["stackup_xml"] == str(stackup)
    assert payload["recorded_layout_settings"]["hfss_extents"]["AirHorExt"]["Ext"] == "3mm"
    assert payload["recorded_layout_settings"]["sweep_options"]["MaxSolutions"] == 2500
    assert payload["aedt"] == {"version": "2026.1", "non_graphical": False, "edb_backend": "auto", "cadence_launcher": "", "ansysem_root": "", "awp_root": ""}
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_brd_mission.py::test_cli_create_brd_real_build_payload_with_recorded_analysis -q
```

Expected: FAIL，原因是 CLI 参数尚不存在。

- [ ] **Step 3：实现 CLI 参数和 recorded analysis 合并**

Modify `build_parser()` in `src/aedt_agent/agent/cli.py`:

```python
create.add_argument("--adapter-mode", choices=["deterministic", "real_build"], default="deterministic")
create.add_argument("--stackup-xml")
create.add_argument("--recorded-analysis", type=Path)
create.add_argument("--aedt-version", default="2026.1")
create.add_argument("--edb-backend", choices=["auto", "grpc", "dotnet"], default="auto")
create.add_argument("--cadence-launcher", default="")
create.add_argument("--ansysem-root", default="")
create.add_argument("--awp-root", default="")
mode = create.add_mutually_exclusive_group()
mode.add_argument("--graphical", dest="non_graphical", action="store_false")
mode.add_argument("--non-graphical", dest="non_graphical", action="store_true")
create.set_defaults(non_graphical=False)
```

In the `mission create` BRD branch:

```python
recorded_layout_settings = _recorded_layout_settings_from_analysis(args.recorded_analysis)
runtime.create_job(
    mission.mission_id,
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    "brd-local-cut:0",
    build_brd_local_cut_job_input(
        layout_file=args.layout_file,
        signal_nets=args.signal_net,
        reference_nets=args.reference_net or ["GND"],
        local_cut_region=_parse_bbox(args.bbox),
        artifact_dir=artifact_dir,
        target_metrics=criteria,
        adapter_mode=args.adapter_mode,
        stackup_xml=args.stackup_xml,
        recorded_layout_settings=recorded_layout_settings,
        aedt={
            "version": args.aedt_version,
            "non_graphical": args.non_graphical,
            "edb_backend": args.edb_backend,
            "cadence_launcher": args.cadence_launcher,
            "ansysem_root": args.ansysem_root,
            "awp_root": args.awp_root,
        },
    ),
)
```

Add helper:

```python
def _recorded_layout_settings_from_analysis(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    params: dict[str, Any] = {}
    merge_recorded_layout_settings(params, data)
    return {
        "hfss_extents": dict(params.get("recorded_hfss_extents") or {}),
        "design_options": dict(params.get("recorded_design_options") or {}),
        "setup_options": dict(params.get("recorded_setup_options") or {}),
        "setup_advanced_settings": dict(params.get("recorded_setup_advanced_settings") or {}),
        "setup_curve_approximation": dict(params.get("recorded_setup_curve_approximation") or {}),
        "sweep_options": dict(params.get("recorded_sweep_options") or {}),
    }
```

- [ ] **Step 4：运行 CLI 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_brd_mission.py tests\test_agent_cli_runtime.py tests\test_agent_cli_boundary.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 CLI real_build 创建路径**

```powershell
git add src/aedt_agent/agent/cli.py tests/test_agent_cli_brd_mission.py
git commit -m "feat: create brd real build missions from cli"
```

---

## Task 5：真实 smoke、回归与审计

**Files:**
- Create: `tests/test_agent_brd_real_build_smoke.py`
- Modify only if verification finds defects.

- [ ] **Step 1：写 opt-in 真实 AEDT smoke 测试**

Create `tests/test_agent_brd_real_build_smoke.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from aedt_agent.infrastructure import BrdRealBuildAdapter, BrdRealBuildRequest, RealAedtEnvironment


@pytest.mark.skipif(os.environ.get("RUN_REAL_AEDT") != "1", reason="real AEDT smoke is opt-in")
def test_real_brd_build_only_smoke(tmp_path):
    layout = Path(os.environ["AEDT_AGENT_REAL_BRD"])
    stackup_value = os.environ.get("AEDT_AGENT_REAL_STACKUP", "")
    stackup = Path(stackup_value) if stackup_value else None
    result = BrdRealBuildAdapter().run(
        BrdRealBuildRequest(
            layout_file=layout,
            stackup_xml=stackup,
            artifact_dir=tmp_path / "real_build",
            signal_nets=os.environ["AEDT_AGENT_REAL_SIGNAL_NETS"].split(","),
            reference_nets=os.environ.get("AEDT_AGENT_REAL_REFERENCE_NETS", "GND").split(","),
            local_cut_region={
                "type": "bbox",
                "unit": os.environ.get("AEDT_AGENT_REAL_BBOX_UNIT", "mil"),
                "x_min": float(os.environ["AEDT_AGENT_REAL_BBOX_X_MIN"]),
                "y_min": float(os.environ["AEDT_AGENT_REAL_BBOX_Y_MIN"]),
                "x_max": float(os.environ["AEDT_AGENT_REAL_BBOX_X_MAX"]),
                "y_max": float(os.environ["AEDT_AGENT_REAL_BBOX_Y_MAX"]),
            },
            environment=RealAedtEnvironment(
                version=os.environ.get("AEDT_AGENT_REAL_AEDT_VERSION", "2026.1"),
                non_graphical=os.environ.get("AEDT_AGENT_REAL_NON_GRAPHICAL", "0") == "1",
            ),
        )
    )

    assert result.summary["status"] == "succeeded"
    assert result.summary["layout_solve"]["status"] == "skipped"
    assert result.summary["edb_path"].endswith(".aedb")
    assert result.summary["aedt_project"].endswith(".aedt")
```

- [ ] **Step 2：运行 smoke 默认跳过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_real_build_smoke.py -q
```

Expected: `1 skipped`。

- [ ] **Step 3：运行本阶段重点测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_infrastructure_brd_real_build.py `
  tests\test_agent_brd_local_cut_worker.py `
  tests\test_agent_cli_brd_mission.py `
  tests\test_agent_brd_mission_runtime.py `
  tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 4：检查新 Agent 与 infrastructure 不依赖 v0**

Run:

```powershell
rg -n "aedt_agent\.v0|aedt_agent\.demo|aedt_agent\.benchmark|aedt_agent\.chat|aedt_agent\.evolution" src\aedt_agent\agent src\aedt_agent\infrastructure
```

Expected: 无输出。

- [ ] **Step 5：运行 Runtime + CLI 迁移重点测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_runtime_contracts.py `
  tests\test_agent_sqlite_store.py `
  tests\test_agent_state_machine.py `
  tests\test_agent_worker_registry.py `
  tests\test_agent_runtime_service.py `
  tests\test_agent_approval_service.py `
  tests\test_agent_cli_runtime.py `
  tests\test_agent_cli_boundary.py `
  tests\test_v0_namespace_compatibility.py `
  tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 6：运行全量测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
```

Expected: 失败集合不超过当前 9 个历史失败。

- [ ] **Step 7：检查 Git 变更范围并提交 smoke 测试**

Run:

```powershell
git diff --check
git status --short
git diff --name-only HEAD~4..HEAD
```

Expected: 新增/修改只涉及 `src/aedt_agent/infrastructure`、`src/aedt_agent/agent`、`tests/test_agent_*`、`tests/test_infrastructure_*`、本计划文档。

Commit:

```powershell
git add tests/test_agent_brd_real_build_smoke.py
git commit -m "test: add opt-in brd real build smoke"
```

---

## 完成定义

1. `BrdRealBuildAdapter` 能通过 fake EDB/HFSS class 执行 build-only 调用链。
2. `real_build` 路径会创建 PyEDB cutout polygon，并创建 HFSS 3D Layout setup/sweep。
3. `real_build` 默认且强制不求解，不调用 `analyze_setup`。
4. `brd.local_cut.build` worker 能通过 `adapter_mode=real_build` 调用 infrastructure adapter。
5. CLI 能创建 real build Mission，并合并 recorded analysis。
6. 新 Agent 和 infrastructure 不依赖 `aedt_agent.v0`。
7. 真实 AEDT smoke 默认 skipped，只在显式环境变量下运行。
8. 全量测试失败集合不扩大。
