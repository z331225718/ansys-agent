# Real BRD Solve Mission 设计

## 状态

- 日期：2026-06-15
- 状态：Approved by standing execution direction
- 前置能力：BRD real build-only adapter、Persistent YAML DAG Runner、Controlled Process Harness、BRD Solve Evidence Pipeline

## 目标

把审批后的 BRD local-cut AEDT 工程推进为一个真实、可恢复、可审计的求解闭环：

```text
approved model checkpoint
    -> controlled real AEDT solve
    -> Touchstone export
    -> TDR report export
    -> bounded deterministic analysis
    -> EvidencePackage
    -> scorecard / engineering delivery
```

这个阶段证明新 Agent Runtime 能通过正式 Worker、Job、Event、Approval、Checkpoint 和 Process Harness 契约完成一次真实 AEDT 场景。它不依赖 VLM，也不把数千个频点直接塞入 GLM 上下文。

## 当前缺口

现有代码已经具备：

- `BrdRealBuildAdapter`：创建 build-only `.aedt` 工程；
- `brd.channel.score`：读取 Touchstone/TDR 并生成确定性评分；
- S 参数 extrema-preserving buckets 和 bounded window query；
- EvidencePackage、Scorecard、YAML DAG、Approval；
- local-process Harness、timeout/cancel、heartbeat recovery；
- recorded void action 的审批、比较和 rollback 语义。

缺少的关键链路是：

1. 从已审批模型 checkpoint 打开真实 `.aedt` 工程；
2. 在独立子进程中执行指定 setup；
3. 导出标准 Touchstone 和 TDR CSV；
4. 把求解日志、工程副本和结果注册为 artifact；
5. 将 bounded evidence 传给 Graph，而非传原始数组；
6. 对 license、AEDT 进程、timeout、cancel 和父进程重启做一致处理。

## 非目标

- 不在本阶段自动修改 void、anti-pad 或 stackup。
- 不允许 LLM 生成任意 PyAEDT/PyEDB 代码。
- 不求解整板；输入必须是已批准的 local-cut 工程。
- 不把 raw Touchstone、TDR 数组或完整 bucket 列表放进 planner prompt。
- 不实现 VLM 曲线读图。
- 不实现跨机器的全局 license broker。
- 不引入 Pi；Pi 继续等待真实 native Mission 验收结果。
- 不承诺所有 AEDT 版本和所有报告模板，首版固定支持 2026.1 的 HFSS 3D Layout local-cut 项目。

## 设计选择

采用三个明确边界：

1. `BrdRealSolveAdapter` 只负责 AEDT 生命周期、求解和导出。
2. `brd.local_cut.solve` Worker 只负责协议校验、调用 adapter 和生成 manifest。
3. 现有 `brd.channel.score` 继续负责解析、压缩和工程判分。

不把求解与评分写进同一个 Worker。这样 AEDT 进程失败时不会污染确定性分析，已有 Touchstone/TDR 也可以独立重跑评分。

## 公共契约

### BrdRealSolveRequest

```python
@dataclass(frozen=True)
class BrdRealSolveRequest:
    project_path: Path
    artifact_dir: Path
    setup_name: str
    sweep_name: str
    solution_name: str
    touchstone_name: str
    tdr_report_name: str
    tdr_expression: str
    expected_port_count: int
    environment: RealAedtEnvironment
```

约束：

- `project_path` 必须存在且后缀为 `.aedt`；
- 项目必须来自已审批 checkpoint；
- setup、sweep、solution、TDR expression 必须显式给出；
- `expected_port_count >= 2`；
- `artifact_dir` 只能由 local-process `WorkerContext` 提供，且必须是当前 Job attempt 工作区内的 `artifacts/`；
- 不接受任意脚本、命令行或 Python expression。

### BrdRealSolveResult

```python
@dataclass(frozen=True)
class BrdRealSolveResult:
    project_checkpoint: str
    solved_project: str
    touchstone_path: str
    tdr_path: str
    solve_manifest_path: str
    summary: dict[str, Any]
```

`summary` 只包含：

