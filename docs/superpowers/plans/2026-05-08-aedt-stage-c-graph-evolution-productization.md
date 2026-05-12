# AEDT Stage C Graph Evolution Productization 计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 Stage A/B 已通过验收后，引入真实 AEDT adapter、图谱增强、节点进化、深度 semantic validation 和产品化入口。Stage C 不替代 Stage A/B，而是在其稳定基座上扩展。

**架构：** Stage C 采用插件化升级：`PyaedtAdapter` 替换 Fake Adapter，`KnowledgeProvider` 增加 GraphProvider，节点 catalog 引入版本治理和评估门禁，validation 从状态检查扩展到几何/仿真/物理规则。所有新增能力必须保持 `execute_node` 正式路径不变，不能重新开放裸脚本执行。

**技术栈：** Python 3.11+、pyaedt/ansys.aedt.core、SQLite/FTS5、可选 Graphify 或 GitNexus、pytest、FastMCP、可选轻量 Web UI。

---

## 进入 Stage C 的前置条件

Stage C 只能在以下条件满足后启动：

- [ ] Stage A Group C 相对 Group B semantic pass 提升大于或等于 15%。
- [ ] Stage A known trap 捕获率大于或等于 80%。
- [ ] Stage B `execute_node` fake adapter 全测试通过。
- [ ] Stage B `execute_script_restricted` 仍只作为开发期工具。
- [ ] Stage B `SessionManager`、`ExecutionQueue`、`AstGuard`、`NodeExecutor` 已稳定。
- [ ] 至少 5 个节点达到 `candidate-ready` 或 `stable`。

如果任一条件不满足，先回到 Stage A/B 修正，不进入 Stage C。

---

## 文件结构

Stage C 计划新增或修改：

```text
src/aedt_agent/mcp/pyaedt_adapter.py
src/aedt_agent/mcp/environment_check.py
src/aedt_agent/knowledge/graph_provider_interface.py
src/aedt_agent/knowledge/graphify_provider.py
src/aedt_agent/knowledge/gitnexus_provider.py
src/aedt_agent/nodes/versioning.py
src/aedt_agent/nodes/evaluation.py
src/aedt_agent/nodes/evolution.py
src/aedt_agent/validation/geometry_rules.py
src/aedt_agent/validation/simulation_rules.py
src/aedt_agent/validation/physics_rules.py
src/aedt_agent/workflow/models.py
src/aedt_agent/workflow/planner.py
src/aedt_agent/workflow/executor.py
docs/stage-c-gitnexus-authorization.md
docs/stage-c-real-aedt-validation.md
docs/stage-c-node-evolution-policy.md
tests/test_environment_check.py
tests/test_pyaedt_adapter_contract.py
tests/test_graph_provider_contract.py
tests/test_node_versioning.py
tests/test_node_evaluation.py
tests/test_semantic_validators.py
tests/test_workflow_planner.py
```

职责边界：

- `pyaedt_adapter.py`：真实 AEDT 连接，实现 Stage B 的 `AedtAdapter` 协议。
- `environment_check.py`：检测 Windows、AEDT、pyaedt、license、项目目录权限。
- `graph_provider_interface.py`：定义图谱增强能力，不污染 SQLite Provider。
- `graphify_provider.py` / `gitnexus_provider.py`：可插拔图谱后端。
- `nodes/versioning.py`：节点版本、兼容性、升级策略。
- `nodes/evaluation.py`：节点准入评分与回归结果。
- `nodes/evolution.py`：节点进化建议生成，默认人工审核。
- `validation/*_rules.py`：几何、仿真、物理三层 semantic validator。
- `workflow/*`：基于稳定节点的轻量 workflow planner/executor。

---

## 任务 1：真实 AEDT 环境自检

**目标：** 在接入 pyaedt 前先给出明确环境诊断，避免弱模型把连接失败误判成代码逻辑失败。

**文件：**
- 创建：`src/aedt_agent/mcp/environment_check.py`
- 创建：`tests/test_environment_check.py`
- 创建：`docs/stage-c-real-aedt-validation.md`

- [ ] 实现 `EnvironmentReport`，字段包括：

```text
os_name
is_windows
python_version
pyaedt_importable
aedt_version_hint
license_hint
workspace_writable
errors
warnings
```

- [ ] 实现 `check_environment(workspace: Path) -> EnvironmentReport`。

- [ ] 测试必须覆盖：

```text
非 Windows 环境给 warning，而不是崩溃
workspace 不可写时报 error
pyaedt 不可导入时报 warning
```

- [ ] 文档 `docs/stage-c-real-aedt-validation.md` 必须说明：

