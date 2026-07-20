# HomeRail 借鉴项本地顾问团结论

> 最新复审与实施状态见：[HomeRail 2026-07 最终复审与实施结论](homerail-2026-07-reassessment-and-implementation.md)。

本文是在 `docs/homerail-runtime-lessons.md` 的基础上，调用本地 `local-agent-council` 后形成的结论文档。

原始顾问输出保存在：

```text
runs-agent-consult/20260709-000358/agy-gemini.md
runs-agent-consult/20260709-000358/reasonix-deepseek.md
```

本次咨询要求两个本地顾问只分析、不修改文件。`git-status-before.txt` 与 `git-status-after.txt` 内容一致，说明顾问执行过程没有额外修改仓库。

## 总结结论

HomeRail 对 `ansys-agent` 最值得借鉴的不是语音入口、Docker Worker 或通用平台能力，而是它把 DAG runtime 变成可观察、可回放、可干预的 operator 系统这一点。

结合两个顾问意见，`ansys-agent` 当前最该补的不是重新设计运行时，而是把已有的运行记录变成可消费的工程审计产品：

1. 先消费已有事件流，做 `replay` / `trace` / timeline。
2. 再增强 `graph-status`，输出 operator-grade diagnosis。
3. 同步做很薄的 AEDT resource preflight，避免明显环境错误进入慢求解。
4. 运行时干预和 checkpoint resume 暂缓到第二阶段，先不要碰 graph scheduler 的核心语义。

## 顾问共识

两个顾问都认可以下判断：

- `ansys-agent` 已经有较完整的领域契约基础，核心对象包括 `MissionRecord`、`GraphRunRecord`、`NodeRunRecord`、`GraphHandoffRecord`、`ArtifactManifest`、`EvidencePackage`、`CheckpointRecord`、`ApprovalRequest`，位置在 `src/aedt_agent/agent/mission/contracts.py`。
- 真正的短板不是缺少对象定义，而是这些对象尚未形成稳定的消费层。CLI、Web dashboard、graph runner、loop runner 仍在不同位置拼装 JSON 和状态摘要。
- `EventRecord` 和 `EventType` 已经存在，SQLite store 也已经在多处写入事件，但当前 `graph_status()` 主要返回 `graph_run`、`node_runs`、`handoffs`、`jobs`，事件流没有成为 replay / audit / trace 的主数据源。
- `scorecard`、`eval-run`、`replay` 这类命令的产品化价值很高，因为它们能让“是否成功、为什么失败、下一步做什么”不依赖 LLM 自述。
- Docker Worker、voice-first、通用 memory/git/project 平台能力不适合作为近期方向。AEDT/PyAEDT/Windows COM/license 场景更适合保留本地 process harness。
- artifact-only 和 bounded evidence 原则不能削弱。LLM 决策节点只能看确定性 worker 提炼后的指标摘要，不能直接吞 raw Touchstone/TDR 数据。

## 主要分歧

两个顾问对第一优先级排序略有不同：

- agy/Gemini-side 更强调 AEDT resource / capability registry，理由是 license、AEDT 可启动性、路径写权限等问题可以 fail-fast，避免昂贵求解白跑。
- reasonix/DeepSeek-side 更强调事件流消费与 replay CLI，理由是事件数据已经存在，开发成本低、风险小、对调试和审计收益立刻可见。

最终判断：两者都值得做，但第一阶段主线应先选择 **事件流消费与 replay**，因为它不改变执行路径、不碰 scheduler、不影响真实 BRD loop；AEDT resource preflight 可以作为并行小切片加入 ansys-agent preflight，但不应先扩展成复杂的 capability scheduler。

## 最终优先级

### P0：事件流消费与 replay CLI

目标：让已有事件表变成可读、可审计、可回放的时间线。

收益：

- 直接回答“这次 graph run 到底发生了什么”；
- 排查 AEDT solve/export/score/approval 失败时，不再手工拼 `node_runs`、`jobs`、`handoffs`；
- 为后续 Web timeline、trace、eval-run 提供统一数据源；
- 低风险：主要新增读取和渲染逻辑，不改 worker 执行路径。

建议落地：

- 新增 `src/aedt_agent/agent/event_replay.py`，负责把 `EventRecord` 渲染为 text/json timeline。
- 在 `src/aedt_agent/agent/cli.py` 下增加 `mission replay --graph-run-id <id> [--format text|json]`。
- 通过 `graph_run_id` 找到 `mission_id`，调用 `runtime.list_events(mission_id)` 读取事件。
- 优先支持这些事件：graph run created/updated、node run created/updated、job created/leased/succeeded/failed/requeued、approval requested/resolved、handoff created/consumed、artifact manifest created、evidence package created。

### P1：graph-status 增加 diagnosis

目标：把 `graph-status` 从原始 JSON 状态提升为 operator 可读状态摘要。

收益：

