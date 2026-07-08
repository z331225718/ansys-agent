import json
import subprocess
import sys

from aedt_agent.layout.channel_scoring import compare_channel_scores, parse_tdr_csv, parse_touchstone, score_channel_result
from aedt_agent.reporting.channel_scoring_report import render_channel_score_html


def test_parse_touchstone_reads_s2p_magnitude_angle_samples(tmp_path):
    path = tmp_path / "case.s2p"
    path.write_text(
        "! demo\n"
        "# GHz S MA R 50\n"
        "0.0 0.10 0 0.80 0 0.80 0 0.10 0\n"
        "13.28 0.05 0 0.70 0 0.70 0 0.05 0\n"
        "26.56 0.20 0 0.60 0 0.60 0 0.20 0\n",
        encoding="utf-8",
    )

    samples = parse_touchstone(path)

    assert [sample["frequency_ghz"] for sample in samples] == [0.0, 13.28, 26.56]
    assert round(samples[0]["s11_db"], 3) == -20.0
    assert round(samples[1]["s21_db"], 3) == -3.098


def test_parse_touchstone_reads_ri_format(tmp_path):
    path = tmp_path / "case.s2p"
    path.write_text(
        "# GHz S RI R 50\n"
        "1.0 0.1 0.0 0.7 0.0 0.7 0.0 0.1 0.0\n",
        encoding="utf-8",
    )

    samples = parse_touchstone(path)

    assert round(samples[0]["s11_db"], 3) == -20.0


def test_parse_touchstone_reads_s4p_differential_samples(tmp_path):
    path = tmp_path / "case.s4p"
    path.write_text(
        "# GHz S MA R 50\n"
        "1.0 "
        "0.2 0 0.05 0 0 0 0 0 "
        "0.05 0 0.2 0 0 0 0 0 "
        "0.8 0 0 0 0.05 0 0 0 "
        "0 0 0.8 0 0 0 0.05 0\n",
        encoding="utf-8",
    )

    samples = parse_touchstone(path)

    assert round(samples[0]["sdd11_db"], 3) == -16.478
    assert round(samples[0]["sdd21_db"], 3) == -1.938
    assert samples[0]["s11_db"] == samples[0]["sdd11_db"]
    assert samples[0]["s21_db"] == samples[0]["sdd21_db"]


def test_parse_touchstone_renormalizes_s4p_to_configured_diff90(tmp_path):
    path = tmp_path / "case.s4p"
    path.write_text(
        "# GHz S MA R 50\n"
        "1.0 "
        "0 0 0 0 0 0 0 0 "
        "0 0 0 0 0 0 0 0 "
        "0 0 0 0 0 0 0 0 "
        "0 0 0 0 0 0 0 0\n",
        encoding="utf-8",
    )

    samples = parse_touchstone(path, reference_impedance_ohm=90.0)

    assert samples[0]["touchstone_reference_impedance_ohm"] == 50.0
    assert samples[0]["single_ended_reference_impedance_ohm"] == 45.0
    assert samples[0]["differential_reference_impedance_ohm"] == 90.0
    assert round(samples[0]["sdd11_db"], 3) == -25.575


def test_parse_tdr_csv_accepts_time_and_impedance_columns(tmp_path):
    path = tmp_path / "tdr.csv"
    path.write_text("time_ps,impedance_ohm\n0,100\n10,104\n20,92\n", encoding="utf-8")

    samples = parse_tdr_csv(path)

    assert samples == [
        {"time_ps": 0.0, "impedance_ohm": 100.0},
        {"time_ps": 10.0, "impedance_ohm": 104.0},
        {"time_ps": 20.0, "impedance_ohm": 92.0},
    ]


