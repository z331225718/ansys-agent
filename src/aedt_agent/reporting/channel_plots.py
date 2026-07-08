from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any

from aedt_agent.layout.channel_scoring import parse_tdr_csv, parse_touchstone


def write_channel_plot_artifacts(
    *,
    touchstone_path: str | Path,
    tdr_path: str | Path,
    artifact_dir: str | Path,
    sparameter_mode: str = "auto",
    frequency_start_ghz: float | None = None,
    frequency_stop_ghz: float | None = None,
    rl_target_db: float | None = None,
    tdr_target_ohm: float | None = None,
    tdr_tolerance_ohm: float | None = None,
    tdr_plot_time_stop_ps: float | None = 120.0,
    reference_impedance_ohm: float | None = None,
) -> dict[str, str]:
    output_dir = Path(artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sparameter_samples = parse_touchstone(
        Path(touchstone_path),
        reference_impedance_ohm=reference_impedance_ohm,
    )
    tdr_samples = parse_tdr_csv(Path(tdr_path))
    trace_config = _trace_config(sparameter_samples, sparameter_mode)
    sparameter_points = _filter_sparameter_samples(
        sparameter_samples,
        frequency_start_ghz=frequency_start_ghz,
        frequency_stop_ghz=frequency_stop_ghz,
    )
    tdr_points = _filter_tdr_samples(
        tdr_samples,
        tdr_plot_time_stop_ps=tdr_plot_time_stop_ps,
    )

    plots = {
        "tdr": output_dir / "tdr.svg",
        trace_config["return_loss_trace"].casefold(): output_dir
        / f"{trace_config['return_loss_trace'].casefold()}.svg",
        trace_config["insertion_loss_trace"].casefold(): output_dir
        / f"{trace_config['insertion_loss_trace'].casefold()}.svg",
    }
    _write_svg(
        plots["tdr"],
        title="TDR",
        x_label="Time (ps)",
        y_label="Impedance (ohm)",
        points=[
            (sample["time_ps"], sample["impedance_ohm"])
            for sample in tdr_points
        ],
        x_range=_tdr_x_range(tdr_points, tdr_plot_time_stop_ps),
        y_range=_tdr_y_range(
            [sample["impedance_ohm"] for sample in tdr_points],
            target_ohm=tdr_target_ohm,
            tolerance_ohm=tdr_tolerance_ohm,
        ),
        horizontal_markers=_tdr_markers(tdr_target_ohm, tdr_tolerance_ohm),
    )
    _write_svg(
        plots[trace_config["return_loss_trace"].casefold()],
        title=f"{trace_config['return_loss_trace']} Return Loss",
        x_label="Frequency (GHz)",
        y_label="Magnitude (dB)",
        points=[
            (sample["frequency_ghz"], sample[trace_config["return_loss_key"]])
            for sample in sparameter_points
        ],
        x_range=_frequency_x_range(
            sparameter_points,
            frequency_start_ghz=frequency_start_ghz,
            frequency_stop_ghz=frequency_stop_ghz,
        ),
        y_range=_return_loss_y_range(
            [sample[trace_config["return_loss_key"]] for sample in sparameter_points]
        ),
        horizontal_markers=_rl_markers(rl_target_db),
    )
    _write_svg(
        plots[trace_config["insertion_loss_trace"].casefold()],
        title=f"{trace_config['insertion_loss_trace']} Insertion Loss",
        x_label="Frequency (GHz)",
        y_label="Magnitude (dB)",
        points=[
            (
                sample["frequency_ghz"],
                sample[trace_config["insertion_loss_key"]],
            )
            for sample in sparameter_points
        ],
        x_range=_frequency_x_range(
            sparameter_points,
            frequency_start_ghz=frequency_start_ghz,
            frequency_stop_ghz=frequency_stop_ghz,
        ),
        y_range=_insertion_loss_y_range(
            [sample[trace_config["insertion_loss_key"]] for sample in sparameter_points]
        ),
    )
    return {name: str(path) for name, path in plots.items()}


def _trace_config(
    samples: list[dict[str, float]],
    sparameter_mode: str,
) -> dict[str, str]:
    if not samples:
        raise ValueError("cannot plot empty Touchstone artifact")
    mode = sparameter_mode.strip().casefold()
    if mode == "auto":
        mode = "differential" if "sdd11_db" in samples[0] else "single_ended"
    if mode in {"diff", "mixed_mode"}:
        mode = "differential"
    if mode in {"single", "single-ended"}:
        mode = "single_ended"
    if mode == "differential":
        return {
            "return_loss_key": "sdd11_db",
            "insertion_loss_key": "sdd21_db",
            "return_loss_trace": "SDD11",
            "insertion_loss_trace": "SDD21",
        }
    if mode != "single_ended":
        raise ValueError("sparameter_mode must be auto, differential, or single_ended")
    return {
        "return_loss_key": "s11_db",
        "insertion_loss_key": "s21_db",
        "return_loss_trace": "S11",
        "insertion_loss_trace": "S21",
    }


def _write_svg(
    path: Path,
    *,
    title: str,
    x_label: str,
    y_label: str,
    points: list[tuple[float, float]],
    x_range: tuple[float, float] | None = None,
    y_range: tuple[float, float] | None = None,
    horizontal_markers: list[dict[str, Any]] | None = None,
) -> None:
    if not points:
        raise ValueError(f"cannot plot empty trace: {title}")
    width = 900
    height = 360
    margin_left = 70
    margin_right = 22
    margin_top = 34
    margin_bottom = 54
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_min, x_max = x_range or _expanded_range(min(xs), max(xs))
    y_min, y_max = y_range or _expanded_range(min(ys), max(ys))
    if abs(x_max - x_min) < 1e-15:
        x_min, x_max = _expanded_range(x_min, x_max)
    if abs(y_max - y_min) < 1e-15:
        y_min, y_max = _expanded_range(y_min, y_max)

    def sx(value: float) -> float:
        return margin_left + (value - x_min) / (x_max - x_min) * plot_width

    def sy(value: float) -> float:
        return margin_top + (y_max - value) / (y_max - y_min) * plot_height

    polyline = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in points)
    x_ticks = _ticks(x_min, x_max, 5)
    y_ticks = _ticks(y_min, y_max, 5)
    tick_markup = []
    for value in x_ticks:
        x = sx(value)
        tick_markup.append(
            f'<line class="grid x-grid" x1="{x:.2f}" y1="{margin_top}" '
            f'x2="{x:.2f}" y2="{margin_top + plot_height}" />'
        )
        tick_markup.append(
            f'<line x1="{x:.2f}" y1="{margin_top + plot_height}" '
            f'x2="{x:.2f}" y2="{margin_top + plot_height + 5}" />'
        )
        tick_markup.append(
            f'<text x="{x:.2f}" y="{height - 28}" text-anchor="middle">'
            f"{value:.3g}</text>"
        )
    for value in y_ticks:
        y = sy(value)
        tick_markup.append(
            f'<line x1="{margin_left - 5}" y1="{y:.2f}" '
            f'x2="{margin_left}" y2="{y:.2f}" />'
        )
        tick_markup.append(
            f'<line class="grid" x1="{margin_left}" y1="{y:.2f}" '
            f'x2="{margin_left + plot_width}" y2="{y:.2f}" />'
        )
        tick_markup.append(
            f'<text x="{margin_left - 10}" y="{y + 4:.2f}" '
            f'text-anchor="end">{value:.3g}</text>'
        )
    marker_markup = []
    for marker in horizontal_markers or []:
        value = float(marker["value"])
        if not y_min <= value <= y_max:
            continue
        y = sy(value)
        label = str(marker.get("label") or "")
        css_class = str(marker.get("class") or "marker")
        marker_markup.append(
            f'<line class="{css_class}" x1="{margin_left}" y1="{y:.2f}" '
            f'x2="{margin_left + plot_width}" y2="{y:.2f}" />'
        )
        if label:
            marker_markup.append(
                f'<text class="{css_class}-label" x="{margin_left + plot_width - 4}" '
                f'y="{y - 5:.2f}" text-anchor="end">{html.escape(label)}</text>'
            )

    point_markup = []
    for x_value, y_value in points:
        point_markup.append(
            f'<circle class="hit-point" cx="{sx(x_value):.2f}" '
            f'cy="{sy(y_value):.2f}" r="5">'
            f"<title>{html.escape(_point_title(x_label, y_label, x_value, y_value))}</title>"
            "</circle>"
        )

    path.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>
  text {{ font-family: Arial, 'Microsoft YaHei', sans-serif; font-size: 12px; fill: #1f2933; }}
  .axis {{ stroke: #263445; stroke-width: 1.2; }}
  .grid {{ stroke: #d8dee8; stroke-width: 1; }}
  .x-grid {{ stroke: #edf1f6; }}
  .trace {{ fill: none; stroke: #0067b1; stroke-width: 2; }}
  .hit-point {{ fill: transparent; stroke: transparent; pointer-events: all; }}
  .hit-point:hover {{ fill: #0067b1; fill-opacity: .18; stroke: #0067b1; stroke-width: 1; }}
  .target {{ stroke: #d13438; stroke-width: 1.4; stroke-dasharray: 6 5; }}
  .limit {{ stroke: #f59e0b; stroke-width: 1.1; stroke-dasharray: 4 5; }}
  .target-label, .limit-label {{ font-size: 11px; fill: #52616f; }}
  .title {{ font-size: 18px; font-weight: 700; }}
</style>
<rect width="100%" height="100%" fill="#fff"
      data-x-min="{_fmt_attr(x_min)}" data-x-max="{_fmt_attr(x_max)}"
      data-y-min="{_fmt_attr(y_min)}" data-y-max="{_fmt_attr(y_max)}" />
<text class="title" x="{margin_left}" y="22">{html.escape(title)}</text>
<line class="axis" x1="{margin_left}" y1="{margin_top + plot_height}" x2="{margin_left + plot_width}" y2="{margin_top + plot_height}" />
<line class="axis" x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" />
{''.join(tick_markup)}
{''.join(marker_markup)}
<polyline class="trace" points="{polyline}" />
<g class="hover-points">{''.join(point_markup)}</g>
<text x="{margin_left + plot_width / 2:.2f}" y="{height - 6}" text-anchor="middle">{html.escape(x_label)}</text>
<text transform="translate(16 {margin_top + plot_height / 2:.2f}) rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>
</svg>
""",
        encoding="utf-8",
    )


def _expanded_range(low: float, high: float) -> tuple[float, float]:
    if abs(high - low) < 1e-15:
        padding = max(1.0, abs(low) * 0.05)
        return low - padding, high + padding
    padding = (high - low) * 0.05
    return low - padding, high + padding


def _ticks(low: float, high: float, count: int) -> list[float]:
    if count <= 1:
        return [low]
    return [low + (high - low) * index / (count - 1) for index in range(count)]


def _filter_sparameter_samples(
    samples: list[dict[str, float]],
    *,
    frequency_start_ghz: float | None,
    frequency_stop_ghz: float | None,
) -> list[dict[str, float]]:
    if frequency_start_ghz is None and frequency_stop_ghz is None:
        return samples
    filtered = [
        sample
        for sample in samples
        if (
            frequency_start_ghz is None
            or sample["frequency_ghz"] >= frequency_start_ghz
        )
        and (
            frequency_stop_ghz is None
            or sample["frequency_ghz"] <= frequency_stop_ghz
        )
    ]
    return filtered or samples


def _filter_tdr_samples(
    samples: list[dict[str, float]],
    *,
    tdr_plot_time_stop_ps: float | None,
) -> list[dict[str, float]]:
    if tdr_plot_time_stop_ps is None or tdr_plot_time_stop_ps <= 0:
        return samples
    filtered = [
        sample
        for sample in samples
        if sample["time_ps"] <= tdr_plot_time_stop_ps
    ]
    return filtered or samples


def _frequency_x_range(
    samples: list[dict[str, float]],
    *,
    frequency_start_ghz: float | None,
    frequency_stop_ghz: float | None,
) -> tuple[float, float]:
    if frequency_start_ghz is not None and frequency_stop_ghz is not None:
        return float(frequency_start_ghz), float(frequency_stop_ghz)
    xs = [sample["frequency_ghz"] for sample in samples]
    if frequency_start_ghz is not None:
        return float(frequency_start_ghz), max(xs)
    if frequency_stop_ghz is not None:
        return min(xs), float(frequency_stop_ghz)
    return _expanded_range(min(xs), max(xs))


def _tdr_x_range(
    samples: list[dict[str, float]],
    tdr_plot_time_stop_ps: float | None,
) -> tuple[float, float] | None:
    if tdr_plot_time_stop_ps is not None and tdr_plot_time_stop_ps > 0:
        return 0.0, float(tdr_plot_time_stop_ps)
    xs = [sample["time_ps"] for sample in samples]
    if min(xs) >= 0:
        return 0.0, max(xs)
    return None


def _return_loss_y_range(values: list[float]) -> tuple[float, float]:
    bottom = min(-40.0, math.floor(min(values) / 5.0) * 5.0)
    return bottom, 0.0


def _insertion_loss_y_range(values: list[float]) -> tuple[float, float]:
    bottom = min(-5.0, math.floor(min(values) / 5.0) * 5.0)
    return bottom, 0.0


def _tdr_y_range(
    values: list[float],
    *,
    target_ohm: float | None,
    tolerance_ohm: float | None,
) -> tuple[float, float]:
    if target_ohm is None:
        return _expanded_range(min(values), max(values))
    half_span = max(20.0, float(tolerance_ohm or 0.0) * 1.5)
    low = min(target_ohm - half_span, min(values))
    high = max(target_ohm + half_span, max(values))
    return _round_down(low, 5.0), _round_up(high, 5.0)


def _rl_markers(rl_target_db: float | None) -> list[dict[str, Any]]:
    if rl_target_db is None:
        return []
    return [
        {
            "value": float(rl_target_db),
            "label": f"target {rl_target_db:g} dB",
            "class": "target",
        }
    ]


def _tdr_markers(
    tdr_target_ohm: float | None,
    tdr_tolerance_ohm: float | None,
) -> list[dict[str, Any]]:
    if tdr_target_ohm is None:
        return []
    markers = [
        {
            "value": float(tdr_target_ohm),
            "label": f"target {tdr_target_ohm:g} ohm",
            "class": "target",
        }
    ]
    if tdr_tolerance_ohm is not None:
        markers.extend(
            [
                {
                    "value": float(tdr_target_ohm) + float(tdr_tolerance_ohm),
                    "label": f"+{tdr_tolerance_ohm:g} ohm",
                    "class": "limit",
                },
                {
                    "value": float(tdr_target_ohm) - float(tdr_tolerance_ohm),
                    "label": f"-{tdr_tolerance_ohm:g} ohm",
                    "class": "limit",
                },
            ]
        )
    return markers


def _round_down(value: float, step: float) -> float:
    return math.floor(value / step) * step


def _round_up(value: float, step: float) -> float:
    return math.ceil(value / step) * step


def _fmt_attr(value: float) -> str:
    return f"{value:g}"


def _point_title(
    x_label: str,
    y_label: str,
    x_value: float,
    y_value: float,
) -> str:
    return f"{x_label}: {x_value:.6g}; {y_label}: {y_value:.6g}"
