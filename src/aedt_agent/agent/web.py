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
    BRD_GEOMETRY_VALIDATE_CAPABILITY,
    BRD_ITERATION_QUALIFY_CAPABILITY,
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    BRD_TDR_EXPORT_CAPABILITY,
    BRD_TOUCHSTONE_EXPORT_CAPABILITY,
    InMemoryWorkerRegistry,
    run_brd_channel_score_worker,
    run_brd_geometry_validate_worker,
    run_brd_iteration_qualify_worker,
    run_brd_local_cut_worker,
    run_brd_tdr_export_worker,
    run_brd_touchstone_export_worker,
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
  <div id="llmStatus" class="muted" style="font-size:11px;color:var(--yellow)">LLM: checking...</div>
  <div style="display:grid;gap:6px">
    <button onclick="refreshMissions()">🔄 刷新 Missions</button>
    <button class="secondary" onclick="showCreate()">＋ 新建 Mission</button>
    <button class="secondary" onclick="monitorAll()" id="btnMonitorAll">📡 Monitor All</button>
    <button class="secondary" onclick="toggleLlmConfig()">⚙️ LLM 配置</button>
  </div>
  <div id="llmConfig" style="display:none;gap:6px;padding:8px;border:1px solid var(--line);border-radius:6px;background:#1a1c28">
    <div class="field"><label>Model</label><input id="llmModel" value="gpt-4.1-mini" placeholder="gpt-4.1-mini"></div>
    <div class="field"><label>API Key</label><input id="llmKey" type="password" placeholder="sk-..."></div>
    <div class="field"><label>Base URL</label><input id="llmUrl" placeholder="https://api.openai.com/v1"></div>
    <button onclick="saveLlmConfig()">💾 保存</button>
    <div id="llmConfigStatus" class="muted" style="font-size:11px"></div>
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
  <div style="display:grid;gap:4px;margin-top:8px;padding:8px;border:1px solid var(--line);border-radius:6px;background:#1a1c28">
    <div class="muted" style="font-size:11px">🤖 输入需求，编排者自动选模板并执行</div>
    <input id="orchestrateGoal" placeholder="e.g. Optimize CLK0/CLK1 channel, RL <-20dB @28GHz" style="font-size:12px">
    <button onclick="orchestrateGoal()" style="font-size:12px;padding:8px">🤖 Go</button>
    <div id="orchestrateLiveLog" style="display:none;max-height:100px;overflow:auto;font-size:10px;background:#111;padding:6px;border-radius:4px;line-height:1.4"></div>
  </div>
  <div class="field" style="margin-top:auto">
    <label>Auto-refresh</label>
    <select id="autoRefresh" onchange="toggleAutoRefresh()">
      <option value="0">关闭</option><option value="10">10s</option><option value="30" selected>30s</option><option value="60">60s</option>
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
            <button class="secondary" onclick="orchestrateMission()" id="btnOrchestrate" style="background:var(--accent);color:#1e2030">🤖 Auto Orchestrate</button>
            <button class="secondary" onclick="document.getElementById('createPanel').style.display='none'">Cancel</button>
          </div>
          <div id="orchestrateLog" style="display:none;margin-top:8px;max-height:160px;overflow:auto;font-size:11px;background:#111;padding:8px;border-radius:6px;line-height:1.5"></div>
        </div>
      </div>
      <div id="graphView">
        <h2>DAG 状态</h2>
        <div class="mermaid" id="mermaidGraph">选择 Mission 后显示</div>
        <div id="optimizationProgress" style="margin-top:12px"></div>
        <details style="margin-top:12px">
          <summary style="cursor:pointer;color:var(--muted);font-size:12px">📋 Orchestrator CLI 参考 (Claude Code / Codex)</summary>
          <pre style="font-size:11px;line-height:1.6;background:#111;padding:10px;border-radius:6px;overflow:auto;max-height:200px"># 创建 mission + graph_run
python -m aedt_agent.agent mission create --goal "..." --brd-local-cut-model-review ...

# 推进 graph（Orchestrator 轮询调用）
python -m aedt_agent.agent mission advance-graph --graph-run-id &lt;id&gt;

