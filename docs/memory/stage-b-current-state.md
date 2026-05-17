# Stage B 当前记忆

更新日期：2026-05-15

## 项目状态

- 仓库：`/home/zzmjay/code/ansys-agent`
- GitHub：`https://github.com/z331225718/ansys-agent`
- 当前分支：`stage-a-grounding-benchmark`
- 最新已推送提交：
  - `d5b2d61 Add executive AEDT agent progress report`
  - `0a0fd8a Add AEDT agent stage progress report`
  - `3ec3dab Document stage b reproducible reporting`
  - `281bfaa Stabilize stage b node benchmark`

## 当前阶段结论

Stage A 已收口，证明“给足官方知识/图谱检索”对 PyAEDT 自动化有显著收益。

- Group A：裸 LLM，自由生成 PyAEDT 代码。
  - 首轮成功率：10%
  - 三次内成功率：30%
- Group B：harness + GitNexus + 官方 PyAEDT 源码/examples，自由生成 PyAEDT 代码。
  - 首轮成功率：80%
  - 三次内成功率：100%

Stage B MVP 已收口，证明“受控节点计划 + 本地节点执行”比 grounded free-code 更可控。

- Group B：工具增强自由 Python。
  - 首轮成功率：70%
  - 三次内成功率：90%
  - 失败任务：`L2_dipole_antenna`
- Group C：harness 生成 JSON node plan，本地受控节点执行，不允许自由 Python fallback。
  - 首轮成功率：80%
  - 三次内成功率：100%
  - 平均成功轮次：1.2
  - 自由代码执行次数：0
  - unsupported task count：0

注意：当前 benchmark 使用真实 AEDT 2026.1 non-graphical 执行和 validation，但 validation 仍主要是结构性判卷，不等价于完整电磁物理正确性证明。

## 产品化最终目标

当前项目最终目的不是只做 benchmark，而是做出可产品化的 AEDT 节点化能力。目标入口分三类：

1. 有经验的工程师：通过拖拽少量节点，快速完成一整个仿真流程。
2. 新手工程师：使用现成模板/workflow，按参数填写即可跑通常见任务。
3. 聊天入口：用户与主模型对话，主模型自动判断任务意图、选择模板或生成节点 workflow。

这意味着 Stage C/产品化的重点应从“LLM 能否写对 PyAEDT 代码”转向：

- 节点 schema 是否稳定、可组合、可审计。
- workflow 是否能表达完整仿真流程，而不是单个 API 调用。
- 模板是否覆盖高频工程场景。
- 主模型是否能在聊天中可靠选择节点、补齐参数、解释缺失信息，并把执行日志反馈到修复循环。
- 执行层必须保留真实 AEDT validation、audit 和可复现 artifact。

优化 agent 作为后续增强功能记录下来，但不是当前主线。后续若做优化，重点也不应是对导入 layout/BRD/MCM 的每个对象逐一参数化，而应是抽取工程语义、生成少量可制造/可解释的设计变量，再由优化器和仿真结果闭环搜索。

## Stage B 目标

Stage B 不是继续比较裸 LLM 写 Python，而是验证节点化路径是否比 Stage A Group B 的 grounded free-code harness 更可控、可审计、可修复。

- Group B：Stage A 最终 baseline，Claude Code harness + GitNexus/PyAEDT 官方源码/示例，生成自由 Python。
- Group C：Stage B candidate，Claude Code harness 只生成 JSON node plan，本地 runner 调用 `execute_node`，不允许自由 Python fallback。
- 判据必须来自真实 AEDT 2026.1 non-graphical 执行和 validation，不把 fake adapter 结果当 benchmark 证据。

## 当前已完成能力

- `PyaedtAdapter` 可以自动识别 `~/ansys_inc/v261`，并补齐 `ANSYSEM_ROOT261/AWP_ROOT261`。
- `run_stage_b_benchmark.py` 支持真实 C 组：harness 生成 JSON node plan，本地 kernel 执行节点。
- C 组执行后会读取 `get_model_info()`，再调用 validation script 判定，不再是“节点无异常就算 pass”。
- 支持节点输出引用：
  - 示例：`{"$ref": "select_face.output.selected_face_id"}`
