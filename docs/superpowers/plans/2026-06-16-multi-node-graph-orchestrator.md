# Multi-Node Graph Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前线性 Graph Runner 升级为真正的多节点 DAG 编排器：支持 fan-out、分支条件、节点级失败策略、retry、动态节点实例化。使 `brd_local_cut_build` 模板能按 planner → validator → worker(×N) → scorecard → approval_gate 的多分支结构推进。

**Architecture:** 增量改造现有 `graph_runner.py`、`graph_scheduler.py`、`graph_template.py`、`graph_executors.py`，不破坏已有单链行为。新增 `FanOutNode` 边类型、`on_failure` 策略、`retry` 配置、`if` 条件边、`expand` 动态节点。所有已有测试必须保持绿色。

**Tech Stack:** Python 3.11+、dataclasses、YAML、pytest。

---

## 文件结构

- `src/aedt_agent/agent/graph_template.py`：新增 `GraphNode.on_failure`、`GraphNode.retry`、`GraphEdge.if_condition`、`GraphNode.expand`。
- `src/aedt_agent/agent/graph_scheduler.py`：支持 fan-out handoff 消费、条件边评估。
- `src/aedt_agent/agent/graph_runner.py`：fan-out wave 应用、失败策略路由、retry 循环。
- `src/aedt_agent/agent/graph_executors.py`：fan-out 执行结果支持多 outcome、`expand` handler。
- `docs/agent_templates/brd_local_cut_build.yaml`：升级为多节点 fan-out 模板。
- `docs/agent_templates/brd_real_solve_evidence.yaml`：保持不变，验证向后兼容。
- `tests/test_agent_graph_runner_dag.py`：新增 fan-out / failure-policy / retry / conditional / expand 测试。
- `tests/test_agent_graph_template.py`：新增模板解析测试。
- `tests/test_agent_graph_scheduler.py`：新增调度测试。

---

## Task 1：节点级失败策略 `on_failure`

**目标：** 节点失败时不直接 crash 整个 graph，而是按配置策略处理。

### YAML 语法

```yaml
nodes:
  - id: risky_worker
    role: worker
    kind: worker
    capability: brd.local_cut.build
    on_failure: skip        # skip | fail | retry | fallback:<node_id>
    retry:
      max_attempts: 3
      backoff: linear       # linear | exponential | constant
      delay_seconds: 2
```

### 行为

| `on_failure` | 行为 |
|---|---|
| `fail`（默认） | 全局 graph 失败（现有行为） |
| `skip` | 节点标记 SKIPPED，创建 `on: skipped` 边 handoff |
| `retry` | 使用 `retry` 配置重新执行节点 |
| `fallback:<node_id>` | 创建 `on: failed` 边 + 额外 `on: fallback` handoff 到指定节点 |

- [ ] **Step 1：扩展 `GraphNode` 数据类**

在 `GraphNode` 中新增字段：

```python
on_failure: str = "fail"       # fail | skip | retry | fallback:<node_id>
retry_max_attempts: int = 1
retry_backoff: str = "constant"  # constant | linear | exponential
retry_delay_seconds: float = 0.0
```

更新 `to_json_dict()`、`_node_from_mapping()` 和 YAML 解析。

- [ ] **Step 2：写失败策略测试**

`tests/test_agent_graph_runner_dag.py` 新增：

```python
def test_on_failure_skip_creates_skipped_edge_and_continues():
    """节点失败 + on_failure=skip → 节点 SKIPPED，后续节点继续执行"""

def test_on_failure_fail_stops_graph():
    """节点失败 + on_failure=fail（默认） → graph FAILED"""

def test_on_failure_retry_with_backoff():
    """节点失败 + on_failure=retry + max_attempts=3 → 重试后成功"""

def test_on_failure_fallback_routes_to_alternate_node():
    """节点失败 + on_failure=fallback:recovery → 路由到 recovery 节点"""

def test_on_failure_retry_exhausted_becomes_fail():
    """retry 耗尽 → 转为 fail 行为"""
```

- [ ] **Step 3：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_runner_dag.py -k "on_failure"
```

- [ ] **Step 4：实现失败策略路由**

在 `_apply_wave_results()` 中，当 `result.status == NodeRunStatus.FAILED` 时：

1. 读取 `node.on_failure`
2. `fail` → 现有行为，全局失败
3. `skip` → `runtime.store.complete_node_run(SKIPPED)`，创建 `on: skipped` handoff
4. `retry` → 检查 `retry_count`，未耗尽则重新入队；耗尽则按 `fail` 处理
5. `fallback:<id>` → 创建 `on: failed` + `on: fallback` 两条 handoff

在 `NodeRunRecord` 中需要新增 `retry_count` 字段或在 graph_runner 中自行追踪。

- [ ] **Step 5：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_runner_dag.py tests/test_agent_graph_template.py -k "on_failure"
```

