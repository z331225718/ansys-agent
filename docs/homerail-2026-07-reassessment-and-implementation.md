# HomeRail 2026-07 最终复审与实施结论

> 本文是截至 2026-07-16 的最新复审与实施状态。它更新优先级和落地状态，但不改写
> `docs/homerail-runtime-lessons.md` 与
> `docs/homerail-runtime-council-conclusion.md` 中的历史顾问原结论。

## 1. 复审范围与证据边界

本次复审同时查看了三类证据：

- HomeRail 本地 clone：
  `C:\Users\z3312\.graphify\repos\xiaotianfotos\homerail`；
- HomeRail 最新审视提交：
  `c983764a9d67aa5354efc5b12d102fd1c1d9a85a`，提交时间
  `2026-07-16 17:46:27 +0800`，标题
  `Validate the persistent three-Worker showcase (#55)`；
- `ansys-agent` 当前工作树中的运行时、SQLite store、CLI、真实求解 adapter 与聚焦测试。

相对旧基线 `1557730`，HomeRail 新增 37 个提交，变更 721 个文件，统计为
`+160,038/-3,476`。这已经不是对旧架构文档的小修订，而是把 durable activity
plane、logical actors、command inbox、多轮、worker lease/cold recovery、Manager
supervision、branch-local interventions、WorkflowSpec v1 immutable revisions、fixed
artifacts、retention，以及真实 three-Worker 验证连成了更完整的运行时证据链。

本文对 `ansys-agent` 的判断基于**当前工作树**，不是一个已经发布或打标签的干净提交。
工作树中仍有用户原有的已修改和未跟踪代码、测试及其他文件；本次文档整理没有回滚、
覆盖或清理这些改动，也没有修改代码或测试。

验证边界必须明确：本机本轮没有实际启动 licensed AEDT。真实验收门禁已在无许可证
CI 中通过正式 `BrdRealSolveAdapter` 加模拟 backend 验证；这证明运行路径、契约和证据
校验逻辑可工作，不证明真实许可证可用，也不等同于一次真实电磁求解验收。

## 2. 为什么新上游信息改变了优先级

旧 council 的核心判断是 replay-first：先把已有事件变成可消费的时间线，再做诊断和
干预。以当时信息看，这条路线改动小、收益快、不会碰执行语义，是合理的。

新审视改变优先级的关键不只是 HomeRail 功能更多，而是它把 **generation/lease fence**
放在 durable actor、冷恢复和 branch-local intervention 的正确性边界上。与此同时，
`ansys-agent` 的实测暴露了一个直接的数据正确性问题：旧 lease 已过期且新 lease 已获取
后，旧执行仍曾能够完成同一个 job。若先做 replay，系统只会更清楚地记录一次错误提交，
却不能阻止旧结果覆盖当前执行状态。

因此本轮优先级调整为：

```text
P0 先保护写路径：lease generation fence
  -> P1 再消费事实：graph-run scoped replay + supervision
  -> P2 收紧跨节点数据：bounded handoff contracts
  -> P3 在安全点开放受限控制：retry-node / cancel-branch
  -> P4 管理终态证据：artifact retention + real AEDT acceptance gate
```

这不是否定旧 council。Replay 仍然是低风险、高收益的基础能力，只是从原 P0 调整为
P1：**正确性防线先于可观察性，受保护的事实再进入回放和监督。**

## 3. 逐项实施复审

### P0：JobAttempt lease fence 与 stale attempt 审计

**HomeRail 信号**

HomeRail 将 logical actor identity、actor generation、physical worker lease 和 cold
recovery 分开建模。新 generation 成为当前所有者后，旧 generation 的 activity、handoff
和命令确认不能再改变当前状态，但旧记录仍保留用于审计。这一信号来自 durable actor
设计、lease persistence/reaper 以及 cold recovery 测试，而不只是 UI 层状态展示。

**ansys-agent 落地**

