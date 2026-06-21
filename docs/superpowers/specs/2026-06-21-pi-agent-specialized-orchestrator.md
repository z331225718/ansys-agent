# Pi Agent Specialized Orchestrator Spec

## 背景

ansys-agent 当前已经有 reviewed BRD 优化闭环：

```text
YAML graph -> worker solve/export/score/edit -> bounded evidence -> LLM decider -> report
```

外层可以由 Claude Code、Codex 或其它商业 coding agent 编排。但真实 BRD/AEDT
优化并不需要通用 coding agent 的完整能力。通用 agent 动作空间太大、上下文太重，
容易误用 SSH、绕过 graph、频繁轮询，或把原始 S 参数/TDR 放入上下文。

本 spec 定义一个项目内置的轻量专属 agent：Pi Agent。

## 目标

Pi Agent 是 ansys-agent 的专属工程编排器：

```text
case config
  -> preflight
  -> create/resume reviewed graph
  -> run with local_cli profile
  -> status summary
  -> approval/failure stop
  -> report artifacts
```

它不替代 worker，不自由修改 AEDT，不做通用代码编辑。

## 非目标

- 不实现通用 shell/coding agent。
- 不绕过 YAML graph 直接调用 worker 内部脚本。
- 不默认启动 SSH。
- 不直接解析或塞入完整 S 参数/TDR 曲线给 LLM。
- 不在 MVP 中新增几何优化算法；先复用现有 graph 的 decider/worker。

## 运行模型

默认运行位置是 AEDT 工作站本机：

```text
execution_profile = config\execution_profiles\local_real_aedt.json
simulation_runner = local_cli
```

`ssh_remote` 仅允许在 case config 显式声明 `allow_ssh_remote=true` 且 profile
确实是 `ssh_remote` 时使用。MVP 默认拒绝 SSH。

## Case Config

Pi Agent 只读取一个 case config：

```json
{
  "case_id": "reviewed-brd-s19",
  "db_path": "D:\\aedt-agent-runs\\reviewed-loop\\missions.db",
  "loop_config": "config\\optimization_loops\\reviewed_brd_remote.json",
  "execution_profile": "config\\execution_profiles\\local_real_aedt.json",
  "worker_id": "pi-agent",
  "max_workers": 1,
  "poll_interval_seconds": 30,
  "check_paths": true,
  "allow_ssh_remote": false,
  "dashboard": {
    "host": "0.0.0.0",
    "port": 8766
  }
}
```

`loop_config` 继续是 graph 初始 payload 的权威来源，例如 source AEDT、
working AEDT、report_dir、S4P/TDR 设置、几何约束和候选动作。

## CLI

MVP 新增模块入口：

```powershell
python -m aedt_agent.pi_agent preflight --case config\cases\reviewed_brd.example.json
python -m aedt_agent.pi_agent run --case config\cases\reviewed_brd.example.json
python -m aedt_agent.pi_agent status --case config\cases\reviewed_brd.example.json
```

输出全部为 JSON，方便其它轻量 harness 或 Web UI 使用。

## 状态摘要

`status` 返回 Pi 视角的紧凑状态：

```json
{
  "case_id": "reviewed-brd-s19",
  "status": "running",
  "graph_run_id": "...",
  "mission_id": "...",
  "active_node": "real_solve_worker",
  "latest_round": 1,
  "latest_action": "anti_pad.enlarge",
  "metrics": {
    "sdd11_worst_db": -16.8,
    "sdd21_worst_db": -1.2,
    "tdr_peak_deviation_ohm": 8.7,
    "objective_total_cost": 123.4
  },
  "next_safe_action": "wait",
  "artifacts": {
    "optimization_history_csv": "...",
    "optimization_progress_html": "..."
  }
}
```

如果没有 graph_run_id，`status` 只做文件和配置摘要，不启动任何任务。

## 安全规则

1. 默认拒绝 `ssh_remote` execution profile。
2. `poll_interval_seconds` 必须不小于 graph loop 最小值。
3. `preflight` 必须通过 reviewed loop config 校验。
4. differential contract 必须是四端口 `s4p`、`SDD11`、`SDD21`、`Diff1`。
5. 几何约束必须满足 anti-pad <= 22mil、NFP radius in [7.875mil, 10mil]。
6. `run` 遇到 `waiting_approval`、`failed`、`canceled` 或 `succeeded` 停止。
7. Pi Agent 只输出 bounded metrics 和 artifact refs，不输出 raw 曲线。

## 后续扩展

- Web 页面增加 Pi 视角状态卡片。
- 增加 `resume`、`approve`、`stop` 子命令。
- 增加专属 `PiOptimizationDecider`，只接受 bounded evidence，输出强 schema JSON。
- 将 status 写入 `pi_agent_state.json`，用于外部轻量守护进程。