- `select_face/create_port` 的 allowed nodes 会自动展开 prerequisite：
  - `create_conductor_or_geometry_group -> select_face -> create_port`
- 几何节点兼容常见 LLM 字段别名：
  - `type -> kind`
  - `position -> origin`
  - `sizes/dimensions -> size`
  - `matname -> material`
- Trap wave port validation 已增强：
  - 不只检查 `wave_port_present`
  - 还检查 `create_port.assignment` 是否能追溯到 `select_face.output.selected_face_id`
  - 通过 check 名：`wave_port_uses_selected_face`
- 2026-05-15 新增：
  - 每次 C 组 attempt 使用独立 AEDT project/session，避免失败尝试污染修复尝试。
  - 先生成/解析 node plan，再启动 AEDT，避免 generation/parse 失败时浪费 AEDT session。
  - 节点输出增加便利字段：`object_name/object_names/port_name/boundary_name/setup_name/sweep_name`。
  - `create_airbox.padding` 支持数值列表并归一化为最大 padding。
  - `assign_boundary` 和 `create_port` 可以接受上游节点完整 output 作为 `assignment/reference`，执行时提取合适对象。
  - `create_port.integration_line` 支持 `{"start": [...], "end": [...]}` 并归一化为两点列表。
  - 增加 Stage B 中文 HTML 报告生成器：`src/aedt_agent/benchmark/report_html_stage_b.py`。
  - `scripts/run_stage_b_benchmark.py` 现在会在 run dir 下同时写出 `stage_b_report.html`。
  - `create_sweep_or_export` 兼容 PyAEDT 2026.1 的 `unit` 参数签名，并把 `"1GHz"` 这类字符串拆成数值和单位。
  - `select_face` 输出补充 `object_name`；`create_port` 在 lumped port 收到 face id 时会回溯所属 sheet/object，避免 AEDT 报 `a geometry selection is required for assignment`。
  - 节点 audit 的 snapshot 失败会记录 `snapshot_error`，不再让失败后的 AEDT 状态读取直接中断 benchmark。
  - `create_conductor_or_geometry_group` 对 LLM 常输出的 cylinder 输入做 box 近似，减少与节点 schema 无关的低价值失败。
  - C 组提示词明确要求 lumped port 优先使用 port sheet 对象名，sweep 使用频率字符串。

## 已验证结果

单元测试：

```bash
.venv/bin/python -m pytest -q
```

最新结果：

- `131 passed, 2 skipped`

真实 AEDT / harness smoke：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task L1_create_wave_port \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_l1_wave_port_real_harness_v3
```

结果：

- `L1_create_wave_port`：PASS，1/3
- validation checks：
  - `session_available`
  - `wave_port_present`

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_trap_waveport_real_harness_v2
```

结果：

- `Trap_waveport_wrong_face`：PASS，1/3

当前可展示 HTML 报告：

- `benchmarks/reports/stage_b_5task_compare.html`
- `benchmarks/reports/stage_b_10task_compare.html`
- validation checks：
  - `session_available`
  - `wave_port_present`
  - `wave_port_uses_selected_face`

5-task C-only smoke v2：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task L1_create_substrate \
  --task L1_create_setup \
  --task L1_create_wave_port \
  --task L2_microstrip_line \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_5task_smoke_v2
```

结果：

- `task_count`: 5
- `first_pass_rate`: 1.0
- `pass_rate_3try`: 1.0
- `avg_attempts_to_success`: 1.0
- `avg_attempts_all`: 1.0
- `avg_node_count`: 2.4
- `free_code_execution_count`: 0
- `failure_categories`: `{}`

5-task B/C compare：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups B C \
  --task L1_create_substrate \
  --task L1_create_setup \
  --task L1_create_wave_port \
  --task L2_microstrip_line \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_bc_5task_compare
```

