# 持久化 YAML DAG Runner 设计

## 1. 背景

当前项目已经具备：

- Mission、Job、JobAttempt、GraphRun、NodeRun 持久化；
- 有界 Mission Loop、重试、预算和终态结果；
- YAML GraphTemplate、HandoffSchema 和条件边的静态定义；
- worker、scorecard、approval、action、evidence 等执行能力。

但现有 `run_graph_sequential()` 只做了以下事情：

1. 新建 GraphRun；
2. 找到第一个 queued Job；
3. 按 capability 找到一个 worker 节点；
4. 执行这个 Job；
5. 立即结束 GraphRun。

它没有执行 YAML 中的 planner、validator、scorecard、approval gate，也没有消费节点依赖、条件边、`after`、并行、汇聚或打回。因此它仍然是“带 GraphRun 审计记录的单 Job 脚本”，不是 Agent 图编排器。

本阶段目标是实现一个 Python native、SQLite 持久化、可重启的 YAML DAG Runner。Pi 或其他 Agent 框架以后可以提供 LLM 会话和 tool-call，但不能替代这层工程状态机。

## 2. 核心决策

### 2.1 图定义是运行时核心接口

Graph Runner 必须从 YAML 读取并执行：

- 节点类型、角色和 capability；
- 条件边 `on`；
- 执行屏障 `after`；
- 汇聚策略 `join`；
- handoff schema；
- 打回边最大遍历次数；
- 图级最大推进步数和最大节点运行次数。

拓扑不能在 Python 中按模板 ID 写死。

### 2.2 GraphRun 保存不可变模板快照

创建 GraphRun 时持久化：

- `template_snapshot`；
- `initial_payload`；
- `max_steps`；
- 当前 `step_count`。

恢复运行时使用快照，而不是重新读取可能已经被修改的 YAML 文件。模板 ID 和版本只用于索引与展示。

### 2.3 一次 advance 推进一个拓扑波次

新增两个层次：

```text
advance_graph()
    -> 找出当前所有 ready 节点
    -> 执行一个拓扑波次
    -> 持久化 NodeRun 和 GraphHandoff
    -> 返回本次调度结果

run_graph()
    -> 重复 advance_graph()
    -> 直到 succeeded / failed / waiting_approval / max_steps
```

这样既能由 CLI 一次跑到阻塞点，也能由上层 MissionLoopController 逐步监控。

### 2.4 节点之间只传结构化 Handoff

新增 `GraphHandoffRecord`，每条被触发的边生成一条持久化记录：

- 来源 NodeRun；
- edge ID；
- `from_node`、`to_node`；
- outcome；
- payload；
- pending / consumed 状态；
- 被哪个目标 NodeRun 消费。

节点不读取其他节点的自由文本上下文。恢复、汇聚、审计和程序校验都基于这些记录。

## 3. YAML 契约

### 3.1 GraphNode

扩展节点字段：

```yaml
- id: aggregate_score
  role: scorecard
  kind: program
  join: all
  input_schema: parallel_results
  output_schema: scorecard_report
```

字段语义：

- `join: any`：任一匹配入边到达即可执行；默认值；
- `join: all`：每个入边来源至少有一个 pending handoff 后执行；
- `max_runs`：该节点在一次 GraphRun 中最多运行多少次，默认 1；
- `handler`：program/LLM 节点的注册处理器名称，可选；
- worker 节点继续使用 `capability`。

### 3.2 GraphEdge

扩展边字段：

```yaml
- id: tester_failed_to_coder
  from: tester
  to: coder
  on: failed
  after:
    - qualifier
  max_traversals: 2
```

字段语义：

- `id`：模板内唯一；未填写时由 loader 稳定生成；
- `on`：来源节点的结构化 outcome；
- `after`：目标节点执行前必须已经结束的节点列表；
- `max_traversals`：边在一次 GraphRun 中允许触发的次数，默认 1；
- 超过上限时 GraphRun 失败，错误为 `edge_traversal_limit`。

