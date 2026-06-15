# Pi 集成评估门

## 当前结论

Pi 的状态是 **deferred（延后评估）**，不是 rejected（拒绝采用）。

当前 Python Runtime 已拥有 Mission、Job、YAML DAG、审批、证据、有限循环和 Process Harness。Pi 只有在不破坏这些稳定边界，并能带来可测量收益时才进入产品依赖。

## 允许的接入边界

```text
Pi session / model runtime
    -> Mission API
    -> Event stream
    -> Approval API
    -> Worker capability API
    -> Python Agent Runtime
    -> Process Harness
    -> AEDT / deterministic evaluator
```

Pi 可以负责：

- 模型 provider 与 session 生命周期；
- thinking/tool-call streaming；
- 前端与审批交互；
- 扩展发现和工具描述；
- 面向用户的上下文压缩。

Pi 不得负责：

- Mission、Job、GraphRun 或 Approval 的权威持久化；
- AEDT 进程启动、终止、超时和资源门控；
- 数值 pass/fail；
- 工程动作审批策略；
- checkpoint、rollback 和 artifact 完整性；
- 许可证并发；
- Python Worker 内部导入。

Mission/Job/Event 契约中不得出现 Pi 专有类型。

## 启动 PoC 的前置条件

只有同时满足以下条件才启动 Pi PoC：

1. native Python CLI 能独立完成 BRD build、solve evidence、受控动作和 bounded loop。
2. Process Harness 能在 timeout、cancel 和父进程重启后留下可恢复记录。
3. 至少一次真实 AEDT 场景通过 Worker/Event/Approval 契约。
4. YAML DAG 模板和 Scorecard 已能稳定复现相同流程。
5. Pi 可以只通过公开 API 接入，不修改 Python Runtime 的核心契约。

## PoC 对照组

使用同一组任务、模型、预算和真实/recorded Worker：

- A 组：native Python orchestrator + 当前模型 adapter。
- B 组：Pi session/runtime + 相同 Python Mission API。

至少执行：

- 10 个 recorded BRD Mission；
- 3 个 timeout/recovery 故障注入；
- 3 个 approval wait/resume；
- 1 个 opt-in 真实 AEDT Mission。

## 量化指标

### 必须全部满足

| 指标 | 通过阈值 |
| --- | --- |
| Mission 正确率 | B 组不得低于 A 组 |
| 审批与恢复语义 | 100% 保持相同 Mission/GraphRun/Approval ID |
| artifact 与事件完整性 | B 组不得丢失 A 组已有记录 |
| timeout 后残进程 | 0 |
| Python Runtime 独立运行 | 移除 Pi 后仍可完成全部 A 组任务 |
| 专有类型泄漏 | Mission/Job/Event/Worker 公共契约中为 0 |

### 至少满足一项

| 收益指标 | 通过阈值 |
| --- | --- |
| 模型/session 编排代码 | Python 自维护代码减少至少 25% |
| streaming/approval 交互 | 用户可见首事件延迟降低至少 30% |
| provider 接入 | 新增一个 provider 的适配代码减少至少 40% |
| 扩展维护 | 至少两个工具扩展不修改核心 Runtime 即可安装 |
| 长任务监控成本 | orchestrator 轮询/token 开销降低至少 30% |

### 成本上限

出现任一情况则不采用：

- 部署必须常驻额外服务且无法单进程开发；
- Python/TypeScript 跨运行时故障无法通过统一 trace ID 关联；
- Pi session 中断会导致 Mission 状态丢失；
- Project Trust 与 Process Harness 权限发生冲突或重复授权；
- 真实 AEDT 场景恢复时间增加超过 20%；
- 维护者需要 fork Pi 才能保持核心功能。

## 决策输出

PoC 必须产生：

- `pi-poc-report.json`：原始指标和每个任务结果；
- `pi-poc-report.md`：中文结论；
- 架构依赖图；
- 部署与故障恢复步骤；
- adopt / defer / reject 三选一决策；
- 若 adopt，列出最小接入范围和退出方案。

没有量化报告时，不得因为演示效果或“使用可信框架”把 Pi 加入核心依赖。
