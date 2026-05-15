# AEDT Agent 阶段性报告

更新日期：2026-05-15  
仓库：`https://github.com/z331225718/ansys-agent`  
当前分支：`stage-a-grounding-benchmark`

## 1. 结论摘要

本阶段已经完成从“LLM 直接生成 PyAEDT 代码”到“真实 AEDT 执行判卷 + 受控节点执行”的两阶段验证。

核心结论：

- **Stage A 证明 grounding 有价值。** 裸生成 Group A 三次内成功率为 30%，接入 GitNexus、官方 PyAEDT 源码和官方 examples 的 Group B 三次内成功率达到 100%。
- **Stage B 证明节点化路径有工程价值。** 在 10-task 对照中，工具增强自由代码 Group B 三次内成功率为 90%，受控节点计划 Group C 三次内成功率为 100%，且 Group C 自由代码执行次数为 0。
- **当前 validation 是结构性判卷。** 它检查真实 AEDT 模型状态、对象、材料、端口、边界、setup/sweep 等，但不等同于完整电磁物理正确性证明。
- **下一步不应先扩任务集。** 更高优先级是增强 validation、收紧节点 schema、提高端口/边界/setup/sweep 的语义检查能力。

当前已具备可复现交付：

- Stage B 复现文档：`docs/stage-b-controlled-node-benchmark.md`
- Stage B 中文汇报 HTML：`benchmarks/reports/stage_b_10task_compare.html`
- Stage B 中文汇报 JSON：`benchmarks/reports/stage_b_10task_compare.json`
- 一键报告脚本：`scripts/build_stage_b_report.py`
- 报告合成/脱敏模块：`src/aedt_agent/benchmark/stage_b_presentation.py`

## 2. 项目目标

项目目标是验证一个面向 AEDT/PyAEDT 自动化的 agent 架构：

1. 用户提出仿真建模任务。
2. LLM 生成 PyAEDT 自动化方案。
3. 系统通过官方知识、代码图谱、节点白名单和真实 AEDT 执行反馈降低错误率。
4. 最终形成可审计、可复现、可扩展的 AEDT 自动化执行链。

本阶段关注的是 MVP 级验证，不追求完整产品形态：

- 不做可视化 node editor。
- 不做完整 DAG runtime。
- 不做自动生成所有节点。
- 不把 fake adapter 结果当成 benchmark 证据。

## 3. 阶段路线

### Stage A：Grounding Benchmark

Stage A 的问题是：

> 只给自然语言需求时，LLM 生成 PyAEDT 代码是否可靠？如果提供官方源码、官方 examples 和 GitNexus 图谱检索，正确率是否显著提升？

最终采用两组对照：

| 分组 | 方法 | 输出 | 判据 |
| --- | --- | --- | --- |
| Group A | 同一 harness，不给工具和官方检索 | 自由 PyAEDT Python | 真实 AEDT non-graphical 执行 + validation |
| Group B | harness + GitNexus + PyAEDT 官方源码 + pyaedt-examples | 自由 PyAEDT Python | 真实 AEDT non-graphical 执行 + validation |

Stage A 的关键调整：

- 放弃纯离线判卷作为最终证据。
- 使用真实 AEDT 2026.1 non-graphical 执行。
- 每个任务最多三次修复。
- 失败日志返回给下一轮生成。
- 记录首轮成功率、三次内成功率、平均成功轮次、失败类别和工具使用情况。

### Stage B：Controlled Node Benchmark

Stage B 的问题是：

> 在 grounding 已经有效的情况下，把高风险 PyAEDT 调用封装为受控 node，是否能更稳定、更安全、更可审计？

Stage B 对照定义：

| 分组 | 方法 | 输出 | 判据 |
| --- | --- | --- | --- |
| Group B | Stage A 的工具增强自由代码路径 | 自由 PyAEDT Python | 真实 AEDT non-graphical 执行 + validation |
| Group C | 同一 harness 生成 JSON node plan，本地 `execute_node` 执行 | 结构化节点计划 | 真实 AEDT non-graphical 执行 + validation |

Group C 不允许自由 Python fallback。LLM 只负责生成结构化 plan，本地执行器负责调用受控节点。

## 4. Benchmark 任务与判定

当前固定 10 个任务：

- `L1_create_substrate`
- `L1_assign_material`
- `L1_create_wave_port`
- `L1_create_setup`
- `L2_microstrip_line`
- `L2_dipole_antenna`
- `L2_patch_with_probe_feed`
- `L2_simple_filter`
- `Trap_missing_ground`
- `Trap_waveport_wrong_face`

通过条件：

