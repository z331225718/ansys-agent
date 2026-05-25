# AEDT Agent Stage C 阶段性报告

更新日期：2026-05-17

## 一句话结论

Stage C 已把 Stage B 证明有效的“受控节点执行”推进成产品化骨架：节点 catalog、workflow schema、validator、executor、模板、聊天规划、模型事实 validation、节点进化 proposal、轻量 demo 和真实 AEDT smoke 已形成闭环。

## 目标变化

Stage C 当前不再把重点放在“LLM 写 PyAEDT 代码”，而是放在三个产品入口的底座：

| 入口 | 目标 | 当前支撑能力 |
| --- | --- | --- |
| 经验工程师 | 拖拽少量节点完成完整流程 | 节点 catalog、workflow schema、executor |
| 新手工程师 | 选择模板并填参数 | 3 个 workflow templates |
| 聊天入口 | 主模型判断任务并生成 workflow | chat planner、template selection、validator |

完整拖拽 UI 暂时靠后。当前优先保证数据结构、执行链路和报告 artifact 对 UI 友好。

## 当前架构

```text
用户请求 / 模板 / 节点图
        ↓
Node Catalog
        ↓
Workflow JSON
        ↓
Workflow Validator
        ↓
Workflow Executor
        ↓
Controlled NodeExecutor
        ↓
AEDT Adapter / Fake Adapter / AEDT 2026.1 non-graphical
        ↓
Inspector + Validation
        ↓
Audit / Report / Repair Context
        ↓
Node Evolution Proposal
```

## 已完成 Milestones

| Milestone | 状态 | 产物 |
| --- | --- | --- |
| 1. 节点 catalog | 完成 | 8 个节点 metadata/schema/version/ui_hints/postchecks |
| 2. Workflow 模型 | 完成 | Workflow/Node/Edge/Parameter/Output JSON |
| 3. Workflow Validator | 完成 | 启动 AEDT 前结构校验 |
| 4. Workflow Executor | 完成 | 受控执行、artifact、repair context |
| 5. Template Catalog | 完成 | 3 个 starter templates |
| 6. Inspector + Validation | 完成 | 模型事实抽取和结构性规则 |
| 7. Chat Planner | 完成 | 模板选择、填参、简单 workflow 生成 |
| 8. Node Evolution | 完成 | benchmark/audit -> proposal/evaluator |
| 9. Demo/Report | 完成 | CLI demo、workflow run、proposal report |
| 10. Real AEDT Smoke | 完成 | AEDT 2026.1 non-graphical 跑通 1 个 workflow |
| 11. Port Smoke | 完成 | AEDT 2026.1 non-graphical 跑通 wave port workflow |
| 12. Boundary Smoke | 完成 | AEDT 2026.1 non-graphical 跑通 radiation airbox workflow |
| 13. Smoke Dashboard | 完成 | 3 个真实 AEDT smoke 汇总 HTML/JSON |
| 14. Node Evolution Review | 完成 | proposal 审核 HTML/JSON，默认不自动发布 stable |
| 15. Demo Index | 完成 | Stage C 展示入口 HTML/JSON |

## Demo 结果

| Demo | 结果 |
| --- | --- |
| microstrip template run | `succeeded`，4 个节点，输出 `setup` 和 `sweep` |
| real AEDT smoke | `succeeded`，创建 `Substrate`、`Trace`、`Setup1`、`Sweep1`，model validation 3/3 通过 |
| real wave port smoke | `succeeded`，创建 `WaveguideSection` 和 `Port1`，从 PyAEDT props 验证 face assignment |
| real radiation boundary smoke | `succeeded`，创建 `AirBox` 和 `Radiation`，从 PyAEDT props 验证 object assignment |
| real smoke dashboard | 3/3 通过，覆盖 geometry/setup/sweep/selection/port/airbox/boundary |
| node evolution review | 23 条 evidence，11 个 proposal，全部保持 needs_review |
| chat planning sample | 选择 `microstrip_sparameter`，confidence `0.82`，0 个 validation error |
| node evolution report | 23 条 evidence，11 条 proposal |

关键 artifact：

- `benchmarks/runs/stage_c_demo_microstrip/workflow_run.json`
- `benchmarks/runs/stage_c_demo_microstrip/audit.jsonl`
- `benchmarks/runs/stage_c_demo_microstrip/validation.json`
- `benchmarks/runs/stage_c_demo_microstrip/report.html`
- `benchmarks/reports/stage_c_chat_plan_sample.json`
- `benchmarks/reports/stage_c_node_evolution_report.json`
- `benchmarks/runs/stage_c_real_microstrip_smoke/workflow_run.json`
- `benchmarks/runs/stage_c_real_microstrip_smoke/validation.json`
- `benchmarks/runs/stage_c_real_microstrip_smoke/smoke_summary.json`
- `benchmarks/runs/stage_c_real_wave_port_smoke/workflow_run.json`
- `benchmarks/runs/stage_c_real_wave_port_smoke/validation.json`
- `benchmarks/runs/stage_c_real_wave_port_smoke/smoke_summary.json`
- `benchmarks/runs/stage_c_real_radiation_airbox_smoke/workflow_run.json`
- `benchmarks/runs/stage_c_real_radiation_airbox_smoke/validation.json`
- `benchmarks/runs/stage_c_real_radiation_airbox_smoke/smoke_summary.json`
- `benchmarks/reports/stage_c_real_smoke_dashboard.html`
- `benchmarks/reports/stage_c_real_smoke_dashboard.json`
- `benchmarks/reports/stage_c_node_evolution_review.html`
- `benchmarks/reports/stage_c_node_evolution_review.json`
- `benchmarks/reports/stage_c_demo_index.html`
- `benchmarks/reports/stage_c_demo_index.json`

## 当前能力边界

- 已跑通 3 个真实 AEDT smoke，分别覆盖 geometry/setup/sweep、select_face/port、airbox/boundary。
- validation 仍是结构性检查，不等价于完整电磁物理正确。
- chat planner 当前是确定性骨架，后续可接主模型，但输出仍必须限制为 workflow JSON。
- node evolution 只生成 proposal，不自动发布 stable 节点。

## Validation 边界

当前系统的验证分三层：

1. 结构性验证：检查对象、材料、端口、边界、setup、sweep、report 是否按 workflow 预期创建。
2. 结果文件验证：检查 Touchstone、CSV、TDR 等文件是否存在、可解析，且频率范围覆盖用户目标。
3. 电磁语义验证：只在少数模板中使用启发式规则，例如谐振点是否接近目标频率；这不是完整电磁正确性证明。

因此，当前结论应表述为“受控 workflow 能稳定生成并验证 AEDT 模型结构和基础结果文件”，不应表述为“自动保证仿真设计物理正确”。

## 下一步建议

Stage C.1 的下一步是把当前产品骨架变成可操作 demo。启动命令：

```bash
.venv/bin/python scripts/run_stage_c1_demo_server.py --port 8765
```

这个 demo 默认只使用 fake adapter，浏览器里不直接启动真实 AEDT；真实 AEDT 结果通过已有 smoke dashboard 展示。