- adapter/version/setup/sweep；
- started/completed/duration；
- exported file sizes 和 SHA-256；
- 端口数量；
- AEDT analyze/export 状态；
- warning/error 摘要；
- artifact refs。

它不得包含 raw S 参数或 TDR 点。

## 工程 checkpoint 与幂等性

真实 solve 不直接修改 build-only 原件。

每次 attempt：

1. 从输入 artifact 复制 `.aedt` 及必要的 `.aedb`/results 关联目录到 attempt workspace；
2. 对副本执行 solve；
3. 所有输出写入 `artifacts/`；
4. 原 build checkpoint 保持只读语义；
5. retry 创建新的 attempt workspace，但使用相同输入 digest；
6. completed `result.json` 由 Harness recovery 接管，不重复求解。

`solve_manifest.json` 至少记录：

```json
{
  "mission_id": "...",
  "job_id": "...",
  "attempt_id": "...",
  "input_project_sha256": "...",
  "setup_name": "Setup1",
  "sweep_name": "Sweep1",
  "solution_name": "Setup1 : Sweep1",
  "outputs": {
    "touchstone": {"path": "...", "sha256": "...", "bytes": 123},
    "tdr": {"path": "...", "sha256": "...", "bytes": 456}
  }
}
```

## AEDT Adapter 生命周期

首版 adapter 使用显式 factory，便于 fake 和真实环境共用测试：

```python
class BrdRealSolveAdapter:
    def __init__(self, *, hfss3dlayout_factory=None):
        ...

    def run(self, request: BrdRealSolveRequest) -> BrdRealSolveResult:
        ...
```

执行顺序固定：

1. 验证请求与工程 artifact；
2. 复制工程 checkpoint；
3. 打开 `Hfss3dLayout`，`new_desktop=True`；
4. 验证 setup/sweep 存在；
5. 调用 analyze；
6. 保存 solved project；
7. 导出 Touchstone；
8. 创建或复用固定名称的 TDR report，导出 CSV；
9. 验证输出非空且格式可解析；
10. 写原子 manifest；
11. release desktop。

`release_desktop()` 必须在 `finally` 执行。Process Harness timeout/cancel 仍负责终止整个进程树，adapter 不拥有外部终止策略。

## TDR 导出策略

第一版以 AEDT 的确定性 report export 为权威来源，不让 LLM 看图，也不在 Python 中猜测图片曲线。

请求必须提供受控 `tdr_expression`。首版只接受白名单形式：

```text
TDRZt(<port>,<port>)
```

或项目模板预注册的等价表达式。Worker 不接受任意 report expression。

若 AEDT 无法创建或导出 TDR report：

- Job 失败；
- 保留 solved project、日志和已导出的 Touchstone；
- 不伪造 TDR；
- 不进入 channel score；
- 错误标记为 `artifact_missing` 或 `invalid_input`，由策略决定是否 retry。

以后可以增加“由复数 S 参数离线变换生成 TDR”的独立 adapter，但不与本阶段混合。

## Process Harness 与组合资源

真实 solve Worker 必须使用：

```text
execution_mode = local_process
resource_classes = ["aedt", "license"]
```

当前 Registry 只有单个 `resource_class`，本阶段扩展为有序组合资源：

```python
resource_classes: tuple[str, ...] = ("cpu",)
```

ResourceGate 按固定全局顺序获取 semaphore：

```text
license -> aedt -> cpu
```

获取失败时按相反顺序释放已获得 lease，防止死锁和泄漏。相同资源名去重，未知资源拒绝。

这仍是单 Runtime 进程内门控。多机 license broker 留到真实部署阶段。

## Worker

新增 capability：

```text
brd.local_cut.solve
```

Worker entrypoint 必须是可导入的顶层函数：

```text
aedt_agent.agent.workers.brd_real_solve:run_brd_real_solve_worker
```

为避免 Job payload 自行选择输出路径，`WorkerContext` 扩展为：

```python
@dataclass(frozen=True)
class WorkerContext:
    worker_id: str
    workspace: str | None = None
    artifacts_dir: str | None = None
```

