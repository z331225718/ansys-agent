# AEDT Stage C 节点化产品化计划

> 面向后续执行者：Stage C 的目标已经从“继续扩大 benchmark/图谱增强”调整为“把 Stage B 证明有效的节点化路径产品化”。执行时不要重新开放裸 Python 生成作为主路径，也不要把优化 agent 提前做成主线。

## 背景

Stage A 已证明：对 PyAEDT 这类高约束 API，官方源码/examples 和 GitNexus 检索能显著提升 LLM 生成成功率。

Stage B 已证明：受控节点计划比工具增强自由 Python 更可控。

- Stage A Group A：首轮 10%，三次内 30%。
- Stage A Group B：首轮 80%，三次内 100%。
- Stage B Group B：首轮 70%，三次内 90%。
- Stage B Group C：首轮 80%，三次内 100%，自由代码执行次数 0。

Stage C 不再以“LLM 能不能写对 PyAEDT 代码”为中心，而是把节点、模板、workflow、聊天入口、validation、audit 和受控节点进化做成一个可用的产品骨架。

## 产品目标

最终系统面向三类入口：

1. 有经验的工程师：通过拖拽少量节点，快速完成一整个 AEDT 仿真流程。
2. 新手工程师：选择现成模板/workflow，填写参数即可完成常见任务。
3. 聊天入口：用户与主模型对话，主模型自动判断任务、选择模板或生成节点 workflow。

Stage C 的核心交付不是 UI 完成度，而是产品化底座：

- 节点 catalog 稳定、可组合、可版本化。
- workflow 能表达完整仿真流程，而不是单个 API 调用。
- 模板能覆盖高频场景，并能被聊天入口复用。
- 节点进化能把 benchmark/真实使用中反复出现的失败，沉淀为新节点或已有节点能力升级。
- 执行层有真实 AEDT validation、audit、artifact 和修复闭环。
- 主模型只能生成/修改 node workflow，不直接执行任意 PyAEDT 代码。

UI 顺序：完整拖拽 UI 靠后做。Stage C 前半段只提供 UI 可消费的数据结构和轻量 demo 命令，优先把节点 catalog、workflow schema、validator、executor、模板、聊天 planner、validation 和节点进化跑通。否则 UI 会过早绑定不稳定的数据模型。

## 非目标

Stage C 暂不做：

- 通用优化 agent。
- 对 BRD/MCM/layout 导入模型的逐对象参数化。
- 大规模任务集扩展。
- 完整可视化 node editor。
- 完整云服务、多用户权限、作业调度平台。
- 宣称 validation 等价于完整电磁物理正确性。
- agent 自动发布新节点到 stable。

优化 agent 记录为后续增强方向。后续若做，应从 layout/EDB/3D Layout 中抽取工程语义，生成少量可制造、可解释的设计变量，再由优化器和仿真结果闭环搜索，而不是让 LLM 参数化每个对象。

## 架构方向

Stage C 推荐架构：

```text
用户入口
  ├─ 拖拽节点
  ├─ 模板 workflow
  └─ 聊天生成 workflow
        ↓
Workflow Model
        ↓
Node Catalog + Schema + Version
        ↓
Workflow Validator
        ↓
Controlled Executor
        ↓
PyAEDT / AEDT 2026.1 non-graphical
        ↓
Inspector + Validation
        ↓
Audit / Artifact / Report / Repair Context
```

关键原则：

- `execute_node` / controlled node executor 继续作为正式执行路径。
- LLM 的输出是 workflow JSON，不是任意 Python。
- 每个节点必须有输入 schema、输出 schema、引用关系、postcheck 和 audit 字段。
- workflow validation 必须在启动 AEDT 前尽早发现缺参数、引用错误、节点顺序错误。
- AEDT 执行后的 inspector 负责抽取模型事实，再交给 validation 判定。
- 节点进化必须走候选节点、回归测试、人工审核、版本发布路径，不能让 LLM 在正式执行中临时扩展任意代码。

## 建议文件结构