结果：

- Group B:
  - `task_count`: 5
  - `first_pass_rate`: 0.8
  - `pass_rate_3try`: 0.8
  - 失败任务：`L1_create_wave_port`
  - 失败原因：第 1/3 轮均为 `AssignWavePort` runtime error，第 2 轮 harness 900s timeout。
- Group C:
  - `task_count`: 5
  - `first_pass_rate`: 0.8
  - `pass_rate_3try`: 1.0
  - `avg_attempts_to_success`: 1.2
  - `free_code_execution_count`: 0
  - 首轮失败任务：`Trap_waveport_wrong_face`，原因是 `integration_line` 使用了 `{"start": ..., "end": ...}` 格式。

修复 `integration_line` 归一化后，单独重跑：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_trap_waveport_after_line_normalization
```

结果：

- `Trap_waveport_wrong_face`：PASS，1/3

10-task C-only after node fixes：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_10task_after_node_fixes
```

结果：

- `task_count`: 10
- `first_pass_rate`: 0.8
- `pass_rate_3try`: 1.0
- `avg_attempts_to_success`: 1.2
- `avg_attempts_all`: 1.2
- `avg_node_count`: 4.3
- `node_coverage_rate`: 1.0
- `free_code_execution_count`: 0
- 失败类别：`{}`

