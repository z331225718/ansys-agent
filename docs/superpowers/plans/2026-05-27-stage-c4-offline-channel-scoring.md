# Stage C.4 Offline Channel Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the offline scoring foundation for BRD via optimization by parsing existing Touchstone/TDR files, computing RL and TDR metrics, and generating Chinese scoring reports without launching AEDT or modifying models.

**Architecture:** Add a focused `aedt_agent.layout.channel_scoring` module for parsing/scoring and a separate `aedt_agent.reporting.channel_scoring_report` module for Chinese HTML rendering. Add CLI scripts that score a pair of result files and optionally compare before/after runs. Keep this independent from AEDT execution so Stage C.5 can reuse the same metrics after real simulations.

**Tech Stack:** Python 3.12 standard library, pytest, pathlib, csv, math, existing docs/reporting style.

---

## Scope

This plan implements Stage C.4 only:

- Parse Touchstone `.s1p`/`.s2p`/`.sNp` style files enough for Sdd11/RL scoring.
- Parse TDR CSV with time and impedance columns.
- Score 0-26.56GHz by default.
- Report worst RL, pass/fail against -20dB, pass-band segments, TDR peak deviation, and TDR anomaly window.
- Generate JSON and Chinese HTML report.
- Compare before/after metrics offline.

This plan does not:

- Launch AEDT.
- Modify BRD/MCM, EDB, AEDT projects, holes, backdrill, anti-pad, void, or ports.
- Ask LLM to choose actions.
- Implement the layer-void adjustment node.

## File Structure

- Create `src/aedt_agent/layout/channel_scoring.py`  
  Pure parsing and scoring functions for Touchstone/TDR.

- Create `src/aedt_agent/reporting/channel_scoring_report.py`  
  Chinese HTML renderer for one scoring result.

- Create `scripts/score_stage_c_channel.py`  
  CLI for one Touchstone/TDR pair.

- Create `scripts/compare_stage_c_channel.py`  
  CLI for before/after result comparison.

- Create `tests/test_channel_scoring.py`  
  Unit and CLI tests for parsing, scoring, reporting, and comparison.

- Modify `docs/superpowers/specs/2026-05-27-brd-via-optimization-agent-design.md`  
  Add a short link to the Stage C.4 scripts once implemented.

## Data Model

`score_channel_result()` should return a JSON-serializable dictionary:

```python
{
    "status": "pass" | "fail",
    "frequency_start_ghz": 0.0,
    "frequency_stop_ghz": 26.56,
    "rl_target_db": -20.0,
    "rl_worst_db": -14.2,
    "rl_worst_frequency_ghz": 18.0,
    "rl_pass_band": [{"start_ghz": 0.0, "stop_ghz": 12.0}],
    "tdr_target_ohm": 100.0,
    "tdr_peak_deviation_ohm": 8.5,
    "tdr_peak_time_ps": 35.0,
    "tdr_anomaly_window": {"start_ps": 25.0, "stop_ps": 45.0},
    "samples": {
        "sparameter_count": 4,
        "tdr_count": 5
    },
    "sources": {
        "touchstone": "D:/runs/case.s2p",
        "tdr": "D:/runs/case_tdr.csv"
    },
    "diagnosis": [
        "0-26.56GHz 内 RL 未达到 -20dB 目标。",
        "TDR 最大偏差 8.5ohm，建议检查对应过孔 transition 附近挖空。"
    ]
}
```

`compare_channel_scores(before, after)` should return:

```python
{
    "status": "improved" | "regressed" | "mixed" | "unchanged",
    "rl_worst_delta_db": -3.2,
    "tdr_peak_deviation_delta_ohm": -1.5,
    "summary": "RL 改善，TDR 也更平滑。",
    "before": {"rl_worst_db": -14.2, "tdr_peak_deviation_ohm": 8.5},
    "after": {"rl_worst_db": -17.4, "tdr_peak_deviation_ohm": 7.0}
}
```

For deltas:

- `rl_worst_delta_db = after["rl_worst_db"] - before["rl_worst_db"]`  
  More negative is better, so negative delta is improvement.
