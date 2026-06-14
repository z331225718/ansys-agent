from __future__ import annotations

from statistics import mean
from typing import Any


def build_sparameter_evidence(
    *,
    trace_id: str,
    samples: list[dict[str, float]],
    artifact_ref: str,
    rl_target_db: float,
    bucket_count: int = 128,
) -> dict[str, Any]:
    ordered = _ordered_samples(samples)
    if not ordered:
        raise ValueError("samples must not be empty")
    buckets = _extrema_preserving_buckets(ordered, bucket_count, rl_target_db)
    worst = _worst_rl(ordered)
    return {
        "trace_id": trace_id,
        "raw_trace_policy": "artifact_only",
        "artifact_refs": [artifact_ref],
        "summary": {
            "frequency_start_ghz": ordered[0]["frequency_ghz"],
            "frequency_stop_ghz": ordered[-1]["frequency_ghz"],
            "sample_count": len(ordered),
            "rl_target_db": rl_target_db,
            "rl_worst_db": worst["s11_db"],
            "rl_worst_frequency_ghz": worst["frequency_ghz"],
            "failure_windows": _failure_windows(ordered, rl_target_db),
            "buckets": buckets,
        },
    }


def query_sparameter_window(
    *,
    trace_id: str,
    samples: list[dict[str, float]],
    frequency_start_ghz: float,
    frequency_stop_ghz: float,
    max_points: int = 128,
    rl_target_db: float = -20.0,
) -> dict[str, Any]:
    if max_points < 2:
        raise ValueError("max_points must be at least 2")
    window = [
        sample
        for sample in _ordered_samples(samples)
        if frequency_start_ghz <= sample["frequency_ghz"] <= frequency_stop_ghz
    ]
    if not window:
        raise ValueError("query window contains no samples")
    points = _window_points(window, max_points)
    worst = _worst_rl(window)
    return {
        "trace_id": trace_id,
        "frequency_start_ghz": frequency_start_ghz,
        "frequency_stop_ghz": frequency_stop_ghz,
        "point_count": len(points),
        "points": points,
        "window_summary": {
            "sample_count": len(window),
            "rl_target_db": rl_target_db,
            "rl_worst_db": worst["s11_db"],
            "rl_worst_frequency_ghz": worst["frequency_ghz"],
            "failure_windows": _failure_windows(window, rl_target_db),
        },
    }


def _ordered_samples(samples: list[dict[str, float]]) -> list[dict[str, float]]:
    return sorted(
        [
            {
                "frequency_ghz": float(sample["frequency_ghz"]),
                "s11_db": float(sample["s11_db"]),
                "s21_db": float(sample.get("s21_db", 0.0)),
            }
            for sample in samples
        ],
        key=lambda sample: sample["frequency_ghz"],
    )


def _worst_rl(samples: list[dict[str, float]]) -> dict[str, float]:
    return max(samples, key=lambda sample: sample["s11_db"])


def _extrema_preserving_buckets(
    samples: list[dict[str, float]],
    bucket_count: int,
    rl_target_db: float,
) -> list[dict[str, Any]]:
    if bucket_count < 1:
        raise ValueError("bucket_count must be positive")
    if len(samples) <= bucket_count:
        return [_bucket_summary([sample], rl_target_db) for sample in samples]

    buckets: list[dict[str, Any]] = []
    for index in range(bucket_count):
        start = index * len(samples) // bucket_count
        stop = (index + 1) * len(samples) // bucket_count
        chunk = samples[start:stop]
        if chunk:
            buckets.append(_bucket_summary(chunk, rl_target_db))
    return buckets


def _bucket_summary(samples: list[dict[str, float]], rl_target_db: float) -> dict[str, Any]:
    min_sample = min(samples, key=lambda sample: sample["s11_db"])
    max_sample = max(samples, key=lambda sample: sample["s11_db"])
    return {
        "frequency_start_ghz": samples[0]["frequency_ghz"],
        "frequency_stop_ghz": samples[-1]["frequency_ghz"],
        "min_db": min_sample["s11_db"],
        "min_frequency_ghz": min_sample["frequency_ghz"],
        "max_db": max_sample["s11_db"],
        "max_frequency_ghz": max_sample["frequency_ghz"],
        "mean_db": round(mean(sample["s11_db"] for sample in samples), 6),
        "first_db": samples[0]["s11_db"],
        "last_db": samples[-1]["s11_db"],
        "threshold_crossings": _threshold_crossings(samples, rl_target_db),
        "local_extrema_count": _local_extrema_count(samples),
    }


def _threshold_crossings(samples: list[dict[str, float]], rl_target_db: float) -> int:
    if not samples:
        return 0
    crossings = 0
    previous_failed = samples[0]["s11_db"] > rl_target_db
    if previous_failed:
        crossings += 1
    for sample in samples[1:]:
        failed = sample["s11_db"] > rl_target_db
        if failed != previous_failed:
            crossings += 1
        previous_failed = failed
    return crossings


def _local_extrema_count(samples: list[dict[str, float]]) -> int:
    count = 0
    for index in range(1, len(samples) - 1):
        previous_value = samples[index - 1]["s11_db"]
        value = samples[index]["s11_db"]
        next_value = samples[index + 1]["s11_db"]
        if (value > previous_value and value > next_value) or (value < previous_value and value < next_value):
            count += 1
    return count


def _failure_windows(samples: list[dict[str, float]], rl_target_db: float) -> list[dict[str, float]]:
    windows: list[dict[str, float]] = []
    start: float | None = None
    stop: float | None = None
    worst: dict[str, float] | None = None
    for sample in samples:
        failed = sample["s11_db"] > rl_target_db
        if failed:
            start = sample["frequency_ghz"] if start is None else start
            stop = sample["frequency_ghz"]
            worst = sample if worst is None or sample["s11_db"] > worst["s11_db"] else worst
        elif start is not None and stop is not None and worst is not None:
            windows.append({"start_ghz": start, "stop_ghz": stop, "worst_db": worst["s11_db"]})
            start = None
            stop = None
            worst = None
    if start is not None and stop is not None and worst is not None:
        windows.append({"start_ghz": start, "stop_ghz": stop, "worst_db": worst["s11_db"]})
    return windows


def _window_points(samples: list[dict[str, float]], max_points: int) -> list[dict[str, float]]:
    if len(samples) <= max_points:
        return samples
    selected: dict[float, dict[str, float]] = {
        samples[0]["frequency_ghz"]: samples[0],
        samples[-1]["frequency_ghz"]: samples[-1],
    }
    for sample in (min(samples, key=lambda item: item["s11_db"]), max(samples, key=lambda item: item["s11_db"])):
        selected[sample["frequency_ghz"]] = sample
    bucket_budget = max_points - len(selected)
    if bucket_budget > 0:
        buckets = _extrema_preserving_buckets(samples, max(1, bucket_budget // 2), rl_target_db=max(item["s11_db"] for item in samples) + 1)
        for bucket in buckets:
            for frequency_key in ("min_frequency_ghz", "max_frequency_ghz"):
                if len(selected) >= max_points:
                    break
                frequency = float(bucket[frequency_key])
                selected[frequency] = next(sample for sample in samples if sample["frequency_ghz"] == frequency)
    return [selected[key] for key in sorted(selected)][:max_points]
