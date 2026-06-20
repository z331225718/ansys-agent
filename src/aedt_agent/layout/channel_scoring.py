from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any


def parse_touchstone(path: Path) -> list[dict[str, float]]:
    frequency_unit = "GHZ"
    data_format = "MA"
    port_count = _touchstone_port_count(path)
    expected_number_count = 1 + (2 * port_count * port_count)
    pending_numbers: list[float] = []
    samples: list[dict[str, float]] = []
    for raw_line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line[1:].split()
            if parts:
                frequency_unit = parts[0].upper()
            if len(parts) >= 3:
                data_format = parts[2].upper()
            continue
        if line.startswith("["):
            match = re.match(r"\[Number of Ports\]\s+([0-9]+)", line, re.IGNORECASE)
            if match:
                port_count = int(match.group(1))
                expected_number_count = 1 + (2 * port_count * port_count)
            continue
        pending_numbers.extend(float(item) for item in line.split())
        while len(pending_numbers) >= expected_number_count:
            numbers = pending_numbers[:expected_number_count]
            del pending_numbers[:expected_number_count]
            samples.append(
                _touchstone_sample(
                    numbers,
                    frequency_unit=frequency_unit,
                    data_format=data_format,
                    port_count=port_count,
                )
            )
    return samples


def parse_tdr_csv(path: Path) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    with Path(path).open("r", encoding="utf-8", errors="replace", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            time_value = _first_present(row, ["time_ps", "Time [ps]", "time"])
            impedance_value = _first_present(row, ["impedance_ohm", "Impedance [Ohm]", "impedance"])
            if time_value is None or impedance_value is None:
                continue
            rows.append({"time_ps": float(time_value), "impedance_ohm": float(impedance_value)})
    return rows


def score_channel_result(
    touchstone_path: Path,
    tdr_path: Path,
    *,
    frequency_start_ghz: float = 0.0,
    frequency_stop_ghz: float = 26.56,
    rl_target_db: float = -20.0,
    tdr_target_ohm: float = 100.0,
    tdr_tolerance_ohm: float = 5.0,
    sparameter_mode: str = "auto",
    tdr_observation_port: str = "",
) -> dict[str, Any]:
    sparameters = [
        sample
        for sample in parse_touchstone(touchstone_path)
        if frequency_start_ghz <= sample["frequency_ghz"] <= frequency_stop_ghz
    ]
    if not sparameters:
        raise ValueError("Touchstone frequency window contains no samples")
    trace_config = _trace_config(sparameters, sparameter_mode)
    rl_key = trace_config["return_loss_key"]
    il_key = trace_config["insertion_loss_key"]
    touchstone_port_count = int(sparameters[0].get("port_count", 0))
    touchstone_kind = f"s{touchstone_port_count}p" if touchstone_port_count else "unknown"
    tdr_samples = parse_tdr_csv(tdr_path)
    if not tdr_samples:
        raise ValueError("TDR artifact contains no samples")
    worst = max(sparameters, key=lambda sample: sample[rl_key])
    worst_il = min(sparameters, key=lambda sample: sample[il_key])
    peak = max(tdr_samples, key=lambda sample: abs(sample["impedance_ohm"] - tdr_target_ohm))
    peak_deviation = abs(peak["impedance_ohm"] - tdr_target_ohm)
    tdr_metrics = _tdr_objective_metrics(tdr_samples, tdr_target_ohm)
    rl_metrics = _rl_objective_metrics(sparameters, rl_target_db, rl_key)
    objective = _optimization_objective(
        rl_metrics,
        tdr_metrics,
        tdr_tolerance_ohm=tdr_tolerance_ohm,
    )
    status = "pass" if worst[rl_key] <= rl_target_db and peak_deviation <= tdr_tolerance_ohm else "fail"
    score: dict[str, Any] = {
        "status": status,
        "touchstone_kind": touchstone_kind,
        "sparameter_mode": trace_config["mode"],
        "return_loss_trace": trace_config["return_loss_trace"],
        "insertion_loss_trace": trace_config["insertion_loss_trace"],
        "frequency_start_ghz": frequency_start_ghz,
        "frequency_stop_ghz": frequency_stop_ghz,
        "rl_target_db": rl_target_db,
        "rl_worst_db": round(worst[rl_key], 3),
        "rl_worst_frequency_ghz": worst["frequency_ghz"],
        "rl_pass_band": _pass_bands(sparameters, rl_target_db, rl_key),
        "insertion_worst_db_in_band": round(worst_il[il_key], 3),
        "insertion_worst_frequency_ghz": worst_il["frequency_ghz"],
        "tdr_target_ohm": tdr_target_ohm,
        "tdr_tolerance_ohm": tdr_tolerance_ohm,
        "tdr_observation_port": tdr_observation_port,
        "tdr_peak_deviation_ohm": round(peak_deviation, 3),
        "tdr_peak_time_ps": peak["time_ps"],
        "tdr_anomaly_window": _tdr_window(tdr_samples, peak["time_ps"]),
        "tdr_mean_impedance_ohm": tdr_metrics["mean_impedance_ohm"],
        "tdr_min_impedance_ohm": tdr_metrics["min_impedance_ohm"],
        "tdr_max_impedance_ohm": tdr_metrics["max_impedance_ohm"],
        "tdr_peak_to_peak_ohm": tdr_metrics["peak_to_peak_ohm"],
        "tdr_proximity_mse_ohm2": tdr_metrics["proximity_mse_ohm2"],
        "tdr_proximity_rmse_ohm": tdr_metrics["proximity_rmse_ohm"],
        "tdr_flatness_msd_ohm2": tdr_metrics["flatness_msd_ohm2"],
        "tdr_flatness_rms_step_ohm": tdr_metrics["flatness_rms_step_ohm"],
        "rl_violation_sum_db": rl_metrics["violation_sum_db"],
        "rl_violation_max_db": rl_metrics["violation_max_db"],
        "rl_violation_point_count": rl_metrics["violation_point_count"],
        "optimization_objective": objective,
        "samples": {"sparameter_count": len(sparameters), "tdr_count": len(tdr_samples)},
        "sources": {"touchstone": str(touchstone_path), "tdr": str(tdr_path)},
        "diagnosis": _diagnosis(
            worst[rl_key],
            rl_target_db,
            peak_deviation,
            tdr_tolerance_ohm,
            trace_config["return_loss_trace"],
            frequency_stop_ghz,
        ),
    }
    if trace_config["mode"] == "differential":
        score["sdd11_worst_db"] = score["rl_worst_db"]
        score["sdd11_worst_frequency_ghz"] = score["rl_worst_frequency_ghz"]
        score["sdd21_worst_db_in_band"] = score["insertion_worst_db_in_band"]
        score["sdd21_worst_frequency_ghz"] = score["insertion_worst_frequency_ghz"]
    else:
        score["s11_worst_db"] = score["rl_worst_db"]
        score["s11_worst_frequency_ghz"] = score["rl_worst_frequency_ghz"]
        score["s21_worst_db_in_band"] = score["insertion_worst_db_in_band"]
        score["s21_worst_frequency_ghz"] = score["insertion_worst_frequency_ghz"]
    return score


def compare_channel_scores(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    rl_delta = round(float(after["rl_worst_db"]) - float(before["rl_worst_db"]), 3)
    tdr_delta = round(float(after["tdr_peak_deviation_ohm"]) - float(before["tdr_peak_deviation_ohm"]), 3)
    flatness_delta = _optional_delta(after, before, "tdr_flatness_msd_ohm2")
    objective_delta = _objective_delta(after, before)
    rl_improved = rl_delta < 0
    tdr_improved = tdr_delta < 0
    objective_improved = objective_delta is not None and objective_delta < 0
    if rl_improved and (tdr_improved or objective_improved):
        status = "improved"
        summary = "RL 改善，TDR 目标函数也改善。"
    elif not rl_improved and not tdr_improved and not objective_improved:
        status = "regressed" if rl_delta > 0 or tdr_delta > 0 or (objective_delta or 0) > 0 else "unchanged"
        summary = "RL/TDR 未改善，需要回退或更换调整方向。" if status == "regressed" else "指标基本不变。"
    else:
        status = "mixed"
        summary = "RL 和 TDR 变化方向不一致，需要工程师复核。"
    return {
        "status": status,
        "rl_worst_delta_db": rl_delta,
        "tdr_peak_deviation_delta_ohm": tdr_delta,
        "tdr_flatness_msd_delta_ohm2": flatness_delta,
        "objective_total_cost_delta": objective_delta,
        "summary": summary,
        "before": before,
        "after": after,
    }


def _complex_value(first: float, second: float, data_format: str) -> complex:
    if data_format == "RI":
        return complex(first, second)
    if data_format == "DB":
        magnitude = 10 ** (first / 20.0)
        angle = math.radians(second)
        return complex(magnitude * math.cos(angle), magnitude * math.sin(angle))
    angle = math.radians(second)
    return complex(first * math.cos(angle), first * math.sin(angle))


def _touchstone_port_count(path: Path) -> int:
    match = re.search(r"\.s([1-9][0-9]*)p$", str(path), re.IGNORECASE)
    return int(match.group(1)) if match else 2


def _touchstone_sample(
    numbers: list[float],
    *,
    frequency_unit: str,
    data_format: str,
    port_count: int,
) -> dict[str, float]:
    frequency_ghz = _to_ghz(numbers[0], frequency_unit)
    matrix = _touchstone_matrix(numbers[1:], port_count, data_format)
    single_ended_s11 = matrix[0][0]
    single_ended_s21 = (
        matrix[1][0] if port_count >= 2 else complex(0.0, 0.0)
    )
    sample: dict[str, float] = {
        "frequency_ghz": frequency_ghz,
        "port_count": float(port_count),
        "single_ended_s11_db": _mag_to_db(abs(single_ended_s11)),
        "single_ended_s21_db": _mag_to_db(abs(single_ended_s21))
        if single_ended_s21
        else float("-inf"),
    }
    if port_count >= 4:
        sdd11 = 0.5 * (
            matrix[0][0]
            - matrix[0][1]
            - matrix[1][0]
            + matrix[1][1]
        )
        sdd21 = 0.5 * (
            matrix[2][0]
            - matrix[2][1]
            - matrix[3][0]
            + matrix[3][1]
        )
        sample.update(
            {
                "sdd11_db": _mag_to_db(abs(sdd11)),
                "sdd21_db": _mag_to_db(abs(sdd21))
                if sdd21
                else float("-inf"),
            }
        )
        sample["s11_db"] = sample["sdd11_db"]
        sample["s21_db"] = sample["sdd21_db"]
    else:
        sample["s11_db"] = sample["single_ended_s11_db"]
        sample["s21_db"] = sample["single_ended_s21_db"]
    return sample


def _touchstone_matrix(
    values: list[float],
    port_count: int,
    data_format: str,
) -> list[list[complex]]:
    pairs = [
        _complex_value(values[index], values[index + 1], data_format)
        for index in range(0, 2 * port_count * port_count, 2)
    ]
    matrix = [
        [complex(0.0, 0.0) for _ in range(port_count)]
        for _ in range(port_count)
    ]
    if port_count == 2:
        # Touchstone 1.x two-port files use S11, S21, S12, S22 order.
        matrix[0][0], matrix[1][0], matrix[0][1], matrix[1][1] = pairs
        return matrix
    index = 0
    for row in range(port_count):
        for column in range(port_count):
            matrix[row][column] = pairs[index]
            index += 1
    return matrix


def _trace_config(
    samples: list[dict[str, float]],
    sparameter_mode: str,
) -> dict[str, str]:
    mode = sparameter_mode.strip().casefold()
    if mode == "auto":
        mode = "differential" if "sdd11_db" in samples[0] else "single_ended"
    if mode in {"diff", "mixed_mode"}:
        mode = "differential"
    if mode in {"single", "single-ended"}:
        mode = "single_ended"
    if mode == "differential":
        if "sdd11_db" not in samples[0] or "sdd21_db" not in samples[0]:
            raise ValueError("differential scoring requires a 4-port Touchstone artifact")
        return {
            "mode": "differential",
            "return_loss_key": "sdd11_db",
            "insertion_loss_key": "sdd21_db",
            "return_loss_trace": "SDD11",
            "insertion_loss_trace": "SDD21",
        }
    if mode != "single_ended":
        raise ValueError("sparameter_mode must be auto, differential, or single_ended")
    return {
        "mode": "single_ended",
        "return_loss_key": "s11_db",
        "insertion_loss_key": "s21_db",
        "return_loss_trace": "S11",
        "insertion_loss_trace": "S21",
    }


def _to_ghz(value: float, unit: str) -> float:
    if unit == "HZ":
        return value / 1e9
    if unit == "KHZ":
        return value / 1e6
    if unit == "MHZ":
        return value / 1e3
    return value


def _mag_to_db(value: float) -> float:
    if value <= 0:
        return float("-inf")
    return 20.0 * math.log10(value)


def _first_present(row: dict[str, str], names: list[str]) -> str | None:
    for name in names:
        if name in row and row[name] not in {None, ""}:
            return row[name]
    return None


def _pass_bands(
    samples: list[dict[str, float]],
    target_db: float,
    trace_key: str = "s11_db",
) -> list[dict[str, float]]:
    bands = []
    start = None
    last = None
    for sample in samples:
        if sample[trace_key] <= target_db:
            start = sample["frequency_ghz"] if start is None else start
            last = sample["frequency_ghz"]
        elif start is not None and last is not None:
            bands.append({"start_ghz": start, "stop_ghz": last})
            start = None
            last = None
    if start is not None and last is not None:
        bands.append({"start_ghz": start, "stop_ghz": last})
    return bands


def _tdr_window(samples: list[dict[str, float]], peak_time_ps: float) -> dict[str, float]:
    times = [sample["time_ps"] for sample in samples]
    if not times:
        return {"start_ps": 0.0, "stop_ps": 0.0}
    before = max([time for time in times if time < peak_time_ps], default=peak_time_ps)
    after = min([time for time in times if time > peak_time_ps], default=peak_time_ps)
    return {"start_ps": before, "stop_ps": after}


def _tdr_objective_metrics(
    samples: list[dict[str, float]],
    target_ohm: float,
) -> dict[str, float]:
    impedances = [float(sample["impedance_ohm"]) for sample in samples]
    mean_impedance = sum(impedances) / len(impedances)
    deviations = [value - target_ohm for value in impedances]
    proximity_mse = sum(value * value for value in deviations) / len(deviations)
    steps = [
        impedances[index] - impedances[index - 1]
        for index in range(1, len(impedances))
    ]
    flatness_msd = (
        sum(step * step for step in steps) / len(steps)
        if steps
        else 0.0
    )
    return {
        "mean_impedance_ohm": round(mean_impedance, 3),
        "min_impedance_ohm": round(min(impedances), 3),
        "max_impedance_ohm": round(max(impedances), 3),
        "peak_to_peak_ohm": round(max(impedances) - min(impedances), 3),
        "proximity_mse_ohm2": round(proximity_mse, 3),
        "proximity_rmse_ohm": round(math.sqrt(proximity_mse), 3),
        "flatness_msd_ohm2": round(flatness_msd, 3),
        "flatness_rms_step_ohm": round(math.sqrt(flatness_msd), 3),
    }


def _rl_objective_metrics(
    samples: list[dict[str, float]],
    target_db: float,
    trace_key: str,
) -> dict[str, float | int]:
    violations = [
        max(0.0, float(sample[trace_key]) - target_db)
        for sample in samples
    ]
    violating = [value for value in violations if value > 0.0]
    return {
        "violation_sum_db": round(sum(violations), 3),
        "violation_max_db": round(max(violations, default=0.0), 3),
        "violation_mean_db": round(
            sum(violations) / len(violations),
            3,
        ),
        "violation_point_count": len(violating),
    }


def _optimization_objective(
    rl_metrics: dict[str, float | int],
    tdr_metrics: dict[str, float],
    *,
    tdr_tolerance_ohm: float,
) -> dict[str, Any]:
    tolerance = max(float(tdr_tolerance_ohm), 1e-9)
    tdr_proximity_cost = float(tdr_metrics["proximity_mse_ohm2"]) / (tolerance * tolerance)
    tdr_flatness_cost = float(tdr_metrics["flatness_msd_ohm2"]) / (tolerance * tolerance)
    rl_cost = float(rl_metrics["violation_sum_db"])
    total_cost = rl_cost + tdr_proximity_cost + tdr_flatness_cost
    return {
        "version": 1,
        "strategy": "rl_violation_plus_tdr_proximity_flatness",
        "weights": {
            "rl_violation_sum": 1.0,
            "tdr_proximity_mse_normalized": 1.0,
            "tdr_flatness_msd_normalized": 1.0,
        },
        "components": {
            "rl_violation_sum_db": round(rl_cost, 3),
            "tdr_proximity_mse_normalized": round(tdr_proximity_cost, 6),
            "tdr_flatness_msd_normalized": round(tdr_flatness_cost, 6),
        },
        "total_cost": round(total_cost, 6),
    }


def _optional_delta(
    after: dict[str, Any],
    before: dict[str, Any],
    key: str,
) -> float | None:
    if key not in after or key not in before:
        return None
    return round(float(after[key]) - float(before[key]), 3)


def _objective_delta(
    after: dict[str, Any],
    before: dict[str, Any],
) -> float | None:
    after_objective = after.get("optimization_objective") or {}
    before_objective = before.get("optimization_objective") or {}
    if "total_cost" not in after_objective or "total_cost" not in before_objective:
        return None
    return round(float(after_objective["total_cost"]) - float(before_objective["total_cost"]), 6)


def _diagnosis(
    rl_worst_db: float,
    rl_target_db: float,
    tdr_peak_deviation_ohm: float,
    tdr_tolerance_ohm: float,
    return_loss_trace: str = "S11",
    frequency_stop_ghz: float = 26.56,
) -> list[str]:
    messages = []
    if rl_worst_db > rl_target_db:
        messages.append(
            f"0-{frequency_stop_ghz:g}GHz 内 {return_loss_trace}/RL "
            f"未达到 {rl_target_db:g}dB 目标。"
        )
    if tdr_peak_deviation_ohm > tdr_tolerance_ohm:
        messages.append(f"TDR 最大偏差 {tdr_peak_deviation_ohm:.1f}ohm，建议检查对应过孔 transition 附近挖空。")
    if not messages:
        messages.append("RL 和 TDR 指标满足当前离线评分目标。")
    return messages