10-task B-only after node fixes：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups B \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_b_10task_after_node_fixes
```

结果：

- `task_count`: 10
- `first_pass_rate`: 0.7
- `pass_rate_3try`: 0.9
- `avg_attempts_to_success`: 1.3333333333333333
- `avg_attempts_all`: 1.5
- `tool_usage_rate`: 1.0
- `avg_gitnexus_queries`: 6.9
- `retrieval_before_code_rate`: 1.0
- 失败任务：`L2_dipole_antenna`
- 失败类别：`{"generation_error": 1}`

10-task 对照汇报版报告：

- JSON：`benchmarks/reports/stage_b_10task_compare.json`
- HTML：`benchmarks/reports/stage_b_10task_compare.html`
- 报告结论：B 组三次内成功率 90%，C 组三次内成功率 100%；B 组首轮 70%，C 组首轮 80%；C 组自由代码执行次数 0，平均成功轮次 1.2。

面向不熟悉 Ansys 仿真的团队，另有更偏业务/架构展示的阶段性报告：

- Markdown：`docs/aedt-agent-executive-report.md`
- HTML：`benchmarks/reports/aedt_agent_executive_report.html`

这份 executive report 的口径：

- 用一句话解释 AEDT agent：把自然语言任务转成可执行、可验证、可追踪的 AEDT/PyAEDT 自动化流程。
- 用图展示 agent 闭环：用户需求 -> harness/agent -> 知识检索 -> 方案生成 -> PyAEDT/受控节点执行 -> AEDT -> validation -> 错误反馈/报告。
- 用表格解释 Stage A/B/C：
  - Stage A：证明知识检索有效。
  - Stage B：证明受控节点有效。
  - Stage C：产品化为 DAG workflow、可恢复执行、更强 validation。
- 对外强调当前效果和边界，不夸大为完整仿真物理正确。

## 重要经验

- C 组不能只给 task 的 `allowed_nodes`，因为真实 AEDT 会话是空模型。涉及端口/边界/face 的任务必须允许 prerequisite nodes 先创建最小几何。
- LLM 常把节点输入写成 PyAEDT 代码字符串，或者使用 `position/type/dimensions` 这类字段。Stage B 应该用 schema 示例和有限 normalization 吸收这类低价值错误。
- Trap 任务不能只验证对象是否存在。至少要验证节点间数据流是否符合预期，例如端口 assignment 来自选中的 face。
- 当前 Trap 判卷仍不是完整电磁语义判断；它是比“端口存在”更强的结构性检查。后续若要正式报告，需要继续增强几何/物理 validation。
- 多次 attempt 必须隔离 AEDT session。之前同一 task 的修复尝试复用同一 session，会导致第二轮在第一轮残留对象上成功，benchmark 证据不干净。
- B/C 小集显示 C 组的主要收益不是所有任务 first-pass 立刻更高，而是三轮内成功率更稳定、失败更可控、无自由代码执行；B 组在 wave port 上会出现自由代码难以修复的 runtime error 和长时间 harness timeout。

## 下一步

Stage A 与 Stage B MVP 当前都可以作为阶段性结果对外展示。下一步建议：

1. 若目标是跨团队汇报，优先使用 `benchmarks/reports/aedt_agent_executive_report.html`，而不是只展示 benchmark 明细。
2. 若目标是继续工程化，优先增强 validation：
   - wave port 是否绑定合理 face。
   - lumped port 是否绑定 port sheet/object。
   - sweep 是否挂到正确 setup。
   - radiation boundary 是否绑定 airbox/region。
3. 若目标是 Stage C，再开始 node schema 细分、DAG runtime、执行恢复、更大任务集和更强物理语义判卷。

报告已经展示：

- B/C 10-task 对照指标。
- B 组 `L2_dipole_antenna` 三轮失败原因。
- C 组节点化如何通过 schema、节点输出、真实 AEDT 执行和 validation 控制风险。
- 当前 validation 仍有限，Trap 的电磁语义仍只是结构性检查，不应夸大。
- 非仿真团队汇报版已经弱化 API 细节，重点说明 agent 架构、Stage A/B/C 区别、当前效果和未来目标。

## Stage C 启动记录

2026-05-16 已按新的产品化方向启动 Stage C。当前完成：

- Milestone 1：节点 catalog 产品化。
  - 新增 `NodeMetadata` / `NodeCatalog`。
  - 现有 8 个 Stage B 节点可导出 metadata/schema/version/ui_hints/postchecks。
  - catalog JSON 可给 UI、模板、聊天入口消费，不包含 Python callable 或本机路径。
- Milestone 2：workflow 数据模型。
  - 新增 `Workflow`, `WorkflowNode`, `WorkflowEdge`, `WorkflowParameter`, `WorkflowOutput`。
  - 支持 workflow JSON 读写、参数引用、节点输出引用、Stage B node plan 转换。
- Milestone 3：workflow validator。
  - 启动 AEDT 前校验未知节点、输入 schema、edge 引用、依赖顺序、高风险 prerequisite 和参数范围。
  - 输出结构化 errors/warnings，后续可供聊天修复 loop 使用。

验证结果：

```bash
.venv/bin/python -m pytest -q
```

- `145 passed, 2 skipped`

2026-05-16 继续完成 Milestone 4：`WorkflowExecutor`。

- 新增 `src/aedt_agent/workflow/executor.py`。
- workflow 执行会先跑 `WorkflowValidator`，validator 失败时不启动节点执行。
- 支持 workflow edge 注入 input，也支持 inputs 内 `{"$ref": ...}` 引用解析。
- 执行路径落到现有 `NodeExecutor.execute_node()`，保持自由 Python 执行次数为 0。
- 每个 step 记录 inputs、outputs、status、error、elapsed_seconds、snapshot_summary。
- 中途节点失败会停止并返回 repair_context。
- 支持从指定 step 开始继续执行，并允许传入 initial_step_outputs。
- artifact 输出包括 `workflow_run.json`、`validation.json`、轻量 `report.html`；`audit.jsonl` 仍由现有 `NodeExecutor`/`AuditLogger` 产生。

最新局部验证：

```bash
.venv/bin/python -m pytest tests/test_workflow_executor.py tests/test_workflow_validator.py tests/test_workflow_models.py -q
```

- `16 passed`

2026-05-16 继续完成 Milestone 5：模板 Workflow Catalog。

- 新增 `src/aedt_agent/workflow/templates.py`。
- 新增模板目录 `workflow_templates/`。
- 第一批模板：
  - `microstrip_sparameter`
  - `wave_port_setup`
  - `radiation_airbox_setup`
- 每个模板包含 workflow JSON、参数说明、适用场景、输出结果、validation checks、known limits。
- 支持模板加载、UI summary 导出、完整 workflow 导出、参数默认值覆盖。
- 所有模板均可通过 `WorkflowValidator`。

最新局部验证：

```bash
.venv/bin/python -m pytest tests/test_workflow_templates.py tests/test_workflow_executor.py tests/test_workflow_validator.py tests/test_workflow_models.py tests/test_node_catalog.py -q
```

- `25 passed`

2026-05-16 继续完成 Milestone 6：Inspector + 更强 Validation。

- 新增 `src/aedt_agent/validation/inspector.py`。
  - `inspect_aedt_model()` 可从 AEDT adapter/snapshot 生成统一模型事实。
  - facts 包含 objects、materials、faces、ports、boundaries、setups、sweeps、reports 和 summary counts。
- 新增 `src/aedt_agent/validation/rules.py`。
  - 支持 `object_exists`、`material_assigned`、`port_exists`、`port_assignment_valid`、`boundary_exists`、`setup_exists`、`sweep_exists`、`sweep_attached_to_setup`、`airbox_radiation_relation_valid`。
  - 输出机器可读 `ModelValidationResult`。
  - `validation_repair_context()` 可把 validation failure 转成修复上下文。
- 新增 `src/aedt_agent/validation/report.py`。
  - 输出人类可读 validation summary。

最新局部验证：

```bash
.venv/bin/python -m pytest tests/test_inspector_validation.py tests/test_workflow_templates.py tests/test_workflow_executor.py -q
```

- `15 passed`

2026-05-16 继续完成 Milestone 7：聊天生成 Workflow。

- 新增 `src/aedt_agent/chat/workflow_planner.py`。
  - 定义 `ChatPlannerInput` 和 `ChatPlannerOutput`。
  - 支持三条路径：直接选择模板、选择模板并填参数、生成简单 workflow。
  - planner 输出必须经过 `WorkflowValidator`。
  - 缺少关键参数时返回 `missing_information`，不启动 AEDT。
  - 目前是确定性 planner 骨架，后续可以接真实主模型，但输出仍限制为 workflow，不输出自由 PyAEDT 代码。
- 新增 `src/aedt_agent/chat/repair_context.py`。
  - 可把 workflow validation failure、step failure、model validation failure 汇总成人类可读 repair summary。
- 已覆盖模板选择：
  - microstrip S-parameter。
  - wave port setup。
  - radiation airbox setup。
  - 简单 setup workflow 生成。

最新局部验证：

```bash
.venv/bin/python -m pytest tests/test_chat_workflow_planner.py tests/test_workflow_templates.py tests/test_workflow_validator.py -q
```

- `18 passed`

2026-05-16 继续完成 Milestone 8：受控节点进化 proposal/evaluator。

- 新增 `src/aedt_agent/evolution/models.py`。
  - 定义 `NodeEvolutionEvidence`、`NodeEvolutionProposal`、`NodeEvolutionReport`。
  - proposal 字段包含 source、problem_pattern、affected_tasks、recommended_action、candidate_node_metadata、required_tests、risk_level、review_status。
- 新增 `src/aedt_agent/evolution/miner.py`。
  - 可从 Stage B `stage_b_report.json` 挖 failure pattern、repeated repair、node subgraph。
  - 可从 audit jsonl 挖 node usage、audit failure、session node sequence。
- 新增 `src/aedt_agent/evolution/proposer.py`。
  - 将 evidence 转成新增复合节点、增加 postcheck、增加 normalization、升级模板等 proposal。
  - 只生成 proposal，不修改 stable catalog。
- 新增 `src/aedt_agent/evolution/evaluator.py` 和 `policy.py`。
  - evaluator 会阻止缺少 schema/test/validator/AEDT smoke/manual gate/benchmark regression 的候选进入 candidate。
  - stable 发布必须 human approved。
- 已验证 proposal 可追溯到 benchmark/audit/workflow 证据。

最新局部验证：

```bash
.venv/bin/python -m pytest tests/test_node_evolution.py tests/test_chat_workflow_planner.py -q
```

- `13 passed`

下一步进入 Milestone 9：轻量产品 Demo。建议先做 CLI 命令，而不是完整 UI：列出模板、从聊天请求生成 workflow、运行模板 workflow、从 benchmark/audit 生成节点进化 proposal。

2026-05-16 继续完成 Milestone 9：轻量产品 Demo 与阶段性报告。

- 新增 demo scripts：
  - `scripts/list_node_catalog.py`
  - `scripts/list_workflow_templates.py`
  - `scripts/plan_workflow_from_chat.py`
  - `scripts/run_workflow_template.py`
  - `scripts/generate_node_evolution_report.py`
- 生成 demo artifacts：
  - `benchmarks/runs/stage_c_demo_microstrip/workflow_run.json`
  - `benchmarks/runs/stage_c_demo_microstrip/audit.jsonl`
  - `benchmarks/runs/stage_c_demo_microstrip/validation.json`
  - `benchmarks/runs/stage_c_demo_microstrip/report.html`
  - `benchmarks/reports/stage_c_chat_plan_sample.json`
  - `benchmarks/reports/stage_c_node_evolution_report.json`
- Demo 结果：
  - microstrip template run：`succeeded`，4 个节点，输出 `setup` 和 `sweep`。
  - chat planning sample：选择 `microstrip_sparameter`，confidence `0.82`，0 个 validation error。
  - node evolution report：23 条 evidence，11 条 proposal。
- 新增阶段性报告：
  - Markdown：`docs/aedt-agent-stage-c-progress-report.md`
  - HTML：`benchmarks/reports/aedt_agent_stage_c_progress_report.html`

Milestone 1-9 当前均已完成到 MVP 骨架。下一步建议不急做完整 UI，先补真实 AEDT smoke，并把 inspector/validation 接入正式 workflow run artifact。

UI 顺序确认：

- 完整拖拽 UI 最后做或至少靠后做。
- 当前阶段只保证数据结构和接口对 UI 友好，例如 catalog JSON、workflow JSON、template summary、run artifact、validation report。
- 先把底层链路跑通：节点 catalog -> workflow schema -> validator -> executor -> templates -> chat planner -> inspector/validation -> node evolution。
- 轻量 CLI/Web demo 可以作为阶段展示，但不提前投入完整图编辑器，避免 UI 绑定未稳定的节点和 workflow 模型。

2026-05-17 继续完成 Milestone 10：真实 AEDT workflow smoke 与正式 model validation artifact。

- 新增真实 smoke 脚本：
  - `scripts/run_stage_c_real_workflow_smoke.py`
  - 默认 `--adapter real`，可用 `--adapter fake` 做快速契约测试。
  - 默认 `PyaedtAdapter(non_graphical=True)`，本机 AEDT 2026.1 可通过 `~/ansys_inc/v261` 自动识别。
- `WorkflowExecutor` 正式接入 inspector/model validation：
  - `workflow_run.json` 包含 `model_facts` 和 `model_validation`。
  - `validation.json` 同时包含 workflow preflight validation、model validation、model facts。
  - model validation 失败时返回 `model_validation_failed` repair context。
- 修复真实 PyAEDT 下 setup/sweep 返回对象时的命名问题：
  - `create_setup` 输出稳定为 `setup.name` 或 fallback name。
  - `create_sweep_or_export` 输出稳定为 `sweep.name` 或 fallback name。
- 真实 AEDT smoke 已跑通：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py \
  --adapter real \
  --template microstrip_sparameter \
  --run-dir benchmarks/runs/stage_c_real_microstrip_smoke \
  --timeout-seconds 600
```