# 查看状态
python -m aedt_agent.agent mission graph-status --graph-run-id &lt;id&gt;

# 可视化
python -m aedt_agent.agent mission graph-visualize --graph-run-id &lt;id&gt;

# 审批
python -m aedt_agent.agent mission approve --approval-id &lt;id&gt; --option-id approve

# 接管
python -m aedt_agent.agent mission takeover --graph-run-id &lt;id&gt; --reason "..."</pre>
        </details>
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
    try{
      const progress=await api('/api/missions/'+activeMission+'/optimization-progress');
      const panel=document.getElementById('optimizationProgress');
      if(progress.optimization_history_csv){
        const rows=(progress.history_rows||[]).slice(-6);
        panel.innerHTML='<h2>优化历史</h2><div class="muted" style="font-size:11px;margin-bottom:6px">'+progress.optimization_history_csv+'</div>'+
          '<table style="width:100%;border-collapse:collapse;background:#181a25;border:1px solid var(--line);font-size:12px">'+
          '<tr><th>Round</th><th>Status</th><th>Action</th><th>RL</th><th>TDR</th><th>Next</th></tr>'+
          rows.map(r=>'<tr><td>'+esc(r.round_index)+'</td><td>'+esc(r.round_status)+'</td><td>'+esc(r.action_type||'')+'</td><td>'+esc(r.rl_worst_db||'')+'</td><td>'+esc(r.tdr_peak_deviation_ohm||'')+'</td><td>'+esc(r.continue_recommendation||'')+'</td></tr>').join('')+
          '</table>';
      }else{panel.innerHTML=''}
    }catch(e){}
  }catch(e){console.error(e)}
}

