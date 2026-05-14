# AEDT Stage B Controlled Node MCP 实现计划

> **面向 AI 代理的工作者：** 使用 superpowers:executing-plans 或同等执行流程逐项推进。每个任务完成后运行对应测试，保持小步提交。

## 结论先行

Stage A 已结束，经验非常明确：

1. 离线判卷和 fake-only 验证不足以证明 PyAEDT 自动化真的可用。
2. A/B 的关键差异不是“提示词更长”，而是本地 harness 能不能查询官方 PyAEDT 源码、examples 和 GitNexus 图谱。
3. B 组已经达到三次内 100% 成功率，因此 Stage B 不能只追求 pass rate 提升；更重要的是验证节点化是否带来确定性、安全性、可审计性、更低修复轮次和更少自由代码。
4. node 不应该一开始做完整 DAG Runtime。先做受控节点执行层，真实 AEDT 跑通后再进入 Stage C 的图分解、节点组合和产品化。

因此，本计划将旧版 Stage B 从“fake AEDT + 静态 MCP 原型”调整为：

**以真实 AEDT non-graphical 执行为判据，实现最小可用的受控 node MCP，并用 Stage A 的 10 个任务和 B 组基线进行 Stage B/Candidate-C 对比。**

## Stage B 目标

Stage B 的目标不是替代 Stage A 的 grounded free-code harness，而是回答一个更具体的问题：

**在已经证明 GitNexus + 官方 PyAEDT grounding 有效之后，把高频、易错、可复用的 PyAEDT 操作封装成受控 node，是否能在真实 AEDT 中更稳定、更安全、更可审计地完成同一类任务？**

成功定义：

- `execute_node` 是正式执行入口。
- 真实 AEDT/PyAEDT adapter 是验收路径，fake adapter 只用于单元测试。
- 每个 node 有明确输入 schema、输出 schema、postcheck 和审计日志。
- harness 不再直接生成大段 PyAEDT Python 作为最终产物，而是生成 node plan JSON。
- benchmark 仍然使用 Stage A 的真实 AEDT 执行、最多三次修复和 HTML 报告机制。
- Stage B 报告需要同时展示 B 组 baseline 和 C 组 node-plan 结果。

## 非目标

本阶段不做：

- 完整 DAG Runtime。
- 可视化节点编辑器。
- 自动从 GitNexus 生成节点。
- 大规模 node graph 演化。
- 多 AEDT 实例并行调度。
- 用 fake AEDT 作为 benchmark 判据。

这些放到 Stage C 或更后面。

## 最终对比定义

### Group B：Stage A 最优基线

- 使用本地 harness CLI。
- GitNexus MCP 开启。
- 只读 PyAEDT 源码和 pyaedt-examples。
- 输出自由 PyAEDT Python。
- 真实 AEDT non-graphical 执行判定。

这是 Stage A 已验证有效的 grounded free-code baseline。

### Group C：Stage B node-plan 候选

- 使用同一个 harness CLI。
- 可以查询 GitNexus、PyAEDT 源码、examples 和 node catalog。
- 不能直接输出最终 PyAEDT 自由代码。
- 输出结构化 node plan JSON。
- benchmark runner 调用本地 MCP/kernel 的 `execute_node` 逐步执行。
- 每个 node 失败后，把 node 输入、真实 AEDT/PyAEDT 错误、postcheck 结果返回 harness 修复 node plan，最多三次。

Group C 评估的是：**受控节点是否能减少自由代码风险，并保持或接近 B 组的真实 AEDT 成功率。**

## 验收指标

Stage A B 组已经达到：

- 首轮成功率：80.0%
- 三次内成功率：100.0%
- 平均成功轮次：1.20

Stage B 不应只用“超过 100% 成功率”这种不可能的指标。建议验收分两层：

### 必须达成

- 单元测试全通过。
- 所有 node 执行都有 audit event。
- `execute_node` 不允许任意 Python 代码进入正式路径。
- dev-only `execute_script_restricted` 必须经过 AST guard，且不进入正式 benchmark。
- 真实 AEDT smoke tasks 至少覆盖：
  - `L1_create_substrate`
  - `L1_create_setup`
  - `L1_create_wave_port`
  - `L2_microstrip_line`
  - `Trap_waveport_wrong_face`
- 上述 smoke tasks 的 C 组 node-plan 三次内成功率达到 100%。

### 推荐达成

- 10 task 全量 benchmark 中，C 组三次内成功率 >= 80%。
- C 组首轮成功率 >= B 组首轮成功率的 80% 相对值，即 >= 64%。
- C 组平均成功轮次 <= B 组 + 0.5。
- C 组自由代码执行次数为 0。
- 不支持的任务必须明确返回 `unsupported_node_coverage`，不能用自由代码绕过。