- 结果：
  - AEDT 2026.1 non-graphical 启动成功。
  - `microstrip_sparameter` workflow 执行成功。
  - 创建 `Substrate`、`Trace`、`Setup1`、`Sweep1`。
  - model validation：`Validation passed (3/3 checks).`
- 产物：
  - `benchmarks/runs/stage_c_real_microstrip_smoke/workflow_run.json`
  - `benchmarks/runs/stage_c_real_microstrip_smoke/validation.json`
  - `benchmarks/runs/stage_c_real_microstrip_smoke/audit.jsonl`
  - `benchmarks/runs/stage_c_real_microstrip_smoke/report.html`
  - `benchmarks/runs/stage_c_real_microstrip_smoke/smoke_summary.json`

最新验证：

```bash
.venv/bin/python -m pytest tests/test_workflow_executor.py tests/test_stage_c_demo_scripts.py -q
```

- `14 passed`

下一步建议：先补 node evolution proposal 的 HTML/人工审核报告，再补第二个真实 AEDT smoke，优先覆盖 port/boundary 类节点。完整拖拽 UI 仍靠后。

2026-05-17 继续完成 Milestone 11：第二个真实 AEDT smoke，覆盖 port 类节点。

- 使用现有 `wave_port_setup` 模板跑真实 AEDT：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py \
  --adapter real \
  --template wave_port_setup \
  --run-dir benchmarks/runs/stage_c_real_wave_port_smoke \
  --timeout-seconds 600