- `JobAttemptRecord` 新增 `lease_id`，把每次 attempt 绑定到实际执行代次；
- job 的 `complete`、`fail`、`cancel` 都在 SQLite 条件更新中校验 execution fence；
- 有 lease 的提交必须命中唯一有效的 active lease；无 lease 提交只能作用于没有任何
  lease 的 queued job，不能绕过恢复后的执行所有权；
- 旧 attempt 的提交被拒绝后，attempt 以 `canceled/stale_fenced` 结束，记录请求 lease、
  当前 job 状态和 active lease 列表；
- harness recovery 同样走 fence，且重复恢复不会重复制造 stale audit。

**具体收益**

- 修复“旧 lease 在新 lease 获取后仍完成 job”的实测错误；
- 防止迟到 worker 把新执行的状态、输出和 artifact refs 覆盖掉；
- 冷恢复、超时重派和 worker 重连有了共同的所有权判据；
- stale 结果既不成为当前事实，也不会无痕消失，便于事后追责和故障分类。

**边界/风险**

- 这是 job/attempt/lease 层的写入 fence，不是 HomeRail 式完整 logical actor registry；
- fence 拒绝迟到的数据库提交，但不会强杀旧进程，也不能撤销它已在外部产生的 AEDT
  文件或其他副作用；外部写入仍需隔离目录、manifest 和哈希校验；
- 当前所有权依赖 SQLite 中 lease 状态正确维护，未来若引入远程 store，条件更新语义
  必须保持等价，不能退化为“先读后写”的非原子检查。

**关键文件**

- HomeRail：`docs/architecture/durable-dag-actors.md`、
  `homerail_manager/src/persistence/dag-actor-leases.ts`、
  `homerail_manager/tests/dag-actor-leases.test.ts`、
  `homerail_manager/tests/cold-recovery.test.ts`；
- ansys-agent：`src/aedt_agent/agent/mission/contracts.py`、
  `src/aedt_agent/infrastructure/sqlite_mission_store.py`、
  `src/aedt_agent/agent/orchestrator/runtime.py`、
  `tests/test_agent_sqlite_store.py`、`tests/test_agent_runtime_service.py`、
  `tests/test_agent_harness_recovery.py`。

### P1：graph-run scoped replay 与 graph-status supervision

**HomeRail 信号**

HomeRail 的 durable activity plane 将 append-only fact 与 actor command/control 分离，
Manager supervisor 只消费已接受、已脱敏、受界的 activity milestone，并用持久 cursor
增量读取。真实 three-Worker showcase 又把多 actor 活动、监督读取、Manager 重启和
surface 结果放进了同一条验证路径。

**ansys-agent 落地**

- 新增 `mission replay`，按 `graph_run_id` 从 mission 事件中重建 graph-local timeline；
- 通过 graph、node run、handoff、bound job、attempt、artifact、evidence、approval 的
  关联闭包筛选事件，避免把同 mission 的无关 graph 混入；
- 支持 `text` 和 `json` 输出，返回单调 `event_cursor`、entity counts 和事件 scope；
- text 渲染只展示受控摘要字段，不展开任意大 payload；
- `mission graph-status` 新增确定性的 `supervision`，给出 summary、blocking node、
  reason、recommended action、event cursor 和 counts。

**具体收益**

- operator 能回答一次 graph run 发生了什么，而不必手工拼多张表；
- failed、waiting approval、succeeded 等状态有稳定、非 LLM 的监督摘要；
- `supervision.event_cursor` 可直接作为受限干预的并发前置条件；
- CLI、后续 Web timeline 和审计报告有了同一份读取模型。

**边界/风险**

- 这是对现有 `EventRecord` 的 graph-scope 投影，不是全新的、带 actor generation 与
  producer sequence 的 HomeRail Activity Plane；
- graph 关联依赖持久对象和事件 payload 中的 ID。没有任何 graph 关联的旧事件不会被
  强行归入回放，这是避免误归属的保守选择；
- `supervision` 是确定性状态摘要，不包含完整资源诊断、实时订阅或自主 Manager 决策；
- 跨 worker 的因果关系仍不能仅凭全局时间戳推断，event cursor 主要用于回放顺序和
  graph-control 并发保护。