- 外层 Codex / Claude Code 可以更快判断是否继续、审批、重试、接管；
- Web dashboard 和 CLI 同时收益；
- 对 `waiting_approval`、`failed`、`running` 的状态解释更稳定。

建议落地：

- 在 `src/aedt_agent/agent/graph_runner.py` 的 `graph_status()` 返回值中追加 `diagnosis` 字段。
- `diagnosis` 只做确定性判断，不调用 LLM。
- 最小字段：
  - `summary`：一句话说明当前状态；
  - `blocking_node_id`：当前卡住的节点；
  - `reason`：审批原因或失败原因；
  - `recommended_action`：下一步建议命令或操作；
  - `risk`：例如 license、timeout、artifact_missing、geometry_invalid。

### P2：薄 AEDT resource preflight

目标：在进入慢求解前发现明显环境问题，但不先做复杂调度平台。

收益：

- AEDT 未安装、license 不可用、profile 路径错误、working project 不可写等问题可 fail-fast；
- 真实 reviewed loop 的错误信息更可解释；
- 对本地和远程 AEDT 工作站都有价值。

建议落地：

- 先挂到 `ansys-agent preflight` 和现有 execution profile 校验路径。
- 检查项保持薄而实用：
  - execution profile 是否存在；
  - `simulation_runner` 是否为预期值；
  - working/report 路径是否存在或可创建；
  - AEDT/PyAEDT 轻量探测是否可用；
  - license 探测如果不稳定，先作为 warning，不作为 hard fail。

### P3：runtime contract 引用收敛

目标：减少 CLI/Web/runner 中对字典字段名的散落硬编码。

收益：

- 后续 status report、replay、diagnosis、UI 共享同一组字段约定；
- 降低字段漂移风险；
- 为跨进程 worker 或远程 manager 化做准备。

建议落地：

- 不急着新建大 protocol 包。
- 先在 `contracts.py` 或新增轻量模块中定义 status report key 常量和小型 builder。
- 优先收敛 `graph_status()` 输出，而不是全仓库大重构。

### P4：checkpoint resume / inject / dynamic intervention

目标：允许运行中局部恢复或人工注入，但应放在第二阶段。

收益：

- 对 AEDT 慢求解有潜在高价值；
- 可减少因 license timeout、COM 崩溃、导出失败导致的整轮重跑。

暂缓原因：

- 真正的 checkpoint resume 会触碰 `ready_nodes()`、handoff 重放、node run 状态语义；
- 当前已有 retry/skip/fallback、approval、takeover、harness recovery 等基础能力；
- 在没有 replay/timeline 的情况下先做干预，反而可能增加不可解释状态。

建议第二阶段再做：

- 先支持只读 replay；
- 再支持显式人工 `resume-from-node`；
- 最后才考虑动态 append node 或运行时 inject。

## 不建议方向

### 不照搬 Docker Worker

HomeRail 的每节点容器适合通用 agent 任务，但 AEDT 依赖 Windows COM、PyAEDT、本机 license 和用户 profile 环境。当前本地 process harness 与 recovery 机制更贴合真实场景。

### 不把 voice-first 作为近期主线

`ansys-agent` 当前最重要的是工程可信度、证据链、状态诊断和失败恢复。语音入口不是瓶颈。

### 不引入通用平台功能

memory、git server、项目管理、通用 generated UI 平台能力都不应进入近期主线。它们会稀释 AEDT/BRD 工程闭环的领域优势。

### 不削弱 artifact-only

Touchstone、TDR 原始曲线、AEDT 项目文件仍应作为 artifact 保存。LLM 只看 bounded summary、scorecard、candidate inventory 和确定性 worker 提供的指标解释。

## 第一阶段最小实施计划

第一阶段建议只做“观察性增强”，不要重写运行时：

1. 新增 `event_replay.py`。
2. 新增 `mission replay --graph-run-id <id>`。
3. 给 `graph_status()` 加 `diagnosis` 字段。
4. 给 `ansys-agent preflight` 加薄 AEDT resource checks。
5. 为 replay 和 diagnosis 增加聚焦测试。

建议测试：

- 构造一个成功 graph run，验证 replay 输出 node/job/handoff/artifact 时间线；
- 构造一个 failed graph run，验证 replay 和 diagnosis 输出失败节点、error code、建议动作；
- 构造一个 waiting approval graph run，验证 diagnosis 能提取 approval reason；
- 构造 example case preflight，验证 resource warning 不破坏 `--no-check-paths` dry run。

## 最终推荐

采用以下近期路线：

```text
事件 replay CLI
  -> graph-status diagnosis
  -> 薄 AEDT resource preflight
  -> status contract 收敛
  -> 第二阶段再做 checkpoint resume / inject
```

这条路线的优点是：收益立刻可见，改动面小，不影响真实 AEDT loop 执行路径，同时把 HomeRail 最有价值的“可观察、可回放、可操作”能力转化为 `ansys-agent` 的工程审计能力。
