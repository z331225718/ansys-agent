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
