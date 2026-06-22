"""Agent main window — a web dashboard for mission management and DAG visualization."""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

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
    BRD_OPTIMIZATION_PROGRESS_CAPABILITY,
    BRD_OPTIMIZATION_REPORT_CAPABILITY,
    BRD_TDR_EXPORT_CAPABILITY,
    BRD_TOUCHSTONE_EXPORT_CAPABILITY,
    InMemoryWorkerRegistry,
    run_brd_channel_score_worker,
    run_brd_geometry_validate_worker,
    run_brd_iteration_qualify_worker,
    run_brd_local_cut_worker,
    run_brd_optimization_progress_worker,
    run_brd_optimization_report_worker,
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
.graph-panel .mermaid.monitor-board{background:transparent;border-radius:0;padding:0}
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
.dashboard{display:grid;gap:12px;margin-top:12px}
.dashboard-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.dash-panel{border:1px solid var(--line);border-radius:6px;background:#181a25;padding:10px;overflow:hidden}
.dash-panel h3{font-size:13px;margin:0 0 8px;color:var(--accent)}
.metric-row{display:grid;grid-template-columns:1fr auto;gap:8px;font-size:12px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.metric-row:last-child{border-bottom:0}.metric-row span:first-child{color:var(--muted)}
.data-table{width:100%;border-collapse:collapse;background:#181a25;border:1px solid var(--line);font-size:12px}
.data-table th,.data-table td{text-align:left;border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top}
.data-table th{color:var(--muted);font-size:11px;text-transform:uppercase}
.artifact-list{display:grid;gap:6px}.artifact-item{display:grid;gap:2px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.05)}
.artifact-item:last-child{border-bottom:0}.artifact-kind{font-size:11px;color:var(--yellow);font-weight:700}
.artifact-path{font:11px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--muted);word-break:break-all}
.artifact-item a{color:var(--blue);font-size:12px;text-decoration:none}.artifact-item a:hover{text-decoration:underline}
.monitor-shell{display:grid;gap:14px}
.monitor-topline{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;margin-bottom:2px}
.monitor-topline h2{font-size:18px;margin:0;color:#edf4ff;letter-spacing:0}
.monitor-db{font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;color:#8f9bb7;text-align:right;word-break:break-all;max-width:760px}
.monitor-card{position:relative;border:1px solid #2b3246;border-left:4px solid #6ea8ff;border-radius:8px;padding:14px 16px;background:linear-gradient(180deg,#1d2130 0%,#171b28 100%);box-shadow:0 12px 30px rgba(0,0,0,.22);display:grid;gap:12px;overflow:hidden}
.monitor-card::before{content:"";position:absolute;inset:0 0 auto 0;height:1px;background:linear-gradient(90deg,rgba(137,180,250,.7),rgba(166,227,161,.35),transparent);pointer-events:none}
.monitor-card.ok{border-left-color:#6fcf97}.monitor-card.err{border-left-color:#f07178}.monitor-card.wait{border-left-color:#f2c94c}.monitor-card.run{border-left-color:#6ea8ff}
.monitor-head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;align-items:start}
.monitor-title{cursor:pointer;color:#f5f8ff;font-weight:800;font-size:14px;line-height:1.35;max-width:960px}.monitor-title:hover{color:#9dc4ff;text-decoration:none}
.monitor-meta{font-size:11px;color:#8f9bb7}
.monitor-submeta{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
.monitor-chip{border:1px solid #343d55;border-radius:999px;background:#151a26;color:#aeb9d6;padding:3px 8px;font:11px/1.3 ui-monospace,SFMono-Regular,Consolas,monospace}
.monitor-chip.current{border-color:#6ea8ff;color:#d7e7ff;background:#132033}
.monitor-actions{display:grid;justify-items:end;gap:8px}
.monitor-counts{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.monitor-count{border:1px solid #343d55;border-radius:6px;padding:4px 7px;background:#141925;font:11px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace;color:#aeb9d6}
.monitor-count.ok{color:#8ee6a1;border-color:#2e6040}.monitor-count.err{color:#ff9ca8;border-color:#6b3340}.monitor-count.wait{color:#f8d56a;border-color:#6d5b25}.monitor-count.run{color:#9fc7ff;border-color:#315b8a}
.node-map-wrap{overflow:auto;padding:6px 0 2px;display:grid;justify-content:center}
.node-map{position:relative;min-width:520px;height:126px}
.node-map.looped{height:auto}
.node-map.vertical{min-width:560px}
.node-map-lines{position:absolute;inset:0;pointer-events:none;overflow:visible;z-index:0}
.flow-line{fill:none;stroke:#343d55;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.flow-line.ok{stroke:#3e7c52}
.flow-line.run{stroke:#74b7ff;stroke-dasharray:8 8;animation:flowDash 1.1s linear infinite;filter:drop-shadow(0 0 5px rgba(116,183,255,.65))}
.flow-line.wait{stroke:#7c6929}
.flow-line.err{stroke:#7a3745}
.node-flow{display:flex;flex-wrap:wrap;gap:10px 0;align-items:center;justify-content:center;padding:2px 0}
.flow-step{display:flex;align-items:center;min-width:0}
.flow-arrow{position:relative;width:48px;height:18px;margin:0 8px;flex:0 0 48px}
.flow-arrow::after{content:"";position:absolute;left:0;right:7px;top:8px;height:2px;background:#343d55}
.flow-arrow .arrow-head{position:absolute;right:0;top:4px;width:10px;height:10px;border-top:2px solid #343d55;border-right:2px solid #343d55;transform:rotate(45deg)}
.flow-step.ok .flow-arrow::after{background:#3e7c52}.flow-step.ok .arrow-head{border-color:#3e7c52}
.flow-step.run .flow-arrow::after{background:linear-gradient(90deg,#315b8a,#74b7ff,#315b8a);background-size:200% 100%;animation:flowLine 1.4s linear infinite}
.flow-step.run .arrow-head{border-color:#74b7ff;filter:drop-shadow(0 0 6px rgba(116,183,255,.7))}
.flow-step.wait .flow-arrow::after{background:#7c6929}.flow-step.wait .arrow-head{border-color:#7c6929}
.flow-step.err .flow-arrow::after{background:#7a3745}.flow-step.err .arrow-head{border-color:#7a3745}
.node-pill{position:relative;border:1px solid #30384e;border-radius:7px;padding:8px 34px 8px 12px;background:#121722;font-size:11px;min-height:58px;width:178px;display:grid;align-content:center;gap:3px;overflow:hidden}
.node-map .node-pill{position:absolute;z-index:1}
.node-map.vertical .node-pill{width:250px;height:64px;min-height:64px}
.node-map.vertical .node-pill b{white-space:normal;overflow-wrap:anywhere;line-height:1.15}
.node-pill.loop-hub{box-shadow:0 14px 34px rgba(0,0,0,.28),0 0 0 1px rgba(110,168,255,.10)}
.node-pill::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:#4b556f}
.node-pill.ok{background:#121d19;border-color:#2e6040}.node-pill.ok::before{background:#6fcf97}
.node-pill.err{background:#24161b;border-color:#6b3340}.node-pill.err::before{background:#f07178}
.node-pill.wait{background:#241f13;border-color:#6d5b25}.node-pill.wait::before{background:#f2c94c}
.node-pill.run{background:#121b2a;border-color:#315b8a;box-shadow:0 0 0 1px rgba(110,168,255,.16),0 0 30px rgba(110,168,255,.18)}.node-pill.run::before{background:#6ea8ff}
.node-pill.run::after{content:"";position:absolute;inset:-40% auto -40% -55%;width:46%;background:linear-gradient(90deg,transparent,rgba(133,195,255,.28),transparent);transform:skewX(-18deg);animation:nodeSheen 1.8s ease-in-out infinite}
.node-pill.pending{background:#111722;border-color:#2d3448}
.node-pill.pending b,.node-pill.pending span{opacity:.72}
.node-pill b{display:block;color:#f4f7ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px;letter-spacing:0}
.node-pill span{color:#8f9bb7}
.node-pill .node-kind{color:#c5cee8;text-transform:lowercase}.node-pill .node-status{font-weight:700}
.node-pill.run .node-status{color:#9fc7ff}.node-pill.ok .node-status{color:#8ee6a1}.node-pill.err .node-status{color:#ff9ca8}.node-pill.wait .node-status{color:#f8d56a}
.node-pill .node-index{position:absolute;right:8px;top:6px;color:#59627d;font:10px/1 ui-monospace,SFMono-Regular,Consolas,monospace}
@keyframes nodeSheen{0%{left:-55%;opacity:0}18%{opacity:1}58%{opacity:1}100%{left:108%;opacity:0}}
@keyframes flowLine{0%{background-position:200% 0}100%{background-position:0 0}}
@keyframes flowDash{to{stroke-dashoffset:-32}}
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
        <div id="runDashboard" class="dashboard"></div>
        <div id="optimizationProgress" style="margin-top:12px"></div>
      </div>
    </div>
    <div class="log-panel">
      <h2>Events</h2>
      <div id="eventLog"><div class="muted">选择 Mission 后显示事件</div></div>
    </div>
  </div>
  <div class="status-bar">
    <span id="connStatus">● 就绪</span>
    <span id="dbStatus" style="color:var(--muted)">DB: checking...</span>
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
  return `<span class="badge ${statusClass(status)}">${esc(status||'unknown')}</span>`;
}

function statusClass(status){
  const m={succeeded:'ok',failed:'err',waiting_approval:'wait',running:'run',canceled:'err',created:'run',pending:''};
  return m[status]||'';
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
      el.classList.remove('monitor-board');
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
      const dashboard=await api('/api/missions/'+activeMission+'/dashboard');
      renderDashboard(dashboard);
    }catch(e){
      document.getElementById('runDashboard').innerHTML='';
      document.getElementById('optimizationProgress').innerHTML='';
    }
  }catch(e){console.error(e)}
}

function esc(v){
  return String(v==null?'':v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function shortId(v){
  const s=String(v||'');
  return s.length>18?s.slice(0,18)+'…':s;
}

function metricRows(items){
  return items.map(([k,v])=>'<div class="metric-row"><span>'+esc(k)+'</span><b>'+esc(v==null||v===''?'--':v)+'</b></div>').join('');
}

function renderDashboard(data){
  const latest=data.latest_metrics||{};
  const pending=(data.approvals||[]).filter(a=>a.decision==='pending');
  const artifacts=(data.artifacts||[]).slice(0,18);
  const nodes=(data.graph_nodes||data.node_runs||[]).slice(-12);
  const dash=document.getElementById('runDashboard');
  dash.innerHTML=
    '<div class="dashboard-grid">'+
      '<div class="dash-panel"><h3>最新指标</h3>'+
        metricRows([
          ['Round', latest.round_index],
          ['Score', latest.score_status],
          ['SDD11 worst', latest.rl_worst_db],
          ['SDD21 worst', latest.insertion_worst_db_in_band],
          ['TDR peak dev', latest.tdr_peak_deviation_ohm],
          ['Objective', latest.objective_total_cost],
          ['TDR port', latest.tdr_observation_port]
        ])+
      '</div>'+
      '<div class="dash-panel"><h3>审批</h3>'+
        (pending.length?pending.map(a=>'<div class="artifact-item"><div class="artifact-kind">pending · '+esc(shortId(a.approval_id))+'</div><div class="artifact-path">'+esc(a.reason)+'</div></div>').join(''):'<div class="muted">没有待审批项</div>')+
      '</div>'+
      '<div class="dash-panel"><h3>关键文件</h3>'+
        (artifacts.length?'<div class="artifact-list">'+artifacts.map(renderArtifact).join('')+'</div>':'<div class="muted">暂无 artifact</div>')+
      '</div>'+
    '</div>'+
    '<div class="dash-panel"><h3>节点运行</h3>'+
      (nodes.length?'<table class="data-table"><tr><th>#</th><th>Node</th><th>Kind</th><th>Status</th><th>Decision</th></tr>'+
      nodes.map(n=>'<tr><td>'+esc(n.sequence)+'</td><td>'+esc(n.node_id)+'</td><td>'+esc(n.node_kind)+'</td><td>'+badge(n.status)+'</td><td>'+esc(n.edge_decision||'')+'</td></tr>').join('')+
      '</table>':'<div class="muted">暂无节点运行记录</div>')+
    '</div>';
  renderOptimizationProgress(data.progress||{});
}

function renderArtifact(a){
  const link=a.view_url?'<a target="_blank" href="'+esc(a.view_url)+'">打开</a> ':'';
  const exists=a.exists?'local':'path';
  return '<div class="artifact-item"><div><span class="artifact-kind">'+esc(a.kind||'artifact')+'</span> <span class="muted">'+exists+'</span></div><div>'+link+'<span class="artifact-path">'+esc(a.path)+'</span></div></div>';
}

function renderOptimizationProgress(progress){
  const panel=document.getElementById('optimizationProgress');
  if(progress.optimization_history_csv){
    const rows=(progress.history_rows||[]).slice(-8);
    panel.innerHTML='<h2>优化历史</h2><div class="muted" style="font-size:11px;margin-bottom:6px">'+esc(progress.optimization_history_csv)+'</div>'+
      '<table class="data-table">'+
      '<tr><th>Round</th><th>Status</th><th>Action</th><th>RL</th><th>TDR</th><th>Next</th></tr>'+
      rows.map(r=>'<tr><td>'+esc(r.round_index)+'</td><td>'+esc(r.round_status)+'</td><td>'+esc(r.action_type||'')+'</td><td>'+esc(r.rl_worst_db||'')+'</td><td>'+esc(r.tdr_peak_deviation_ohm||'')+'</td><td>'+esc(r.continue_recommendation||'')+'</td></tr>').join('')+
      '</table>';
  }else{
    panel.innerHTML='';
  }
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
  const [missions, system]=await Promise.all([
    api('/api/missions'),
    api('/api/system').catch(()=>null)
  ]);
  renderSystemStatus(system);
  const el=document.getElementById('mermaidGraph');
  el.classList.add('monitor-board');
  let html='<div class="monitor-shell"><div class="monitor-topline"><h2>Graph Monitor</h2>';
  if(system){
    html+='<div class="monitor-db">DB '+esc(system.db_path)+'<br>missions '+esc(system.counts.missions||0)+' · graph runs '+esc(system.counts.graph_runs||0)+' · node runs '+esc(system.counts.node_runs||0)+'</div>';
  }
  html+='</div>';
  let hasCards=false;
  for(const m of missions.missions){
    try{
      const [detail,dashboard]=await Promise.all([
        api('/api/missions/'+m.mission_id),
        api('/api/missions/'+m.mission_id+'/dashboard').catch(()=>({}))
      ]);
      const gr=dashboard.graph_run||detail.graph_run;
      if(!gr)continue;
      hasCards=true;
      const st=gr.status||'unknown';
      const nodes=(dashboard.graph_nodes||dashboard.node_runs||[]).slice(-20);
      const counts=monitorCounts(nodes);
      html+='<div class="monitor-card '+statusClass(st)+'">';
      html+='<div class="monitor-head">';
      html+='<div><div class="monitor-title" onclick="selectMission(\''+m.mission_id+'\')">'+esc(m.goal||'Untitled')+'</div>';
      html+='<div class="monitor-submeta">'+
        '<span class="monitor-chip">mission '+esc(shortId(m.mission_id))+'</span>'+
        '<span class="monitor-chip">graph '+esc(shortId(gr.graph_run_id))+'</span>'+
        '<span class="monitor-chip">step '+esc(gr.step_count||0)+'</span>'+
        '<span class="monitor-chip current">current '+esc(gr.current_node_id||'--')+'</span>'+
      '</div></div>';
      html+='<div class="monitor-actions">'+badge(st)+'<div class="monitor-counts">'+
        renderMonitorCount('ok',counts.succeeded)+
        renderMonitorCount('run',counts.running)+
        renderMonitorCount('wait',counts.waiting_approval)+
        renderMonitorCount('err',counts.failed)+
        renderMonitorCount('',counts.pending,'pending')+
      '</div></div></div>';
      html+=nodes.length?renderMonitorNodes(nodes):'<div class="muted">暂无节点运行记录</div>';
      html+='</div>';
    }catch(e){console.error('monitorAll fetch failed for '+m.mission_id,e)}
  }
  if(!hasCards)html+='<div class="muted">暂无 Graph。请确认 dashboard 和 run-loop 使用同一个 --db；当前 DB 见上方状态。</div>';
  html+='</div>';
  el.innerHTML=html;
  document.getElementById('runDashboard').innerHTML='';
  document.getElementById('optimizationProgress').innerHTML='';
}

function renderMonitorNodes(nodes){
  const solveIndex=nodes.findIndex(n=>(n.node_id||'')==='real_solve_worker');
  const nextSolveIndex=nodes.findIndex(n=>(n.node_id||'')==='prepare_next_solve');
  if(solveIndex>=0&&nextSolveIndex>=0)return renderLoopMonitorNodes(nodes,solveIndex,nextSolveIndex);
  return '<div class="node-flow">'+nodes.map((n,i)=>{
    const status=n.status||'pending';
    const cls=statusClass(status)||'pending';
    const connector=i<nodes.length-1?'<div class="flow-arrow" aria-hidden="true"><span class="arrow-head"></span></div>':'';
    return '<div class="flow-step '+cls+'"><div class="node-pill '+cls+'"><span class="node-index">'+String(i+1).padStart(2,'0')+'</span><b>'+esc(n.node_id||'node')+'</b><span><span class="node-kind">'+esc(n.node_kind||'')+'</span> · <span class="node-status">'+esc(statusLabel(status))+'</span>'+(n.edge_decision?' · '+esc(n.edge_decision):'')+'</span></div>'+connector+'</div>';
  }).join('')+'</div>';
}

function renderLoopMonitorNodes(nodes,solveIndex,nextSolveIndex){
  const nodeW=250,nodeH=64,row=88,padX=28,padY=18,laneGap=86;
  const mainX=padX,sideX=padX+nodeW+laneGap;
  const logicalMain=[
    'prepare_working_project',
    'real_solve_worker',
    'touchstone_export_worker',
    'tdr_export_worker',
    'channel_score_worker',
    'iteration_qualifier_worker',
    'progress_report_worker',
    'optimization_decider',
    'geometry_validator_worker',
    'model_edit_worker',
  ];
  const byId=new Map(nodes.map((node,index)=>[node.node_id||'',{node,index}]));
  const main=[];
  const placed=new Set();
  for(const id of logicalMain){
    const item=byId.get(id);
    if(item){main.push(item);placed.add(item.index);}
  }
  for(let i=0;i<nodes.length;i++){
    const id=nodes[i].node_id||'';
    if(!placed.has(i)&&id!==''&&id!=='prepare_next_solve'&&id!=='iteration_qualification_approval_gate'&&id!=='action_approval_gate'&&id!=='optimization_report'){
      main.push({node:nodes[i],index:i});
      placed.add(i);
    }
  }
  const width=sideX+nodeW+40;
  const height=Math.max(420,padY+Math.max(main.length,5)*row+nodeH+24);
  const positions=new Map();
  main.forEach((item,i)=>positions.set(item.index,{x:mainX,y:padY+i*row}));
  placeSideNode('iteration_qualification_approval_gate','iteration_qualifier_worker');
  placeSideNode('optimization_report','optimization_decider');
  placeSideNode('action_approval_gate','geometry_validator_worker');
  placeSideNode('prepare_next_solve','model_edit_worker');
  const markerId='arrow'+Math.random().toString(36).slice(2,8);
  let svg='<svg class="node-map-lines" viewBox="0 0 '+width+' '+height+'" preserveAspectRatio="none">'+
    '<defs>'+renderArrowMarker(markerId,'Neutral','#343d55')+renderArrowMarker(markerId,'Ok','#3e7c52')+renderArrowMarker(markerId,'Run','#74b7ff')+renderArrowMarker(markerId,'Wait','#7c6929')+renderArrowMarker(markerId,'Err','#7a3745')+'</defs>';
  for(let i=0;i<main.length-1;i++){
    const from=main[i],to=main[i+1],a=positions.get(from.index),b=positions.get(to.index);
    svg+=renderSvgEdge('M '+(a.x+nodeW/2)+' '+(a.y+nodeH)+' L '+(b.x+nodeW/2)+' '+(b.y-10),edgeClass(from.node),markerId);
  }
  svg+=renderBranchEdge('iteration_qualifier_worker','iteration_qualification_approval_gate',markerId);
  svg+=renderBranchEdge('iteration_qualification_approval_gate','progress_report_worker',markerId);
  svg+=renderBranchEdge('optimization_decider','optimization_report',markerId);
  svg+=renderBranchEdge('geometry_validator_worker','action_approval_gate',markerId);
  svg+=renderBranchEdge('action_approval_gate','model_edit_worker',markerId);
  svg+=renderBranchEdge('model_edit_worker','prepare_next_solve',markerId);
  const nextPos=positions.get(nextSolveIndex),solvePos=positions.get(solveIndex);
  if(nextPos&&solvePos){
    const loopX=sideX+nodeW+28;
    const startX=nextPos.x+nodeW,startY=nextPos.y+nodeH/2;
    const endX=solvePos.x+nodeW,endY=solvePos.y+nodeH/2;
    svg+=renderSvgEdge('M '+startX+' '+startY+' L '+loopX+' '+startY+' L '+loopX+' '+endY+' L '+endX+' '+endY,edgeClass(nodes[nextSolveIndex]),markerId);
  }
  svg+='</svg>';
  let html='<div class="node-map-wrap"><div class="node-map looped vertical" style="width:'+width+'px;height:'+height+'px">'+svg;
  for(let i=0;i<nodes.length;i++){
    const p=positions.get(i);
    if(!p)continue;
    html+=renderNodePill(nodes[i],i,'left:'+p.x+'px;top:'+p.y+'px',i===nextSolveIndex?'loop-hub':'');
  }
  return html+'</div></div>';

  function placeSideNode(id,anchorId){
    const item=byId.get(id),anchor=byId.get(anchorId);
    if(!item||!anchor)return;
    const anchorPos=positions.get(anchor.index);
    if(!anchorPos)return;
    positions.set(item.index,{x:sideX,y:anchorPos.y});
    placed.add(item.index);
  }

  function renderBranchEdge(fromId,toId,marker){
    const from=byId.get(fromId),to=byId.get(toId);
    if(!from||!to)return '';
    const a=positions.get(from.index),b=positions.get(to.index);
    if(!a||!b)return '';
    const cls=edgeClass(from.node);
    if(a.x===b.x){
      return renderSvgEdge('M '+(a.x+nodeW/2)+' '+(a.y+nodeH)+' L '+(b.x+nodeW/2)+' '+(b.y-10),cls,marker);
    }
    const forward=a.x<b.x;
    const startX=forward?a.x+nodeW:a.x;
    const endX=forward?b.x:b.x+nodeW;
    const startY=a.y+nodeH/2,endY=b.y+nodeH/2,midX=startX+(endX-startX)/2;
    return renderSvgEdge('M '+startX+' '+startY+' C '+midX+' '+startY+' '+midX+' '+endY+' '+endX+' '+endY,cls,marker);
  }
}

function renderNodePill(n,i,style,extraClass){
  const status=n.status||'pending';
  const cls=statusClass(status)||'pending';
  return '<div class="node-pill '+cls+' '+(extraClass||'')+'" style="'+style+'"><span class="node-index">'+String(i+1).padStart(2,'0')+'</span><b>'+esc(n.node_id||'node')+'</b><span><span class="node-kind">'+esc(n.node_kind||'')+'</span> · <span class="node-status">'+esc(statusLabel(status))+'</span>'+(n.edge_decision?' · '+esc(n.edge_decision):'')+'</span></div>';
}

function renderArrowMarker(base,name,color){
  return '<marker id="'+base+name+'" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="'+color+'"></path></marker>';
}

function renderSvgEdge(path,cls,markerId){
  const key=cls==='ok'?'Ok':cls==='run'?'Run':cls==='wait'?'Wait':cls==='err'?'Err':'Neutral';
  return '<path class="flow-line '+cls+'" marker-end="url(#'+markerId+key+')" d="'+path+'"></path>';
}

function edgeClass(node){
  return statusClass((node&&node.status)||'pending')||'pending';
}

function monitorCounts(nodes){
  const counts={succeeded:0,running:0,waiting_approval:0,failed:0,pending:0};
  for(const n of nodes){
    const s=n.status||'pending';
    if(s==='succeeded')counts.succeeded++;
    else if(s==='running'||s==='created')counts.running++;
    else if(s==='waiting_approval')counts.waiting_approval++;
    else if(s==='failed'||s==='canceled')counts.failed++;
    else counts.pending++;
  }
  return counts;
}

function renderMonitorCount(cls,value,label){
  return '<span class="monitor-count '+cls+'">'+esc(label||cls||'other')+' '+esc(value||0)+'</span>';
}

function statusLabel(status){
  const labels={succeeded:'done',running:'running',created:'queued',waiting_approval:'approval',failed:'failed',canceled:'canceled',pending:'pending'};
  return labels[status]||status||'pending';
}

function renderSystemStatus(system){
  if(!system)return;
  document.getElementById('dbStatus').textContent='DB: '+system.db_path+' · missions '+(system.counts.missions||0)+' · nodes '+(system.counts.node_runs||0);
  document.getElementById('connStatus').textContent=system.db_exists?'● DB connected':'● DB missing';
}

refreshMissions();
api('/api/system').then(renderSystemStatus).catch(()=>{});
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

        if method == "GET" and route == "/api/artifacts/file":
            return _artifact_file_response(runtime, parsed.query)

        if method == "GET" and route == "/api/system":
            return _json(_runtime_system_status(runtime))

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

        if method == "GET" and route.endswith("/dashboard"):
            parts = route.rstrip("/").split("/")
            mission_id = parts[3]  # /api/missions/{id}/dashboard
            return _json(_mission_dashboard(runtime, mission_id))

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
        registry.register(BRD_OPTIMIZATION_PROGRESS_CAPABILITY, run_brd_optimization_progress_worker)
        registry.register(BRD_OPTIMIZATION_REPORT_CAPABILITY, run_brd_optimization_report_worker)
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

    print(_dashboard_startup_message(host, port, db))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dashboard_startup_message(host: str, port: int, db_path: str | Path | None = None) -> str:
    message = f"[ansys-agent] dashboard: http://{host}:{port}"
    if db_path is not None:
        message += f" db={Path(db_path)}"
    return message.encode("ascii", errors="replace").decode("ascii")


def _json(data, *, status=200):
    return status, {"content-type": "application/json; charset=utf-8"}, (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _html(html_str):
    return 200, {"content-type": "text/html; charset=utf-8"}, html_str.encode("utf-8")


def _file_response(path: Path):
    suffix = path.suffix.casefold()
    content_type = {
        ".csv": "text/csv; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".txt": "text/plain; charset=utf-8",
    }.get(suffix, "application/octet-stream")
    return 200, {"content-type": content_type}, path.read_bytes()


def _artifact_file_response(runtime: AgentRuntime, query: str):
    params = parse_qs(query)
    mission_id = str(params.get("mission_id", [""])[0])
    artifact_path = unquote(str(params.get("path", [""])[0]))
    if not mission_id or not artifact_path:
        return _json({"error": "mission_id and path are required"}, status=400)
    known_paths = {item["path"] for item in _collect_mission_artifacts(runtime, mission_id)}
    if artifact_path not in known_paths:
        return _json({"error": "artifact path is not registered for this mission"}, status=403)
    path = Path(artifact_path)
    if not path.is_file():
        return _json({"error": "artifact file is not available on this host"}, status=404)
    return _file_response(path)


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


def _runtime_system_status(runtime: AgentRuntime) -> dict[str, Any]:
    db_path_value = getattr(runtime.store, "db_path", None)
    db_path = Path(db_path_value) if db_path_value is not None else None
    counts = {
        "missions": 0,
        "graph_runs": 0,
        "node_runs": 0,
        "approvals": 0,
        "events": 0,
    }
    if db_path is not None and db_path.exists():
        with sqlite3.connect(db_path) as db:
            table_names = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in counts:
                if table in table_names:
                    counts[table] = int(
                        db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
    return {
        "db_path": str(db_path) if db_path is not None else "",
        "db_exists": bool(db_path and db_path.exists()),
        "db_size_bytes": db_path.stat().st_size if db_path and db_path.exists() else 0,
        "counts": counts,
    }


def _mission_dashboard(runtime: AgentRuntime, mission_id: str) -> dict[str, Any]:
    runtime.get_mission(mission_id)
    graph_runs = runtime.store.list_graph_runs(mission_id)
    latest_status: dict[str, Any] = {}
    if graph_runs:
        latest_status = graph_status(runtime, graph_runs[-1].graph_run_id)
    progress = _optimization_progress(runtime, mission_id)
    node_runs = list(latest_status.get("node_runs") or [])
    approvals = [
        approval.to_json_dict()
        for approval in runtime.store.list_approvals(mission_id)
    ]
    return {
        "mission_id": mission_id,
        "graph_run": latest_status.get("graph_run"),
        "node_runs": [_node_run_dashboard_summary(item) for item in node_runs],
        "graph_nodes": _graph_node_dashboard_summaries(
            latest_status.get("graph_run") or {},
            node_runs,
        ),
        "handoffs": latest_status.get("handoffs") or [],
        "approvals": approvals,
        "artifacts": _collect_mission_artifacts(runtime, mission_id),
        "latest_metrics": _latest_metrics(node_runs, progress.get("history_rows") or []),
        "progress": progress,
    }


def _node_run_dashboard_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_run_id": item.get("node_run_id"),
        "node_id": item.get("node_id"),
        "node_role": item.get("node_role"),
        "node_kind": item.get("node_kind"),
        "sequence": item.get("sequence"),
        "status": item.get("status"),
        "edge_decision": item.get("edge_decision"),
        "artifact_count": len(item.get("artifact_refs") or []),
        "error": item.get("error"),
        "completed_at": item.get("completed_at"),
    }


def _graph_node_dashboard_summaries(
    graph_run: dict[str, Any],
    node_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_by_node: dict[str, dict[str, Any]] = {}
    for run in node_runs:
        node_id = str(run.get("node_id") or "")
        if node_id:
            latest_by_node[node_id] = run

    snapshot = graph_run.get("template_snapshot")
    template_nodes = []
    if isinstance(snapshot, dict):
        template_nodes = [
            item for item in snapshot.get("nodes", [])
            if isinstance(item, dict)
        ]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, node in enumerate(template_nodes, start=1):
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        run = latest_by_node.get(node_id, {})
        seen.add(node_id)
        rows.append(
            {
                "node_run_id": run.get("node_run_id"),
                "node_id": node_id,
                "node_role": run.get("node_role") or node.get("role") or "",
                "node_kind": run.get("node_kind") or node.get("kind") or "",
                "sequence": run.get("sequence") or index,
                "status": run.get("status") or "pending",
                "edge_decision": run.get("edge_decision") or "",
                "artifact_count": len(run.get("artifact_refs") or []),
                "error": run.get("error") or {},
                "capability": node.get("capability") or "",
            }
        )
    for run in node_runs:
        node_id = str(run.get("node_id") or "")
        if not node_id or node_id in seen:
            continue
        rows.append(_node_run_dashboard_summary(run))
    return rows


def _latest_metrics(
    node_runs: list[dict[str, Any]],
    history_rows: list[dict[str, str]],
) -> dict[str, Any]:
    keys = [
        "round_index",
        "round_status",
        "score_status",
        "touchstone_kind",
        "return_loss_trace",
        "insertion_loss_trace",
        "rl_worst_db",
        "sdd11_worst_db",
        "insertion_worst_db_in_band",
        "sdd21_worst_db_in_band",
        "tdr_observation_port",
        "tdr_peak_deviation_ohm",
        "objective_total_cost",
        "pass_fail_reason",
        "continue_recommendation",
    ]
    if history_rows:
        row = dict(history_rows[-1])
        return {key: row.get(key, "") for key in keys}
    for node_run in reversed(node_runs):
        payload = node_run.get("output_payload") or {}
        values = {
            key: _find_nested_value(payload, key)
            for key in keys
        }
        if any(value not in (None, "", []) for value in values.values()):
            return values
    return {key: "" for key in keys}


def _find_nested_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_nested_value(child, key)
            if found not in (None, "", []):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_nested_value(child, key)
            if found not in (None, "", []):
                return found
    return None


def _collect_mission_artifacts(runtime: AgentRuntime, mission_id: str) -> list[dict[str, Any]]:
    paths: list[str] = []
    for manifest in runtime.store.list_artifact_manifests(mission_id):
        paths.append(manifest.path)
    for graph_run in runtime.store.list_graph_runs(mission_id):
        for node_run in runtime.store.list_node_runs(graph_run.graph_run_id):
            paths.extend(str(item) for item in node_run.artifact_refs)
            paths.extend(_artifact_paths_from_payload(node_run.output_payload))
    progress = _optimization_progress(runtime, mission_id)
    for key in ("optimization_history_csv", "report_html", "report_json"):
        value = progress.get(key)
        if value:
            paths.append(str(value))
    for row in progress.get("history_rows") or []:
        for key in (
            "touchstone_path",
            "tdr_path",
            "edit_manifest_path",
            "solve_result_path",
            "solve_manifest_path",
            "score_evidence_path",
            "artifact_refs",
        ):
            paths.extend(_coerce_artifact_strings(row.get(key)))
    unique_paths = _unique_strings(paths)
    return [_artifact_descriptor(mission_id, path) for path in unique_paths]


_ARTIFACT_KEYS = {
    "artifact_refs",
    "plot_artifacts",
    "optimization_history_csv",
    "optimization_report_html",
    "report_html",
    "report_json",
    "touchstone_path",
    "tdr_path",
    "edit_manifest_path",
    "solve_result_path",
    "solve_manifest_path",
    "score_evidence_path",
}


def _artifact_paths_from_payload(value: Any, *, field_name: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            key_text = str(key)
            key_lower = key_text.casefold()
            if (
                key_lower in _ARTIFACT_KEYS
                or key_lower.endswith("_path")
                or "artifact" in key_lower
            ):
                paths.extend(_coerce_artifact_strings(child))
            else:
                paths.extend(_artifact_paths_from_payload(child, field_name=key_text))
        return paths
    if isinstance(value, list):
        paths: list[str] = []
        for child in value:
            paths.extend(_artifact_paths_from_payload(child, field_name=field_name))
        return paths
    if field_name.casefold() in _ARTIFACT_KEYS:
        return _coerce_artifact_strings(value)
    return []


def _coerce_artifact_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            try:
                return _coerce_artifact_strings(json.loads(text))
            except Exception:
                pass
        if "\n" in text or ";" in text:
            parts = [part.strip() for part in text.replace("\n", ";").split(";")]
            return [part for part in parts if part]
        return [text]
    if isinstance(value, dict):
        paths: list[str] = []
        for child in value.values():
            paths.extend(_coerce_artifact_strings(child))
        return paths
    if isinstance(value, list):
        paths: list[str] = []
        for child in value:
            paths.extend(_coerce_artifact_strings(child))
        return paths
    return []


def _artifact_descriptor(mission_id: str, path: str) -> dict[str, Any]:
    exists = Path(path).is_file()
    return {
        "path": path,
        "kind": _artifact_kind(path),
        "exists": exists,
        "view_url": (
            f"/api/artifacts/file?mission_id={quote(mission_id, safe='')}"
            f"&path={quote(path, safe='')}"
            if exists
            else ""
        ),
    }


def _artifact_kind(path: str) -> str:
    name = Path(path).name.casefold()
    suffix = Path(path).suffix.casefold()
    if suffix in {".s4p", ".s2p", ".snp"}:
        return "touchstone"
    if suffix == ".html":
        return "report_html"
    if suffix == ".csv" and "history" in name:
        return "history_csv"
    if suffix == ".csv" and "tdr" in name:
        return "tdr_csv"
    if suffix in {".svg", ".png", ".jpg", ".jpeg"}:
        if "sdd11" in name:
            return "plot_sdd11"
        if "sdd21" in name:
            return "plot_sdd21"
        if "tdr" in name:
            return "plot_tdr"
        return "plot"
    if suffix == ".json" and "evidence" in name:
        return "score_evidence"
    if suffix == ".json" and "manifest" in name:
        return "manifest"
    if suffix == ".json":
        return "json"
    if suffix == ".aedt":
        return "aedt_model"
    return "artifact"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


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
