from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path


GROUPS = ("A", "B")


def write_html_report_v2(report: dict, output_path: Path, model_name: str = "") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html_report_v2(report, model_name=model_name), encoding="utf-8")
    return output_path


def render_html_report_v2(report: dict, model_name: str = "") -> str:
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    groups = report.get("groups", {})
    tasks = report.get("tasks", {})
    cards = [
        _metric_card("Model", model_name or "N/A"),
        _metric_card("A: first-pass", _pct(groups.get("A", {}).get("first_pass_rate", 0.0))),
        _metric_card("A: 3-attempt pass", _pct(groups.get("A", {}).get("pass_rate_3try", 0.0))),
        _metric_card("B: first-pass", _pct(groups.get("B", {}).get("first_pass_rate", 0.0))),
        _metric_card("B: 3-attempt pass", _pct(groups.get("B", {}).get("pass_rate_3try", 0.0))),
        _metric_card("B avg attempts", _num(groups.get("B", {}).get("avg_attempts_to_success", 0.0))),
        _metric_card("B tool usage", _pct(groups.get("B", {}).get("tool_usage_rate", 0.0))),
        _metric_card("B avg GitNexus queries", _num(groups.get("B", {}).get("avg_gitnexus_queries", 0.0))),
    ]
    rows = []
    for task_id, task_data in sorted(tasks.items()):
        row = [
            f"<td class='task-id'>{escape(task_id)}</td>",
            f"<td>{escape(str(task_data.get('metadata', {}).get('level', '')))}</td>",
        ]
        for group in GROUPS:
            result = task_data.get(group, {})
            status = "pass" if result.get("final_pass") else "fail"
            label = "PASS" if result.get("final_pass") else "FAIL"
            success = result.get("success_on_attempt")
            detail = f"success attempt: {success}" if success else f"failure: {result.get('failure_type', 'unknown')}"
            row.append(
                f"<td><span class='pill {status}'>{label}</span>"
                f"<div class='subtle'>{escape(detail)}</div>"
                f"<div class='subtle'>attempts: {len(result.get('attempts', []))}</div>"
                f"{_attempt_links(result.get('attempts', []))}</td>"
            )
        rows.append("<tr>" + "".join(row) + "</tr>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stage A Harness AEDT Benchmark</title>
  <style>
    :root {{
      --bg: #f5f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5c6672;
      --line: #d9dee6;
      --pass: #177245;
      --fail: #b93636;
      --accent: #2454a6;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    .page {{ max-width: 1320px; margin: 0 auto; padding: 28px 24px 44px; }}
    .hero {{ background: var(--panel); border: 1px solid var(--line); border-left: 6px solid var(--accent); padding: 24px; border-radius: 8px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; }}
    p {{ margin: 0; color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin: 22px 0; }}
    .card, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .card {{ padding: 16px; min-height: 104px; }}
    .label {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-size: 26px; font-weight: 700; }}
    .panel {{ padding: 18px; margin-top: 18px; }}
    .explain {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
    .box {{ background: #fafbfc; border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .box strong {{ display: block; margin-bottom: 6px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ border-top: none; color: var(--muted); background: #fafbfc; }}
    .task-id {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .pill {{ display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .pill.pass {{ color: var(--pass); background: rgba(23,114,69,0.12); }}
    .pill.fail {{ color: var(--fail); background: rgba(185,54,54,0.12); }}
    .subtle {{ color: var(--muted); font-size: 12px; margin-top: 5px; }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <h1>AEDT Harness Benchmark</h1>
      <p>Generated at {escape(generated_at)}. AEDT Execution Benchmark for two local agent-harness configurations using AEDT non-graphical execution and validation scripts.</p>
    </section>
    <section class="grid">{''.join(cards)}</section>
    <section class="panel">
      <h2>Method</h2>
      <div class="explain">
        <div class="box"><strong>Group A</strong>Same harness and model, no tools or official repository access.</div>
        <div class="box"><strong>Group B</strong>Same harness and model, GitNexus MCP plus read-only PyAEDT and pyaedt-examples access.</div>
        <div class="box"><strong>Success within 3 attempts</strong>A task passes when any attempt executes in AEDT and its validation script passes.</div>
        <div class="box"><strong>Average attempts to success</strong>Mean successful attempt number across tasks that passed.</div>
      </div>
    </section>
    <section class="panel">
      <h2>Group Metrics</h2>
      <table>
        <thead><tr><th>Group</th><th>Tasks</th><th>First-pass success</th><th>Success within 3 attempts</th><th>Average attempts to success</th><th>Tool usage</th><th>Avg GitNexus queries</th><th>Retrieval before code</th><th>Failure categories</th></tr></thead>
        <tbody>{_group_rows(groups)}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Task Results</h2>
      <table>
        <thead><tr><th>Task</th><th>Level</th><th>Group A</th><th>Group B</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def _group_rows(groups: dict) -> str:
    rows = []
    for group in GROUPS:
        metrics = groups.get(group, {})
        rows.append(
            "<tr>"
            f"<td>Group {group}</td>"
            f"<td>{metrics.get('task_count', 0)}</td>"
            f"<td>{_pct(metrics.get('first_pass_rate', 0.0))}</td>"
            f"<td>{_pct(metrics.get('pass_rate_3try', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_attempts_to_success', 0.0))}</td>"
            f"<td>{_pct(metrics.get('tool_usage_rate', 0.0))}</td>"
            f"<td>{_num(metrics.get('avg_gitnexus_queries', 0.0))}</td>"
            f"<td>{_pct(metrics.get('retrieval_before_code_rate', 0.0))}</td>"
            f"<td>{escape(str(metrics.get('failure_categories', {})))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _metric_card(label: str, value: str) -> str:
    return f"<div class='card'><div class='label'>{escape(label)}</div><div class='value'>{escape(value)}</div></div>"


def _attempt_links(attempts: list[dict]) -> str:
    links = []
    for attempt in attempts:
        label = f"attempt {attempt.get('attempt', '?')}"
        pieces = []
        for key, text in (
            ("code_path", "code"),
            ("exec_log_path", "exec"),
            ("transcript_path", "transcript"),
            ("tool_usage_path", "tools"),
        ):
            path = attempt.get(key)
            if path:
                pieces.append(f"<a href='{escape(str(path))}'>{text}</a>")
        if pieces:
            links.append(f"<div class='subtle'>{escape(label)}: {' | '.join(pieces)}</div>")
    return "".join(links)


def _pct(value: float) -> str:
    return f"{float(value) * 100:.1f}%"


def _num(value: float) -> str:
    return f"{float(value):.2f}"
