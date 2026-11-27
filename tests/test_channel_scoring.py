import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from aedt_agent.layout.channel_scoring import (
    compare_channel_scores,
    parse_tdr_csv,
    parse_touchstone,
    score_channel_result,
    touchstone_port_count,
)
from aedt_agent.reporting.channel_scoring_report import render_channel_score_html

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "touchstone"
REAL_S4P = FIXTURES / "channel_1ghz.s4p"
REAL_ADS_S2P = FIXTURES / "ads2027_rlgc_0_40ghz_dc.s2p"


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


def test_compare_channel_scores_does_not_hide_peak_tdr_regression_in_total_objective():
    before = {
        "rl_worst_db": -14.0,
        "tdr_peak_deviation_ohm": 4.0,
        "optimization_objective": {"total_cost": 100.0},
    }
    after = {
        "rl_worst_db": -21.0,
        "tdr_peak_deviation_ohm": 9.0,
        "optimization_objective": {"total_cost": 10.0},
    }

    comparison = compare_channel_scores(before, after)

    assert comparison["objective_total_cost_delta"] == -90.0
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


# ── the file's content decides its width, not its name ──────────────────────
#
# A real AEDT export renamed to `.touchstone` (or `.s1p`) still holds four
# ports. Slicing it as a two-port file does not merely downgrade the mode: it
# reads S values as frequencies, so `-4.29` becomes -4.29 GHz and every metric
# downstream is computed from nonsense.

_TDR_CSV = "time_ps,impedance_ohm\n0,100\n10,104\n20,92\n30,101\n"


def _copy_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copyfile(REAL_S4P, target)
    return target


def test_parse_touchstone_reads_four_ports_from_a_touchstone_named_file(tmp_path):
    path = _copy_fixture(tmp_path, "channel_1ghz.touchstone")

    samples = parse_touchstone(path)

    assert len(samples) == 1
    assert samples[0]["port_count"] == 4
    assert "sdd11_db" in samples[0]
    assert "sdd21_db" in samples[0]
    assert samples[0]["frequency_ghz"] == 1.0


def test_parse_touchstone_reads_four_ports_from_an_s1p_named_file(tmp_path):
    path = _copy_fixture(tmp_path, "channel_1ghz.s1p")

    samples = parse_touchstone(path)

    assert len(samples) == 1
    assert samples[0]["port_count"] == 4
    assert "sdd11_db" in samples[0]
    assert samples[0]["frequency_ghz"] == 1.0


def test_parse_touchstone_still_slices_a_real_s2p_by_two_ports(tmp_path):
    """No declaration, `.s2p` name: the existing behaviour is unchanged."""
    path = tmp_path / "case.s2p"
    path.write_text(
        "# GHz S MA R 50\n"
        "1.0 0.10 0 0.80 0 0.80 0 0.10 0\n"
        "2.0 0.20 0 0.70 0 0.70 0 0.20 0\n",
        encoding="utf-8",
    )

    samples = parse_touchstone(path)

    assert [sample["frequency_ghz"] for sample in samples] == [1.0, 2.0]
    assert samples[0]["port_count"] == 2
    assert "sdd11_db" not in samples[0]


def test_touchstone_port_count_falls_back_to_two_without_a_name_or_declaration(tmp_path):
    """The last resort stays 2 — it is an existing default, not an error."""
    path = tmp_path / "exported"
    path.write_text(
        "# GHz S MA R 50\n"
        "1.0 0.10 0 0.80 0 0.80 0 0.10 0\n",
        encoding="utf-8",
    )

    assert touchstone_port_count(path) == 2


def test_touchstone_port_count_reads_the_number_of_ports_keyword(tmp_path):
    path = tmp_path / "declared.s2p"
    path.write_text(
        "[Number of Ports] 4\n"
        "# GHz S MA R 50\n"
        "1.0 " + " ".join(["0.1 0"] * 16) + "\n",
        encoding="utf-8",
    )

    assert touchstone_port_count(path) == 4
    samples = parse_touchstone(path)
    assert len(samples) == 1
    assert samples[0]["port_count"] == 4


def test_score_channel_result_agrees_across_names_for_the_same_data(tmp_path):
    """The same bytes under two names must score identically, end to end."""
    tdr = tmp_path / "tdr.csv"
    tdr.write_text(_TDR_CSV, encoding="utf-8")
    as_s4p = _copy_fixture(tmp_path, "channel_1ghz.s4p")
    as_bare = _copy_fixture(tmp_path, "channel_1ghz.touchstone")

    score_s4p = score_channel_result(as_s4p, tdr, frequency_stop_ghz=28.0, tdr_target_ohm=100)
    score_bare = score_channel_result(as_bare, tdr, frequency_stop_ghz=28.0, tdr_target_ohm=100)

    assert score_bare["sparameter_mode"] == "differential"
    assert score_bare["touchstone_kind"] == "s4p"
    for key in (
        "sparameter_mode",
        "touchstone_kind",
        "rl_worst_db",
        "insertion_worst_db_in_band",
        "rl_worst_frequency_ghz",
        "reference_impedance_ohm",
    ):
        assert score_bare[key] == score_s4p[key], key


