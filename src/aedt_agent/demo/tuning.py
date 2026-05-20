from __future__ import annotations

import math
import re
from typing import Any, Callable

TuningAdvisor = Callable[[dict[str, Any]], dict[str, Any]]


def find_s11_resonance(samples: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [sample for sample in samples if _is_number(sample.get("frequency_hz")) and _is_number(sample.get("s11_db"))]
    if not candidates:
        raise ValueError("S11 samples do not contain numeric frequency_hz and s11_db values")
    return min(candidates, key=lambda sample: float(sample["s11_db"]))


def next_dipole_arm_length(
    *,
    current_length_mm: float,
    resonance_frequency_hz: float,
    target_frequency_hz: float,
    min_ratio: float = 0.80,
    max_ratio: float = 1.20,
) -> float:
    if current_length_mm <= 0:
        raise ValueError("current_length_mm must be positive")
    if resonance_frequency_hz <= 0 or target_frequency_hz <= 0:
        raise ValueError("frequencies must be positive")
    ratio = resonance_frequency_hz / target_frequency_hz
    ratio = max(min_ratio, min(max_ratio, ratio))
    return round(current_length_mm * ratio, 3)


def run_fake_dipole_tuning(
    *,
    target_frequency: str,
    initial_arm_length_mm: float,
    sweep_start: str,
    sweep_stop: str,
    max_rounds: int = 3,
    tolerance: float = 0.02,
    velocity_factor: float = 0.95,
    advisor: TuningAdvisor | None = None,
) -> dict[str, Any]:
    target_hz = _parse_frequency_hz(target_frequency)
    start_hz = _parse_frequency_hz(sweep_start)
    stop_hz = _parse_frequency_hz(sweep_stop)
    if target_hz is None:
        raise ValueError("target_frequency must be a frequency string such as 2.5GHz")
    if start_hz is None or stop_hz is None or start_hz >= stop_hz:
        raise ValueError("sweep_start and sweep_stop must be valid ascending frequency strings")
    if initial_arm_length_mm <= 0:
        raise ValueError("initial_arm_length_mm must be positive")

    ideal_length = _quarter_wave_arm_length_mm(target_hz, velocity_factor)
    rounds: list[dict[str, Any]] = []
    current_length = round(float(initial_arm_length_mm), 3)
    status = "max_rounds"
    max_rounds = max(1, int(max_rounds))
    for index in range(1, max_rounds + 1):
        resonance_hz = target_hz * ideal_length / current_length
        samples = _synthetic_s11_sweep(start_hz, stop_hz, resonance_hz)
        resonance = find_s11_resonance(samples)
        error_ratio = (float(resonance["frequency_hz"]) - target_hz) / target_hz
        converged = abs(error_ratio) <= tolerance
        default_next = current_length if converged else next_dipole_arm_length(
            current_length_mm=current_length,
            resonance_frequency_hz=float(resonance["frequency_hz"]),
            target_frequency_hz=target_hz,
        )
        advice = _advisor_advice(
            advisor,
            {
                "round": index,
                "target_frequency_hz": target_hz,
                "resonance_frequency_hz": float(resonance["frequency_hz"]),
                "target_error_percent": round(error_ratio * 100.0, 3),
                "current_arm_length_mm": current_length,
                "default_next_arm_length_mm": default_next,
                "controlled_variable": "dipole_arm_length_mm",
                "samples": samples,
            },
        )
        next_length = current_length if converged else float(advice.get("next_arm_length_mm", default_next))
        next_length = round(max(0.1, min(current_length * 1.2, max(current_length * 0.8, next_length))), 3)
        message = str(advice.get("message") or _agent_message(error_ratio, current_length, next_length, converged))
        rounds.append(
            {
                "round": index,
                "arm_length_mm": current_length,
                "resonance_frequency_hz": float(resonance["frequency_hz"]),
                "resonance_frequency": _format_ghz(float(resonance["frequency_hz"])),
                "target_error_percent": round(error_ratio * 100.0, 3),
                "s11_db": resonance["s11_db"],
                "next_arm_length_mm": next_length,
                "samples": samples,
                "agent_message": message,
                "converged": converged,
            }
        )
        if converged:
            status = "converged"
            break
        current_length = next_length
    return {
        "status": status,
        "target_frequency": target_frequency,
        "target_frequency_hz": target_hz,
        "sweep_start": sweep_start,
        "sweep_stop": sweep_stop,
        "initial_arm_length_mm": round(float(initial_arm_length_mm), 3),
        "final_arm_length_mm": rounds[-1]["next_arm_length_mm"],
        "rounds": rounds,
    }


def _advisor_advice(advisor: TuningAdvisor | None, context: dict[str, Any]) -> dict[str, Any]:
    if advisor is None:
        return {}
    try:
        advice = advisor(context)
    except Exception as exc:
        return {"message": f"LLM 调参建议调用失败，使用工程规则兜底：{type(exc).__name__}: {exc}"}
    return advice if isinstance(advice, dict) else {}


def _quarter_wave_arm_length_mm(frequency_hz: float, velocity_factor: float) -> float:
    return 299_792_458.0 / (4.0 * frequency_hz) * 1000.0 * velocity_factor


def _synthetic_s11_sweep(start_hz: float, stop_hz: float, resonance_hz: float) -> list[dict[str, Any]]:
    points = [start_hz + (stop_hz - start_hz) * index / 30 for index in range(31)]
    if start_hz <= resonance_hz <= stop_hz:
        points.append(resonance_hz)
    samples = []
    for frequency_hz in sorted(set(round(point, 3) for point in points)):
        distance = abs(math.log(max(frequency_hz, 1.0) / max(resonance_hz, 1.0)))
        s11_db = -28.0 + min(24.0, distance * 70.0)
        samples.append(
            {
                "frequency": round(frequency_hz / 1e9, 6),
                "frequency_hz": float(frequency_hz),
                "s11_mag": 10 ** (s11_db / 20.0),
                "s21_mag": None,
                "s11_db": round(s11_db, 3),
                "s21_db": None,
            }
        )
    return samples


def _agent_message(error_ratio: float, current_length: float, next_length: float, converged: bool) -> str:
    if converged:
        return f"谐振点已经落入目标频率容差内，保持单臂长度 {current_length:.3f} mm。"
    if error_ratio < 0:
        return f"谐振频率偏低，说明偶极子偏长；将单臂长度从 {current_length:.3f} mm 缩短到 {next_length:.3f} mm。"
    return f"谐振频率偏高，说明偶极子偏短；将单臂长度从 {current_length:.3f} mm 加长到 {next_length:.3f} mm。"


def _parse_frequency_hz(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([GMK]?Hz)\s*", value, flags=re.IGNORECASE)
    if not match:
        return None
    scale = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
    return float(match.group(1)) * scale[match.group(2).lower()]


def _format_ghz(frequency_hz: float) -> str:
    return f"{frequency_hz / 1e9:.4g}GHz"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))
