# ansys-agent

ansys-agent 是本项目内置的轻量专属工程编排器。它不是通用 coding agent，
也不是 Claude Code/Codex 的包装层。它只负责 reviewed BRD/AEDT 优化闭环的
受控推进：

```text
case config -> preflight -> graph run-loop -> status summary -> approval/report
```

## 边界

ansys-agent 可以：

- 读取一个 case config；
- 校验 reviewed loop config 和 execution profile；
- 使用 `local_cli` 在 AEDT 工作站本机推进 YAML graph；
- 输出紧凑 JSON 状态；
- 在 `waiting_approval`、`failed`、`canceled`、`succeeded` 停止。

ansys-agent 不可以：

- 默认启动 SSH；
- 绕过 YAML graph 直接调用 worker 内部脚本；
- 自由修改 AEDT；
- 把 raw S 参数或完整 TDR 曲线放进 LLM 上下文；
- 忽略反焊盘/NFP 几何约束。

## 5 分钟验证

这组命令只验证仓库、配置 contract、CLI 入口和单元测试，不启动 AEDT，也不修改真实模型。
适合远端机器 `git pull` 后先确认环境是好的。

```powershell
cd D:\ansys-agent
.\.venv\Scripts\python.exe -m pip install -e .
```

验证 example case 的 contract。这里用 `--no-check-paths`，所以不会要求本机真实存在
`D:\aedt-agent-runs\...` 里的 AEDT 文件：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent preflight `
  --case config\cases\reviewed_brd.example.json `
  --no-check-paths
```

期望结果：命令退出码为 `0`，JSON 里 `"status": "passed"`。

验证交互式 CLI 的自然语言入口：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent cli `
  --case config\cases\reviewed_brd.example.json `
  --once "看状态"
```

期望结果：输出 `状态：not_started`，并给出下一条建议命令。旧兼容入口也可以验证，
但只用于旧脚本过渡：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.pi_agent cli `
  --case config\cases\reviewed_brd.example.json `
  --once "看状态"
```

验证 Python 入口和核心 ansys-agent 回归：

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  src\aedt_agent\ansys_agent\__main__.py `
  src\aedt_agent\ansys_agent\case_config.py `
  src\aedt_agent\ansys_agent\supervisor.py `
  src\aedt_agent\ansys_agent\status.py `
  src\aedt_agent\ansys_agent\chat.py `
  src\aedt_agent\ansys_agent\web.py

.\.venv\Scripts\python.exe -m pytest `
  tests\test_ansys_agent_case_config.py `
  tests\test_ansys_agent_cli.py `
  tests\test_ansys_agent_chat.py `
  tests\test_ansys_agent_controls.py `
  tests\test_ansys_agent_status.py `
  tests\test_ansys_agent_web.py `
  tests\test_agent_loop_runner.py `
  tests\test_agent_cli_dag_runner.py `
  -q
```

期望结果：`py_compile` 无输出且退出码为 `0`；pytest 全部通过。

## 真实使用流程

真实使用分四步：

```text
初始化本机配置 -> 修改真实路径 -> preflight -> cli/run/web 托管闭环
```

### 1. 初始化本机配置

先复制并编辑真实本机配置：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent init `
  --case config\cases\reviewed_brd.example.json
```

这个命令会生成或保留以下本机文件：

```text
config\cases\reviewed_brd.local.json
config\optimization_loops\reviewed_brd_remote.local.json
config\execution_profiles\local_real_aedt.local.json
```

也可以手动复制，效果等价：

```powershell
Copy-Item config\cases\reviewed_brd.example.json config\cases\reviewed_brd.local.json
Copy-Item config\optimization_loops\reviewed_brd_remote.example.json config\optimization_loops\reviewed_brd_remote.local.json
Copy-Item config\execution_profiles\local_real_aedt.example.json config\execution_profiles\local_real_aedt.local.json
```

### 2. 修改真实路径

编辑 `config\cases\reviewed_brd.local.json`，确认它指向 local 文件：

