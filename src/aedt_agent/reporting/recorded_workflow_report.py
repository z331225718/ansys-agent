from __future__ import annotations

import html
from typing import Any, Mapping


def render_recorded_workflow_html(analysis: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{_e(key)}</td><td>{_e(value.get('preferred'))}</td><td>{_e(value.get('fallback'))}</td></tr>"
        for key, value in sorted((analysis.get("pyaedt_migration") or {}).items())
        if isinstance(value, Mapping)
    )
    steps = "".join(f"<li>{_e(step)}</li>" for step in analysis.get("steps", []))
    variables = "".join(
        f"<tr><td>{_e(item.get('name'))}</td><td>{_e(item.get('value'))}</td></tr>"
        for item in analysis.get("optimization_variables", [])
        if isinstance(item, Mapping)
    )
    voids = "".join(
        f"<tr><td>{_e(item.get('layer'))}</td><td>{_e(item.get('kind'))}</td></tr>"
        for item in analysis.get("voids", [])
        if isinstance(item, Mapping)
    )
    paths = analysis.get("paths") or {}
    nets = analysis.get("nets") or {}
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Stage C.5 录制工作流分析</title>
  <style>
    body{{font-family:Arial,"Microsoft YaHei",sans-serif;background:#f6f7f9;color:#1f2933;margin:0}}
    main{{max-width:1180px;margin:0 auto;padding:28px}}
    h1{{font-size:28px;margin:0 0 8px}} h2{{font-size:18px;margin:24px 0 10px}}
    table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d8dee8}}
    th,td{{text-align:left;border-bottom:1px solid #e5e9f0;padding:9px 10px;font-size:13px;vertical-align:top}}
    th{{background:#eef2f6}} code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}
  </style>
</head>
<body><main>
  <h1>Stage C.5 录制工作流分析</h1>
  <p>录制脚本只作为事实来源和 fallback；产品化实现优先使用 PyAEDT/PyEDB 包装 API。</p>
  <h2>输入与输出</h2>
  <table>
    <tr><th>BRD</th><td>{_e(paths.get("brd"))}</td></tr>
    <tr><th>AEDB</th><td>{_e(paths.get("aedb"))}</td></tr>
    <tr><th>AEDT Project</th><td>{_e(paths.get("aedt_project"))}</td></tr>
    <tr><th>Signal Nets</th><td>{_e(", ".join(nets.get("signal", [])))}</td></tr>
    <tr><th>Reference Nets</th><td>{_e(", ".join(nets.get("reference", [])))}</td></tr>
  </table>
  <h2>识别出的工作流步骤</h2>
  <ol>{steps}</ol>
  <h2>优化变量</h2>
  <table><tr><th>变量</th><th>值</th></tr>{variables}</table>
  <h2>反焊盘/挖空操作</h2>
  <table><tr><th>Layer</th><th>Kind</th></tr>{voids}</table>
  <h2>PyAEDT 优先迁移表</h2>
  <table><tr><th>Recorded operation</th><th>Preferred wrapper</th><th>Fallback</th></tr>{rows}</table>
</main></body></html>
"""


def _e(value: Any) -> str:
    return html.escape(str(value or ""))
