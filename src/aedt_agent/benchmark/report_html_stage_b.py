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
    task_count = max((metrics.get("task_count", 0) for metrics in groups.values()), default=len(tasks))

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
    .lead {{ font-size: 16px; color: var(--text); margin-top: 10px; }}
    .finding {{ border-left: 4px solid var(--accent); padding-left: 12px; }}
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
      <p class="lead">本轮目标不是比较不同 LLM，而是验证：把 PyAEDT 调用收敛为受控节点以后，是否能在同一个 harness、同一批任务、同样最多三次修复的条件下，提高自动建模任务的稳定性。</p>
    </section>

    <section class="grid">{_summary_cards(groups)}</section>

    <section class="panel callout">
      <h2>结论摘要</h2>
      <p>{escape(_conclusion(groups))}</p>
    </section>

    <section class="panel">
      <h2>实验设计</h2>
      <div class="split">
        <div class="box"><strong>任务集合</strong>固定 {escape(str(task_count))} 个 AEDT/PyAEDT 建模任务，覆盖 L1 基础操作、L2 组合建模和 Trap 类结构性错误检查。</div>
        <div class="box"><strong>Group B：工具增强自由代码</strong>Claude Code harness 允许读取官方 PyAEDT 源码、pyaedt-examples，并通过 GitNexus 查询 API 上下文；最终输出可在 <span class="mono">app</span> 对象上执行的 Python。</div>
        <div class="box"><strong>Group C：受控节点计划</strong>同一个 harness 输出 JSON node plan，本地 runner 只按白名单节点执行，如 <span class="mono">create_port</span>、<span class="mono">create_setup</span>、<span class="mono">create_sweep_or_export</span>。</div>
        <div class="box"><strong>三次修复机制</strong>每个任务最多三次。失败时把真实 schema、节点、AEDT runtime 或 validation 错误反馈给下一轮，记录首轮成功率、三次内成功率和平均成功轮次。</div>
      </div>
    </section>

    <section class="panel">
      <h2>判定依据</h2>
      <div class="split">
        <div class="box"><strong>通过条件</strong>候选方案必须在真实 AEDT 2026.1 non-graphical 会话中执行完成，并通过对应 validation script 检查模型状态、对象、材料、端口、边界或 setup/sweep。</div>
        <div class="box"><strong>失败条件</strong>生成失败、schema 不匹配、节点引用错误、PyAEDT/AEDT runtime error、timeout 或 validation 不通过都会计为失败，并进入下一轮修复。</div>
        <div class="box"><strong>边界说明</strong>当前 validation 是结构性判卷，不等价于完整电磁物理正确性证明；Trap 任务用于检查可自动化拦截的典型结构错误。</div>
      </div>
    </section>

    <section class="panel">
      <h2>关键发现</h2>
      <div class="split">{_finding_boxes(groups)}</div>
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
        <div class="box"><strong>当前限制</strong>Trap validation 已检查端口 assignment 等结构约束，但仍不是完整电磁语义判卷。正式结论中应把它表述为结构性检查，而不是完整物理正确性证明。</div>
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
    if "B" in groups and "C" in groups:
        cards.append(_card("三次成功率差值", _delta_pct(groups["C"].get("pass_rate_3try", 0.0), groups["B"].get("pass_rate_3try", 0.0))))
        cards.append(_card("首轮成功率差值", _delta_pct(groups["C"].get("first_pass_rate", 0.0), groups["B"].get("first_pass_rate", 0.0))))
    return "".join(cards)


def _finding_boxes(groups: dict[str, Any]) -> str:
    b = groups.get("B", {})
    c = groups.get("C", {})
    if not b or not c:
        return "<div class='box finding'><strong>数据不足</strong>需要同时运行 B/C 两组后才能生成关键发现。</div>"
    findings = [
        (
            "三次内成功率",
            f"B 组 {_pct(b.get('pass_rate_3try', 0.0))}，C 组 {_pct(c.get('pass_rate_3try', 0.0))}，差值 {_delta_pct(c.get('pass_rate_3try', 0.0), b.get('pass_rate_3try', 0.0))}。",
        ),
        (
            "首轮稳定性",
            f"B 组 {_pct(b.get('first_pass_rate', 0.0))}，C 组 {_pct(c.get('first_pass_rate', 0.0))}，节点路径减少了自由代码直接撞 AEDT API 的概率。",
        ),
        (
            "修复成本",
            f"B 组全部任务平均轮次 {_num(b.get('avg_attempts_all', 0.0))}，C 组 {_num(c.get('avg_attempts_all', 0.0))}；C 组失败反馈更集中在节点/schema 层。",
        ),
        (
            "受控执行",
            f"C 组自由代码执行次数 {c.get('free_code_execution_count', 0)}，节点覆盖率 {_pct(c.get('node_coverage_rate', 0.0))}。",
        ),
    ]
    return "".join(f"<div class='box finding'><strong>{escape(title)}</strong>{escape(text)}</div>" for title, text in findings)


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
        f"平均成功轮次为 {_num(c.get('avg_attempts_to_success', 0.0))}。"
        "结果支持继续推进 Stage B：把高风险 AEDT API 调用集中到受控节点中，并让 LLM 只负责可校验的结构化计划。"
    )


def _card(label: str, value: str) -> str:
    return f"<div class='card'><div class='label'>{escape(label)}</div><div class='value'>{escape(value)}</div></div>"


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _delta_pct(left: Any, right: Any) -> str:
    try:
        value = (float(left) - float(right)) * 100
    except (TypeError, ValueError):
        value = 0.0
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f} pp"


def _num(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"