**关键文件**

- HomeRail：`homerail_protocol/src/dag-activity.ts`、
  `homerail_manager/src/persistence/dag-activity-journal.ts`、
  `homerail_manager/src/runtime/dag-manager-supervisor.ts`、
  `scripts/validate-three-worker-showcase.mjs`；
- ansys-agent：`src/aedt_agent/agent/event_replay.py`、
  `src/aedt_agent/agent/graph_runner.py`、`src/aedt_agent/agent/cli.py`、
  `tests/test_agent_event_replay.py`。

### P2：向后兼容的 bounded handoff contract

**HomeRail 信号**

HomeRail WorkflowSpec v1 将 handoff contract、严格 schema、canonical IR、不可变
workflow revision 和 run-bound snapshot 作为同一可复现性边界。Fixed artifacts 也只从
已验证 handoff 或受限 workspace 路径产生，避免由场景脚本临时解释输出。

**ansys-agent 落地**

- 保留旧 `required_fields` 形态，并与新 `required` 合并去重；
- 支持受限 schema 关键字：object/array/scalar type、properties、required、
  `additionalProperties`、enum、字符串/数值/数组/对象上下界和递归 items；
- 模板加载时校验 schema 定义，运行时在 handoff 创建前校验 payload；
- 默认硬上限为 `max_depth=16`、`max_nodes=10,000`、
  `max_serialized_bytes=1 MiB`；模板只能进一步收紧，不能放宽；
- schema 可序列化回 graph template snapshot，保持加载/保存兼容。

**具体收益**

- 跨节点字段错误在 handoff 边界失败，不再延迟到下游 scorecard 或 LLM；
- 对深层对象、超大数组和大 payload 有统一上限，降低数据库、日志和 prompt 放大风险；
- 旧模板无需一次性迁移，新模板可以逐步启用严格对象契约；
- 为后续固定 artifact、canonical template hash 和版本化模板目录提供了可复用基础。

**边界/风险**

- 这是受限 schema 子集，不是完整 JSON Schema；不支持任意组合关键字、引用或自定义
  format；
- 为兼容 legacy，仅声明 `required_fields` 的旧 schema 仍然较宽松，不会自动把 payload
  变成严格 JSON 对象。新增模板若需要强约束，必须显式声明 type/properties/
  `additionalProperties`；
- `ansys-agent` 已保存每次 graph run 的 template snapshot，但尚未实现 HomeRail 那种
  canonical hash 驱动的 immutable workflow revision catalog、compiler version 和
  sync audit；不能把 snapshot 等同于完整 WorkflowSpec v1 revisions；
- fixed artifact 声明、通用物化和下载协议也尚未完整落地。

**关键文件**

- HomeRail：`docs/dag-workflow-spec-v1-overview.zh-CN.md`、
  `docs/workflow-spec-v1-authoring.md`、
  `homerail_manager/src/orchestration/workflow-spec-v1-schema.ts`、
  `homerail_manager/src/orchestration/workflow-spec-v1.ts`；
- ansys-agent：`src/aedt_agent/agent/handoff.py`、
  `src/aedt_agent/agent/graph_template.py`、
  `src/aedt_agent/agent/graph_runner.py`、
  `tests/test_agent_handoff_contracts.py`。

### P3：safe-point retry-node / cancel-branch

**HomeRail 信号**

HomeRail 把 command inbox 与 branch-local intervention 分开持久化：命令是 operator
intent，不是 activity fact；干预必须带 actor state token、idempotency key 和 generation
transition，并在恢复后可继续处理。Multi-round 也使用 expected round fence，拒绝迟到
命令和旧 round handoff。

**ansys-agent 落地**

- 新增 `mission intervene`，当前只开放 `retry-node` 与 `cancel-branch`；
- SQLite 写路径使用 `BEGIN IMMEDIATE`，先获取写锁，再校验 graph、mission、target、
  active job/lease、handoff 和 join 状态；