# ── a declaration is a candidate, not a fact ────────────────────────────────
#
# Trusting `! Port[n]` outright is the same bug as trusting the suffix, moved
# one level in: a file that keeps one of its four port lines still holds four
# ports, and slicing it one port wide reads S values as frequencies.

def _s4p_with_declarations(kept: int | None) -> str:
    """The real AEDT export with `kept` port declarations left (None = all)."""
    lines = REAL_S4P.read_text(encoding="utf-8").splitlines()
    declarations = [line for line in lines if line.startswith("! Port[")]
    if kept is None:
        return "\n".join(lines) + "\n"
    kept_names = set(declarations[:kept])
    return "\n".join(
        line for line in lines if not line.startswith("! Port[") or line in kept_names
    ) + "\n"


def test_one_port_declaration_does_not_narrow_a_four_port_export(tmp_path):
    """The data says four ports; one stray declaration must not win."""
    path = tmp_path / "channel_1ghz.s4p"
    path.write_text(_s4p_with_declarations(1), encoding="utf-8")

    assert touchstone_port_count(path) == 4
    samples = parse_touchstone(path)
    assert len(samples) == 1
    assert samples[0]["port_count"] == 4
    assert "sdd11_db" in samples[0]
    assert samples[0]["frequency_ghz"] == 1.0


def test_three_port_declarations_do_not_truncate_a_four_port_export(tmp_path):
    """33 numbers do not divide by 1+2*3^2, so three ports would drop data."""
    path = tmp_path / "channel_1ghz.s4p"
    path.write_text(_s4p_with_declarations(3), encoding="utf-8")

    assert touchstone_port_count(path) == 4
    samples = parse_touchstone(path)
    assert len(samples) == 1
    assert samples[0]["port_count"] == 4
    assert samples[0]["frequency_ghz"] == 1.0


def test_four_port_data_with_no_declaration_at_all_still_reads_four(tmp_path):
    """Nothing in the file names a width: the data has to decide."""
    path = tmp_path / "channel_1ghz.touchstone"
    path.write_text(_s4p_with_declarations(0), encoding="utf-8")

    assert touchstone_port_count(path) == 4
    samples = parse_touchstone(path)
    assert len(samples) == 1
    assert samples[0]["port_count"] == 4
    assert "sdd11_db" in samples[0]
    assert "sdd21_db" in samples[0]
    assert samples[0]["frequency_ghz"] == 1.0


def test_real_two_port_ads_export_keeps_every_point(tmp_path):
    """A real third-party export: 801 points, no declaration, evenly sampled."""
    path = tmp_path / "ads2027_rlgc_0_40ghz_dc.s2p"
    shutil.copyfile(REAL_ADS_S2P, path)

    assert touchstone_port_count(path) == 2
    samples = parse_touchstone(path)
    assert len(samples) == 801
    assert samples[0]["port_count"] == 2
    frequencies = [sample["frequency_ghz"] for sample in samples]
    assert frequencies[0] == 0.0
    assert frequencies[-1] == 40.0
    assert all(
        later >= earlier for earlier, later in zip(frequencies, frequencies[1:])
    )
    assert "sdd11_db" not in samples[0]


def test_a_two_port_export_without_a_suffix_or_declaration_reads_two(tmp_path):
    """Same real data, named so neither suffix nor declaration can help."""
    path = tmp_path / "ads.touchstone"
    shutil.copyfile(REAL_ADS_S2P, path)

    assert touchstone_port_count(path) == 2
    assert len(parse_touchstone(path)) == 801


def _synthetic_touchstone(port_count: int, points: int) -> str:
    rows = ["# GHz S MA R 50"]
    for index in range(points):
        values = [f"{index * 0.1:.6f}"] + ["0.1 0"] * (port_count * port_count)
        rows.append(" ".join(values))
    return "\n".join(rows) + "\n"


def test_inferred_widths_match_the_data_shape(tmp_path):
    """The inference has to land on the shape, not on a preferred default."""
    for port_count, points in ((4, 4), (8, 2), (3, 3), (2, 40)):
        path = tmp_path / f"synthetic_{port_count}p.s{port_count}p"
        path.write_text(_synthetic_touchstone(port_count, points), encoding="utf-8")

        assert touchstone_port_count(path) == port_count
        assert len(parse_touchstone(path)) == points


# ── an ambiguous width must keep the most points, not the most ports ────────
#
# `1 + 2N^2` overlaps: a two-port 33-point sweep is 297 values, which is also
# exactly seven ports at three points (99 per point). Both slices produce a
# monotonic axis, so "the data supports it" is true of both. Choosing the wider
# width reads 33 real points as 3, drops 264 values, and reported a positive
# insertion loss on a correctly named file.