---

## Task 2：Fan-out 支持

**目标：** 一个节点可以同时触发多条下游边（多个 outcome），实现并行分支。

### YAML 语法

```yaml
edges:
  - id: score-to-channel-1
    from: channel_score
    to: scorecard_ch1
    on: fan_out
    fan_out_group: channel_scoring
  - id: score-to-channel-2
    from: channel_score
    to: scorecard_ch2
    on: fan_out
    fan_out_group: channel_scoring
```

或者更简单：同一 `from` + `on` 的多条边 = fan-out。

### 行为

- 节点的 `edge_outcome` 可以被设置为 `fan_out`。
- 当节点 outcome 为 `fan_out` 或节点配置了 `fan_out: true`，所有匹配该 outcome 的边同时被激活。
- 每条边独立创建 handoff，下游节点按 `join` 策略汇聚。

- [ ] **Step 1：扩展 `GraphNode` 支持 fan_out 标记**

```python
fan_out: bool = False
```

当 `fan_out=True` 或 `edge_outcome == "fan_out"` 时，同时遍历所有 `on` 匹配的边。

- [ ] **Step 2：写 Fan-out 测试**

```python
def test_fan_out_creates_multiple_parallel_handoffs():
    """一个 worker fan_out → 3 条边 → 3 个下游节点并行"""

def test_fan_out_with_join_all_converges():
    """fan_out 后 join:all 汇聚"""
```

- [ ] **Step 3：确认红灯**

- [ ] **Step 4：实现 fan-out 边遍历**

在 `_apply_wave_results()` 中：当 outcome 匹配多条边时，全部激活。

- [ ] **Step 5：测试并提交**

---

## Task 3：条件边 `if`

**目标：** 边支持条件表达式，按节点输出数据决定是否激活。

### YAML 语法

```yaml
edges:
  - id: pass-to-approval
    from: scorecard
    to: final_approval
    on: succeeded
    if: "score >= 0.8"
  - id: fail-to-repair
    from: scorecard
    to: repair_worker
    on: succeeded
    if: "score < 0.8"
```

条件表达式支持：
- 数值比较：`>=` `<=` `>` `<` `==` `!=`
- 字段存在：`has(evidence_summary)`
- AND 组合：`score >= 0.8 and rl_margin > 3`

- [ ] **Step 1：扩展 `GraphEdge`**

```python
if_condition: str = ""   # e.g. "score >= 0.8"
```

- [ ] **Step 2：写条件边测试**

```python
def test_conditional_edge_true_activates():
def test_conditional_edge_false_skips():
def test_conditional_edge_with_has_operator():
def test_conditional_edge_and_combination():
```

- [ ] **Step 3：确认红灯**

- [ ] **Step 4：实现条件评估器**

新增 `_evaluate_edge_condition(condition: str, payload: dict) -> bool`：
- 解析简单表达式
- 从 payload 中提取字段值
- 返回 bool

- [ ] **Step 5：测试并提交**

---

## Task 4：动态节点 `expand`

**目标：** planner 节点可以动态生成下游节点和边，不要求所有节点在 YAML 中预定义。

### YAML 语法

```yaml
nodes:
  - id: planner
    role: planner
    kind: llm
    expand: true          # 该节点输出中包含要动态创建的 nodes + edges
```

planner 节点的 `output_payload` 中包含：

```json
{
  "plan": "...",
  "expand_nodes": [
    {"id": "worker_ch1", "role": "worker", "kind": "worker", "capability": "brd.channel.score", ...}
  ],
  "expand_edges": [
    {"id": "plan-to-ch1", "from": "planner", "to": "worker_ch1", "on": "succeeded"}
  ]
}
```

- [ ] **Step 1：扩展 `GraphNode`**

```python
expand: bool = False
```

- [ ] **Step 2：写 expand 测试**

```python
def test_expand_node_creates_dynamic_downstream():
def test_expand_without_flag_ignores_expand_payload():
```

- [ ] **Step 3：确认红灯**

- [ ] **Step 4：实现动态节点注入**

在 `_apply_wave_results()` 中：当节点 `expand=True` 且 output 包含 `expand_nodes`/`expand_edges`，将动态节点/边注入当前 template 的快照（或存入 graph_run 的扩展元数据）。

- [ ] **Step 5：测试并提交**

---

## Task 5：升级 BRD demo 模板为多节点 Fan-out