```text
src/aedt_agent/nodes/catalog.py
src/aedt_agent/nodes/metadata.py
src/aedt_agent/nodes/versioning.py
src/aedt_agent/workflow/models.py
src/aedt_agent/workflow/validator.py
src/aedt_agent/workflow/executor.py
src/aedt_agent/workflow/templates.py
src/aedt_agent/workflow/planner.py
src/aedt_agent/evolution/models.py
src/aedt_agent/evolution/miner.py
src/aedt_agent/evolution/proposer.py
src/aedt_agent/evolution/evaluator.py
src/aedt_agent/evolution/policy.py
src/aedt_agent/validation/inspector.py
src/aedt_agent/validation/rules.py
src/aedt_agent/validation/report.py
src/aedt_agent/chat/workflow_planner.py
src/aedt_agent/chat/repair_context.py
docs/stage-c-node-productization.md
docs/stage-c-workflow-schema.md
docs/stage-c-template-catalog.md
docs/stage-c-chat-workflow-generation.md
docs/stage-c-node-evolution-policy.md
tests/test_node_catalog.py
tests/test_workflow_models.py
tests/test_workflow_validator.py
tests/test_workflow_templates.py
tests/test_workflow_executor.py
tests/test_inspector_validation.py
tests/test_chat_workflow_planner.py
tests/test_node_evolution.py
```

## Milestone 1：节点 catalog 产品化

**目标：** 把 Stage B 的节点从“benchmark 可用”整理成“可被 UI、模板、聊天入口共同消费”的 catalog。

任务：

- [x] 定义 `NodeMetadata`：
  - `node_id`
  - `display_name`
  - `category`
  - `description`
  - `input_schema`
  - `output_schema`
  - `required_capabilities`
  - `version`
  - `stability`
  - `ui_hints`
  - `postchecks`
- [x] 定义节点分类：
  - geometry
  - material
  - boundary
  - port
  - setup
  - sweep
  - report/export
  - validation
- [x] 为现有 Stage B 节点补 metadata。
- [x] 明确节点稳定性等级：
  - experimental
  - candidate
  - stable
  - deprecated
- [x] 输出可用于前端/模板/聊天入口的 catalog JSON。

验收：

```bash
.venv/bin/python -m pytest tests/test_node_catalog.py -q
```

必须证明：

- 每个可执行节点都有 metadata。
- catalog JSON 不包含 Python callable 或本机路径。
- 节点 input/output schema 可被序列化。

## Milestone 2：Workflow 数据模型

**目标：** 定义完整 workflow 表达，不再只是一串 benchmark node calls。

建议模型：

```json
{
  "workflow_id": "microstrip_sparameter_v1",
  "name": "Microstrip S-Parameter",
  "version": "0.1.0",
  "parameters": {},
  "nodes": [
    {
      "id": "substrate",
      "node_id": "create_conductor_or_geometry_group",
      "inputs": {}
    }
  ],
  "edges": [
    {
      "from": "substrate.output.object_name",
      "to": "wave_port.inputs.reference"
    }
  ],
  "validation": [],
  "outputs": []
}
```

任务：

- [x] 实现 `Workflow`, `WorkflowNode`, `WorkflowEdge`, `WorkflowParameter`, `WorkflowOutput`。
- [x] 支持 workflow 级参数：
  - 默认值
  - 单位
  - 合法范围
  - UI label
- [x] 支持节点引用：
  - `node_id.output.field`
  - workflow parameter 引用
- [x] 支持 workflow JSON 读写。
- [x] 保持和现有 Stage B node plan 的兼容转换。

验收：

```bash
.venv/bin/python -m pytest tests/test_workflow_models.py -q
```

## Milestone 3：Workflow Validator

**目标：** 在启动 AEDT 前发现 workflow 的结构问题，减少无意义的 AEDT 启动和长 timeout。

任务：

- [x] 校验节点是否存在于 catalog。
- [x] 校验输入是否满足节点 schema。
- [x] 校验 edge 引用是否存在。
- [x] 校验节点顺序或 DAG 依赖是否可解析。
- [x] 校验单位/参数范围。
- [x] 校验高风险节点的 prerequisite：
  - port 前必须有几何/face/object 引用。
  - boundary 前必须有 region/airbox/object。
  - sweep 前必须有 setup。