def _ads_sweep_with(points: int, name: str, tmp_path: Path) -> Path:
    """The real ADS export cut to its first `points` frequency points."""
    lines = REAL_ADS_S2P.read_text(encoding="utf-8").splitlines()
    header = [line for line in lines if line.startswith("#")]
    data = [line for line in lines if line.strip() and not line.startswith(("#", "!"))]
    assert len(data) >= points, f"fixture only has {len(data)} points"
    path = tmp_path / name
    path.write_text("\n".join(header + data[:points]) + "\n", encoding="utf-8")
    return path


def test_a_33_point_two_port_sweep_is_not_read_as_seven_ports(tmp_path):
    """The regression this round exists for: 297 values fit 7 ports x 3 points."""
    path = _ads_sweep_with(33, "ads33.s2p", tmp_path)

    assert touchstone_port_count(path) == 2
    samples = parse_touchstone(path)
    assert len(samples) == 33
    frequencies = [sample["frequency_ghz"] for sample in samples]
    assert frequencies[0] == 0.0
    assert frequencies[1] == 0.05
    assert all(later >= earlier for earlier, later in zip(frequencies, frequencies[1:]))
    assert "sdd11_db" not in samples[0]


def test_the_same_33_point_sweep_reads_the_same_under_any_name(tmp_path):
    """The overlap is in the data, so the name must not be able to swing it."""
    as_s2p = _ads_sweep_with(33, "ads33.s2p", tmp_path)
    as_bare = _ads_sweep_with(33, "ads33.touchstone", tmp_path)

    for path in (as_s2p, as_bare):
        assert touchstone_port_count(path) == 2, path.name
        samples = parse_touchstone(path)
        assert len(samples) == 33, path.name
        assert samples[0]["port_count"] == 2, path.name


def _four_port_channel(points: int) -> str:
    """A plausible channel: through magnitude decays, return magnitude grows."""
    rows = ["# GHz S MA R 50"]
    for index in range(points):
        frequency = index * 0.85
        loss = math.exp(-0.02 * frequency)
        reflection = 0.05 + 0.35 * (frequency / 27.2) ** 2
        values = [f"{frequency:.9g}"]
        for row in range(4):
            for column in range(4):
                if row == column:
                    magnitude, angle = reflection, 10.0
                elif (row, column) in {(0, 2), (1, 3), (2, 0), (3, 1)}:
                    magnitude, angle = loss, -frequency * 1.5
                else:
                    magnitude, angle = 0.01, 0.0
                values += [f"{magnitude:.9g}", f"{angle:.9g}"]
        rows.append(" ".join(values))
    return "\n".join(rows) + "\n"


def test_a_correctly_named_four_port_sweep_keeps_all_its_points(tmp_path):
    """The bug hit correctly named files too — 33 points became 11."""
    path = tmp_path / "chan.s4p"
    path.write_text(_four_port_channel(33), encoding="utf-8")
    tdr = tmp_path / "tdr.csv"
    tdr.write_text("time_ps,impedance_ohm\n0,100\n10,104\n20,92\n30,101\n", encoding="utf-8")

    assert touchstone_port_count(path) == 4
    samples = parse_touchstone(path)
    assert len(samples) == 33

    score = score_channel_result(path, tdr, frequency_stop_ghz=28.0, tdr_target_ohm=100)
    assert score["sparameter_mode"] == "differential"
    assert score["touchstone_kind"] == "s4p"
    # Physics, not just metadata: insertion loss and return loss are negative.
    assert score["insertion_worst_db_in_band"] < 0
    assert score["rl_worst_db"] < 0


def test_sixteen_ports_are_not_capped_away(tmp_path):
    """A 1026-value file: a fixed ceiling of 8 dropped it back to two ports."""
    path = tmp_path / "wide.touchstone"
    path.write_text(_synthetic_touchstone(16, 2), encoding="utf-8")

    assert touchstone_port_count(path) == 16
    samples = parse_touchstone(path)
    assert len(samples) == 2
    assert samples[0]["port_count"] == 16


def test_a_single_point_file_is_decided_by_its_shape_not_by_the_default(tmp_path):
    """One point makes "non-decreasing" vacuous, so the count decides.

    33 values cannot be two ports (9 does not divide 33) and only four ports
    fit, so the answer is 4 — the two-port default must not override that.
    """
    wide = tmp_path / "one_point_four_port.touchstone"
    wide.write_text(_synthetic_touchstone(4, 1), encoding="utf-8")
    assert touchstone_port_count(wide) == 4
    assert len(parse_touchstone(wide)) == 1

    three = tmp_path / "one_point_three_port.touchstone"
    three.write_text(_synthetic_touchstone(3, 1), encoding="utf-8")
    assert touchstone_port_count(three) == 3

    # Nine values is genuinely ambiguous and keeps the documented fallback.
    ambiguous = tmp_path / "nine_values.touchstone"
    ambiguous.write_text(
        "# GHz S MA R 50\n1.0 0.10 0 0.80 0 0.80 0 0.10 0\n", encoding="utf-8"
    )
    assert touchstone_port_count(ambiguous) == 2