- 请求必须携带 `expected-event-cursor`、`idempotency-key` 和非空 reason；
- cursor 按 graph-control 相关事件计算：落后于控制状态或超前于 mission cursor 都拒绝；
- exact retry 返回原结果，同一 idempotency key 改变语义会冲突；
- created/applied/rejected 都有 intervention record 与事件审计；数据库 busy/locked 时
  返回 `intervention_busy`，由 operator 在重新读取状态后重试；
- `retry-node` 只重开最新 failed node，复用已持久输入并生成 synthetic handoff；
- `cancel-branch` 只取消尚未运行、已有 pending inbound handoff 的非 root 分支，并沿
  显式 `canceled` edge 继续。

**具体收益**

- 单个失败节点可局部重试，不必总是整图 takeover；
- 可取消尚未开始的分支，同时保留其他并行分支的结果；
- cursor、事务和幂等键把 operator 的“看见状态”和“应用控制”连成可审计写路径；
- 拒绝记录和成功记录同样可回放，避免人工操作只存在于聊天文本。

**边界/风险**

- 当前是 safe-point intervention，不会强杀任意运行中 worker；target 有 active node run、
  active job 或 active lease 时会拒绝；
- `retry-node` 要求最新 run 为 failed，且 failure branch 尚未被下游消费；
- `cancel-branch` 不允许 root、已运行节点或会破坏 `join=all` 必需来源的取消；
- 尚未支持 checkpoint fork、reassign、interrupt、运行中 inject、复杂 append node 或
  任意 Graph Patch；
- 事务保护的是 SQLite graph-control 状态，不能自动回滚外部 AEDT 副作用。

**关键文件**

- HomeRail：`docs/architecture/durable-dag-actors.md`、
  `docs/multi-round-dags.md`、
  `homerail_manager/src/persistence/dag-actor-interventions.ts`、
  `homerail_manager/tests/dag-actor-branch-intervention.test.ts`；
- ansys-agent：`src/aedt_agent/agent/graph_interventions.py`、
  `src/aedt_agent/infrastructure/sqlite_mission_store.py`、
  `src/aedt_agent/agent/cli.py`、
  `tests/test_agent_graph_interventions.py`、
  `tests/test_agent_cli_graph_interventions.py`。

### P4-A：terminal-only、hash-verified artifact prune

**HomeRail 信号**

HomeRail 同时建立 fixed run artifacts 与 workspace retention：运行输出有稳定名称、来源、
size/SHA-256 和发布条件；清理只考虑持久化终态 run、受管 workspace、pin 和 cleanup
状态，默认 dry-run，不扫描未知 orphan 目录。

**ansys-agent 落地**

- 新增 `mission artifact-prune`，默认 dry-run，显式 `--apply` 才删除；
- 仅允许 terminal mission：completed、failed 或 canceled；
- 只考虑 manifest-backed、超过年龄阈值、policy 为 `mission` 或 `transient` 且未 pinned
  的 regular file；
- 同时校验 lexical/resolved path 均位于 operator 指定 root 内，拒绝 symlink/reparse
  component、目录和未知路径；
- dry-run 和删除前都校验 size 与 SHA-256，删除前再次校验，降低 TOCTOU 风险；
- 记录 planned/applied/failed retention event，返回候选、跳过原因、删除量和 partial
  failure。

**具体收益**

- 长期 AEDT 运行产生的大量中间文件有了保守、可预览的清理入口；
- manifest、事件和工程证据元数据不随文件删除而静默消失；
- hash、root 和 reparse 检查降低误删用户文件或越界路径的风险；
- operator 可以把“预计释放多少空间”与实际删除结果纳入运维审计。

**边界/风险**

- 只删除受管 manifest 指向的文件，不删除目录，不扫描或猜测 orphan 文件；
- `keep`、pinned、过新、缺 hash、hash/size 不匹配、root 外路径都不会删除；
- 如果文件删除后 applied event 写入失败，报告会是 `partial_failure`；文件已经删除，
  需要依靠 planned/failed event 和 manifest 复核；
