from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aedt_agent.infrastructure.brd_real_build import RealAedtEnvironment
from aedt_agent.infrastructure.brd_real_solve import (
    ArtifactExportError,
    ArtifactValidationError,
    BrdRealSolveAdapter,
    BrdRealSolveRequest,
)


def _request(tmp_path: Path, **overrides) -> BrdRealSolveRequest:
    project = tmp_path / "approved.aedt"
    project.write_text("approved project", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    values = {
        "project_path": project,
        "artifact_dir": artifact_dir,
        "setup_name": "Setup1",
        "sweep_name": "Sweep1",
        "solution_name": "Setup1 : Sweep1",
        "touchstone_name": "channel.s2p",
        "tdr_report_name": "ChannelTDR",
        "tdr_expression": "TDRZt(P1,P1)",
        "expected_port_count": 2,
        "environment": RealAedtEnvironment(
            version="2026.1",
            non_graphical=True,
        ),
    }
    values.update(overrides)
    return BrdRealSolveRequest(**values)


def test_real_solve_rejects_non_aedt_project(tmp_path):
    project = tmp_path / "model.txt"
    project.write_text("not a project", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="project_path must end with .aedt",
    ):
        BrdRealSolveAdapter().run(
            _request(tmp_path, project_path=project)
        )


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "dB(S(1,1))",
        "TDRZt(P1,P2);DeleteProject()",
        "TDRZ(Diff1,Diff1)",
    ],
)
def test_real_solve_rejects_unapproved_tdr_expression(
    tmp_path,
    expression,
):
    with pytest.raises(ValueError, match="tdr_expression"):
        BrdRealSolveAdapter().run(
            _request(tmp_path, tdr_expression=expression)
        )


class FakePost:
    def create_report(
        self,
        expressions,
        setup_sweep_name,
        domain,
        variations,
        primary_sweep_variable,
        plot_name,
        context,
    ):
        FakeHfss3dLayout.calls.append(
            (
                "create_report",
                {
                    "expressions": expressions,
                    "setup_sweep_name": setup_sweep_name,
                    "domain": domain,
                    "variations": variations,
                    "primary_sweep_variable": primary_sweep_variable,
                    "plot_name": plot_name,
                    "context": context,
                },
            )
        )
        if FakeHfss3dLayout.use_expression_data_report:
            return FakeExpressionDataReport()
        if FakeHfss3dLayout.use_solution_data_report:
            return FakeReport()
        return object()

    def export_report_to_file(
        self,
        output_dir,
        plot_name,
        extension,
    ):
        path = Path(output_dir) / f"{plot_name}{extension}"
        path.write_text(
            FakeHfss3dLayout.tdr_text,
            encoding="utf-8",
        )
        FakeHfss3dLayout.calls.append(
            (
                "export_report_to_file",
                {
                    "output_dir": output_dir,
                    "plot_name": plot_name,
                    "extension": extension,
                },
            )
        )
        return str(path)

    def delete_report(self, plot_name):
        FakeHfss3dLayout.calls.append(("delete_report", plot_name))


class FakeReport:
    def get_solution_data(self):
        FakeHfss3dLayout.calls.append(("get_solution_data", {}))
        return FakeSolutionData()


class FakeSolutionData:
    primary_sweep_values = None
    units_sweeps = {"Time": "ns"}

    def __init__(self):
        self.primary_sweep_values = FakeArrayLike([0.0, 0.01])

    def data_real(self):
        return FakeArrayLike([100.0, 105.0])


class FakeExpressionDataReport:
    def get_solution_data(self):
        FakeHfss3dLayout.calls.append(("get_solution_data", {}))
        return FakeExpressionSolutionData()


class FakeExpressionSolutionData:
    primary_sweep_values = None
    units_sweeps = {"Time": "ns"}
    expressions = ["TDRZ(Diff1)"]

    def get_expression_data(self, expression=None, formula="real"):
        FakeHfss3dLayout.calls.append(
            (
                "get_expression_data",
                {"expression": expression, "formula": formula},
            )
        )
        return (
            FakeArrayLike([0.0, 0.032835820895522366]),
            FakeArrayLike([91.175, 74.228]),
        )


