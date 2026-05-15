from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any


GROUP_ORDER = ("B", "C")


def write_html_report_stage_b(report: dict[str, Any], output_path: Path, model_name: str = "") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html_report_stage_b(report, model_name=model_name), encoding="utf-8")
    return output_path


def render_html_report_stage_b(report: dict[str, Any], model_name: str = "") -> str:
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    groups = report.get("groups", {})
    tasks = report.get("tasks", {})
    rows = "".join(_task_row(task_id, task_data) for task_id, task_data in sorted(tasks.items()))
    failure_items = "".join(_failure_item(task_id, task_data) for task_id, task_data in sorted(tasks.items()))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stage B 节点化 AEDT Benchmark 报告</title>
  <style>
    :root {{
      --bg: #f5f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5c6675;
      --line: #d9dee7;
      --pass: #136f46;
      --fail: #b13636;
      --warn: #8a5a00;
      --accent: #1f5f8f;
      --accent2: #2f766f;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .page {{ max-width: 1380px; margin: 0 auto; padding: 30px 24px 48px; }}
    .hero {{ background: var(--panel); border: 1px solid var(--line); border-left: 7px solid var(--accent); border-radius: 8px; padding: 26px; }}
    h1 {{ margin: 0 0 10px; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 15px; letter-spacing: 0; }}
    p, li {{ color: var(--muted); line-height: 1.55; }}
    p {{ margin: 0; }}
    ul, ol {{ margin: 0; padding-left: 20px; }}
    li + li {{ margin-top: 6px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 14px; margin: 22px 0; }}
    .card, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .card {{ padding: 16px; min-height: 104px; }}
    .panel {{ padding: 18px; margin-top: 18px; }}
    .label {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-size: 26px; font-weight: 700; }}
    .split {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
    .box {{ background: #fafbfc; border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .box strong {{ display: block; margin-bottom: 6px; color: var(--text); }}
    .callout {{ border-left: 5px solid var(--accent2); background: #f7fbfa; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ border-top: none; color: var(--muted); background: #fafbfc; }}
    .task-id, .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .pill {{ display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .pill.pass {{ color: var(--pass); background: rgba(19,111,70,0.12); }}
    .pill.fail {{ color: var(--fail); background: rgba(177,54,54,0.12); }}
    .pill.warn {{ color: var(--warn); background: rgba(138,90,0,0.12); }}
    .subtle {{ color: var(--muted); font-size: 12px; margin-top: 5px; line-height: 1.35; }}
    .footer {{ color: var(--muted); font-size: 12px; margin-top: 18px; }}
    @media print {{
      body {{ background: #fff; }}
      .page {{ max-width: none; padding: 18px; }}
      .panel, .hero, .card {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <h1>Stage B 节点化 AEDT Benchmark 报告</h1>
      <p>生成时间：{escape(generated_at)}。模型/执行 harness：{escape(model_name or "N/A")}。本报告对比 Stage A 的 grounded free-code 路径 B 组与 Stage B 的受控节点路径 C 组，判据来自真实 AEDT 2026.1 non-graphical 执行和 validation。</p>
    </section>

    <section class="grid">{_summary_cards(groups)}</section>

    <section class="panel callout">
      <h2>结论摘要</h2>
      <p>{escape(_conclusion(groups))}</p>
    </section>

    <section class="panel">
      <h2>我们做了什么</h2>
      <div class="split">
        <div class="box"><strong>Group B：官方知识增强自由代码</strong>Claude Code harness 接入 GitNexus、PyAEDT 官方源码和 pyaedt-examples，生成可直接在已有 <span class="mono">app</span> 对象上执行的 Python。</div>
        <div class="box"><strong>Group C：受控节点计划</strong>同一个 harness 只允许输出 JSON node plan，本地 runner 按顺序调用 <span class="mono">execute_node</span>，不允许自由 Python fallback。</div>
        <div class="box"><strong>真实 AEDT 判卷</strong>每次尝试在 AEDT non-graphical 中执行，并用 validation script 检查真实模型状态。C 组每次 attempt 使用独立 AEDT session，避免修复尝试被前一次残留污染。</div>
        <div class="box"><strong>修复反馈</strong>最多三次尝试。失败后把 schema、节点、AEDT 或 validation 错误反馈给 harness，让下一轮基于真实错误修复。</div>
      </div>
    </section>

    <section class="panel">
      <h2>分组指标</h2>
      <table>
        <thead><tr><th>分组</th><th>任务数</th><th>首轮成功率</th><th>三次内成功率</th><th>平均成功轮次</th><th>全部任务平均轮次</th><th>平均节点数</th><th>自由代码次数</th><th>失败类别</th></tr></thead>
        <tbody>{_group_rows(groups)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>任务级结果</h2>
      <table>
        <thead><tr><th>任务</th><th>难度</th><th>B 组自由代码</th><th>C 组节点计划</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>关键失败案例</h2>
      {_failure_block(failure_items)}
    </section>

    <section class="panel">
      <h2>工程解读</h2>
      <div class="split">
        <div class="box"><strong>C 组收益</strong>节点路径把高风险 PyAEDT 调用收敛到受控实现里。LLM 只负责生成结构化计划，schema、引用解析、节点输出和 validation 可以系统性修复。</div>
        <div class="box"><strong>B 组风险</strong>自由代码可以在简单几何和 setup 上表现很好，但 wave port 这类 AEDT boundary 调用仍容易出现 runtime error，甚至触发长时间 harness timeout。</div>
        <div class="box"><strong>当前限制</strong>Trap validation 已检查端口 assignment 是否来自 selected face，但仍不是完整电磁语义判卷。正式结论中应把它表述为结构性检查，而不是完整物理正确性证明。</div>
      </div>
    </section>

    <p class="footer">报告版本：{escape(str(report.get("version", "")))}；最大尝试次数：{escape(str(report.get("max_attempts", "")))}。</p>
  </main>
</body>
</html>
"""


def _summary_cards(groups: dict[str, Any]) -> str:
    cards = []
    for group in GROUP_ORDER:
        metrics = groups.get(group, {})
        cards.extend(
            [
                _card(f"{group} 组首轮成功率", _pct(metrics.get("first_pass_rate", 0.0))),
                _card(f"{group} 组三次内成功率", _pct(metrics.get("pass_rate_3try", 0.0))),
            ]
        )
    if "C" in groups:
        cards.append(_card("C 组自由代码次数", str(groups["C"].get("free_code_execution_count", 0))))
        cards.append(_card("C 组平均节点数", _num(groups["C"].get("avg_node_count", 0.0))))
    return "".join(cards)


def _group_rows(groups: dict[str, Any]) -> str:
    rows = []
    for group in GROUP_ORDER:
        metrics = groups.get(group, {})
        rows.append(
            "<tr>"
            f"<td>{escape(group)} 组</td>"
            f"<td>{metrics.get('task_count', 0)}</td>"
            f"<td>{_pct(metrics.get('first_pass_rate', 0.0))}</td>"
            f"<td>{_pct(metrics.get('pass_rate_3try', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_attempts_to_success', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_attempts_all', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_node_count', 0.0))}</td>"
            f"<td>{metrics.get('free_code_execution_count', '-')}</td>"
            f"<td>{escape(str(metrics.get('failure_categories', {})))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _task_row(task_id: str, task_data: dict[str, Any]) -> str:
    metadata = task_data.get("metadata", {})
    return (
        "<tr>"
        f"<td class='task-id'>{escape(task_id)}</td>"
        f"<td>{escape(str(metadata.get('level', '')))}</td>"
        f"{_result_cell(task_data.get('B', {}), include_tools=True)}"
        f"{_result_cell(task_data.get('C', {}), include_nodes=True)}"
        "</tr>"
    )


def _result_cell(result: dict[str, Any], include_tools: bool = False, include_nodes: bool = False) -> str:
    if not result:
        return "<td><span class='pill warn'>未运行</span></td>"
    final_pass = bool(result.get("final_pass", result.get("passed", False)))
    status = "pass" if final_pass else "fail"
    label = "通过" if final_pass else "失败"
    attempts = result.get("attempts", [])
    details = [f"成功轮次：{result.get('success_on_attempt') or '-'}", f"尝试次数：{len(attempts)}"]
    if result.get("failure_type"):
        details.append(f"失败类型：{result.get('failure_type')}")
    if include_tools and attempts:
        tool_usage = attempts[-1].get("tool_usage", {})
        details.append(f"GitNexus 查询：{tool_usage.get('gitnexus_query_count', 0)}")
    if include_nodes:
        details.append(f"节点步数：{len(result.get('node_steps', []))}")
    return (
        f"<td><span class='pill {status}'>{label}</span>"
        + "".join(f"<div class='subtle'>{escape(text)}</div>" for text in details)
        + "</td>"
    )


def _failure_block(items: str) -> str:
    if items:
        return f"<ul>{items}</ul>"
    return "<p>本次报告没有失败任务。</p>"


def _failure_item(task_id: str, task_data: dict[str, Any]) -> str:
    items = []
    for group in GROUP_ORDER:
        result = task_data.get(group, {})
        if not result or result.get("final_pass"):
            continue
        attempts = result.get("attempts", [])
        summaries = [
            f"第 {attempt.get('attempt')} 次：{attempt.get('failure_type') or result.get('failure_type') or 'unknown'}，{attempt.get('error_summary', '')}"
            for attempt in attempts
        ]
        items.append(
            f"<li><strong>{escape(task_id)} / {escape(group)} 组</strong><br>"
            + "<br>".join(escape(summary) for summary in summaries)
            + "</li>"
        )
    return "".join(items)


def _conclusion(groups: dict[str, Any]) -> str:
    b = groups.get("B", {})
    c = groups.get("C", {})
    if not b or not c:
        return "报告缺少 B/C 完整分组指标。"
    return (
        f"在本次小集对照中，B 组三次内成功率为 {_pct(b.get('pass_rate_3try', 0.0))}，"
        f"C 组为 {_pct(c.get('pass_rate_3try', 0.0))}。"
        f"C 组保持自由代码执行次数 {c.get('free_code_execution_count', 0)}，"
        "说明节点化路径能把高风险 AEDT API 调用集中在受控节点中，并在 wave port 类任务上提供更稳定的修复边界。"
    )


def _card(label: str, value: str) -> str:
    return f"<div class='card'><div class='label'>{escape(label)}</div><div class='value'>{escape(value)}</div></div>"


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _num(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"
