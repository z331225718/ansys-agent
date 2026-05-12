# AEDT-MCP 节点化智能仿真系统 — 完整工程规格书

> 文档状态：设计规格，已通过 brainstorming 决策  
> 日期：2026-05-08  
> 范围：Stage A/B 可执行蓝图，Stage C 仅保留升级接口与触发条件  
> 输入材料：`AEDT-MCP 节点化智能仿真系统 — 设计讨论稿.md`、LLM_A/B/reviewA、Codex/Gemini/GLM Agent Teams 最终方案

---

## 1. 项目定位

### 1.1 渐进式目标

本系统采用渐进式定位：

1. **短期**：HFSS 工程师提效工具。用户默认理解基本电磁仿真概念，系统帮助其更快完成建模、激励、边界、setup、sweep、S 参数导出，并减少 pyaedt API 试错。
2. **长期**：降低 AEDT 学习门槛。通过节点化 workflow、类型约束、validation 反馈和案例库，让新手逐步理解仿真流程，而不是直接相信黑盒脚本。

第一版不是聊天机器人，也不是薄 MCP 脚本执行器，而是 **节点化智能仿真执行与评估基座**。

### 1.2 第一版范围

Stage A/B 严格限定为 HFSS-only。

包含：

- 几何建模的最小闭环
- 材料与导体创建
- 面选择
- 端口创建
- airbox 与边界
- setup 与 sweep
- S 参数导出
- API 语义检索
- 结构化案例检索
- common traps 检索
- 自动判卷
- 少量真实 AEDT 抽检
- 受控 MCP 执行队列
- 静态节点原型

不包含：

- Maxwell、Icepak、Mechanical、EDB、Optimetrics
- 复杂后处理
- 完整可视化节点编辑器
- 动态节点生成
- DAG 并行执行同一个 AEDT 实例
- 商业版强依赖 GitNexus
- 向正式用户开放裸 `execute_script`

### 1.3 成功定义

一个任务成功不等于“代码没报错”。成功必须同时满足：

```text
代码语法通过
AND API 调用基本合法
AND 真实 AEDT 抽检时可执行
AND validation script 或节点 postcheck 通过
AND AEDT 状态摘要可记录
```

---

## 2. 总体架构

系统采用轻量融合架构。Stage A 同时验证 grounding 和伪节点价值，Stage B 固化为受控 MCP + 静态节点，Stage C 再考虑图谱增强和可视化。

```text
用户自然语言
  -> 调度/编排层
  -> 静态节点或伪节点
  -> KnowledgeProvider 检索上下文
  -> 受控代码生成或模板填参
  -> AEDT MCP 执行队列
  -> validation/postcheck
  -> 反馈与修复
```

### 2.1 调度/编排层

职责：

- 理解用户目标
- 选择 workflow pattern
- 分解为节点序列
- 提取参数
- 检查节点输入输出类型

Stage A 可用离线 prompt、手工 workflow 或简单规则模拟调度器，不实现完整 DAG Runtime。

Stage B 固化首批静态节点，并通过 `execute_node` 执行。

### 2.2 节点层

节点是系统的工程边界，而不仅是 UI 元素。

每个节点必须定义：

- 输入类型
- 输出类型
- 参数 schema
- API 白名单
- 知识检索 query
- precheck
- execution 方式
- postcheck
- validation rules
- failure modes

Stage A 允许伪节点和候选节点。Stage B 只有通过 Benchmark 的节点才能进入稳定 catalog。

### 2.3 KnowledgeProvider

知识层采用接口抽象，避免绑定某个存储。

```text
KnowledgeProvider
  - SQLiteProvider: MVP 默认，SQLite + FTS5
  - JsonProvider: 离线最小方案，JSONL/YAML/Markdown
  - GitNexusProvider: Stage C 研究增强项
  - GraphifyProvider: GitNexus 授权不可接受时的图谱备选
```

