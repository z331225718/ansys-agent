from __future__ import annotations

import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from aedt_agent.ansys_agent.case_config import AnsysAgentCase


def run_ansys_agent_panel(supervisor, *, host: str, port: int) -> None:
    handler = _make_handler(supervisor)
    server = ThreadingHTTPServer((host, port), handler)
    display_host = "localhost" if host in {"", "0.0.0.0", "::"} else host
    print(f"ansys-agent operator panel: http://{display_host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def render_operator_panel(case: AnsysAgentCase) -> str:
    poll_seconds = max(10, int(case.poll_interval_seconds or 30))
    title = f"ansys-agent / {case.case_id}"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --surface: #ffffff;
      --surface-strong: #eef3f8;
      --text: #17202a;
      --muted: #617083;
      --line: #d9e1ea;
      --accent: #1769aa;
      --accent-strong: #0e4f82;
      --ok: #0a7c59;
      --warn: #a65f00;
      --bad: #b42318;
      --shadow: 0 10px 28px rgba(24, 39, 75, 0.08);
      font-family: "Segoe UI", Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      line-height: 1.4;
    }}
    .shell {{
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      box-shadow: 0 1px 0 rgba(18, 32, 46, 0.03);
    }}
    .topbar {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 14px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.2;
      font-weight: 650;
      letter-spacing: 0;
    }}
    .meta {{
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    main {{
      max-width: 1280px;
      width: 100%;
      margin: 0 auto;
      padding: 18px 22px 28px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: minmax(240px, 1.1fr) minmax(260px, 1.4fr) auto;
      gap: 12px;
      align-items: stretch;
      margin-bottom: 14px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px;
      min-width: 0;
    }}
    .panel h2 {{
      margin: 0 0 10px;
      font-size: 13px;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 700;
      letter-spacing: 0;
    }}
    .status-line {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--muted);
      flex: 0 0 auto;
    }}
    .dot.running {{ background: var(--accent); }}
    .dot.waiting_approval {{ background: var(--warn); }}
    .dot.succeeded {{ background: var(--ok); }}
    .dot.failed, .dot.canceled {{ background: var(--bad); }}
    .status-word {{
      font-size: 24px;
      font-weight: 700;
      line-height: 1.1;
      overflow-wrap: anywhere;
    }}
    .subtle {{
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .command {{
      margin-top: 8px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface-strong);
      color: #263545;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-content: flex-start;
      justify-content: flex-end;
    }}
    button {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      min-height: 34px;
      padding: 7px 11px;
      font: 600 13px/1.2 "Segoe UI", Arial, sans-serif;
      cursor: pointer;
    }}
    button.primary {{
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }}
    button.danger {{
      color: var(--bad);
      border-color: #efc7c2;
    }}
    button:hover {{ border-color: var(--accent); }}
    button.primary:hover {{ background: var(--accent-strong); }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.55;
    }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(360px, 1fr) minmax(360px, 1fr);
      gap: 14px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    th, td {{
      padding: 8px 6px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .approval {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 10px;
      background: #fffaf2;
    }}
    .approval textarea {{
      width: 100%;
      min-height: 64px;
      resize: vertical;
      margin: 9px 0;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: 13px/1.3 "Segoe UI", Arial, sans-serif;
    }}
    .json-block {{
      max-height: 280px;
      overflow: auto;
      background: #111827;
      color: #d1d5db;
      border-radius: 8px;
      padding: 12px;
      font: 12px/1.45 Consolas, "Courier New", monospace;
      white-space: pre-wrap;
    }}
    .empty {{
      color: var(--muted);
      padding: 8px 0;
    }}
    @media (max-width: 880px) {{
      .topbar, main {{ padding-left: 14px; padding-right: 14px; }}
      .summary, .grid {{ grid-template-columns: 1fr; }}
      .actions {{ justify-content: flex-start; }}
      .meta {{ white-space: normal; justify-content: flex-end; }}
    }}
  </style>
</head>
<body data-poll-seconds="{poll_seconds}">
  <div class="shell">
    <header>
      <div class="topbar">
        <h1>{escape(title)}</h1>
        <div class="meta">
          <span id="graphRunId"></span>
          <span id="lastRefresh"></span>
        </div>
      </div>
    </header>
    <main>
      <section class="summary">
        <div class="panel">
          <h2>Status</h2>
          <div class="status-line">
            <span id="statusDot" class="dot"></span>
            <span id="statusWord" class="status-word">loading</span>
          </div>
          <div id="activeNode" class="subtle"></div>
        </div>
        <div class="panel">
          <h2>Next</h2>
          <div id="nextSafeAction" class="subtle"></div>
          <div id="recommendedCommand" class="command"></div>
        </div>
        <div class="panel actions" id="globalActions"></div>
      </section>
      <section class="grid">
        <div class="panel">
          <h2>Approvals</h2>
          <div id="approvals"></div>
        </div>
        <div class="panel">
          <h2>Metrics</h2>
          <table><tbody id="metrics"></tbody></table>
        </div>
        <div class="panel">
          <h2>Artifacts</h2>
          <table>
            <thead><tr><th>Kind</th><th>Path</th><th>Exists</th></tr></thead>
            <tbody id="artifacts"></tbody>
          </table>
        </div>
        <div class="panel">
          <h2>Failure</h2>
          <div id="failure"></div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const pollSeconds = Number(document.body.dataset.pollSeconds || 30);
    const state = {{ busy: false, latest: null }};
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({{
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }}[ch]));

    async function api(path, body) {{
      const options = body === undefined ? {{}} : {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }};
      const res = await fetch(path, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || data.error || res.statusText);
      return data;
    }}

    async function loadStatus() {{
      if (state.busy) return;
      try {{
        const data = await api('/api/status');
        state.latest = data;
        render(data);
      }} catch (error) {{
        $('failure').innerHTML = `<div class="json-block">${{esc(error.message)}}</div>`;
      }}
    }}

    function render(data) {{
      const status = data.status || 'unknown';
      $('statusDot').className = `dot ${{status}}`;
      $('statusWord').textContent = status;
      $('activeNode').textContent = data.active_node ? `active node: ${{data.active_node}}` : '';
      $('graphRunId').textContent = data.graph_run_id ? `graph: ${{data.graph_run_id}}` : '';
      $('lastRefresh').textContent = new Date().toLocaleTimeString();
      $('nextSafeAction').textContent = data.next_safe_action || '';
      $('recommendedCommand').textContent = data.recommended_command || '';
      window.__handlers = {{}};
      renderGlobalActions(data);
      renderApprovals(data.pending_approvals || []);
      renderMetrics(data.metrics || {{}});
      renderArtifacts(data.latest_artifacts || []);
      renderFailure(data.failure || {{}});
    }}

    function renderGlobalActions(data) {{
      const commands = data.available_commands || {{}};
      const graphRunId = data.graph_run_id || '';
      const buttons = [];
      if (commands.resume) buttons.push(button('Resume', 'primary', () => postAction('/api/resume', {{ graph_run_id: graphRunId }})));
      if (commands.resume_after_decision) buttons.push(button('Resume', 'primary', () => postAction('/api/resume', {{ graph_run_id: graphRunId }})));
      if (commands.status) buttons.push(button('Refresh', '', loadStatus));
      if (commands.stop) buttons.push(button('Stop', 'danger', () => postAction('/api/stop', {{ graph_run_id: graphRunId, reason: 'operator panel stop' }})));
      $('globalActions').innerHTML = buttons.join('');
      bindButtons('globalActions');
    }}

    function renderApprovals(items) {{
      if (!items.length) {{
        $('approvals').innerHTML = '<div class="empty">none</div>';
        return;
      }}
      $('approvals').innerHTML = items.map((item, index) => `
        <div class="approval">
          <div><strong>${{esc(item.approval_id)}}</strong></div>
          <div class="subtle">${{esc(item.reason || '')}}</div>
          <textarea id="approvalComment${{index}}" placeholder="comment"></textarea>
          <div class="actions" style="justify-content:flex-start">
            ${{button('Approve + Resume', 'primary', () => approve(item.approval_id, index, true))}}
            ${{button('Approve', '', () => approve(item.approval_id, index, false))}}
            ${{button('Reject', 'danger', () => rejectApproval(item.approval_id, index))}}
          </div>
        </div>
      `).join('');
      bindButtons('approvals');
    }}

    function renderMetrics(metrics) {{
      const rows = Object.entries(metrics).filter(([, value]) => value !== '' && value !== null && value !== undefined);
      $('metrics').innerHTML = rows.length
        ? rows.map(([key, value]) => `<tr><th>${{esc(key)}}</th><td>${{esc(value)}}</td></tr>`).join('')
        : '<tr><td class="empty">none</td></tr>';
    }}

    function renderArtifacts(items) {{
      $('artifacts').innerHTML = items.length
        ? items.map((item) => `<tr><td>${{esc(item.kind)}}</td><td>${{esc(item.path)}}</td><td>${{item.exists ? 'yes' : 'no'}}</td></tr>`).join('')
        : '<tr><td class="empty" colspan="3">none</td></tr>';
    }}

    function renderFailure(failure) {{
      $('failure').innerHTML = Object.keys(failure).length
        ? `<div class="json-block">${{esc(JSON.stringify(failure, null, 2))}}</div>`
        : '<div class="empty">none</div>';
    }}

    function button(label, variant, handler) {{
      const id = `btn${{Math.random().toString(36).slice(2)}}`;
      window.__handlers[id] = handler;
      return `<button data-handler="${{id}}" class="${{variant || ''}}">${{esc(label)}}</button>`;
    }}

    window.__handlers = {{}};
    function bindButtons(rootId) {{
      document.querySelectorAll(`#${{rootId}} button[data-handler]`).forEach((node) => {{
        const handler = window.__handlers[node.dataset.handler];
        node.addEventListener('click', handler);
      }});
    }}

    async function postAction(path, body) {{
      state.busy = true;
      try {{
        const data = await api(path, body);
        render(data.agent_status || data);
      }} catch (error) {{
        $('failure').innerHTML = `<div class="json-block">${{esc(error.message)}}</div>`;
      }} finally {{
        state.busy = false;
      }}
    }}

    function comment(index) {{
      const node = $(`approvalComment${{index}}`);
      return node ? node.value : '';
    }}
    function approve(approvalId, index, resume) {{
      const graphRunId = state.latest?.graph_run_id || '';
      return postAction('/api/approve', {{
        approval_id: approvalId,
        option_id: 'approve',
        comment: comment(index),
        resume,
        graph_run_id: graphRunId
      }});
    }}
    function rejectApproval(approvalId, index) {{
      return postAction('/api/reject', {{ approval_id: approvalId, comment: comment(index) }});
    }}

    loadStatus();
    setInterval(loadStatus, pollSeconds * 1000);
  </script>
