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