```

- 首次真实运行中，AEDT 成功创建 `Port1`，但 inspector 没有提取 PyAEDT boundary props，因此 `port_assignment_valid` 缺少 assignment 证据。
- 已修复：
  - `PyaedtAdapter.snapshot_state()` 现在从 boundary/port props 提取 `Faces`、`Objects`、`Sheets`、`Assignment`。
  - `port_assignment_valid` 支持从真实 PyAEDT props 中读取到的 face id list。
  - 增加 `tests/test_pyaedt_adapter_contract.py` 单测，锁住 port props/assignment 提取。
- 复跑真实 AEDT 成功：
  - AEDT 2026.1 non-graphical 启动成功。
  - `wave_port_setup` workflow 执行成功。
  - 创建 `WaveguideSection` 和 `Port1`。
  - 从真实 PyAEDT port props 抽取 `Faces: [12]`。
  - model validation：`Validation passed (3/3 checks).`
- 产物：
  - `benchmarks/runs/stage_c_real_wave_port_smoke/workflow_run.json`
  - `benchmarks/runs/stage_c_real_wave_port_smoke/validation.json`
  - `benchmarks/runs/stage_c_real_wave_port_smoke/audit.jsonl`
  - `benchmarks/runs/stage_c_real_wave_port_smoke/report.html`
  - `benchmarks/runs/stage_c_real_wave_port_smoke/smoke_summary.json`

下一步建议：先补 node evolution proposal 的 HTML/人工审核报告；若继续补真实 smoke，则第三个 smoke 优先覆盖 `create_airbox` + `assign_boundary`。

2026-05-17 继续完成 Milestone 12：第三个真实 AEDT smoke，覆盖 boundary 类节点。

- 使用现有 `radiation_airbox_setup` 模板跑真实 AEDT：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py \
  --adapter real \
  --template radiation_airbox_setup \
  --run-dir benchmarks/runs/stage_c_real_radiation_airbox_smoke \
  --timeout-seconds 600
```

