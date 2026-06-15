# 受控 Process Harness 设计

## 状态

- 日期：2026-06-15
- 状态：已批准进入实施
- 前置能力：持久化 Mission Runtime、有限 Mission Loop、YAML DAG Runner
- 范围：本地子进程隔离、工作区与环境策略、超时取消、心跳恢复、资源门控

## 目标

为 `ansys-agent` 增加一个独立于编排逻辑的受控执行底座，使 Worker 可以在隔离的本地进程中运行，并将请求、日志、结果、错误和资源状态持久化为可审计证据。

这个阶段解决的是“如何可靠地执行一个 Job”，不是“下一步执行什么”。工程判定、审批、重试次数、DAG 路由和 Mission 终态仍由 Agent Runtime 与策略层负责。

## 方案选择

采用“原生 Process Harness，预留容器适配器”：

```text
Graph Worker Node
    -> AgentRuntime.execute_job
    -> WorkerRegistry
    -> HarnessRequest
    -> LocalProcessHarness
    -> 独立 Python worker process
    -> HarnessResult
    -> WorkerExecutionResult
    -> JobAttempt / Artifact / DAG outcome
```

不采用 Docker-first，原因是当前主要执行环境包含 Windows、AEDT Desktop、许可证和可能的 COM/gRPC 会话，容器会提前放大部署成本。容器模式保留同一协议接口，但本阶段不要求真实 Docker 执行。

不在本阶段引入 Pi。Pi 的会话、工具和 Project Trust 能力不能替代 AEDT 进程树终止、许可证并发、工作区策略和 Job checkpoint。稳定 Harness API 是后续 Pi PoC 的前提。

## 核心原则

1. **Fail closed**：无法确认子进程状态时不得把 Job 或 Graph 标记为成功。
2. **Job 边界恢复**：只从最近完成的 Job checkpoint 恢复，不序列化活跃 AEDT 句柄。
3. **结构化通信**：父子进程通过版本化 JSON 文件交换请求和结果，自由文本只进入日志 artifact。
4. **最小环境继承**：默认不复制完整父进程环境，只继承运行必需项和 Profile 显式白名单。
5. **工作区约束**：每个 Attempt 使用独立目录，所有协议文件和默认 artifact 必须位于该目录。
6. **进程树治理**：timeout、cancel 和父进程清理必须针对整个进程树。
7. **资源分类**：普通 CPU Worker、AEDT Worker 和许可证 Worker 使用不同资源槽。
8. **机制与决策分离**：Harness 返回事实；Runtime/Policy 决定 retry、fail、approval 或 replan。

## 包边界

新增：

```text
src/aedt_agent/infrastructure/harness/
├── __init__.py
├── contracts.py
├── workspace.py
├── local_process.py
├── child_main.py
├── recovery.py
└── resources.py
```

职责：

- `contracts.py`：版本化 `HarnessRequest`、`HarnessResult`、`HarnessError`、`HarnessStatus`。
- `workspace.py`：Attempt 工作区创建、路径约束、环境白名单和 artifact 清单。
- `local_process.py`：启动、轮询、心跳、超时、取消、进程树终止和结果装载。
- `child_main.py`：子进程入口，导入强类型 Worker entrypoint，执行并原子写入结果。
- `recovery.py`：扫描不完整 Attempt，识别 active、stale、interrupted。
- `resources.py`：进程内资源槽，约束 AEDT 与许可证并发。

Agent 层修改：

- `agent/workers/registry.py` 增加 Worker registration，明确 `in_process` 或 `local_process`。
- `agent/orchestrator/runtime.py` 将 Job、Attempt 和 Harness 生命周期连接起来。
- `agent/policies/execution_profile.py` 增加 Harness 工作区、环境、心跳和终止宽限配置。
- `agent/cli.py` 增加 `mission recover-harness` 和 Harness 状态输出。

## Worker 注册

Worker 注册不再只保存一个 Python callable，而是保存可审计的执行规格：

```python
@dataclass(frozen=True)
class WorkerRegistration:
    capability: str
    execution_mode: str
    handler: WorkerFn | None = None
    entrypoint: str = ""
    resource_class: str = "cpu"
    allowed_env: tuple[str, ...] = ()
```

约束：

- `in_process` 必须提供 `handler`；
- `local_process` 必须提供 `module:function` 格式的 `entrypoint`；
- `resource_class` 只允许 `cpu`、`aedt`、`license`；
- 不允许把 lambda 或闭包序列化给子进程；
- 未注册 capability 继续返回结构化 `INVALID_INPUT`。

现有测试 Worker 默认保留 `in_process`，避免所有单元测试都依赖 subprocess。真实长任务 Worker 可以逐个切换到 `local_process`。

## Harness 协议

### HarnessRequest

