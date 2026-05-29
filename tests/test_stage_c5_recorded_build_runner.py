import json
import subprocess
import sys


def _recorded_analysis():
    return {
        "paths": {"brd": "/boards/case.brd", "aedb": "/runs/case.aedb", "aedt_project": "/runs/case.aedt"},
        "nets": {"signal": ["SRDS_0_RX0_N", "SRDS_0_RX0_P"], "reference": ["GND"]},
        "component": "U1",
        "setup": {"name": "Setup1", "frequency": "10GHz"},
        "sweep": {"name": "Sweep1", "data": "LIN 0GHz 67GHz 0.05GHz", "stop_ghz": 67.0},
        "optimization_variables": [{"name": "r_cut_L3", "value": "15mil"}],
        "voids": [{"layer": "ART03", "kind": "circle"}, {"layer": "ART03", "kind": "rectangle"}],
    }


def test_build_recorded_optimization_action_plan_prefers_wrappers_without_solve():
    from aedt_agent.layout.optimization_actions import build_recorded_optimization_action_plan

    plan = build_recorded_optimization_action_plan(_recorded_analysis(), solve_enabled=False)

    assert plan["status"] == "ready"
    assert [action["type"] for action in plan["actions"]] == [
        "build_layout_model",
        "apply_layout_void_adjustment",
        "save_project",
    ]
    assert plan["actions"][0]["api"] == "pyedb_hfss3dlayout_build"
    assert plan["actions"][1]["api"] == "raw_aedt_void_fallback"
    assert plan["actions"][1]["variable"] == "r_cut_L3"
    assert plan["actions"][2]["api"] == "Hfss3dLayout.save_project"
    assert not any(action["type"] == "solve_layout_channel" for action in plan["actions"])


def test_build_void_fallback_payload_is_data_only():
    from aedt_agent.layout.void_fallback import build_void_fallback_payload

    payload = build_void_fallback_payload(_recorded_analysis())

    assert payload["status"] == "ready"
    assert payload["variable"] == "r_cut_L3"
    assert payload["operations"] == [
        {"api": "oEditor.CreateCircleVoid", "layer": "ART03", "kind": "circle", "variable": "r_cut_L3"},
        {"api": "oEditor.CreateRectangleVoid", "layer": "ART03", "kind": "rectangle", "variable": "r_cut_L3"},
    ]
    assert "app" not in payload
    assert "execute" not in payload


def test_run_stage_c5_recorded_build_cli_fake_writes_build_artifacts(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(
        json.dumps(
            {
                "layout_file": str(layout_file),
                "signal_nets": "SRDS_0_RX0_*",
                "reference_nets": "GND",
            }
        ),
        encoding="utf-8",
    )
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps(_recorded_analysis()), encoding="utf-8")
    run_dir = tmp_path / "run"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_stage_c5_recorded_build.py",
            "--adapter",
            "fake",
            "--params",
            str(params),
            "--recorded-analysis",
            str(analysis),
            "--run-dir",
            str(run_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    build_summary = json.loads((run_dir / "stage_c5_build_summary.json").read_text(encoding="utf-8"))
    action_plan = json.loads((run_dir / "stage_c5_action_plan.json").read_text(encoding="utf-8"))
    assert (run_dir / "import_cutout_summary.json").exists()
    assert build_summary["status"] == "succeeded"
    assert build_summary["layout_solve"]["status"] == "skipped"
    assert action_plan["actions"][1]["api"] == "raw_aedt_void_fallback"
    assert "Stage C.5 recorded build" in result.stdout