MVP 使用 SQLite/JSON 核心存储，但规格中保留 Provider 接口，便于后续接入 GitNexus 或 Graphify。

### 2.4 MCP 执行层

AEDT MCP Server 维护持久化 HFSS/pyaedt 实例。所有写操作必须进入单实例 FIFO 队列，串行执行。

MCP 执行层负责：

- session 管理
- project/design 绑定
- 全局锁
- timeout watchdog
- checkpoint
- AEDT 健康检查
- AST 审计
- 审计日志
- traceback 分类
- validation 结果回传

---

## 3. 阶段策略

### 3.1 Stage A：离线判卷 + 少量真实 AEDT 抽检

目标：验证轻量融合方案是否显著优于裸 LLM 和基础 docstring 注入。

Stage A 产出：

- Top 50 HFSS API 清单
- `api_semantics.schema.sql`
- `api_semantics.seed.jsonl`
- 10-15 个 workflow cases
- 5-10 个 common traps
- 30 个 Benchmark 任务
- validation script 规范
- Group A/B/C 实验报告
- `candidate-ready` 节点清单

Stage A 不做：

- 完整 MCP server
- 完整节点 Runtime
- 可视化编辑器
- GitNexus 接入

### 3.2 Stage B：受控 MCP + 静态节点原型

目标：把 Stage A 中表现稳定的伪节点固化为静态节点，接入真实 AEDT。

Stage B 产出：

- FastMCP AEDT Server 原型
- `execute_node` 正式工具
- `execute_script_restricted` 开发期工具
- session manager
- execution queue
- AST guard
- 首批 8 个节点 YAML
- 节点 precheck/postcheck
- 真实 AEDT 抽检报告

### 3.3 Stage C：图谱增强 + 节点进化

Stage C 仅在 Stage A/B 指标通过后启动。

Stage C 可做：

- `GitNexusProvider` 或 `GraphifyProvider`
- 完整 DAG Runtime
- 节点版本治理
- 节点进化机制
- 更深的 Semantic Validator
- 可视化节点编辑器

GitNexus 只有在授权明确且 ROI 被 Benchmark 证明后，才能进入主链路。

---

## 4. 数据资产与 Schema

### 4.1 API 语义库

主存储：SQLite + FTS5。  
导出格式：JSONL。

核心表：`api_semantics`

```text
fqname              TEXT PRIMARY KEY
domain              TEXT
category            TEXT
signature           TEXT
params_json         TEXT
returns_json        TEXT
docstring           TEXT
constraints_json    TEXT
common_errors_json  TEXT
common_traps_json   TEXT
examples_ref_json   TEXT
source_refs_json    TEXT
confidence          TEXT
pyaedt_version      TEXT
aedt_version        TEXT
last_verified_at    TEXT
```

字段约束：

- `fqname` 使用全限定名，例如 `Hfss.modeler.create_box`
- `domain` 第一版固定为 `hfss`
- `category` 取值：`geometry`、`material`、`boundary`、`excitation`、`setup`、`postprocess`
- `confidence` 取值：`verified`、`manual`、`inferred`

抽取原则：

- 自动抽取：signature、type hints、default values、docstring
- 人工精标：hidden constraints、common traps、物理约束
- 测试沉淀：常见报错、validation failure、修复建议

每条约束必须有 `source_refs_json`，禁止没有来源的“权威化猜测”。

### 4.2 结构化案例库

路径：`knowledge/workflow_cases/<case_id>.yaml`

格式：

```yaml
case_id: hfss_patch_antenna
domain: hfss
task_type: antenna
natural_language_task: "设计一个 2.4GHz 矩形贴片天线并导出 S11"
workflow_steps:
  - create_substrate
  - create_conductor_or_geometry_group
  - select_face
  - create_port
  - create_airbox
  - assign_boundary
  - create_setup
  - create_sweep_or_export
api_used:
  - Hfss.modeler.create_box
  - Hfss.modeler.create_rectangle
parameters:
  frequency: "2.4GHz"
reference_script: "benchmarks/reference_scripts/hfss_patch_antenna.py"
validation_script: "benchmarks/validation_scripts/validate_hfss_patch_antenna.py"
expected_state:
  objects:
    - substrate
    - patch
    - ground
known_traps:
  - missing_ground_plane
  - airbox_too_small
notes: "案例必须结构化摘要，不直接把大段脚本塞进 prompt。"
```

