# HomeRail runtime lessons for ansys-agent

> 最新复审与实施状态见：[HomeRail 2026-07 最终复审与实施结论](homerail-2026-07-reassessment-and-implementation.md)。

本文记录从 `xiaotianfotos/homerail` 仓库中可借鉴到 `ansys-agent` 的设计点，并说明每项对本项目的具体收益。这里关注的是 DAG Agent 系统的运行时、控制面和可审计性，不是照搬 HomeRail 的语音优先产品形态或 Docker Worker 部署方式。

## 背景

`ansys-agent` 当前主线是高速 BRD / AEDT 工程闭环：

```text
User goal
  -> external orchestrator
  -> YAML graph
  -> agent / worker / program / human_gate / scorecard nodes
  -> AEDT artifacts + bounded evidence + optimization report
```

项目已经具备 graph run、node run、handoff、approval、artifact、evidence package、worker attempt、scorecard 等核心对象。HomeRail 的启发在于：它把这些对象进一步产品化成稳定协议、事件流、运行时控制面和 operator 体验。

## 借鉴项与收益

### 1. 统一运行时协议层

HomeRail 将 Manager、Node、Worker、UI 之间的消息类型集中在独立 protocol 包中。`ansys-agent` 可以借鉴为一个更清晰的 `runtime_contracts` 层，覆盖：

- graph run / node run / worker attempt 状态；
- handoff payload 与 artifact ref；
- approval request / decision；
- scorecard / evidence package；
- runtime event / error class。

收益：

- 降低 CLI、Web dashboard、mission store、worker harness 之间的字段漂移；
- 让 graph-status、operator UI、远程 worker 可以复用同一份契约；
- 为后续 checkpoint resume、运行中注入、跨进程 worker 打基础；
- 让测试从“具体实现测试”更多转向“契约兼容性测试”。

### 2. DAG 运行时事件流

HomeRail 将 run events、handoffs、chats、metrics、replay 作为一等对象。`ansys-agent` 已有 `EventRecord` 和多类 mission/graph 事件，但还可以提升为标准 timeline：

- node started / completed / failed / skipped；
- handoff created / consumed；
- approval requested / resolved；
- artifact manifest created；
- evidence package created；
- scorecard gate passed / failed；
- worker attempt started / recovered / requeued。

收益：

- 长时间 AEDT 求解不再只是“当前状态”，而是可解释的完整过程；
- 失败后能快速定位是资源问题、模型问题、几何校验问题还是 scorecard gate；
- replay / audit report 可以直接从事件流生成，不必反向拼多个表；
- dashboard 能展示面向工程用户的时间线，而不是暴露底层 DB 结构。

### 3. 运行时干预能力

HomeRail 支持 cancel、inject、checkpoint resume、dynamic node append 等控制面。`ansys-agent` 已有 approval、takeover 和 graph advance 语义，可以逐步增强为：

- 对等待或运行中的节点注入人工说明；
- 从某个 checkpoint 或 artifact 状态恢复；
- 对失败节点按策略重试、跳过或改路由；
- 追加一次性审计、修复、导出或报告节点；
- 将 takeover 变成有记录的 graph transition，而不是外部手工操作。

收益：

- AEDT 慢任务失败后不必整轮重跑；
- 人工工程判断可以进入审计链，而不是散落在聊天记录里；
- reviewed BRD loop 遇到 geometry ambiguity、license timeout、导出缺失时更容易局部恢复；
- 外层 Codex / Claude Code 的角色更像 operator，而不是只能重启脚本。

### 4. 资源状态与 worker capability registry

HomeRail 的 Manager 能知道 Node / Worker 是否在线、能力是什么、资源是否可用。`ansys-agent` 可做 AEDT 专用版本：

- AEDT / PyAEDT 可启动性；
- AEDT license 状态；
- working project 锁和路径可写性；
- Touchstone / TDR export 能力；
- local_cli / ssh_remote profile 可用性；
- worker capability 到真实执行环境的映射。

收益：