**目标：** 创建一个展示多节点编排的 YAML 模板。

### 模板结构

```yaml
id: brd_multi_channel_demo
version: 1
description: Multi-channel BRD local-cut with fan-out scoring and conditional approval.

nodes:
  - id: planner
    role: planner
    kind: program
    max_runs: 1
  - id: input_validator
    role: validator
    kind: program
    input_schema: plan_output
    output_schema: validated_plan
    max_runs: 1
    after: [planner]
  - id: build_worker
    role: worker
    kind: worker
    capability: brd.local_cut.build
    input_schema: validated_plan
    output_schema: build_result
    max_runs: 1
    on_failure: fail
    after: [input_validator]
  - id: score_ch1
    role: worker
    kind: worker
    capability: brd.channel.score
    input_schema: build_result
    output_schema: channel_score
    max_runs: 1
    after: [build_worker]
  - id: score_ch2
    role: worker
    kind: worker
    capability: brd.channel.score
    input_schema: build_result
    output_schema: channel_score
    max_runs: 1
    after: [build_worker]
  - id: aggregate_scorecard
    role: scorecard
    kind: program
    input_schema: channel_score
    output_schema: scorecard_report
    join: all
    max_runs: 1
    after: [score_ch1, score_ch2]
  - id: final_approval
    role: approval_gate
    kind: human_gate
    input_schema: scorecard_report
    output_schema: final_approval
    max_runs: 1
    after: [aggregate_scorecard]

edges:
  - id: plan-to-validate
    from: planner
    to: input_validator
    on: succeeded
    max_traversals: 1
  - id: validate-to-build
    from: input_validator
    to: build_worker
    on: succeeded
    max_traversals: 1
  - id: build-to-score-ch1
    from: build_worker
    to: score_ch1
    on: fan_out
    max_traversals: 1
  - id: build-to-score-ch2
    from: build_worker
    to: score_ch2
    on: fan_out
    max_traversals: 1
  - id: score-ch1-to-aggregate
    from: score_ch1
    to: aggregate_scorecard
    on: succeeded
    max_traversals: 1
  - id: score-ch2-to-aggregate
    from: score_ch2
    to: aggregate_scorecard
    on: succeeded
    max_traversals: 1
  - id: aggregate-to-approval
    from: aggregate_scorecard
    to: final_approval
    on: passed
    if: "overall_score >= 0.8"
    max_traversals: 1

handoffs:
  plan_output:
    required_fields: [plan, target_spec]
  validated_plan:
    required_fields: [plan, target_spec, validation]
  build_result:
    required_fields: [status, project_path, artifact_refs]
  channel_score:
    required_fields: [status, score, channel_id, evidence_summary]
  scorecard_report:
    required_fields: [status, checks, overall_score]
  final_approval:
    required_fields: [approval_id, decision]
```

- [ ] **Step 1：创建 YAML 模板**

写入 `docs/agent_templates/brd_multi_channel_demo.yaml`。

- [ ] **Step 2：写端到端 Graph 测试**

```python
def test_multi_channel_fan_out_graph_runs_all_branches():
def test_multi_channel_conditional_approval_branches_on_score():
```

- [ ] **Step 3：确认红灯**

- [ ] **Step 4：实现 worker `fan_out` outcome**

在 `_execute_worker()` 中支持 `edge_outcome: fan_out` 标记。

- [ ] **Step 5：测试并提交**

---

## Task 6：全量回归 + 向后兼容验证

**目标：** 确保所有已有测试保持绿色，`brd_real_solve_evidence.yaml` 等现有模板不受影响。

- [ ] **Step 1：跑全量**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

- [ ] **Step 2：验证旧模板不变**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_brd_real_solve_graph.py tests/test_agent_graph_runner_dag.py
```

- [ ] **Step 3：静态检查**

- [ ] **Step 4：提交**

---

## 完成定义

- [ ] 节点支持 `on_failure: fail|skip|retry|fallback:<id>`，默认 `fail` 向后兼容。
- [ ] `on_failure=skip` 时节点 SKIPPED，graph 继续。
- [ ] `on_failure=retry` 时支持 `max_attempts` 和 `backoff`。
- [ ] 节点支持 `fan_out` 模式，单节点可触发多条下游边。
- [ ] 边支持 `if` 条件表达式，按 payload 数据路由。
- [ ] 节点支持 `expand` 动态生成下游节点和边。
- [ ] `brd_multi_channel_demo.yaml` 可跑通 fan-out → join:all → conditional approval。
- [ ] 所有已有测试绿色。
- [ ] `brd_real_solve_evidence.yaml` 行为不变。
