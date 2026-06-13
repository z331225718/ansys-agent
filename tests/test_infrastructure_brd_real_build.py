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


class FakeNets:
    def __init__(self) -> None:
        self.nets = {"56G_TX0_P": object(), "56G_TX0_N": object(), "GND": object()}


class FakeEdb:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *, edbpath: str, version: str, grpc: bool | None) -> None:
        self.edbpath = f"fake-edb://{edbpath}"
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
        self.calls.append(
            (
                "init",
                {
                    "project": project,
                    "version": version,
                    "non_graphical": non_graphical,
                    "new_desktop": new_desktop,
                    "close_on_exit": close_on_exit,
                },
            )
        )
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
        Path(self.project_file).write_text("new project", encoding="utf-8")
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
    assert result.summary["source_edb_path"].startswith("fake-edb://")
    assert result.summary["source_edb_path"].endswith("case.brd")
    assert result.summary["cutout_extent_points"] == 5
    assert result.summary["layout_solve"] == {"status": "skipped", "reason": "model_review_only"}
    init_call = dict(FakeHfss3dLayout.calls[0][1])
    assert init_call["close_on_exit"] is False
    assert init_call["non_graphical"] is False
    call_names = [name for name, _ in FakeHfss3dLayout.calls]
    assert "ImportStackupXML" in call_names
    assert "EditHfssExtents" in call_names
    assert "DesignOptions" in call_names
    assert "create_setup" in call_names
    assert "create_linear_count_sweep" in call_names
    assert "save_project" in call_names
    release_call = [value for name, value in FakeHfss3dLayout.calls if name == "release_desktop"][-1]
    assert release_call == {"close_projects": False, "close_desktop": False}


def test_real_build_closes_desktop_for_non_graphical_environment(tmp_path):
    FakeEdb.calls = []
    FakeHfss3dLayout.calls = []
    request = _request(tmp_path, environment=RealAedtEnvironment(version="2026.1", non_graphical=True))
    adapter = BrdRealBuildAdapter(edb_factory=FakeEdb, hfss3dlayout_factory=FakeHfss3dLayout)

    adapter.run(request)

    init_call = dict(FakeHfss3dLayout.calls[0][1])
    assert init_call["close_on_exit"] is True
    release_call = [value for name, value in FakeHfss3dLayout.calls if name == "release_desktop"][-1]
    assert release_call == {"close_projects": True, "close_desktop": True}


def test_real_build_cleans_old_hfss_project_artifacts_before_run(tmp_path):
    FakeEdb.calls = []
    FakeHfss3dLayout.calls = []
    request = _request(tmp_path)
    project_path = request.artifact_dir / "case_cutout_hfss.aedt"
    results_dir = Path(str(project_path) + "results")
    project_path.parent.mkdir(parents=True)
    project_path.write_text("old project marker", encoding="utf-8")
    results_dir.mkdir()
    old_result = results_dir / "old.txt"
    old_result.write_text("old result marker", encoding="utf-8")
    adapter = BrdRealBuildAdapter(edb_factory=FakeEdb, hfss3dlayout_factory=FakeHfss3dLayout)

    adapter.run(request)

    assert not old_result.exists()
    assert project_path.exists()
    assert project_path.read_text(encoding="utf-8") == "new project"


def test_real_build_uses_stable_source_edb_path_for_repeated_runs(tmp_path):
    FakeEdb.calls = []
    FakeHfss3dLayout.calls = []
    request = _request(tmp_path)
    adapter = BrdRealBuildAdapter(edb_factory=FakeEdb, hfss3dlayout_factory=FakeHfss3dLayout)

    first = adapter.run(request)
    second = adapter.run(request)

    assert first.summary["source_edb_path"] == second.summary["source_edb_path"]


class FakeHfss3dLayoutWithoutStackupImport(FakeHfss3dLayout):
    def __init__(self, *, project: str, version: str, non_graphical: bool, new_desktop: bool, close_on_exit: bool) -> None:
        super().__init__(
            project=project,
            version=version,
            non_graphical=non_graphical,
            new_desktop=new_desktop,
            close_on_exit=close_on_exit,
        )
        self.modeler = type("Modeler", (), {"oeditor": object()})()


def test_real_build_fails_when_explicit_stackup_cannot_be_imported(tmp_path):
    FakeEdb.calls = []
    FakeHfss3dLayoutWithoutStackupImport.calls = []
    stackup = tmp_path / "stackup.xml"
    stackup.write_text("<stackup />", encoding="utf-8")
    request = _request(tmp_path, stackup_xml=stackup)
    adapter = BrdRealBuildAdapter(edb_factory=FakeEdb, hfss3dlayout_factory=FakeHfss3dLayoutWithoutStackupImport)

    with pytest.raises(RuntimeError, match="HFSS editor does not support ImportStackupXML"):
        adapter.run(request)