function esc(v){
  return String(v==null?'':v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
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

async function monitorAll(){
  // Show all missions with their graph runs in a compact view
  const missions=await api('/api/missions');
  const el=document.getElementById('mermaidGraph');
  let html='<h2>📡 All Active Graphs</h2><div style="display:grid;gap:10px">';
  let hasCards=false;
  for(const m of missions.missions){
    try{
      const detail=await api('/api/missions/'+m.mission_id);
      const gr=detail.graph_run;
      if(!gr)continue;
      hasCards=true;
      const st=gr.status||'unknown';
      const cls={succeeded:'ok',failed:'err',waiting_approval:'wait',running:'run',canceled:'err'}[st]||'';
      html+='<div style="border:1px solid var(--line);border-radius:8px;padding:12px;background:#1e2030">';
      html+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">';
      html+='<b style="cursor:pointer;color:var(--accent)" onclick="selectMission(\''+m.mission_id+'\')">'+m.goal+'</b>';
      html+='<span class="badge '+cls+'">'+st+'</span></div>';
      html+='<div style="font-size:11px;color:var(--muted)">Step '+gr.step_count+' | '+m.mission_id.slice(0,16)+'…</div>';
      html+='</div>';
    }catch(e){console.error('monitorAll fetch failed for '+m.mission_id,e)}
  }
  html+='</div>';
  if(!hasCards)html+='<div class="muted">暂无活跃 Graph。用 CLI 创建: python -m aedt_agent.agent mission create ...</div>';
  el.innerHTML=html;
}

refreshMissions();
toggleAutoRefresh();
(async function checkLlm(){
  try{
    const data=await api('/api/llm-status');
    document.getElementById('llmStatus').textContent='LLM: '+data.model+(data.configured?' ✅':' ⚠️');
    document.getElementById('llmStatus').style.color=data.configured?'var(--green)':'var(--yellow)';
    // Fill config fields
    document.getElementById('llmModel').value=data.model||'gpt-4.1-mini';
    document.getElementById('llmUrl').value=data.base_url||'';
    document.getElementById('llmConfigStatus').textContent=data.configured?'已配置':'未配置 API Key';
  }catch(e){
    document.getElementById('llmStatus').textContent='LLM: unknown';
  }
})();

function toggleLlmConfig(){
  const el=document.getElementById('llmConfig');
  el.style.display=el.style.display==='none'?'grid':'none';
}

async function orchestrateMission(){
  const goal=document.getElementById('newGoal').value;
  const templateId=document.getElementById('newTemplate').value||'brd_local_cut_build';
  const nets=document.getElementById('newNets').value.split(',').map(s=>s.trim()).filter(Boolean);
  const btn=document.getElementById('btnOrchestrate');
  btn.disabled=true;btn.textContent='⏳ Running…';
  const log=document.getElementById('orchestrateLog');
  log.style.display='block';log.innerHTML='<div style="color:var(--accent)">Starting orchestrator…</div>';

  const data=await api('/api/orchestrate',{method:'POST',body:JSON.stringify({
    goal,template_id:templateId,signal_nets:nets,
    bbox:document.getElementById('newBbox').value,
    layout_file:document.getElementById('newLayout').value,
    adapter_mode:'deterministic',
  })});

  const sid=data.session_id;
  let lastLogLen=0;
  const poll=setInterval(async()=>{
    try{
      const s=await api('/api/orchestrate-status/'+sid);
      // Append new log entries
      for(let i=lastLogLen;i<s.log.length;i++){
        const e=s.log[i];
        const cls={ok:'ok',err:'err',warn:'warn'}[e.type]||'info';
        log.innerHTML+=`<div class="log-entry ${cls}">${e.msg}</div>`;
        log.scrollTop=log.scrollHeight;
      }
      lastLogLen=s.log.length;
      if(!s.running){
        clearInterval(poll);
        btn.disabled=false;btn.textContent='🤖 Auto Orchestrate';
        log.innerHTML+='<div style="color:var(--green)">Done.</div>';
        if(s.mission_id)await refreshMissions();
      }
      if(s.mission_id)activeMission=s.mission_id;
      if(s.graph_run_id)activeGraphRun=s.graph_run_id;
      document.getElementById('currentMission').textContent='Orchestrating: '+((s.mission_id||'').slice(0,12))+'…';
      document.getElementById('stepCount').textContent='?';
    }catch(e){clearInterval(poll);btn.disabled=false;btn.textContent='🤖 Auto Orchestrate'}
  },1000);
}

async function orchestrateGoal(){
  const goal=document.getElementById('orchestrateGoal').value.trim();
  if(!goal)return alert('Please enter a goal');
  const log=document.getElementById('orchestrateLiveLog');
  log.style.display='block';log.innerHTML='<span style="color:var(--accent)">⏳ Orchestrator selecting template…</span>';

  const data=await api('/api/orchestrate',{method:'POST',body:JSON.stringify({goal,adapter_mode:'deterministic'})});
  const sid=data.session_id;
  let lastLen=0;
  const poll=setInterval(async()=>{
    try{
      const s=await api('/api/orchestrate-status/'+sid);
      for(let i=lastLen;i<s.log.length;i++){
        const e=s.log[i];
        log.innerHTML+=`<div style="color:${e.type==='err'?'var(--red)':e.type==='ok'?'var(--green)':e.type==='warn'?'var(--yellow)':'var(--muted)'}">${e.msg}</div>`;
        log.scrollTop=log.scrollHeight;
      }
      lastLen=s.log.length;
      if(!s.running){clearInterval(poll);refreshMissions()}
      if(s.mission_id){activeMission=s.mission_id;refreshGraph()}
    }catch(e){clearInterval(poll)}
  },1000);
}

async function saveLlmConfig(){
  const config={
    model:document.getElementById('llmModel').value,
    api_key:document.getElementById('llmKey').value,
    base_url:document.getElementById('llmUrl').value,
  };
  await api('/api/llm-config',{method:'POST',body:JSON.stringify(config)});
  document.getElementById('llmConfigStatus').textContent='已保存 ✅';
  // Refresh LLM status
  const data=await api('/api/llm-status');
  document.getElementById('llmStatus').textContent='LLM: '+data.model+(data.configured?' ✅':' ⚠️');
  document.getElementById('llmStatus').style.color=data.configured?'var(--green)':'var(--yellow)';
}
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
                    "_goal": goal,
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

        if method == "GET" and "/approvals" in route:
            parts = route.rstrip("/").split("/")
            mission_id = parts[3]  # /api/missions/{id}/approvals
            approvals = runtime.store.list_approvals(mission_id)
            return _json({"approvals": [a.to_json_dict() for a in approvals]})

        if method == "GET" and route.endswith("/optimization-progress"):
            parts = route.rstrip("/").split("/")
            mission_id = parts[3]  # /api/missions/{id}/optimization-progress
            return _json(_optimization_progress(runtime, mission_id))

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
            from aedt_agent.agent.approvals import ApprovalService

            approval_id = route.rstrip("/").split("/")[-2]
            req = _json_body(body)
            service = ApprovalService(runtime.store)
            decision = str(req.get("decision") or "")
            if decision in {"approved", "approve"}:
                approval = service.approve(
                    approval_id,
                    str(req.get("option_id") or "approve"),
                    req.get("comment"),
                )
            else:
                approval = service.reject(
                    approval_id,
                    req.get("comment"),
                )
            return _json({"approval_id": approval_id, "decision": approval.decision.value})

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

        if method == "GET" and route == "/api/llm-status":
            from aedt_agent.agent.llm import LlmConfig
            config = LlmConfig.from_env()
            # Also check web-saved config
            config = _merge_web_llm_config(config)
            return _json({
                "model": config.model,
                "configured": bool(config.api_key),
                "base_url": config.base_url or "https://api.openai.com/v1",
            })

        if method == "GET" and route == "/api/llm-config":
            return _json(_load_web_llm_config())

        if method == "POST" and route == "/api/llm-config":
            req = _json_body(body)
            _save_web_llm_config(req)
            return _json({"saved": True})

        # ── Orchestrator ──
        if method == "POST" and route == "/api/orchestrate":
            import uuid as _uuid
            req = _json_body(body)
            goal = str(req.get("goal", ""))
            if not goal:
                return _json({"error": "goal is required"}, status=400)

            # Auto-select template via LLM or fallback to default
            template_id = str(req.get("template_id") or "")
            if not template_id:
                template_id = _select_template_for_goal(goal)

            session_id = str(_uuid.uuid4())[:12]
            _orchestrator_sessions[session_id] = {
                "running": True, "log": [], "current_status": "starting",
                "mission_id": None, "graph_run_id": None,
            }
            payload = {
                "_goal": goal,
                "signal_nets": req.get("signal_nets", []),
                "reference_nets": ["GND"],
                "layout_file": str(req.get("layout_file", "")),
                "local_cut_region": _parse_bbox(str(req.get("bbox", "0,0,10,10"))),
                "target_metrics": [],
                "adapter_mode": str(req.get("adapter_mode", "deterministic")),
            }
            _threading.Thread(
                target=_orchestrator_loop,
                args=(session_id, goal, runtime, template_id, payload),
                daemon=True,
            ).start()
            return _json({"session_id": session_id, "goal": goal, "template_id": template_id}, status=201)

        if method == "GET" and route.startswith("/api/orchestrate-status/"):
            session_id = route.rsplit("/", 1)[-1]
            session = _orchestrator_sessions.get(session_id)
            if session is None:
                return _json({"error": "session not found"}, status=404)
            return _json({
                "running": session["running"],
                "current_status": session["current_status"],
                "mission_id": session.get("mission_id"),
                "graph_run_id": session.get("graph_run_id"),
                "log": session["log"][-50:],  # last 50 entries
            })

        return _json({"error": "not_found", "path": route}, status=404)
    except Exception as exc:
        return _json({"error": type(exc).__name__, "message": str(exc)}, status=400)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


# ── Built-in Orchestrator ──

import threading as _threading

_orchestrator_sessions: dict[str, dict[str, Any]] = {}


def _orchestrator_loop(
    session_id: str,
    goal: str,
    runtime: Any,
    template_id: str,
    initial_payload: dict[str, Any],
) -> None:
    """Background orchestrator loop: create → monitor → decide → repeat."""
    from aedt_agent.agent.graph_runner import advance_graph, create_graph_run, graph_status
    from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path
    from aedt_agent.agent.llm import LlmConfig, llm_complete, llm_complete_json

    session = _orchestrator_sessions[session_id]
    try:
        # Step 1: Create mission + graph_run
        mission = runtime.create_mission(goal, [], [])
        session["mission_id"] = mission.mission_id
        template = load_graph_template(resolve_template_path(template_id))
        graph_run = create_graph_run(runtime, mission.mission_id, template, initial_payload=initial_payload)
        session["graph_run_id"] = graph_run.graph_run_id
        session["log"].append({"type": "info", "msg": f"Created mission {mission.mission_id[:12]}… with {template_id}"})

        # Step 2: Orchestration loop
        last_signature = None
        while session["running"]:
            report = advance_graph(runtime, graph_run.graph_run_id)
            status = report["status"]
            session["current_status"] = status
            session["log"].append({"type": "info", "msg": f"Graph step: {status}"})

            if status == "succeeded":
                session["log"].append({"type": "ok", "msg": "✅ Graph completed successfully"})
                break
            elif status == "failed":
                error = report.get("graph_run", {}).get("error", {})
                session["log"].append({"type": "err", "msg": f"❌ Failed: {error.get('code','unknown')}"})
                session["log"].append({"type": "warn", "msg": "Stopped; explicit takeover required"})
                break
            elif status == "waiting_approval":
                session["log"].append({"type": "warn", "msg": "⏸ Waiting for human approval"})
                break
            elif status == "canceled":
                session["log"].append({"type": "warn", "msg": "Graph was canceled"})
                break
            elif status == "running":
                pass  # continue polling

            import time as _time
            signature = _web_orchestrator_signature(report)
            if signature == last_signature:
                _time.sleep(30)
            last_signature = signature
    except Exception as e:
        session["log"].append({"type": "err", "msg": f"Orchestrator error: {e}"})
    finally:
        session["running"] = False
        session["log"].append({"type": "info", "msg": "Orchestrator stopped"})


def run_agent_window(
    host: str = "127.0.0.1",
    port: int = 8766,
    db_path: str | Path = ".aedt-agent/missions.db",
    runtime: AgentRuntime | None = None,
) -> None:
    """Start the agent main window web server."""
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    if runtime is None:
        registry = InMemoryWorkerRegistry()
        registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
        registry.register(BRD_CHANNEL_SCORE_CAPABILITY, run_brd_channel_score_worker)
        registry.register(BRD_GEOMETRY_VALIDATE_CAPABILITY, run_brd_geometry_validate_worker)
        registry.register(BRD_ITERATION_QUALIFY_CAPABILITY, run_brd_iteration_qualify_worker)
        registry.register(BRD_TOUCHSTONE_EXPORT_CAPABILITY, run_brd_touchstone_export_worker)
        registry.register(BRD_TDR_EXPORT_CAPABILITY, run_brd_tdr_export_worker)
        registry.register(BRD_EVIDENCE_COMPARE_CAPABILITY, run_evidence_compare_worker)
        runtime = AgentRuntime(SQLiteMissionStore(db), registry=registry)

    # Load knowledge provider for agent context injection
    try:
        from aedt_agent.knowledge.sqlite_provider import SqliteKnowledgeProvider
        from aedt_agent.agent.graph_executors import set_agent_knowledge_provider
        kp = SqliteKnowledgeProvider()
        set_agent_knowledge_provider(kp)
        print(f"Knowledge base loaded for agent context injection")
    except Exception as e:
        print(f"Knowledge base not available: {e}")

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


# ── LLM config persistence ──

_LLM_CONFIG_PATH = Path(".aedt-agent/llm-config.json")


def _load_web_llm_config() -> dict[str, Any]:
    if _LLM_CONFIG_PATH.exists():
        return json.loads(_LLM_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def _save_web_llm_config(config: dict[str, Any]) -> None:
    _LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    saved = {}
    if config.get("api_key"):
        saved["api_key"] = str(config["api_key"])
    if config.get("model"):
        saved["model"] = str(config["model"])
    if config.get("base_url"):
        saved["base_url"] = str(config["base_url"])
    _LLM_CONFIG_PATH.write_text(json.dumps(saved, indent=2), encoding="utf-8")


def _select_template_for_goal(goal: str) -> str:
    """Auto-select the best YAML template for a given goal using LLM or keyword heuristics."""
    # Try LLM first
    try:
        from aedt_agent.agent.llm import LlmConfig, llm_complete_json
        config = LlmConfig.from_env()
        if config.api_key:
            system = (
                "Select the best ansys-agent YAML template for this engineering task. "
                "Available templates:\n"
                "- brd_local_cut_build: model review only (no solve)\n"
                "- brd_channel_optimize: full optimization (analyze→build→score→decide→loop)\n"
                "- brd_before_after_compare: compare before/after channel scores\n"
                "- brd_real_solve_evidence: real AEDT solve with evidence package\n"
                "- brd_multi_channel_demo: multi-channel fan-out scoring\n\n"
                "Return JSON: {\"template_id\": \"...\", \"reason\": \"...\"}"
            )
            result = llm_complete_json(system, f"Task: {goal}", config=config)
            tid = str(result.get("template_id", ""))
            if tid in {"brd_local_cut_build", "brd_channel_optimize", "brd_before_after_compare",
                        "brd_real_solve_evidence", "brd_multi_channel_demo"}:
                return tid
    except Exception:
        pass

    # Fallback: keyword heuristics
    g = goal.lower()
    if "optimize" in g or "optim" in g or "improve" in g or "tune" in g:
        return "brd_channel_optimize"
    if "compare" in g or "before" in g or "after" in g:
        return "brd_before_after_compare"
    if "solve" in g or "evidence" in g:
        return "brd_real_solve_evidence"
    if "multi" in g or "fan" in g:
        return "brd_multi_channel_demo"
    return "brd_local_cut_build"


def _merge_web_llm_config(config: Any) -> Any:
    """Merge web-saved LLM config into LlmConfig, web takes precedence over env."""
    try:
        saved = _load_web_llm_config()
    except Exception:
        return config
    if not saved:
        return config
    from aedt_agent.agent.llm import LlmConfig
    return LlmConfig(
        model=str(saved.get("model") or config.model),
        api_key=str(saved.get("api_key") or config.api_key),
        base_url=str(saved.get("base_url") or config.base_url),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )


def _web_orchestrator_signature(report: dict[str, Any]) -> tuple[Any, ...]:
    graph_run = report.get("graph_run") or {}
    return (
        report.get("status"),
        graph_run.get("step_count"),
        graph_run.get("current_node_id"),
        tuple(
            (
                item.get("node_id"),
                item.get("sequence"),
                item.get("status"),
                item.get("edge_decision"),
            )
            for item in report.get("node_runs", [])
        ),
    )


def _optimization_progress(runtime: AgentRuntime, mission_id: str) -> dict[str, Any]:
    history_csv = ""
    report_html = ""
    report_json = ""
    for graph_run in runtime.store.list_graph_runs(mission_id):
        for node_run in runtime.store.list_node_runs(graph_run.graph_run_id):
            payload = dict(node_run.output_payload or {})
            loop_context = payload.get("loop_context")
            if isinstance(loop_context, dict):
                history_csv = str(
                    loop_context.get("optimization_history_csv") or history_csv
                )
                report_html = str(loop_context.get("report_html") or report_html)
                report_json = str(loop_context.get("report_json") or report_json)
            history_csv = str(payload.get("optimization_history_csv") or history_csv)
            report_html = str(payload.get("report_html") or report_html)
            report_json = str(payload.get("report_json") or report_json)
    rows = []
    if history_csv:
        try:
            from aedt_agent.agent.optimization_handlers import read_history_csv

            rows = read_history_csv(history_csv, limit=20)
        except Exception:
            rows = []
    return {
        "mission_id": mission_id,
        "optimization_history_csv": history_csv,
        "report_html": report_html,
        "report_json": report_json,
        "history_rows": rows,
    }