```text
真实 AEDT 验证需要 Windows + AEDT Desktop + license
CI 默认只跑 fake adapter
真实 AEDT 测试需人工或专用机器触发
```

**验收：**

```powershell
python -m pytest tests/test_environment_check.py -q
```

通过。

---

## 任务 2：实现 PyaedtAdapter 合同层

**目标：** 用真实 pyaedt 实现 Stage B 定义的 `AedtAdapter` 协议，但不改变 `SessionManager`、`ExecutionQueue`、`NodeExecutor`。

**文件：**
- 创建：`src/aedt_agent/mcp/pyaedt_adapter.py`
- 创建：`tests/test_pyaedt_adapter_contract.py`

- [ ] 定义 `PyaedtAdapter`，公开方法必须与协议一致：

```python
class PyaedtAdapter:
    def health_check(self) -> bool:
        ...

    def execute_code(self, code: str) -> dict:
        ...

    def snapshot_state(self) -> dict:
        ...
```

- [ ] `execute_code` 必须只接收已通过 `AstGuard` 的代码。

- [ ] `snapshot_state` 至少返回：

```text
project_id
design_id
objects
ports
boundaries
setups
reports
```

- [ ] 单元测试不启动 AEDT，只验证：

```text
PyaedtAdapter 有三个协议方法
缺少 pyaedt 时错误信息清晰
SessionManager 可接收 adapter_factory
```

- [ ] 真实 AEDT smoke test 作为手动命令写入文档，不默认进 pytest。

**验收：**

```powershell
python -m pytest tests/test_pyaedt_adapter_contract.py -q
```

通过。

---

## 任务 3：图谱 Provider 抽象

**目标：** 在不影响 SQLiteProvider 的前提下，为 Graphify/GitNexus 增加统一接口。

**文件：**
- 创建：`src/aedt_agent/knowledge/graph_provider_interface.py`
- 创建：`tests/test_graph_provider_contract.py`

- [ ] 定义图谱查询结果：

```text
GraphSymbol
  fqname
  symbol_type
  signature
  module
  neighbors
  source

GraphContext
  query
  symbols
  call_edges
  confidence
```

- [ ] 定义接口：

```python
class GraphProvider:
    def search_symbols(self, query: str, limit: int = 10) -> list[GraphSymbol]:
        ...

    def get_context(self, fqname: str, depth: int = 1) -> GraphContext:
        ...

    def get_api_scope_candidates(self, node_id: str, seed_apis: list[str]) -> list[str]:
        ...
```

- [ ] 测试用 fake provider 验证接口行为。

**验收：**

```powershell
python -m pytest tests/test_graph_provider_contract.py -q
```

通过。

---

## 任务 4：GraphifyProvider 作为 MIT 优先图谱后端

**目标：** 优先实现商业友好的图谱后端。Graphify 不要求成为主链路，只提供可选增强。

**文件：**
- 创建：`src/aedt_agent/knowledge/graphify_provider.py`
- 修改：`tests/test_graph_provider_contract.py`

- [ ] `GraphifyProvider` 从 Graphify 导出的 JSON 图读取符号和邻接关系。

- [ ] 支持：

```text
search_symbols(query)
get_context(fqname, depth)
get_api_scope_candidates(node_id, seed_apis)
```

- [ ] 测试使用小型 fixture JSON，不依赖真实 Graphify 安装。

- [ ] 如果 JSON schema 不匹配，返回清晰错误。

**验收：**

```powershell
python -m pytest tests/test_graph_provider_contract.py -q
```

通过。

---

## 任务 5：GitNexusProvider 作为授权后增强项

**目标：** 保留 GitNexus 的 Process/context/Cypher 价值，但明确授权门槛。

**文件：**
- 创建：`src/aedt_agent/knowledge/gitnexus_provider.py`
- 创建：`docs/stage-c-gitnexus-authorization.md`

- [ ] `GitNexusProvider` 必须默认禁用，只有显式配置 `ENABLE_GITNEXUS=1` 才可初始化。

- [ ] 初始化时必须检查授权配置：

```text
research_only=true
commercial_license_confirmed=true/false
```

- [ ] 未确认商业授权时，商业模式初始化必须失败并给出错误信息。

- [ ] 文档必须写清：

```text
GitNexus 使用 PolyForm Noncommercial
商业交付默认不依赖 GitNexus
Graphify/SQLite 是默认安全路线
```

**验收：**

```powershell
python -m pytest tests/test_graph_provider_contract.py -q
```

通过，且默认不会尝试启动 GitNexus。

---

## 任务 6：节点版本治理

**目标：** 防止节点进化变成维护地狱。节点版本必须可比较、可锁定、可回归。

