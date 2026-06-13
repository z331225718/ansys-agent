# BRD Real Build Adapter Design

## 背景

当前 `brd.local_cut.build` 已经能在 Agent Runtime 中形成闭环：Mission 创建、Job 执行、artifact 持久化、候选端口审批、bounded evidence summary、CLI status。它仍是 deterministic build artifact，不会真的打开 BRD/MCM，也不会启动 PyEDB 或 HFSS 3D Layout。

旧版真实能力仍在 `aedt_agent.v0.demo.import_cutout` 中：它可以打开 BRD/MCM、执行 PyEDB cutout、定位端口候选、创建端口、导入 stackup、应用 recorded HFSS settings、创建 setup/sweep，并在 `solve_enabled=false` 时保存 build-only AEDT 工程。新 Agent 不能直接依赖 `aedt_agent.v0`，所以下一阶段要把这条真实建模能力抽成正式 infrastructure adapter。

## 决策

第一版真实 adapter 只做 **真实建模但不求解**。

也就是说：

1. 使用真实 PyEDB 打开用户给定 BRD/MCM。
2. 按用户明确给定的 `local_cut_region` 执行 bbox/polygon local cut。
3. 定位端口候选，能明确选择时创建端口。
4. 导入 stackup XML。
5. 应用 recorded HFSS extents、DesignOptions、setup、sweep、curve approximation 等建模设置。
6. 创建 HFSS 3D Layout setup/sweep。
7. 保存 AEDT 工程。
8. 默认 `solve_enabled=false`，不运行 `analyze_setup`，不导出完整 Touchstone/TDR。
9. 将 Mission 推进到模型审查阶段，等待工程师确认后再进入求解计划。

这比“直接完整求解”更适合 agent-first 架构：真实仿真很慢、license/桌面/服务器状态不稳定，而且错误端口或过大的 cutout 会浪费大量资源。build-only 阶段先让 agent 交付一个可审查的工程对象，符合用户输入到规划到完成的闭环，也保留人工审批点。

## 目标

- 让 `brd.local_cut.build` worker 可以通过配置选择 `adapter_mode="deterministic"` 或 `adapter_mode="real_build"`。
- `real_build` 路径必须使用 `aedt_agent.infrastructure` 中的正式 adapter，不依赖 `aedt_agent.v0`、`aedt_agent.demo` 或旧脚本。
- worker 输出继续复用当前 artifact summary 合同：`brd_local_cut_summary.json`、`workflow_run.json`、bounded `evidence_summary`。
- 真实 build-only 输出必须明确记录 `layout_solve.status="skipped"` 和原因 `model_review_only` 或 `build_only`.
- 不把 S 参数或 TDR 原始数组塞进 LLM 上下文；本阶段没有真实曲线时只返回 artifact 引用和状态。
- 如果端口候选不明确，adapter 不创建猜测端口，而是返回 `approval_required`，Mission 进入 `waiting_approval`。

## 非目标

- 不在本阶段实现完整求解。
- 不在本阶段实现大 S 参数/TDR 的窗口查询或压缩评分。
- 不让 LLM 自动猜 bbox。
- 不直接调用 v0 真实函数。
- 不把旧 demo service/web 行为迁到新 Agent。

## 架构

新结构分三层：

```text
aedt_agent.agent.workers.brd_local_cut
    -> aedt_agent.infrastructure.brd_real_build
        -> aedt_agent.layout.* shared pure helpers
        -> PyEDB / ansys.aedt.core lazy imports
```

### Worker 层

`src/aedt_agent/agent/workers/brd_local_cut.py` 继续负责：

- 校验 job payload。
- 决定 adapter mode。
- 调用 adapter。
- 写统一 summary 和 workflow artifact。
- 生成 bounded evidence summary。
- 把 ambiguous port candidates 翻译成 `approval_required`。

Worker 不直接 import `pyedb`、`ansys.aedt.core`，也不 import `aedt_agent.v0`。

### Infrastructure Adapter 层

新增 `src/aedt_agent/infrastructure/brd_real_build.py`，负责真实环境交互：

- `BrdRealBuildRequest`
- `BrdRealBuildResult`
- `BrdRealBuildAdapter`
- `RealAedtEnvironment`
- lazy import `pyedb.Edb`
- lazy import `ansys.aedt.core.Hfss3dLayout`

Adapter 必须能被单元测试注入 fake EDB/HFSS classes。真实 AEDT 只在 `RUN_REAL_AEDT=1` 或 CLI 显式 `--adapter-mode real_build` 时使用。

### Shared Layout 层

继续复用：

- `aedt_agent.layout.local_cut`
- `aedt_agent.layout.import_cutout`
- `aedt_agent.layout.ports`
- `aedt_agent.layout.recorded_settings`
- `aedt_agent.layout.workflow_run`

如果旧 v0 中有纯函数值得复用，应抽到 `aedt_agent.layout` 或 `aedt_agent.infrastructure`，然后 v0 和新 Agent 都可以调用新位置。迁移时不应让新 Agent 反向依赖 v0。

## 输入合同

`build_brd_local_cut_job_input` 扩展以下字段：

```json
{
  "adapter_mode": "real_build",
  "layout_file": "D:/boards/case.brd",
  "stackup_xml": "D:/boards/stackup.xml",
  "signal_nets": ["56G_TX0_P", "56G_TX0_N"],
  "reference_nets": ["GND"],
  "local_cut_region": {
    "type": "bbox",
    "unit": "mil",
    "x_min": 1.0,
    "y_min": 2.0,
    "x_max": 3.0,
    "y_max": 4.0
  },
  "artifact_dir": "D:/runs/mission-1",
  "recorded_layout_settings": {
    "hfss_extents": {},
    "design_options": {},
    "setup_options": {},
    "setup_advanced_settings": {},
    "setup_curve_approximation": {},
    "sweep_options": {}
  },
  "uniform_line_port_hint": {
    "side": "right",
    "layer": "ART03",
    "port_type": "edge"
  },
  "aedt": {
    "version": "2026.1",
    "non_graphical": false,
    "edb_backend": "auto",
    "cadence_launcher": "",
    "ansysem_root": "",
    "awp_root": ""
  },
  "solve_enabled": false
}
```