## 文件结构

新增或重点修改：

```text
src/aedt_agent/mcp/__init__.py
src/aedt_agent/mcp/types.py
src/aedt_agent/mcp/ast_guard.py
src/aedt_agent/mcp/session_manager.py
src/aedt_agent/mcp/pyaedt_adapter.py
src/aedt_agent/mcp/fake_aedt.py
src/aedt_agent/mcp/execution_queue.py
src/aedt_agent/mcp/audit_log.py
src/aedt_agent/mcp/node_schemas.py
src/aedt_agent/mcp/node_executor.py
src/aedt_agent/mcp/tools.py
src/aedt_agent/mcp/server.py
src/aedt_agent/benchmark/runner_stage_b.py
src/aedt_agent/benchmark/report_html_stage_b.py
src/aedt_agent/validation/state_snapshot.py
src/aedt_agent/validation/rules.py
config/harness/group_c.json
config/stage_b_nodes.json
docs/stage-b-controlled-node-benchmark.md
tests/test_ast_guard.py
tests/test_session_manager.py
tests/test_pyaedt_adapter_contract.py
tests/test_execution_queue.py
tests/test_audit_log.py
tests/test_node_schemas.py
tests/test_node_executor.py
tests/test_mcp_tools.py
tests/test_stage_b_runner.py
```

保留：

```text
nodes/catalog/*.yaml
benchmarks/tasks/*.yaml
scripts/run_stage_a_benchmark.py
```

新增脚本：

```text
scripts/run_stage_b_benchmark.py
```

## Node 范围

先实现对 Stage A 失败和修复最敏感的最小节点集。

第一批节点：

- `create_substrate`
- `create_conductor_or_geometry_group`
- `create_airbox`
- `assign_boundary`
- `create_port`
- `create_setup`
- `create_sweep_or_export`

`select_face` 不单独作为首批正式执行 node。Stage A 经验表明 face 选择是高频错误源，但它更适合作为内部 helper/postcheck 能力，而不是让模型直接裸选 face id。`create_port` 内部应提供明确策略：

- `lumped_port_on_sheet`
- `wave_port_on_sheet`
- `wave_port_on_face_id`
- `microstrip_lumped_port_default`

后续如果 face helper 足够稳定，再暴露为只读查询工具。

## Node 输入输出原则

每个 node 必须满足：

- 输入是 JSON 可序列化对象。
- 输入 schema 显式列出 required/optional/default。
- 不接受任意 Python 表达式。
- 输出包含创建对象、边界、端口、setup、sweep 的稳定 id。
- postcheck 从真实 AEDT snapshot 读取状态。
- 失败返回结构化错误，不吞 traceback。

示例：

```json
{
  "node_id": "create_substrate",
  "inputs": {
    "name": "Substrate",
    "origin": [-25, -25, 0],
    "size": [50, 50, 1.6],
    "material": "FR4_epoxy",
    "units": "mm"
  }
}
```

输出：

```json
{
  "status": "succeeded",
  "created": {
    "objects": ["Substrate"]
  },
  "postcheck": {
    "passed": true,
    "checks": ["object_exists", "material_matches"]
  }
}
```

## 实现任务

### 任务 1：冻结 Stage A baseline 并定义 Stage B 报告 schema

**文件：**

- 创建：`docs/stage-b-controlled-node-benchmark.md`
- 创建：`src/aedt_agent/benchmark/stage_b_models.py`
- 创建：`tests/test_stage_b_models.py`

**步骤：**

- [ ] 记录 Stage A 最终 B 组指标作为 baseline：first-pass 80.0%，3-attempt 100.0%，avg success attempt 1.20。
- [ ] 定义 Stage B 报告字段：
  - `baseline_group_b`
  - `node_group_c`
  - `node_coverage`
  - `unsupported_tasks`
  - `free_code_execution_count`
  - `node_failures`
  - `audit_log_path`
- [ ] 添加测试，确认 Stage B metrics 能计算：
  - C 组三次内成功率
  - node 覆盖率
  - unsupported task 数
  - 平均 node 数
  - 平均修复次数
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/test_stage_b_models.py -q
```

### 任务 2：实现共享类型和 node schema

**文件：**

- 创建：`src/aedt_agent/mcp/types.py`
- 创建：`src/aedt_agent/mcp/node_schemas.py`
- 创建：`tests/test_node_schemas.py`

**步骤：**

- [ ] 定义 `SessionRef`、`ExecutionStatus`、`ExecutionResult`、`NodePlan`、`NodeStep`、`NodeResult`。
- [ ] 实现轻量 schema 校验，不引入复杂框架。
- [ ] 校验 unknown input、missing required、wrong primitive type。
- [ ] 输出错误必须结构化为 `schema_error`。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/test_node_schemas.py -q
```