- 生成成功。
- AEDT 执行完成。
- validation script 基于真实模型信息通过。
- 在最多三次尝试内成功。

失败条件：

- 生成失败。
- JSON plan 解析失败。
- node schema 不匹配。
- node 引用解析失败。
- PyAEDT/AEDT runtime error。
- harness 或 AEDT timeout。
- validation 不通过。

## 5. 实验结果

### Stage A 结果

数据来源：`benchmarks/reports/stage_a_sample_report.json`

| 指标 | Group A：裸生成 | Group B：官方知识 + GitNexus |
| --- | ---: | ---: |
| 任务数 | 10 | 10 |
| 首轮成功率 | 10% | 80% |
| 三次内成功率 | 30% | 100% |
| 成功任务平均轮次 | 1.67 | 1.20 |
| 全部任务平均轮次 | 2.60 | 1.20 |
| 工具使用率 | 0% | 100% |
| 平均 GitNexus 查询数 | 0.0 | 6.0 |
| 代码前检索率 | 0% | 100% |

Stage A 结论：

- 裸 LLM 对 PyAEDT 这类高约束 API 生成不可靠。
- 官方源码、官方 examples 和 GitNexus 检索显著提高成功率。
- 真实 AEDT 执行比语法检查或静态检查更能揭示端口、边界、setup/sweep 等 runtime 问题。

### Stage B 结果

数据来源：`benchmarks/reports/stage_b_10task_compare.json`

| 指标 | Group B：工具增强自由代码 | Group C：受控节点计划 |
| --- | ---: | ---: |
| 任务数 | 10 | 10 |
| 首轮成功率 | 70% | 80% |
| 三次内成功率 | 90% | 100% |
| 成功任务平均轮次 | 1.33 | 1.20 |
| 全部任务平均轮次 | 1.50 | 1.20 |
| 平均 GitNexus 查询数 | 6.9 | 不适用 |
| 平均节点数 | 不适用 | 4.3 |
| 节点覆盖率 | 不适用 | 100% |
| 自由代码执行次数 | 自由代码路径 | 0 |
| 未支持任务数 | 不适用 | 0 |

Stage B 关键案例：

- `L2_dipole_antenna`：Group B 三次后失败，第三次 harness 超时；Group C 第二次通过。
- `L2_patch_with_probe_feed`：Group B 第二次通过；Group C 第一轮通过。
- `Trap_missing_ground`：Group B 第三次通过；Group C 第一轮通过。

Stage B 结论：

- 节点化不只是提高 pass rate，更重要的是把高风险调用收敛到受控实现里。
- C 组失败更容易定位到 schema、引用、节点输入或 validation，而不是散落在任意 Python 代码里。
- C 组保持自由代码执行次数为 0，说明正式路径没有绕过节点执行边界。

## 6. 已完成工程能力

### Benchmark 与报告

- `scripts/run_stage_b_benchmark.py`
  - 支持 B/C 分组。
  - 支持真实 AEDT non-graphical。
  - 支持最多三次尝试和真实错误反馈。
  - 支持 terminal heartbeat/progress 输出。
- `scripts/build_stage_b_report.py`
  - 合成 B-only 与 C-only 结果。
  - 生成 presentation-safe JSON/HTML。
  - 删除 artifact path 字段。
  - 脱敏本机绝对路径。
- `src/aedt_agent/benchmark/report_html_stage_b.py`
  - 生成中文 Stage B HTML 汇报。
- `src/aedt_agent/benchmark/stage_b_presentation.py`
  - 提供可测试的报告合成与脱敏逻辑。

### AEDT 与节点执行

- `PyaedtAdapter`
  - 支持 AEDT 2026.1 non-graphical。
  - 可自动识别 `~/ansys_inc/v261`。
- `NodeExecutor`
  - 统一 `execute_node` 入口。
  - 每次 C 组 attempt 使用独立 AEDT session/project。
  - audit JSONL 记录节点输入、输出、前后状态。
  - snapshot 失败记录为 `snapshot_error`，不再直接中断 benchmark。

### 节点兼容性修复

- 节点输出补充：
  - `object_name`
  - `object_names`
  - `port_name`
  - `boundary_name`
  - `setup_name`
  - `sweep_name`
- 支持常见 LLM 字段别名：
  - `type -> kind`
  - `position -> origin`
  - `dimensions/sizes -> size`
  - `matname -> material`
- `create_airbox.padding` 支持列表并取最大值。
- `create_port.integration_line` 支持 `{"start": ..., "end": ...}`。
- `create_sweep_or_export` 兼容 PyAEDT 2026.1 的 `unit` 参数签名。
- `create_port` 在 lumped port 收到 face id 时回溯所属 sheet/object。
- `create_conductor_or_geometry_group` 对 cylinder 输入做 box 近似，降低非核心 schema 错误。

