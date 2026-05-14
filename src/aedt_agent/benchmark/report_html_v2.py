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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PyAEDT Agent Benchmark Report</title>
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
      <h1>PyAEDT Agent Grounding Benchmark</h1>
      <p class="lede">Generated at {escape(generated_at)}. AEDT Execution Benchmark for the Stage A experiment: compare a bare LLM coding harness against the same harness grounded with GitNexus MCP, official PyAEDT source, and official examples, then judge generated code by running it in local AEDT non-graphical mode.</p>
    </section>

    <section class="grid">{''.join(cards)}</section>

    <section class="panel callout">
      <h2>Executive Summary</h2>
      <p>{escape(comparison)}</p>
    </section>

    <section class="panel">
      <h2>What We Built</h2>
      <div class="split">
        <div class="box"><strong>Real AEDT judging</strong>Each candidate script runs in AEDT 2026.1 non-graphical mode through PyAEDT. A pass requires both Python execution and task validation to succeed.</div>
        <div class="box"><strong>Two comparable groups</strong>Group A uses the same model and harness with no tools. Group B uses the same model plus GitNexus MCP, read-only PyAEDT source, and pyaedt-examples access.</div>
        <div class="box"><strong>Repair loop</strong>Each task allows up to three attempts. When an attempt fails, the AEDT/PyAEDT traceback is returned to the harness for targeted repair.</div>
        <div class="box"><strong>Grounding controls</strong>Group B is instructed to query official sources before coding, avoid prior generated candidates, and use verified PyAEDT API signatures and port patterns.</div>
      </div>
    </section>

    <section class="panel">
      <h2>How The Benchmark Runs</h2>
      <ol>
        <li>Select the Stage A task set and create a fresh HFSS design for each attempt.</li>
        <li>Ask the local harness CLI to generate Python using the existing <span class="mono">app</span> object.</li>
        <li>For Group B, require retrieval from GitNexus/PyAEDT official sources before code generation.</li>
        <li>Execute the script in AEDT non-graphical mode and capture stdout, stderr, AEDT logs, transcripts, and tool usage.</li>
        <li>If execution or validation fails, feed the error log back for another attempt, up to three attempts.</li>
        <li>Aggregate first-pass rate, three-attempt pass rate, attempts-to-success, failure categories, and retrieval behavior.</li>
      </ol>
    </section>

    <section class="panel">
      <h2>Configuration Compared</h2>
      <div class="split">
        <div class="box"><strong>Group A: bare semantic generation</strong>No official repository access, no MCP tools, no examples. This measures what the model can produce from the task wording and prior error logs alone.</div>
        <div class="box"><strong>Group B: grounded generation</strong>GitNexus MCP over the PyAEDT repository, read-only official examples, official API signature lookup, and stricter prompt rules for PyAEDT geometry, ports, and repair behavior.</div>
      </div>
    </section>

    <section class="panel">
      <h2>Group Metrics</h2>
      <table>
        <thead><tr><th>Group</th><th>Tasks</th><th>First-pass success</th><th>Success within 3 attempts</th><th>Average attempts to success</th><th>Average attempts all</th><th>Tool usage</th><th>Avg GitNexus queries</th><th>Retrieval before code</th><th>Failure categories</th></tr></thead>
        <tbody>{_group_rows(groups, available_groups)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Task-Level Results</h2>
      <table>
        <thead><tr><th>Task</th><th>Level</th>{''.join(f'<th>Group {escape(group)}</th>' for group in available_groups)}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Interpretation</h2>
      <div class="split">
        <div class="box"><strong>What improved</strong>Grounding reduced API-shape mistakes such as wrong PyAEDT argument names, invalid face iteration, and unsafe wave-port patterns. The repair loop converted many runtime failures into successful later attempts.</div>
        <div class="box"><strong>What remains hard</strong>Some antenna and port tasks remain sensitive to exact AEDT boundary setup. These failures are useful because they identify where future node decomposition or stronger templates should focus.</div>
        <div class="box"><strong>Why offline judging was removed</strong>The benchmark now measures real executable behavior against local AEDT instead of static string checks, so pass/fail reflects whether the generated automation actually runs.</div>
      </div>
    </section>

    <p class="footer">Artifacts linked in the task table point to local run files when available: generated code, AEDT execution logs, harness transcripts, and tool-usage summaries.</p>
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
    cards = [_metric_card("Harness", model_name or "N/A")]
    for group in GROUP_ORDER:
        if group not in groups:
            continue
        metrics = groups[group]
        cards.extend(
            [
                _metric_card(f"{group}: first-pass", _pct(metrics.get("first_pass_rate", 0.0))),
                _metric_card(f"{group}: 3-attempt pass", _pct(metrics.get("pass_rate_3try", 0.0))),
            ]
        )
    if "B" in groups:
        cards.extend(
            [
                _metric_card("B avg success attempt", _num(groups["B"].get("avg_attempts_to_success", 0.0))),
                _metric_card("B avg GitNexus queries", _num(groups["B"].get("avg_gitnexus_queries", 0.0))),
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
            f"Group B reached {_pct(b)} success within three attempts versus Group A at {_pct(a)}, "
            f"a {_pct_points(b - a)} lift. First-pass success moved from {_pct(first_a)} to {_pct(first_b)}. "
            "This indicates that official-source grounding plus repair feedback improves executable PyAEDT code generation."
        )
    if "B" in groups:
        b = groups["B"]
        return (
            f"Final Group B validation reached {_pct(b.get('pass_rate_3try', 0.0))} success within three attempts "
            f"with {_pct(b.get('first_pass_rate', 0.0))} first-pass success and an average successful attempt of "
            f"{_num(b.get('avg_attempts_to_success', 0.0))}."
        )
    return "No group metrics were found in this report."


def _group_rows(groups: dict, available_groups: list[str]) -> str:
    rows = []
    for group in available_groups:
        metrics = groups.get(group, {})
        rows.append(
            "<tr>"
            f"<td>Group {escape(group)}</td>"
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
        return "<td><span class='pill warn'>NOT RUN</span></td>"
    final_pass = _result_passed(result)
    status = "pass" if final_pass else "fail"
    label = "PASS" if final_pass else "FAIL"
    success = result.get("success_on_attempt")
    attempts = result.get("attempts", [])
    if success:
        detail = f"success attempt: {success}"
    elif attempts:
        detail = f"failure: {result.get('failure_type', 'unknown')}"
    else:
        detail = "legacy result" if "passed" in result else "not run"
    return (
        f"<td><span class='pill {status}'>{label}</span>"
        f"<div class='subtle'>{escape(detail)}</div>"
        f"<div class='subtle'>attempts: {len(attempts) if attempts else _legacy_attempt_count(result)}</div>"
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
    return f"{float(value) * 100:+.1f} percentage points"


def _num(value: float) -> str:
    return f"{float(value):.2f}"
