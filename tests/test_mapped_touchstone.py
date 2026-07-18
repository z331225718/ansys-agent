from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.layout.mapped_touchstone import score_mapped_touchstone


def _write_s2p(path: Path) -> Path:
    path.write_text(
        "# GHZ S RI R 50\n"
        "1 0.1 0 0.8 0 0.8 0 0.1 0\n"
        "2 0.12 0 0.7 0 0.7 0 0.12 0\n",
        encoding="ascii",
    )
    return path


def test_score_mapped_touchstone_scores_explicit_single_ended_ports(tmp_path: Path):
    touchstone = _write_s2p(tmp_path / "channel.s2p")

    score = score_mapped_touchstone(
        touchstone,
        port_order=["TX", "RX"],
        sparameter_mode="single_ended",
        source_ports=["TX"],
        destination_ports=["RX"],
        frequency_start_ghz=1.0,
        frequency_stop_ghz=2.0,
        rl_target_db=-15.0,
        insertion_loss_min_db=-4.0,
        reference_impedance_ohm=50.0,
    )

    assert score["status"] == "pass"
    assert score["return_loss_trace"] == "S(TX,TX)"
    assert score["insertion_loss_trace"] == "S(RX,TX)"
    assert score["rl_worst_db"] == pytest.approx(-18.416, abs=0.001)
    assert score["insertion_worst_db_in_band"] == pytest.approx(-3.098, abs=0.001)
    assert score["port_order_source"] == "aedt_export_snapshot"
    assert score["tdr_evaluated"] is False
    assert len(score["bounded_samples"]) == 2


def test_score_mapped_touchstone_reports_limit_failure(tmp_path: Path):
    touchstone = _write_s2p(tmp_path / "channel.s2p")

    score = score_mapped_touchstone(
        touchstone,
        port_order=["TX", "RX"],
        sparameter_mode="single_ended",
        source_ports=["TX"],
        destination_ports=["RX"],
        frequency_start_ghz=1.0,
        frequency_stop_ghz=2.0,
        rl_target_db=-20.0,
        insertion_loss_min_db=-2.0,
        reference_impedance_ohm=50.0,
    )

    assert score["status"] == "fail"
    assert score["rl_violation_point_count"] == 1
    assert score["insertion_violation_point_count"] == 1
    assert len(score["diagnosis"]) == 2


def test_score_mapped_touchstone_rejects_unproven_port_count(tmp_path: Path):
    touchstone = _write_s2p(tmp_path / "channel.s2p")

    with pytest.raises(ValueError, match="2 ports but port_order contains 3"):
        score_mapped_touchstone(
            touchstone,
            port_order=["TX", "RX", "AUX"],
            sparameter_mode="single_ended",
            source_ports=["TX"],
            destination_ports=["RX"],
            frequency_start_ghz=1.0,
            frequency_stop_ghz=2.0,
            rl_target_db=-15.0,
            insertion_loss_min_db=-4.0,
            reference_impedance_ohm=50.0,
        )


def test_score_mapped_touchstone_supports_explicit_pairs_in_multiport_file(tmp_path: Path):
    import numpy as np
    import skrf as rf

    matrix = np.zeros((2, 6, 6), dtype=complex)
    source = [2, 4]
    destination = [0, 5]
    for frequency_index in range(2):
        reflection = 0.05 if frequency_index == 0 else 0.06
        transmission = 0.4 if frequency_index == 0 else 0.35
        matrix[frequency_index, source[0], source[0]] = reflection
        matrix[frequency_index, source[0], source[1]] = -reflection
        matrix[frequency_index, source[1], source[0]] = -reflection
        matrix[frequency_index, source[1], source[1]] = reflection
        matrix[frequency_index, destination[0], source[0]] = transmission
        matrix[frequency_index, destination[0], source[1]] = -transmission
        matrix[frequency_index, destination[1], source[0]] = -transmission
        matrix[frequency_index, destination[1], source[1]] = transmission
    network = rf.Network(f=np.array([1e9, 2e9]), s=matrix, z0=50.0)
    network.write_touchstone(str(tmp_path / "mapped"))

    score = score_mapped_touchstone(
        tmp_path / "mapped.s6p",
        port_order=["RX_P", "AUX1", "TX_P", "AUX2", "TX_N", "RX_N"],
        sparameter_mode="differential",
        source_ports=["TX_P", "TX_N"],
        destination_ports=["RX_P", "RX_N"],
        frequency_start_ghz=1.0,
        frequency_stop_ghz=2.0,
        rl_target_db=-15.0,
        insertion_loss_min_db=-4.0,
        reference_impedance_ohm=100.0,
    )

    assert score["status"] == "pass"
    assert score["touchstone_kind"] == "s6p"
    assert score["return_loss_trace"] == "SDD(TX_P-TX_N,TX_P-TX_N)"
    assert score["insertion_loss_trace"] == "SDD(RX_P-RX_N,TX_P-TX_N)"
    assert score["rl_worst_db"] == pytest.approx(-18.416, abs=0.001)
    assert score["insertion_worst_db_in_band"] == pytest.approx(-3.098, abs=0.001)
