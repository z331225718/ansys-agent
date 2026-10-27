(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const state = {
    antipadRadius: 18, nfpRadius: 8.5, nfpEnabled: true, layer: 'L2_GND',
    zoom: 100, chart: 'tdr', previewed: false, approved: false,
    running: false, optimized: false, dirty: false, history: [],
  };
  const baseline = { antipadRadius: 18, nfpRadius: 8.5, nfpEnabled: true };
  const limits = { antipad: [12, 22], nfp: [7.875, 10] };
  const layerNames = ['L1 · TOP', 'L2 · GND', 'L3 · SIG', 'L4 · GND', 'L5 · SIG', 'L6 · GND', 'L7 · BOTTOM'];
  const fmt = (v, d = 1) => Number(v).toFixed(d);
  const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function notify(message, kind = 'success', duration = 3500) {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.textContent = message;
    $('#toast-stack').appendChild(el);
    setTimeout(() => el.remove(), duration);
  }
  function setSaved(text, dirty = state.dirty) {
    state.dirty = dirty;
    $('#save-state-text').textContent = text;
    $('#preview-status').classList.toggle('dirty', dirty);
    $('#preview-status span:last-child').textContent = dirty ? '参数已修改，等待生成预览' : '参数未修改，可预览当前基线';
  }
  function syncRadius(kind, raw) {
    const anti = kind === 'antipad';
    const value = Number(raw);
    const [min, max] = anti ? limits.antipad : limits.nfp;
    const key = anti ? 'antipadRadius' : 'nfpRadius';
    if (!Number.isFinite(value) || value < min || value > max) {
      notify((anti ? '反焊盘' : '非功能焊盘') + '半径必须在 ' + min + '–' + max + ' mil 范围内。', 'warning');
      $('#' + kind + '-radius-number').value = state[key];
      return;
    }
    state[key] = value;
    $('#' + kind + '-radius').value = value;
    $('#' + kind + '-radius-number').value = value;
    state.previewed = false;
    state.approved = false;
    setSaved('有未预览的参数修改', true);
    if (anti) {
      $('#radius-caption').textContent = 'r = ' + fmt(value) + ' mil';
      const scale = value / 18;
      $('#radius-dimension').setAttribute('transform', 'translate(' + (276 - 276 * scale) + ' 0) scale(' + scale + ' 1)');
    }
    updateCandidateSummary();
  }
  function updateCandidateSummary() {
    $('#candidate-summary').textContent = '反焊盘 r = ' + fmt(state.antipadRadius) + ' mil · ' + (state.antipadRadius > 18 ? '减小寄生电容' : '候选几何调整');
    const delta = Math.max(.3, (state.antipadRadius - 18) * .9);
    $('.history-item.next .history-metrics span:last-child').innerHTML = '预计 <b>−' + fmt(delta) + ' dB</b>';
    $('#chart-insight').textContent = state.previewed ? '候选预览 · 反焊盘 ' + fmt(state.antipadRadius) + ' mil · 等待人工审批' : '过孔阻抗下冲 · 11.0 Ω @ 20.0 ps';
  }
  ['antipad', 'nfp'].forEach(kind => {
    $('#' + kind + '-radius').addEventListener('input', e => syncRadius(kind, e.target.value));
    $('#' + kind + '-radius-number').addEventListener('change', e => syncRadius(kind, e.target.value));
  });
  $('#antipad-layer').addEventListener('change', e => {
    state.layer = e.target.value;
    state.previewed = false;
    state.approved = false;
    setSaved('参考层已修改，等待预览', true);
    $('.selected-via text:last-child').textContent = 'via_149632 · ' + state.layer;
    notify('已选择 ' + state.layer + ' 参考平面，请重新生成预览。');
  });
  $('#nfp-toggle').addEventListener('change', e => {
    state.nfpEnabled = e.target.checked;
    $('#nfp-radius').disabled = !state.nfpEnabled;
    $('#nfp-radius-number').disabled = !state.nfpEnabled;
    $('#nfp-pads').style.opacity = state.nfpEnabled ? '1' : '.12';
    state.previewed = false;
    state.approved = false;
    setSaved('NFP 设置已修改，等待预览', true);
  });
  $('#reset-antipad').addEventListener('click', () => {
    syncRadius('antipad', baseline.antipadRadius);
    notify('反焊盘已恢复基线 18.0 mil。');
  });

  function setGeometryView(view) {
    $$('[data-geometry-view]').forEach(b => b.classList.toggle('active', b.dataset.geometryView === view));
    $('#via-geometry').hidden = view !== 'cutaway';
    $('#layout-overlay').hidden = view !== 'layout';
    $('#stackup-overlay').hidden = view !== 'stackup';
    const captions = { cutaway: 'Via transition · X-ray cutaway', layout: 'TX0 → RX0 · Top-layer routing', stackup: '7-layer stackup · material & reference' };
    $('#structure-caption').textContent = captions[view];
    $('#canvas-note-text').textContent = view === 'cutaway' ? '选中反焊盘以调整几何约束' : view === 'layout' ? '差分对走线、过孔与反焊盘位置关系' : '参考层与信号层材料定义（演示）';
    applyZoom();
  }
  $$('[data-geometry-view]').forEach(b => b.addEventListener('click', () => setGeometryView(b.dataset.geometryView)));
  $$('[data-model-view]').forEach(b => b.addEventListener('click', () => {
    $$('[data-model-view]').forEach(item => item.classList.toggle('active', item === b));
    setGeometryView(b.dataset.modelView);
  }));
  $('#stackup-overlay').innerHTML = '<div class="stackup-list">' + layerNames.map((name, i) => '<div class="stackup-row ' + (i % 2 === 0 ? 'signal' : '') + '"><span>' + name + '</span><i></i><span>' + (i % 2 === 0 ? 'copper' : 'FR-4') + '</span><small>' + (i % 2 === 0 ? '35 μm' : '120 μm') + '</small></div>').join('') + '</div>';
  function applyZoom() {
    $('#zoom-label').textContent = state.zoom + '%';
    const scale = state.zoom / 100;
    $('#via-geometry').style.transform = 'scale(' + scale + ')';
    $('#layout-overlay').style.transform = 'scale(' + scale + ')';
    $('#stackup-overlay').style.transform = 'scale(' + scale + ')';
  }
  $('#zoom-in').addEventListener('click', () => { state.zoom = Math.min(160, state.zoom + 10); applyZoom(); });
  $('#zoom-out').addEventListener('click', () => { state.zoom = Math.max(60, state.zoom - 10); applyZoom(); });
  $('#fit-view').addEventListener('click', () => { state.zoom = 100; applyZoom(); notify('结构视图已适配窗口。'); });
  $('#reset-geometry').addEventListener('click', () => { state.zoom = 100; setGeometryView('cutaway'); });
  $$('[data-tool]').forEach(button => button.addEventListener('click', () => {
    $$('[data-tool]').forEach(item => item.classList.toggle('active', item === button));
    $('#canvas-note-text').textContent = button.dataset.tool === 'measure' ? '测量模式：反焊盘半径与差分过孔间距' : '选中反焊盘以调整几何约束';
    if (button.dataset.tool === 'measure') notify('当前选中尺寸：反焊盘半径 18.0 mil；差分过孔中心距 0.45 mm（演示）。');
  }));
  $('.selected-via').addEventListener('click', () => {
    $('#antipad-layer').focus();
    notify('已选中 via_149632 的反焊盘，可在右侧调整参考层与半径。');
  });
  $('#geometry-expand').addEventListener('click', () => {
    $('.geometry-panel').classList.toggle('expanded');
    $('#geometry-expand').textContent = $('.geometry-panel').classList.contains('expanded') ? '×' : '⛶';
  });
  $$('[data-inspector]').forEach(button => button.addEventListener('click', () => {
    $$('[data-inspector]').forEach(item => item.classList.toggle('active', item === button));
    const review = button.dataset.inspector === 'review';
    $('#parameters-pane').hidden = review;
    $('#review-pane').hidden = !review;
  }));


  const chartDefinitions = {
    tdr: {
      title: '差分阻抗 / TDR', subtitle: '近端过孔不连续性是主要优化对象',
      xLabel: '时间 / ps', yLabel: '阻抗 / Ω', xMax: 120, yMin: 65, yMax: 110,
      ticksX: [0, 20, 40, 60, 80, 100, 120], ticksY: [70, 80, 90, 100, 110],
      target: 90, tolerance: 9, xFormat: v => v.toFixed(0) + ' ps', yFormat: v => v.toFixed(1) + ' Ω',
      insight: '过孔阻抗下冲 · 11.0 Ω @ 20.0 ps',
      values: [[0,89.7],[5,90.5],[10,91.2],[14,90.2],[17,85.4],[20,79],[23,82.1],[26,89.8],[30,94.1],[35,96.4],[39,94.5],[44,89.4],[50,88.8],[57,90.4],[63,92.1],[69,91.1],[75,89.4],[82,89.6],[90,90.7],[98,90.2],[106,89.5],[113,90.1],[120,90.2]],
      candidate: [[0,89.7],[5,90.2],[10,90.6],[14,90.2],[17,88.1],[20,84.2],[23,86.1],[26,90.3],[30,92.3],[35,93.4],[39,92.4],[44,90.1],[50,89.2],[57,90.3],[63,91.1],[69,90.7],[75,89.8],[82,89.7],[90,90.3],[98,90.1],[106,89.8],[113,90.1],[120,90.2]],
    },
    sdd11: {
      title: '差分回波损耗 / SDD11', subtitle: '目标：0–28 GHz 频段内 ≤ −17 dB',
      xLabel: '频率 / GHz', yLabel: '幅度 / dB', xMax: 28, yMin: -32, yMax: -6,
      ticksX: [0, 4, 8, 12, 16, 20, 24, 28], ticksY: [-30, -25, -20, -15, -10],
      target: -17, tolerance: 0, xFormat: v => v.toFixed(1) + ' GHz', yFormat: v => v.toFixed(1) + ' dB',
      insight: '回波损耗最差 −14.2 dB @ 18.0 GHz · 目标差距 2.8 dB',
      values: [[0,-25],[2,-24.5],[4,-22.4],[6,-21.3],[8,-19.7],[10,-17.6],[12,-16.1],[14,-15.7],[16,-14.9],[18,-14.2],[20,-15.4],[22,-17.1],[24,-18.4],[26,-20.5],[28,-21.2]],
      candidate: [[0,-25],[2,-25],[4,-23.4],[6,-22.8],[8,-21.1],[10,-19.3],[12,-17.8],[14,-17.1],[16,-17.2],[18,-16.6],[20,-17.9],[22,-18.9],[24,-19.5],[26,-21.1],[28,-22]],
    },
    sdd21: {
      title: '差分插入损耗 / SDD21', subtitle: '通道损耗预算内，重点改善过孔反射而非盲目衰减',
      xLabel: '频率 / GHz', yLabel: '幅度 / dB', xMax: 28, yMin: -4.5, yMax: 0,
      ticksX: [0, 4, 8, 12, 16, 20, 24, 28], ticksY: [-4, -3, -2, -1, 0],
      target: -3, tolerance: 0, xFormat: v => v.toFixed(1) + ' GHz', yFormat: v => v.toFixed(2) + ' dB',
      insight: '插入损耗最差 −2.10 dB @ 26.5 GHz · 裕量 0.90 dB',
      values: [[0,-.05],[2,-.23],[4,-.39],[6,-.54],[8,-.72],[10,-.91],[12,-1.05],[14,-1.23],[16,-1.4],[18,-1.55],[20,-1.72],[22,-1.85],[24,-1.97],[26,-2.06],[26.5,-2.1],[28,-2.02]],
      candidate: [[0,-.05],[2,-.22],[4,-.38],[6,-.52],[8,-.7],[10,-.89],[12,-1.02],[14,-1.2],[16,-1.36],[18,-1.5],[20,-1.67],[22,-1.8],[24,-1.91],[26,-2],[26.5,-2.04],[28,-1.98]],
    },
  };
  function chartCoords(def, x, y) {
    return [53 + (x / def.xMax) * 607, 214 - ((y - def.yMin) / (def.yMax - def.yMin)) * 200];
  }
  function linePath(def, values) {
    return values.map(([x, y], i) => (i ? 'L' : 'M') + chartCoords(def, x, y).map(n => n.toFixed(2)).join(',')).join(' ');
  }
  function renderChart() {
    const def = chartDefinitions[state.chart];
    $('#chart-title').textContent = def.title;
    $('#chart-subtitle').textContent = def.subtitle;
    $('#chart-insight').textContent = def.insight;
    $('#chart-grid').innerHTML = [
      ...def.ticksY.map(value => {
        const y = chartCoords(def, 0, value)[1];
        return '<line class="grid-line" x1="53" x2="660" y1="' + y + '" y2="' + y + '"/><text x="43" y="' + (y + 3) + '" text-anchor="end">' + value + '</text>';
      }),
      ...def.ticksX.map(value => {
        const x = chartCoords(def, value, def.yMin)[0];
        return '<line class="grid-line" x1="' + x + '" x2="' + x + '" y1="14" y2="214"/><text x="' + x + '" y="232" text-anchor="middle">' + value + '</text>';
      }),
      '<text x="13" y="17">' + def.yLabel + '</text><text x="657" y="247" text-anchor="end">' + def.xLabel + '</text>',
    ].join('');
    const targetY = chartCoords(def, 0, def.target)[1];
    const toleranceRect = def.tolerance ? '<rect class="target-fill" x="53" y="' + chartCoords(def, 0, def.target + def.tolerance)[1] + '" width="607" height="' + Math.abs(chartCoords(def, 0, def.target - def.tolerance)[1] - chartCoords(def, 0, def.target + def.tolerance)[1]) + '"/>' : '';
    $('#chart-thresholds').innerHTML = toleranceRect + '<line class="target-line" x1="53" x2="660" y1="' + targetY + '" y2="' + targetY + '"/><text x="655" y="' + (targetY - 5) + '" text-anchor="end">' + def.target + (state.chart === 'tdr' ? ' Ω' : ' dB') + ' target</text>';
    const path = linePath(def, def.values);
    $('#chart-line').setAttribute('d', path);
    $('#chart-area').setAttribute('d', path + ' L660,214 L53,214 Z');
    $('#chart-candidate').setAttribute('d', linePath(def, def.candidate));
    $('#chart-candidate').setAttribute('opacity', state.previewed ? '.92' : '0');
    $('#candidate-legend').hidden = !state.previewed;
    const [peakX, peakY] = state.chart === 'tdr' ? [20, 79] : state.chart === 'sdd11' ? [18, -14.2] : [26.5, -2.1];
    const [ax, ay] = chartCoords(def, peakX, peakY);
    const label = state.chart === 'tdr' ? '79.0 Ω @ 20 ps' : state.chart === 'sdd11' ? '−14.2 dB @ 18 GHz' : '−2.10 dB @ 26.5 GHz';
    $('#chart-annotation').innerHTML = '<line class="annotation-line" x1="' + ax + '" x2="' + ax + '" y1="' + ay + '" y2="214"/><circle cx="' + ax + '" cy="' + ay + '" r="3" fill="#d9ad69"/><text class="annotation-text" x="' + Math.min(ax + 8, 550) + '" y="' + Math.max(ay - 10, 24) + '">' + label + '</text>';
  }
  $$('[data-chart]').forEach(button => button.addEventListener('click', () => {
    state.chart = button.dataset.chart;
    $$('[data-chart]').forEach(item => item.classList.toggle('active', item === button));
    renderChart();
  }));
  $('#chart-hit-area').addEventListener('mousemove', event => {
    const svg = $('#response-chart');
    const rect = svg.getBoundingClientRect();
    const x = (event.clientX - rect.left) * 680 / rect.width;
    if (x < 53 || x > 660) { $('#chart-crosshair').setAttribute('opacity', '0'); return; }
    const def = chartDefinitions[state.chart];
    const xValue = (x - 53) / 607 * def.xMax;
    const nearest = def.values.reduce((a, b) => Math.abs(b[0] - xValue) < Math.abs(a[0] - xValue) ? b : a);
    const [cx, cy] = chartCoords(def, ...nearest);
    const crosshair = $('#chart-crosshair');
    crosshair.setAttribute('opacity', '1');
    crosshair.setAttribute('transform', 'translate(' + cx + ',' + cy + ')');
    crosshair.querySelector('line').setAttribute('y1', String(-cy + 14));
    crosshair.querySelector('line').setAttribute('y2', String(214 - cy));
    $('#crosshair-value').textContent = def.yFormat(nearest[1]);
    $('#crosshair-x').textContent = def.xFormat(nearest[0]);
  });
  $('#chart-hit-area').addEventListener('mouseleave', () => $('#chart-crosshair').setAttribute('opacity', '0'));
  $('#chart-expand').addEventListener('click', () => {
    $('.chart-panel').classList.toggle('expanded');
    $('#chart-expand').textContent = $('.chart-panel').classList.contains('expanded') ? '×' : '⛶';
  });
  $('#chart-data-button').addEventListener('click', showBoundedMetrics);

  function changeTable() {
    const changes = [
      ['参考平面', 'L2_GND', state.layer],
      ['反焊盘半径', fmt(baseline.antipadRadius) + ' mil', fmt(state.antipadRadius) + ' mil'],
      ['NFP 信号圆', baseline.nfpEnabled ? fmt(baseline.nfpRadius) + ' mil' : 'disabled', state.nfpEnabled ? fmt(state.nfpRadius, 3) + ' mil' : 'disabled'],
      ['观察端 / 阻抗', 'Diff1 / 90 Ω', 'Diff1 / 90 Ω'],
    ];
    return '<table class="diff-table"><thead><tr><th>参数</th><th>当前结构</th><th>候选结构</th></tr></thead><tbody>' + changes.map(row => '<tr><td>' + row[0] + '</td><td class="old">' + row[1] + '</td><td class="new">' + row[2] + '</td></tr>').join('') + '</tbody></table>';
  }
  function openModal({ title, kicker = 'ENGINEERING WORKSPACE', content, buttons = [], large = false }) {
    $('#modal-title').textContent = title;
    $('#modal-kicker').textContent = kicker;
    $('#modal-content').innerHTML = content;
    $('#modal').classList.toggle('large', large);
    $('#modal-footer').innerHTML = '';
    buttons.forEach(({ label, primary = false, onClick }) => {
      const button = document.createElement('button');
      button.className = 'button ' + (primary ? 'primary' : 'secondary');
      button.textContent = label;
      button.addEventListener('click', onClick);
      $('#modal-footer').appendChild(button);
    });
    $('#modal-backdrop').hidden = false;
    document.body.classList.add('modal-open');
    $('#modal-close').focus();
  }
  function closeModal() {
    if (state.running) return;
    $('#modal-backdrop').hidden = true;
    document.body.classList.remove('modal-open');
    $$('.geometry-panel.expanded, .chart-panel.expanded').forEach(panel => panel.classList.remove('expanded'));
  }
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-backdrop').addEventListener('click', e => { if (e.target === $('#modal-backdrop')) closeModal(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeModal();
    if (event.key === '?' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) showShortcuts();
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      setSaved('所有更改已保存', false);
      notify('当前演示配置已保存在浏览器会话中。');
    }
  });


  function previewChange() {
    if (state.antipadRadius > 22 || state.nfpRadius < 7.875 || state.nfpRadius > 10) {
      notify('几何约束校验失败，请修正半径后再预览。', 'error');
      return;
    }
    state.previewed = true;
    setSaved('候选预览已生成，等待人工审批', false);
    renderChart();
    updateCandidateSummary();
    const digest = simpleDigest(JSON.stringify({ radius: state.antipadRadius, nfp: state.nfpRadius, layer: state.layer, source: 'a91c7e42' }));
    openModal({
      title: '确认候选几何修改', kicker: 'PREVIEW / HUMAN APPROVAL REQUIRED',
      content: '<p>以下变更将写入<strong>工作副本</strong>，不会修改人工审查的源工程。请先核对物理目标与证据，再批准应用。</p>' + changeTable() +
        '<h3>几何与证据检查</h3><ul class="evidence-list"><li>目标层 ' + esc(state.layer) + ' 在过孔附近具有有效参考平面 shape</li><li>圆心来自 via_149632 / via_149633 的 padstack 实例</li><li>反焊盘半径 ' + fmt(state.antipadRadius) + ' mil，满足 ≤ 22 mil</li><li>NFP ' + (state.nfpEnabled ? fmt(state.nfpRadius, 3) + ' mil，满足 [7.875, 10] mil' : '已禁用，本轮不修改') + '</li><li>TDR 观察端 Diff1 已定向至 TX 侧</li></ul><div class="callout warning">此处是交互演示预览，未连接 AEDT。生产模式将由 Harness 执行 preview → apply → native readback，失败自动回滚。</div><div class="digest-box">snapshot_digest: ' + digest + '<br>source_project: reviewed TX0_RX0.aedt (read-only)<br>working_project: run-002/working/TX0_RX0.aedt</div>',
      buttons: [
        { label: '返回修改', onClick: closeModal },
        { label: '批准并应用候选', primary: true, onClick: applyCandidate },
      ],
    });
  }
  function applyCandidate() {
    state.approved = true;
    state.optimized = false;
    closeModal();
    state.history.push({ round: 2, radius: state.antipadRadius, nfp: state.nfpRadius, status: 'approved', time: new Date().toISOString() });
    $('.history-item.next .history-status').textContent = 'APPROVED';
    $('.history-item.next .history-status').style.color = 'var(--teal)';
    $('.history-item.next small').textContent = '候选已批准 · 等待运行验证';
    $('.history-item.next .history-metrics').innerHTML = '<span>Cost <b>12.7</b></span><span>SDD11 <b>−16.6 dB</b></span>';
    $('#metric-cost').innerHTML = '12.7 <small>cost</small>';
    $('#metric-sdd11').innerHTML = '−16.6 <small>dB</small>';
    $('#metric-tdr').innerHTML = '±5.8 <small>Ω</small>';
    $('#round-label').textContent = '第 02 轮 · 候选';
    $('#footer-round').textContent = 'Round 02 / 03';
    notify('候选已批准并应用到演示工作副本；模拟 readback 已通过。');
    setSaved('候选已应用 · 待运行验证', false);
  }
  $('#preview-button').addEventListener('click', previewChange);

  function runOptimization() {
    if (state.running) return;
    if (!state.approved) {
      notify('请先生成修改预览并完成人工审批，再运行优化。', 'warning');
      previewChange();
      return;
    }
    state.running = true;
    $('#run-button').disabled = true;
    $('#run-button').innerHTML = '<span>◌</span>优化运行中';
    const stages = [
      ['检查工作副本与源工程隔离', 14],
      ['候选几何 readback 与约束校验', 31],
      ['HFSS 求解与 Touchstone 导出（演示）', 55],
      ['TDR / SDD 确定性评分', 76],
      ['保存证据与优化历史', 93],
      ['验证完成 · 未修改源工程', 100],
    ];
    openModal({
      title: '运行通道优化', kicker: 'LOCAL WORKER / DEMO RUN',
      content: '<p>正在通过受控 worker 执行候选结构验证。此演示不会调用真实 AEDT，也不会修改任何工程文件。</p><div class="progress-track"><span id="run-progress"></span></div><div class="log-box" id="run-log"></div>',
      buttons: [],
    });
    let index = 0;
    const next = () => {
      if (index >= stages.length) {
        state.running = false;
        state.optimized = true;
        $('#run-button').disabled = false;
        $('#run-button').innerHTML = '<span>↻</span>再次验证';
        $('#modal-content').innerHTML = '<div class="callout">验证完成。候选结构改善了过孔阻抗连续性，综合成本从 14.5 降至 12.7。</div><h3>本轮有界指标</h3><table class="diff-table"><thead><tr><th>指标</th><th>基线</th><th>第 02 轮</th><th>变化</th></tr></thead><tbody><tr><td>SDD11 最差</td><td class="old">−14.2 dB</td><td class="new">−16.6 dB</td><td class="new">+2.4 dB</td></tr><tr><td>TDR 峰值偏差</td><td class="old">±11.0 Ω</td><td class="new">±5.8 Ω</td><td class="new">−5.2 Ω</td></tr><tr><td>Objective cost</td><td class="old">14.5</td><td class="new">12.7</td><td class="new">−1.8</td></tr></tbody></table><ul class="evidence-list"><li>working project 隔离且模拟 readback 一致</li><li>源工程 SHA256 未改变（演示）</li><li>optimization_history.csv 已追加第 02 轮</li><li>TDR / SDD 证据与审批记录已归档</li></ul><p>当前仍未达到全频段 SDD11 ≤ −17 dB 的目标，可保留候选并评估下一轮小幅调整。</p>';
        $('#modal-footer').innerHTML = '';
        const exportBtn = document.createElement('button');
        exportBtn.className = 'button secondary';
        exportBtn.textContent = '导出本轮证据';
        exportBtn.addEventListener('click', exportReport);
        const closeBtn = document.createElement('button');
        closeBtn.className = 'button primary';
        closeBtn.textContent = '返回工作台';
        closeBtn.addEventListener('click', closeModal);
        $('#modal-footer').append(exportBtn, closeBtn);
        $('.history-item.next .history-status').textContent = 'VERIFIED';
        $('.history-item.next small').textContent = '已求解 · readback 通过 · 证据已固化';
        $('.history-item.muted').classList.remove('muted');
        $('#preview-status span:last-child').textContent = '第 02 轮验证完成，可继续评估';
        notify('第 02 轮验证完成：TDR 偏差降低 5.2 Ω，成本降至 12.7。', 'success', 5000);
        return;
      }
      const [label, progress] = stages[index++];
      $('#run-progress').style.width = progress + '%';
      const line = document.createElement('span');
      line.className = 'log-line';
      line.textContent = String(index).padStart(2, '0') + '  ' + label;
      $('#run-log').appendChild(line);
      $('#run-log').scrollTop = $('#run-log').scrollHeight;
      setTimeout(next, index === 3 ? 950 : 650);
    };
    setTimeout(next, 350);
  }
  $('#run-button').addEventListener('click', runOptimization);

  function simpleDigest(input) {
    let hash = 2166136261;
    for (let i = 0; i < input.length; i++) { hash ^= input.charCodeAt(i); hash = Math.imul(hash, 16777619); }
    return 'demo_' + (hash >>> 0).toString(16).padStart(8, '0') + String(input.length).padStart(4, '0');
  }
  function exportReport() {
    const report = {
      schema: 'si-forge-demo-report/v1',
      generated_at: new Date().toISOString(),
      demo: true,
      notice: '交互演示数据，不是 AEDT 仿真结果，不可作为生产验收证据。',
      channel: 'TX0 → RX0', protocol: '112G PAM4',
      source_project: { path: 'reviewed/TX0_RX0.aedt', read_only: true, sha256: 'a91c…7e42' },
      working_project: { isolated: true, path: 'run-002/working/TX0_RX0.aedt' },
      baseline: { anti_pad_radius_mil: 18, nfp_radius_mil: 8.5, sdd11_worst_db: -14.2, sdd11_frequency_ghz: 18, sdd21_worst_db: -2.1, tdr_peak_deviation_ohm: 11, tdr_peak_time_ps: 20, objective_cost: 14.5 },
      candidate: { anti_pad_radius_mil: state.antipadRadius, nfp_radius_mil: state.nfpEnabled ? state.nfpRadius : null, layer: state.layer, approved: state.approved, verified: state.optimized, sdd11_worst_db: state.approved ? -16.6 : null, tdr_peak_deviation_ohm: state.approved ? 5.8 : null, objective_cost: state.approved ? 12.7 : null },
      constraints: { anti_pad_radius_max_mil: 22, nfp_radius_range_mil: [7.875, 10], tdr_observation_port: 'Diff1', orientation: 'TX side', differential_reference_ohm: 90 },
      history: state.history,
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'si-forge-round-02-evidence.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('已导出有界演示证据 JSON；未包含原始曲线或工程文件。');
  }
  $('#export-button').addEventListener('click', exportReport);

  function showBoundedMetrics() {
    const def = chartDefinitions[state.chart];
    openModal({
      title: '有界通道指标', kicker: 'ARTIFACT-ONLY CURVES / BOUNDED SUMMARY',
      content: '<p>工作台只将关键指标送入分析上下文；原始曲线作为 artifact 保存，不粘贴完整采样数组。</p><table class="diff-table"><tbody><tr><td>当前视图</td><td class="new">' + def.title + '</td></tr><tr><td>观察端</td><td class="new">Diff1 → TX 侧（已定向）</td></tr><tr><td>频率 / 时间范围</td><td class="new">' + (state.chart === 'tdr' ? '0–120 ps' : '0–28 GHz') + '</td></tr><tr><td>最差值</td><td class="new">' + (state.chart === 'tdr' ? '79.0 Ω @ 20 ps' : state.chart === 'sdd11' ? '−14.2 dB @ 18.0 GHz' : '−2.10 dB @ 26.5 GHz') + '</td></tr><tr><td>参考阻抗</td><td class="new">90 Ω differential</td></tr><tr><td>artifact SHA256</td><td class="new">demo_7e42a91c</td></tr></tbody></table><div class="callout">指标用于定位候选物理结构；若需把 TDR 峰值映射到过孔，必须先确认观察端方向。</div>',
      buttons: [{ label: '关闭', primary: true, onClick: closeModal }],
    });
  }
  function showShortcuts() {
    openModal({ title: '工作台快捷键', content: '<div class="shortcut-grid"><span>保存当前演示配置</span><kbd>Ctrl / ⌘ + S</kbd><span>关闭对话框或展开视图</span><kbd>Esc</kbd><span>打开快捷键帮助</span><kbd>?</kbd><span>预览参数修改</span><kbd>右侧「生成修改预览」</kbd><span>切换响应曲线</span><kbd>TDR / SDD11 / SDD21</kbd></div>', buttons: [{ label: '知道了', primary: true, onClick: closeModal }] });
  }
  $('#shortcuts-button').addEventListener('click', showShortcuts);
  $('#notification-button').addEventListener('click', () => openModal({ title: '工程通知', content: '<ul class="evidence-list"><li>源模型已通过人工审查，working project 已隔离</li><li>候选动作约束已加载：anti-pad ≤ 22 mil，NFP 7.875–10 mil</li><li>本地 AEDT 2026.1 gRPC 可用（状态为演示）</li></ul>', buttons: [{ label: '关闭', primary: true, onClick: closeModal }] }));
  $('#heading-more').addEventListener('click', () => openModal({ title: '项目操作', content: '<div class="setting-row"><span>工程</span><strong>TX0 → RX0 / 112G PAM4</strong></div><div class="setting-row"><span>源工程</span><strong>已审查 · 只读</strong></div><div class="setting-row"><span>工作副本</span><strong>run-002 / isolated</strong></div><div class="callout">此原型仅操作浏览器内演示状态，不连接 AEDT 或生产 Mission。</div>', buttons: [{ label: '关闭', primary: true, onClick: closeModal }] }));
  $('#workspace-button').addEventListener('click', () => openModal({ title: '选择工作空间', content: '<div class="callout">当前工作空间：<strong>SerDes Lab</strong><br>本地 AEDT 工作站 · 112G 通道优化</div><p>本原型只包含一个演示工作空间。生产版本可从 Mission/Graph 数据库读取项目列表。</p>', buttons: [{ label: '继续使用 SerDes Lab', primary: true, onClick: closeModal }] }));
  $('#user-card').addEventListener('click', () => openModal({ title: '工程身份', content: '<div class="setting-row"><span>工程师</span><strong>Z. Lin</strong></div><div class="setting-row"><span>角色</span><strong>SI / PI Engineer</strong></div><div class="setting-row"><span>审批权限</span><strong>Human gate / reviewed</strong></div><p>审批身份将绑定到每次候选几何修改的审计记录中。</p>', buttons: [{ label: '关闭', primary: true, onClick: closeModal }] }));
  $('#review-evidence-button').addEventListener('click', () => openModal({ title: '源模型审查证据', kicker: 'HUMAN-REVIEWED SOURCE', content: '<ul class="evidence-list"><li>四端口差分模型 · Touchstone s4p</li><li>端口位置与参考关系已人工确认</li><li>L2/L4/L6 参考平面 shape 证据完整</li><li>Diff1 观察端定向至 TX 侧</li><li>源工程与工作副本路径分离</li><li>几何半径与 NFP 工艺约束已加载</li></ul><div class="digest-box">source_sha256: a91c…7e42<br>reviewer: Z. Lin<br>review_status: approved<br>scope: TX0_RX0 via transition</div>', buttons: [{ label: '关闭', primary: true, onClick: closeModal }] }));
  $$('[data-view]').forEach(button => button.addEventListener('click', () => {
    const view = button.dataset.view;
    if (view === 'workbench') return;
    const viewInfo = {
      models: ['模型库', '已审查模型与工作副本', ['TX0 → RX0 · reviewed', 'RX1 → Connector · reviewed', 'TX2 → Backplane · draft', 'Training via · sandbox']],
      patterns: ['Via 模式库', '从实际板级证据归类的过孔结构', ['L3 + BGA + P100 · 8 pairs', 'L5 + Connector + P85 · 6 pairs', 'L7 + BGA + P100 · 4 pairs', 'L2 + Backplane + P110 · 3 pairs']],
      history: ['优化历史', '可追溯的轮次与指标变化', ['Round 01 · baseline · cost 14.5', 'Round 02 · approved candidate · cost 12.7', 'Round 03 · awaiting evidence']],
      evidence: ['证据与报告', 'Bounded evidence / artifact-only curves', ['optimization_history.csv', 'optimization_progress.html', 'source_integrity.json', 'approval_manifest.json']],
      settings: ['工作站设置', '本地执行环境与安全边界', ['AEDT 2026.1 · gRPC connected', 'simulation_runner = local_cli', 'max_concurrent_aedt = 1', 'source project = read-only']],
    };
    const [title, subtitle, items] = viewInfo[view];
    openModal({ title, kicker: subtitle.toUpperCase(), content: '<p>' + subtitle + '。以下为演示导航入口，实际数据将在生产集成时读取 Mission/Graph 与 artifact store。</p><ul class="evidence-list">' + items.map(item => '<li>' + item + '</li>').join('') + '</ul><div class="callout">返回「通道优化」继续当前 TX0 → RX0 工作流。</div>', buttons: [{ label: '返回工作台', primary: true, onClick: closeModal }] });
  }));
  $('#history-button').addEventListener('click', () => $('[data-view="history"]').click());

  function assistantReply(prompt) {
    const text = prompt.toLowerCase();
    if (text.includes('tdr') || text.includes('峰值') || text.includes('阻抗')) {
      return 'Diff1 已确认观察 TX 侧。20 ps 附近的 79.0 Ω 下冲与过孔过渡区相符，但不能只凭时间位置指定物理层。建议结合 L2/L4/L6 参考平面 shape 和 padstack 圆心，先验证增大反焊盘的影响。';
    }
    if (text.includes('半径') || text.includes('下一轮') || text.includes('建议') || text.includes('优化')) {
      return '建议先把反焊盘从 18.0 mil 调整为 20.0 mil，保持 NFP 8.5 mil 不变，以单变量评估寄生电容改善。该值满足 ≤ 22 mil。请通过“生成修改预览”核对 shape、圆心和观察端后，再人工批准。';
    }
    if (text.includes('sdd') || text.includes('回波') || text.includes('插入')) {
      return '当前 SDD11 最差为 −14.2 dB @ 18.0 GHz，距离 −17 dB 目标仍差 2.8 dB；SDD21 最差为 −2.10 dB，通道损耗在预算内。优先降低反射，而不是增加链路衰减。';
    }
    if (text.includes('证据') || text.includes('审查') || text.includes('源工程')) {
      return '源工程保持只读，候选只写入隔离 working project。演示流程包含快照摘要、人工批准、模拟 readback 和优化历史；生产模式必须由 Harness 返回真实回读与失败回滚证据。';
    }
    return '我目前聚焦 TX0 → RX0 的差分过孔优化。你可以询问“TDR 峰值原因”“下一轮反焊盘半径”或“SDD11 为什么未达标”。我只会提供有界工程建议，修改仍需通过预览和人工审批。';
  }
  function appendAssistantMessage(text, user = false) {
    const article = document.createElement('article');
    article.className = 'assistant-message ' + (user ? 'assistant-user' : 'assistant-note');
    article.textContent = text;
    $('#assistant-messages').appendChild(article);
    $('#assistant-messages').scrollTop = $('#assistant-messages').scrollHeight;
  }
  function askAssistant(prompt) {
    const text = String(prompt || '').trim();
    if (!text) { notify('请输入要分析的工程问题。', 'warning'); return; }
    appendAssistantMessage(text, true);
    $('#assistant-input').value = '';
    const thinking = document.createElement('div');
    thinking.className = 'assistant-thinking';
    thinking.textContent = '正在结合当前几何与指标分析…';
    $('#assistant-messages').appendChild(thinking);
    $('#assistant-messages').scrollTop = $('#assistant-messages').scrollHeight;
    setTimeout(() => { thinking.remove(); appendAssistantMessage(assistantReply(text)); }, 550);
  }
  $('#assistant-form').addEventListener('submit', event => { event.preventDefault(); askAssistant($('#assistant-input').value); });
  $('#assistant-input').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); askAssistant(event.target.value); }
  });
  $$('[data-prompt]').forEach(button => button.addEventListener('click', () => askAssistant(button.dataset.prompt)));
  $$('[data-assistant-action]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.assistantAction === 'preview') previewChange();
    else askAssistant('解释 TDR 峰值和下一轮优化依据');
  }));
  $('#assistant-collapse').addEventListener('click', () => {
    const collapsed = $('.assistant-panel').classList.toggle('assistant-collapsed');
    $$('.assistant-context, .assistant-messages, .assistant-composer').forEach(el => { el.hidden = collapsed; });
    $('#assistant-collapse').textContent = collapsed ? '+' : '×';
  });
  $('#assistant-context-button').addEventListener('click', () => notify('当前助手上下文：TX0 → RX0、差分过孔对、Diff1 / 90 Ω、L2/L4/L6 参考平面。'));


  const connection={connected:false,graphRunId:'',snapshot:null,missionId:''};
  const apiBase=()=>location.pathname.startsWith('/workbench')?'':'http://127.0.0.1:8766';
  async function fetchJson(path){const r=await fetch(apiBase()+path,{headers:{Accept:'application/json'}});if(!r.ok)throw new Error('项目接口 '+r.status+': '+(await r.text()).slice(0,160));return r.json();}
  const textOr=(v,f)=>v===null||v===undefined||v===''?f:String(v);
  const numOr=(v,f,d=1)=>v===null||v===undefined||v===''||Number.isNaN(Number(v))?f:Number(v).toFixed(d);
  const setText=(s,v)=>{const e=$(s);if(e)e.textContent=v;};
  function showConnectionDialog(){openModal({title:'连接 SI 工程项目',kicker:'MISSION / GRAPH · READ ONLY',content:'<p>连接现有 dashboard 的 Mission SQLite 数据库。只读取 graph、worker 评分与 artifact，不启动求解、不推进 graph、不批准人工门。</p><div class="digest-box">只读连接 · 真实数据标记 LIVE<br>无 inventory 不伪造模型<br>演示数据只在独立模式使用</div><div id="mission-list"><p>正在读取 Mission 列表…</p></div>',buttons:connection.connected?[{label:'断开并重新载入演示页',onClick:()=>location.reload()}]:[{label:'重新读取',primary:true,onClick:loadMissionList}]});loadMissionList();}
  async function loadMissionList(){const c=$('#mission-list');if(!c)return;c.innerHTML='<p>正在读取 Mission 列表…</p>';try{const d=await fetchJson('/api/workbench/missions'),ms=d.missions||[];if(!ms.length){c.innerHTML='<div class="callout warning">当前数据库没有 Mission。</div>';return;}c.innerHTML=ms.map(m=>'<button class="mission-choice" data-graph-id="'+esc(m.graph_run_id)+'"><span><strong>'+esc(m.goal)+'</strong><small>'+esc(m.mission_id)+' · '+esc(m.graph_status||m.state)+'</small></span><span>读取 →</span></button>').join('');$$('[data-graph-id]').forEach(b=>b.addEventListener('click',()=>connectGraph(b.dataset.graphId)));}catch(e){c.innerHTML='<div class="callout warning">无法连接 dashboard：'+esc(e.message)+'</div>';}}
  async function connectGraph(id){if(!id){notify('该 Mission 尚无 graph run。','warning');return;}try{const s=await fetchJson('/api/workbench/graphs/'+encodeURIComponent(id));Object.assign(connection,{connected:true,graphRunId:id,snapshot:s,missionId:s.mission.mission_id});closeModal();applySnapshot(s);notify('已连接真实 Mission / Graph，当前为只读观察模式。');}catch(e){notify('读取 graph 失败：'+e.message,'error',5000);}}
  function applySnapshot(s){
    const m=s.metrics||{},g=s.graph||{},src=s.source||{},c=s.constraints||{};
    $('#connection-badge').textContent='LIVE';$('#connection-badge').classList.add('live-badge');document.body.classList.add('project-connected');
    $('#save-state-text').textContent='真实 Mission · 只读观察';$('.breadcrumb strong').textContent=s.mission.goal||g.graph_run_id;$('.page-heading h1 > span:last-child').textContent=s.mission.goal||g.graph_run_id;$('.heading-kicker').textContent='MISSION / '+(g.template_id||'graph')+' · '+g.status;$('#assistant-context-status').textContent='真实 graph · 只读观察';$('#assistant-context-button').textContent=(s.mission.goal||g.graph_run_id)+' ⌄';$('#footer-round').textContent='Step '+(g.step_count||0)+' / '+(g.max_steps||'—');$('.page-footer code').textContent=src.source_sha256?'worker-reported '+src.source_sha256:'hash unavailable';$('.page-footer code').title='hash 由 worker 上报，读取时未重算';
    setText('#metric-sdd11',m.rl_worst_db==null||m.rl_worst_db===''?'—':numOr(m.rl_worst_db,'—').replace('-','−')+' dB');setText('#metric-sdd21',m.insertion_worst_db_in_band==null||m.insertion_worst_db_in_band===''?'—':numOr(m.insertion_worst_db_in_band,'—',2).replace('-','−')+' dB');setText('#metric-tdr',m.tdr_peak_deviation_ohm==null||m.tdr_peak_deviation_ohm===''?'—':'±'+numOr(m.tdr_peak_deviation_ohm,'—')+' Ω');setText('#metric-cost',m.objective_total_cost==null||m.objective_total_cost===''?'—':numOr(m.objective_total_cost,'—')+' cost');setText('#metric-rl-frequency',textOr(m.rl_worst_frequency_ghz,'—'));setText('#metric-tdr-time',textOr(m.tdr_peak_time_ps,'—'));setText('#metric-il-frequency',textOr(m.insertion_worst_frequency_ghz,'—'));$('#round-label').textContent='第 '+textOr(m.round_index,'—')+' 轮';
    $('#run-button').innerHTML='<span>↻</span>刷新项目';$('#run-button').title='只读刷新，不启动求解或推进 graph';$('#production-actions').hidden=true;$$('.metric-target').forEach(x=>{x.innerHTML='<span>目标见 Mission 配置</span><span>真实评分</span>';});
    const cp=$('.chart-panel'),plots=(s.artifacts||[]).filter(a=>String(a.kind||'').startsWith('plot')&&a.view_url);
    cp.innerHTML=plots.length?'<div class="panel-header"><div class="panel-title"><span class="panel-symbol">∿</span><h2>通道响应</h2><span class="subtle-badge">LIVE ARTIFACTS</span></div></div><div class="real-plots">'+plots.map(a=>'<figure><figcaption>'+esc(a.kind)+'</figcaption><a href="'+esc(a.view_url)+'" target="_blank"><img src="'+esc(a.view_url)+'" alt="'+esc(a.kind)+'"></a></figure>').join('')+'</div>':'<div class="panel-header"><div class="panel-title"><span class="panel-symbol">∿</span><h2>通道响应</h2><span class="subtle-badge">READ ONLY</span></div></div><div class="chart-unavailable"><span>◇</span><h3>真实曲线 artifact 未注册</h3><p>当前 Mission 只有有界指标和历史记录，没有已注册的 TDR / SDD11 / SDD21 plot artifact。</p><small>连接模式不使用演示曲线；报告中的数值来自真实 worker 评分。</small></div>';
    renderRounds(s.rounds||[]);renderModel(s.model||{});renderConstraints(c);
    const note=$('.assistant-note');if(note)note.innerHTML='<span class="message-label">真实工程分析 · READ ONLY</span><p>Mission <strong>'+esc(s.mission.mission_id)+'</strong> 当前状态为 <strong>'+esc(g.status)+'</strong>。读取的是 worker 与 artifact 的真实结果，不使用演示指标。</p><p>SDD11：'+esc(textOr(m.rl_worst_db,'暂无'))+' dB；TDR 峰值偏差：'+esc(textOr(m.tdr_peak_deviation_ohm,'暂无'))+' Ω；观察端：'+esc(textOr(m.tdr_observation_port,'未提供'))+'。</p><p>此界面只读，不启动/advance graph，不批准人工门。</p>';
  }
  function renderRounds(rs){const c=$('#history-list');c.innerHTML=rs.length?rs.map((r,i)=>'<article class="history-item"><div class="round-marker"><span>'+String(r.round_index||i+1).padStart(2,'0')+'</span><i></i></div><div class="history-content"><div><strong>'+esc(r.action_type||'通道评分')+'</strong><span class="history-status baseline">'+esc(r.status||'recorded')+'</span></div><p>'+esc(r.layers||'worker evidence')+'</p><div class="history-metrics"><span>Cost <b>'+esc(textOr(r.objective_total_cost,'—'))+'</b></span><span>SDD11 <b>'+esc(textOr(r.rl_worst_db,'—'))+' dB</b></span></div><small>'+esc(r.solve_status||'')+' · '+esc(r.score_status||'')+'</small></div></article>').join(''):'<div class="callout warning">该 graph 尚无优化历史 artifact。</div>';}
  function renderModel(m){const stage=$('#geometry-stage');if(m.available){stage.innerHTML='<div class="model-unavailable real-model"><h3>真实模型 inventory</h3><p>以下数据来自当前 Mission 的 model_inventory，不是演示几何。</p><pre>'+esc(JSON.stringify(m.inventory,null,2).slice(0,7000))+'</pre></div>';$$('.model-thumb').forEach(b=>{b.disabled=true;});return;}stage.innerHTML='<div class="model-unavailable"><span>◇</span><h3>几何预览未接入</h3><p>'+esc(m.notice||'当前 graph/report 未提供几何 inventory。')+'</p><small>仅显示真实 graph、评分和 artifact，不使用演示几何冒充实际模型。</small></div>';$$('.model-thumb').forEach(b=>{b.disabled=true;});}
  function renderConstraints(c){$('#parameters-pane').innerHTML='<div class="inspector-section"><div class="section-label">真实运行约束 <span>READ ONLY</span></div><div class="observation-row"><span>TDR 观察端</span><strong>'+esc(textOr(c.tdr_observation_port,'未提供'))+'</strong></div><div class="observation-row"><span>参考阻抗</span><strong>'+esc(textOr(c.reference_impedance_ohm,'—'))+' Ω</strong></div><div class="observation-row"><span>频率范围</span><strong>'+esc(textOr(c.frequency_start_ghz,'0'))+'–'+esc(textOr(c.frequency_stop_ghz,'—'))+' GHz</strong></div><div class="observation-row"><span>反焊盘半径上限</span><strong>'+esc(textOr(c.anti_pad_max_radius_mil,'—'))+' mil</strong></div><div class="observation-row"><span>NFP 半径范围</span><strong>'+esc(textOr(c.nfp_min_radius_mil,'—'))+'–'+esc(textOr(c.nfp_max_radius_mil,'—'))+' mil</strong></div></div><div class="inspector-section"><h3>证据与报告</h3><div id="evidence-artifact-links" class="artifact-links"></div><div class="callout">写入、preview/apply 和 human_gate 审批仍由受控本地 CLI/dashboard 完成。</div></div>';renderArtifacts((connection.snapshot||{}).artifacts||[]);}
  function renderArtifacts(as){as=Array.isArray(as)?as:[];const c=$('#evidence-artifact-links');if(!c)return;const rs=as.filter(a=>['report_html','history_csv','score_evidence','plot','plot_tdr','plot_sdd11','plot_sdd21','touchstone'].includes(a.kind));c.innerHTML=rs.length?rs.map(a=>'<a href="'+esc(a.view_url)+'" target="_blank" rel="noopener">'+esc(a.kind)+' · '+esc(a.path.split(/[\\/]/).pop())+' ↗</a>').join(''):'<span>未注册可查看的报告 artifact</span>';}
  function showLiveReport(){const as=((connection.snapshot||{}).artifacts||[]).filter(a=>a.view_url);openModal({title:'真实工程证据与报告',kicker:'MISSION ARTIFACTS · READ ONLY',content:'<p>链接沿用 dashboard 的 mission/path 权限检查。</p><div class="artifact-links large-links">'+(as.length?as.map(a=>'<a href="'+esc(a.view_url)+'" target="_blank" rel="noopener"><strong>'+esc(a.kind)+'</strong><span>'+esc(a.path)+'</span><b>打开 ↗</b></a>').join(''):'<div class="callout warning">当前没有可读取的 artifact。</div>')+'</div>',buttons:[{label:'关闭',primary:true,onClick:closeModal}]});}
  function liveReply(q){const s=connection.snapshot||{},m=s.metrics||{},g=s.graph||{},t=q.toLowerCase();if(t.includes('状态')||t.includes('进度')||t.includes('运行'))return '真实 graph '+g.graph_run_id+' 当前为 '+g.status+'，step '+(g.step_count||0)+'/'+(g.max_steps||'—')+'。这里只读显示，不启动或推进 Mission。';if(t.includes('证据')||t.includes('报告')||t.includes('artifact'))return '当前 Mission 注册了 '+(s.artifacts||[]).length+' 个 artifact。';if(t.includes('tdr')||t.includes('阻抗')||t.includes('峰值'))return '真实 TDR：峰值偏差 '+textOr(m.tdr_peak_deviation_ohm,'暂无')+' Ω，时间 '+textOr(m.tdr_peak_time_ps,'暂无')+' ps，观察端 '+textOr(m.tdr_observation_port,'未提供')+'。';if(t.includes('sdd')||t.includes('回波')||t.includes('插入'))return '真实评分：RL 最差 '+textOr(m.rl_worst_db,'暂无')+' dB；IL 最差 '+textOr(m.insertion_worst_db_in_band,'暂无')+' dB。';if(t.includes('模型')||t.includes('几何')||t.includes('结构'))return (s.model||{}).available?'当前有几何 inventory。':'当前 graph/report 没有几何 inventory，不使用演示几何冒充实际模型。';return '当前读取真实 Mission '+s.mission.mission_id+'，graph 状态为 '+g.status+'。';}
  function liveAsk(q){const a=document.createElement('article');a.className='assistant-message assistant-user';a.textContent=q;$('#assistant-messages').appendChild(a);$('#assistant-input').value='';setTimeout(()=>{const b=document.createElement('article');b.className='assistant-message assistant-note';b.textContent=liveReply(q);$('#assistant-messages').appendChild(b);$('#assistant-messages').scrollTop=$('#assistant-messages').scrollHeight;},250);}
  document.addEventListener('submit',e=>{if(connection.connected&&e.target.id==='assistant-form'){e.preventDefault();e.stopImmediatePropagation();liveAsk($('#assistant-input').value);}},true);
  document.addEventListener('click',e=>{if(!connection.connected)return;const t=e.target.closest('#export-button,#history-button,#run-button,[data-prompt]');if(!t)return;e.preventDefault();e.stopImmediatePropagation();if(t.id==='run-button')connectGraph(connection.graphRunId);else if(t.id==='export-button'||t.id==='history-button')showLiveReport();else if(t.dataset.prompt)liveAsk(t.dataset.prompt);},true);
  $('#connect-button').addEventListener('click',showConnectionDialog);



  // ── workspace / task management (P2) ───────────────────────────────────
  // A workspace is one project directory; every optimization task created in
  // it owns its own missions.db. This block is additive: the legacy demo and
  // the read-only "connect project" path above keep working unchanged.
  const wf = { workspaces: [], workspaceId: "", tasks: [], taskId: "", snapshot: null, approvals: [], busy: false, timer: null };

  async function wfJson(path, options) {
    const opts = Object.assign({}, options || {});
    const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    opts.headers = headers;
    const response = await fetch(apiBase() + path, opts);
    let payload = null;
    try { payload = await response.json(); } catch (error) { payload = null; }
    if (!response.ok) {
      const detail = payload && (payload.error || payload.code) ? (payload.error || payload.code) : ("HTTP " + response.status);
      const failure = new Error(detail);
      failure.payload = payload;
      throw failure;
    }
    return payload || {};
  }

  function wfHint(text, kind) {
    const el = $("#workspace-hint");
    if (!el) return;
    el.textContent = text;
    el.className = "workspace-hint" + (kind ? " " + kind : "");
    el.hidden = text === "";
  }

  function workspaceById(id) { return wf.workspaces.find(function (item) { return item.workspace_id === id; }) || null; }
  function taskById(id) { return wf.tasks.find(function (item) { return item.task_id === id; }) || null; }

  function taskBadge(status) {
    const table = { succeeded: ["ok", "完成"], failed: ["bad", "失败"], running: ["live", "运行中"], waiting_approval: ["warn", "待审批"], canceled: ["idle", "已取消"], created: ["idle", "已创建"] };
    return table[status] || ["idle", status || "未知"];
  }

  function renderWorkspaces() {
    const list = $("#workspace-list");
    if (!list) return;
    if (!wf.workspaces.length) list.innerHTML = "";
    else {
      list.innerHTML = wf.workspaces.map(function (item) {
        const active = item.workspace_id === wf.workspaceId ? " active" : "";
        return "<button type=\"button\" class=\"workspace-item" + active + "\" data-workspace-id=\"" + esc(item.workspace_id) + "\" title=\"" + esc(item.root || "") + "\">" +
          "<strong>" + esc(item.name || item.root || item.workspace_id) + "</strong>" +
          "<small>" + esc(item.root || "") + "</small></button>";
      }).join("");
      $$("[data-workspace-id]").forEach(function (button) {
        button.addEventListener("click", function () { selectWorkspace(button.dataset.workspaceId); });
      });
    }
    const toggle = $("#new-task-button");
    if (toggle) toggle.disabled = wf.workspaceId === "";
    if (!wf.workspaces.length) wfHint("还没有工作区。点 ＋ 选择一个工程目录。");
    else if (wf.workspaceId === "") wfHint("选择一个工作区查看它的优化任务。");
    else wfHint("");
  }

  function renderTasks() {
    const list = $("#task-list");
    if (!list) return;
    if (!wf.workspaceId) { list.innerHTML = ""; return; }
    if (!wf.tasks.length) { list.innerHTML = "<p class=\"task-empty\">该工作区还没有任务。点 ＋ 新建。</p>"; return; }
    list.innerHTML = wf.tasks.map(function (item) {
      const badge = taskBadge(item.status);
      const active = item.task_id === wf.taskId ? " active" : "";
      return "<button type=\"button\" class=\"task-item" + active + "\" data-task-id=\"" + esc(item.task_id) + "\">" +
        "<span class=\"task-badge " + badge[0] + "\">" + esc(badge[1]) + "</span>" +
        "<strong>" + esc(item.name || item.goal || item.task_id) + "</strong>" +
        "<small>" + esc(item.template_id || "") + "</small></button>";
    }).join("");
    $$("[data-task-id]").forEach(function (button) {
      button.addEventListener("click", function () { selectTask(button.dataset.taskId); });
    });
  }

  function renderStatusbar() {
    const bar = $("#task-statusbar");
    if (!bar) return;
    const task = wf.taskId ? taskById(wf.taskId) : null;
    if (!task) { bar.hidden = true; return; }
    bar.hidden = false;
    const snapshot = wf.snapshot || {};
    const graph = snapshot.graph || {};
    const driver = snapshot.driver || {};
    const badge = taskBadge(task.status);
    const dot = $("#task-status-dot");
    if (dot) dot.className = "task-status-dot " + badge[0];
    const title = $("#task-status-title");
    if (title) title.textContent = task.name || task.task_id;
    const meta = $("#task-status-meta");
    if (meta) {
      const parts = [];
      parts.push("任务 " + (task.status || "未知"));
      if (graph.status) parts.push("graph " + graph.status);
      if (graph.step_count !== undefined) parts.push("step " + graph.step_count + "/" + (graph.max_steps === undefined ? "—" : graph.max_steps));
      if (driver.running) parts.push("自动推进中");
      parts.push(task.task_id);
      meta.textContent = parts.join("  ·  ");
    }
    const pending = wf.approvals.filter(function (item) { return item.decision === "pending" || item.decision === null || item.decision === undefined; });
    const advance = $("#task-advance");
    if (advance) advance.disabled = Boolean(wf.busy) || Boolean(driver.running);
    const auto = $("#task-auto");
    if (auto) auto.hidden = Boolean(driver.running);
    const stopAuto = $("#task-stop-auto");
    if (stopAuto) stopAuto.hidden = !driver.running;
    const approve = $("#task-approve");
    if (approve) approve.hidden = pending.length === 0;
    const reject = $("#task-reject");
    if (reject) reject.hidden = pending.length === 0;
  }

  async function refreshWorkspaces() {
    try {
      const payload = await wfJson("/api/workbench/workspaces");
      wf.workspaces = payload.workspaces || [];
      if (wf.workspaceId && !workspaceById(wf.workspaceId)) wf.workspaceId = "";
      renderWorkspaces();
      if (wf.workspaceId) await refreshTasks();
    } catch (error) {
      wf.workspaces = [];
      renderWorkspaces();
      wfHint("无法连接工作台服务：" + error.message + "（静态预览或 dashboard 未启动）", "warn");
    }
  }

  async function selectWorkspace(workspaceId) {
    wf.workspaceId = workspaceId;
    wf.taskId = "";
    wf.snapshot = null;
    wf.approvals = [];
    stopTaskPolling();
    renderWorkspaces();
    renderStatusbar();
    await refreshTasks();
  }

  async function refreshTasks() {
    if (!wf.workspaceId) { wf.tasks = []; renderTasks(); return; }
    try {
      const payload = await wfJson("/api/workbench/workspaces/" + encodeURIComponent(wf.workspaceId) + "/tasks");
      wf.tasks = payload.tasks || [];
      renderTasks();
      if (wf.taskId && !taskById(wf.taskId)) { wf.taskId = ""; wf.snapshot = null; wf.approvals = []; }
      renderStatusbar();
    } catch (error) {
      wf.tasks = [];
      renderTasks();
      notify("读取任务失败：" + error.message, "error", 5000);
    }
  }

  async function selectTask(taskId) {
    wf.taskId = taskId;
    wf.approvals = [];
    renderTasks();
    await refreshSnapshot();
    await refreshApprovals();
    startTaskPolling();
  }

  async function refreshSnapshot() {
    if (!wf.taskId) return;
    try {
      const snapshot = await wfJson("/api/workbench/tasks/" + encodeURIComponent(wf.taskId));
      wf.snapshot = snapshot;
      if (snapshot.task) {
        const index = wf.tasks.findIndex(function (item) { return item.task_id === snapshot.task.task_id; });
        if (index >= 0) wf.tasks[index] = snapshot.task; else wf.tasks.push(snapshot.task);
        renderTasks();
      }
      // applySnapshot's later sections read connection.snapshot (artifacts).
      connection.connected = true;
      connection.snapshot = snapshot;
      if (snapshot.graph) connection.graphRunId = snapshot.graph.graph_run_id || "";
      if (snapshot.mission) connection.missionId = snapshot.mission.mission_id || "";
      applySnapshot(snapshot);
      renderStatusbar();
    } catch (error) {
      notify("读取任务失败：" + error.message, "error", 5000);
    }
  }

  async function refreshApprovals() {
    if (!wf.taskId) return;
    try {
      const payload = await wfJson("/api/workbench/tasks/" + encodeURIComponent(wf.taskId) + "/approvals");
      wf.approvals = payload.approvals || [];
    } catch (error) {
      wf.approvals = [];
    }
    renderStatusbar();
  }

  async function withBusy(action, label) {
    if (wf.busy) return;
    wf.busy = true;
    renderStatusbar();
    try { await action(); notify(label, "success"); }
    catch (error) { notify(label + "失败：" + error.message, "error", 6000); }
    finally { wf.busy = false; renderStatusbar(); }
  }

  function advanceTask() {
    if (!wf.taskId) return;
    withBusy(async function () {
      await wfJson("/api/workbench/tasks/" + encodeURIComponent(wf.taskId) + "/advance", { method: "POST", json: {} });
      await refreshSnapshot();
      await refreshApprovals();
      await refreshTasks();
    }, "已推进一个节点");
  }

  function setAuto(enabled) {
    if (!wf.taskId) return;
    withBusy(async function () {
      await wfJson("/api/workbench/tasks/" + encodeURIComponent(wf.taskId) + "/auto", { method: "POST", json: { enabled: enabled ? true : false, poll_seconds: 5 } });
      await refreshSnapshot();
    }, enabled ? "已启动自动推进" : "已请求停止自动推进");
  }

  function decideTask(decision) {
    if (!wf.taskId) return;
    const pending = wf.approvals.filter(function (item) { return item.decision === "pending" || item.decision === null || item.decision === undefined; });
    if (!pending.length) { notify("当前没有待审批项。", "warning"); return; }
    const approvalId = pending[pending.length - 1].approval_id;
    withBusy(async function () {
      await wfJson("/api/workbench/tasks/" + encodeURIComponent(wf.taskId) + "/decide/" + encodeURIComponent(approvalId), { method: "POST", json: { decision: decision } });
      await refreshSnapshot();
      await refreshApprovals();
      await refreshTasks();
    }, decision === "approved" ? "已批准" : "已拒绝");
  }

  function openNewWorkspaceDialog() {
    openModal({
      title: "新建工作区",
      kicker: "WORKSPACE · 一个工程目录",
      content: "<p>工作区就是一个工程目录。任务数据库生成在该目录的 <code>.ai-hfss/tasks/&lt;task_id&gt;/missions.db</code>，不会改动工程文件。</p>" +
        "<label class=\"form-field\"><span>目录路径</span><input id=\"wf-root\" type=\"text\" placeholder=\"例如 E:\\ai-hfss-test\"></label>" +
        "<label class=\"form-field\"><span>名称（可选）</span><input id=\"wf-name\" type=\"text\" placeholder=\"默认取目录名\"></label>",
      buttons: [
        { label: "取消", onClick: closeModal },
        { label: "创建", primary: true, onClick: function () {
            const root = ($("#wf-root").value || "").trim();
            const name = ($("#wf-name").value || "").trim();
            if (!root) { notify("请填写工作区目录。", "warning"); return; }
            wfJson("/api/workbench/workspaces", { method: "POST", json: { root: root, name: name || undefined } })
              .then(function (payload) {
                closeModal();
                notify("工作区已创建。");
                return refreshWorkspaces().then(function () { return selectWorkspace(payload.workspace.workspace_id); });
              })
              .catch(function (error) { notify("创建工作区失败：" + error.message, "error", 6000); });
          } }
      ]
    });
  }

  function openNewTaskDialog() {
    if (!wf.workspaceId) { notify("请先选择或新建一个工作区。", "warning"); return; }
    openModal({
      title: "新建优化任务",
      kicker: "TASK · 每任务独立 MISSION 库",
      content: "<p>任务创建后立刻生成自己的 <code>missions.db</code> 与运行目录；推进与审批都在这个工作台里完成。</p>" +
        "<label class=\"form-field\"><span>目标（goal）</span><input id=\"task-goal\" type=\"text\" placeholder=\"例如 1GHz 单点冒烟\"></label>" +
        "<label class=\"form-field\"><span>源工程（.aedt 绝对路径）</span><input id=\"task-source\" type=\"text\" placeholder=\"E:\\ai-hfss-test\\xxx.aedt\"></label>" +
        "<label class=\"form-field\"><span>模板</span><select id=\"task-template\"><option value=\"aihfss_layout_solve_point\">1GHz 单点求解（冒烟）</option><option value=\"brd_reviewed_model_optimize_loop\">reviewed BRD 优化循环</option></select></label>" +
        "<label class=\"form-field\"><span>设计名（可选）</span><input id=\"task-design\" type=\"text\" placeholder=\"留空则用默认设计\"></label>" +
        "<label class=\"form-field\"><span>Setup / 频率点（GHz）</span><span class=\"form-inline\"><input id=\"task-setup\" type=\"text\" value=\"Setup1\"><input id=\"task-frequency\" type=\"number\" step=\"0.1\" value=\"1\"></span></label>" +
        "<label class=\"form-field\"><span>最大轮次</span><input id=\"task-rounds\" type=\"number\" min=\"1\" value=\"1\"></label>",
      buttons: [
        { label: "取消", onClick: closeModal },
        { label: "创建任务", primary: true, onClick: function () {
            const goal = ($("#task-goal").value || "").trim();
            const source = ($("#task-source").value || "").trim();
            const templateId = $("#task-template").value;
            if (!goal) { notify("请填写任务目标。", "warning"); return; }
            if (!source) { notify("请填写源工程路径。", "warning"); return; }
            const params = {
              goal: goal,
              template_id: templateId,
              source_project_path: source,
              design_name: ($("#task-design").value || "").trim() || undefined,
              setup_name: ($("#task-setup").value || "").trim() || "Setup1",
              frequency_ghz: Number($("#task-frequency").value || 1),
              max_rounds: Number($("#task-rounds").value || 1)
            };
            wfJson("/api/workbench/workspaces/" + encodeURIComponent(wf.workspaceId) + "/tasks", { method: "POST", json: params })
              .then(function (payload) {
                closeModal();
                notify("任务已创建。");
                return refreshTasks().then(function () { return selectTask(payload.task.task_id); });
              })
              .catch(function (error) { notify("创建任务失败：" + error.message, "error", 6000); });
          } }
      ]
    });
  }

  function startTaskPolling() {
    stopTaskPolling();
    wf.timer = setInterval(function () {
      if (!wf.taskId || wf.busy) return;
      refreshSnapshot().then(refreshApprovals).catch(function () {});
    }, 4000);
  }
  function stopTaskPolling() { if (wf.timer) { clearInterval(wf.timer); wf.timer = null; } }

  if ($("#new-workspace-button")) $("#new-workspace-button").addEventListener("click", openNewWorkspaceDialog);
  if ($("#new-task-button")) $("#new-task-button").addEventListener("click", openNewTaskDialog);
  if ($("#task-refresh")) $("#task-refresh").addEventListener("click", function () { refreshSnapshot().then(refreshApprovals); });
  if ($("#task-advance")) $("#task-advance").addEventListener("click", advanceTask);
  if ($("#task-auto")) $("#task-auto").addEventListener("click", function () { setAuto(true); });
  if ($("#task-stop-auto")) $("#task-stop-auto").addEventListener("click", function () { setAuto(false); });
  if ($("#task-approve")) $("#task-approve").addEventListener("click", function () { decideTask("approved"); });
  if ($("#task-reject")) $("#task-reject").addEventListener("click", function () { decideTask("rejected"); });

  refreshWorkspaces();

  renderChart();
  updateCandidateSummary();
})();