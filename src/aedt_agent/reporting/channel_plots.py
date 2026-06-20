from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from aedt_agent.layout.channel_scoring import parse_tdr_csv, parse_touchstone


def write_channel_plot_artifacts(
    *,
    touchstone_path: str | Path,
    tdr_path: str | Path,
    artifact_dir: str | Path,
    sparameter_mode: str = "auto",
) -> dict[str, str]:
    output_dir = Path(artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sparameter_samples = parse_touchstone(Path(touchstone_path))
    tdr_samples = parse_tdr_csv(Path(tdr_path))
    trace_config = _trace_config(sparameter_samples, sparameter_mode)

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
            for sample in tdr_samples
        ],
    )
    _write_svg(
        plots[trace_config["return_loss_trace"].casefold()],
        title=trace_config["return_loss_trace"],
        x_label="Frequency (GHz)",
        y_label="Magnitude (dB)",
        points=[
            (sample["frequency_ghz"], sample[trace_config["return_loss_key"]])
            for sample in sparameter_samples
        ],
    )
    _write_svg(
        plots[trace_config["insertion_loss_trace"].casefold()],
        title=trace_config["insertion_loss_trace"],
        x_label="Frequency (GHz)",
        y_label="Magnitude (dB)",
        points=[
            (
                sample["frequency_ghz"],
                sample[trace_config["insertion_loss_key"]],
            )
            for sample in sparameter_samples
        ],
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
    x_min, x_max = _expanded_range(min(xs), max(xs))
    y_min, y_max = _expanded_range(min(ys), max(ys))

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

    path.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>
  text {{ font-family: Arial, 'Microsoft YaHei', sans-serif; font-size: 12px; fill: #1f2933; }}
  .axis {{ stroke: #263445; stroke-width: 1.2; }}
  .grid {{ stroke: #d8dee8; stroke-width: 1; }}
  .trace {{ fill: none; stroke: #0067b1; stroke-width: 2; }}
  .title {{ font-size: 18px; font-weight: 700; }}
</style>
<rect width="100%" height="100%" fill="#fff" />
<text class="title" x="{margin_left}" y="22">{html.escape(title)}</text>
<line class="axis" x1="{margin_left}" y1="{margin_top + plot_height}" x2="{margin_left + plot_width}" y2="{margin_top + plot_height}" />
<line class="axis" x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" />
{''.join(tick_markup)}
<polyline class="trace" points="{polyline}" />
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