def test_score_channel_result_reports_worst_rl_and_tdr_peak(tmp_path):
    touchstone = tmp_path / "case.s2p"
    touchstone.write_text(
        "# GHz S MA R 50\n"
        "0.0 0.05 0 0.80 0 0.80 0 0.05 0\n"
        "13.28 0.10 0 0.70 0 0.70 0 0.10 0\n"
        "26.56 0.25 0 0.60 0 0.60 0 0.25 0\n",
        encoding="utf-8",
    )
    tdr = tmp_path / "tdr.csv"
    tdr.write_text("time_ps,impedance_ohm\n0,100\n10,104\n20,92\n30,101\n", encoding="utf-8")

    score = score_channel_result(touchstone, tdr, frequency_stop_ghz=26.56, rl_target_db=-20, tdr_target_ohm=100)

    assert score["status"] == "fail"
    assert round(score["rl_worst_db"], 3) == -12.041
    assert score["rl_worst_frequency_ghz"] == 26.56
    assert score["tdr_peak_deviation_ohm"] == 8.0
    assert score["tdr_peak_time_ps"] == 20.0
    assert score["tdr_anomaly_window"] == {"start_ps": 10.0, "stop_ps": 30.0}
    assert score["tdr_mean_impedance_ohm"] == 99.25
    assert score["tdr_peak_to_peak_ohm"] == 12.0
    assert score["tdr_proximity_mse_ohm2"] == 20.25
    assert score["tdr_proximity_rmse_ohm"] == 4.5
    assert score["tdr_flatness_msd_ohm2"] == 80.333
    assert score["tdr_flatness_rms_step_ohm"] == 8.963
    assert score["rl_violation_point_count"] == 1
    assert score["optimization_objective"]["strategy"] == "rl_violation_plus_tdr_proximity_flatness"
    assert score["optimization_objective"]["total_cost"] == 11.98232


def test_score_channel_result_uses_configured_tdr_tolerance(tmp_path):
    touchstone = tmp_path / "case.s2p"
    touchstone.write_text(
        "# GHz S MA R 50\n"
        "0.0 0.05 0 0.80 0 0.80 0 0.05 0\n"
        "13.28 0.08 0 0.70 0 0.70 0 0.08 0\n",
        encoding="utf-8",
    )
    tdr = tmp_path / "tdr.csv"
    tdr.write_text(
        "time_ps,impedance_ohm\n0,100\n10,108\n",
        encoding="utf-8",
    )

    default_score = score_channel_result(
        touchstone,
        tdr,
        frequency_stop_ghz=28.0,
        rl_target_db=-17,
        tdr_target_ohm=100,
    )
    relaxed_score = score_channel_result(
        touchstone,
        tdr,
        frequency_stop_ghz=28.0,
        rl_target_db=-17,
        tdr_target_ohm=100,
        tdr_tolerance_ohm=9,
    )

    assert default_score["status"] == "fail"
    assert relaxed_score["status"] == "pass"
    assert relaxed_score["tdr_tolerance_ohm"] == 9


def test_score_channel_result_uses_sdd11_for_s4p(tmp_path):
    touchstone = tmp_path / "case.s4p"
    touchstone.write_text(
        "# GHz S MA R 50\n"
        "1.0 "
        "0.2 0 0.05 0 0 0 0 0 "
        "0.05 0 0.2 0 0 0 0 0 "
        "0.8 0 0 0 0.05 0 0 0 "
        "0 0 0.8 0 0 0 0.05 0\n"
        "28.0 "
        "0.4 0 0.05 0 0 0 0 0 "
        "0.05 0 0.4 0 0 0 0 0 "
        "0.7 0 0 0 0.05 0 0 0 "
        "0 0 0.7 0 0 0 0.05 0\n",
        encoding="utf-8",
    )
    tdr = tmp_path / "tdr.csv"
    tdr.write_text(
        "time_ps,impedance_ohm\n0,90\n10,96\n",
        encoding="utf-8",
    )

    score = score_channel_result(
        touchstone,
        tdr,
        frequency_stop_ghz=28.0,
        rl_target_db=-17,
        tdr_target_ohm=90,
        tdr_tolerance_ohm=9,
        tdr_observation_port="Diff1",
    )

    assert score["sparameter_mode"] == "differential"
    assert score["touchstone_kind"] == "s4p"
    assert score["return_loss_trace"] == "SDD11"
    assert score["insertion_loss_trace"] == "SDD21"
    assert score["tdr_observation_port"] == "Diff1"
    assert score["reference_impedance_ohm"] == 90
    assert score["single_ended_reference_impedance_ohm"] == 45
    assert score["rl_worst_frequency_ghz"] == 28.0
    assert "sdd21_worst_db_in_band" in score


