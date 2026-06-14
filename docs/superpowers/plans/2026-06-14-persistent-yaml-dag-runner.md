# Persistent YAML DAG Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有单 Job graph runner 升级为按 YAML 条件边推进、持久化 handoff、支持并行汇聚、有限打回和审批续跑的 DAG 编排器。

**Architecture:** GraphTemplate 负责静态拓扑与有限循环校验；SQLite 保存模板快照、GraphHandoff 和节点执行历史；纯函数 scheduler 计算 ready 节点；NodeExecutorRegistry 分发 planner/validator/worker/scorecard/human gate；GraphRunner 每次推进一个拓扑波次，并可循环运行到完成或阻塞。

**Tech Stack:** Python 3.11+、dataclasses、PyYAML、SQLite、`concurrent.futures.ThreadPoolExecutor`、pytest、现有 Mission/Job/Approval/Scorecard Runtime。

---

## 文件结构

- `src/aedt_agent/agent/graph_template.py`：YAML 图静态契约和校验。
- `src/aedt_agent/agent/graph_scheduler.py`：无数据库副作用的 ready/join/handoff 合并算法。
- `src/aedt_agent/agent/graph_executors.py`：节点类型执行器和 handler registry。
- `src/aedt_agent/agent/graph_runner.py`：GraphRun 生命周期、波次推进和 run-until-blocked。
- `src/aedt_agent/agent/mission/contracts.py`：GraphRun/GraphHandoff 契约。
- `src/aedt_agent/infrastructure/sqlite_mission_store.py`：图快照、handoff、job binding 持久化。
- `src/aedt_agent/agent/orchestrator/runtime.py`：按 job ID 精确执行。
- `src/aedt_agent/agent/cli.py`：图创建、推进、查询、审批和恢复。

## Task 1：扩展并严格校验 YAML 图契约

**Files:**
- Modify: `src/aedt_agent/agent/graph_template.py`
- Modify: `tests/test_agent_graph_template.py`

- [ ] **Step 1: 写失败测试**

覆盖：

```python
def test_graph_template_loads_join_after_and_limits(tmp_path):
    template = load_graph_template(_write_yaml(tmp_path, """
id: parallel
version: 1
nodes:
  - {id: source, role: planner, kind: llm, max_runs: 1}
  - {id: left, role: worker, kind: worker, capability: fake.left}
  - {id: right, role: worker, kind: worker, capability: fake.right}
  - {id: join, role: scorecard, kind: program, join: all, after: [left, right]}
edges:
  - {id: source-left, from: source, to: left, on: succeeded}
  - {id: source-right, from: source, to: right, on: succeeded}
  - {id: left-join, from: left, to: join, on: succeeded}
  - {id: right-join, from: right, to: join, on: succeeded}
handoffs: {}
"""))
    assert template.node("join").join == "all"
    assert template.node("join").after == ["left", "right"]
    assert template.edges[0].edge_id == "source-left"


def test_graph_template_rejects_unbounded_cycle(tmp_path):
    with pytest.raises(GraphTemplateError, match="cycle.*max_traversals"):
        load_graph_template(_write_yaml(tmp_path, """
id: loop
version: 1
nodes:
  - {id: coder, role: worker, kind: worker, capability: fake.coder}
  - {id: tester, role: worker, kind: worker, capability: fake.tester}
edges:
  - {from: coder, to: tester, on: succeeded}
  - {from: tester, to: coder, on: failed}
handoffs: {}
"""))
```

另写五个独立测试，分别构造重复 edge ID、`join: maybe`、`max_runs: 0`、未知 `after` 节点和缺 capability 的 worker，并断言对应错误信息。

- [ ] **Step 2: 运行测试并确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_template.py
```

Expected: 新字段不存在，非法模板未被拒绝。

- [ ] **Step 3: 实现最小契约**

目标签名：

```python
@dataclass(frozen=True)
class GraphNode:
    node_id: str
    role: str
    kind: str
    capability: str = ""
    input_schema: str = ""
    output_schema: str = ""
    join: str = "any"
    after: list[str] = field(default_factory=list)
    max_runs: int = 1
    handler: str = ""


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    from_node: str
    to_node: str
    on: str
    after: list[str] = field(default_factory=list)
    max_traversals: int = 1