**文件：**
- 创建：`src/aedt_agent/nodes/versioning.py`
- 创建：`tests/test_node_versioning.py`

- [ ] 实现：

```text
NodeVersion
NodeCompatibility
NodeUpgradePolicy
```

- [ ] 节点版本规则：

```text
patch: prompt/metadata 修复，不改变输入输出
minor: 新增可选输入或 validation
major: 改变输入输出或行为语义
```

- [ ] workflow 引用节点时必须锁定：

```text
node_id
version_constraint
```

- [ ] 旧 workflow 不自动升级 major version。

**验收：**

```powershell
python -m pytest tests/test_node_versioning.py -q
```

通过。

---

## 任务 7：节点评估与准入门禁

**目标：** 将 Stage A/B 指标固化为节点 catalog 准入机制。

**文件：**
- 创建：`src/aedt_agent/nodes/evaluation.py`
- 创建：`tests/test_node_evaluation.py`

- [ ] 实现 `NodeEvalResult`，字段包括：

```text
node_id
version
benchmark_count
two_round_success_rate
semantic_pass_rate
postcheck_coverage
known_trap_coverage
recommendation
```

- [ ] 实现 `evaluate_node_for_stability(result) -> recommendation`。

- [ ] 稳定准入规则：

```text
benchmark_count >= 3
two_round_success_rate >= 0.85
semantic_pass_rate >= 0.70
postcheck_coverage == 1.0
known_trap_coverage >= 0.80
```

- [ ] 不达标节点保持 `candidate`，不得进入 `stable`。

**验收：**

```powershell
python -m pytest tests/test_node_evaluation.py -q
```

通过。

---

## 任务 8：节点进化建议机制

**目标：** 支持报错驱动和评估驱动的节点改进，但默认人工审核。

**文件：**
- 创建：`src/aedt_agent/nodes/evolution.py`
- 创建：`docs/stage-c-node-evolution-policy.md`

- [ ] 实现 `EvolutionProposal`：

```text
proposal_id
node_id
current_version
trigger_type
observed_failures
proposed_change_type
proposed_diff_summary
risk_level
requires_human_review
```

- [ ] 支持触发类型：

```text
traceback_pattern
validation_failure
benchmark_regression
cost_optimization
user_request
```

- [ ] 所有 proposal 默认 `requires_human_review = true`。

- [ ] 文档明确禁止运行时自动生成新节点进入正式 catalog。

**验收：**

```powershell
python -m pytest tests/test_node_evaluation.py tests/test_node_versioning.py -q
```

通过。

---

## 任务 9：Geometry Semantic Validator

**目标：** 把 silent failure 的第一道防线从节点 postcheck 扩展到几何规则。

**文件：**
- 创建：`src/aedt_agent/validation/geometry_rules.py`
- 修改：`tests/test_semantic_validators.py`

- [ ] 实现规则：

```text
validate_ground_plane_exists
validate_airbox_padding
validate_port_face_is_exterior
validate_no_required_object_missing
```

- [ ] 输入使用 Stage B `snapshot_state()` 格式。

- [ ] 规则返回统一 `ValidationOutcome`。

- [ ] 测试覆盖：

```text
缺 ground 失败
airbox padding 不足失败
外部 face 检查通过
必需对象存在通过
```

**验收：**

```powershell
python -m pytest tests/test_semantic_validators.py -q
```

通过。

---

## 任务 10：Simulation Semantic Validator

**目标：** 检查 setup、sweep 和求解配置是否合理。

**文件：**
- 创建：`src/aedt_agent/validation/simulation_rules.py`
- 修改：`tests/test_semantic_validators.py`

- [ ] 实现规则：

```text
validate_setup_exists
validate_excitation_exists_before_solve
validate_sweep_covers_target_frequency
validate_convergence_settings_present
```

- [ ] 测试覆盖：

```text
无 setup 失败
无 port/excitation 失败
sweep 未覆盖目标频率失败
有 convergence 配置通过
```

**验收：**

```powershell
python -m pytest tests/test_semantic_validators.py -q
```

通过。

---

## 任务 11：Physics Semantic Validator

**目标：** 对求解结果做基础物理 sanity check，不替代专家判断。

**文件：**
- 创建：`src/aedt_agent/validation/physics_rules.py`
- 修改：`tests/test_semantic_validators.py`

- [ ] 实现规则：

```text
validate_sparameter_range
validate_resonance_presence_hint
validate_efficiency_range_if_available
```

- [ ] 明确规则性质：

```text
只给 warning 或 validation failure
不自动修改模型
不声称替代工程判断
```

- [ ] 测试使用合成结果数据，不依赖真实求解。

**验收：**

