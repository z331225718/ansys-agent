# 有界 Mission Loop 与安全执行配置设计

## 1. 背景

当前项目已经具备以下 Agent 基础能力：

- Mission、Job、JobAttempt、GraphRun、NodeRun 持久化；
- Worker 注册、租约、检查点、证据包与 Scorecard；
- 受控 Action、审批绑定、recorded adapter、结果比较与回滚；
- 面向大规模 S 参数数据的确定性摘要。

但现有运行时仍然是“执行一次 queued Job”的脚本式入口：

- `retry_available` 只被记录，不会重新入队；
- `MissionRecord.final_outcome` 没有统一写入入口；
- 没有 Mission 级迭代、Job、时间、查询和 token 预算；
- 没有“连续无改善”“重复动作”的停止规则；
- `resume` 尚未形成可恢复推进闭环；
- 环境、License、并发和真实 AEDT 开关没有成为一等配置。

因此第四阶段的目标不是扩大 worker 能力，而是增加一个**确定性、可持久化、可审计的 Mission 控制层**。

## 2. 核心决策

### 2.1 采用单步推进器，不采用进程内无限循环

新增 `MissionLoopController.advance()`。一次调用最多做一件有副作用的事情：

1. 检查 Mission 是否已经终止或正在等待审批；
2. 汇总持久化使用量并执行预算判断；
3. 选择一个 queued Job；
4. 执行一次 JobAttempt；
5. 根据结果决定继续、重试、等待审批、完成或失败；
6. 持久化本次循环决策。

外层 CLI、服务或未来的编排者可以重复调用 `advance()`，但核心控制器本身不依赖常驻 LLM，也不在内存里无限运行。这样进程重启后可以从 SQLite 的真实记录继续。

### 2.2 worker 保持单职责

预算、重试、停止条件和环境策略属于 orchestrator/policy 层，不能塞进 BRD worker。

Worker 只负责：

- 校验单次输入；
- 执行单个工程能力；
- 返回结构化结果、artifact refs 或分类错误。

### 2.3 所有终止都必须有 `final_outcome`

Mission 到达 `COMPLETED`、`FAILED` 或 `CANCELED` 时必须写入结构化结果：

```json
{
  "code": "budget_exhausted",
  "reason": "max_job_attempts reached",
  "decision": "failed",
  "usage": {},
  "limits": {},
  "last_job_id": "..."
}
```

预算耗尽不是异常丢失，而是一种可解释、可审计的工程结论。

## 3. 一等契约

### 3.1 ExecutionProfile

`ExecutionProfile` 描述 Mission 的安全边界：

- `max_iterations`：最大推进次数；
- `max_job_attempts`：所有 JobAttempt 总数；
- `max_wall_seconds`：Mission 从循环启动后的最长墙钟时间；
- `max_evidence_query_calls`：允许的证据细查次数；
- `max_evidence_tokens`：允许写入模型上下文的摘要 token 预算；
- `max_consecutive_no_improvement`：连续无改善 Action 上限；
- `max_duplicate_actions`：重复 Action 提案上限；
- `retry_backoff_seconds`：重试退避序列；
- `solve_timeout_seconds`：单次真实求解时间上限；
- `max_concurrent_aedt`：AEDT 并发槽位；
- `max_concurrent_license_jobs`：License 并发槽位；
- `allow_real_aedt`：是否允许真实 AEDT adapter；
- `execution_mode`：`recorded`、`local` 或未来的 `container`。

默认配置必须保守：

- `allow_real_aedt = false`；
- `max_concurrent_aedt = 1`；
- `max_concurrent_license_jobs = 1`；
- 不允许无限预算；
- recorded/deterministic 路径可以直接运行，真实求解必须显式启用。

### 3.2 MissionLoopRecord

每个 Mission 维护一个持久化循环记录：

- `loop_id`、`mission_id`、`profile`；
- `status`；
- `iteration_count`；
- `job_attempt_count`；
- `evidence_query_calls`、`evidence_tokens`；
- `duplicate_action_count`；
- `consecutive_no_improvement`；
- `started_at`、`updated_at`、`completed_at`；
- `last_decision`、`last_reason`、`last_job_id`。

使用量从持久化记录和真实审计对象计算或校验，不能只相信 LLM 自报。

### 3.3 LoopDecision

控制器输出固定枚举：

- `execute_job`
- `retry_job`
- `waiting_approval`
- `continue`
- `completed`
- `failed`
- `budget_exhausted`
- `stopped_no_improvement`
- `stopped_duplicate_action`
- `idle`

每个决策都包含 `reason`、`usage`、`limits` 和相关对象 ID。

## 4. 状态与重试

### 4.1 Mission 状态

