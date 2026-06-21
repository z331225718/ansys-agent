# ansys-agent Pi Agent

Pi Agent 是 ansys-agent 内置的轻量专属工程编排器。它不是通用 coding agent，
也不是 Claude Code/Codex 的包装层。它只负责 reviewed BRD/AEDT 优化闭环的
受控推进：

```text
case config -> preflight -> graph run-loop -> status summary -> approval/report
```

## 边界

Pi Agent 可以：

- 读取一个 case config；
- 校验 reviewed loop config 和 execution profile；
- 使用 `local_cli` 在 AEDT 工作站本机推进 YAML graph；
- 输出紧凑 JSON 状态；
- 在 `waiting_approval`、`failed`、`canceled`、`succeeded` 停止。

Pi Agent 不可以：

- 默认启动 SSH；
- 绕过 YAML graph 直接调用 worker 内部脚本；
- 自由修改 AEDT；
- 把 raw S 参数或完整 TDR 曲线放进 LLM 上下文；
- 忽略反焊盘/NFP 几何约束。

## 默认运行方式

先复制并编辑真实本机配置：

```powershell
Copy-Item config\execution_profiles\local_real_aedt.example.json config\execution_profiles\local_real_aedt.json
Copy-Item config\optimization_loops\reviewed_brd_remote.example.json config\optimization_loops\reviewed_brd_remote.json
Copy-Item config\cases\reviewed_brd.example.json config\cases\reviewed_brd.local.json
```

把 `config\cases\reviewed_brd.local.json` 里的路径改成非 example 文件：

```json
{
  "loop_config": "config\\optimization_loops\\reviewed_brd_remote.json",
  "execution_profile": "config\\execution_profiles\\local_real_aedt.json"
}
```

然后运行：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent init `
  --case config\cases\reviewed_brd.example.json

.\.venv\Scripts\python.exe -m aedt_agent.pi_agent preflight `
  --case config\cases\reviewed_brd.local.json

.\.venv\Scripts\python.exe -m aedt_agent.pi_agent run `
  --case config\cases\reviewed_brd.local.json

.\.venv\Scripts\python.exe -m aedt_agent.pi_agent status `
  --case config\cases\reviewed_brd.local.json
```

如果只想验证仓库里的 example contract，不检查本机 AEDT 路径：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent preflight `
  --case config\cases\reviewed_brd.example.json `
  --no-check-paths
```

## Case Config

最小字段：

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
  "allow_ssh_remote": false
}
```

MVP 固定 `max_workers=1`，避免多个 AEDT worker 同时争用模型和 license。

## 状态输出

`status` 输出 JSON，面向轻量 UI 或其它小 harness：

```json
{
  "status": "waiting_approval",
  "active_node": "action_approval_gate",
  "latest_round": "1",
  "latest_action": "anti_pad.enlarge",
  "metrics": {
    "rl_worst_db": "-16.8",
    "tdr_observation_port": "Diff1",
    "tdr_peak_deviation_ohm": "8.7",
    "objective_total_cost": "123.4"
  },
  "next_safe_action": "ask_user"
}
```

只汇报 bounded metrics 和 artifact refs；raw Touchstone/TDR 仍然是 artifact-only。

## 控制命令

Phase 2 起 Pi Agent 还提供受控操作命令：

```powershell
# 继续一个已存在或最新 graph_run
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent resume `
  --case config\cases\reviewed_brd.local.json

# 批准 graph/human gate 审批项
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent approve `
  --case config\cases\reviewed_brd.local.json `
  --approval-id <approval_id> `
  --option-id approve

# 拒绝审批项
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent reject `
  --case config\cases\reviewed_brd.local.json `
  --approval-id <approval_id>

# 停止当前或指定 graph_run
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent stop `
  --case config\cases\reviewed_brd.local.json `
  --reason "manual stop"

# 启动现有 ansys-agent dashboard
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent web `
  --case config\cases\reviewed_brd.local.json
```

`resume` 和 `web` 会执行同一套 execution profile 安全检查：默认只允许
`local_cli`，除非 case 明确设置 `allow_ssh_remote=true`。
