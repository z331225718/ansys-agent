from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from aedt_agent.demo.config import AedtConfig, PlannerConfig
from aedt_agent.demo.service import DemoService


def render_demo_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AEDT Agent End-to-End Demo</title>
  <style>
    :root{--bg:#f3f4ef;--paper:#fffefa;--ink:#18212b;--muted:#637083;--line:#d9ddd2;--blue:#285ee8;--teal:#0f766e;--amber:#b7791f;--red:#b42318;--soft:#eef2ea;--graph:#f8faf6}
    *{box-sizing:border-box}body{margin:0;font-family:Inter,Arial,'Noto Sans SC',sans-serif;background:var(--bg);color:var(--ink);letter-spacing:0}
    button,input,select,textarea{font:inherit}button{border:0;background:var(--ink);color:#fff;border-radius:6px;padding:10px 14px;cursor:pointer;font-weight:800}button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}button:hover{filter:brightness(.96)}button:disabled{opacity:.55;cursor:not-allowed}
    input,select,textarea{border:1px solid var(--line);border-radius:6px;padding:10px 11px;width:100%;background:#fff;color:var(--ink)}textarea{min-height:118px;resize:vertical;line-height:1.5}a{color:var(--blue);text-decoration:none}.muted{color:var(--muted);line-height:1.55}
    .page{max-width:1320px;margin:0 auto;padding:24px 20px 36px}.top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:end;margin-bottom:18px}.kicker{font-size:12px;text-transform:uppercase;color:var(--amber);font-weight:900;letter-spacing:.08em}.top h1{font-size:34px;line-height:1.08;margin:5px 0 8px}.top p{margin:0;max-width:780px}.status-pill{display:inline-flex;gap:8px;align-items:center;border:1px solid #b7decf;background:#ecfdf7;color:#047857;border-radius:999px;padding:7px 12px;font-size:13px;font-weight:800;white-space:nowrap}.dot{width:8px;height:8px;border-radius:50%;background:#10b981}
    .shell{display:grid;grid-template-columns:360px minmax(0,1fr) 330px;gap:14px}.panel{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:16px}.panel h2{font-size:17px;margin:0 0 12px}.stack{display:grid;gap:13px}.row{display:flex;gap:9px;flex-wrap:wrap}.field{display:grid;gap:6px}.field label{font-size:12px;font-weight:900;color:#334155;text-transform:uppercase}.params{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    .agent-note{border-left:3px solid var(--blue);background:#f5f7ff;padding:10px 11px;border-radius:6px;color:#344054;font-size:13px;line-height:1.5}.planner-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.planner-chip{border:1px solid var(--line);border-radius:8px;background:#fff;padding:9px}.planner-chip b{display:block;font-size:13px}.planner-chip span{font-size:11px;color:var(--muted);font-weight:800}.diagram{height:170px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,#f8faf6,#eef2ea);position:relative;overflow:hidden}.air{position:absolute;inset:14px;border:1px dashed #94a3b8;border-radius:6px}.substrate{position:absolute;left:38px;right:38px;bottom:44px;height:46px;background:#d8c58b;border:1px solid #a5883a}.ground{position:absolute;left:34px;right:34px;bottom:38px;height:5px;background:#7c5b21}.trace{position:absolute;left:74px;right:74px;bottom:91px;height:7px;background:#c58b2a}.port{position:absolute;bottom:43px;width:4px;height:55px;background:#2563eb}.port.p1{left:73px}.port.p2{right:73px}.dipole-arm{position:absolute;top:82px;height:8px;background:#b87333;border:1px solid #8a4f20;border-radius:999px}.dipole-arm.left{left:44px;right:154px}.dipole-arm.right{left:154px;right:44px}.feed-sheet{position:absolute;left:145px;top:67px;width:10px;height:38px;border:2px solid #2563eb;background:rgba(37,99,235,.12)}.sphere{position:absolute;inset:24px;border:1px dashed #64748b;border-radius:50%}.diagram-label{position:absolute;font-size:11px;color:#475569;font-weight:800}.diagram-label.l1{left:38px;bottom:98px}.diagram-label.l2{right:40px;bottom:28px}
    .flow{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.step{display:grid;grid-template-columns:30px minmax(0,1fr) auto;align-items:center;gap:9px;padding:11px;border:1px solid var(--line);border-radius:8px;background:#fff}.index{width:26px;height:26px;border-radius:6px;background:var(--soft);display:grid;place-items:center;font-weight:900;color:#334155}.step b{font-size:14px}.state{font-size:12px;color:var(--muted);font-weight:900}.state.ok{color:var(--teal)}.state.fail{color:var(--red)}
    .result{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.metric,.sparam{border:1px solid var(--line);border-radius:8px;padding:12px;background:#fff}.metric strong{display:block;font-size:19px;line-height:1.15}.metric span,.sparam span{font-size:12px;color:var(--muted);font-weight:800}.sparams{display:grid;gap:10px}.sparam strong{display:block;font-size:30px;line-height:1.05;margin-bottom:6px}.sparam.primary{background:#f7fbf9;border-color:#b7decf}.sparam.secondary{background:#fff8ed;border-color:#ecd3a5}.chart{border:1px solid var(--line);border-radius:8px;background:#fff;padding:10px}.chart-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}.legend{display:flex;gap:10px;font-size:12px;color:var(--muted);font-weight:800}.legend i{display:inline-block;width:18px;height:3px;border-radius:999px;margin-right:5px;vertical-align:middle}.legend .s11{background:var(--teal)}.legend .s21{background:var(--amber)}#sparamChart{width:100%;height:220px;display:block}.llm-log{background:#fff;border:1px solid var(--line);border-radius:8px;padding:11px;min-height:190px;max-height:280px;overflow:auto;font-size:13px;line-height:1.5}.log-entry{border-left:3px solid var(--line);padding:7px 9px;margin-bottom:8px;background:#fafaf7}.log-entry.user{border-left-color:var(--blue)}.log-entry.llm{border-left-color:var(--amber)}.log-entry.ok{border-left-color:var(--teal)}.log-entry.err{border-left-color:var(--red)}.log-entry b{display:block;font-size:12px;text-transform:uppercase;color:#334155;margin-bottom:3px}.artifacts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.artifact{border:1px solid var(--line);border-radius:8px;padding:10px;background:#fff;font-size:12px;overflow:hidden;text-overflow:ellipsis}.reports{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}.report{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:13px}.report b{display:block;margin-bottom:5px}.advanced{font-size:13px;color:var(--muted)}pre{background:#18212b;color:#e5e7eb;border-radius:8px;padding:12px;overflow:auto;max-height:330px;font-size:12px;line-height:1.45}
    @media(max-width:1120px){.shell{grid-template-columns:1fr}.flow,.result,.artifacts,.reports{grid-template-columns:1fr}.top{grid-template-columns:1fr}.page{padding:18px 12px}.params{grid-template-columns:1fr}}
  </style>
</head>
<body>
<main class="page">
  <section class="top">
    <div>
      <div class="kicker">Controlled AEDT Agent Demo</div>
      <h1>AEDT Agent End-to-End Demo</h1>
      <p class="muted">LLM 先把自然语言仿真需求规划成受控 workflow，后端 validator 接受后再驱动真实 AEDT 创建模型、求解并读取结果。当前演示支持微带线 S 参数和偶极子天线两条 workflow。</p>
    </div>
    <div class="status-pill"><span class="dot"></span>Real AEDT graphical run</div>
  </section>

  <section class="shell">
    <aside class="panel stack">
      <h2 id="workflowTitle">Microstrip S-Parameter Workflow</h2>
      <div class="field">
        <label for="workflowSelect">Workflow Template</label>
        <select id="workflowSelect" onchange="changeWorkflow(this.value)">
          <option value="microstrip_sparameter">Microstrip S 参数</option>
          <option value="dipole_antenna_s11_farfield">Dipole 天线 S11</option>
        </select>
      </div>
      <div class="field">
        <label for="agentRequest">用户需求</label>
        <textarea id="agentRequest">做一个微带线 S 参数仿真，求解频率 2.4GHz，扫频到 10GHz。</textarea>
      </div>
      <div class="agent-note" id="agentPlan">LLM planner 会从自然语言中推测仿真参数，并生成受控 workflow JSON；如果未配置 LLM API，会回退到 deterministic planner。</div>
      <div class="planner-strip">
        <div class="planner-chip"><b id="plannerModeMetric">not planned</b><span>Planner</span></div>
        <div class="planner-chip"><b id="repairMetric">0</b><span>Repair</span></div>
        <div class="planner-chip"><b id="workflowMetric">--</b><span>Workflow</span></div>
      </div>
      <div class="diagram" id="modelDiagram" aria-label="workflow model preview"></div>
      <div class="row">
        <button id="planButton" class="secondary" onclick="planWorkflowForDemo()">Plan with LLM</button>
        <button id="runButton" onclick="runRealAedtDemo()">Run Real AEDT</button>
      </div>
      <div class="muted">主路径：需求输入 → LLM planner → workflow validation → 真实 AEDT 图形界面执行。节点 catalog、planner 和 benchmark 等调试入口在 <a href="/advanced">Advanced 工作台</a>。</div>
    </aside>

    <section class="panel stack">
      <h2>LLM 交互与流程进度</h2>
      <div class="llm-log" id="llmLog">
        <div class="log-entry"><b>System</b>等待用户输入需求并点击 Plan with LLM。</div>
      </div>
      <div class="flow" id="workflowFlow"></div>
    </section>

    <aside class="panel stack">
      <h2>结果</h2>
      <div class="sparams" id="sparams">
        <div class="sparam primary"><strong id="s11Metric">--</strong><span>S11 at selected frequency</span></div>
        <div class="sparam secondary"><strong id="s21Metric">--</strong><span id="s21Label">S21 at selected frequency</span></div>
        <div class="sparam"><strong id="freqMetric">--</strong><span>Touchstone sample</span></div>
      </div>
      <div class="chart">
        <div class="chart-head"><b>S-Parameter Sweep</b><div class="legend"><span><i class="s11"></i>S11</span><span><i class="s21"></i>S21</span></div></div>
        <svg id="sparamChart" viewBox="0 0 300 220" role="img" aria-label="S11 and S21 versus frequency"></svg>
      </div>
      <div class="result">
        <div class="metric"><strong id="statusMetric">not run</strong><span>Status</span></div>
        <div class="metric"><strong id="validationMetric">not run</strong><span>Validation Result</span></div>
        <div class="metric"><strong id="objectMetric">PEC · P1/P2 · Radiation · S2P</strong><span>Expected Outputs</span></div>
      </div>
      <div class="artifacts" id="artifacts"></div>
      <details>
        <summary class="advanced">展开 workflow_run JSON</summary>
        <pre id="rawResult">{}</pre>
      </details>
    </aside>
  </section>

  <section class="reports">
    <a class="report" href="/reports/stage_c_real_smoke_dashboard.html" target="_blank"><b>真实 AEDT Smoke</b><span class="muted">4 个真实 AEDT workflow 结果</span></a>
    <a class="report" href="/reports/stage_c_node_evolution_review.html" target="_blank"><b>节点进化 Review</b><span class="muted">proposal 和人工 gate</span></a>
    <a class="report" href="/reports/stage_c2_planner_benchmark.html" target="_blank"><b>Planner Benchmark</b><span class="muted">自然语言规划成功率</span></a>
    <a class="report" href="/advanced"><b>Advanced 工作台</b><span class="muted">catalog / planner / API 调试入口</span></a>
  </section>
</main>
<script>
let currentWorkflow = null;
let currentWorkflowSource = '';
let currentTemplateId = 'microstrip_sparameter';
let plannerInFlight = false;
let seenTuningRounds = new Set();
let tuningCompletionLogged = false;
const WORKFLOWS = {
  microstrip_sparameter: {
    title: 'Microstrip S-Parameter Workflow',
    request: '做一个微带线 S 参数仿真，求解频率 2.4GHz，扫频到 10GHz。',
    expected: 'PEC · P1/P2 · Radiation · S2P',
    s21Label: 'S21 at selected frequency',
    diagram: '<div class="air"></div><div class="substrate"></div><div class="ground"></div><div class="trace"></div><div class="port p1"></div><div class="port p2"></div><div class="diagram-label l1">Trace + lumped ports</div><div class="diagram-label l2">Ground / FR4</div>',
    steps: [
      ['substrate','Create Substrate','创建 FR4 substrate'],
      ['trace','Create Ground, Trace & Port Sheets','端口 sheet 与 trace 等宽'],
      ['ground_pec','Assign Ground PEC','给 ground 设置 Perfect E'],
      ['trace_pec','Assign Trace PEC','给 trace 设置 Perfect E'],
      ['airbox','Create Airbox','创建空气盒'],
      ['radiation','Assign Radiation','设置辐射边界'],
      ['lumped_port_1','Create Lumped Port P1','输入端 lumped port'],
      ['lumped_port_2','Create Lumped Port P2','输出端 lumped port'],
      ['setup','Create Setup','创建 HFSS adaptive setup'],
      ['sweep','Create Sweep','创建 frequency sweep'],
      ['solve','Solve Setup','运行 AEDT 仿真'],
      ['postprocess','Postprocess S11/S21','生成 S 参数报告和 Touchstone'],
      ['validation','Validate Result','校验端口、边界、报告和 artifact']
    ]
  },
  dipole_antenna_s11_farfield: {
    title: 'Dipole Antenna S11 Workflow',
    request: '做一个偶极子天线 S11 仿真，求解频率 2.4GHz，扫频 1GHz 到 4GHz，并创建远场设置。',
    expected: 'Copper arms · Lumped feed · Radiation · Farfield setup · S1P',
    s21Label: 'Farfield setup',
    diagram: '<div class="sphere"></div><div class="dipole-arm left"></div><div class="feed-sheet"></div><div class="dipole-arm right"></div><div class="diagram-label l1">Copper dipole arms</div><div class="diagram-label l2">Radiation + farfield setup</div>',
    steps: [
      ['dipole_geometry','Create Dipole Geometry','创建铜偶极子臂和 feed sheet'],
      ['airbox','Create Airbox','创建空气盒'],
      ['radiation','Assign Radiation','设置辐射边界'],
      ['feed_port','Create Lumped Feed Port','中心馈电 lumped port'],
      ['setup','Create Setup','创建 HFSS adaptive setup'],
      ['sweep','Create Sweep','创建 frequency sweep'],
      ['farfield','Create Farfield Setup','创建 InfiniteSphere 远场设置'],
      ['solve','Solve Setup','运行 AEDT 仿真'],
      ['s11_report','Postprocess S11','生成 S11 报告和 Touchstone'],
      ['validation','Validate Result','校验端口、边界、报告和 artifact']
    ]
  }
};
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'content-type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(data));
  return data;
}
function setStep(id, state) {
  const node = document.querySelector(`#${id} .state`);
  if (!node) return;
  node.textContent = state;
  node.className = 'state ' + (state === 'done' ? 'ok' : state === 'failed' ? 'fail' : '');
}
function workflowDef() { return WORKFLOWS[currentTemplateId] || WORKFLOWS.microstrip_sparameter; }
function renderWorkflowChrome() {
  const def = workflowDef();
  document.getElementById('workflowTitle').textContent = def.title;
  document.getElementById('modelDiagram').innerHTML = def.diagram;
  document.getElementById('objectMetric').textContent = def.expected;
  document.getElementById('s21Label').textContent = def.s21Label;
  document.getElementById('workflowFlow').innerHTML = def.steps.map((step, index) => `<div class="step" id="step-${step[0]}"><div class="index">${index + 1}</div><div><b>${step[1]}</b><div class="muted">${step[2]}</div></div><div class="state">pending</div></div>`).join('');
}
function resetSteps() {
  workflowDef().steps.forEach(step => setStep('step-' + step[0], 'pending'));
}
function changeWorkflow(templateId) {
  currentTemplateId = templateId;
  currentWorkflow = null;
  currentWorkflowSource = '';
  seenTuningRounds = new Set();
  tuningCompletionLogged = false;
  const def = workflowDef();
  document.getElementById('agentRequest').value = def.request;
  document.getElementById('workflowMetric').textContent = '--';
  document.getElementById('plannerModeMetric').textContent = 'not planned';
  document.getElementById('agentPlan').textContent = 'LLM planner 会从自然语言中推测仿真参数，并生成受控 workflow JSON。';
  renderWorkflowChrome();
  renderSParameterChart([], '');
}
function appendLog(role, text, klass='') {
  const log = document.getElementById('llmLog');
  const node = document.createElement('div');
  node.className = `log-entry ${klass}`;
  node.innerHTML = `<b>${role}</b>${text}`;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
}
function setBusy(isBusy) {
  plannerInFlight = isBusy;
  document.getElementById('planButton').disabled = isBusy;
  document.getElementById('runButton').disabled = isBusy;
  document.getElementById('planButton').textContent = isBusy ? 'Planning...' : 'Plan with LLM';
}
async function loadFixedWorkflow() {
  const data = await api('/api/templates/' + encodeURIComponent(currentTemplateId));
  currentWorkflow = data.workflow;
  currentWorkflowSource = 'template';
  document.getElementById('plannerModeMetric').textContent = 'template';
  document.getElementById('repairMetric').textContent = '0';
  document.getElementById('workflowMetric').textContent = currentWorkflow.workflow_id || '--';
  document.getElementById('rawResult').textContent = JSON.stringify(data.workflow, null, 2);
}
async function planWorkflowForDemo() {
  if (plannerInFlight) return null;
  setBusy(true);
  document.getElementById('plannerModeMetric').textContent = 'planning';
  document.getElementById('agentPlan').textContent = 'LLM planner 正在生成 workflow JSON，并等待 backend validator 校验。';
  const request = document.getElementById('agentRequest').value;
  appendLog('User', request, 'user');
  appendLog('LLM', '已发送规划请求。模型需要返回 workflow JSON，页面会等待 validator 结果。', 'llm');
  let waited = 0;
  const heartbeat = setInterval(() => {
    waited += 5;
    appendLog('LLM', `仍在等待模型响应... ${waited}s`, 'llm');
  }, 5000);
  try {
    const data = await api('/api/plan', {method:'POST', body:JSON.stringify({planner_mode:'llm', user_request:request})});
    clearInterval(heartbeat);
    currentWorkflow = data.generated_workflow;
    if (!currentWorkflow) throw new Error('Planner did not return a valid workflow');
    currentWorkflowSource = 'planner';
    if (data.selected_template && WORKFLOWS[data.selected_template]) {
      currentTemplateId = data.selected_template;
      document.getElementById('workflowSelect').value = currentTemplateId;
      renderWorkflowChrome();
    }
    document.getElementById('plannerModeMetric').textContent = data.planner_mode || 'unknown';
    document.getElementById('repairMetric').textContent = String(data.repair_count || 0);
    document.getElementById('workflowMetric').textContent = currentWorkflow && currentWorkflow.workflow_id ? currentWorkflow.workflow_id : '--';
    const fallback = data.fallback_reason ? ` fallback=${data.fallback_reason}` : '';
    document.getElementById('agentPlan').textContent = `Planner ${data.planner_mode}${fallback}，repair ${data.repair_count || 0}，validation errors ${(data.validation_errors || []).length}。`;
    appendLog('Validator', `workflow=${currentWorkflow.workflow_id}，planner=${data.planner_mode}${fallback}，repair=${data.repair_count || 0}，errors=${(data.validation_errors || []).length}`, 'ok');
    for (const attempt of (data.attempts || [])) {
      const status = attempt.validation && attempt.validation.passed ? 'passed' : 'failed';
      appendLog('Attempt ' + attempt.attempt, `${attempt.mode} ${status}${attempt.error ? ' - ' + attempt.error : ''}`, status === 'passed' ? 'ok' : 'err');
    }
    document.getElementById('rawResult').textContent = JSON.stringify(data, null, 2);
    return data;
  } catch (error) {
    clearInterval(heartbeat);
    appendLog('Error', String(error.message || error), 'err');
    document.getElementById('plannerModeMetric').textContent = 'failed';
    throw error;
  } finally {
    setBusy(false);
  }
}
function renderResult(result) {
  const stepMap = Object.fromEntries(workflowDef().steps.map(step => [step[0], 'step-' + step[0]]));
  for (const step of (result.steps || [])) {
    if (stepMap[step.step_id]) setStep(stepMap[step.step_id], step.status === 'succeeded' ? 'done' : 'failed');
  }
  if (!result.steps || result.steps.length === 0) {
    const state = result.status === 'failed' ? 'failed' : 'running';
    workflowDef().steps.filter(step => step[0] !== 'validation').forEach(step => setStep('step-' + step[0], state));
  }
  const validationPassed = result.model_validation && result.model_validation.passed;
  setStep('step-validation', validationPassed ? 'done' : (result.status === 'failed' ? 'failed' : 'running'));
  document.getElementById('statusMetric').textContent = result.status;
  document.getElementById('validationMetric').textContent = result.model_validation && result.model_validation.summary ? result.model_validation.summary : 'waiting';
  renderSParameters(result.sparameters || {});
  document.getElementById('rawResult').textContent = JSON.stringify(result, null, 2);
  document.getElementById('artifacts').innerHTML = Object.entries(result.artifacts || {}).map(([key,value]) => `<a class="artifact" href="/${value}" target="_blank"><b>${key}</b><br>${value}</a>`).join('');
}
function renderSParameters(sparameters) {
  const selected = sparameters.selected || {};
  document.getElementById('s11Metric').textContent = Number.isFinite(selected.s11_db) ? `${selected.s11_db.toFixed(2)} dB` : '--';
  document.getElementById('s21Metric').textContent = currentTemplateId === 'dipole_antenna_s11_farfield' ? 'InfiniteSphere1' : (Number.isFinite(selected.s21_db) ? `${selected.s21_db.toFixed(2)} dB` : '--');
  document.getElementById('freqMetric').textContent = selected.frequency ? `${selected.frequency} ${sparameters.frequency_unit || ''}` : '--';
  renderSParameterChart(sparameters.samples || [], sparameters.frequency_unit || '');
}
function renderSParameterChart(samples, unit) {
  const svg = document.getElementById('sparamChart');
  const width = 300, height = 220, left = 42, right = 12, top = 16, bottom = 34;
  const plotW = width - left - right, plotH = height - top - bottom;
  const valid = samples.filter(item => Number.isFinite(item.frequency) && Number.isFinite(item.s11_db));
  if (valid.length < 2) {
    svg.innerHTML = `<text x="150" y="112" text-anchor="middle" fill="#637083" font-size="12">waiting for sweep data</text>`;
    return;
  }
  const xs = valid.map(item => item.frequency);
  const ys = valid.flatMap(item => Number.isFinite(item.s21_db) ? [item.s11_db, item.s21_db] : [item.s11_db]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.floor(Math.min(...ys) / 5) * 5;
  const maxY = Math.ceil(Math.max(...ys) / 5) * 5;
  const spanX = maxX - minX || 1, spanY = maxY - minY || 1;
  const x = value => left + ((value - minX) / spanX) * plotW;
  const y = value => top + ((maxY - value) / spanY) * plotH;
  const path = key => valid.map((item, index) => `${index ? 'L' : 'M'}${x(item.frequency).toFixed(2)},${y(item[key]).toFixed(2)}`).join(' ');
  const yTicks = [maxY, (maxY + minY) / 2, minY];
  svg.innerHTML = `
    <rect x="0" y="0" width="${width}" height="${height}" fill="#fff"/>
    ${yTicks.map(tick => `<line x1="${left}" y1="${y(tick)}" x2="${width - right}" y2="${y(tick)}" stroke="#e5e7eb"/><text x="${left - 8}" y="${y(tick) + 4}" text-anchor="end" fill="#637083" font-size="10">${tick.toFixed(0)}</text>`).join('')}
    <line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" stroke="#cbd5e1"/>
    <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" stroke="#cbd5e1"/>
    <path d="${path('s11_db')}" fill="none" stroke="#0f766e" stroke-width="2.4"/>
    ${valid.some(item => Number.isFinite(item.s21_db)) ? `<path d="${path('s21_db')}" fill="none" stroke="#b7791f" stroke-width="2.4"/>` : ''}
    <text x="${left}" y="${height - 10}" fill="#637083" font-size="10">${minX.toFixed(2)} ${unit}</text>
    <text x="${width - right}" y="${height - 10}" text-anchor="end" fill="#637083" font-size="10">${maxX.toFixed(2)} ${unit}</text>
    <text x="12" y="16" fill="#637083" font-size="10">dB</text>
  `;
}
async function runRealAedtDemo() {
  if (!currentWorkflow || currentWorkflowSource !== 'planner') await planWorkflowForDemo();
  appendLog('LLM Agent', '开始判断执行模式：普通需求走一次真实 AEDT workflow；如果用户要求谐振点落到目标频率，则自动进入多轮真实 AEDT 调参。', 'llm');
  seenTuningRounds = new Set();
  tuningCompletionLogged = false;
  resetSteps();
  document.getElementById('statusMetric').textContent = 'running';
  document.getElementById('validationMetric').textContent = 'launching AEDT';
  const payload = {workflow:currentWorkflow, template_id:currentTemplateId, graphical:true, user_request:document.getElementById('agentRequest').value};
  const started = await api('/api/agent-run', {method:'POST', body:JSON.stringify(payload)});
  appendLog('Agent Decision', started.run_kind === 'dipole_tuning' ? 'LLM 判断为闭环调参 workflow，将逐轮运行 AEDT 并读取 S11 谐振点。' : 'LLM 判断为单次生成 workflow，直接运行一次 AEDT。', 'ok');
  renderResult(started);
  let result = started;
  while (result.status === 'queued' || result.status === 'running') {
    await new Promise(resolve => setTimeout(resolve, 2000));
    result = await api('/api/agent-run/' + encodeURIComponent(started.job_id));
    renderResult(result);
    if (result.run_kind === 'dipole_tuning') renderTuningResult(result);
  }
}
function renderTuningResult(result) {
  const rounds = result.rounds || [];
  if (rounds.length && !seenTuningRounds.has('mode')) {
    appendLog('Tuning Mode', `advisor=${result.advisor_mode || 'unknown'}，controlled_variable=${result.controlled_variable || 'dipole_arm_length_mm'}`, 'ok');
    seenTuningRounds.add('mode');
  }
  for (const item of rounds) {
    const key = 'round-' + item.round;
    if (seenTuningRounds.has(key)) continue;
    seenTuningRounds.add(key);
    appendLog(
      'Tune Round ' + item.round,
      `单臂 ${item.arm_length_mm} mm；谐振 ${item.resonance_frequency}；误差 ${item.target_error_percent}%；${item.agent_message}`,
      item.converged ? 'ok' : 'llm'
    );
  }
  if (rounds.length) {
    const last = rounds[rounds.length - 1];
    renderSParameters({frequency_unit:'GHz', selected:{frequency:last.resonance_frequency_hz / 1e9, s11_db:last.s11_db, s21_db:null}, samples:last.samples});
    document.getElementById('statusMetric').textContent = result.status;
    document.getElementById('validationMetric').textContent = `${rounds.length} tuning rounds`;
    if (!tuningCompletionLogged && result.status === 'succeeded' && last.converged) {
      appendLog('Tuning Complete', `调谐已收敛，最终谐振 ${last.resonance_frequency}，不需要启动下一轮。`, 'ok');
      tuningCompletionLogged = true;
    }
    if (!tuningCompletionLogged && result.status === 'failed') {
      appendLog('Tuning Failed', result.error || '调谐未在最大轮次内收敛。', 'err');
      tuningCompletionLogged = true;
    }
  }
  document.getElementById('rawResult').textContent = JSON.stringify(result, null, 2);
}
async function runOfflineDemo() {
  if (!currentWorkflow || currentWorkflowSource !== 'planner') await planWorkflowForDemo();
  resetSteps();
  document.getElementById('statusMetric').textContent = 'offline running';
  document.getElementById('validationMetric').textContent = 'fake adapter';
  const payload = {workflow:currentWorkflow, template_id:currentTemplateId, user_request:document.getElementById('agentRequest').value};
  const result = await api('/api/run', {method:'POST', body:JSON.stringify(payload)});
  renderResult(result);
}
renderWorkflowChrome();
renderSParameterChart([], '');
</script>
</body>
</html>
"""


def render_advanced_page() -> str:
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
        if method == "GET" and route == "/advanced":
            return _html_response(render_advanced_page())
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
        if method == "POST" and route == "/api/tune-dipole":
            return _json_response(service.tune_dipole(_json_body(body)))
        if method == "POST" and route == "/api/agent-run":
            return _json_response(service.start_agent_run(_json_body(body)), status=202)
        if method == "GET" and route.startswith("/api/agent-run/"):
            job_id = unquote(route.rsplit("/", 1)[-1])
            return _json_response(service.agent_run_status(job_id))
        if method == "POST" and route == "/api/run-real":
            return _json_response(service.start_real_run(_json_body(body)), status=202)
        if method == "GET" and route.startswith("/api/run-real/"):
            job_id = unquote(route.rsplit("/", 1)[-1])
            return _json_response(service.real_run_status(job_id))
        if method == "GET" and route == "/api/reports":
            return _json_response(service.reports())
        if method == "GET" and route.startswith("/reports/"):
            return _report_response(service.repo_root, route)
        if method == "GET" and route.startswith("/benchmarks/runs/"):
            return _artifact_response(service.repo_root, route)
        return _json_response({"error": "not_found", "path": route}, status=404)
    except Exception as exc:
        return _json_response({"error": type(exc).__name__, "message": str(exc)}, status=400)


def run_demo_server(
    host: str,
    port: int,
    repo_root: Path,
    run_dir: Path,
    *,
    planner_config: PlannerConfig | None = None,
    default_adapter: str = "fake",
    aedt_config: AedtConfig | None = None,
) -> None:
    service = DemoService(
        repo_root,
        run_dir=run_dir,
        planner_config=planner_config,
        default_adapter=default_adapter,
        aedt_config=aedt_config,
    )

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


def _artifact_response(repo_root: Path, route: str) -> tuple[int, dict[str, str], bytes]:
    relative = unquote(route.lstrip("/"))
    artifact_path = (repo_root / relative).resolve()
    runs_dir = (repo_root / "benchmarks/runs").resolve()
    if runs_dir not in artifact_path.parents or not artifact_path.is_file():
        return _json_response({"error": "artifact_not_found", "path": relative}, status=404)
    return 200, {"content-type": _content_type_for_path(artifact_path)}, artifact_path.read_bytes()


def _content_type_for_path(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json; charset=utf-8"
    if path.suffix == ".jsonl":
        return "application/x-ndjson; charset=utf-8"
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".log":
        return "text/plain; charset=utf-8"
    return "application/octet-stream"