```

loader 稳定生成缺省 edge ID：`{index}:{from}:{to}:{on}`。使用 DFS 找环；环内回边必须在 YAML 显式提供 `max_traversals`。

- [ ] **Step 4: 运行测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_template.py
git add src/aedt_agent/agent/graph_template.py tests/test_agent_graph_template.py
git commit -m "feat: validate bounded yaml graph topology"
```

## Task 2：持久化 GraphRun 快照与 GraphHandoff

**Files:**
- Modify: `src/aedt_agent/agent/mission/contracts.py`
- Modify: `src/aedt_agent/agent/mission/__init__.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- Modify: `tests/test_agent_graph_control_contracts.py`
- Modify: `tests/test_agent_graph_control_store.py`

- [ ] **Step 1: 写失败测试**

```python
def test_graph_run_persists_template_snapshot_and_step_budget(tmp_path):
    record = GraphRunRecord.create(
        graph_run_id="g1",
        mission_id="m1",
        template_id="parallel",
        template_version=1,
        plan_version=1,
        template_snapshot={"id": "parallel", "nodes": []},
        initial_payload={"x": 1},
        max_steps=20,
    )
    store.create_graph_run(record)
    loaded = SQLiteMissionStore(db).get_graph_run("g1")
    assert loaded.template_snapshot["id"] == "parallel"
    assert loaded.initial_payload == {"x": 1}
    assert loaded.step_count == 0
    assert loaded.max_steps == 20


def test_graph_handoff_can_be_created_consumed_and_reloaded(tmp_path):
    handoff = GraphHandoffRecord.create(
        handoff_id="h1",
        graph_run_id="g1",
        mission_id="m1",
        edge_id="source-worker",
        source_node_run_id="nr1",
        from_node="source",
        to_node="worker",
        outcome="succeeded",
        payload={"value": 1},
    )
    store.create_graph_handoff(handoff)
    consumed = store.consume_graph_handoffs([handoff.handoff_id], "node-run-2")
    assert consumed[0].status == GraphHandoffStatus.CONSUMED
```

- [ ] **Step 2: 确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_control_contracts.py tests/test_agent_graph_control_store.py
```

- [ ] **Step 3: 实现契约和 schema migration**

新增：

```python
class GraphHandoffStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class GraphHandoffRecord:
    handoff_id: str
    graph_run_id: str
    mission_id: str
    edge_id: str
    source_node_run_id: str
    from_node: str
    to_node: str
    outcome: str
    payload: JsonDict
    status: GraphHandoffStatus
    created_at: str
    consumed_at: str | None = None
    consumed_by_node_run_id: str | None = None
```

SQLite 增加：

- `graph_handoffs`
- `graph_node_jobs`
- `graph_runs.template_snapshot_json`
- `graph_runs.initial_payload_json`
- `graph_runs.step_count`
- `graph_runs.max_steps`

对已存在数据库使用 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` 补列。

- [ ] **Step 4: 实现 store API**

```python
create_graph_handoff(record)
list_graph_handoffs(graph_run_id, status=None, to_node=None)
consume_graph_handoffs(handoff_ids, node_run_id)
increment_graph_step(graph_run_id)
bind_graph_node_job(graph_run_id, node_id, run_index, job_id)
get_graph_node_job(graph_run_id, node_id, run_index)
```

- [ ] **Step 5: 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_control_contracts.py tests/test_agent_graph_control_store.py
git add src/aedt_agent/agent/mission src/aedt_agent/infrastructure/sqlite_mission_store.py tests/test_agent_graph_control_contracts.py tests/test_agent_graph_control_store.py
git commit -m "feat: persist graph snapshots and handoffs"
```

## Task 3：按 Job ID 精确执行并支持图绑定

**Files:**
- Modify: `src/aedt_agent/agent/orchestrator/runtime.py`
- Modify: `tests/test_agent_runtime_service.py`
- Modify: `tests/test_agent_job_attempts.py`