首批案例：

- `hfss_patch_antenna`
- `microstrip_line`
- `rectangular_waveguide`
- `waveguide_filter`
- `coaxial_feed`
- `cavity_resonator`
- `simple_sparameter_export`

### 4.3 Common Traps

路径：`knowledge/common_traps/<trap_id>.yaml`

格式：

```yaml
trap_id: waveport_no_background_contact
domain: hfss
applies_to:
  - create_port
  - Hfss.create_wave_port
symptom: "端口创建看似成功，但边界条件或求解结果异常"
root_cause: "Wave Port 面没有完全接触背景区域"
why_silent: "部分情况下 AEDT 不会在建模阶段直接抛出明确异常"
detection: "检查端口面与 airbox/background 的拓扑接触关系"
prevention: "create_port 前必须通过 select_face 和 port precheck"
validation_rule: "validate_port_face_touches_background"
source: "manual"
```

首批 traps：

- `waveport_no_background_contact`
- `airbox_too_small`
- `missing_ground_plane`
- `wrong_face_selected_for_port`
- `sweep_range_misses_target_frequency`
- `material_or_unit_mismatch`
- `boundary_assigned_to_wrong_object`

### 4.4 Benchmark 任务

路径：`benchmarks/tasks/<task_id>.yaml`

格式：

```yaml
task_id: L3_patch_antenna_sparameter
level: L3
domain: hfss
requirement: "创建 2.4GHz 贴片天线，设置 radiation 边界，求解并导出 S11"
allowed_nodes:
  - create_substrate
  - create_conductor_or_geometry_group
  - select_face
  - create_port
  - create_airbox
  - assign_boundary
  - create_setup
  - create_sweep_or_export
expected_workflow:
  - create_substrate
  - create_conductor_or_geometry_group
  - select_face
  - create_port
  - create_airbox
  - assign_boundary
  - create_setup
  - create_sweep_or_export
required_api_categories:
  - geometry
  - excitation
  - boundary
  - setup
reference_script: "benchmarks/reference_scripts/L3_patch_antenna_sparameter.py"
validation_script: "benchmarks/validation_scripts/validate_L3_patch_antenna_sparameter.py"
expected_outputs:
  - "S11 report or exported touchstone file"
known_failure_modes:
  - missing_ground_plane
  - wrong_face_selected_for_port
grading:
  syntax_pass: true
  api_pass: true
  runtime_pass_sampled: true
  semantic_pass_required: true
```

任务分层：

- L1：10 个单节点任务
- L2：10 个小工作流任务
- L3：5 个完整闭环任务
- Trap：5 个反直觉陷阱任务

总计 30 个任务。

### 4.5 节点定义

路径：`nodes/catalog/<node_id>.yaml`

格式：

```yaml
id: create_substrate
version: 0.1.0
status: candidate
category: geometry
description: "创建 HFSS 介质基板"
inputs:
  app:
    type: HfssSession
    required: true
  length:
    type: Quantity
    required: true
  width:
    type: Quantity
    required: true
  thickness:
    type: Quantity
    required: true
  material:
    type: MaterialName
    required: true
outputs:
  substrate_id:
    type: ObjectId
api_whitelist:
  - Hfss.modeler.create_box
  - Hfss.assign_material
knowledge_queries:
  - "HFSS create substrate box assign material"
precheck:
  - validate_positive_dimensions
  - validate_material_exists_or_creatable
execution:
  mode: generated_code_or_template
postcheck:
  - validate_object_exists
  - validate_object_material
  - validate_object_dimensions
validation_rules:
  - substrate_dimensions_within_tolerance
examples_ref:
  - hfss_patch_antenna
failure_modes:
  - unit_mismatch
  - material_not_found
```