class FakeArrayLike(list):
    def __bool__(self):
        raise ValueError("truth value is ambiguous")


class FakeReportSetupModule:
    def CreateReport(
        self,
        report_name,
        report_category,
        plot_type,
        setup_sweep_name,
        context,
        variations,
        components,
    ):
        FakeHfss3dLayout.calls.append(
            (
                "native_create_report",
                {
                    "report_name": report_name,
                    "report_category": report_category,
                    "plot_type": plot_type,
                    "setup_sweep_name": setup_sweep_name,
                    "context": context,
                    "variations": variations,
                    "components": components,
                },
            )
        )

    def ExportToFile(self, report_name, output_file, include_header=False):
        Path(output_file).write_text(
            FakeHfss3dLayout.tdr_text,
            encoding="utf-8",
        )
        FakeHfss3dLayout.calls.append(
            (
                "native_export_report",
                {
                    "report_name": report_name,
                    "output_file": output_file,
                    "include_header": include_header,
                },
            )
        )


class FakeDesign:
    def GetModule(self, name):
        FakeHfss3dLayout.calls.append(("get_module", name))
        if name == "ReportSetup":
            return FakeReportSetupModule()
        raise ValueError(name)


class FakeHfss3dLayout:
    calls: list[tuple[str, object]] = []
    setup_names = ["Setup1"]
    setup_sweeps_names = ["Setup1 : Sweep1"]
    port_list = ["P1", "P2"]
    differential_pairs = ["Diff1"]
    analyze_result = True
    use_native_report = False
    use_solution_data_report = False
    use_expression_data_report = False
    touchstone_text = (
        "# GHz S MA R 50\n"
        "0 0.05 0 0.9 0 0.9 0 0.05 0\n"
        "18 0.45 0 0.8 0 0.8 0 0.05 0\n"
    )
    tdr_text = (
        "Time [ps],TDRZt(P1,P1)\n"
        "0,100\n"
        "10,105\n"
    )

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.setup_names = ["Setup1"]
        cls.setup_sweeps_names = ["Setup1 : Sweep1"]
        cls.port_list = ["P1", "P2"]
        cls.differential_pairs = ["Diff1"]
        cls.analyze_result = True
        cls.use_native_report = False
        cls.use_solution_data_report = False
        cls.use_expression_data_report = False
        cls.touchstone_text = (
            "# GHz S MA R 50\n"
            "0 0.05 0 0.9 0 0.9 0 0.05 0\n"
            "18 0.45 0 0.8 0 0.8 0 0.05 0\n"
        )
        cls.tdr_text = (
            "Time [ps],TDRZt(P1,P1)\n"
            "0,100\n"
            "10,105\n"
        )

    def __init__(
        self,
        *,
        project,
        version,
        non_graphical,
        new_desktop,
        close_on_exit,
        remove_lock,
    ):
        self.post = FakePost()
        if self.use_native_report:
            self.odesign = FakeDesign()
        self.calls.append(
            (
                "init",
                {
                    "project": project,
                    "version": version,
                    "non_graphical": non_graphical,
                    "new_desktop": new_desktop,
                    "close_on_exit": close_on_exit,
                    "remove_lock": remove_lock,
                },
            )
        )

    def analyze_setup(self, name, blocking):
        self.calls.append(
            (
                "analyze_setup",
                {"name": name, "blocking": blocking},
            )
        )
        return self.analyze_result

    def export_touchstone(self, setup, sweep, output_file):
        Path(output_file).write_text(
            self.touchstone_text,
            encoding="utf-8",
        )
        self.calls.append(
            (
                "export_touchstone",
                {
                    "setup": setup,
                    "sweep": sweep,
                    "output_file": output_file,
                },
            )
        )
        return output_file

    def save_project(self, file_name):
        Path(file_name).write_text(
            "solved project",
            encoding="utf-8",
        )
        self.calls.append(("save_project", file_name))
        return True

    def release_desktop(self, close_projects, close_desktop):
        self.calls.append(
            (
                "release_desktop",
                {
                    "close_projects": close_projects,
                    "close_desktop": close_desktop,
                },
            )
        )