- [x] 输出结构化错误，供聊天修复 loop 使用。

验收：

```bash
.venv/bin/python -m pytest tests/test_workflow_validator.py -q
```

## Milestone 4：Workflow Executor

**目标：** 把 workflow 执行统一落到现有 controlled node executor，不允许自由代码 fallback。

任务：

- [x] 实现 `WorkflowExecutor`。
- [x] 将 workflow DAG/topological order 转成 `execute_node` 调用。
- [x] 保存每个节点的：
  - inputs
  - outputs
  - elapsed time
  - AEDT snapshot summary
  - postcheck result
  - error log
- [x] 支持失败后停止并返回 repair context。
- [x] 支持从指定节点继续执行，但必须保证依赖状态可验证。
- [x] 输出统一 artifact：
  - `workflow_run.json`
  - `audit.jsonl`
  - `validation.json`
  - `report.html`

验收：

```bash
.venv/bin/python -m pytest tests/test_workflow_executor.py -q
```

真实 AEDT smoke 作为手动命令，不默认进入 CI。

## Milestone 5：模板 Workflow Catalog

**目标：** 让新手可以直接选择模板，不需要从空白节点图开始。

第一批模板建议：

- HFSS microstrip S-parameter。
- HFSS wave port setup。
- HFSS lumped port setup。
- HFSS radiation boundary + airbox。
- Simple antenna setup。

每个模板必须包含：

- workflow JSON。
- 参数说明。
- 适用场景。
- 输出结果。
- validation checks。
- 已知限制。

任务：

- [x] 定义 `WorkflowTemplate`。
- [x] 实现模板加载与参数实例化。
- [x] 至少沉淀 3 个模板。
- [x] 模板必须可由 validator 通过。
- [x] 模板必须能导出给 UI。

验收：

```bash
.venv/bin/python -m pytest tests/test_workflow_templates.py -q
```

## Milestone 6：Inspector + 更强 Validation

**目标：** 把判定依据从“节点无异常”升级为“真实 AEDT 模型事实满足 workflow 目标”。

任务：

- [x] 实现 `inspect_aedt_model()`，输出统一模型事实：
  - objects
  - materials
  - faces/ref summaries
  - ports
  - boundaries
  - setups
  - sweeps
  - reports
- [x] 定义 validation rule：
  - object exists
  - material assigned
  - port exists
  - port assignment valid
  - boundary exists
  - setup exists
  - sweep attached to setup
  - airbox/radiation relation valid
- [x] validation 输出机器可读 JSON 和人类可读 summary。
- [x] repair context 必须包含 validation failure，而不是只包含 Python exception。

验收：

```bash
.venv/bin/python -m pytest tests/test_inspector_validation.py -q
```

## Milestone 7：聊天生成 Workflow

**目标：** 主模型根据用户需求选择模板或生成节点 workflow，而不是直接写 PyAEDT。

任务：

- [x] 定义聊天 planner 输入：
  - user request
  - node catalog
  - workflow templates
  - optional retrieved context
- [x] 定义 planner 输出：
  - selected_template 或 generated_workflow
  - missing_information
  - assumptions
  - confidence
  - validation_errors
- [x] 支持三条路径：
  - 直接选择模板。
  - 选择模板并填参数。
  - 从节点 catalog 生成新 workflow。
- [x] planner 输出必须经过 workflow validator。
- [x] 若缺少关键参数，返回澄清问题，不启动 AEDT。
- [x] 若执行失败，把 audit/validation/error log 汇总成 repair context，再让 planner 修改 workflow。

验收：

```bash
.venv/bin/python -m pytest tests/test_chat_workflow_planner.py -q
```

## Milestone 8：受控节点进化

**目标：** 把“节点进化”作为 Stage C 亮点，但必须保持受控：从失败模式和高频 workflow 中发现节点缺口，生成候选方案，经过 benchmark/validation 回归和人工审核后才进入 catalog。

节点进化解决的问题：