### 任务 3：实现真实 PyAEDT adapter contract

**文件：**

- 创建：`src/aedt_agent/mcp/session_manager.py`
- 创建：`src/aedt_agent/mcp/pyaedt_adapter.py`
- 创建：`src/aedt_agent/mcp/fake_aedt.py`
- 创建：`tests/test_session_manager.py`
- 创建：`tests/test_pyaedt_adapter_contract.py`

**步骤：**

- [ ] `SessionManager` 管理 session/project/design 生命周期。
- [ ] `FakeAedtAdapter` 只用于单元测试，模拟最小对象/端口/setup 状态。
- [ ] `PyaedtAdapter` 使用 `ansys.aedt.core.Hfss`，默认 `non_graphical=True`。
- [ ] adapter 接口必须包含：
  - `health_check()`
  - `execute_node_callable(fn)`
  - `snapshot_state()`
  - `release()`
- [ ] `snapshot_state()` 至少返回 objects、materials、ports、boundaries、setups。
- [ ] 单元测试用 fake adapter。
- [ ] 真实 AEDT smoke 测试默认跳过，只有显式设置环境变量时运行：

```bash
RUN_REAL_AEDT=1 .venv/bin/python -m pytest tests/test_pyaedt_adapter_contract.py -q
```

### 任务 4：实现串行 execution queue 和 audit log

**文件：**

- 创建：`src/aedt_agent/mcp/execution_queue.py`
- 创建：`src/aedt_agent/mcp/audit_log.py`
- 创建：`tests/test_execution_queue.py`
- 创建：`tests/test_audit_log.py`

**步骤：**

- [ ] 同一 AEDT session 的写操作必须串行。
- [ ] timeout 要返回 `timeout`，不能让 benchmark 卡死。
- [ ] 每次 node 执行记录：
  - timestamp
  - session id
  - node id
  - inputs
  - state_before
  - state_after
  - result
  - elapsed_seconds
- [ ] audit log 使用 JSONL。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/test_execution_queue.py tests/test_audit_log.py -q
```

### 任务 5：实现第一批真实 node executor

**文件：**

- 创建：`src/aedt_agent/mcp/node_executor.py`
- 创建：`tests/test_node_executor.py`

**步骤：**

- [ ] 先用 fake adapter 测通 schema、dispatch、audit 和 postcheck。
- [ ] 实现节点：
  - `create_substrate`
  - `create_conductor_or_geometry_group`
  - `create_airbox`
  - `assign_boundary`
  - `create_port`
  - `create_setup`
  - `create_sweep_or_export`
- [ ] PyAEDT 调用必须使用 Stage A 已验证的签名：
  - `create_box(origin, sizes, ...)`
  - `create_rectangle(orientation, origin, sizes, ...)`
  - face 使用 `FacePrimitive.id` 和 `FacePrimitive.center`
  - HFSS port 前设置 `app.solution_type = "Modal"`
- [ ] `create_port` 必须内置安全默认：
  - planar/microstrip/patch 优先 lumped port sheet
  - explicit waveguide 才用 wave port
  - 不对 port face 使用不安全 PEC cap
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/test_node_executor.py -q
```

### 任务 6：真实 AEDT node smoke

**文件：**

- 创建：`tests/test_real_aedt_nodes.py`

**步骤：**

- [ ] 添加 `RUN_REAL_AEDT=1` 门控。
- [ ] 真实 AEDT 中运行：
  - create substrate
  - create setup
  - create lumped port minimal geometry
  - create wave port minimal waveguide
- [ ] 失败时保留 AEDT log 和 node audit log。
- [ ] 运行：

```bash
RUN_REAL_AEDT=1 .venv/bin/python -m pytest tests/test_real_aedt_nodes.py -q -s
```

### 任务 7：实现 MCP tools 和薄 server

**文件：**

- 创建：`src/aedt_agent/mcp/tools.py`
- 创建：`src/aedt_agent/mcp/server.py`
- 修改：`pyproject.toml`
- 创建：`tests/test_mcp_tools.py`

**步骤：**

- [ ] tool kernel 提供：
  - `create_session`
  - `release_session`
  - `list_available_nodes`
  - `describe_node`
  - `execute_node`
  - `get_model_info`