- graph run 前即可失败前置检查，减少跑到中途才发现环境缺失；
- scheduler 可以避免把真实 solve 派给没有 AEDT 能力的 worker；
- dashboard 能明确告诉用户“为什么不能继续”，而不是只给 generic failed；
- 对远程 AEDT 工作站模式更关键，可以避免 Manager 和 worker 状态不一致。

### 5. Scorecard / eval-run / replay 的 CLI 产品化

HomeRail 将 `scorecard`、`eval-run`、`replay` 做成明确命令。`ansys-agent` 已有 scorecard 和 artifact query，但可以进一步形成固定操作入口：

- `scorecard <graph_run_id>`：给出是否满足工程 gate；
- `eval-run <graph_run_id>`：给出质量结论、风险、下一步；
- `replay <graph_run_id>`：重放 node/handoff/artifact timeline；
- `trace <artifact_or_metric>`：解释某个指标来自哪个 worker、哪个文件、哪个 gate。

收益：

- 让“是否成功”不依赖 LLM 自述；
- 工程用户可以审计 S 参数、TDR、几何约束、修改动作之间的因果链；
- 回归测试和真实运行的输出形态更一致；
- 便于积累失败案例，反哺 node catalog 和 playbook。

### 6. 本地密钥和模型配置管理

HomeRail 使用本地加密 secret store 保存 provider credentials，YAML/profile 只引用配置标识。`ansys-agent` 后续如果支持更多 LLM provider 或远程 worker，也应避免将 API key 写入 graph template、case config 或 execution profile。

收益：

- 减少密钥泄漏到 git、artifact、dashboard 的风险；
- 支持不同 agent 节点使用不同 provider/model，而不污染工程配置；
- 便于切换本地/远程/离线 deterministic profile；
- 更适合多人或多工作站部署。

### 7. Operator UI 信息架构

HomeRail 的 UI 重点是 session/run list、DAG overlay、node detail、evidence、settings，而不是纯聊天。`ansys-agent` dashboard 可以借鉴这种布局：

- 左侧：case / mission / graph run 列表；
- 中间：当前 run 时间线、推荐动作、approval；
- 右侧：DAG、node detail、artifact/evidence、scorecard；
- 顶部：资源状态、profile、AEDT/license 状态。

收益：

- 工程用户能一眼判断“现在卡在哪里”和“下一步该做什么”；
- approval 不再是隐藏在 JSON 中的状态；
- artifact-only 原则更容易贯彻，图表和摘要在 UI 中展示，raw S 参数仍留在文件中；
- 更适合长时间无人值守优化 loop。

## 不建议照搬

- 不建议照搬“每个节点一个 Docker Worker”。AEDT / PyAEDT / Windows COM / license 场景太重，当前 local process harness 更合适。
- 不建议把 voice-first 作为近期主线。`ansys-agent` 当前瓶颈是工程可信度、证据链、审批和恢复能力，不是输入方式。
- 不建议引入过宽的平台功能，如通用 memory、git server、项目管理、generated UI 平台化能力。
- 不应削弱本项目的领域边界：artifact-only、TDR/S 参数 bounded evidence、几何约束、candidate action inventory、确定性 worker 仍然是核心优势。

## 建议优先级

1. 统一 runtime contract：先稳定 event / handoff / artifact / approval / scorecard schema。
2. 标准化 event timeline 和 replay：让每个 graph run 可回放、可解释。
3. 增强 graph-status：输出 operator-grade 状态摘要、失败原因、建议动作。
4. 增加运行时干预：inject、checkpoint resume、局部 retry/skip/takeover。
5. 建立 AEDT resource/capability registry：把环境可用性变成调度前置条件。

## 最小可行实现

第一阶段不要重构整个运行时。建议先做一个薄的 `runtime_events` 和 `runtime_contracts` 增量层：

- 保留现有 SQLite store 和 graph runner；
- 定义版本化事件 schema；
- 在已有 create/update/complete/handoff/approval/artifact 位置补事件；
- 新增 `mission replay` 或 `graph replay` CLI，从事件流生成文本报告；
- 将 `graph-status` 摘要改为读取同一批契约对象。

这样可以先获得审计、回放、UI 数据源和测试收益，同时避免大规模改动真实 BRD loop。