- 某类任务总需要多个低层节点拼接，用户拖拽成本高。
- 聊天入口反复生成相同子图，说明应该沉淀为模板或复合节点。
- benchmark/真实执行中反复出现同类失败，说明节点 schema、normalization、postcheck 或引用机制需要升级。
- 官方 examples/GitNexus 中存在稳定 API 模式，可以沉淀为节点能力。

受控流程：

```text
audit / benchmark / user workflows
        ↓
failure + usage pattern mining
        ↓
node gap report
        ↓
candidate node proposal
        ↓
schema + implementation draft
        ↓
unit tests + fake adapter tests
        ↓
real AEDT smoke / benchmark regression
        ↓
human review
        ↓
candidate/stable catalog release
```

任务：

- [x] 定义 `NodeEvolutionProposal`：
  - `proposal_id`
  - `source`
  - `problem_pattern`
  - `affected_tasks`
  - `recommended_action`
  - `candidate_node_metadata`
  - `required_tests`
  - `risk_level`
  - `review_status`
- [x] 实现 failure miner：
  - 从 benchmark report / audit jsonl 中统计高频失败。
  - 识别 repeated repair patterns。
  - 识别常被组合在一起的 node subgraph。
- [x] 实现 proposer：
  - 输出“新增节点 / 升级节点 schema / 增加 normalization / 增加 postcheck / 升级模板”的建议。
  - 不直接修改 stable catalog。
- [x] 实现 evaluator：
  - 候选节点必须有单元测试。
  - 候选节点必须通过相关 workflow validator。
  - 若涉及 AEDT 行为，必须有真实 AEDT smoke 或标记为 manual-gated。
  - 候选节点不得降低现有 benchmark 成功率。
- [x] 定义 release policy：
  - experimental -> candidate -> stable -> deprecated。
  - 每次升级必须记录版本、兼容性和迁移说明。
  - stable 发布需要人工审核。
- [x] 生成节点进化报告：
  - 当前节点缺口。
  - 推荐新增/升级节点。
  - 证据来源。
  - 风险和验收标准。

验收：

```bash
.venv/bin/python -m pytest tests/test_node_evolution.py -q
```

必须证明：

- LLM/agent 只能生成 proposal，不能自动把节点发布成 stable。
- proposal 可以追溯到 benchmark/audit/workflow 证据。
- evaluator 能阻止缺少 schema/test/validation 的候选节点进入 candidate。

## Milestone 9：轻量产品 Demo

**目标：** 先证明产品链路，不追求完整 UI。

建议先做 CLI/TUI 或极简 Web：

```bash
.venv/bin/python scripts/list_workflow_templates.py
.venv/bin/python scripts/run_workflow_template.py --template microstrip_sparameter --params params.json
.venv/bin/python scripts/plan_workflow_from_chat.py --request "create a microstrip s-parameter simulation"
```

Demo 必须展示：

- [x] 查看节点 catalog。
- [x] 查看模板列表。
- [x] 选择模板并填参数。
- [x] 从自然语言生成 workflow。
- [x] 从 benchmark/audit 生成节点进化 proposal。
- [x] validator 拦截缺参/错误引用。
- [x] controlled executor 执行 workflow。
- [x] HTML 报告展示 audit、validation、artifact。

## Milestone 10：真实 AEDT Workflow Smoke

**目标：** 证明 Stage C 的产品链路不是只在 fake adapter 上成立，而是可以通过同一条 controlled executor 路径启动 AEDT 2026.1 non-graphical 并完成一个完整 workflow。

任务：

- [x] 增加真实 smoke 启动脚本：
  - `scripts/run_stage_c_real_workflow_smoke.py`
  - 默认 adapter 为 `real`，可用 `--adapter fake` 做快速契约测试。
  - 使用 `PyaedtAdapter`，默认 `non_graphical=True`。
  - 输出 `workflow_run.json`、`validation.json`、`audit.jsonl`、`report.html`、`smoke_summary.json`。
- [x] 将 inspector/model validation 接入 `WorkflowExecutor` 正式 artifact：
  - `workflow_run.json` 包含 `model_facts` 和 `model_validation`。
  - `validation.json` 同时包含 workflow preflight validation 和 AEDT model validation。
  - validation 失败时返回 `model_validation_failed` repair context。