- [ ] **Step 1: 写失败测试**

```python
def test_runtime_executes_requested_job_not_first_queued_job(tmp_path):
    first = runtime.create_job(mission_id, "fake.echo", "first", {"value": 1})
    second = runtime.create_job(mission_id, "fake.echo", "second", {"value": 2})
    result = runtime.execute_job(second.job_id, worker_id="graph")
    assert result.output_payload["value"] == 2
    assert runtime.get_job(first.job_id).status == JobStatus.QUEUED
```

- [ ] **Step 2: 确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_runtime_service.py tests/test_agent_job_attempts.py
```

- [ ] **Step 3: 提取精确执行路径**

```python
def execute_next_job(self, mission_id, worker_id):
    job = self.store.next_queued_job(mission_id)
    if job is None:
        raise ValueError(f"no queued job for mission: {mission_id}")
    return self.execute_job(job.job_id, worker_id)

def execute_job(self, job_id, worker_id):
    job = self.get_job(job_id)
    if job.status != JobStatus.QUEUED:
        raise ValueError(f"job is not queued: {job_id} ({job.status.value})")
    # 复用现有 lease / attempt / retry / artifact / approval 逻辑
```

- [ ] **Step 4: 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_runtime_service.py tests/test_agent_job_attempts.py
git add src/aedt_agent/agent/orchestrator/runtime.py tests/test_agent_runtime_service.py tests/test_agent_job_attempts.py
git commit -m "feat: execute graph-bound jobs by id"
```

## Task 4：实现纯函数拓扑调度器

**Files:**
- Create: `src/aedt_agent/agent/graph_scheduler.py`
- Create: `tests/test_agent_graph_scheduler.py`

- [ ] **Step 1: 写失败测试**

写七个独立测试：

1. 根节点没有历史 NodeRun 时 ready，已有一次运行后不再 ready；
2. `join:any` 收到一个 pending handoff 后 ready；
3. `join:all` 只收到一个来源时不 ready，收到全部来源后 ready；
4. `after: [tester]` 在 tester 未终态时阻塞，tester succeeded 后 ready；
5. 单 handoff payload 同时出现在顶层和 `_handoffs`；
6. 多 handoff 的同名冲突字段只保留在 `_handoffs`；
7. NodeRun 数量达到 `max_runs` 后，即使有新 handoff 也不 ready。

期望 API：

```python
ready_nodes(template, node_runs, pending_handoffs) -> list[ReadyNode]
merge_handoff_payloads(handoffs) -> dict[str, Any]
```

- [ ] **Step 2: 确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_scheduler.py
```

- [ ] **Step 3: 实现 scheduler**

`ReadyNode` 包含：

```python
@dataclass(frozen=True)
class ReadyNode:
    node: GraphNode
    input_payload: dict[str, Any]
    handoff_ids: list[str]
    run_index: int
```

只做计算，不写 SQLite，不调用 worker。

- [ ] **Step 4: 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_scheduler.py
git add src/aedt_agent/agent/graph_scheduler.py tests/test_agent_graph_scheduler.py
git commit -m "feat: schedule ready yaml graph nodes"
```

## Task 5：实现节点执行器与审批恢复

**Files:**
- Create: `src/aedt_agent/agent/graph_executors.py`
- Create: `tests/test_agent_graph_executors.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`

- [ ] **Step 1: 写失败测试**

写七个独立测试：

1. planner 输出 initial payload，并包含 `planning_source=graph_initial_payload`；
2. validator 缺少 required field 时返回 failed；
3. worker 绑定同 capability 的未绑定 queued Job；
4. 没有匹配 Job 时创建 `graph:{graph_run_id}:{node_id}:{run_index}` Job；
5. scorecard 返回 `passed` 并创建 EvidencePackage；
6. human gate 首次执行创建 Approval，NodeRun/GraphRun 等待；
7. 审批后恢复同一个 NodeRun ID，不创建第二个 gate NodeRun。

