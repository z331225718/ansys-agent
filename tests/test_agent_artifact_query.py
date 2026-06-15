from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aedt_agent.agent.evaluation import (
    query_sparameter_artifact,
    query_tdr_artifact,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_dense_touchstone(path: Path) -> None:
    lines = ["# GHz S MA R 50"]
    for index in range(1341):
        frequency = index * 0.05
        magnitude = 0.5 if frequency == 18.0 else 0.05
        lines.append(
            f"{frequency:.2f} {magnitude} 0 0.9 0 0.9 0 0.05 0"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_dense_tdr(path: Path) -> None:
    lines = ["time_ps,impedance_ohm"]
    for index in range(1000):
        impedance = 115.0 if index == 510 else 100.0
        lines.append(f"{index},{impedance}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_query_sparameter_artifact_limits_points_and_returns_digest(
    tmp_path,
):
    touchstone = tmp_path / "dense.s2p"
    _write_dense_touchstone(touchstone)

    result = query_sparameter_artifact(
        touchstone,
        17.0,
        19.0,
        max_points=8,
        rl_target_db=-20.0,
    )

    assert result["point_count"] <= 8
    assert result["artifact_sha256"] == _sha256(touchstone)
    assert result["window_summary"]["sample_count"] == 41
    assert any(
        point["frequency_ghz"] == 18.0
        for point in result["points"]
    )


def test_query_tdr_artifact_preserves_peak_and_limits_points(tmp_path):
    tdr = tmp_path / "dense_tdr.csv"
    _write_dense_tdr(tdr)

    result = query_tdr_artifact(
        tdr,
        500.0,
        520.0,
        max_points=8,
        target_ohm=100.0,
    )

    assert result["point_count"] <= 8
    assert result["window_summary"]["peak_time_ps"] == 510.0
    assert result["window_summary"]["peak_deviation_ohm"] == 15.0


@pytest.mark.parametrize("max_points", [1, 129])
def test_artifact_query_rejects_unbounded_point_limits(
    tmp_path,
    max_points,
):
    touchstone = tmp_path / "dense.s2p"
    _write_dense_touchstone(touchstone)

    with pytest.raises(ValueError, match="max_points"):
        query_sparameter_artifact(
            touchstone,
            0,
            1,
            max_points=max_points,
        )


def test_artifact_query_rejects_missing_and_empty_windows(tmp_path):
    with pytest.raises(FileNotFoundError):
        query_tdr_artifact(tmp_path / "missing.csv", 0, 1)

    tdr = tmp_path / "dense.csv"
    _write_dense_tdr(tdr)
    with pytest.raises(ValueError, match="no TDR samples"):
        query_tdr_artifact(tdr, 2000, 3000)
