from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aedt_agent.agent.cli", "--db", str(tmp_path / "mission.db"), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_runs_brd_local_cut_mission_to_model_review(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "构建 local cut",
        "--brd-local-cut",
        "--layout-file",
        str(layout_file),
        "--signal-net",
        "56G_TX0_P",
        "--signal-net",
        "56G_TX0_N",
        "--reference-net",
        "GND",
        "--bbox",
        "mil,1,2,3,4",
        "--criterion",
        "s21_db_at_56g>=-8",
    )
    mission_id = json.loads(created.stdout)["mission_id"]

    ran = _run(tmp_path, "mission", "run", "--mission-id", mission_id)
    status = _run(tmp_path, "mission", "status", "--mission-id", mission_id)

    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout)["status"] == "succeeded"
    payload = json.loads(status.stdout)
    assert payload["state"] == "evaluating"
    assert payload["jobs"][0]["capability"] == "brd.local_cut.build"
    assert payload["jobs"][0]["output_payload"]["evidence_summary"]["raw_sparameters"] == "artifact_only"


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
    assert payload["aedt"] == {
        "version": "2026.1",
        "non_graphical": False,
        "edb_backend": "auto",
        "cadence_launcher": "",
        "ansysem_root": "",
        "awp_root": "",
    }


def test_cli_creates_real_solve_job_without_output_directory(tmp_path):
    project = tmp_path / "approved.aedt"
    project.write_text("approved project", encoding="utf-8")

    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "求解 approved local cut",
        "--brd-real-solve",
        "--project",
        str(project),
        "--setup",
        "Setup1",
        "--sweep",
        "Sweep1",
        "--tdr-expression",
        "TDRZt(P1,P1)",
        "--expected-port-count",
        "2",
    )

    assert created.returncode == 0, created.stderr
    mission_id = json.loads(created.stdout)["mission_id"]
    status = json.loads(
        _run(
            tmp_path,
            "mission",
            "status",
            "--mission-id",
            mission_id,
        ).stdout
    )
    job = status["jobs"][0]
    assert job["capability"] == "brd.local_cut.solve"
    assert job["timeout_seconds"] == 7200
    assert "artifact_dir" not in job["input_payload"]
    assert job["input_payload"]["approval_reason"] == (
        "approve_real_brd_solve"
    )
