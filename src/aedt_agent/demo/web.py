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
  <title>AEDT Agent 工作台</title>
  <style>
    :root{--bg:#f4f6f8;--panel:#fff;--line:#d8dee8;--text:#17202c;--muted:#667085;--blue:#1f5eff;--green:#047857;--amber:#a16207}
    *{box-sizing:border-box}body{margin:0;font-family:Arial,'Noto Sans SC',sans-serif;background:var(--bg);color:var(--text);letter-spacing:0}
    button,select,textarea,input{font:inherit}button{border:0;background:var(--blue);color:#fff;border-radius:6px;padding:9px 12px;cursor:pointer;font-weight:700}
    button.secondary{background:#eef2ff;color:#1d4ed8;border:1px solid #c7d2fe}select,textarea{border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--text)}
    textarea{width:100%;min-height:104px;padding:10px;resize:vertical}.shell{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}
    .sidebar{background:#111827;color:#e5e7eb;padding:22px 16px;display:flex;flex-direction:column;gap:18px}.brand{font-size:20px;font-weight:800}.brand span{display:block;color:#9ca3af;font-size:12px;font-weight:400;margin-top:5px}
    .nav{display:grid;gap:8px}.nav button,.nav a{display:block;text-align:left;text-decoration:none;color:#d1d5db;background:transparent;border:1px solid transparent;border-radius:6px;padding:9px 10px;font-weight:700}.nav button:hover,.nav a:hover{background:#1f2937;color:#fff}
    .content{padding:22px;display:grid;gap:16px}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.title h1{font-size:28px;margin:0 0 6px}.muted{color:var(--muted);line-height:1.5}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
    .metric,.panel,.report-card{background:var(--panel);border:1px solid var(--line);border-radius:8px}.metric{padding:14px}.metric strong{display:block;font-size:24px}.metric span{color:var(--muted);font-size:13px}
    .workspace{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(360px,.85fr);gap:16px}.panel{padding:16px}.panel h2{font-size:18px;margin:0 0 12px}.stack{display:grid;gap:12px}.row{display:flex;gap:10px;flex-wrap:wrap}.field{display:grid;gap:6px}.field label{font-size:13px;font-weight:700;color:#344054}
    .steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.step{border:1px solid var(--line);border-radius:8px;padding:11px;background:#fbfdff}.step b{display:block;margin-bottom:4px}.step small{color:var(--muted)}
    .preview{background:#111827;color:#e5e7eb;border-radius:8px;padding:12px;overflow:auto;min-height:300px;max-height:520px;font-size:12px;line-height:1.45}.result{background:#f8fafc;border:1px solid var(--line);border-radius:8px;padding:12px;min-height:190px;max-height:520px;overflow:auto;white-space:pre-wrap;font-size:12px}
    .reports{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.report-card{padding:14px;text-decoration:none;color:var(--text)}.report-card b{display:block;margin-bottom:6px;color:#1d4ed8}.report-card span{color:var(--muted);font-size:13px;line-height:1.45}
    .status-pill{display:inline-flex;align-items:center;border-radius:999px;background:#ecfdf3;color:var(--green);padding:5px 10px;font-size:13px;font-weight:700;white-space:nowrap}
    @media(max-width:1040px){.shell{grid-template-columns:1fr}.sidebar{position:static}.workspace,.metrics,.reports{grid-template-columns:1fr}.topbar{display:grid}}
  </style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">AEDT Agent<span>Stage C.2 workflow workstation</span></div>
    <div class="nav">
      <button onclick="loadTemplates()">Templates</button>
      <button onclick="loadNodes()">Node Catalog</button>
      <button onclick="loadReports()">Reports API</button>
      <a href="/reports/stage_c_real_smoke_dashboard.html" target="_blank">真实 AEDT Smoke</a>
      <a href="/reports/stage_c_node_evolution_review.html" target="_blank">节点进化 Review</a>
      <a href="/reports/stage_c2_planner_benchmark.html" target="_blank">Planner Benchmark</a>
    </div>
  </aside>
  <main class="content">
    <div class="topbar">
      <div class="title">
        <h1>AEDT Agent 工作台</h1>
        <div class="muted">把自然语言请求转换为受控 workflow，先校验，再执行 fake adapter；真实 AEDT 结果通过报告入口展示。</div>
      </div>
      <div class="status-pill">Browser AEDT execution: disabled</div>
    </div>
    <section class="metrics">
      <div class="metric"><strong>3</strong><span>workflow templates</span></div>
      <div class="metric"><strong>8+</strong><span>controlled nodes</span></div>
      <div class="metric"><strong>3/3</strong><span>real AEDT smoke</span></div>
      <div class="metric"><strong>JSON</strong><span>LLM output boundary</span></div>
    </section>
    <section class="workspace">
      <div class="stack">
        <section class="panel stack">
          <h2>任务规划</h2>
          <div class="steps">
            <div class="step"><b>1. Plan</b><small>生成 workflow JSON</small></div>
            <div class="step"><b>2. Validate</b><small>拦截错误引用和缺参</small></div>
            <div class="step"><b>3. Run Fake Demo</b><small>执行受控节点链路</small></div>
          </div>
          <div class="field">
            <label for="plannerMode">Planner Mode</label>
            <select id="plannerMode">
              <option value="deterministic">deterministic</option>
              <option value="llm">llm</option>
            </select>
          </div>
          <div class="field">
            <label for="request">User Request</label>
            <textarea id="request">create a microstrip s-parameter simulation at 5GHz</textarea>
          </div>
          <div class="row">
            <button onclick="planWorkflow()">Plan Workflow</button>
            <button class="secondary" onclick="validateWorkflow()">Validate</button>
          </div>
          <div class="muted">Repair Attempts 会显示在右侧结果中。LLM mode 仍只允许返回 workflow JSON，不允许执行 PyAEDT Python。</div>
        </section>
        <section class="panel stack">
          <h2>模板与执行</h2>
          <div class="field">
            <label for="template">Workflow Template</label>
            <select id="template"></select>
          </div>
          <div class="row">
            <button class="secondary" onclick="loadTemplate()">Load Template</button>
            <button onclick="runFakeDemo()">Run Fake Demo</button>
          </div>
        </section>
        <section class="panel">
          <h2>Workflow 预览</h2>
          <pre class="preview" id="preview">{}</pre>
        </section>
      </div>
      <aside class="panel stack">
        <h2>结果摘要</h2>
        <div class="muted">显示 planner attempts、repair_count、validation 和 artifact 链接。</div>
        <pre class="result" id="status">{}</pre>
      </aside>
    </section>
    <section class="reports">
      <a class="report-card" href="/reports/stage_c_real_smoke_dashboard.html" target="_blank"><b>真实 AEDT Smoke</b><span>3 个真实 AEDT workflow 的模型事实 validation 汇总。</span></a>
      <a class="report-card" href="/reports/stage_c_node_evolution_review.html" target="_blank"><b>节点进化 Review</b><span>从 benchmark/audit 证据生成 proposal，并保持人工审核 gate。</span></a>
      <a class="report-card" href="/reports/stage_c2_planner_benchmark.html" target="_blank"><b>Planner Benchmark</b><span>5 条自然语言任务的规划成功率和 repair attempts。</span></a>
    </section>
  </main>
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