`child_main` 从已验证的 HarnessRequest workspace 注入这两个字段。in-process Worker 的值保持 `None`。真实 solve Worker 如果没有 `artifacts_dir` 必须 fail closed。

输入来源：

- build NodeRun/Job 的 approved project artifact；
- setup/sweep metadata；
- ExecutionProfile 的 AEDT 环境；
- target metric 配置；
- attempt workspace。

输出：

```json
{
  "status": "succeeded",
  "solve_summary": {
    "raw_sparameters": "artifact_only",
    "raw_tdr": "artifact_only"
  },
  "touchstone_path": "...",
  "tdr_path": "...",
  "solve_manifest": "...",
  "artifact_refs": ["..."]
}
```

Worker 不解析全部频点，不做 pass/fail。

## 大数组与 Evidence Query

现有 `brd.channel.score` 保留总体判分。新增 artifact query service，使模型只按需获取小窗口：

```python
query_sparameter_artifact(
    artifact_ref,
    frequency_start_ghz,
    frequency_stop_ghz,
    *,
    max_points=64,
)

query_tdr_artifact(
    artifact_ref,
    time_start_ps,
    time_stop_ps,
    *,
    max_points=64,
)
```

硬限制：

- 单次最多 128 点；
- 默认 64 点；
- 保留窗口首尾、最大/最小、阈值 crossing 和局部极值；
- 返回原始 artifact digest；
- query 参数和结果写 Event；
- planner 每个 iteration 的 query 次数受 ExecutionProfile 限制；
- EvidencePackage 默认只保存 summary 与 artifact refs。

0 到 67 GHz、0.05 GHz 间隔产生约 1341 个频点，不会直接进入 GLM 128k 上下文。

## Graph

新增 YAML 模板：

```text
model_checkpoint_validator
    -> model_approval_gate
    -> real_solve_worker
    -> channel_score_worker
    -> scorecard
```

边：

```text
validator.succeeded -> approval_gate
approval_gate.approved -> real_solve_worker
approval_gate.rejected -> terminal rejected
real_solve_worker.succeeded -> channel_score_worker
real_solve_worker.failed -> terminal failed/retry policy
channel_score_worker.succeeded -> scorecard
```

同一 Mission/GraphRun 在 approval 后恢复；不得新建一个“看似相关”的 Mission。Graph handoff 只传：

- project artifact ref；
- setup/sweep 名称；
- target metrics；
- solve manifest ref；
- Touchstone/TDR refs；
- bounded evidence summary。

## ExecutionProfile

新增或明确以下字段：

```json
{
  "allow_real_aedt": false,
  "aedt_version": "2026.1",
  "aedt_non_graphical": true,
  "solve_timeout_seconds": 7200,
  "max_concurrent_aedt": 1,
  "max_concurrent_license_jobs": 1,
  "max_evidence_query_calls": 24,
  "max_evidence_tokens": 24000
}
```

安全默认值继续 `allow_real_aedt=false`。只有显式 profile 才允许真实 solve。

CLI/runtime 构建 Registry 时注册 real solve process worker，但 MissionLoop 在执行前必须继续检查 `allow_real_aedt`。

## 错误分类与重试

| 场景 | ErrorClass | Retry |
| --- | --- | --- |
| 工程、setup、sweep、TDR expression 非法 | invalid_input | 否 |
| license unavailable/denied | license_unavailable | 是 |
| AEDT 子进程崩溃 | worker_crash | 是 |
| wall timeout | timeout | 是，受 retry limit |
| 用户取消 | canceled | 否 |
| Touchstone/TDR 未导出或为空 | artifact_missing | 默认否 |
| 输出无法解析 | artifact_invalid | 否 |

retry 必须从只读 checkpoint 创建新 workspace，不复用可能损坏的 solved project。

## CLI

新增创建入口：

```text
aedt-agent mission create \
  --goal "求解已审批的 BRD local cut" \
  --brd-real-solve \
  --project <approved.aedt> \
  --setup Setup1 \
  --sweep Sweep1 \
  --tdr-expression "TDRZt(P1,P1)"
```