```json
{
  "protocol_version": 1,
  "harness_run_id": "uuid",
  "mission_id": "uuid",
  "job_id": "uuid",
  "attempt_id": "uuid",
  "worker_id": "graph:worker:1",
  "capability": "brd.local_cut.build",
  "entrypoint": "aedt_agent.agent.workers.process_entrypoints:run_brd_local_cut",
  "timeout_seconds": 900,
  "heartbeat_interval_seconds": 5,
  "input_payload": {},
  "workspace": "absolute-path"
}
```

### HarnessResult

```json
{
  "protocol_version": 1,
  "harness_run_id": "uuid",
  "status": "succeeded",
  "output_payload": {},
  "artifact_refs": [],
  "error": null,
  "started_at": "ISO-8601",
  "completed_at": "ISO-8601",
  "exit_code": 0,
  "termination_reason": ""
}
```

`status` 只允许：

- `succeeded`
- `failed`
- `timed_out`
- `canceled`
- `interrupted`

子进程必须先写临时文件，再原子替换 `result.json`。父进程只接受协议版本、run ID 和 Job ID 一致的结果。

## 工作区与环境

默认目录：

```text
<harness_root>/
└── <mission_id>/
    └── <job_id>/
        └── <attempt_id>/
            ├── request.json
            ├── result.json
            ├── heartbeat.json
            ├── stdout.log
            ├── stderr.log
            └── artifacts/
```

安全规则：

- ID 只作为单个路径段使用，禁止 `..`、斜杠、反斜杠和绝对路径；
- resolved attempt 目录必须位于 configured harness root；
- 子进程 cwd 固定为 attempt workspace；
- 默认允许环境变量：`PATH`、`PATHEXT`、`SYSTEMROOT`、`WINDIR`、`TEMP`、`TMP`、`HOME`、`USERPROFILE`、`PYTHONPATH`；
- Profile 可以追加 `AWP_ROOT*`、`ANSYSEM_ROOT*`、`LM_LICENSE_FILE`、`CDS_*` 等明确名称；
- 密钥类变量不默认继承；
- 请求和结果 JSON 不记录环境变量值，只记录允许的变量名和环境摘要哈希。

输入 artifact 可以位于工作区外，但必须通过 Job 的 artifact 引用显式传入。Harness 不承诺操作系统级文件系统沙箱；当前阶段的安全保证是受控 Worker entrypoint、cwd、环境白名单和无任意命令入口。

## 子进程生命周期

父进程：

1. 创建 Attempt workspace；
2. 写入 `request.json`；
3. 获取对应资源槽；
4. 启动 `python -m aedt_agent.infrastructure.harness.child_main`；
5. 将 stdout/stderr 写入独立日志；
6. 轮询退出状态、cancel signal、wall timeout 和 heartbeat；
7. 正常退出后校验 `result.json`；
8. 异常退出时合成结构化 Harness error；
9. 释放资源槽；
10. 返回 HarnessResult。

子进程：

1. 校验请求协议；
2. 原子写 heartbeat；
3. 导入 entrypoint；
4. 重建只读 Job 与 WorkerContext；
5. 执行 Worker；
6. 将返回值规范化；
7. 原子写 `result.json`；
8. 停止 heartbeat 并退出。

## 超时与取消

`LocalProcessHarness` 接收可查询的 cancel callback。满足任一条件时进入终止流程：

- `Job.timeout_seconds` 到期；
- Graph/Mission 已取消；
- 外部调用方请求取消；
- heartbeat 超过 `heartbeat_timeout_seconds`，且子进程状态无法确认。

终止流程：

1. 发送 graceful termination；
2. 等待 `termination_grace_seconds`；
3. 强制终止整个进程树；
4. 写入 `timed_out` 或 `canceled` 结果；
5. stdout/stderr 和已有 artifact 保留。

Windows 优先使用新的进程组和 `taskkill /T /F` 作为强制清理手段；POSIX 使用 process group 的 `SIGTERM`/`SIGKILL`。命令参数使用列表，不经过 shell。

## 心跳与恢复

heartbeat 至少包含：

```json
{
  "protocol_version": 1,
  "harness_run_id": "uuid",
  "job_id": "uuid",
  "pid": 1234,
  "updated_at": "ISO-8601"
}
```

恢复分类：

- `completed`：存在有效 `result.json`；
- `active`：heartbeat 新鲜且 PID 存活；
- `stale`：heartbeat 过期但 PID 仍存活；
- `interrupted`：无有效结果且 PID 不存在；
- `invalid`：协议或目录内容不合法。

本阶段对 `interrupted` Attempt 的行为：

- Job 当前为 `running` 时标记失败；
- JobError 使用 `WORKER_CRASH`、`retryable=true`；
- 结束对应 JobAttempt；
- 在 retry limit 内重新入队；
- 与该 Job 绑定的 Graph NodeRun 保持 `running`，直到 Runtime recovery 将它转换为可重试状态。

不会自动接管一个仍然活着但失去父进程的 AEDT 会话。`active/stale` 只报告，除非调用方明确请求清理。

## 资源门控