def _adapter() -> BrdRealSolveAdapter:
    FakeHfss3dLayout.reset()
    return BrdRealSolveAdapter(
        hfss3dlayout_factory=FakeHfss3dLayout
    )


def test_real_solve_copies_checkpoint_solves_and_exports_artifacts(
    tmp_path,
):
    request = _request(tmp_path)
    source_digest = hashlib.sha256(
        request.project_path.read_bytes()
    ).hexdigest()

    result = _adapter().run(request)

    assert Path(result.project_checkpoint).read_text(
        encoding="utf-8"
    ) == "approved project"
    assert Path(result.solved_project).read_text(
        encoding="utf-8"
    ) == "solved project"
    assert Path(result.touchstone_path).stat().st_size > 0
    assert Path(result.tdr_path).stat().st_size > 0
    assert not (request.artifact_dir / "_aedt_report_tmp").exists()
    assert hashlib.sha256(
        request.project_path.read_bytes()
    ).hexdigest() == source_digest
    manifest = json.loads(
        Path(result.solve_manifest_path).read_text(encoding="utf-8")
    )
    assert manifest["outputs"]["touchstone"]["sha256"]
    assert manifest["outputs"]["tdr"]["sha256"]
    assert result.summary["touchstone_sample_count"] == 2
    assert result.summary["tdr_sample_count"] == 2
    assert [name for name, _ in FakeHfss3dLayout.calls] == [
        "init",
        "analyze_setup",
        "save_project",
        "export_touchstone",
        "create_report",
        "delete_report",
        "create_report",
        "export_report_to_file",
        "release_desktop",
    ]


def test_real_solve_can_defer_tdr_export_for_manual_recorded_script(
    tmp_path,
):
    result = _adapter().run(_request(tmp_path, export_tdr=False))

    manifest = json.loads(
        Path(result.solve_manifest_path).read_text(encoding="utf-8")
    )
    assert Path(result.touchstone_path).stat().st_size > 0
    assert result.tdr_path == ""
    assert "tdr" not in manifest["outputs"]
    assert result.summary["tdr_exported"] is False
    assert result.summary["raw_tdr"] == "deferred_manual_export"
    assert [name for name, _ in FakeHfss3dLayout.calls] == [
        "init",
        "analyze_setup",
        "save_project",
        "export_touchstone",
        "release_desktop",
    ]


def test_real_solve_can_export_tdr_from_solution_data_without_csv_report_export(
    tmp_path,
):
    adapter = _adapter()
    FakeHfss3dLayout.use_solution_data_report = True

    result = adapter.run(_request(tmp_path))

    assert Path(result.tdr_path).read_text(encoding="utf-8").splitlines() == [
        "time_ps,impedance_ohm",
        "0.0,100.0",
        "10.0,105.0",
    ]
    assert result.summary["tdr_sample_count"] == 2
    assert [name for name, _ in FakeHfss3dLayout.calls] == [
        "init",
        "analyze_setup",
        "save_project",
        "export_touchstone",
        "create_report",
        "get_solution_data",
        "delete_report",
        "release_desktop",
    ]


def test_real_solve_can_export_tdr_from_expression_data_without_data_real(
    tmp_path,
):
    adapter = _adapter()
    FakeHfss3dLayout.use_expression_data_report = True

    result = adapter.run(
        _request(
            tmp_path,
            tdr_expression="TDRZ(Diff1)",
            tdr_differential_pairs=True,
            tdr_observation_port="Diff1",
        )
    )

    assert Path(result.tdr_path).read_text(encoding="utf-8").splitlines() == [
        "time_ps,impedance_ohm",
        "0.0,91.175",
        "32.83582089552237,74.228",
    ]
    assert result.summary["tdr_sample_count"] == 2
    assert (
        "get_expression_data",
        {"expression": "TDRZ(Diff1)", "formula": "real"},
    ) in FakeHfss3dLayout.calls