### 3.3 模板静态校验

loader 必须拒绝：

- 重复 node ID 或 edge ID；
- 未知节点引用；
- `join` 不是 `any/all`；
- 非正数 `max_runs/max_traversals`；
- `after` 引用未知节点；
- 无条件环或没有遍历上限的回边；
- worker 节点缺 capability；
- program/LLM 节点既没有内置 role 也没有 handler；
- input/output schema 引用不存在的 handoff。

允许有界循环，因此不能简单拒绝所有有向环。环上的每一条回边必须显式声明有限 `max_traversals`。

## 4. 持久化对象

### 4.1 GraphRunRecord 扩展

新增：

- `template_snapshot: dict`
- `initial_payload: dict`
- `step_count: int`
- `max_steps: int`

GraphRun 终态：

- `SUCCEEDED`
- `FAILED`
- `CANCELED`

阻塞态：

- `WAITING_APPROVAL`

### 4.2 GraphHandoffRecord

```text
handoff_id
graph_run_id
mission_id
edge_id
source_node_run_id
from_node
to_node
outcome
payload
status
created_at
consumed_at
consumed_by_node_run_id
```

handoff payload 在创建和消费时都校验 schema。

### 4.3 NodeRun

一个 node ID 可以在同一 GraphRun 中产生多条 NodeRun，用于：

- retry 后再次进入；
- 条件打回；
- 有界优化循环。

`sequence` 继续作为图内全局递增序号。调度器通过 `max_runs` 和 edge traversal 计数限制循环。

## 5. Ready 判定与汇聚

### 5.1 根节点

没有入边的节点在尚未运行时 ready。

根 planner 在当前阶段使用 `initial_payload` 作为结构化输入源，并在审计中标记：

```json
{"planning_source": "graph_initial_payload"}
```

这不伪装成 LLM 推理。以后接入 Pi/LLM provider 时，只替换 planner handler，不改变图状态机。

### 5.2 非根节点

- `join:any`：至少一条匹配入边有 pending handoff；
- `join:all`：每个入边来源都有 pending handoff；
- `after` 中的节点必须至少有一次终态 NodeRun；
- 节点运行次数未达到 `max_runs`；
- GraphRun 不在终态或审批阻塞态。

### 5.3 多输入合并

目标节点输入固定包含：

```json
{
  "_handoffs": {
    "source_a": {...},
    "source_b": {...}
  }
}
```

若只有一个 handoff，其 payload 同时浅层提升到顶层，保持现有 worker 输入兼容。多个 payload 的同名字段值不同则不静默覆盖，只保留在 `_handoffs`，顶层不写该冲突字段。

## 6. 节点执行器

### 6.1 planner/source

- 根 planner 输出 `initial_payload`；
- output schema 校验；
- outcome 为 `succeeded`。

### 6.2 validator

- 校验 input schema；
- 原样输出规范化 payload；
- output schema 校验；
- outcome 为 `succeeded` 或 `failed`。

### 6.3 worker

- 优先绑定同 Mission 中 capability 相同、尚未绑定的 queued Job；
- 没有现成 Job 时，根据 handoff payload 创建幂等 Job：
  `graph:{graph_run_id}:{node_id}:{run_index}`；
- 通过新增 `AgentRuntime.execute_job(job_id)` 精确执行，不再依赖“队列第一个”；
- worker 的 output payload 加入 `artifact_refs` 后进行 output schema 校验；
- outcome 来自：
  - `approval_required`
  - `succeeded`
  - `failed`
  - worker 输出中的显式 `edge_outcome`。

同一波次的多个 ready worker 使用受限线程池并行执行。SQLite 写入仍通过事务串行化，真实 AEDT 并发继续受 ExecutionProfile 限制。

### 6.4 program/scorecard

- 内置 validator 和 scorecard handler；
- 其他 program 节点必须注册 handler；
- scorecard outcome 为 `passed` 或 `failed`；
- scorecard 报告写 EvidencePackage。