执行继续使用：

```text
aedt-agent mission advance --profile real-aedt.json
aedt-agent mission resume --profile real-aedt.json
aedt-agent mission recover-harness --mission-id ...
```

查询：

```text
aedt-agent mission evidence --mission-id ...
aedt-agent mission artifact-query --artifact-ref ... --frequency 17 19 --max-points 64
```

`artifact-query` 输出 bounded JSON，不输出整个文件。

## Scorecard

真实 solve scorecard 至少验证：

- model approval 属于同一 Mission；
- solve Job 使用 local-process；
- Attempt metadata 有 harness run/workspace；
- project input digest 与 checkpoint 一致；
- Touchstone/TDR/manifest/log artifacts 存在且 digest 可重算；
- channel score 来源指向 solve 输出；
- EvidencePackage 不含 raw arrays；
- timeout/cancel 后没有存活进程；
- GraphRun 到达明确终态。

Scorecard 不以 Worker 自报 `status=succeeded` 为充分证据。

## 测试策略

### 单元测试

- request 拒绝非 `.aedt`、空 setup、非法 TDR expression；
- adapter 调用 analyze、Touchstone export、TDR export、save、release；
- 导出空文件失败；
- manifest digest 正确；
- Worker output 不含 raw 数组；
- composite ResourceGate 获取/释放顺序稳定；
- artifact query 受 max_points 限制并保留窄带异常。

### 集成测试

- fake AEDT adapter 通过 local-process Harness 运行；
- Graph 在 approval 前停止，批准后恢复同一 GraphRun；
- solve 结果进入现有 channel score；
- EvidencePackage 和 ArtifactManifest 关联正确；
- timeout/cancel/recovery 不重复已完成 solve；
- safe profile 在打开 AEDT 前阻止真实 Job。

### 真实 smoke

默认跳过，只有显式环境变量启用：

```text
ANSYS_AGENT_RUN_REAL_AEDT=1
```

smoke 输入是小型、已审批 local-cut 工程，不使用整板。验收：

- AEDT 2026.1 可打开工程；
- 指定 setup 完成；
- Touchstone 与 TDR CSV 非空且可解析；
- EvidencePackage 生成；
- Mission/GraphRun 明确成功或以结构化错误失败；
- Desktop/子进程退出。

## 安全与审计

- 无任意 shell、Python 或 report expression 入口；
- 工程输入和输出均计算 SHA-256；
- 原 build checkpoint 不原地修改；
- 环境变量仍走 Profile 白名单；
- AEDT 和 license 并发均受门控；
- 每次 artifact query 记录范围和返回点数；
- raw 数据只通过 artifact ref 传播；
- 失败输出和日志保留，不因 rollback 或 retry 删除。

## 与受控 Action 的衔接

本阶段成功后，现有 `adjust_layout_void` action 才可启用 `real_aedt` adapter：

```text
solve before
    -> deterministic evidence
    -> propose bounded action
    -> approval
    -> copy checkpoint and apply
    -> solve after
    -> compare
    -> accept or rollback
```

真实 action 不属于本规格的实现范围，但本阶段产生的 project checkpoint、solve manifest、Touchstone/TDR 和 EvidencePackage 必须可直接作为其输入。

## 完成定义

1. 真实 solve 有独立 adapter 和 `brd.local_cut.solve` process worker。
2. Worker 同时受 AEDT 与 license 组合资源门控。
3. 求解只操作 checkpoint 副本，retry/recovery 不重复已完成 attempt。
4. Touchstone、TDR、solved project、manifest 和日志均成为可校验 artifact。
5. raw S 参数/TDR 不进入 Mission summary、EvidencePackage 或模型上下文。
6. bounded artifact query 支持 S 参数和 TDR 小窗口。
7. YAML Graph 在同一 Mission 中完成 approval、solve、score、scorecard。
8. safe profile 默认阻止真实 AEDT；显式 profile 才允许。
9. fake/recorded 集成测试稳定，真实 AEDT smoke 有显式入口。
10. Agent 测试全绿，全仓既有失败集合不扩大。