`ResourceGate` 按 Profile 建立三个 semaphore：

- `cpu`：默认不额外限制或使用通用上限；
- `aedt`：上限为 `max_concurrent_aedt`；
- `license`：上限为 `max_concurrent_license_jobs`。

获取资源槽的等待时间计入 Job wall timeout。资源等待状态必须出现在 Harness metadata 中。

当前门控是单 Runtime 进程内有效。跨进程/多机全局许可证调度属于后续阶段，不在本阶段伪装实现。

## Runtime 集成

`AgentRuntime.execute_job()` 保留唯一 Job 执行入口：

- in-process registration 继续走现有 callable；
- local-process registration 生成 HarnessRequest 并调用 Harness；
- HarnessResult 转换为 WorkerExecutionResult；
- JobAttempt 记录 `harness_run_id`、workspace、exit code、termination reason；
- Harness artifact 注册为 ArtifactManifest；
- retry 决策仍使用现有 JobError 和 retry limit。

GraphRunner 不直接调用 Harness，也不感知 subprocess。它只观察 Job/NodeRun 的结构化结果。

## CLI

新增：

```text
aedt-agent mission recover-harness
    --mission-id <id>
    [--terminate-stale]
```

输出：

- 扫描到的 Harness Attempt；
- recovery classification；
- 被终止的 PID；
- 被标记 interrupted 的 Job/Attempt；
- 重新入队的 Job。

`mission status` 的 JobAttempt 输出增加 Harness metadata。

## 错误映射

| Harness 事实 | JobError |
| --- | --- |
| 子进程正常返回 failed | 使用子进程结构化错误 |
| wall timeout | `TIMEOUT`, retryable |
| heartbeat 丢失且进程消失 | `WORKER_CRASH`, retryable |
| cancel | `CANCELED`, non-retryable |
| 非零退出且无结果 | `WORKER_CRASH`, retryable |
| 结果协议错误 | `WORKER_CRASH`, retryable |
| workspace/env/profile 非法 | `INVALID_INPUT`, non-retryable |
| 资源槽等待超时 | `TIMEOUT`, retryable |

Harness 不把许可证错误从普通日志中猜出来。Worker 应返回结构化错误；现有错误分类器只作为兼容后备。

## 测试策略

单元测试：

- 协议 round-trip 和非法字段；
- 路径逃逸拒绝；
- 环境白名单；
- ResourceGate 并发上限；
- recovery 分类。

子进程集成测试：

- 成功 Worker 返回结构化结果；
- stdout/stderr 成为 artifact；
- Worker 异常映射为 failed；
- timeout 终止进程；
- 子进程再启动子进程时，取消可清理进程树；
- 非原子/损坏结果被拒绝；
- heartbeat 更新和 interrupted recovery。

Runtime/DAG 测试：

- local-process Job 与 in-process Job 使用同一 Runtime API；
- retryable timeout 重新入队；
- approval 与 scorecard 行为不受影响；
- Graph 只在 Job 真正完成后推进；
- 重启扫描不会把 running NodeRun 误判为成功。

平台测试：

- Windows 为主要 CI/开发路径；
- POSIX 进程组逻辑通过可注入 process controller 单元测试；
- 真实 AEDT smoke 保持 opt-in。

## 非目标

本阶段不实现：

- 任意 shell 命令执行；
- 完整 OS sandbox；
- Docker 镜像构建和调度；
- Kubernetes、Celery、Redis 或跨机器 Worker；
- 活跃 AEDT COM handle 迁移；
- 多租户权限系统；
- Pi 集成；
- 让 LLM 动态提高资源或环境权限。

## Pi 后续评估入口

Harness 完成后，Pi PoC 只能通过稳定公共边界接入：

```text
Pi session/runtime
    -> Mission API
    -> Event / Approval stream
    -> Worker capability request
    -> Python Runtime
    -> Process Harness
```

PoC 必须测量：

- 会话和模型 provider 代码减少量；
- streaming/approval 交互改善；
- Python/TypeScript 双运行时部署成本；
- 崩溃恢复与审计是否保持；
- Project Trust 与 Harness 策略是否职责重叠；
- 对真实 AEDT 长任务的成功率、恢复时间和运维复杂度。

## 完成定义

1. Worker 可以显式选择 `in_process` 或 `local_process`。
2. local-process Worker 通过版本化结构化协议运行。
3. 每个 Attempt 有独立安全工作区和环境白名单。
4. timeout/cancel 能终止整个进程树并保留日志。
5. heartbeat 与恢复扫描能识别 interrupted Attempt。
6. interrupted Job 可按现有 retry policy 重新入队，Graph 不误报成功。
7. AEDT 与许可证资源并发受 Profile 限制。
8. 请求、结果、stdout、stderr 和执行元数据可审计。
9. Agent Runtime、YAML DAG 和现有 in-process Worker 兼容。
10. Agent 测试通过，新增代码不依赖 `aedt_agent.v0`。