- [x] 规范 PyAEDT 返回对象名，避免 setup/sweep 输出变成对象 repr。
- [x] 跑通真实 AEDT smoke：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py \
  --adapter real \
  --template microstrip_sparameter \
  --run-dir benchmarks/runs/stage_c_real_microstrip_smoke \
  --timeout-seconds 600
```

结果：

- AEDT 2026.1 non-graphical 启动成功。
- `microstrip_sparameter` workflow 执行成功。
- 创建 `Substrate`、`Trace`、`Setup1`、`Sweep1`。
- model validation：`Validation passed (3/3 checks).`

## Milestone 11：第二个真实 AEDT Smoke（Port 类节点）

**目标：** 在第一个真实 smoke 覆盖 geometry/setup/sweep 后，继续覆盖 port 类节点，证明 `select_face -> create_port` 也能在真实 AEDT 中通过 controlled workflow 执行和模型事实 validation。

任务：

- [x] 用现有 `wave_port_setup` 模板跑真实 AEDT smoke。
- [x] 修正 `PyaedtAdapter.snapshot_state()`，从 PyAEDT boundary/port props 中提取 assignment 证据：
  - `Faces`
  - `Objects`
  - `Sheets`
  - `Assignment`
- [x] 增强 `port_assignment_valid`，支持从真实 PyAEDT props 中读取到的 face id list。
- [x] 增加单测锁住 PyAEDT boundary props 提取逻辑。

执行命令：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py \
  --adapter real \
  --template wave_port_setup \
  --run-dir benchmarks/runs/stage_c_real_wave_port_smoke \
  --timeout-seconds 600
```

结果：

- AEDT 2026.1 non-graphical 启动成功。
- `wave_port_setup` workflow 执行成功。
- 创建 `WaveguideSection` 和 `Port1`。
- 从真实 PyAEDT port props 抽取 `Faces: [12]`。
- model validation：`Validation passed (3/3 checks).`

## Milestone 12：第三个真实 AEDT Smoke（Boundary 类节点）

**目标：** 覆盖 `create_airbox + assign_boundary`，证明真实 AEDT 下 radiation boundary 不只是创建成功，还能从 PyAEDT props 中提取 assignment 证据并通过模型事实 validation。

任务：

- [x] 用现有 `radiation_airbox_setup` 模板跑真实 AEDT smoke。
- [x] 将模板 validation 从“boundary exists”增强为“radiation boundary 必须引用 AirBox”：
  - `object_exists: AirBox`
  - `boundary_exists: Radiation`
  - `airbox_radiation_relation_valid: Radiation`
- [x] 复用 `PyaedtAdapter.snapshot_state()` 的 boundary props 提取能力，从真实 PyAEDT `Objects` 中抽取 assignment。

执行命令：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py \
  --adapter real \
  --template radiation_airbox_setup \
  --run-dir benchmarks/runs/stage_c_real_radiation_airbox_smoke \
  --timeout-seconds 600
