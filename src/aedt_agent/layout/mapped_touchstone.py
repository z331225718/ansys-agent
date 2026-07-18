from __future__ import annotations

import math
from pathlib import Path
from typing import Any


_SUPPORTED_MODES = frozenset({"single_ended", "differential"})


def score_mapped_touchstone(
    touchstone_path: str | Path,
    *,
    port_order: list[str],
    sparameter_mode: str,
    source_ports: list[str],
    destination_ports: list[str],
    frequency_start_ghz: float,
    frequency_stop_ghz: float,
    rl_target_db: float,
    insertion_loss_min_db: float,
    reference_impedance_ohm: float,
) -> dict[str, Any]:
    """Score an explicitly mapped single-ended or differential Touchstone path."""
    try:
        import numpy as np
        import skrf as rf
    except ModuleNotFoundError as exc:
        raise RuntimeError("scikit-rf and numpy are required for mapped Touchstone scoring") from exc

    path = Path(touchstone_path).resolve()
    if not path.is_file():
        raise ValueError(f"Touchstone artifact does not exist: {path}")
    mode = str(sparameter_mode).strip().casefold()
    if mode not in _SUPPORTED_MODES:
        raise ValueError("sparameter_mode must be single_ended or differential")
    names = _unique_nonempty_names(port_order, field="port_order")
    expected_port_count = 1 if mode == "single_ended" else 2
    sources = _mapped_ports(source_ports, names, expected_port_count, field="source_ports")
    destinations = _mapped_ports(
        destination_ports,
        names,
        expected_port_count,
        field="destination_ports",
    )
    if set(sources).intersection(destinations):
        raise ValueError("source_ports and destination_ports must not overlap")
    start = _finite_float(frequency_start_ghz, field="frequency_start_ghz")
    stop = _finite_float(frequency_stop_ghz, field="frequency_stop_ghz")
    rl_target = _finite_float(rl_target_db, field="rl_target_db")
    il_target = _finite_float(insertion_loss_min_db, field="insertion_loss_min_db")
    reference = _finite_float(reference_impedance_ohm, field="reference_impedance_ohm")
    if start < 0 or stop <= start:
        raise ValueError("frequency range must satisfy 0 <= start < stop")
    if rl_target > 0 or il_target > 0:
        raise ValueError("RL and insertion-loss limits must be non-positive dB values")
    if reference <= 0:
        raise ValueError("reference_impedance_ohm must be positive")

    try:
        network = rf.Network(str(path))
    except Exception as exc:
        raise ValueError(f"failed to parse Touchstone artifact: {path}") from exc
    if network.nports != len(names):
        raise ValueError(
            f"Touchstone has {network.nports} ports but port_order contains {len(names)} names"
        )
    file_port_names = _network_port_names(network)
    if file_port_names and file_port_names != names:
        raise ValueError(
            "Touchstone port names do not match the AEDT export snapshot port order"
        )

    scored_network = network.copy()
    target_single_ended_reference = reference if mode == "single_ended" else reference / 2.0
    try:
        scored_network.renormalize(target_single_ended_reference)
    except Exception as exc:
        raise ValueError(
            f"failed to renormalize Touchstone to {reference:g} ohm for {mode} scoring"
        ) from exc

    frequencies_ghz = np.asarray(scored_network.f, dtype=float) / 1e9
    window = (frequencies_ghz >= start) & (frequencies_ghz <= stop)
    if not bool(np.any(window)):
        raise ValueError("Touchstone frequency window contains no samples")
    source_indices = [names.index(item) for item in sources]
    destination_indices = [names.index(item) for item in destinations]
    if mode == "single_ended":
        source = source_indices[0]
        destination = destination_indices[0]
        return_loss = scored_network.s[:, source, source]
        insertion_loss = scored_network.s[:, destination, source]
        return_trace = f"S({sources[0]},{sources[0]})"
        insertion_trace = f"S({destinations[0]},{sources[0]})"
    else:
        return_loss = _differential_trace(
            scored_network.s,
            output_pair=source_indices,
            input_pair=source_indices,
        )
        insertion_loss = _differential_trace(
            scored_network.s,
            output_pair=destination_indices,
            input_pair=source_indices,
        )
        return_trace = f"SDD({sources[0]}-{sources[1]},{sources[0]}-{sources[1]})"
        insertion_trace = (
            f"SDD({destinations[0]}-{destinations[1]},{sources[0]}-{sources[1]})"
        )

    window_frequencies = frequencies_ghz[window]
    rl_db = _magnitude_db(np.abs(return_loss[window]), np)
    il_db = _magnitude_db(np.abs(insertion_loss[window]), np)
    worst_rl_index = int(np.argmax(rl_db))
    worst_il_index = int(np.argmin(il_db))
    rl_violations = np.maximum(rl_db - rl_target, 0.0)
    il_violations = np.maximum(il_target - il_db, 0.0)
    passed = bool(np.all(rl_violations <= 0.0) and np.all(il_violations <= 0.0))
    samples = [
        {
            "frequency_ghz": float(frequency),
            "s11_db": float(rl_value),
            "s21_db": float(il_value),
        }
        for frequency, rl_value, il_value in zip(
            window_frequencies,
            rl_db,
            il_db,
            strict=False,
        )
    ]
    return {
        "status": "pass" if passed else "fail",
        "touchstone_kind": f"s{network.nports}p",
        "sparameter_mode": mode,
        "port_order": names,
        "port_order_source": (
            "touchstone_port_names_and_aedt_snapshot"
            if file_port_names
            else "aedt_export_snapshot"
        ),
        "touchstone_port_names": file_port_names,
        "source_ports": sources,
        "destination_ports": destinations,
        "return_loss_trace": return_trace,
        "insertion_loss_trace": insertion_trace,
        "frequency_start_ghz": start,
        "frequency_stop_ghz": stop,
        "available_frequency_start_ghz": float(frequencies_ghz[0]),
        "available_frequency_stop_ghz": float(frequencies_ghz[-1]),
        "rl_target_db": rl_target,
        "rl_worst_db": round(float(rl_db[worst_rl_index]), 3),
        "rl_worst_frequency_ghz": float(window_frequencies[worst_rl_index]),
        "rl_violation_sum_db": round(float(np.sum(rl_violations)), 6),
        "rl_violation_max_db": round(float(np.max(rl_violations)), 6),
        "rl_violation_point_count": int(np.count_nonzero(rl_violations > 0.0)),
        "insertion_loss_min_db": il_target,
        "insertion_worst_db_in_band": round(float(il_db[worst_il_index]), 3),
        "insertion_worst_frequency_ghz": float(window_frequencies[worst_il_index]),
        "insertion_violation_sum_db": round(float(np.sum(il_violations)), 6),
        "insertion_violation_max_db": round(float(np.max(il_violations)), 6),
        "insertion_violation_point_count": int(np.count_nonzero(il_violations > 0.0)),
        "reference_impedance_ohm": reference,
        "single_ended_reference_impedance_ohm": target_single_ended_reference,
        "samples": {"sparameter_count": len(samples)},
        "bounded_samples": samples,
        "sources": {"touchstone": str(path)},
        "tdr_evaluated": False,
        "diagnosis": _diagnosis(
            rl_worst=float(rl_db[worst_rl_index]),
            rl_target=rl_target,
            il_worst=float(il_db[worst_il_index]),
            il_target=il_target,
        ),
    }


