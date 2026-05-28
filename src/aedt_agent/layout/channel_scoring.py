from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any


def parse_touchstone(path: Path) -> list[dict[str, float]]:
    frequency_unit = "GHZ"
    data_format = "MA"
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
        numbers = [float(item) for item in line.split()]
        if len(numbers) < 3:
            continue
        frequency_ghz = _to_ghz(numbers[0], frequency_unit)
        s11 = _complex_value(numbers[1], numbers[2], data_format)
        s21 = _complex_value(numbers[3], numbers[4], data_format) if len(numbers) >= 5 else complex(0.0, 0.0)
        samples.append(
            {
                "frequency_ghz": frequency_ghz,
                "s11_db": _mag_to_db(abs(s11)),
                "s21_db": _mag_to_db(abs(s21)) if s21 else float("-inf"),
            }
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
) -> dict[str, Any]:
    sparameters = [
        sample
        for sample in parse_touchstone(touchstone_path)
        if frequency_start_ghz <= sample["frequency_ghz"] <= frequency_stop_ghz
    ]
    tdr_samples = parse_tdr_csv(tdr_path)
    worst = max(sparameters, key=lambda sample: sample["s11_db"])
    peak = max(tdr_samples, key=lambda sample: abs(sample["impedance_ohm"] - tdr_target_ohm))
    peak_deviation = abs(peak["impedance_ohm"] - tdr_target_ohm)
    status = "pass" if worst["s11_db"] <= rl_target_db and peak_deviation <= 5.0 else "fail"
    return {
        "status": status,
        "frequency_start_ghz": frequency_start_ghz,
        "frequency_stop_ghz": frequency_stop_ghz,
        "rl_target_db": rl_target_db,
        "rl_worst_db": round(worst["s11_db"], 3),
        "rl_worst_frequency_ghz": worst["frequency_ghz"],
        "rl_pass_band": _pass_bands(sparameters, rl_target_db),
        "tdr_target_ohm": tdr_target_ohm,
        "tdr_peak_deviation_ohm": round(peak_deviation, 3),
        "tdr_peak_time_ps": peak["time_ps"],
        "tdr_anomaly_window": _tdr_window(tdr_samples, peak["time_ps"]),
        "samples": {"sparameter_count": len(sparameters), "tdr_count": len(tdr_samples)},
        "sources": {"touchstone": str(touchstone_path), "tdr": str(tdr_path)},
        "diagnosis": _diagnosis(worst["s11_db"], rl_target_db, peak_deviation),
    }


def compare_channel_scores(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    rl_delta = round(float(after["rl_worst_db"]) - float(before["rl_worst_db"]), 3)
    tdr_delta = round(float(after["tdr_peak_deviation_ohm"]) - float(before["tdr_peak_deviation_ohm"]), 3)
    rl_improved = rl_delta < 0
    tdr_improved = tdr_delta < 0
    if rl_improved and tdr_improved:
        status = "improved"
        summary = "RL 改善，TDR 也更平滑。"
    elif not rl_improved and not tdr_improved:
        status = "regressed" if rl_delta > 0 or tdr_delta > 0 else "unchanged"
        summary = "RL/TDR 未改善，需要回退或更换调整方向。" if status == "regressed" else "指标基本不变。"
    else:
        status = "mixed"
        summary = "RL 和 TDR 变化方向不一致，需要工程师复核。"
    return {
        "status": status,
        "rl_worst_delta_db": rl_delta,
        "tdr_peak_deviation_delta_ohm": tdr_delta,
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


def _pass_bands(samples: list[dict[str, float]], target_db: float) -> list[dict[str, float]]:
    bands = []
    start = None
    last = None
    for sample in samples:
        if sample["s11_db"] <= target_db:
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


def _diagnosis(rl_worst_db: float, rl_target_db: float, tdr_peak_deviation_ohm: float) -> list[str]:
    messages = []
    if rl_worst_db > rl_target_db:
        messages.append(f"0-26.56GHz 内 RL 未达到 {rl_target_db:g}dB 目标。")
    if tdr_peak_deviation_ohm > 5.0:
        messages.append(f"TDR 最大偏差 {tdr_peak_deviation_ohm:.1f}ohm，建议检查对应过孔 transition 附近挖空。")
    if not messages:
        messages.append("RL 和 TDR 指标满足当前离线评分目标。")
    return messages