- 这不是完整 fixed artifact materialization/download service，也不处理远程 AEDT 节点
  的独立数据根。

**关键文件**

- HomeRail：`docs/workflow-spec-v1-authoring.md`、`docs/workspace-retention.md`、
  `homerail_manager/src/runtime/run-artifact-service.ts`、
  `homerail_manager/src/runtime/workspace-retention.ts`；
- ansys-agent：`src/aedt_agent/agent/artifact_retention.py`、
  `src/aedt_agent/agent/mission/contracts.py`、
  `src/aedt_agent/infrastructure/sqlite_mission_store.py`、
  `tests/test_agent_artifact_retention.py`。

### P4-B：real AEDT acceptance attestation gate

**HomeRail 信号**

HomeRail 最新提交的 three-Worker showcase 不只增加单元测试，还增加 self-hosted real
model/Worker workflow、Manager restart evidence、独立验证脚本和验收 artifact。对
`ansys-agent` 的直接启发是：模拟 worker 可以验证契约，但不能作为“真实工程执行已完成”
的最终证据；必须有单独、保守、可失败的 acceptance gate。

**ansys-agent 落地**

- 新增 `mission real-acceptance`，只检查指定 graph run，不负责启动 AEDT；
- 要求 graph 与 mission 成功终结，graph 是 `brd_real_solve_evidence` 或 snapshot 明确
  包含 real solve capability 与 scorecard；
- 要求 bound solve/score job 存在、solve job 成功、最新 solve attempt 为成功的
  `local_process` harness 执行、scorecard 通过；
- 复用现有 real-solve score checks，并要求 solve manifest version 1；
- `solve_summary` 与 manifest 中的 execution attestation 必须一致，且至少声明
  `kind=real_aedt`、`adapter=BrdRealSolveAdapter`、
  `backend=ansys.aedt.core.Hfss3dLayout`、`analyze_executed=true`，同时匹配请求的 AEDT
  version 与 graphical mode；
- solved project、Touchstone、TDR 必须与 job output 路径一致，并通过 size/SHA-256
  校验；
- 现有 `fake_real_solve` 即使能生成看似完整的 manifest 和输出，也因缺少真实执行
  attestation 被明确拒绝。

**具体收益**

- “graph succeeded”不再自动等于“真实 AEDT 已验收”；
- fake/recorded/simulated path 与真实 adapter path 有了显式分界；
- 篡改或缺失输出会在最终验收中失败；
- gate 结果可写成独立 JSON，供 operator、CI 和交付审计使用。

**边界/风险**

- 本机尚未实际启动 licensed AEDT，因此当前状态是“门禁实现并在模拟 backend 上验证”，
  不是“真实 licensed AEDT 最终验收已完成”；
- attestation 是运行路径与证据门禁，不是密码学签名、远程可信证明或许可证证明；它
  主要防止错误/测试 adapter 被误当成真实结果，不能抵御有意伪造同名字段；
- gate 是 post-run validator，不做 AEDT 安装、license、project lock 或资源调度的
  preflight；
- 一次真实通过仍需保留 AEDT 启动/求解日志、验收 JSON 和哈希后的输出 artifact。

**关键文件**

- HomeRail：`.github/workflows/three-worker-showcase.yml`、
  `scripts/validate-three-worker-showcase.mjs`、
  `scripts/manager-only-restart-evidence.mjs`、
  `homerail_manager/tests/three-worker-showcase-asset.test.ts`；
- ansys-agent：`src/aedt_agent/agent/real_aedt_acceptance.py`、
  `src/aedt_agent/infrastructure/brd_real_solve.py`、
  `src/aedt_agent/agent/graph_executors.py`、
  `tests/test_agent_real_aedt_acceptance.py`、
  `tests/test_infrastructure_brd_real_solve.py`。

## 4. Operator 命令

以下示例从仓库根目录运行，使用 PowerShell 和当前 CLI module。先替换实际 ID 与路径：