def test_compare_channel_scores_classifies_improvement():
    before = {"rl_worst_db": -14.0, "tdr_peak_deviation_ohm": 9.0}
    after = {"rl_worst_db": -21.0, "tdr_peak_deviation_ohm": 4.0}

    comparison = compare_channel_scores(before, after)

    assert comparison["status"] == "improved"
    assert comparison["rl_worst_delta_db"] == -7.0
    assert comparison["tdr_peak_deviation_delta_ohm"] == -5.0
    assert "改善" in comparison["summary"]


def test_compare_channel_scores_classifies_mixed_result():
    before = {"rl_worst_db": -14.0, "tdr_peak_deviation_ohm": 4.0}
    after = {"rl_worst_db": -21.0, "tdr_peak_deviation_ohm": 9.0}

    comparison = compare_channel_scores(before, after)

    assert comparison["status"] == "mixed"


def test_render_channel_score_html_contains_chinese_sections():
    score = {
        "status": "fail",
        "frequency_stop_ghz": 26.56,
        "rl_target_db": -20,
        "rl_worst_db": -12.041,
        "rl_worst_frequency_ghz": 26.56,
        "tdr_target_ohm": 100,
        "tdr_peak_deviation_ohm": 8.0,
        "tdr_peak_time_ps": 20.0,
        "tdr_anomaly_window": {"start_ps": 10.0, "stop_ps": 30.0},
        "diagnosis": ["0-26.56GHz 内 RL 未达到 -20dB 目标。"],
        "sources": {"touchstone": "case.s2p", "tdr": "tdr.csv"},
        "plot_artifacts": {"tdr": "tdr.svg", "s11": "s11.svg", "s21": "s21.svg"},
        "samples": {"sparameter_count": 3, "tdr_count": 4},
    }

    html = render_channel_score_html(score)

    assert "Stage C.4 通道离线评分报告" in html
    assert "回波损耗" in html
    assert "TDR" in html
    assert "优化目标函数" in html
    assert '<object data="tdr.svg" type="image/svg+xml"' in html
    assert "-12.041" in html
    assert "case.s2p" in html


def test_score_stage_c_channel_cli_writes_json_and_html(tmp_path):
    touchstone = tmp_path / "case.s2p"
    touchstone.write_text(
        "# GHz S MA R 50\n0 0.05 0 0.8 0 0.8 0 0.05 0\n26.56 0.25 0 0.6 0 0.6 0 0.25 0\n",
        encoding="utf-8",
    )
    tdr = tmp_path / "tdr.csv"
    tdr.write_text("time_ps,impedance_ohm\n0,100\n10,108\n", encoding="utf-8")
    output_json = tmp_path / "score.json"
    output_html = tmp_path / "score.html"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/score_stage_c_channel.py",
            "--touchstone",
            str(touchstone),
            "--tdr",
            str(tdr),
            "--output-json",
            str(output_json),
            "--output-html",
            str(output_html),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert output_json.exists()
    assert output_html.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["status"] == "fail"


def test_compare_stage_c_channel_cli_writes_comparison(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    output = tmp_path / "comparison.json"
    before.write_text(json.dumps({"rl_worst_db": -14, "tdr_peak_deviation_ohm": 9}), encoding="utf-8")
    after.write_text(json.dumps({"rl_worst_db": -21, "tdr_peak_deviation_ohm": 4}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/compare_stage_c_channel.py", "--before", str(before), "--after", str(after), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "improved"