- [ ] **Step 2: 确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_executors.py
```

- [ ] **Step 3: 实现 registry 和结果契约**

```python
@dataclass(frozen=True)
class GraphNodeExecutionResult:
    status: NodeRunStatus
    outcome: str
    output_payload: dict[str, Any]
    artifact_refs: list[str]
    evidence_package_id: str | None = None
    error: dict[str, Any] | None = None


class GraphNodeExecutorRegistry:
    def register(self, handler_id, handler):
        if handler_id in self._handlers:
            raise ValueError(f"graph handler already registered: {handler_id}")
        self._handlers[handler_id] = handler

    def execute(self, handler_id, context):
        handler = self._handlers.get(handler_id)
        if handler is None:
            raise KeyError(f"graph handler not found: {handler_id}")
        return handler(context)
```

内置角色：

- `planner`
- `validator`
- `scorecard`
- `approval_gate`
- `worker`

- [ ] **Step 4: 增加 Approval 查询**

Store 新增：

```python
list_approvals(mission_id, decision=None)
```

human gate 优先绑定上游已经创建的 pending approval；否则创建 `graph_gate:{graph_run_id}:{node_id}:{run_index}` 审批。

- [ ] **Step 5: 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_executors.py tests/test_agent_approval_service.py
git add src/aedt_agent/agent/graph_executors.py src/aedt_agent/infrastructure/sqlite_mission_store.py tests/test_agent_graph_executors.py
git commit -m "feat: execute graph nodes and approval gates"
```

## Task 6：实现波次推进、并行、条件边和有限打回

**Files:**
- Rewrite: `src/aedt_agent/agent/graph_runner.py`
- Modify: `tests/test_agent_graph_runner.py`
- Create: `tests/test_agent_graph_runner_dag.py`

- [ ] **Step 1: 写串行和条件分支失败测试**

写三个测试：完整串行图终态为 succeeded；来源 outcome 为 `approval_required` 时只创建对应 handoff；failed outcome 没有匹配边时 GraphRun 为 failed。

- [ ] **Step 2: 写 fan-out/fan-in 失败测试**

写两个测试：两个 worker 共享 `threading.Barrier(2)` 并都能越过 barrier，证明同波并发；join 节点在第一条 handoff 后不运行，第二条 handoff 到达后运行一次。

测试记录两个 worker 的 barrier，证明它们同时进入执行区，而不是只检查顺序结果。

- [ ] **Step 3: 写打回、死锁和预算失败测试**

写四个测试：tester 第一次 failed 后 coder 产生第二次 NodeRun 并最终成功；第三次触发 `max_traversals:2` 回边时失败；step_count 达到 max_steps 时失败；存在 pending handoff 但目标被 `after` 永久阻塞时报告 `graph_deadlock`。

- [ ] **Step 4: 确认全部红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_runner.py tests/test_agent_graph_runner_dag.py
```

- [ ] **Step 5: 实现 GraphRunner**

公开 API：

```python
create_graph_run(runtime, mission_id, template, initial_payload, max_steps=32)
advance_graph(runtime, graph_run_id, *, worker_id="graph", max_workers=4)
run_graph(runtime, mission_id, template, *, initial_payload=None, max_steps=32, worker_id="graph")
resume_graph(runtime, graph_run_id, *, worker_id="graph")
graph_status(runtime, graph_run_id)
run_graph_once(runtime, mission_id, template, *, worker_id="graph")
```

执行流程：

1. 从 GraphRun snapshot 重建模板；
2. 若有 waiting approval，先恢复 gate；
3. scheduler 计算 ready wave；
4. program 节点确定性执行；
5. worker 节点用 ThreadPoolExecutor；
6. 完成 NodeRun；
7. 校验 output handoff；
8. 创建匹配边 GraphHandoff；
9. 消费输入 handoff；
10. 增加 step_count；
11. 判断 waiting/succeeded/failed/deadlock。

- [ ] **Step 6: 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_graph_runner.py tests/test_agent_graph_runner_dag.py
git add src/aedt_agent/agent/graph_runner.py tests/test_agent_graph_runner.py tests/test_agent_graph_runner_dag.py
git commit -m "feat: run persistent yaml dag waves"
```