`status` 取值：

- `pseudo`
- `candidate`
- `candidate-ready`
- `stable`
- `deprecated`

Stage A 可使用 `pseudo` 和 `candidate`。Stage B 只执行 `candidate-ready` 或 `stable`。

---

## 5. 首批节点与 GeometryGroup

### 5.1 首批 8 个闭环节点

1. `create_substrate`  
   创建介质基板。输入尺寸、厚度、材料、坐标原点策略；输出 `ObjectId`。

2. `create_conductor_or_geometry_group`  
   建模占位节点。用于创建贴片、地、导体、波导壁、馈电几何等。Stage A/B 可先覆盖常见 conductor/sheet/box，长期必须拆分。必须标注 `decomposition_pending: true`。

3. `select_face`  
   从 `ObjectId` 和选择策略得到 `FaceId`。这是端口和边界正确性的关键节点。

4. `create_port`  
   创建 wave port、lumped port 或 coaxial port。输入 `FaceId`、端口类型、积分线策略；输出 `PortId`。

5. `create_airbox`  
   按频率和几何包围盒创建 airbox。输入工作频率、padding 策略；输出 `ObjectId`。

6. `assign_boundary`  
   创建 radiation、perfect E、impedance 等边界。输入对象或面；输出 `BoundaryId`。

7. `create_setup`  
   创建求解设置。输入中心频率、最大迭代、收敛阈值等；输出 `SetupId`。

8. `create_sweep_or_export`  
   Stage A/B 合并 sweep 和 S 参数导出，确保闭环可跑。Stage C 再拆成 `create_sweep` 与 `export_sparameters`。

### 5.2 `select_face` 必须独立

端口和边界最常见的 silent failure 是选错面，而不是 API 拼错。`FaceId` 选择必须是可观察、可验证、可替换的节点，不能隐藏在 `create_port` 或 `assign_boundary` 内部。

### 5.3 GeometryGroup 拆分策略

`create_conductor_or_geometry_group` 是过渡节点，不是长期稳定设计。

后续候选拆分：

- `create_box_body`
- `create_sheet_conductor`
- `create_ground`
- `create_patch`
- `create_feed_geometry`
- `boolean_unite_subtract`
- `assign_material`
- `set_coordinate_system`
- `parametrize_geometry`

拆分触发条件：

- 某类几何失败在 Benchmark 中出现 3 次或更多
- GeometryGroup 的 API 白名单超过 8 个
- postcheck 无法准确判断建模是否正确
- 不同任务复用同一子几何模式 3 次或更多
- 节点输出类型开始混乱，例如同时输出 `ObjectId`、`FaceId`、`Material`

### 5.4 节点稳定准入条件

节点从 `candidate` 进入 `stable` 必须满足：

- 有明确 input/output schema
- API 白名单不超过 5 个；GeometryGroup 例外但必须标记待拆分
- 至少有 3 个 Benchmark 任务覆盖
- postcheck 覆盖 100%
- 已知 traps 有对应 validation rule
- 两轮内成功率大于或等于 85%
- semantic pass 大于或等于 70%

---

## 6. 执行层、安全边界与 MCP 工具

### 6.1 执行接口裁决

开发/评测期允许受限脚本执行。正式用户路径只开放节点执行。

```text
开发期：execute_script_restricted
正式期：execute_node
```

正式系统不得向普通用户开放裸 `execute_script`。即使底层使用 Python 代码，也必须隐藏在节点执行和安全审计之后。

### 6.2 MCP 工具

开发期工具：

```text
search_api(query)
list_examples(task_type)
get_model_info(session_id)
execute_script_restricted(code, context)
run_validation(task_id, session_id)
```

正式路径工具：

```text
execute_node(node_id, inputs, session_id)
get_node_status(run_id)
get_model_info(session_id)
run_validation(task_id, session_id)
list_available_nodes()
```

