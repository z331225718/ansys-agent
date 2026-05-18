from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from aedt_agent.demo.service import DemoService


def render_demo_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AEDT Agent Stage C.1</title>
  <style>
    body{margin:0;font-family:Arial,'Noto Sans SC',sans-serif;background:#f8fafc;color:#111827}
    header{padding:18px 24px;border-bottom:1px solid #d1d5db;background:#fff}
    h1{margin:0;font-size:24px}.layout{display:grid;grid-template-columns:240px minmax(0,1fr) 300px;min-height:calc(100vh - 65px)}
    nav,aside{background:#fff;border-right:1px solid #d1d5db;padding:16px}aside{border-right:0;border-left:1px solid #d1d5db}
    main{padding:18px;display:grid;gap:16px}.panel{background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:14px}
    button,select,textarea,input{font:inherit}button{border:1px solid #1d4ed8;background:#1d4ed8;color:#fff;border-radius:6px;padding:8px 10px;cursor:pointer}
    textarea{width:100%;min-height:90px}pre{background:#0f172a;color:#e5e7eb;padding:12px;border-radius:6px;overflow:auto;max-height:360px}
    a{color:#1d4ed8}.muted{color:#6b7280}.stack{display:grid;gap:10px}.row{display:flex;gap:8px;flex-wrap:wrap}
    @media(max-width:900px){.layout{grid-template-columns:1fr}nav,aside{border:0;border-bottom:1px solid #d1d5db}}
  </style>
</head>
<body>
<header><h1>AEDT Agent Stage C.1</h1><div class="muted">节点化 workflow demo / fake adapter execution / report links</div></header>
<div class="layout">
  <nav class="stack">
    <strong>Navigation</strong>
    <button onclick="loadNodes()">Nodes</button>
    <button onclick="loadTemplates()">Templates</button>
    <button onclick="loadReports()">Reports</button>
    <a href="/reports/stage_c_real_smoke_dashboard.html" target="_blank">真实 AEDT Smoke</a>
    <a href="/reports/stage_c_node_evolution_review.html" target="_blank">节点进化 Review</a>
    <a href="/reports/stage_c2_planner_benchmark.html" target="_blank">Planner Benchmark</a>
  </nav>
  <main>
    <section class="panel stack">
      <h2>Planner</h2>
      <label>Planner Mode
        <select id="plannerMode">
          <option value="deterministic">deterministic</option>
          <option value="llm">llm</option>
        </select>
      </label>
      <textarea id="request">create a microstrip s-parameter simulation at 5GHz</textarea>
      <div class="row">
        <button onclick="planWorkflow()">Plan</button>
        <button onclick="validateWorkflow()">Validate</button>
      </div>
      <div class="muted">Repair Attempts are shown in the status panel. LLM mode still returns workflow JSON only.</div>
    </section>
    <section class="panel stack">
      <h2>Templates</h2>
      <select id="template"></select>
      <div class="row"><button onclick="loadTemplate()">Load Template</button><button onclick="runFakeDemo()">Run Fake Demo</button></div>
    </section>
    <section class="panel">
      <h2>Workflow Preview</h2>
      <pre id="preview">{}</pre>
    </section>
  </main>
  <aside class="stack">
    <strong>Status / Artifacts</strong>
    <pre id="status">{}</pre>
  </aside>
</div>
<script>
let currentWorkflow = null;
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'content-type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(data));
  return data;
}
function show(id, data) { document.getElementById(id).textContent = JSON.stringify(data, null, 2); }
async function loadTemplates() {
  const data = await api('/api/templates');
  const select = document.getElementById('template');
  select.innerHTML = data.templates.map(t => `<option value="${t.template_id}">${t.name}</option>`).join('');
  show('status', data);
}
async function loadTemplate() {
  const id = document.getElementById('template').value || 'microstrip_sparameter';
  const data = await api('/api/templates/' + encodeURIComponent(id));
  currentWorkflow = data.workflow;
  show('preview', currentWorkflow);
}
async function loadNodes(){ show('status', await api('/api/nodes')); }
async function loadReports(){ show('status', await api('/api/reports')); }
async function planWorkflow(){
  const data = await api('/api/plan', {method:'POST', body:JSON.stringify({user_request:document.getElementById('request').value, planner_mode:document.getElementById('plannerMode').value})});
  currentWorkflow = data.generated_workflow;
  show('preview', currentWorkflow || data);
  show('status', data);
}
async function validateWorkflow(){ show('status', await api('/api/validate', {method:'POST', body:JSON.stringify({workflow:currentWorkflow})})); }
async function runFakeDemo(){
  const templateId = document.getElementById('template').value || 'microstrip_sparameter';
  show('status', await api('/api/run', {method:'POST', body:JSON.stringify({template_id:templateId})}));
}
loadTemplates().then(loadTemplate);
</script>
</body>
</html>
"""


def dispatch_demo_request(method: str, path: str, body: bytes, service: DemoService) -> tuple[int, dict[str, str], bytes]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    try:
        if method == "GET" and route == "/":
            return _html_response(render_demo_page())
        if method == "GET" and route == "/api/status":
            return _json_response(service.status())
        if method == "GET" and route == "/api/nodes":
            return _json_response(service.nodes())
        if method == "GET" and route == "/api/templates":
            return _json_response(service.templates())
        if method == "GET" and route.startswith("/api/templates/"):
            template_id = unquote(route.rsplit("/", 1)[-1])
            return _json_response(service.template(template_id))
        if method == "POST" and route == "/api/plan":
            return _json_response(service.plan(_json_body(body)))
        if method == "POST" and route == "/api/validate":
            return _json_response(service.validate(_json_body(body)))
        if method == "POST" and route == "/api/run":
            return _json_response(service.run(_json_body(body)))
        if method == "GET" and route == "/api/reports":
            return _json_response(service.reports())
        if method == "GET" and route.startswith("/reports/"):
            return _report_response(service.repo_root, route)
        return _json_response({"error": "not_found", "path": route}, status=404)
    except Exception as exc:
        return _json_response({"error": type(exc).__name__, "message": str(exc)}, status=400)


def run_demo_server(host: str, port: int, repo_root: Path, run_dir: Path) -> None:
    service = DemoService(repo_root, run_dir=run_dir)

    class DemoRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def _handle(self) -> None:
            length = int(self.headers.get("content-length", "0") or "0")
            status, headers, response = dispatch_demo_request(self.command, self.path, self.rfile.read(length), service)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: Any) -> None:
            return None

    ThreadingHTTPServer((host, port), DemoRequestHandler).serve_forever()


def _json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise TypeError("request body must be a JSON object")
    return data


def _json_response(data: dict[str, Any], *, status: int = 200) -> tuple[int, dict[str, str], bytes]:
    return status, {"content-type": "application/json; charset=utf-8"}, (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _html_response(html: str) -> tuple[int, dict[str, str], bytes]:
    return 200, {"content-type": "text/html; charset=utf-8"}, html.encode("utf-8")


def _report_response(repo_root: Path, route: str) -> tuple[int, dict[str, str], bytes]:
    report_name = unquote(route.removeprefix("/reports/"))
    if "/" in report_name or not report_name:
        return _json_response({"error": "invalid_report_path"}, status=404)
    report_path = (repo_root / "benchmarks/reports" / report_name).resolve()
    reports_dir = (repo_root / "benchmarks/reports").resolve()
    if report_path.parent != reports_dir or not report_path.exists():
        return _json_response({"error": "report_not_found", "path": report_name}, status=404)
    content_type = "application/json; charset=utf-8" if report_path.suffix == ".json" else "text/html; charset=utf-8"
    return 200, {"content-type": content_type}, report_path.read_bytes()
