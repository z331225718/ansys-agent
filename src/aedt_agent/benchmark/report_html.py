from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path


GROUPS = ("A", "B", "C")


def write_html_report(report: dict, output_path: Path, model_name: str = "") -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html_report(report, model_name=model_name), encoding="utf-8")
    return output_path


def render_html_report(report: dict, model_name: str = "") -> str:
    metrics = report.get("go_nogo", {}).get("metrics", {})
    go_value = report.get("go_nogo", {}).get("go", False)
    node_readiness = report.get("node_readiness", {})
    tasks = report.get("tasks", {})
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    summary_cards = [
        _metric_card("Model", model_name or "N/A"),
        _metric_card("Go / No-Go", "GO" if go_value else "NO-GO", "pass" if go_value else "fail"),
        _metric_card("API Pass C", _pct(metrics.get("api_pass_rate_c", 0.0))),
        _metric_card("Semantic Pass B", _pct(metrics.get("semantic_pass_rate_b", 0.0))),
        _metric_card("Semantic Pass C", _pct(metrics.get("semantic_pass_rate_c", 0.0))),
        _metric_card("Trap Capture", _pct(metrics.get("trap_capture_rate", 0.0))),
    ]

    task_rows = []
    for task_id, task_data in sorted(tasks.items()):
        metadata = task_data.get("metadata", {})
        row = [
            f"<td class='task-id'>{escape(task_id)}</td>",
            f"<td>{escape(str(metadata.get('level', '')))}</td>",
        ]
        for group in GROUPS:
            group_data = task_data.get(group)
            if not group_data:
                row.append("<td class='muted'>-</td>")
                continue
            status = "pass" if group_data.get("passed") else "fail"
            label = "PASS" if group_data.get("passed") else "FAIL"
            details = (
                f"syntax:{_yn(group_data.get('syntax_pass'))} "
                f"api:{_yn(group_data.get('api_pass'))} "
                f"semantic:{_yn(group_data.get('semantic_lite_pass'))}"
            )
            row.append(f"<td><span class='pill {status}'>{label}</span><div class='subtle'>{escape(details)}</div></td>")
        task_rows.append("<tr>" + "".join(row) + "</tr>")

    readiness_rows = []
    readiness_nodes = node_readiness.get("nodes", {})
    candidate_ready = set(node_readiness.get("candidate_ready", []))
    for node_id, node_data in sorted(readiness_nodes.items()):
        readiness_rows.append(
            "<tr>"
            f"<td class='task-id'>{escape(node_id)}</td>"
            f"<td>{node_data.get('coverage', 0)}</td>"
            f"<td>{_pct(node_data.get('pass_rate', 0.0))}</td>"
            f"<td>{_pct(node_data.get('semantic_rate', 0.0))}</td>"
            f"<td>{'YES' if node_id in candidate_ready else 'NO'}</td>"
            "</tr>"
        )

    highlights = _render_highlights(tasks)
    methodology = _render_methodology(report)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stage A Benchmark Report</title>
  <style>
    :root {{
      --bg: #f3f5f8;
      --panel: #ffffff;
      --text: #19212b;
      --muted: #5e6a78;
      --line: #d7dde5;
      --pass: #1f8f52;
      --fail: #cc3d3d;
      --accent: #2357d5;
      --accent-soft: #e8f0ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    .page {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 32px 24px 48px;
    }}
    .hero {{
      background: linear-gradient(135deg, #11243f, #2357d5);
      color: white;
      padding: 28px 32px;
      border-radius: 12px;
      box-shadow: 0 12px 32px rgba(17, 36, 63, 0.18);
    }}
    .hero h1 {{ margin: 0 0 10px; font-size: 32px; }}
    .hero p {{ margin: 0; color: rgba(255,255,255,0.85); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin: 24px 0 32px;
    }}
    .card, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
    }}
    .card {{
      padding: 18px 18px 16px;
      min-height: 118px;
    }}
    .label {{ color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    .value {{ font-size: 28px; font-weight: 700; line-height: 1.1; }}
    .value.pass {{ color: var(--pass); }}
    .value.fail {{ color: var(--fail); }}
    .panel {{ padding: 20px; margin-top: 20px; }}
    .panel h2 {{ margin: 0 0 14px; font-size: 20px; }}
    .panel h3 {{ margin: 0 0 10px; font-size: 16px; }}
    .split {{
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 20px;
    }}
    .stack > * + * {{ margin-top: 16px; }}
    .info-list {{
      margin: 0;
      padding-left: 18px;
      color: var(--text);
    }}
    .info-list li {{ margin: 6px 0; }}
    .mini-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .mini-card {{
      padding: 14px;
      border-radius: 8px;
      background: #fafbfd;
      border: 1px solid var(--line);
    }}
    .mini-card strong {{ display: block; margin-bottom: 6px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 12px;
      border-top: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      border-top: none;
      background: #fafbfd;
    }}
    .pill {{
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
    }}
    .pill.pass {{ background: rgba(31, 143, 82, 0.12); color: var(--pass); }}
    .pill.fail {{ background: rgba(204, 61, 61, 0.12); color: var(--fail); }}
    .subtle {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .task-id {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .muted {{ color: var(--muted); }}
    .criteria {{
      background: #fafbfd;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .criteria code {{
      background: #eef2f7;
      padding: 1px 5px;
      border-radius: 4px;
    }}
    .highlights {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }}
    .highlight {{
      padding: 16px;
      border-radius: 10px;
      background: var(--accent-soft);
      border: 1px solid #cadeff;
    }}
    .highlight h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .footer {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 20px;
    }}
    @media (max-width: 980px) {{
      .split {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Stage A Benchmark Report</h1>
      <p>Generated at {escape(generated_at)}. This report compares A/B/C prompt groups using the Stage A offline grading pipeline.</p>
    </section>
    <section class="grid">
      {''.join(summary_cards)}
    </section>
    <section class="panel">
      <h2>Highlights</h2>
      <div class="highlights">{highlights}</div>
    </section>
    <section class="panel">
      <h2>How To Read This Report</h2>
      {methodology}
    </section>
    <section class="panel">
      <h2>Task Matrix</h2>
      <table>
        <thead>
          <tr>
            <th>Task</th>
            <th>Level</th>
            <th>Group A</th>
            <th>Group B</th>
            <th>Group C</th>
          </tr>
        </thead>
        <tbody>
          {''.join(task_rows)}
        </tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Node Readiness</h2>
      <table>
        <thead>
          <tr>
            <th>Node</th>
            <th>Coverage</th>
            <th>Pass Rate</th>
            <th>Semantic Rate</th>
            <th>Candidate Ready</th>
          </tr>
        </thead>
        <tbody>
          {''.join(readiness_rows)}
        </tbody>
      </table>
      <div class="footer">Candidate-ready nodes require coverage >= 3, pass rate >= 85%, and semantic rate >= 70%.</div>
    </section>
  </div>
</body>
</html>
"""


def _metric_card(label: str, value: str, tone: str = "") -> str:
    tone_class = f" {tone}" if tone else ""
    return (
        "<div class='card'>"
        f"<div class='label'>{escape(label)}</div>"
        f"<div class='value{tone_class}'>{escape(value)}</div>"
        "</div>"
    )


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _yn(value: bool) -> str:
    return "Y" if value else "N"


def _render_highlights(tasks: dict) -> str:
    group_totals = {group: {"pass": 0, "total": 0} for group in GROUPS}
    failures = []
    for task_id, task_data in tasks.items():
        for group in GROUPS:
            group_data = task_data.get(group)
            if not group_data:
                continue
            group_totals[group]["total"] += 1
            if group_data.get("passed"):
                group_totals[group]["pass"] += 1
            else:
                failures.append((task_id, group, group_data))

    overview = ", ".join(
        f"{group}: {group_totals[group]['pass']}/{group_totals[group]['total']}"
        for group in GROUPS
        if group_totals[group]["total"]
    )
    top_failures = "".join(
        "<li>"
        f"<span class='task-id'>{escape(task_id)}</span> ({group})"
        f"<div class='subtle'>{escape(_failure_summary(group_data))}</div>"
        "</li>"
        for task_id, group, group_data in failures[:5]
    ) or "<li>No failing tasks captured.</li>"
    return (
        "<div class='highlight'>"
        "<h3>Group pass counts</h3>"
        f"<div>{escape(overview)}</div>"
        "</div>"
        "<div class='highlight'>"
        "<h3>Candidate-ready nodes</h3>"
        f"<div>{escape(', '.join(sorted(set(report_candidate_nodes(tasks)))) or 'None')}</div>"
        "</div>"
        "<div class='highlight'>"
        "<h3>Representative failures</h3>"
        f"<ul>{top_failures}</ul>"
        "</div>"
    )


def report_candidate_nodes(tasks: dict) -> list[str]:
    nodes = set()
    for task_data in tasks.values():
        if task_data.get("C", {}).get("passed"):
            nodes.update(task_data.get("metadata", {}).get("allowed_nodes", []))
    return sorted(nodes)


def _render_methodology(report: dict) -> str:
    go_nogo = report.get("go_nogo", {})
    go = go_nogo.get("go", False)
    return (
        "<div class='split'>"
        "<div class='stack'>"
        "<div class='criteria'>"
        "<h3>Task PASS / FAIL</h3>"
        "<div>A task is marked <strong>PASS</strong> only when all four checks pass:</div>"
        "<ul class='info-list'>"
        "<li><code>syntax_pass</code>: Python syntax is valid.</li>"
        "<li><code>security_pass</code>: no restricted Python usage such as <code>subprocess</code> or <code>eval</code>.</li>"
        "<li><code>api_pass</code>: all <code>app.*</code> calls stay inside the task whitelist.</li>"
        "<li><code>semantic_lite_pass</code>: required workflow steps are present, such as geometry, port, airbox, setup, or sweep.</li>"
        "</ul>"
        "</div>"
        "<div class='criteria'>"
        "<h3>Prompt Groups</h3>"
        "<div class='mini-grid'>"
        "<div class='mini-card'><strong>Group A</strong><span>Requirement only. Minimal grounding.</span></div>"
        "<div class='mini-card'><strong>Group B</strong><span>Requirement plus API semantics and partial context.</span></div>"
        "<div class='mini-card'><strong>Group C</strong><span>Requirement plus whitelist, workflow cases, and trap guidance.</span></div>"
        "</div>"
        "</div>"
        "</div>"
        "<div class='stack'>"
        "<div class='criteria'>"
        "<h3>Go / No-Go Rule</h3>"
        f"<div>Current decision: <strong>{'GO' if go else 'NO-GO'}</strong></div>"
        "<ul class='info-list'>"
        "<li><code>api_pass_rate_c</code> must be at least <strong>85%</strong>.</li>"
        "<li><code>semantic_pass_rate_c</code> must be at least <strong>70%</strong>.</li>"
        "<li><code>trap_capture_rate</code> is tracked as supporting evidence for trap tasks.</li>"
        "</ul>"
        "</div>"
        "<div class='criteria'>"
        "<h3>Task Matrix Legend</h3>"
        "<ul class='info-list'>"
        "<li><strong>PASS</strong>: all four checks passed.</li>"
        "<li><strong>FAIL</strong>: one or more checks failed; the gray line under each pill shows which dimensions passed.</li>"
        "<li><strong>Node Readiness</strong>: computed from Group C only, because that is the most constrained and production-facing prompt set.</li>"
        "</ul>"
        "</div>"
        "</div>"
        "</div>"
    )


def _failure_summary(group_data: dict) -> str:
    failures = []
    if not group_data.get("syntax_pass"):
        failures.append("syntax")
    if not group_data.get("security_pass", True):
        failures.append("security")
    if not group_data.get("api_pass"):
        failures.append("api whitelist")
    if not group_data.get("semantic_lite_pass"):
        failures.append("workflow semantics")
    return ", ".join(failures) if failures else "passed"