```powershell
python -m pytest tests/test_semantic_validators.py -q
```

通过。

---

## 任务 12：轻量 Workflow Planner

**目标：** 在不做完整可视化 DAG 平台的前提下，用稳定节点规划小型 workflow。

**文件：**
- 创建：`src/aedt_agent/workflow/models.py`
- 创建：`src/aedt_agent/workflow/planner.py`
- 创建：`tests/test_workflow_planner.py`

- [ ] 定义：

```text
WorkflowStep
WorkflowPlan
WorkflowEdge
```

- [ ] Planner 输入：

```text
natural_language_requirement
workflow_case_id
available_nodes
```

- [ ] MVP planner 可以基于 workflow case 的 `workflow_steps` 生成 plan，不需要大模型。

- [ ] 类型检查规则：

```text
ObjectId 不能直接接需要 FaceId 的节点
create_port 前必须有 select_face
create_setup 前必须有 excitation
```

**验收：**

```powershell
python -m pytest tests/test_workflow_planner.py -q
```

通过。

---

## 任务 13：轻量 Workflow Executor

**目标：** 串行执行 planner 生成的 workflow，仍然通过 `execute_node`，不绕过节点路径。

**文件：**
- 创建：`src/aedt_agent/workflow/executor.py`
- 修改：`tests/test_workflow_planner.py`

- [ ] Executor 输入：

```text
WorkflowPlan
session_id
McpToolKernel
```

- [ ] 执行策略：

```text
严格串行
每步记录 node result
失败立即停止
每步后读取 model_info
```

- [ ] 不实现并行 DAG。

- [ ] 不使用 `execute_script_restricted`。

**验收：**

```powershell
python -m pytest tests/test_workflow_planner.py -q
```

通过。

---

## 任务 14：产品化入口评估

**目标：** 在后端能力稳定后，决定是否做 CLI、MCP-only、或轻量 Web UI。

**文件：**
- 创建：`docs/stage-c-product-entrypoints.md`

- [ ] 文档比较三种产品入口：

```text
MCP-only: 最轻，适合接 Claude/Cursor/Codex
CLI: 适合离线企业环境和自动化评测
轻量 Web UI: 适合展示 workflow 和 validation，但不做完整 ComfyUI
```

- [ ] 推荐顺序：

```text
1. MCP-only
2. CLI
3. 轻量 Web UI
4. 完整节点编辑器
```

- [ ] 明确完整 ComfyUI 风格编辑器不进入 Stage C 初期。

**验收：**

文档中必须包含“何时不做 UI”的判断：

```text
如果 Stage B/C 的 validation 和 real AEDT adapter 仍不稳定，不投入 UI。
```

---

## 任务 15：Stage C 总体验收

**目标：** 确保 Stage C 没有破坏 Stage A/B 的安全边界。

**验收命令：**

```powershell
python -m pytest -q
```

必须通过。

**安全边界复核：**

- [ ] `execute_node` 仍是正式执行入口。
- [ ] `execute_script_restricted` 仍不出现在正式 workflow executor 中。
- [ ] GraphProvider 不替代 API 语义库，只做增强。
- [ ] GitNexus 默认禁用。
- [ ] 节点进化默认人工审核。
- [ ] Workflow executor 不做并行 AEDT 写操作。
- [ ] 真实 AEDT 测试有独立开关，不污染普通 CI。

**Stage C 完成定义：**

```text
Fake adapter 全测试通过
PyaedtAdapter 合同测试通过
GraphProvider 合同测试通过
节点版本/评估/进化策略通过测试
几何/仿真/物理 validator 通过合成数据测试
轻量 workflow planner/executor 通过 fake adapter 测试
```

---

## 自检

规格覆盖度：

- 真实 AEDT 接入：任务 1、2 覆盖。
- 图谱增强：任务 3、4、5 覆盖。
- 授权风险：任务 5 覆盖。
- 节点版本与进化：任务 6、7、8 覆盖。
- Semantic Validator：任务 9、10、11 覆盖。
- 工作流规划与执行：任务 12、13 覆盖。
- 产品入口：任务 14 覆盖。
- Stage A/B 安全边界复核：任务 15 覆盖。

未完成标记扫描：

- 本计划没有把未决内容作为实现步骤。
- Stage C 的未知外部依赖通过合同测试、文档和启用条件约束。

类型一致性：

- `PyaedtAdapter` 满足 Stage B 的 `AedtAdapter` 协议。
- `GraphProvider` 独立于 `KnowledgeProvider`，不污染 SQLite 路径。
- `WorkflowExecutor` 只依赖 `McpToolKernel.execute_node`。
- 节点进化只产生 proposal，不直接改 stable catalog。

