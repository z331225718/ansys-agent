# 有界 Mission Loop 实施计划

> 设计基线：`docs/superpowers/specs/2026-06-14-bounded-mission-loop-design.md`

## 目标

把现有“一次执行一个 Job”的运行时升级为可重启、可限额、可审计的单步 Mission 推进闭环，同时保持 BRD worker 和 Action executor 的单职责。

## 任务 1：定义循环与安全配置契约

**新增**

- `src/aedt_agent/agent/policies/execution_profile.py`
- `src/aedt_agent/agent/orchestrator/loop_contracts.py`
- `tests/test_agent_execution_profile.py`
- `tests/test_agent_mission_loop_contracts.py`

**步骤**

1. 先测试 `safe-recorded` 默认值、JSON round-trip 和非法上限；
2. 定义 `ExecutionProfile`；
3. 定义 `MissionLoopStatus`、`LoopDecisionType`、`MissionLoopRecord`、`LoopDecision`；
4. 保证所有契约可序列化，且默认拒绝真实 AEDT。

**验收**

```powershell
pytest -q tests/test_agent_execution_profile.py tests/test_agent_mission_loop_contracts.py
```

## 任务 2：持久化 loop、usage 与 final outcome

**修改**

- `src/aedt_agent/agent/mission/contracts.py`
- `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- `tests/test_agent_mission_loop_store.py`
- `tests/test_agent_state_machine.py`

**步骤**

1. 增加 loop 事件类型；
2. 新增 `mission_loops` 表；
3. 实现 create/get/update loop；
4. 实现 `set_mission_final_outcome()`；
5. 让 `update_mission_state()` 调用 `assert_transition()`；
6. 为终态 outcome、非法跳转和重启读取编写测试。

**验收**

```powershell
pytest -q tests/test_agent_mission_loop_store.py tests/test_agent_state_machine.py
```

## 任务 3：完成 Job 重试闭环

**修改**

- `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- `src/aedt_agent/agent/orchestrator/runtime.py`
- `tests/test_agent_job_attempts.py`

**步骤**

1. 先测试 retryable 失败后 Job 重新变为 `QUEUED`；
2. 新增受约束的 `requeue_failed_job()`；
3. 失败 attempt 记录 backoff 与 retry 决策；
4. 达到 Job retry limit 后保持 `FAILED`；
5. 用 `try/finally` 保证 lease 总能释放。

**验收**

```powershell
pytest -q tests/test_agent_job_attempts.py tests/test_agent_runtime_service.py
```

## 任务 4：实现预算与停止策略

**新增**

- `src/aedt_agent/agent/policies/mission_budget.py`
- `tests/test_agent_mission_budget.py`

**步骤**

1. 从 MissionLoopRecord、JobAttempt、Action 记录计算使用量；
2. 实现 iteration、attempt、wall time、evidence call/token 限制；
3. 实现连续无改善判断；
4. 实现重复 Action 计数入口；
5. 返回结构化 `LoopDecision`，不直接修改数据库。

**验收**

```powershell
pytest -q tests/test_agent_mission_budget.py
```

## 任务 5：实现 MissionLoopController

**新增**

- `src/aedt_agent/agent/orchestrator/mission_loop.py`
- `tests/test_agent_mission_loop_controller.py`

**修改**

- `src/aedt_agent/agent/orchestrator/__init__.py`

**步骤**

1. 首次推进完成 `CREATED -> PLANNING -> WAITING_WORKER`；
2. 审批中返回 `waiting_approval` 且不执行 worker；
3. queued Job 执行一个 attempt；
4. retryable 失败返回 `retry_job`；
5. 不可重试失败写 final outcome 并终止；
6. 所有 Job 成功后写 final outcome 并完成；
7. 预算耗尽写 `budget_exhausted`；
8. 无 Job、leased Job 和失败 Job分别返回稳定决策；
9. 重启后从同一个 loop 继续。

**验收**

```powershell
pytest -q tests/test_agent_mission_loop_controller.py
```

## 任务 6：启用 CLI advance/status/resume

**修改**

- `src/aedt_agent/agent/cli.py`
- `tests/test_agent_cli_mission_loop.py`

**步骤**

1. 新增 `advance` 和 `loop-status` parser；
2. `advance` 支持 `safe-recorded` 和 JSON profile；
3. `resume` 先恢复过期 lease，再推进一次；
4. 输出 decision、usage、limits、Mission 与 loop；
5. 保留通用 approval 命令的现有兼容行为；
6. 默认 profile 阻止 real AEDT capability。

**验收**

```powershell
pytest -q tests/test_agent_cli_mission_loop.py
```

## 任务 7：回归与审计

**检查**

1. 运行新增测试；
2. 运行 agent runtime、graph、evidence、action、BRD worker 回归；
3. 运行 `git diff --check`；
4. 确认新 agent 命名空间不依赖 `aedt_agent.v0`；
5. 检查工作区，只提交本阶段文件。

**建议命令**

```powershell
pytest -q `
  tests/test_agent_execution_profile.py `
  tests/test_agent_mission_loop_contracts.py `
  tests/test_agent_mission_loop_store.py `
  tests/test_agent_state_machine.py `
  tests/test_agent_job_attempts.py `
  tests/test_agent_mission_budget.py `
  tests/test_agent_mission_loop_controller.py `
  tests/test_agent_cli_mission_loop.py `
  tests/test_agent_runtime_service.py `
  tests/test_agent_graph_runner.py `
  tests/test_agent_graph_control_store.py `
  tests/test_agent_recorded_action_executor.py `
  tests/test_agent_brd_recorded_void_action.py

rg -n "aedt_agent\.v0" src/aedt_agent/agent src/aedt_agent/infrastructure
git diff --check
```

## 完成定义

- `mission advance` 能从持久化 Mission 执行一个有界步骤；
- retryable 失败确实形成下一次 attempt；
- 预算、审批和终止不会重复执行已完成 Job；
- 每个终态都有结构化 `final_outcome`；
- 重启后 `resume` 不丢 loop 与使用量；
- 默认配置不会误触发真实 AEDT；
- 全部相关回归测试通过。