def _unique_nonempty_names(values: list[str], *, field: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty list")
    names = [str(item).strip() for item in values]
    if any(not item for item in names):
        raise ValueError(f"{field} contains an empty name")
    if len(set(names)) != len(names):
        raise ValueError(f"{field} contains duplicate names")
    return names


def _mapped_ports(
    values: list[str],
    port_order: list[str],
    expected_count: int,
    *,
    field: str,
) -> list[str]:
    names = _unique_nonempty_names(values, field=field)
    if len(names) != expected_count:
        raise ValueError(f"{field} must contain exactly {expected_count} port name(s)")
    missing = [item for item in names if item not in port_order]
    if missing:
        raise ValueError(f"{field} contains ports absent from port_order: {missing}")
    return names


def _finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _network_port_names(network: Any) -> list[str]:
    values = getattr(network, "port_names", None)
    if not values or any(value is None or not str(value).strip() for value in values):
        return []
    return [str(value).strip() for value in values]


def _differential_trace(matrix: Any, *, output_pair: list[int], input_pair: list[int]):
    output_positive, output_negative = output_pair
    input_positive, input_negative = input_pair
    return 0.5 * (
        matrix[:, output_positive, input_positive]
        - matrix[:, output_positive, input_negative]
        - matrix[:, output_negative, input_positive]
        + matrix[:, output_negative, input_negative]
    )


def _magnitude_db(values: Any, np: Any):
    floor = np.finfo(float).tiny
    return 20.0 * np.log10(np.maximum(values, floor))


def _diagnosis(
    *,
    rl_worst: float,
    rl_target: float,
    il_worst: float,
    il_target: float,
) -> list[str]:
    messages = []
    if rl_worst > rl_target:
        messages.append(
            f"return loss violates the target by {rl_worst - rl_target:.3f} dB at the worst point"
        )
    if il_worst < il_target:
        messages.append(
            f"insertion loss violates the minimum by {il_target - il_worst:.3f} dB at the worst point"
        )
    return messages or ["return loss and insertion loss satisfy the requested limits"]