```powershell
$Db = "C:\path\to\missions.db"
$GraphRunId = "<graph-run-id>"
$MissionId = "<mission-id>"
$ArtifactRoot = "C:\path\to\managed-artifacts"
```

### 4.1 查看 supervision 与回放

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission graph-status --graph-run-id $GraphRunId

.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission replay --graph-run-id $GraphRunId --format text

.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission replay --graph-run-id $GraphRunId --format json
```

### 4.2 在最新 graph-control cursor 上干预

不要缓存旧 cursor。每次干预前重新读取 `graph-status`：

```powershell
$StatusJson = .\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission graph-status --graph-run-id $GraphRunId
$Status = $StatusJson | ConvertFrom-Json
$Cursor = $Status.supervision.event_cursor
```

重试一个满足 safe-point 条件的 failed node：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission intervene --graph-run-id $GraphRunId `
  --action retry-node --node-id "<failed-node-id>" `
  --expected-event-cursor $Cursor `
  --idempotency-key "retry-<graph-run-id>-<node-id>-v1" `
  --reason "operator reviewed failure evidence and approved one local retry"
```

取消一个尚未运行且可安全取消的 pending branch：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission intervene --graph-run-id $GraphRunId `
  --action cancel-branch --node-id "<pending-node-id>" `
  --expected-event-cursor $Cursor `
  --idempotency-key "cancel-<graph-run-id>-<node-id>-v1" `
  --reason "branch is no longer required after engineering review"
```

返回 `stale_event_cursor`、`intervention_busy` 或其他 rejected 状态时，不要盲重放不同
请求；先重新执行 `graph-status`/`replay`，确认 graph-control 状态和原 idempotency key。

### 4.3 预览并应用 artifact retention

默认是 dry-run：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission artifact-prune --mission-id $MissionId `
  --root $ArtifactRoot --older-than-hours 168
```

人工复核 candidates、skipped 和 bytes 后再 apply：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission artifact-prune --mission-id $MissionId `
  --root $ArtifactRoot --older-than-hours 168 --apply
```

### 4.4 生成真实 AEDT 验收报告

此命令只验证已完成的 graph run；不会替 operator 启动 AEDT：

```powershell
$AcceptanceReport = "C:\path\to\acceptance\real-aedt-acceptance.json"

.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $Db `
  mission real-acceptance --graph-run-id $GraphRunId `
  --output $AcceptanceReport
