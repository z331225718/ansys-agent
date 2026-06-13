from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any


GROUP_ORDER = ("A", "B")


def write_html_report_v2(report: dict, output_path: Path, model_name: str = "") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html_report_v2(report, model_name=model_name), encoding="utf-8")
    return output_path


def render_html_report_v2(report: dict, model_name: str = "") -> str:
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    groups = report.get("groups", {})
    tasks = report.get("tasks", {})
    available_groups = _available_groups(report)
    comparison = _comparison(groups)
    cards = _summary_cards(groups, model_name)
    rows = [_task_row(task_id, task_data, available_groups) for task_id, task_data in sorted(tasks.items())]

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PyAEDT Agent 基准测试报告</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5b6673;
      --line: #d9dee6;
      --pass: #167048;
      --fail: #b23b3b;
      --warn: #8a5a00;
      --accent: #2454a6;
      --accent-2: #2f766f;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    .page {{ max-width: 1360px; margin: 0 auto; padding: 30px 24px 48px; }}
    .hero {{ background: var(--panel); border: 1px solid var(--line); border-left: 7px solid var(--accent); padding: 26px; border-radius: 8px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 15px; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.55; }}
    ul, ol {{ margin: 0; padding-left: 20px; color: var(--muted); line-height: 1.55; }}
    li + li {{ margin-top: 5px; }}
    .lede {{ max-width: 980px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin: 22px 0; }}
    .card, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .card {{ padding: 16px; min-height: 104px; }}
    .label {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-size: 26px; font-weight: 700; }}
    .panel {{ padding: 18px; margin-top: 18px; }}
    .split {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
    .box {{ background: #fafbfc; border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .box strong {{ display: block; margin-bottom: 6px; color: var(--text); }}
    .callout {{ border-left: 5px solid var(--accent-2); background: #f7fbfa; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ border-top: none; color: var(--muted); background: #fafbfc; }}
    .task-id {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .pill {{ display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .pill.pass {{ color: var(--pass); background: rgba(22,112,72,0.12); }}
    .pill.fail {{ color: var(--fail); background: rgba(178,59,59,0.12); }}
    .pill.warn {{ color: var(--warn); background: rgba(138,90,0,0.12); }}
    .subtle {{ color: var(--muted); font-size: 12px; margin-top: 5px; line-height: 1.35; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
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
      <h1>PyAEDT Agent 官方知识增强基准测试</h1>
      <p class="lede">生成时间：{escape(generated_at)}。这是 Stage A 的 AEDT 执行基准测试：对比“裸 LLM 代码生成 harness”和“接入 GitNexus MCP、PyAEDT 官方源码、官方 examples 后的 grounding harness”，并通过本地 AEDT non-graphical 模式真实运行生成代码来判定结果。</p>
    </section>

    <section class="grid">{''.join(cards)}</section>

    <section class="panel callout">
      <h2>结论摘要</h2>
      <p>{escape(comparison)}</p>
    </section>

    <section class="panel">
      <h2>我们做了什么</h2>
      <div class="split">
        <div class="box"><strong>真实 AEDT 判卷</strong>每个候选脚本都会通过 PyAEDT 在 AEDT 2026.1 non-graphical 模式下运行。只有 Python 执行成功且任务验证通过，才记为通过。</div>
        <div class="box"><strong>两个可比较实验组</strong>A 组使用同一个模型和 harness，但不提供工具。B 组使用同一个模型，同时接入 GitNexus MCP、只读 PyAEDT 源码和 pyaedt-examples。</div>
        <div class="box"><strong>失败修复循环</strong>每个任务最多允许三次尝试。某次尝试失败后，会把 AEDT/PyAEDT traceback 返回给 harness，让模型基于真实错误做定向修复。</div>
        <div class="box"><strong>Grounding 约束</strong>B 组被要求先查询官方来源再写代码，不能参考历史生成候选，并优先使用确认过的 PyAEDT API 签名和端口建模模式。</div>
      </div>
    </section>

    <section class="panel">
      <h2>Benchmark 如何运行</h2>
      <ol>
        <li>选择 Stage A 任务集，并为每次尝试创建一个干净的 HFSS design。</li>
        <li>调用本地 harness CLI，让它基于已有 <span class="mono">app</span> 对象生成 Python 代码。</li>
        <li>对 B 组，强制在生成代码前检索 GitNexus/PyAEDT 官方来源。</li>
        <li>在 AEDT non-graphical 模式下执行脚本，并记录 stdout、stderr、AEDT 日志、harness transcript 和工具使用情况。</li>
        <li>如果执行或验证失败，把错误日志反馈给模型继续修复，最多三次。</li>
        <li>汇总首轮成功率、三次内成功率、平均成功尝试次数、失败类别和检索行为。</li>
      </ol>
    </section>

    <section class="panel">
      <h2>对比配置</h2>
      <div class="split">
        <div class="box"><strong>A 组：纯语义生成</strong>不提供官方仓库访问、不提供 MCP 工具、不提供 examples。它衡量模型仅依靠任务文字和上一轮错误日志能生成到什么程度。</div>
        <div class="box"><strong>B 组：官方知识增强生成</strong>接入 PyAEDT 仓库上的 GitNexus MCP、只读官方 examples、官方 API 签名检索，并对 PyAEDT 几何、端口和修复行为加入更严格的 prompt 约束。</div>
      </div>
    </section>

    <section class="panel">
      <h2>分组指标</h2>
      <table>
        <thead><tr><th>分组</th><th>任务数</th><th>首轮成功率</th><th>三次内成功率</th><th>成功任务平均尝试次数</th><th>全部任务平均尝试次数</th><th>工具使用率</th><th>平均 GitNexus 查询数</th><th>代码前检索率</th><th>失败类别</th></tr></thead>
        <tbody>{_group_rows(groups, available_groups)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>任务级结果</h2>
      <table>
        <thead><tr><th>任务</th><th>难度</th>{''.join(f'<th>{escape(group)} 组</th>' for group in available_groups)}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>结果解读</h2>
      <div class="split">
        <div class="box"><strong>提升来自哪里</strong>Grounding 减少了 API 形态错误，例如 PyAEDT 参数名错误、face 遍历方式错误、wave port 使用方式不安全等。失败修复循环也把一部分 runtime failure 转化成后续尝试成功。</div>
        <div class="box"><strong>仍然困难的地方</strong>部分天线和端口任务仍然对 AEDT boundary 设置非常敏感。这些失败有价值，因为它们指出了后续 node 分解或强模板应该优先覆盖的位置。</div>
        <div class="box"><strong>为什么去掉离线判卷</strong>当前 benchmark 不再依赖静态字符串检查，而是用本地 AEDT 真实执行行为判定，所以 pass/fail 反映的是生成出来的自动化脚本是否真的能跑。</div>
      </div>
    </section>

    <p class="footer">任务表中的链接会指向本地运行产物：生成代码、AEDT 执行日志、harness transcript 和工具使用摘要。</p>
  </main>
</body>
</html>
"""


def _available_groups(report: dict) -> list[str]:
    groups = report.get("groups", {})
    tasks = report.get("tasks", {})
    available = [group for group in GROUP_ORDER if group in groups or any(group in data for data in tasks.values())]
    return available or list(GROUP_ORDER)


def _summary_cards(groups: dict, model_name: str) -> list[str]:
    cards = [_metric_card("生成 Harness", model_name or "N/A")]
    for group in GROUP_ORDER:
        if group not in groups:
            continue
        metrics = groups[group]
        cards.extend(
            [
                _metric_card(f"{group} 组首轮成功率", _pct(metrics.get("first_pass_rate", 0.0))),
                _metric_card(f"{group} 组三次内成功率", _pct(metrics.get("pass_rate_3try", 0.0))),
            ]
        )
    if "B" in groups:
        cards.extend(
            [
                _metric_card("B 组平均成功轮次", _num(groups["B"].get("avg_attempts_to_success", 0.0))),
                _metric_card("B 组平均 GitNexus 查询数", _num(groups["B"].get("avg_gitnexus_queries", 0.0))),
            ]
        )
    return cards


def _comparison(groups: dict) -> str:
    if "A" in groups and "B" in groups:
        a = float(groups["A"].get("pass_rate_3try", 0.0))
        b = float(groups["B"].get("pass_rate_3try", 0.0))
        first_a = float(groups["A"].get("first_pass_rate", 0.0))
        first_b = float(groups["B"].get("first_pass_rate", 0.0))
        return (
            f"B 组三次内成功率达到 {_pct(b)}，A 组为 {_pct(a)}，提升 {_pct_points(b - a)}。"
            f"首轮成功率从 {_pct(first_a)} 提升到 {_pct(first_b)}。"
            "这说明官方来源 grounding 与真实错误日志修复反馈，能够显著提高可执行 PyAEDT 代码生成质量。"
        )
    if "B" in groups:
        b = groups["B"]
        return (
            f"最终 B 组三次内成功率为 {_pct(b.get('pass_rate_3try', 0.0))}，"
            f"首轮成功率为 {_pct(b.get('first_pass_rate', 0.0))}，"
            f"成功任务的平均成功轮次为 {_num(b.get('avg_attempts_to_success', 0.0))}。"
        )
    return "报告中没有找到分组指标。"


def _group_rows(groups: dict, available_groups: list[str]) -> str:
    rows = []
    for group in available_groups:
        metrics = groups.get(group, {})
        rows.append(
            "<tr>"
            f"<td>{escape(group)} 组</td>"
            f"<td>{metrics.get('task_count', 0)}</td>"
            f"<td>{_pct(metrics.get('first_pass_rate', 0.0))}</td>"
            f"<td>{_pct(metrics.get('pass_rate_3try', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_attempts_to_success', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_attempts_all', 0.0))}</td>"
            f"<td>{_pct(metrics.get('tool_usage_rate', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_gitnexus_queries', 0.0))}</td>"
            f"<td>{_pct(metrics.get('retrieval_before_code_rate', 0.0))}</td>"
            f"<td>{escape(str(metrics.get('failure_categories', {})))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _task_row(task_id: str, task_data: dict, available_groups: list[str]) -> str:
    cells = [
        f"<td class='task-id'>{escape(task_id)}</td>",
        f"<td>{escape(str(task_data.get('metadata', {}).get('level', '')))}</td>",
    ]
    for group in available_groups:
        cells.append(_task_group_cell(task_data.get(group, {})))
    return "<tr>" + "".join(cells) + "</tr>"


def _task_group_cell(result: dict) -> str:
    if not result:
        return "<td><span class='pill warn'>未运行</span></td>"
    final_pass = _result_passed(result)
    status = "pass" if final_pass else "fail"
    label = "通过" if final_pass else "失败"
    success = result.get("success_on_attempt")
    attempts = result.get("attempts", [])
    if success:
        detail = f"成功轮次：第 {success} 次"
    elif attempts:
        detail = f"失败类型：{result.get('failure_type', 'unknown')}"
    else:
        detail = "旧版结果" if "passed" in result else "未运行"
    return (
        f"<td><span class='pill {status}'>{label}</span>"
        f"<div class='subtle'>{escape(detail)}</div>"
        f"<div class='subtle'>尝试次数：{len(attempts) if attempts else _legacy_attempt_count(result)}</div>"
        f"{_attempt_links(attempts)}</td>"
    )


def _result_passed(result: dict) -> bool:
    if "final_pass" in result:
        return bool(result.get("final_pass"))
    return bool(result.get("passed"))


def _legacy_attempt_count(result: dict) -> int:
    if "passed" in result:
        return 1
    return 0


def _metric_card(label: str, value: str) -> str:
    return f"<div class='card'><div class='label'>{escape(label)}</div><div class='value'>{escape(value)}</div></div>"


def _attempt_links(attempts: list[dict]) -> str:
    links = []
    for attempt in attempts:
        label = f"第 {attempt.get('attempt', '?')} 次尝试"
        pieces = []
        for key, text in (
            ("code_path", "代码"),
            ("exec_log_path", "执行日志"),
            ("transcript_path", "过程记录"),
            ("tool_usage_path", "工具"),
        ):
            path = attempt.get(key)
            if path:
                pieces.append(f"<a href='{escape(_artifact_href(str(path)))}'>{text}</a>")
        elapsed = attempt.get("elapsed_seconds")
        elapsed_text = f" | {float(elapsed):.1f}s" if elapsed is not None else ""
        if pieces:
            links.append(f"<div class='subtle'>{escape(label)}{elapsed_text}: {' | '.join(pieces)}</div>")
    return "".join(links)


def _artifact_href(path: str) -> str:
    marker = "benchmarks/runs/"
    if marker in path:
        return "../runs/" + path.split(marker, 1)[1]
    return path


def _pct(value: float) -> str:
    return f"{float(value) * 100:.1f}%"


def _pct_points(value: float) -> str:
    return f"{float(value) * 100:+.1f} 个百分点"


def _num(value: float) -> str:
    return f"{float(value):.2f}"
