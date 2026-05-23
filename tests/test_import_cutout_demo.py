from pathlib import Path

from aedt_agent.demo.import_cutout import (
    build_import_cutout_request,
    discover_layout_files,
    expand_net_patterns,
    parse_net_patterns,
    read_tdr_csv,
    run_fake_import_cutout,
)


def test_parse_net_patterns_accepts_comma_and_bracket_lists():
    assert parse_net_patterns("[DQS*, CLK*, VDD]") == ["DQS*", "CLK*", "VDD"]
    assert parse_net_patterns("DQS*,CLK*, VDD") == ["DQS*", "CLK*", "VDD"]
    assert parse_net_patterns(["DQS*", "CLK*,VDD"]) == ["DQS*", "CLK*", "VDD"]


def test_expand_net_patterns_is_case_insensitive_and_preserves_board_names():
    available = ["SOC_TX0", "soc_rx0", "GND", "VDD_1V0"]

    matched = expand_net_patterns(["*soc*tx*", "SOC_RX0", "vdd_1v0"], available)

    assert matched == ["SOC_TX0", "soc_rx0", "VDD_1V0"]


def test_discover_layout_files_prefers_brd_and_mcm(tmp_path):
    (tmp_path / "board.brd").write_text("", encoding="utf-8")
    (tmp_path / "module.mcm").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")

    files = discover_layout_files(tmp_path)

    assert files == [tmp_path / "board.brd", tmp_path / "module.mcm"]


def test_fake_import_cutout_writes_sparameter_and_tdr_artifacts(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    request = build_import_cutout_request(
        {
            "layout_file": str(layout_file),
            "signal_nets": "*tx0*",
            "reference_nets": "gnd",
            "artifact_dir": str(tmp_path / "run"),
        }
    )

    result = run_fake_import_cutout(request)
    tdr = read_tdr_csv(result["tdr"])

    assert result["status"] == "succeeded"
    assert result["signal_nets"] == ["56G_TX0_P", "56G_TX0_N"]
    assert result["reference_nets"] == ["GND"]
    assert Path(result["touchstone"]).exists()
    assert tdr["point_count"] == 6