- [ ] `execute_script_restricted` 只允许 dev 模式，不出现在 Group C benchmark tool 列表中。
- [ ] FastMCP server 只包装 kernel，不复制业务逻辑。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/test_mcp_tools.py -q
```

### 任务 8：实现 Group C harness 配置和 node-plan 解析

**文件：**

- 创建：`config/harness/group_c.json`
- 创建：`src/aedt_agent/benchmark/node_plan_parser.py`
- 创建：`tests/test_node_plan_parser.py`

**步骤：**

- [ ] Group C prompt 要求输出 JSON，不输出 Python。
- [ ] JSON schema：

```json
{
  "plan": [
    {
      "node_id": "create_substrate",
      "inputs": {}
    }
  ]
}
```

- [ ] parser 必须能从 harness transcript 中提取最后一个合法 JSON plan。
- [ ] parser 失败返回 `generation_error`，不能尝试执行自由文本。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/test_node_plan_parser.py -q
```

### 任务 9：实现 Stage B benchmark runner

**文件：**

- 创建：`src/aedt_agent/benchmark/runner_stage_b.py`
- 创建：`scripts/run_stage_b_benchmark.py`
- 创建：`tests/test_stage_b_runner.py`

**步骤：**

- [ ] 支持 `--groups B C`。
- [ ] B 组复用 Stage A grounded free-code runner。
- [ ] C 组执行流程：
  - harness 生成 node plan
  - parser 提取 JSON
  - 按顺序调用 `execute_node`
  - 每步记录 audit
  - 全部 node 完成后运行任务 validation script
  - 失败时把 node result、AEDT log、postcheck 返回下一轮修复
- [ ] C 组禁止自由代码 fallback。
- [ ] 命令：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py --task L1_create_substrate --groups C --max-attempts 3
```

### 任务 10：Stage B smoke benchmark

**文件：**

- 不新增文件，除非修 bug。

**步骤：**

- [ ] 启动 GitNexus eval-server。
- [ ] 运行 5 个 smoke tasks：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --task L1_create_substrate \
  --task L1_create_setup \
  --task L1_create_wave_port \
  --task L2_microstrip_line \
  --task Trap_waveport_wrong_face \
  --groups B C \
  --max-attempts 3
```

- [ ] C 组必须三次内 100%。
- [ ] 检查 audit log、node plan、AEDT log 和 HTML 报告。
- [ ] 如果失败，优先修 node/schema/postcheck，不优先放宽成自由代码。

### 任务 11：Stage B 全量 benchmark 和中文报告

**文件：**

- 创建：`src/aedt_agent/benchmark/report_html_stage_b.py`
- 修改或创建：`benchmarks/reports/stage_b_report.html`

**步骤：**

- [ ] 运行 10 task 全量：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py --groups B C --max-attempts 3
```

- [ ] 中文 HTML 报告必须包含：
  - Stage A 经验回顾
  - B/C 方法对比
  - C 组 node coverage
  - C 组 unsupported tasks
  - C 组自由代码执行次数
  - task-level B/C 对比
  - node-level failure table
  - audit artifact links
- [ ] 报告里不能含 API key、本机绝对 home 路径或历史 prompt secret。

### 任务 12：Stage B Go/No-Go 复盘

**文件：**

- 创建：`docs/stage-b-go-no-go.md`

**步骤：**

- [ ] 记录最终指标。
- [ ] 判断是否进入 Stage C：
  - 如果 C 组 smoke 100%，全量 >= 80%，且自由代码次数 0：进入 Stage C node decomposition。
  - 如果 C 组 smoke 未达标：继续修 node executor，不进入 Stage C。
  - 如果 C 组全量低但 unsupported 明确：扩展 node coverage 后再评估。
- [ ] 明确 Stage C 输入：
  - 稳定 node 列表
  - 高失败 node 列表
  - 不适合节点化的任务类型
  - 需要图分解的复合任务类型

## 推荐执行顺序

1. 先实现 schema、session、queue、audit。
2. 再实现 fake-backed node executor 单元测试。
3. 再接真实 PyAEDT adapter smoke。
4. 再接 MCP tools。
5. 最后接 benchmark C 组。

不要一开始写完整 MCP server 或 DAG Runtime。Stage A 已经证明，能不能真实跑通 AEDT 才是核心。

## 自检清单

- [ ] 公开配置没有密钥。
- [ ] 报告没有本机 home 绝对路径。
- [ ] fake adapter 没有被用作 benchmark 判据。
- [ ] `execute_node` 是正式路径。
- [ ] Group C 不执行自由 Python。
- [ ] 真实 AEDT smoke 失败时能保留足够日志用于修复。
- [ ] 中文报告足够用于汇报。