def test_real_solve_uses_native_recorded_tdr_report_when_available(
    tmp_path,
):
    adapter = _adapter()
    FakeHfss3dLayout.use_native_report = True
    FakeHfss3dLayout.tdr_text = (
        "Time [ns],TDRZ(Diff1)\n"
        "0,90\n"
        "0.01,94\n"
    )

    result = adapter.run(
        _request(
            tmp_path,
            tdr_expression="TDRZ(Diff1)",
            tdr_differential_pairs=True,
            tdr_observation_port="Diff1",
        )
    )

    native_call = next(
        payload
        for name, payload in FakeHfss3dLayout.calls
        if name == "native_create_report"
    )
    assert native_call["report_category"] == "Standard"
    assert native_call["plot_type"] == "Rectangular Plot"
    assert native_call["context"][:3] == [
        "NAME:Context",
        "Domain:=",
        "Time",
    ]
    assert native_call["components"] == [
        "X Component:=",
        "Time",
        "Y Component:=",
        ["TDRZ(Diff1)"],
    ]
    assert result.summary["tdr_observation_port"] == "Diff1"
    samples = Path(result.tdr_path).read_text(encoding="utf-8").splitlines()
    assert samples[1].startswith("0.0,")
    assert samples[2].startswith("10.0,")


def test_real_solve_copies_existing_results_directory(tmp_path):
    request = _request(tmp_path)
    results = Path(f"{request.project_path}results")
    results.mkdir()
    (results / "seed.dat").write_text("seed", encoding="utf-8")

    result = _adapter().run(request)

    checkpoint_results = Path(f"{result.project_checkpoint}results")
    solved_results = Path(f"{result.solved_project}results")
    assert (checkpoint_results / "seed.dat").read_text(
        encoding="utf-8"
    ) == "seed"
    assert (solved_results / "seed.dat").read_text(
        encoding="utf-8"
    ) == "seed"


def test_real_solve_copies_existing_edb_sidecar(tmp_path):
    request = _request(tmp_path)
    sidecar = request.project_path.with_suffix(".aedb")
    sidecar.mkdir()
    (sidecar / "edb.def").write_text("edb", encoding="utf-8")

    result = _adapter().run(request)

    checkpoint_edb = Path(result.project_checkpoint).with_suffix(".aedb")
    solved_edb = Path(result.solved_project).with_suffix(".aedb")
    assert (checkpoint_edb / "edb.def").read_text(encoding="utf-8") == "edb"
    assert (solved_edb / "edb.def").read_text(encoding="utf-8") == "edb"
    manifest = json.loads(
        Path(result.solve_manifest_path).read_text(encoding="utf-8")
    )
    assert manifest["input"]["project_checkpoint_edb"]["file_count"] == 1
    assert manifest["outputs"]["solved_edb"]["file_count"] == 1
    assert result.summary["sidecar_edb_copied"] is True


def test_real_solve_accepts_setup_only_solution_name_from_aedt(
    tmp_path,
):
    adapter = _adapter()
    FakeHfss3dLayout.setup_sweeps_names = ["Setup1"]

    result = adapter.run(_request(tmp_path))

    create_report_call = next(
        payload
        for name, payload in FakeHfss3dLayout.calls
        if name == "create_report"
    )
    assert create_report_call["setup_sweep_name"] == "Setup1"
    assert result.summary["solution_name"] == "Setup1"
    assert result.summary["requested_solution_name"] == "Setup1 : Sweep1"


def test_real_solve_can_export_existing_results_without_analyze(
    tmp_path,
):
    result = _adapter().run(_request(tmp_path, run_analyze=False))

    call_names = [name for name, _ in FakeHfss3dLayout.calls]
    assert "analyze_setup" not in call_names
    assert call_names == [
        "init",
        "save_project",
        "export_touchstone",
        "create_report",
        "delete_report",
        "create_report",
        "export_report_to_file",
        "release_desktop",
    ]
    assert result.summary["analyze_executed"] is False