SQLite store 必须调用现有 `assert_transition()`，禁止绕过状态机。

为兼容当前“创建 Mission 后立即创建 Job”的路径，控制器第一次推进时负责：

`CREATED -> PLANNING -> WAITING_WORKER`

之后：

- queued Job：保持或进入 `WAITING_WORKER`；
- Job 成功：进入 `EVALUATING`；
- 存在下一 Job：`EVALUATING -> WAITING_WORKER`；
- 等待审批：进入 `WAITING_APPROVAL`；
- 所有 Job 成功且无待审批：进入 `COMPLETED`；
- 不可重试错误或预算耗尽：进入 `FAILED`。

### 4.2 Job 重试

同一个 Job 使用同一个 `job_id` 和 `idempotency_key`，每次重试创建新的 `JobAttemptRecord`。

首次失败后：

- 仅当错误 `retryable=true`；
- 且 `attempt_number <= retry_limit`；
- 且 Mission 总预算未耗尽；

才把 Job 从 `FAILED` 重新置为 `QUEUED`。

重试决定必须记录：

- 错误分类；
- 当前 attempt；
- 剩余次数；
- 退避秒数；
- `retry_available` 或 `no_retry`。

第一版只持久化 `not_before`/退避决策，由下一次 `advance()` 判断是否到期；不在调用线程中 `sleep`。

## 5. 停止规则

### 5.1 连续无改善

从 Action comparison/decision 的真实记录计算：

- `improved` 或 `accepted`：计数清零；
- `unchanged`、`regressed`、`mixed`：计数加一；
- 达到 `max_consecutive_no_improvement` 后停止并写 final outcome。

### 5.2 重复动作

Action digest 已经保证同一 Mission 内唯一。控制层把重复创建异常转换为重复动作计数；达到上限后停止，避免 Planner 围绕同一个参数来回打转。

### 5.3 空闲与完成

无 queued Job 时不能一律报错：

- 有 pending approval：`waiting_approval`；
- 有 leased Job：`idle`，等待租约或恢复；
- 所有 Job succeeded：`completed`；
- 存在 failed 且无重试资格：`failed`；
- Mission 尚无 Job：`idle`，等待 Planner 创建计划。

## 6. 环境与安全

### 6.1 第一版保障

- recorded adapter 默认可用；
- real AEDT adapter 默认关闭；
- profile 明确单次 solve timeout；
- License 与 AEDT 并发上限持久化；
- 控制器执行真实能力前检查 profile；
- worker 异常继续使用确定性 `ErrorClass` 分类；
- 租约过期后可恢复为 queued。

### 6.2 第一版不承诺

- 不在 Python 线程内强杀失控的 AEDT COM/gRPC 调用；
- 不实现跨机器分布式 semaphore；
- 不实现容器级 CPU/RAM 限制；
- 不让 LLM 自由修改 profile；
- 不自动生成新的几何策略。

真正的进程隔离、容器资源限制和 License broker 属于后续 Harness 阶段。

## 7. CLI

新增：

```text
aedt-agent mission advance --mission-id ... [--profile ...]
aedt-agent mission loop-status --mission-id ...
aedt-agent mission resume --mission-id ...
```

- `advance`：执行一个确定性推进步骤；
- `loop-status`：返回 loop、usage、limits、Mission、Job 摘要；
- `resume`：恢复过期租约并执行一次 `advance`，不会重建已完成 Job。

profile 第一版支持内置 `safe-recorded` 和 JSON 文件。未知字段、负数或零上限必须拒绝。

## 8. 验收标准

1. Mission 状态非法跳转被 store 拒绝；
2. retryable Job 会在预算内重新入队并形成第二个 attempt；
3. 不可重试错误直接终止 Mission；
4. 达到 attempt、iteration 或 wall-time 上限时写入 `budget_exhausted`；
5. 等待审批时 `advance` 不重复执行 Job；
6. 进程重启后 `resume` 使用同一 Mission/Job/loop；
7. 所有 Job 成功后 Mission `COMPLETED` 且存在 `final_outcome`；
8. 默认 profile 拒绝真实 AEDT capability；
9. CLI 可以查询每一步循环决策；
10. 原有 graph、evidence、action 和 worker 测试保持通过。

## 9. 后续衔接

本阶段完成后，项目才具备“用户输入到受控执行再到明确终态”的最小 Agent 闭环。下一阶段再把：

- YAML DAG 的多节点依赖、分支、汇聚与打回；
- Planner 生成下一轮 Action；
- 外部 License broker 与隔离执行环境；
- Pi 或其他可信 Agent runtime 的模型会话、tool-call 与上下文管理；

接入这个已经稳定的控制契约，而不是反过来让框架决定工程状态机。
