from __future__ import annotations

import html
from typing import Any, Mapping


def render_brd_acceptance_html(summary: Mapping[str, Any]) -> str:
    status = str(summary.get("status", "unknown"))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Stage C BRD/MCM 生产验收报告</title>
  <style>
    body{{font-family:Arial,"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#1f2933}}
    main{{max-width:1180px;margin:0 auto;padding:28px}}
    h1{{font-size:28px;margin:0 0 8px}} h2{{font-size:18px;margin:24px 0 10px}}
    .sub{{color:#5f6b7a;margin-bottom:18px}} .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-weight:700}}
    .succeeded{{background:#d9f2e5;color:#14633b}} .failed{{background:#fee2e2;color:#991b1b}}
    .grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
    .card{{background:white;border:1px solid #d8dee8;border-radius:8px;padding:14px;min-width:0}}
    .card b{{display:block;font-size:12px;color:#5f6b7a;margin-bottom:5px}} .card span{{word-break:break-all}}
    table{{width:100%;border-collapse:collapse;background:white;border:1px solid #d8dee8;border-radius:8px;overflow:hidden}}
    th,td{{text-align:left;border-bottom:1px solid #e5e9f0;padding:9px 10px;font-size:13px;vertical-align:top}}
    th{{background:#eef2f6;color:#374151}} tr:last-child td{{border-bottom:0}}
    code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}}
    .issue{{background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;margin:6px 0}}
  </style>
</head>
<body>
<main>
  <h1>Stage C BRD/MCM 生产验收报告</h1>
  <div class="sub">用于确认 BRD/MCM cutout 建模运行是否具备生产复盘价值；默认 model-build-only，不代表大板全仿真已完成。</div>
  <div class="grid">
    <div class="card"><b>运行状态</b><span class="badge {html.escape(status)}">{html.escape(status)}</span></div>
    <div class="card"><b>Layout</b><span>{_e(summary.get("layout_file"))}</span></div>
    <div class="card"><b>Signal Nets</b><span>{_join(summary.get("signal_nets"))}</span></div>
    <div class="card"><b>Reference Nets</b><span>{_join(summary.get("reference_nets"))}</span></div>
  </div>
  {_blocking_section(summary)}
  <h2>环境预检</h2>
  {_checks_table(summary.get("preflight_checks", []))}
  <h2>节点执行状态</h2>
  {_mapping_table(summary.get("step_statuses", {}), "Step", "Status")}
  <h2>端口策略</h2>
  {_actions_table(summary.get("port_actions", []))}
  <h2>输出文件</h2>
  <div class="grid">
    <div class="card"><b>AEDT Project</b><span>{_e(summary.get("aedt_project"))}</span></div>
    <div class="card"><b>EDB Path</b><span>{_e(summary.get("edb_path"))}</span></div>
    <div class="card"><b>Touchstone</b><span>{_e((summary.get("optional_results") or {}).get("touchstone"))}</span></div>
    <div class="card"><b>TDR</b><span>{_e((summary.get("optional_results") or {}).get("tdr"))}</span></div>
  </div>
  {_mapping_table(summary.get("artifacts", {}), "Artifact", "Path")}
</main>
</body>
</html>
"""


def _blocking_section(summary: Mapping[str, Any]) -> str:
    issues = summary.get("blocking_issues") or []
    warnings = summary.get("warnings") or []
    if not issues and not warnings:
        return ""
    issue_html = "".join(f"<div class=\"issue\">{_e(issue)}</div>" for issue in issues)
    warning_html = "".join(f"<div class=\"issue\">{_e(warning)}</div>" for warning in warnings)
    return f"<h2>阻塞问题</h2>{issue_html}<h2>风险提示</h2>{warning_html}"


def _checks_table(checks: Any) -> str:
    rows = []
    for check in checks if isinstance(checks, list) else []:
        if isinstance(check, Mapping):
            rows.append(f"<tr><td>{_e(check.get('id'))}</td><td>{_e(check.get('status'))}</td><td>{_e(check.get('message'))}</td></tr>")
    return "<table><tr><th>Check</th><th>Status</th><th>Message</th></tr>" + "".join(rows) + "</table>"


def _actions_table(actions: Any) -> str:
    rows = []
    for index, action in enumerate(actions if isinstance(actions, list) else [], start=1):
        if isinstance(action, Mapping):
            endpoint = action.get("endpoint") or action.get("component") or action.get("pin") or ""
            rows.append(f"<tr><td>{index}</td><td>{_e(endpoint)}</td><td>{_e(action.get('strategy'))}</td></tr>")
    return "<table><tr><th>#</th><th>Endpoint</th><th>Strategy</th></tr>" + "".join(rows) + "</table>"


def _mapping_table(mapping: Any, left: str, right: str) -> str:
    rows = []
    for key, value in (mapping.items() if isinstance(mapping, Mapping) else []):
        rows.append(f"<tr><td><code>{_e(key)}</code></td><td>{_e(value)}</td></tr>")
    return f"<table><tr><th>{left}</th><th>{right}</th></tr>{''.join(rows)}</table>"


def _join(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_e(item) for item in value)
    return _e(value)


def _e(value: Any) -> str:
    return html.escape(str(value or ""))