## 7. 复现方式

### 环境

需要：

- Python venv：`.venv`
- PyAEDT / pyedb
- AEDT 2026.1：`~/ansys_inc/v261`
- GitNexus eval server：`http://127.0.0.1:4848`
- harness CLI

本地模型配置：

- `config/benchmark_config.local.json`
- 当前本地测试模型已配置为 `deepseek-v4-flash`
- local 配置被 `.gitignore` 排除，不应提交 API key 或 base URL。

说明：当前提交的 benchmark 结果来自已保存的 run artifacts。若要得到 `deepseek-v4-flash` 重新生成的全量结果，需要用当前 local 配置重新运行 B/C benchmark。

### 常用命令

C-only：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_10task_after_node_fixes
```

B-only：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups B \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_b_10task_after_node_fixes
```

生成中文汇报：

```bash
.venv/bin/python scripts/build_stage_b_report.py \
  --group-b-report benchmarks/runs/stage_b_b_10task_after_node_fixes/stage_b_report.json \
  --group-c-report benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json \
  --output-html benchmarks/reports/stage_b_10task_compare.html \
  --output-json benchmarks/reports/stage_b_10task_compare.json \
  --model-name "deepseek-v4-flash / AEDT 2026.1"
```

## 8. 当前限制

当前系统仍有明确边界：

- validation 是结构性判卷，不是完整物理正确性证明。
- Trap 任务检查的是端口/边界/对象关系等结构问题，不能替代电磁设计审查。
- Group C 当前节点数量有限，尚未覆盖复杂后处理、求解结果分析、报告导出等场景。
- GitNexus 当前主要用于 harness 检索和 B 组 grounding，尚未成为节点执行链的核心依赖。
- 报告中的 DeepSeek 配置表示当前本地测试模型配置，不代表已有历史 run 都由 DeepSeek 重新生成。

## 9. 阶段判断

### Stage A 是否完成

完成。

原因：

- 已有真实 AEDT 判卷。
- 10-task A/B 对照差异显著。
- 证明官方知识与图谱检索对 PyAEDT 代码生成有效。

### Stage B 是否达到 MVP

达到。

原因：

- C 组三次内成功率达到 100%。
- C 组自由代码执行次数为 0。
- C 组所有通过都来自真实 AEDT non-graphical + validation。
- 已具备报告、复现文档、一键报告脚本和测试。

### 是否应进入 Stage C

不建议立刻进入完整 Stage C。

建议先做 Stage B+：

1. 增强 validation。
2. 收紧 node schema。
3. 增加端口、边界、setup/sweep 的结构性和语义检查。
4. 在现有 10-task 上保持稳定，再考虑更大任务集。

## 10. 下一步建议

优先级从高到低：

1. **增强 validation**
   - wave port 是否绑定合理 face。
   - lumped port 是否绑定 sheet/object。
   - sweep 是否挂到正确 setup。
   - radiation boundary 是否作用在 airbox/region。

2. **节点 schema 精细化**
   - 为 port、boundary、setup/sweep 单独定义更严格输入约束。
   - 减少依赖 LLM 猜字段。

3. **补充报告与 demo 材料**
   - 从 HTML 报告提取 3-5 页 slides。
   - 明确讲清楚 Stage A/B 的问题、方法、结果、边界。

4. **再决定是否扩任务集**
   - 当前不急于扩展到 30/90 task。
   - 扩任务前先保证 validation 质量，否则任务越多，噪声越大。

## 11. 附录：关键文件

| 类型 | 文件 |
| --- | --- |
| Stage A 报告 | `benchmarks/reports/stage_a_sample_report.html` |
| Stage A JSON | `benchmarks/reports/stage_a_sample_report.json` |
| Stage B 报告 | `benchmarks/reports/stage_b_10task_compare.html` |
| Stage B JSON | `benchmarks/reports/stage_b_10task_compare.json` |
| Stage B 复现文档 | `docs/stage-b-controlled-node-benchmark.md` |
| Stage B 收口计划 | `docs/superpowers/plans/2026-05-15-stage-b-closure-and-reproducible-report.md` |
| Stage B runner | `scripts/run_stage_b_benchmark.py` |
| Stage B 报告脚本 | `scripts/build_stage_b_report.py` |
| 节点执行器 | `src/aedt_agent/mcp/node_executor.py` |
| PyAEDT adapter | `src/aedt_agent/mcp/pyaedt_adapter.py` |
| 报告合成模块 | `src/aedt_agent/benchmark/stage_b_presentation.py` |