```json
{
  "loop_config": "config\\optimization_loops\\reviewed_brd_remote.local.json",
  "execution_profile": "config\\execution_profiles\\local_real_aedt.local.json",
  "worker_id": "ansys-agent",
  "max_workers": 1,
  "poll_interval_seconds": 30,
  "allow_ssh_remote": false
}
```

编辑 `config\optimization_loops\reviewed_brd_remote.local.json`，至少确认：

```text
run_root              本次运行目录，例如 D:\aedt-agent-runs\reviewed-loop
source_project_path   人工检查过、可以仿真的源 AEDT 模型
working_project_path  ansys-agent 复制后反复修改的工作模型
report_dir            optimization_history.csv 和报告输出目录
setup_name            AEDT setup 名称
sweep_name            AEDT sweep 名称
touchstone_name       channel.s4p
tdr_expression        TDRZt(Diff1)
tdr_observation_port  Diff1
expected_port_count   4
geometry_constraints  anti-pad <= 22mil; NFP radius 7.875-10mil
```

编辑 `config\execution_profiles\local_real_aedt.local.json`，默认保持：

```json
{
  "simulation_runner": "local_cli",
  "allow_real_aedt": true,
  "max_concurrent_aedt": 1,
  "max_concurrent_license_jobs": 1
}
```

默认不启动 SSH。只有明确拆分部署时，才把 case 里的 `allow_ssh_remote` 改成
`true` 并使用 `ssh_remote` profile。

### 3. Preflight

真实路径填好后运行：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent preflight `
  --case config\cases\reviewed_brd.local.json
```

期望结果：JSON 里 `"status": "passed"`。如果失败，先看 `failed_checks`：

```text
profile_local_cli              默认只接受 local_cli，除非 allow_ssh_remote=true
profile_real_aedt_enabled      真实 reviewed loop 必须 allow_real_aedt=true
case_max_workers_one           当前 AEDT 工作模型只允许 max_workers=1
touchstone_is_s4p              差分通道必须是 s4p
tdr_diff1                      TDR 观察端口默认 Diff1
```

### 4. 运行闭环

推荐先用交互式 CLI：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent cli `
  --case config\cases\reviewed_brd.local.json
```

进入后输入：

```text
开始优化
看状态
批准并继续
继续
停止
打开页面
退出
```

也可以直接跑一次受控命令：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent preflight `
  --case config\cases\reviewed_brd.local.json

.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent run `
  --case config\cases\reviewed_brd.local.json

.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent status `
  --case config\cases\reviewed_brd.local.json
```

状态页面：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent web `
  --case config\cases\reviewed_brd.local.json
```

打开 case 中配置的 dashboard 地址，默认类似：

```text
http://localhost:8766
```

`chat` 和 `cli` 是同一个入口；`--once` 可用于脚本化验证：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent cli `
  --case config\cases\reviewed_brd.local.json `
  --once "看状态"