- 初次真实运行已成功创建 `Radiator`、`AirBox`、`Radiation`。
- 为提高判据强度，已将模板 validation 从 2 条升级到 3 条：
  - `object_exists: AirBox`
  - `boundary_exists: Radiation`
  - `airbox_radiation_relation_valid: Radiation`
- 复跑真实 AEDT 成功：
  - AEDT 2026.1 non-graphical 启动成功。
  - `radiation_airbox_setup` workflow 执行成功。
  - 从真实 PyAEDT boundary props 抽取 `Objects: ["AirBox"]`。
  - model validation：`Validation passed (3/3 checks).`
- 产物：
  - `benchmarks/runs/stage_c_real_radiation_airbox_smoke/workflow_run.json`
  - `benchmarks/runs/stage_c_real_radiation_airbox_smoke/validation.json`
  - `benchmarks/runs/stage_c_real_radiation_airbox_smoke/audit.jsonl`
  - `benchmarks/runs/stage_c_real_radiation_airbox_smoke/report.html`
  - `benchmarks/runs/stage_c_real_radiation_airbox_smoke/smoke_summary.json`

当前真实 AEDT smoke 覆盖：

- `microstrip_sparameter`：geometry/setup/sweep。
- `wave_port_setup`：select_face/port。
- `radiation_airbox_setup`：airbox/boundary。

