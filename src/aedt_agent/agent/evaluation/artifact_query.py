from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from aedt_agent.agent.evaluation.spectral import (
    query_sparameter_window,
)
from aedt_agent.layout.channel_scoring import (
    parse_tdr_csv,
    parse_touchstone,
)


def query_sparameter_artifact(
    path: str | Path,
    frequency_start_ghz: float,
    frequency_stop_ghz: float,
    *,
    max_points: int = 64,
    rl_target_db: float = -20.0,
) -> dict[str, Any]:
    _validate_max_points(max_points)
    source = _validated_source(path)
    result = query_sparameter_window(
        trace_id=str(source),
        samples=parse_touchstone(source),
        frequency_start_ghz=float(frequency_start_ghz),
        frequency_stop_ghz=float(frequency_stop_ghz),
        max_points=max_points,
        rl_target_db=float(rl_target_db),
    )
    return {
        "artifact_ref": str(source),
        "artifact_sha256": _sha256(source),
        **result,
    }


def query_tdr_artifact(
    path: str | Path,
    time_start_ps: float,
    time_stop_ps: float,
    *,
    max_points: int = 64,
    target_ohm: float = 100.0,
) -> dict[str, Any]:
    _validate_max_points(max_points)
    source = _validated_source(path)
    samples = [
        sample
        for sample in parse_tdr_csv(source)
        if float(time_start_ps)
        <= sample["time_ps"]
        <= float(time_stop_ps)
    ]
    if not samples:
        raise ValueError("query window contains no TDR samples")
    peak = max(
        samples,
        key=lambda sample: abs(
            sample["impedance_ohm"] - target_ohm
        ),
    )
    points = _extrema_preserving_tdr_points(
        samples,
        max_points,
        target_ohm,
    )
    return {
        "artifact_ref": str(source),
        "artifact_sha256": _sha256(source),
        "time_start_ps": float(time_start_ps),
        "time_stop_ps": float(time_stop_ps),
        "point_count": len(points),
        "points": points,
        "window_summary": {
            "sample_count": len(samples),
            "target_ohm": float(target_ohm),
            "peak_time_ps": peak["time_ps"],
            "peak_impedance_ohm": peak["impedance_ohm"],
            "peak_deviation_ohm": abs(
                peak["impedance_ohm"] - target_ohm
            ),
        },
    }


def _extrema_preserving_tdr_points(
    samples: list[dict[str, float]],
    max_points: int,
    target_ohm: float,
) -> list[dict[str, float]]:
    if len(samples) <= max_points:
        return list(samples)
    selected: dict[float, dict[str, float]] = {}

    def add(sample: dict[str, float]) -> None:
        if len(selected) < max_points:
            selected[sample["time_ps"]] = sample

    for sample in (
        samples[0],
        samples[-1],
        min(samples, key=lambda item: item["impedance_ohm"]),
        max(samples, key=lambda item: item["impedance_ohm"]),
        max(
            samples,
            key=lambda item: abs(
                item["impedance_ohm"] - target_ohm
            ),
        ),
    ):
        add(sample)
    bucket_count = max(1, (max_points - len(selected)) // 2)
    for index in range(bucket_count):
        start = index * len(samples) // bucket_count
        stop = (index + 1) * len(samples) // bucket_count
        chunk = samples[start:stop]
        if not chunk:
            continue
        add(min(chunk, key=lambda item: item["impedance_ohm"]))
        add(max(chunk, key=lambda item: item["impedance_ohm"]))
    return [selected[key] for key in sorted(selected)][:max_points]


def _validate_max_points(max_points: int) -> None:
    if (
        not isinstance(max_points, int)
        or isinstance(max_points, bool)
        or not 2 <= max_points <= 128
    ):
        raise ValueError("max_points must be between 2 and 128")


def _validated_source(path: str | Path) -> Path:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"artifact not found: {source}")
    return source


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