```

## 运行中如何判断是否正常

`status` 或 operator panel 里重点看：

```text
status                 running / waiting_approval / failed / succeeded / canceled
active_node            当前 graph 节点
latest_round           当前优化轮次
latest_action          最近一次几何动作
metrics                bounded SDD11 / SDD21 / TDR / objective 指标
pending_approvals      是否需要人工审批
latest_artifacts       输出文件是否存在
failure                失败摘要
recommended_command    下一条安全命令
available_commands     可人工选择的控制命令
```

轮询不需要很频繁。真实 AEDT solve 很慢，建议按 `poll_interval_seconds=30`
或更低频率查看。进入 `waiting_approval` 时，ansys-agent 不会自动批准；需要人工
检查 worker evidence、geometry proposal、模型和 artifact 后，再选择 approve/reject。

## 验收标准

一次真实闭环至少应产生：

```text
working_project_path 对应的工作 AEDT 模型
report_dir\optimization_history.csv
report_dir\optimization_progress.json
report_dir\optimization_progress.html
```

最终报告必须说明每轮修改了什么、结果变好还是变差、最终 bounded 指标，以及
TDR、SDD11、SDD21 图。raw Touchstone/TDR 只作为 artifact ref 出现，不进入 LLM
上下文。

## Case Config

最小字段：

```json
{
  "case_id": "reviewed-brd-s19",
  "db_path": "D:\\aedt-agent-runs\\reviewed-loop\\missions.db",
  "loop_config": "config\\optimization_loops\\reviewed_brd_remote.json",
  "execution_profile": "config\\execution_profiles\\local_real_aedt.json",
  "worker_id": "ansys-agent",
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
  "next_safe_action": "ask_user",
  "recommended_command": "python -m aedt_agent.ansys_agent status --case config\\cases\\reviewed_brd.local.json",
  "available_commands": {
    "approve": "python -m aedt_agent.ansys_agent approve --case config\\cases\\reviewed_brd.local.json --approval-id <approval_id> --option-id approve",
    "approve_and_resume": "python -m aedt_agent.ansys_agent approve --case config\\cases\\reviewed_brd.local.json --approval-id <approval_id> --option-id approve --resume --graph-run-id <graph_run_id>",
    "reject": "python -m aedt_agent.ansys_agent reject --case config\\cases\\reviewed_brd.local.json --approval-id <approval_id>",
    "status": "python -m aedt_agent.ansys_agent status --case config\\cases\\reviewed_brd.local.json",
    "web": "python -m aedt_agent.ansys_agent web --case config\\cases\\reviewed_brd.local.json"
  },
  "pending_approvals": [
    {
      "approval_id": "<approval_id>",
      "reason": "geometry action needs review"
    }
  ],
  "latest_artifacts": [
    {
      "path": "D:\\aedt-agent-runs\\reviewed-loop\\optimization_history.csv",
      "kind": "history_csv",
      "exists": true
    }
  ],
  "dashboard_url": "http://localhost:8766",
  "failure": {}
}
```

`recommended_command` 不会自动建议批准审批；批准和拒绝只放在
`available_commands` 里，由人或上层审计明确选择。状态只汇报 bounded
metrics 和 artifact refs；raw Touchstone/TDR 仍然是 artifact-only。

## 控制命令

Phase 2 起 ansys-agent 还提供受控操作命令：

```powershell
# 继续一个已存在或最新 graph_run
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent resume `
  --case config\cases\reviewed_brd.local.json

# 批准 graph/human gate 审批项
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent approve `
  --case config\cases\reviewed_brd.local.json `
  --approval-id <approval_id> `
  --option-id approve

# 批准后明确恢复同一个 graph_run
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent approve `
  --case config\cases\reviewed_brd.local.json `
  --approval-id <approval_id> `
  --option-id approve `
  --resume `
  --graph-run-id <graph_run_id>

# 拒绝审批项
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent reject `
  --case config\cases\reviewed_brd.local.json `
  --approval-id <approval_id>

# 停止当前或指定 graph_run
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent stop `
  --case config\cases\reviewed_brd.local.json `
  --reason "manual stop"

# 启动 ansys-agent operator panel
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent web `
  --case config\cases\reviewed_brd.local.json
```

`resume` 和 `web` 会执行同一套 execution profile 安全检查：默认只允许
`local_cli`，除非 case 明确设置 `allow_ssh_remote=true`。
如果 graph 仍有 pending approval，`resume` 会返回 `waiting_approval` 和可选命令，
不会重新启动 worker；需要先 `approve` 或 `reject`。

`web` 会启动一个轻量 operator panel，默认按 case 的 `poll_interval_seconds`
低频刷新。页面只通过 ansys-agent 的受控命令操作 graph：查看状态、批准并恢复、
拒绝、恢复、停止；页面展示 bounded metrics、pending approvals、latest artifact
refs 和 failure summary，不读取 raw Touchstone/TDR 内容。

交互式 `cli/chat` 也是同样的安全边界：它只把自然语言映射到 ansys-agent
已有受控命令，不直接调用 worker 内部脚本，不绕过 approval gate。