```

结果：

- AEDT 2026.1 non-graphical 启动成功。
- `radiation_airbox_setup` workflow 执行成功。
- 创建 `Radiator`、`AirBox`、`Radiation`。
- 从真实 PyAEDT boundary props 抽取 `Objects: ["AirBox"]`。
- model validation：`Validation passed (3/3 checks).`

## Milestone 13：真实 AEDT Smoke Dashboard

**目标：** 把 3 个真实 AEDT smoke 的结果汇总成一个适合展示的中文 dashboard，避免汇报时逐个打开 run 目录和 JSON。

任务：

- [x] 新增脚本：
  - `scripts/generate_stage_c_smoke_dashboard.py`
- [x] 默认读取 3 个真实 run：
  - `benchmarks/runs/stage_c_real_microstrip_smoke`
  - `benchmarks/runs/stage_c_real_wave_port_smoke`
  - `benchmarks/runs/stage_c_real_radiation_airbox_smoke`
- [x] 输出：
  - `benchmarks/reports/stage_c_real_smoke_dashboard.html`
  - `benchmarks/reports/stage_c_real_smoke_dashboard.json`
- [x] dashboard 展示：
  - 真实 smoke 通过数。
  - validation success rate。
  - 覆盖的节点能力。
  - 每个模板的节点序列、validation summary 和 artifact 路径。

结果：

- 3/3 真实 AEDT smoke 通过。
- 覆盖：`airbox`、`boundary`、`geometry`、`port`、`selection`、`setup`、`sweep`。

## Milestone 14：节点进化 Proposal 审核报告

**目标：** 把节点进化从 JSON 数据变成可展示、可审核的报告，同时明确边界：proposal 不是 stable 节点，必须经过测试、真实 AEDT/manual gate、benchmark regression 和人工审核。

任务：

- [x] 新增脚本：
  - `scripts/generate_node_evolution_review.py`
- [x] 输入：
  - `benchmarks/reports/stage_c_node_evolution_report.json`
- [x] 输出：
  - `benchmarks/reports/stage_c_node_evolution_review.html`
  - `benchmarks/reports/stage_c_node_evolution_review.json`
- [x] 报告展示：
  - evidence 数量。
  - proposal 数量。
  - 推荐动作分布。
  - 风险分布。
  - 每个 proposal 的 candidate node、证据、required tests、gate 状态和 blockers。
- [x] 默认所有 proposal 仍保持审核态，不自动发布 stable。

结果：

- 23 条 evidence。
- 11 个 proposal。
- 10 个 `add_node`，1 个 `add_normalization`。
- 11 个 proposal 全部为 `needs_review`，符合受控节点进化策略。

## Milestone 15：Stage C Demo Index

**目标：** 给汇报和演示提供一个统一入口，不再让观众分别打开多个 HTML/JSON 文件。

任务：

- [x] 新增脚本：
  - `scripts/generate_stage_c_demo_index.py`
- [x] 输出：
  - `benchmarks/reports/stage_c_demo_index.html`
  - `benchmarks/reports/stage_c_demo_index.json`
- [x] Index 汇总：
  - Stage C 阶段性报告。
  - 真实 AEDT smoke dashboard。
  - 节点进化 proposal 审核报告。
  - 关键 JSON artifacts。
- [x] 从已有 smoke/evolution JSON 中读取摘要指标：
  - 真实 smoke 3/3 通过。
  - 节点能力覆盖 7 类。
  - 节点进化 proposal 11 个。

## Stage C 成功标准

Stage C MVP 完成时应满足：

- 至少 3 个 workflow 模板可运行。
- 至少 8 个节点有完整 metadata/schema/version。
- 至少 1 个节点进化 proposal 能从 benchmark/audit 中自动生成，并被 evaluator 拦截或准入为 candidate。
- workflow validator 能拦截常见结构错误。
- 聊天入口能在模板选择和简单 workflow 生成之间做判断。
- 执行路径仍是 controlled node executor，自由 Python 执行次数为 0。
- 每次 workflow run 都有 audit、validation、artifact 和 HTML report。
- 真实 AEDT smoke 至少覆盖 1 个完整 workflow。
- 单元测试通过，报告不包含本机绝对路径、API key、base URL。

## 建议执行顺序

1. 节点 catalog metadata。
2. workflow model。
3. workflow validator。
4. workflow executor。
5. workflow templates。
6. inspector + validation rules。
7. chat workflow planner。
8. node evolution proposal/evaluator。
9. demo/report。

优先级判断：

- 能提升节点可组合性的，优先。
- 能让模板复用节点的，优先。
- 能让聊天入口不写自由代码的，优先。
- 能把高频失败沉淀为受控节点进化 proposal 的，优先。
- 纯图谱增强、自动发布节点、优化 agent 暂缓。

## 当前阶段的工程边界

Stage C 仍然可以继续用 GitNexus/官方 examples 作为知识来源，但它们只作为 planner 的检索上下文，不是产品主轴。产品主轴是：

```text
稳定节点 -> 可验证 workflow -> 模板 -> 聊天生成/修复 -> 节点进化 proposal -> 真实 AEDT 执行报告
```