- `tdr_peak_deviation_delta_ohm = after - before`  
  Smaller is better, so negative delta is improvement.

## Task 1: Touchstone Parser

**Files:**
- Create: `src/aedt_agent/layout/channel_scoring.py`
- Create: `tests/test_channel_scoring.py`

- [ ] **Step 1: Write failing parser tests**

Add:

```python
from pathlib import Path

from aedt_agent.layout.channel_scoring import parse_touchstone


def test_parse_touchstone_reads_s2p_magnitude_angle_samples(tmp_path):
    path = tmp_path / "case.s2p"
    path.write_text(
        "! demo\n"
        "# GHz S MA R 50\n"
        "0.0 0.10 0 0.80 0 0.80 0 0.10 0\n"
        "13.28 0.05 0 0.70 0 0.70 0 0.05 0\n"
        "26.56 0.20 0 0.60 0 0.60 0 0.20 0\n",
        encoding="utf-8",
    )

    samples = parse_touchstone(path)

    assert [sample["frequency_ghz"] for sample in samples] == [0.0, 13.28, 26.56]
    assert round(samples[0]["s11_db"], 3) == -20.0
    assert round(samples[1]["s21_db"], 3) == -3.098
```

Add:

```python
def test_parse_touchstone_reads_ri_format(tmp_path):
    path = tmp_path / "case.s2p"
    path.write_text(
        "# GHz S RI R 50\n"
        "1.0 0.1 0.0 0.7 0.0 0.7 0.0 0.1 0.0\n",
        encoding="utf-8",
    )

    samples = parse_touchstone(path)

    assert round(samples[0]["s11_db"], 3) == -20.0
```

- [ ] **Step 2: Run parser tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py::test_parse_touchstone_reads_s2p_magnitude_angle_samples tests/test_channel_scoring.py::test_parse_touchstone_reads_ri_format -q
```

Expected: fail because `aedt_agent.layout.channel_scoring` does not exist.

- [ ] **Step 3: Implement `parse_touchstone()`**

Implement in `src/aedt_agent/layout/channel_scoring.py`:

```python
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def parse_touchstone(path: Path) -> list[dict[str, float]]:
    frequency_unit = "GHz"
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
```

- [ ] **Step 4: Verify parser tests pass**

Run the same parser test command. Expected: pass.

## Task 2: TDR Parser and Scoring

**Files:**
- Modify: `src/aedt_agent/layout/channel_scoring.py`
- Modify: `tests/test_channel_scoring.py`

- [ ] **Step 1: Write failing scoring tests**

Add:

```python
from aedt_agent.layout.channel_scoring import parse_tdr_csv, score_channel_result


def test_parse_tdr_csv_accepts_time_and_impedance_columns(tmp_path):
    path = tmp_path / "tdr.csv"
    path.write_text("time_ps,impedance_ohm\n0,100\n10,104\n20,92\n", encoding="utf-8")

    samples = parse_tdr_csv(path)

    assert samples == [
        {"time_ps": 0.0, "impedance_ohm": 100.0},
        {"time_ps": 10.0, "impedance_ohm": 104.0},
        {"time_ps": 20.0, "impedance_ohm": 92.0},
    ]


def test_score_channel_result_reports_worst_rl_and_tdr_peak(tmp_path):
    touchstone = tmp_path / "case.s2p"
    touchstone.write_text(
        "# GHz S MA R 50\n"
        "0.0 0.05 0 0.80 0 0.80 0 0.05 0\n"
        "13.28 0.10 0 0.70 0 0.70 0 0.10 0\n"
        "26.56 0.25 0 0.60 0 0.60 0 0.25 0\n",
        encoding="utf-8",
    )
    tdr = tmp_path / "tdr.csv"
    tdr.write_text("time_ps,impedance_ohm\n0,100\n10,104\n20,92\n30,101\n", encoding="utf-8")

    score = score_channel_result(touchstone, tdr, frequency_stop_ghz=26.56, rl_target_db=-20, tdr_target_ohm=100)

    assert score["status"] == "fail"
    assert round(score["rl_worst_db"], 3) == -12.041
    assert score["rl_worst_frequency_ghz"] == 26.56
    assert score["tdr_peak_deviation_ohm"] == 8.0
    assert score["tdr_peak_time_ps"] == 20.0
    assert score["tdr_anomaly_window"] == {"start_ps": 10.0, "stop_ps": 30.0}