避免在正式工具名中使用 `execute_script`，因为该名称会诱导调度层绕过节点边界。

### 6.3 `execute_script_restricted` 安全规则

受限脚本执行必须执行以下检查：

- `ast.parse`
- import 白名单
- 禁止 `os`
- 禁止 `sys`
- 禁止 `subprocess`
- 禁止 `socket`
- 禁止 `shutil`
- 禁止危险 `pathlib` 用法
- 禁止 `open()` 写入非 session 目录
- 禁止删除、移动、覆盖非 session 目录文件
- 禁止访问非当前 project/design
- 限制最大执行时间
- 记录审计日志
- 执行前后保存 AEDT 状态摘要

安全检查失败时，MCP 必须拒绝执行并返回结构化错误。

### 6.4 AEDT Session 管理

每次执行必须绑定：

```text
session_id
project_id
design_id
transaction_id
node_id 或 task_id
```

Session manager 负责：

- 持久化 HFSS app 实例
- AEDT 健康检查
- 全局锁
- FIFO 任务队列
- timeout watchdog
- checkpoint 保存
- 崩溃后重启和恢复
- traceback 分类
- validation 结果回传

### 6.5 错误处理

错误分层：

- Syntax error：节点生成层修复
- API not found / wrong args：KnowledgeProvider 或节点 prompt 修复
- Runtime AEDT error：MCP 返回 traceback，最多自动修复 2 次
- Semantic validation failure：不得假装成功，进入修复或人工审查
- AEDT hang/crash：中断队列，重启 session，从 checkpoint 恢复

---

## 7. Benchmark 与 Go/No-Go

### 7.1 三组对照

```text
Group A: 裸 LLM
  用户需求 -> 直接生成 pyaedt

Group B: 基础 Grounding
  用户需求 -> 注入 API 签名/docstring -> 生成 pyaedt

Group C: 轻量融合
  用户需求 -> 伪节点/API 白名单 + API 语义 + workflow case + common traps -> 生成 pyaedt
```

### 7.2 评估方式

- 全量任务做离线判卷
- 每个任务类型抽样做真实 AEDT 执行
- Trap 任务必须进入真实 AEDT 抽检
- 真实 AEDT 不要求全量求解，但必须检查 AEDT 内部状态
- 对需要 S 参数的 L3 任务，保留少量端到端求解样本

### 7.3 评估层级

1. Syntax Pass：`ast.parse` 通过，无明显危险代码。
2. API Pass：调用的 API 存在，参数名和必填项基本匹配。
3. Runtime Pass：真实 AEDT 抽检中可执行，不抛异常。
4. Semantic Pass：validation script 判断 AEDT 状态符合预期。
5. Repair Efficiency：最多 2 轮修复内是否成功。

### 7.4 核心指标

```text
API Pass: Group C >= 95%
Group C 相对 Group B 的 Semantic Pass 提升 >= 15%
Group C 两轮内成功率 >= 85%
Group C Semantic Pass >= 70%
Known Trap 捕获率 >= 80%
平均修复次数 <= 0.7
检索上下文 <= 8k tokens
Top 50 API 覆盖率 >= 85%
```

### 7.5 Go 条件

进入 Stage B 必须满足：

- Group C 比 Group B semantic pass 提升大于或等于 15%
- Trap 捕获率大于或等于 80%
- 至少 3 个任务类型真实 AEDT 抽检通过
- 首批节点中至少 5 个达到 `candidate-ready`

### 7.6 No-Go 修正策略

- API Pass 不足：先修 API 语义库和白名单
- Runtime Pass 不足：先修 pyaedt 真实调用样例
- Semantic Pass 不足：先修 validation 和 common traps
- Group C 比 Group B 提升不足 10%：重新评估轻量融合 ROI，不进入 Stage B 大开发

---

## 8. 推荐目录结构

