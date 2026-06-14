# Controlled BRD Action Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `adjust_layout_void` 受控动作的 proposal、digest、审批绑定、recorded apply、before/after 比较和 accept/rollback 闭环。

**Architecture:** `agent.actions` 定义动作契约、验证器、审批绑定和决策策略；SQLite store 持久化 Action 与 Execution；recorded adapter 使用显式 before/after artifacts 验证闭环，real AEDT adapter 默认 fail closed；组合 worker 复用这些组件并写入审计记录。

**Tech Stack:** Python 3.11+、dataclasses、enum、hashlib、json、sqlite3、pytest、现有 ApprovalService、channel scoring、Mission runtime。

---

## Task 1：Action 契约、digest、验证器与决策策略

**Files:**
- Create: `src/aedt_agent/agent/actions/contracts.py`
- Create: `src/aedt_agent/agent/actions/validation.py`
- Create: `src/aedt_agent/agent/actions/policy.py`
- Create: `src/aedt_agent/agent/actions/__init__.py`
- Create: `tests/test_agent_action_contracts.py`

- [ ] 编写失败测试，覆盖：
  - `ActionRecord.create()` 生成稳定 digest。
  - 修改 `new_value_mil` 后 digest 改变。
  - JSON 不含 raw Touchstone/TDR 内容，只保留路径。
  - 空 layer、未知 shape、零 delta、越界值、delta 不一致被拒绝。
  - `improved -> accept`、`regressed/unchanged -> rollback`、`mixed -> review`。

- [ ] 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_action_contracts.py -q
```

Expected: FAIL because `aedt_agent.agent.actions` does not exist.

- [ ] 实现：

```text
ActionStatus
ActionDecision
ActionRecord
ActionExecutionRecord
ActionValidationError
validate_action()
decide_action_outcome()
```

Digest 使用 `json.dumps(..., sort_keys=True, separators=(",", ":"))` 的 UTF-8 SHA256；排除 action_id、status、timestamps、approval_id、comparison、decision 等运行时字段。

- [ ] 运行测试并提交：

```powershell
git commit -m "feat: define controlled brd action contracts"
```

---

## Task 2：Action SQLite 持久化与事件

**Files:**
- Modify: `src/aedt_agent/agent/mission/contracts.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- Create: `tests/test_agent_action_store.py`

- [ ] 编写失败测试：
  - Action 跨 store 重启可读取。
  - status、approval_id、comparison、decision 可更新。
  - ActionExecution 跨重启可读取。
  - create/update 产生单调 Event sequence。

- [ ] 新增 EventType：

```text
action_created
action_updated
action_execution_created
action_execution_updated
```

- [ ] 新增 SQLite tables：

```text
action_records
action_executions
```

- [ ] 新增 CRUD：

```text
create_action
get_action
list_actions
update_action
create_action_execution
get_action_execution
complete_action_execution
list_action_executions
```

- [ ] 运行测试并提交：

```powershell
git commit -m "feat: persist controlled brd actions"
```

---

## Task 3：Action Approval 绑定

**Files:**
- Create: `src/aedt_agent/agent/actions/approval.py`
- Modify: `src/aedt_agent/agent/approvals/service.py`
- Create: `tests/test_agent_action_approval.py`

- [ ] 编写失败测试：
  - request action approval 将 action 置为 waiting_approval。
  - approval option 含 action_id/action_digest。
  - approved digest 匹配时返回绑定成功。
  - action 被修改或 digest 不匹配时拒绝。
  - reject 将 action 置为 rejected。

- [ ] 实现：

```text
request_action_approval(store, action_id)
approve_action(store, approval_id, action_id, action_digest, comment)
assert_action_approved(store, action)
```

普通 `ApprovalService.approve()` 保持兼容；action approval 通过专用函数调用它并更新 ActionRecord。

- [ ] 运行测试并提交：

```powershell
git commit -m "feat: bind approvals to action digest"
```

---

## Task 4：Recorded adapter、比较与回滚

**Files:**
- Create: `src/aedt_agent/agent/actions/adapters.py`
- Create: `src/aedt_agent/agent/actions/executor.py`
- Create: `tests/test_agent_recorded_action_executor.py`

- [ ] 编写失败测试：
  - recorded adapter 校验四个 before/after artifacts。
  - adapter 不修改原始文件。
  - improved 结果 accepted。
  - regressed/unchanged 结果 rolled_back。
  - mixed 结果 waiting_approval。
  - real_aedt adapter 抛出 fail-closed 错误。
  - after artifacts 在 rollback 后仍存在于 execution audit。

- [ ] 实现：

```text
RecordedActionAdapter.apply(action)
RealAedtActionAdapter.apply(action)
execute_approved_action(store, action_id)
```

执行流程：

```text
assert approved
create ActionExecutionRecord
status applying
adapter apply
score before/after
compare_channel_scores
decide_action_outcome
persist applied + final action state
```

- [ ] 运行测试并提交：

```powershell
git commit -m "feat: execute and rollback recorded brd actions"
```

---

## Task 5：组合 worker、CLI 与 graph 模板

**Files:**
- Create: `src/aedt_agent/agent/workers/brd_recorded_void_action.py`
- Modify: `src/aedt_agent/agent/workers/__init__.py`
- Modify: `src/aedt_agent/agent/cli.py`
- Create: `docs/agent_templates/brd_recorded_void_action.yaml`
- Create: `tests/test_agent_brd_recorded_void_action.py`

- [ ] 编写失败测试：
  - CLI 创建 action proposal Mission。
  - `mission actions` 可查询 proposal。
  - `mission approve-action` 绑定 digest。
  - `mission run` 或 `run-graph` 执行 recorded action。
  - improved fixture accepted，regressed fixture rolled_back。
  - Evidence/Action JSON 中没有 raw trace。

- [ ] Worker capability：

```text
brd.action.execute_recorded
```

- [ ] CLI：

```text
mission create --brd-recorded-void-action ...
mission actions --mission-id
mission action-status --action-id
mission approve-action --approval-id --action-id --action-digest
```

- [ ] 运行测试并提交：

```powershell
git commit -m "feat: expose recorded brd action workflow"
```

---

## Task 6：阶段回归与审计

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_action_contracts.py `
  tests\test_agent_action_store.py `
  tests\test_agent_action_approval.py `
  tests\test_agent_recorded_action_executor.py `
  tests\test_agent_brd_recorded_void_action.py `
  tests\test_agent_brd_channel_score_worker.py `
  tests\test_agent_graph_runner_dag.py `
  tests\test_agent_cli_graph_control.py `
  tests\test_architecture_dependencies.py -q
```

Then:

```powershell
rg -n "aedt_agent\.v0" src\aedt_agent\agent src\aedt_agent\infrastructure
git diff --check
git status --short
```

## 完成定义

1. `adjust_layout_void` Action 有稳定 schema、digest 和状态机。
2. Action 与 Execution 跨 SQLite 重启可恢复。
3. Approval 与 action_id/digest 强绑定，篡改后不能执行。
4. recorded adapter 完成 before/after 比较且不修改原始 artifacts。
5. improved 接受，regressed/unchanged 回滚，mixed 等待人工。
6. real AEDT adapter 默认关闭。
7. CLI 能创建、审批、执行和查询 Action。
8. 所有动作和结果有 Event、Execution、Artifact/Evidence 审计。