def test_real_solve_accepts_differential_tdr_port_from_diff_pairs(
    tmp_path,
):
    adapter = _adapter()
    FakeHfss3dLayout.tdr_text = (
        "Time [ps],TDRZ(Diff1)\n"
        "0,90\n"
        "10,94\n"
    )

    result = adapter.run(
        _request(
            tmp_path,
            tdr_expression="TDRZ(Diff1)",
            tdr_differential_pairs=True,
            tdr_observation_port="Diff1",
        )
    )

    create_report_call = next(
        payload
        for name, payload in FakeHfss3dLayout.calls
        if name == "create_report"
    )
    assert create_report_call["context"]["differential_pairs"] is True
    assert result.summary["tdr_observation_port"] == "Diff1"
    assert result.summary["tdr_differential_pairs"] is True


def test_real_solve_accepts_differential_tdr_port_from_project_file(
    tmp_path,
):
    adapter = _adapter()
    FakeHfss3dLayout.differential_pairs = []
    FakeHfss3dLayout.tdr_text = (
        "Time [ps],TDRZ(Diff1)\n"
        "0,90\n"
        "10,94\n"
    )
    request = _request(
        tmp_path,
        tdr_expression="TDRZ(Diff1)",
        tdr_differential_pairs=True,
        tdr_observation_port="Diff1",
    )
    request.project_path.write_text(
        "$begin 'DiffPairs'\n"
        "\tPair(Pos='P2', Neg='P1', On=true, Dif='Diff1')\n"
        "$end 'DiffPairs'\n",
        encoding="utf-8",
    )

    result = adapter.run(request)

    assert result.summary["tdr_observation_port"] == "Diff1"
    assert result.summary["tdr_differential_pairs"] is True


def test_real_solve_can_use_existing_working_project_without_extra_copy(
    tmp_path,
):
    request = _request(tmp_path, project_copy_mode="working_project")

    result = _adapter().run(request)

    assert Path(result.solved_project) == request.project_path
    assert Path(result.project_checkpoint) == request.project_path
    assert not (request.artifact_dir / "input_checkpoint").exists()
    assert result.summary["project_copy_mode"] == "working_project"


def test_real_solve_releases_desktop_when_analyze_fails(tmp_path):
    adapter = _adapter()
    FakeHfss3dLayout.analyze_result = False

    with pytest.raises(ArtifactExportError, match="solve failed"):
        adapter.run(_request(tmp_path))

    assert FakeHfss3dLayout.calls[-1][0] == "release_desktop"


def test_real_solve_rejects_empty_touchstone(tmp_path):
    adapter = _adapter()
    FakeHfss3dLayout.touchstone_text = ""

    with pytest.raises(
        ArtifactExportError,
        match="Touchstone",
    ):
        adapter.run(_request(tmp_path))

    assert FakeHfss3dLayout.calls[-1][0] == "release_desktop"


def test_real_solve_rejects_tdr_without_samples(tmp_path):
    adapter = _adapter()
    FakeHfss3dLayout.tdr_text = "Time [ps],TDRZt(P1,P1)\n"

    with pytest.raises(
        ArtifactValidationError,
        match="no samples",
    ):
        adapter.run(_request(tmp_path))

    assert FakeHfss3dLayout.calls[-1][0] == "release_desktop"


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("setup_names", ["Other"], "setup not found"),
        (
            "setup_sweeps_names",
            ["Setup1 : Other"],
            "setup sweep not found",
        ),
        ("port_list", ["P1"], "expected 2 ports"),
    ],
)
def test_real_solve_rejects_unapproved_project_contract(
    tmp_path,
    attribute,
    value,
    message,
):
    adapter = _adapter()
    setattr(FakeHfss3dLayout, attribute, value)

    with pytest.raises(ValueError, match=message):
        adapter.run(_request(tmp_path))

    assert FakeHfss3dLayout.calls[-1][0] == "release_desktop"


def test_real_solve_rejects_tdr_expression_with_unknown_port(tmp_path):
    adapter = _adapter()

    with pytest.raises(
        ValueError,
        match="TDR expression port not found",
    ):
        adapter.run(
            _request(
                tmp_path,
                tdr_expression="TDRZt(P1,P9)",
            )
        )

    assert FakeHfss3dLayout.calls[-1][0] == "release_desktop"