```text
docs/
  superpowers/
    specs/
      2026-05-08-aedt-node-simulation-design.md

knowledge/
  api_semantics/
    api_semantics.schema.sql
    api_semantics.seed.jsonl
  workflow_cases/
    hfss_patch_antenna.yaml
    microstrip_line.yaml
    rectangular_waveguide.yaml
  common_traps/
    waveport_no_background_contact.yaml
    airbox_too_small.yaml
    missing_ground_plane.yaml

benchmarks/
  tasks/
    L1_create_substrate.yaml
    L2_microstrip_line.yaml
    L3_patch_antenna_sparameter.yaml
    Trap_waveport_wrong_face.yaml
  reference_scripts/
  validation_scripts/
  reports/

nodes/
  catalog/
    create_substrate.yaml
    create_conductor_or_geometry_group.yaml
    select_face.yaml
    create_port.yaml
    create_airbox.yaml
    assign_boundary.yaml
    create_setup.yaml
    create_sweep_or_export.yaml

src/
  knowledge/
    provider_interface.py
    sqlite_provider.py
  benchmark/
    runner.py
    graders.py
  mcp/
    server.py
    session_manager.py
    execution_queue.py
    ast_guard.py
  nodes/
    registry.py
    executor.py
  validation/
    state_snapshot.py
    rules.py
```

---

## 9. Stage 交付物

### 9.1 Stage A 交付物

- API 语义库 schema
- Top 50 API seed 数据
- 结构化案例库
- common traps 初版
- 30 个 Benchmark 任务
- validation script 规范
- Group A/B/C 评估 runner
- Stage A 实验报告
- candidate-ready 节点清单

### 9.2 Stage B 交付物

- FastMCP AEDT Server
- `execute_node`
- `execute_script_restricted`
- session manager
- execution queue
- AST guard
- checkpoint 与审计日志
- 8 个节点 YAML
- 节点 postcheck
- 真实 AEDT 抽检报告

### 9.3 Stage C 预留项

- `GitNexusProvider`
- `GraphifyProvider`
- 完整 DAG Runtime
- 可视化节点编辑器
- 节点进化机制
- 深度 Semantic Validator

Stage C 项目不得在 Stage A/B Go 条件通过前启动。

---

## 10. 反目标

MVP 明确不做：

1. 不做全求解器支持
2. 不做完整 ComfyUI 式前端
3. 不做动态节点生成
4. 不做 DAG 并行执行同一个 AEDT 实例
5. 不把 GitNexus 作为商业版核心依赖
6. 不开放裸 `execute_script` 给正式用户
7. 不承诺自动抽取完整 API 语义
8. 不用 demo 级 Benchmark 冒充工程验证

---

## 11. 规格自检结果

未完成标记扫描：

- 无未完成任务标记
- 无未决需求标记
- Stage C 均标为预留项和触发条件

内部一致性：

- Stage A 为离线判卷 + 少量真实 AEDT 抽检
- Stage B 为受控 MCP + 静态节点
- `execute_script_restricted` 仅开发期可用，正式路径为 `execute_node`
- GitNexus 仅为 Stage C 可插拔 Provider

范围检查：

- 本规格聚焦 Stage A/B，可由一个 implementation plan 覆盖
- Stage C 不展开为当前实现任务

模糊性处理：

- 首批节点选择固定为 8 个
- GeometryGroup 明确为待拆分过渡节点
- Go/No-Go 指标明确量化
- 数据 schema 和路径明确

---

## 12. 最终结论

本项目的核心不是让 LLM 记住 pyaedt，而是把 AEDT/HFSS 仿真流程拆成可约束、可检查、可复用、可演进的工程节点。

执行路线是：

```text
先用 Stage A 证明轻量融合有效
再用 Stage B 接入受控 MCP 和静态节点
最后在 Stage C 引入图谱、节点进化和可视化
```

如果 Stage A/B 指标通过，项目继续扩展。若指标不通过，应优先修正数据资产、validation 和节点边界，而不是堆叠更复杂的架构。