```

只有输出 `status=passed` 且进程退出码为 0 才算门禁通过。`failed`、`blocked`、
`partial_failure` 或 CLI 退出码 2 都应作为未验收处理。

## 5. 阶段结论

### 已可借鉴并已完成

- lease generation fence 已从架构建议变成写路径正确性防线，并修复已复现的 stale
  completion 问题；
- graph-run scoped replay 与 deterministic supervision 已形成可用 CLI；
- handoff contract 已支持兼容旧模板的严格化与统一上限；
- branch-local intervention 已在明确 safe point 上提供 retry-node/cancel-branch、CAS
  cursor、事务、幂等与审计；
- artifact retention 已有 terminal-only、dry-run-first、root/hash/reparse 保护；
- real AEDT acceptance gate 已能拒绝 `fake_real_solve` 和被篡改输出。

这里的“完成”指当前工作树中请求范围内的能力与聚焦验证已落地，不表示 licensed AEDT
最终验收完成，也不表示这些改动已发布。

### 应继续借鉴

- logical actor identity、command inbox 与多轮 round fence，可用于未来长时间工程会话；
- worker lease reaper/cold recovery 的端到端运行验证，尤其是旧进程副作用隔离；
- WorkflowSpec v1 的 canonical hash、immutable semantic revisions、compiler version 和
  run provenance；
- fixed artifact declaration/materialization/download，把稳定工程输出从场景代码中抽离；
- 先做薄 resource preflight，再根据真实多工作站需求评估 capability scheduler；
- 采用 HomeRail three-Worker showcase 的思路，为真实 AEDT 建立可重复、证据完整的
  self-hosted acceptance，而不是扩大 mock 覆盖后宣称完成。

### 现阶段仍不应照搬

- 每节点 Docker Worker。AEDT/PyAEDT/Windows COM/license 更适合本地或受管远程执行；
- voice-first、通用 memory/git/project 平台和大而全 Manager UI；
- 任意运行中 worker 强杀。在外部副作用、目录隔离和恢复语义没有完整定义前，应继续
  使用 safe-point 拒绝与 lease fence；
- 复杂动态 append/inject 或任意 Graph Patch。当前 retry/cancel 已解决一部分高价值
  场景，动态改图应等 immutable revisions、replay 和并发模型更稳定后单独设计；
- 一开始就建设完整 resource/capability scheduler。先用可审计 preflight 发现 AEDT、
  license、路径和 project lock 问题，真实调度需求出现后再扩展。

## 6. 下一步验收清单

以下顺序是有意的，前一项不应被后面的平台化工作替代：

1. **在 licensed AEDT 环境执行真实 `brd_real_solve_evidence`，随后运行
   `mission real-acceptance`。** 要求实际启动 `ansys.aedt.core.Hfss3dLayout`、执行
   analyze、生成 solved project/Touchstone/TDR，门禁 `status=passed`；同时归档 AEDT
   日志、acceptance JSON、manifest 和输出哈希。未完成前不得宣称真实 AEDT 最终验收。
2. **处理并行 fan-out 偶发测试波动。** 在 Windows 上对
   `tests/test_agent_graph_runner_dag.py` 与相关 CLI 并行用例做重复运行，区分线程调度、
   SQLite 锁等待、完成顺序断言和真实竞态；修复后要求多轮重复无偶发失败，且不把断言
   简单放宽为无序忽略。
3. **再考虑 resource preflight。** 先实现并验收薄检查：PyAEDT/AEDT 可启动性、请求
   version、license 探测、working/report/artifact 路径、source project lock/写权限和
   real-solve capability 注册；暂不扩展成完整 scheduler。
4. 对 lease fence 做真实多进程恢复演练：让旧进程迟到提交、新 lease 完成，确认旧
   attempt 只产生一次 `stale_fenced` audit，且新结果与 active lease 不受影响。
5. 对 `mission intervene` 做 advance/intervene 并发演练：验证 stale cursor、重复
   idempotency key、busy retry、已消费 failure branch 和 unsafe join cancel 均保守拒绝。
6. 用终态 mission 的实际 artifact 副本演练 prune：先 dry-run，再 apply；覆盖 keep、
   pinned、tampered、duplicate path、reparse/outside-root 和 applied-event failure 报告。
7. 在上述证据稳定后，再评审 immutable workflow revisions、fixed artifacts 和
   command inbox/multi-round 的独立实施提案，不把它们夹带进 resource preflight。

## 7. Reader test：新维护者能否接手

以第一次接触本轮改动的维护者视角，逐项检查：

| 问题 | 本文是否能回答 | 定位 |
|---|---|---|
| 为什么做？ | 能。说明了 HomeRail 新证据、已复现 stale lease bug，以及 P0/P1 调序理由。 | 第 1、2 节 |
| 做了什么？ | 能。P0-P4-B 均列出 HomeRail 信号、ansys-agent 落地、收益、边界和关键文件。 | 第 3 节 |
| 怎么用？ | 能。给出 PowerShell 的 status、replay、intervene、prune、real-acceptance 示例。 | 第 4 节 |
| 还缺什么？ | 能。明确 licensed AEDT 未验收、fan-out 波动、preflight 与未实现的平台能力。 | 第 5、6 节 |

Reader test 结论：**通过文档可完成方向判断与 operator 入门，但真实工程验收仍为
pending。** 新维护者不应从“模拟 backend 上 gate 通过”推导出“licensed AEDT 已通过”，
也不应把当前 safe-point intervention 误解为完整 dynamic actor runtime。