## Task 7：启用图 CLI、通用审批和恢复

**Files:**
- Modify: `src/aedt_agent/agent/cli.py`
- Modify: `tests/test_agent_cli_graph_control.py`
- Create: `tests/test_agent_cli_dag_runner.py`

- [ ] **Step 1: 写失败测试**

写五个 subprocess 测试：`run-graph` 在 gate 返回 waiting_approval；`graph-status` 返回 handoffs；`approve` 返回 approved；`resume-graph` 使用相同 graph_run_id 并完成；`advance-graph` 的 step_count 只增加 1。

- [ ] **Step 2: 确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_cli_graph_control.py tests/test_agent_cli_dag_runner.py
```

- [ ] **Step 3: 实现命令**

新增 parser：

```text
mission advance-graph --graph-run-id
mission graph-status --graph-run-id
mission resume-graph --graph-run-id
```

修改：

- `run-graph` 支持 `--max-steps`；
- `approve` 必须要求 `--approval-id` 和 `--option-id`；
- approve 调用 `ApprovalService.approve()`；
- 输出始终包含 graph run、node runs、handoffs。

- [ ] **Step 4: 测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_cli_graph_control.py tests/test_agent_cli_dag_runner.py
git add src/aedt_agent/agent/cli.py tests/test_agent_cli_graph_control.py tests/test_agent_cli_dag_runner.py
git commit -m "feat: expose persistent dag control cli"
```

## Task 8：升级内置 YAML 模板并完成审计

**Files:**
- Modify: `docs/agent_templates/brd_local_cut_build.yaml`
- Modify: `docs/agent_templates/brd_local_cut_solve_evidence.yaml`
- Modify: `docs/agent_templates/brd_recorded_void_action.yaml`
- Modify: relevant graph tests

- [ ] **Step 1: 明确 join、after 和限制**

所有模板：

- 每条 edge 增加稳定 ID；
- 节点声明 `max_runs: 1`；
- approval gate 多入边声明 `join: any`；
- 图模板增加 `max_steps` 或 CLI 使用默认 32；
- 不添加无法执行的隐藏节点。

- [ ] **Step 2: 跑 Agent 控制面回归**

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_agent_graph_template.py `
  tests/test_agent_graph_control_contracts.py `
  tests/test_agent_graph_control_store.py `
  tests/test_agent_graph_scheduler.py `
  tests/test_agent_graph_executors.py `
  tests/test_agent_graph_runner.py `
  tests/test_agent_graph_runner_dag.py `
  tests/test_agent_cli_graph_control.py `
  tests/test_agent_cli_dag_runner.py `
  tests/test_agent_mission_loop_controller.py `
  tests/test_agent_cli_mission_loop.py `
  tests/test_agent_recorded_action_executor.py `
  tests/test_agent_brd_recorded_void_action.py
```

- [ ] **Step 3: 静态审计**

```powershell
.\.venv\Scripts\python.exe -m compileall -q src\aedt_agent\agent src\aedt_agent\infrastructure
rg -n "aedt_agent\.v0" src\aedt_agent\agent src\aedt_agent\infrastructure
git diff --check
git status --short
```

Expected:

- Agent 测试全部通过；
- `rg` 无输出；
- 仅保留任务开始前已经存在的无关工作区改动。

- [ ] **Step 4: 提交模板和审计修复**

```powershell
git add docs/agent_templates tests src/aedt_agent/agent src/aedt_agent/infrastructure
git commit -m "feat: adopt persistent dag templates"
```

## 完成定义

- YAML 中的所有节点和条件边被真实执行；
- fan-out、join:all、after 和有限打回有端到端测试；
- GraphHandoff 是一等持久化对象；
- GraphRun 可跨进程审批恢复；
- Job 按 graph node 精确绑定；
- GraphRun 使用不可变模板快照；
- CLI 可以创建、推进、查询、审批、恢复；
- 不依赖 v0 runtime；
- Agent 控制面完整回归通过。