</body>
</html>
"""


def dispatch_action(supervisor, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action == "status":
        return supervisor.status()
    if action == "resume":
        return supervisor.resume(graph_run_id=str(payload.get("graph_run_id") or ""))
    if action == "approve":
        return supervisor.approve(
            approval_id=str(payload.get("approval_id") or ""),
            option_id=str(payload.get("option_id") or "approve"),
            comment=payload.get("comment"),
            resume=bool(payload.get("resume")),
            graph_run_id=str(payload.get("graph_run_id") or ""),
        )
    if action == "reject":
        return supervisor.reject(
            approval_id=str(payload.get("approval_id") or ""),
            comment=payload.get("comment"),
        )
    if action == "stop":
        return supervisor.stop(
            graph_run_id=str(payload.get("graph_run_id") or ""),
            reason=str(payload.get("reason") or "operator panel stop"),
        )
    raise ValueError(f"unknown action: {action}")


def _make_handler(supervisor):
    class AnsysAgentPanelHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                self._write_html(render_operator_panel(supervisor.case))
                return
            if self.path == "/api/status":
                self._write_json(dispatch_action(supervisor, "status", {}))
                return
            self._write_json({"error": {"message": "not found"}}, status=404)

        def do_POST(self) -> None:
            action = self.path.removeprefix("/api/")
            try:
                payload = self._read_json()
                self._write_json(dispatch_action(supervisor, action, payload))
            except Exception as exc:
                self._write_json(
                    {"error": {"message": str(exc), "type": type(exc).__name__}},
                    status=400,
                )

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def _write_html(self, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _write_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return AnsysAgentPanelHandler