### 6.5 human_gate

首次到达：

- 若上游 worker 已经创建 pending Approval，绑定该 Approval；
- 否则创建 graph approval；
- NodeRun 进入 `WAITING_APPROVAL`；
- GraphRun 进入 `WAITING_APPROVAL`。

恢复时：

- pending：继续等待；
- approved：同一 NodeRun 变为 `SUCCEEDED`，outcome=`approved`；
- rejected：同一 NodeRun 变为 `FAILED`，outcome=`rejected`；
- 然后按 YAML 条件边继续。

approval ID 保存在 NodeRun output payload 中，不依赖进程内状态。

## 7. 条件分支、打回与失败

节点结束后只触发 `edge.on == outcome` 的边。

- 有匹配边：创建 GraphHandoff；
- 无匹配边且 outcome 是成功型终点：该分支自然结束；
- 无匹配边且 outcome 为 `failed/rejected`：GraphRun 失败；
- 所有分支结束、没有 pending handoff、没有 ready/running/waiting 节点：GraphRun 成功；
- 存在 pending handoff 但永远没有 ready 节点：GraphRun 失败，错误为 `graph_deadlock`。

打回通过普通条件边表达，例如：

```yaml
- from: tester
  to: coder
  on: failed
  max_traversals: 2
```

不增加隐藏的 retry 语义。

## 8. CLI

命令面调整：

```text
mission run-graph --mission-id ... --template ... [--max-steps 32]
mission advance-graph --graph-run-id ...
mission graph-status --graph-run-id ...
mission resume-graph --graph-run-id ...
mission approve --approval-id ... --option-id ...
```

- `run-graph`：创建图并运行到完成或阻塞；
- `advance-graph`：推进一个拓扑波次；
- `graph-status`：返回 GraphRun、NodeRuns、Handoffs；
- `resume-graph`：审批解决后继续；
- 通用 `approve` 正式启用，不重建 GraphRun。

兼容入口 `run_graph_once()` 保留，但内部调用新的 `run_graph()`。

## 9. 安全与预算

- GraphRun `max_steps` 必须为正数且有保守默认值；
- Node `max_runs`、Edge `max_traversals` 必须有限；
- 一个波次的 worker 并发数受 profile 限制；
- real AEDT worker 不因图并行而绕过 `allow_real_aedt`；
- graph retry 不能绕过 Job retry_limit 和 Mission budget；
- 每次调度、handoff 创建/消费、审批恢复均产生事件；
- 失败时保留所有已完成 NodeRun、JobAttempt、artifact 和 evidence。

## 10. 验收标准

1. YAML loader 能校验 join、after、edge ID、有限回边和 schema 引用；
2. 串行 planner -> validator -> worker -> scorecard 可完整执行；
3. fan-out 的两个独立 worker 在同一波次 ready；
4. join:all 在两个输入均完成前不会执行；
5. 条件边只触发匹配 outcome；
6. failed -> repair/coder 打回可以有限重复；
7. 超过 edge traversal、node runs 或 max_steps 明确失败；
8. approval gate 阻塞并在进程重启后从同一 NodeRun 恢复；
9. GraphRun 使用创建时 YAML 快照；
10. worker 精确执行绑定 Job，不会误取其他 queued Job；
11. GraphHandoff 缺字段时目标节点不执行；
12. CLI 可创建、查询、审批和恢复图；
13. 现有 Mission Loop、Action、Evidence 和 BRD worker 回归保持通过；
14. `src/aedt_agent/agent` 和 `infrastructure` 不依赖 `aedt_agent.v0`。

## 11. 非目标

本阶段不实现：

- LLM 自动生成任意新 YAML；
- 跨机器分布式队列；
- 容器编排和 License broker；
- 真正的动态模型选择；
- Pi runtime 集成；
- 无上限自治循环。

这些能力以后都通过当前 GraphTemplate、handler registry、GraphHandoff 和 Mission budget 接入。