规则：

- `adapter_mode` 默认 `deterministic`，避免开发机误启 AEDT。
- `real_build` 必须要求 `layout_file` 存在。
- `local_cut_region` 必须是显式 bbox。
- `solve_enabled` 在本阶段必须为 false；如果用户传 true，worker 返回清晰错误：真实求解属于下一阶段。
- `recorded_layout_settings` 可为空，但字段结构要稳定。
- `aedt.non_graphical` 默认 false，因为很多真实桌面路径更容易观察；服务器运行时可以显式 true。

## 输出合同

`brd_local_cut_summary.json` 必须包含：

```json
{
  "status": "succeeded",
  "adapter": "real_pyedb_hfss3dlayout_build_only",
  "layout_file": "D:/boards/case.brd",
  "source_edb_path": "D:/runs/mission/source/case.aedb",
  "edb_path": "D:/runs/mission/case_cutout.aedb",
  "aedt_project": "D:/runs/mission/case_cutout_hfss.aedt",
  "signal_nets": ["56G_TX0_P", "56G_TX0_N"],
  "reference_nets": ["GND"],
  "local_cut_region": {},
  "local_cut_polygon": {},
  "port_candidates": {},
  "port_execution": {},
  "layout_setup": {},
  "layout_solve": {
    "status": "skipped",
    "reason": "model_review_only"
  },
  "layout_reports": {},
  "recorded_layout_settings": {},
  "steps": []
}
```

`evidence_summary` 只保留小字段：

- status
- adapter
- layout file
- selected nets
- local cut bbox
- port candidate status/count
- port execution status
- setup/sweep name
- AEDT project path
- EDB path
- `raw_sparameters="artifact_only"`
- `raw_tdr="artifact_only"`

## 错误与审批

真实 adapter 的错误分三类：

1. **用户输入可修复**：缺 bbox、layout 不存在、stackup 不存在、net pattern 无匹配。
   - Job failed，错误进入 event。
2. **需要用户判断**：端口候选 ambiguous、uniform-line edge 无法唯一确定。
   - Job succeeded with `approval_required`，Mission 进入 `waiting_approval`。
3. **环境/工具失败**：PyEDB import 失败、AEDT 启动失败、license 不可用、cutout API 异常。
   - Job failed，错误分类为 infrastructure/tool failure。

审批后的恢复策略：

- 本阶段不重复执行已经完成的 build job。
- 如果审批选择了端口候选，Runtime 后续应创建新的 follow-up job，例如 `brd.local_cut.apply_approval` 或重跑同 capability 但使用新的 idempotency key。
- 第一版计划可以先实现“记录审批结果并提示需要 rerun build”，不要隐藏式重复 build。

## CLI 体验

扩展 `aedt-agent mission create`：

```powershell
aedt-agent mission create `
  --goal "构建 56G local cut" `
  --brd-local-cut `
  --adapter-mode real_build `
  --layout-file D:/boards/case.brd `
  --stackup-xml D:/boards/stackup.xml `
  --signal-net 56G_TX0_P `
  --signal-net 56G_TX0_N `
  --reference-net GND `
  --bbox "mil,1,2,3,4" `
  --recorded-analysis D:/runs/recorded_workflow_analysis.json `
  --aedt-version 2026.1 `
  --graphical
```

扩展 `aedt-agent mission run`：

- 默认只注册 deterministic worker。
- 当 job payload 是 `adapter_mode=real_build` 时注册真实 build adapter。
- 真实路径输出 artifact refs，不输出大曲线。

## 测试策略

单元测试不启动 AEDT：

- 使用 fake EDB/HFSS class 验证 adapter 调用顺序。
- 验证 `solve_enabled=false` 时不会调用 `analyze_setup`。
- 验证 `local_cut_region` 被转换为 polygon custom extent。
- 验证 recorded settings 被应用到 fake HFSS object。
- 验证 worker 不依赖 v0。
- 验证 CLI 能把 `--recorded-analysis` 合并到 job payload。

真实 smoke 测试显式 opt-in：

```powershell
$env:RUN_REAL_AEDT=1
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_real_build_smoke.py -q -s
```

真实 smoke 只验收 build-only：

- 成功打开真实 BRD/MCM。
- 生成 cutout AEDB。
- 保存 AEDT project。
- `layout_solve.status == "skipped"`。

## 后续阶段

真实求解应作为下一份独立设计：

```text
brd.local_cut.build(real build-only)
    -> engineer/model approval
    -> brd.local_cut.solve
    -> artifact-only Touchstone/TDR
    -> bounded window query / feature extraction
    -> evaluator and optimizer
```

S 参数/TDR 大数据处理必须走 artifact query，不直接塞给 GLM。第一版 query 可以只返回窗口统计、极值、阈值 crossing、指定频点采样、趋势摘要。

## 验收标准

- 新 Agent real_build 路径不依赖 `aedt_agent.v0`。
- 默认不启动真实 AEDT，只有显式 `adapter_mode=real_build` 才进入真实 adapter。
- build-only 路径不调用 solve。
- artifact summary 与当前 `brd.local_cut.build` 合同兼容。
- ambiguous port 进入 approval，而不是自动乱建端口。
- 全量测试失败集合不扩大。
