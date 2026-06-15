"""Agent main window — a web dashboard for mission management and DAG visualization."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from aedt_agent.agent.graph_runner import advance_graph, graph_status, resume_graph, run_graph
from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path
from aedt_agent.agent.graph_visualizer import render_graph_mermaid
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import (
    BRD_CHANNEL_SCORE_CAPABILITY,
    BRD_EVIDENCE_COMPARE_CAPABILITY,
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    InMemoryWorkerRegistry,
    run_brd_channel_score_worker,
    run_brd_local_cut_worker,
    run_evidence_compare_worker,
)
from aedt_agent.infrastructure import SQLiteMissionStore


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

AGENT_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ansys-agent · 主窗口</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
:root{--bg:#0f1117;--panel:#161822;--line:#24273a;--text:#cdd6f4;--muted:#6c7086;--blue:#89b4fa;--green:#a6e3a1;--red:#f38ba8;--yellow:#f9e2af;--magenta:#cba6f7;--accent:#89b4fa}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 Inter,system-ui,sans-serif;background:var(--bg);color:var(--text)}
.shell{display:grid;grid-template-columns:280px 1fr;height:100vh}
.sidebar{background:var(--panel);border-right:1px solid var(--line);padding:16px;display:flex;flex-direction:column;gap:10px;overflow-y:auto}
.sidebar h1{font-size:20px;margin:0;color:var(--accent)}.sidebar .muted{font-size:12px;color:var(--muted)}
.main{display:grid;grid-template-rows:auto 1fr auto;overflow:hidden}
.toolbar{display:flex;gap:8px;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--panel);flex-wrap:wrap;align-items:center}
.toolbar .sep{width:1px;height:24px;background:var(--line);margin:0 4px}
.content{display:grid;grid-template-columns:1fr 360px;overflow:hidden}
.graph-panel{padding:16px;overflow:auto;background:var(--bg)}
.graph-panel h2{font-size:16px;margin:0 0 10px}.graph-panel .mermaid{background:#181a25;border-radius:8px;padding:16px}
.log-panel{background:var(--panel);border-left:1px solid var(--line);padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.log-panel h2{font-size:16px;margin:0}.log-panel .muted{font-size:12px}
.log-entry{padding:8px 10px;border-radius:6px;font-size:12px;line-height:1.4;background:#1e2030;border-left:3px solid var(--line)}
.log-entry.info{border-left-color:var(--blue)}.log-entry.ok{border-left-color:var(--green)}
.log-entry.err{border-left-color:var(--red)}.log-entry.warn{border-left-color:var(--yellow)}
.status-bar{display:flex;gap:10px;padding:8px 16px;border-top:1px solid var(--line);background:var(--panel);font-size:12px;align-items:center}
button,input,select{font:inherit;border-radius:6px;padding:8px 12px}
button{border:0;background:var(--accent);color:#1e2030;cursor:pointer;font-weight:700;white-space:nowrap}
button:hover{filter:brightness(1.1)}button:disabled{opacity:.4;cursor:not-allowed}
button.secondary{background:transparent;border:1px solid var(--line);color:var(--text)}
button.approve{background:var(--green);color:#1e2030}button.reject{background:var(--red);color:#fff}
input,select{background:#1e2030;border:1px solid var(--line);color:var(--text);width:100%}
input:focus,select:focus{outline:none;border-color:var(--accent)}
.field{display:grid;gap:4px}.field label{font-size:12px;color:var(--muted);font-weight:700}
.mission-list{display:grid;gap:4px}.mission-item{padding:8px 10px;border-radius:6px;cursor:pointer;border:1px solid transparent;font-size:13px}
.mission-item:hover{border-color:var(--line)}.mission-item.active{border-color:var(--accent);background:#1e2030}
.mission-item .id{color:var(--muted);font-size:11px}.mission-item .state{font-weight:700;font-size:11px}
.badge{display:inline-block;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700}
.badge.ok{background:#1e3a1e;color:var(--green)}.badge.err{background:#3a1e1e;color:var(--red)}
.badge.wait{background:#3a3510;color:var(--yellow)}.badge.run{background:#1e2a3a;color:var(--blue)}
.templates-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-top:8px}
.template-card{padding:10px;border:1px solid var(--line);border-radius:6px;cursor:pointer;font-size:12px}
.template-card:hover{border-color:var(--accent)}.template-card b{display:block;color:var(--accent)}
.quick-actions{display:grid;gap:6px}
</style>
</head>
<body>
<div class="shell">
<aside class="sidebar">
  <h1>⚡ ansys-agent</h1>
  <div class="muted">Agent-First 电磁仿真工作台</div>
  <div style="display:grid;gap:6px">
    <button onclick="refreshMissions()">🔄 刷新 Missions</button>
    <button class="secondary" onclick="showCreate()">＋ 新建 Mission</button>
  </div>
  <div class="field">
    <label>Missions</label>
    <div class="mission-list" id="missionList"><div class="muted">加载中…</div></div>
  </div>
  <div class="quick-actions" id="quickActions" style="display:none">
    <button onclick="stepGraph()" id="btnStep">▶ 推进 Graph</button>
    <button class="secondary" onclick="refreshGraph()">🔄 刷新状态</button>
    <div id="approvalActions" style="display:none;display:grid;grid-template-columns:1fr 1fr;gap:4px">
      <button class="approve" onclick="approveCurrent()">✓ 批准</button>
      <button class="reject" onclick="rejectCurrent()">✕ 拒绝</button>
    </div>
  </div>
  <div class="field" style="margin-top:auto">
    <label>Auto-refresh</label>
    <select id="autoRefresh" onchange="toggleAutoRefresh()">
      <option value="0">关闭</option><option value="3">3s</option><option value="5" selected>5s</option><option value="10">10s</option>
    </select>
  </div>
</aside>
<main class="main">
  <div class="toolbar">
    <span id="currentMission" style="font-weight:700;color:var(--accent)">选择一个 Mission</span>
    <span class="sep"></span>
    <span id="graphStatus" class="badge" style="display:none">--</span>
    <span class="sep"></span>
    <span style="font-size:12px;color:var(--muted)">Step <b id="stepCount">0</b></span>
  </div>
  <div class="content">
    <div class="graph-panel">
      <div id="createPanel" style="display:none">
        <h2>新建 Mission</h2>
        <div style="display:grid;gap:8px;max-width:600px">
          <div class="field"><label>Goal</label><input id="newGoal" value="BRD channel model review"></div>
          <div class="field"><label>Template</label><select id="newTemplate"></select></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div class="field"><label>Layout File</label><input id="newLayout" placeholder="/path/to/board.brd"></div>
            <div class="field"><label>Signal Nets (comma)</label><input id="newNets" value="CLK0,CLK1"></div>
            <div class="field"><label>BBox</label><input id="newBbox" value="0,0,10,10"></div>
            <div class="field"><label>Artifact Dir</label><input id="newArtifactDir" placeholder="auto"></div>
          </div>
          <div style="display:flex;gap:8px">
            <button onclick="createMission()">Create Mission</button>
            <button class="secondary" onclick="document.getElementById('createPanel').style.display='none'">Cancel</button>
          </div>
        </div>
      </div>
      <div id="graphView">
        <h2>DAG 状态</h2>
        <div class="mermaid" id="mermaidGraph">选择 Mission 后显示</div>
      </div>
    </div>
    <div class="log-panel">
      <h2>Events</h2>
      <div id="eventLog"><div class="muted">选择 Mission 后显示事件</div></div>
    </div>
  </div>
  <div class="status-bar">
    <span id="connStatus">● 就绪</span>
    <span style="color:var(--muted);margin-left:auto">ansys-agent · agent-first runtime</span>
  </div>
</main>
</div>
<script>
mermaid.initialize({startOnLoad:true,theme:'dark',securityLevel:'loose'});
let activeMission=null,activeGraphRun=null,refreshTimer=null;

async function api(p,o={}){
  const r=await fetch(p,{headers:{'content-type':'application/json'},...o});
  if(!r.ok){const t=await r.text();throw new Error(t)}
  return r.json();
}

function badge(status){
  const m={succeeded:'ok',failed:'err',waiting_approval:'wait',running:'run',canceled:'err'};
  return `<span class="badge ${m[status]||''}">${status}</span>`;
}

async function refreshMissions(){
  const data=await api('/api/missions');
  const el=document.getElementById('missionList');
  el.innerHTML=data.missions.map(m=>`<div class="mission-item${activeMission===m.mission_id?' active':''}" onclick="selectMission('${m.mission_id}')"><b>${m.goal||'Untitled'}</b><div class="id">${m.mission_id.slice(0,12)}…</div><span class="state">${m.state}</span></div>`).join('')||'<div class="muted">暂无 Mission</div>';
}

async function selectMission(id){
  activeMission=id;activeGraphRun=null;
  document.getElementById('createPanel').style.display='none';
  document.getElementById('quickActions').style.display='grid';
  document.getElementById('currentMission').textContent='Mission: '+id.slice(0,12)+'…';
  await refreshMissions();
  await refreshGraph();
}

async function refreshGraph(){
  if(!activeMission)return;
  try{
    const data=await api('/api/missions/'+activeMission);
    document.getElementById('graphStatus').style.display='inline-block';
    document.getElementById('graphStatus').textContent=data.mission.state;
    document.getElementById('graphStatus').className='badge '+(data.mission.state==='completed'?'ok':data.mission.state==='failed'?'err':data.mission.state==='waiting_approval'?'wait':'run');
    if(data.graph_run){
      activeGraphRun=data.graph_run.graph_run_id;
      document.getElementById('stepCount').textContent=data.graph_run.step_count||0;
      const viz=await api('/api/graph-runs/'+activeGraphRun+'/mermaid');
      const el=document.getElementById('mermaidGraph');
      el.innerHTML=viz.mermaid;el.removeAttribute('data-processed');
      await mermaid.run({nodes:[el]});
      // Show approval actions
      const w=data.graph_run.status==='waiting_approval';
      document.getElementById('approvalActions').style.display=w?'grid':'none';
      document.getElementById('btnStep').disabled=w;
    }
    if(data.events){
      const log=document.getElementById('eventLog');
      log.innerHTML=data.events.slice(-30).map(e=>`<div class="log-entry ${e.event_type.includes('fail')||e.event_type.includes('error')?'err':e.event_type.includes('succe')||e.event_type.includes('complet')?'ok':'info'}"><b>${e.event_type}</b><br><span style="color:var(--muted)">${JSON.stringify(e.payload||{}).slice(0,120)}</span></div>`).join('')||'<div class="muted">暂无事件</div>';
      log.scrollTop=log.scrollHeight;
    }
  }catch(e){console.error(e)}
}

async function stepGraph(){
  if(!activeGraphRun){
    // First step: create graph run
    const data=await api('/api/missions/'+activeMission+'/create-graph-run',{method:'POST',body:JSON.stringify({template_id:document.getElementById('newTemplate').value||'brd_local_cut_build'})});
    activeGraphRun=data.graph_run_id;
  }
  const data=await api('/api/graph-runs/'+activeGraphRun+'/advance',{method:'POST'});
  await refreshGraph();
}

async function approveCurrent(){
  if(!activeMission)return;
  const data=await api('/api/missions/'+activeMission+'/approvals');
  const pending=data.approvals.filter(a=>a.decision==='pending');
  if(!pending.length)return alert('没有待审批项');
  await api('/api/approvals/'+pending[pending.length-1].approval_id+'/decide',{method:'POST',body:JSON.stringify({decision:'approved'})});
  await stepGraph();
}

async function rejectCurrent(){
  if(!activeMission)return;
  const data=await api('/api/missions/'+activeMission+'/approvals');
  const pending=data.approvals.filter(a=>a.decision==='pending');
  if(!pending.length)return alert('没有待审批项');
  await api('/api/approvals/'+pending[pending.length-1].approval_id+'/decide',{method:'POST',body:JSON.stringify({decision:'rejected'})});
  await stepGraph();
}

async function showCreate(){
  document.getElementById('createPanel').style.display='block';
  const data=await api('/api/templates');
  document.getElementById('newTemplate').innerHTML=data.templates.map(t=>`<option value="${t.id}">${t.id}</option>`).join('');
}

async function createMission(){
  const goal=document.getElementById('newGoal').value;
  const templateId=document.getElementById('newTemplate').value||'brd_local_cut_build';
  const nets=document.getElementById('newNets').value.split(',').map(s=>s.trim()).filter(Boolean);
  const bbox=document.getElementById('newBbox').value;
  const layout=document.getElementById('newLayout').value;
  const data=await api('/api/missions',{method:'POST',body:JSON.stringify({goal,template_id:templateId,signal_nets:nets,bbox,layout_file:layout})});
  document.getElementById('createPanel').style.display='none';
  await refreshMissions();
  await selectMission(data.mission_id);
}

function toggleAutoRefresh(){
  const v=parseInt(document.getElementById('autoRefresh').value);
  if(refreshTimer)clearInterval(refreshTimer);
  if(v>0)refreshTimer=setInterval(refreshGraph,v*1000);
}
refreshMissions();
toggleAutoRefresh();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Request dispatcher
# ---------------------------------------------------------------------------


def dispatch_agent_request(
    method: str,
    path: str,
    body: bytes,
    runtime: AgentRuntime,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    try:
        if method == "GET" and route == "/":
            return _html(AGENT_PAGE)

        # --- Missions ---
        if method == "GET" and route == "/api/missions":
            missions = [
                {
                    "mission_id": m.mission_id,
                    "goal": m.user_goal,
                    "state": m.state.value,
                    "created_at": m.created_at,
                }
                for m in runtime.store.list_missions()
            ]
            return _json({"missions": missions})

        if method == "POST" and route == "/api/missions":
            from aedt_agent.agent.graph_runner import create_graph_run

            req = _json_body(body)
            goal = str(req.get("goal", "Untitled"))
            template_id = str(req.get("template_id", "brd_local_cut_build"))
            mission = runtime.create_mission(goal, [], [])

            try:
                template = load_graph_template(resolve_template_path(template_id))
            except FileNotFoundError:
                template = load_graph_template(resolve_template_path("brd_local_cut_build"))

            signal_nets = req.get("signal_nets", [])
            graph_run = create_graph_run(
                runtime,
                mission.mission_id,
                template,
                initial_payload={
                    "layout_file": str(req.get("layout_file", "")),
                    "signal_nets": signal_nets if isinstance(signal_nets, list) else [],
                    "reference_nets": ["GND"],
                    "local_cut_region": _parse_bbox(str(req.get("bbox", "0,0,10,10"))),
                    "artifact_dir": str(req.get("artifact_dir", "")),
                    "target_metrics": [],
                    "adapter_mode": "deterministic",
                },
            )
            return _json({
                "mission_id": mission.mission_id,
                "graph_run_id": graph_run.graph_run_id,
                "status": graph_run.status.value,
            }, status=201)

        if method == "GET" and route.startswith("/api/missions/"):
            mission_id = route.rsplit("/", 1)[-1]
            mission = runtime.get_mission(mission_id)
            graph_runs = runtime.store.list_graph_runs(mission_id)
            graph_run = graph_runs[-1].to_json_dict() if graph_runs else None
            events = [
                {"event_type": e.event_type.value, "payload": e.payload}
                for e in runtime.list_events(mission_id)
            ]
            return _json({
                "mission": mission.to_json_dict(),
                "graph_run": graph_run,
                "events": events,
            })

        if method == "GET" and "/approvals" in route:
            parts = route.rstrip("/").split("/")
            mission_id = parts[3]  # /api/missions/{id}/approvals
            approvals = runtime.store.list_approvals(mission_id)
            return _json({"approvals": [a.to_json_dict() for a in approvals]})

        # --- Graph runs ---
        if method == "POST" and route.endswith("/create-graph-run"):
            from aedt_agent.agent.graph_runner import create_graph_run

            mission_id = route.rsplit("/", 2)[0].rsplit("/", 1)[-1]
            req = _json_body(body)
            template_id = str(req.get("template_id", "brd_local_cut_build"))
            try:
                template = load_graph_template(resolve_template_path(template_id))
            except FileNotFoundError:
                template = load_graph_template(resolve_template_path("brd_local_cut_build"))
            graph_run = create_graph_run(runtime, mission_id, template)
            return _json({"graph_run_id": graph_run.graph_run_id, "status": graph_run.status.value}, status=201)

        if method == "GET" and "/mermaid" in route:
            graph_run_id = route.rsplit("/", 2)[0].rsplit("/", 1)[-1]
            status = graph_status(runtime, graph_run_id)
            mermaid = render_graph_mermaid(
                status.get("graph_run", {}).get("template_snapshot", {}),
                status.get("node_runs", []),
                status.get("handoffs", []),
            )
            return _json({"mermaid": "\n".join(["```mermaid", mermaid, "```"]), "status": status["status"]})

        if method == "POST" and route.endswith("/advance"):
            graph_run_id = route.rsplit("/", 2)[0].rsplit("/", 1)[-1]
            report = advance_graph(runtime, graph_run_id)
            return _json({"status": report["status"], "node_runs": report.get("node_runs", [])})

        # --- Approvals ---
        if method == "POST" and "/decide" in route:
            from aedt_agent.agent.mission import ApprovalDecision

            approval_id = route.rsplit("/", 2)[0].rsplit("/", 1)[-1]
            req = _json_body(body)
            decision = ApprovalDecision.APPROVED if str(req.get("decision")) == "approved" else ApprovalDecision.REJECTED
            runtime.store.resolve_approval(approval_id, decision, None, None)
            return _json({"approval_id": approval_id, "decision": decision.value})

        # --- Templates ---
        if method == "GET" and route == "/api/templates":
            templates_dir = Path(__file__).resolve().parents[3] / "docs" / "agent_templates"
            yaml_files = sorted(templates_dir.glob("*.yaml")) if templates_dir.exists() else []
            return _json({
                "templates": [
                    {"id": f.stem, "path": str(f)}
                    for f in yaml_files
                ]
            })

        return _json({"error": "not_found", "path": route}, status=404)
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)}, status=400)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def run_agent_window(
    host: str = "127.0.0.1",
    port: int = 8766,
    db_path: str | Path = ".aedt-agent/missions.db",
) -> None:
    """Start the agent main window web server."""
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    registry = InMemoryWorkerRegistry()
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    registry.register(BRD_CHANNEL_SCORE_CAPABILITY, run_brd_channel_score_worker)
    registry.register(BRD_EVIDENCE_COMPARE_CAPABILITY, run_evidence_compare_worker)
    runtime = AgentRuntime(SQLiteMissionStore(db), registry=registry)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle()
        def do_POST(self):
            self._handle()
        def _handle(self):
            length = int(self.headers.get("content-length", "0") or "0")
            status, headers, body = dispatch_agent_request(
                self.command, self.path, self.rfile.read(length), runtime,
            )
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    print(f"⚡ ansys-agent window → http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json(data, *, status=200):
    return status, {"content-type": "application/json; charset=utf-8"}, (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _html(html_str):
    return 200, {"content-type": "text/html; charset=utf-8"}, html_str.encode("utf-8")


def _json_body(body):
    if not body:
        return {}
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise TypeError("body must be a JSON object")
    return data


def _parse_bbox(value: str) -> dict[str, Any]:
    parts = [float(x.strip()) for x in value.split(",") if x.strip()]
    if len(parts) != 4:
        return {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
    return {"x1": parts[0], "y1": parts[1], "x2": parts[2], "y2": parts[3]}