```

- [ ] **Step 2: Run scoring tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py::test_parse_tdr_csv_accepts_time_and_impedance_columns tests/test_channel_scoring.py::test_score_channel_result_reports_worst_rl_and_tdr_peak -q
```

Expected: fail because functions are not implemented.

- [ ] **Step 3: Implement `parse_tdr_csv()` and `score_channel_result()`**

Add:

```python
import csv


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
```

Also implement helpers:

```python
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
```

- [ ] **Step 4: Verify scoring tests pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py -q
```

Expected: pass.

## Task 3: Before/After Comparison

**Files:**
- Modify: `src/aedt_agent/layout/channel_scoring.py`
- Modify: `tests/test_channel_scoring.py`

- [ ] **Step 1: Write failing comparison tests**

Add:

```python
from aedt_agent.layout.channel_scoring import compare_channel_scores


def test_compare_channel_scores_classifies_improvement():
    before = {"rl_worst_db": -14.0, "tdr_peak_deviation_ohm": 9.0}
    after = {"rl_worst_db": -21.0, "tdr_peak_deviation_ohm": 4.0}

    comparison = compare_channel_scores(before, after)

    assert comparison["status"] == "improved"
    assert comparison["rl_worst_delta_db"] == -7.0
    assert comparison["tdr_peak_deviation_delta_ohm"] == -5.0
    assert "改善" in comparison["summary"]


def test_compare_channel_scores_classifies_mixed_result():
    before = {"rl_worst_db": -14.0, "tdr_peak_deviation_ohm": 4.0}
    after = {"rl_worst_db": -21.0, "tdr_peak_deviation_ohm": 9.0}

    comparison = compare_channel_scores(before, after)

    assert comparison["status"] == "mixed"
```

- [ ] **Step 2: Run comparison tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py::test_compare_channel_scores_classifies_improvement tests/test_channel_scoring.py::test_compare_channel_scores_classifies_mixed_result -q
```

Expected: fail because `compare_channel_scores` does not exist.

- [ ] **Step 3: Implement comparison**

Add:

```python
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
```

- [ ] **Step 4: Verify comparison tests pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py -q
```

Expected: pass.

## Task 4: Chinese HTML Report

**Files:**
- Create: `src/aedt_agent/reporting/channel_scoring_report.py`
- Modify: `tests/test_channel_scoring.py`

- [ ] **Step 1: Write failing report tests**

Add:

```python
from aedt_agent.reporting.channel_scoring_report import render_channel_score_html


def test_render_channel_score_html_contains_chinese_sections():
    score = {
        "status": "fail",
        "frequency_stop_ghz": 26.56,
        "rl_target_db": -20,
        "rl_worst_db": -12.041,
        "rl_worst_frequency_ghz": 26.56,
        "tdr_target_ohm": 100,
        "tdr_peak_deviation_ohm": 8.0,
        "tdr_peak_time_ps": 20.0,
        "tdr_anomaly_window": {"start_ps": 10.0, "stop_ps": 30.0},
        "diagnosis": ["0-26.56GHz 内 RL 未达到 -20dB 目标。"],
        "sources": {"touchstone": "case.s2p", "tdr": "tdr.csv"},
        "samples": {"sparameter_count": 3, "tdr_count": 4},
    }

    html = render_channel_score_html(score)

    assert "Stage C.4 通道离线评分报告" in html
    assert "回波损耗" in html
    assert "TDR" in html
    assert "-12.041" in html
    assert "case.s2p" in html
```

- [ ] **Step 2: Run report test to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py::test_render_channel_score_html_contains_chinese_sections -q
```

Expected: fail because renderer does not exist.

- [ ] **Step 3: Implement renderer**

Create `src/aedt_agent/reporting/channel_scoring_report.py`:

```python
from __future__ import annotations

import html
from typing import Any, Mapping


def render_channel_score_html(score: Mapping[str, Any]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Stage C.4 通道离线评分报告</title>
  <style>
    body{{font-family:Arial,"Microsoft YaHei",sans-serif;background:#f6f7f9;color:#1f2933;margin:0}}
    main{{max-width:1100px;margin:0 auto;padding:28px}}
    h1{{font-size:28px;margin:0 0 8px}} h2{{font-size:18px;margin:24px 0 10px}}
    .grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
    .card{{background:white;border:1px solid #d8dee8;border-radius:8px;padding:14px}}
    .card b{{display:block;color:#5f6b7a;font-size:12px;margin-bottom:5px}}
    table{{width:100%;border-collapse:collapse;background:white;border:1px solid #d8dee8}}
    th,td{{border-bottom:1px solid #e5e9f0;padding:9px 10px;text-align:left;font-size:13px}}
    th{{background:#eef2f6}}
  </style>
</head>
<body><main>
  <h1>Stage C.4 通道离线评分报告</h1>
  <div class="grid">
    <div class="card"><b>状态</b>{_e(score.get("status"))}</div>
    <div class="card"><b>频段</b>0-{_e(score.get("frequency_stop_ghz"))}GHz</div>
    <div class="card"><b>RL 目标</b>{_e(score.get("rl_target_db"))}dB</div>
    <div class="card"><b>TDR 目标</b>{_e(score.get("tdr_target_ohm"))}ohm</div>
  </div>
  <h2>回波损耗</h2>
  <table><tr><th>Worst RL</th><th>Frequency</th></tr>
  <tr><td>{_e(score.get("rl_worst_db"))}dB</td><td>{_e(score.get("rl_worst_frequency_ghz"))}GHz</td></tr></table>
  <h2>TDR</h2>
  <table><tr><th>最大偏差</th><th>时间</th><th>异常窗口</th></tr>
  <tr><td>{_e(score.get("tdr_peak_deviation_ohm"))}ohm</td><td>{_e(score.get("tdr_peak_time_ps"))}ps</td><td>{_window(score.get("tdr_anomaly_window"))}</td></tr></table>
  <h2>诊断</h2>
  <ul>{''.join(f"<li>{_e(item)}</li>" for item in score.get("diagnosis", []))}</ul>
  <h2>数据源</h2>
  <table><tr><th>Touchstone</th><td>{_e((score.get("sources") or {}).get("touchstone"))}</td></tr>
  <tr><th>TDR</th><td>{_e((score.get("sources") or {}).get("tdr"))}</td></tr></table>
</main></body></html>
"""


def _window(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    return f"{_e(value.get('start_ps'))}-{_e(value.get('stop_ps'))}ps"


def _e(value: Any) -> str:
    return html.escape(str(value or ""))
```

- [ ] **Step 4: Verify report test passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py -q
```

Expected: pass.

## Task 5: CLI Scripts

**Files:**
- Create: `scripts/score_stage_c_channel.py`
- Create: `scripts/compare_stage_c_channel.py`
- Modify: `tests/test_channel_scoring.py`

- [ ] **Step 1: Write failing CLI tests**

Add:

```python
import json
import subprocess
import sys


def test_score_stage_c_channel_cli_writes_json_and_html(tmp_path):
    touchstone = tmp_path / "case.s2p"
    touchstone.write_text("# GHz S MA R 50\n0 0.05 0 0.8 0 0.8 0 0.05 0\n26.56 0.25 0 0.6 0 0.6 0 0.25 0\n", encoding="utf-8")
    tdr = tmp_path / "tdr.csv"
    tdr.write_text("time_ps,impedance_ohm\n0,100\n10,108\n", encoding="utf-8")
    output_json = tmp_path / "score.json"
    output_html = tmp_path / "score.html"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/score_stage_c_channel.py",
            "--touchstone",
            str(touchstone),
            "--tdr",
            str(tdr),
            "--output-json",
            str(output_json),
            "--output-html",
            str(output_html),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert output_json.exists()
    assert output_html.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["status"] == "fail"