下一步建议：先补 node evolution proposal 的 HTML/人工审核报告，并把 3 个真实 smoke 汇总到更适合展示的 dashboard。

2026-05-17 继续完成 Milestone 13：真实 AEDT smoke dashboard。

- 新增脚本：
  - `scripts/generate_stage_c_smoke_dashboard.py`
- 默认读取：
  - `benchmarks/runs/stage_c_real_microstrip_smoke`
  - `benchmarks/runs/stage_c_real_wave_port_smoke`
  - `benchmarks/runs/stage_c_real_radiation_airbox_smoke`
- 生成：
  - `benchmarks/reports/stage_c_real_smoke_dashboard.html`
  - `benchmarks/reports/stage_c_real_smoke_dashboard.json`
- dashboard 汇总结果：
  - 3/3 真实 AEDT smoke 通过。
  - success rate：100%。
  - 覆盖：`airbox`、`boundary`、`geometry`、`port`、`selection`、`setup`、`sweep`。
- 新增测试：
  - `tests/test_stage_c_demo_scripts.py::test_generate_stage_c_smoke_dashboard_writes_html_and_json`

下一步建议：补 node evolution proposal 的 HTML/人工审核报告，把“节点进化”亮点做成可展示产物。

2026-05-17 继续完成 Milestone 14：节点进化 proposal 审核报告。

- 新增脚本：
  - `scripts/generate_node_evolution_review.py`
- 输入：
  - `benchmarks/reports/stage_c_node_evolution_report.json`
- 输出：
  - `benchmarks/reports/stage_c_node_evolution_review.html`
  - `benchmarks/reports/stage_c_node_evolution_review.json`
- 报告展示：
  - evidence 数量。
  - proposal 数量。
  - 推荐动作分布。
  - 风险分布。
  - 每个 proposal 的 candidate node、证据、required tests、gate 状态和 blockers。
- 当前结果：
  - 23 条 evidence。
  - 11 个 proposal。
  - 10 个 `add_node`，1 个 `add_normalization`。
  - 11 个 proposal 全部为 `needs_review`，符合“节点进化只能生成 proposal，不能自动发布 stable”的策略。
- 新增测试：
  - `tests/test_stage_c_demo_scripts.py::test_generate_node_evolution_review_writes_html_and_json`

下一步建议：整理当前 Stage C 产物，准备一次 clean-up/提交前检查；如果继续开发，可以做轻量 demo index 页面，把 progress report、真实 smoke dashboard、node evolution review 统一入口化。

2026-05-17 继续完成 Milestone 15：Stage C demo index。

- 新增脚本：
  - `scripts/generate_stage_c_demo_index.py`
- 生成：
  - `benchmarks/reports/stage_c_demo_index.html`
  - `benchmarks/reports/stage_c_demo_index.json`
- Index 统一入口：
  - Stage C 阶段性报告。
  - 真实 AEDT smoke dashboard。
  - 节点进化 proposal 审核报告。
  - 关键 JSON artifacts。
- 摘要指标来自已有 JSON：
  - 真实 smoke 3/3 通过。
  - 节点能力覆盖 7 类。
  - 节点进化 proposal 11 个。
- 新增测试：
  - `tests/test_stage_c_demo_scripts.py::test_generate_stage_c_demo_index_writes_html_and_json`

下一步建议：做 clean-up/提交前检查；重点看是否有空文件、重复报告、未纳入 git 的必要 artifacts，以及是否需要上传 GitHub。
