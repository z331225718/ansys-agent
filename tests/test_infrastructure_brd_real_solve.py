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
        "TDRZt(P1)",
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


class FakeHfss3dLayout:
    calls: list[tuple[str, object]] = []
    setup_names = ["Setup1"]
    setup_sweeps_names = ["Setup1 : Sweep1"]
    port_list = ["P1", "P2"]
    analyze_result = True
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
        cls.analyze_result = True
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
        "export_report_to_file",
        "release_desktop",
    ]


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