def test_compare_stage_c_channel_cli_writes_comparison(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    output = tmp_path / "comparison.json"
    before.write_text(json.dumps({"rl_worst_db": -14, "tdr_peak_deviation_ohm": 9}), encoding="utf-8")
    after.write_text(json.dumps({"rl_worst_db": -21, "tdr_peak_deviation_ohm": 4}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/compare_stage_c_channel.py", "--before", str(before), "--after", str(after), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "improved"
```

- [ ] **Step 2: Run CLI tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py::test_score_stage_c_channel_cli_writes_json_and_html tests/test_channel_scoring.py::test_compare_stage_c_channel_cli_writes_comparison -q
```

Expected: fail because scripts do not exist.

- [ ] **Step 3: Implement `scripts/score_stage_c_channel.py`**

Create script:

```python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.layout.channel_scoring import score_channel_result
from aedt_agent.reporting.channel_scoring_report import render_channel_score_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Stage C.4 channel Touchstone/TDR results offline.")
    parser.add_argument("--touchstone", required=True)
    parser.add_argument("--tdr", required=True)
    parser.add_argument("--frequency-stop-ghz", type=float, default=26.56)
    parser.add_argument("--rl-target-db", type=float, default=-20.0)
    parser.add_argument("--tdr-target-ohm", type=float, default=100.0)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-html", required=True)
    args = parser.parse_args()
    score = score_channel_result(
        Path(args.touchstone),
        Path(args.tdr),
        frequency_stop_ghz=args.frequency_stop_ghz,
        rl_target_db=args.rl_target_db,
        tdr_target_ohm=args.tdr_target_ohm,
    )
    Path(args.output_json).write_text(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.output_html).write_text(render_channel_score_html(score), encoding="utf-8")
    print(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if score["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement `scripts/compare_stage_c_channel.py`**

Create script:

```python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.layout.channel_scoring import compare_channel_scores


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Stage C.4 before/after channel scores.")
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    comparison = compare_channel_scores(before, after)
    Path(args.output).write_text(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if comparison["status"] in {"improved", "unchanged"} else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Verify CLI tests pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py -q
```

Expected: pass.

## Task 6: Docs, Verification, Commit

**Files:**
- Modify: `docs/superpowers/specs/2026-05-27-brd-via-optimization-agent-design.md`

- [ ] **Step 1: Update spec with Stage C.4 commands**

Append under `Stage C.4: Optimization Spec and Offline Scoring`:

````markdown
Stage C.4 command line entry points:

```bash
.venv/bin/python scripts/score_stage_c_channel.py \
  --touchstone D:/runs/case.s2p \
  --tdr D:/runs/case_tdr.csv \
  --output-json D:/runs/channel_score.json \
  --output-html D:/runs/channel_score.html

.venv/bin/python scripts/compare_stage_c_channel.py \
  --before D:/runs/before_score.json \
  --after D:/runs/after_score.json \
  --output D:/runs/channel_compare.json
```
````

- [ ] **Step 2: Run focused tests**

```bash
.venv/bin/python -m pytest tests/test_channel_scoring.py -q
```

- [ ] **Step 3: Run full suite**

```bash
.venv/bin/python -m pytest -q
```

- [ ] **Step 4: Run contract check**

```bash
.venv/bin/python scripts/check_contract_stabilization.py
```

- [ ] **Step 5: Run diff check**

```bash
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add \
  src/aedt_agent/layout/channel_scoring.py \
  src/aedt_agent/reporting/channel_scoring_report.py \
  scripts/score_stage_c_channel.py \
  scripts/compare_stage_c_channel.py \
  tests/test_channel_scoring.py \
  docs/superpowers/specs/2026-05-27-brd-via-optimization-agent-design.md

git commit -m "feat: add Stage C channel offline scoring"
```

## Done Criteria

- Existing Touchstone/TDR files can be scored without AEDT.
- Default target is 0-26.56GHz and RL <= -20dB.
- TDR peak deviation and anomaly window are reported.
- Before/after comparison classifies improved, mixed, regressed, or unchanged.
- Chinese HTML report is generated for engineering review.
- No BRD/AEDT model mutation is introduced in Stage C.4.
