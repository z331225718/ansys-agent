# ansys-agent

远端 Windows Server 已安装 AEDT 2024 R2、Claude Code 可用且 `pip` 可以联网时，建议直接按
[`docs/remote-windows-server-usage.zh.md`](docs/remote-windows-server-usage.zh.md) 操作。该手册按实际使用顺序
覆盖项目安装、PyAEDT/PyEDB API Memory、复用现有 AEDT 会话、Automation Tab 入口、标准对话、
原生审批、回读、保存、smoke、升级、回滚和完全离线发布包。

面向 AEDT 2024 R2 远端服务器的安装、连接、Desktop 入口、标准对话范例、审批、故障处理和验收步骤见
[`docs/ansys-assistant-operations-guide.zh.md`](docs/ansys-assistant-operations-guide.zh.md)。完整能力、Workflow、
故障排查和维护者说明见
[`docs/ansys-assistant-user-guide.zh.md`](docs/ansys-assistant-user-guide.zh.md)。
远端服务器可联网时按手册第 4.1 节从源码安装；完全离线时按第 4.2～5 节使用 GitHub Release
发布包；上线交接时按第 4.3 节记录 commit、依赖版本、入口来源和 smoke evidence，避免 AEDT 按钮
继续加载旧安装目录。首次使用建议从手册的“十分钟上手”开始；维护者新增写入 Harness 时，还必须执行其中
“新 Harness 的真实 AEDT 准入”，不能只依赖 mock/unit test。

AEDT Automation Tab 的 Claude Code 入口见
[`docs/aedt-desktop-claude-entry.md`](docs/aedt-desktop-claude-entry.md)。
未知能力的 API Memory、受控探索与 Harness 晋升设计见
[`docs/ansys-capability-evolution.md`](docs/ansys-capability-evolution.md)。

3D Layout circle void 与 HFSS 3D 圆柱减金属的严格反焊盘能力见
[`docs/antipad-harness.zh.md`](docs/antipad-harness.zh.md)。

ansys-agent 是面向高速 BRD / AEDT 仿真的工程 agent 系统。当前重点不是旧的
Stage C demo，而是把“脚本式仿真工具”升级为可编排、可审计、可接管的工程
闭环：

```text
User goal
  -> external orchestrator (Claude Code / Codex / other harness)
  -> YAML graph
  -> agent / program / worker / human_gate / scorecard nodes
  -> AEDT artifacts + bounded evidence + optimization report
```

对于没有预定义 Workflow 的临时 Ansys 查询和受控修改，仓库另提供加法式
[`ansys-assistant`](docs/interactive-ansys-assistant.md) 入口。它可以复用正在运行的 AEDT，
查询 HFSS/3D Layout inventory，以 preview、原生审批、apply 和 readback 契约执行受控修改，
并把有序 AEDT design/project variable 原子批量事务、HFSS 相对 Axis/Position 坐标系、typed geometry batch、既有 solid/sheet 的 Global 坐标严格批量平移和绕 Global 原点的 X/Y/Z 轴严格批量旋转、原子 geometry-boundary/port 创建、已有几何上的 typed Wave/Lumped Port、Perfect E/Perfect H/Finite Conductivity/Impedance/Lumped RLC 表面边界、数值型各向同性电磁材料创建、已有工程材料的严格批量更新、未引用 HFSS 工程材料的可回滚批量删除、受控材料批量分配、3D Layout 工程材料原子创建并分配给明确 stackup layer、基于既有 padstack/layer/net 的精确 Via 批量创建、已有 Via 的移动/旋转/改网/锁定批量更新和可重建批量删除、Length Based Mesh、有界 Infinite Sphere 远场设置、原子 setup-sweep 创建、Layout 审计、线宽参数化、组件/trace-edge 端口创建、非阻塞求解、Graph loop 监控、组合式 solve-to-export 闭环和带 SHA256 evidence 的结果导出
提升为严格 live Workflow；现有 YAML Graph 和 BRD Worker 行为保持不变。

## 当前核心原则

不是所有中间节点都是 LLM。

```text
kind: agent      = LLM 推理节点
kind: worker     = 标准化工程执行器，默认不是 LLM
kind: program    = 本进程确定性处理逻辑
kind: human_gate = 人工审批 / 模型检查点
scorecard        = 程序审计层，查 DB 和 artifact，不相信 LLM 自述
```

LLM 负责规划、判断、提案和接管建议；worker 负责标准化执行，例如 AEDT
solve、S4P/TDR 导出、评分、几何校验、模型修改、报告生成。原始 S 参数和
TDR 曲线保持 artifact-only，不直接放进 LLM 上下文。

## 先读这些文档

AEDT 工作站上的 Claude Code 或任何外层编排者启动前，先读：

```text
CLAUDE.md
docs/orchestrator-worker-architecture.zh.md
docs/agent_playbooks/brd-local-cut-optimization.md
docs/remote-reviewed-model-loop.md
docs/agent_templates/brd_reviewed_model_optimize_loop.yaml
```

它们分别说明：

- `CLAUDE.md`：Claude Code 在本项目中如何作为外层编排者。
- `orchestrator-worker-architecture.zh.md`：谁是 LLM、谁是 worker、谁负责审计。
- `brd-local-cut-optimization.md`：BRD local-cut 工程经验、TDR 判断、反焊盘/NFP 规则。
- `remote-reviewed-model-loop.md`：AEDT 工作站怎么配置、校验、开 dashboard、跑 loop。
- `brd_reviewed_model_optimize_loop.yaml`：当前真实 reviewed AEDT 优化闭环图。

## 执行位置和 Runner

默认、大多数真实运行都在 AEDT 工作站本地执行：

```text
config\execution_profiles\local_real_aedt.json
simulation_runner = local_cli
```

这意味着 Claude Code / Codex 可以直接在 AEDT 机器的仓库目录里作为外层编排者，
worker 通过本地 process harness 启动 AEDT/PyAEDT。SSH 默认不启动，也不是主路径。

`ssh_remote` 只用于少数拆分部署：外层编排者不在 AEDT 机器上，而 AEDT/worker 必须
在另一台大内存机器本机执行。除非用户明确要求这种跨机器模式，否则不要使用
`config\execution_profiles\ssh_remote.example.json`。

## 当前真实闭环

主线模板：

```text
docs/agent_templates/brd_reviewed_model_optimize_loop.yaml
```

当前 reviewed BRD loop 的节点边界：

```text
prepare_working_project          program
real_solve_worker                worker  brd.local_cut.solve
touchstone_export_worker         worker  brd.touchstone.export
tdr_export_worker                worker  brd.tdr.export
channel_score_worker             worker  brd.channel.score
iteration_qualifier_worker       worker  brd.iteration.qualify
progress_report_worker           worker  brd.optimization.progress
optimization_decider             agent   LLM high_reasoning
geometry_validator_worker        worker  brd.geometry.validate
model_edit_worker                worker  brd.model.edit
optimization_report              worker  brd.optimization.report
```

也就是说，真实闭环是：

```text
确定性执行 -> 确定性评分/审计 -> LLM 决策 -> 确定性校验 -> 确定性修改 -> 再求解
```

## AEDT 工作站本地快速开始

在 AEDT 工作站：

```powershell
cd D:\ansys-agent
git pull origin main
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

复制本机配置：

```powershell
Copy-Item config\execution_profiles\local_real_aedt.example.json config\execution_profiles\local_real_aedt.json
Copy-Item config\optimization_loops\reviewed_brd_remote.example.json config\optimization_loops\reviewed_brd_remote.json
New-Item -ItemType Directory -Force D:\aedt-agent-runs\reviewed-loop | Out-Null
Copy-Item config\optimization_loops\candidate_action_inventory.example.json `
  D:\aedt-agent-runs\reviewed-loop\candidate_action_inventory.json
```

检查并修改：

```text
config\execution_profiles\local_real_aedt.json
config\optimization_loops\reviewed_brd_remote.json
D:\aedt-agent-runs\reviewed-loop\candidate_action_inventory.json
```

重点确认：

```text
source_project_path   人工检查过的源 AEDT 模型
working_project_path  loop 复制后反复修改的工作模型
run_root              本次 run 的根目录
report_dir            优化历史和报告输出目录
touchstone_name       channel.s4p
tdr_expression        TDRZ(Diff1)
tdr_observation_port  Diff1
geometry_constraints  anti-pad <= 22mil; NFP radius 7.875-10mil
candidate_action_inventory_path  discovery worker 生成/更新的候选事实清单路径
```

`reviewed_brd_remote.json` 不应写死某一层或某个动作。它只通过
`candidate_action_inventory_path` 指向候选事实清单；真实可编辑层、shape id、
padstack instance id 和 via center 由 `candidate_inventory_builder` worker 打开
working AEDB 后自动发现并写入这份 JSON。人工可以提供层范围、signal/ref nets、
TDR 观察口和约束，但不应该逐个手抄 shape id 或 via center。

`candidate_action_inventory.json` 的作用是给 LLM 决策节点提供事实边界，不是让用户
预写完整动作。LLM 会根据 playbook、TDR/RL bounded evidence 和这份清单判断该选择
anti-pad 还是 NFP、哪一层、半径多少；validator/worker 会继续检查它没有引用
inventory 之外的 layer、shape id 或 padstack id。

如果文件里只有层名列表，例如：

```json
{
  "anti_pad_shape_layers": ["L2_GND", "L4_GND"],
  "non_functional_pad_layers": ["L5", "L7"]
}
```

它只会被视为 discovery seed，表示“优先检查这些层”。它不是最终可执行
inventory。`candidate_inventory_builder` 会扫描 AEDB 并补齐
`plane_shape_ids`、`center_padstack_instance_ids`、`bridge_center_padstack_instance_ids`
和 signal-net 证据；后面的 `candidate_action_builder` 如果仍然拿到没有这些事实的
最终 inventory，会直接报 `invalid_candidate_action_inventory`，不会继续跑慢仿真。

示例结构：

```json
{
  "source": "human_reviewed_shape_inventory",
  "tdr_observation_port": "Diff1",
  "tdr_port_orientation_evidence": "reviewed port map: Diff1 starts from <near/far end>",
  "tdr_feature_time": {"value": 32.84, "unit": "ps"},
  "anti_pad_shape_layers": [
    {
      "layer": "<reviewed_shape_backed_layer>",
      "plane_shape_ids": [123456],
      "center_padstack_instance_ids": [501, 502],
      "bridge_center_padstack_instance_ids": [501, 502],
      "parasitic_target": "<ball_or_laser_or_buried_via_parasitic>",
      "target_region": "solder_ball"
    }
  ],
  "non_functional_pad_layers": [
    {
      "layer": "<reviewed_mechanical_hole_layer>",
      "center_padstack_instance_ids": [701, 702],
      "signal_nets": ["<P_NET>", "<N_NET>"],
      "parasitic_target": "<via_barrel_inductance_region>",
      "target_region": "via_barrel"
    }
  ]
}
```

`anti_pad_shape_layers` 可以列任意已检查且 via 附近有 selected shape 的层；
`non_functional_pad_layers` 可以列任意已检查的机械孔/NFP 目标层。清单为空时，
LLM 没有几何事实边界，只能结束或回退到显式 `candidate_actions`。

本地 `local_cli` harness 会自动保留 AEDT/PyAEDT 需要的 Windows 基础环境，
包括 `APPDATA`、`LOCALAPPDATA`、`USERPROFILE`、`TEMP`、`TMP`、`SYSTEMROOT`、
`PATH`、`PATHEXT`。如果 worker 报 `KeyError: 'APPDATA'`，通常说明远端还没
pull 到最新 harness，或本机 profile 过度收窄了环境。

## 让 Claude Code 编排

在 AEDT 工作站的仓库根目录打开 Claude Code：

```powershell
cd D:\ansys-agent
claude
```

给它的任务可以很短：

```text
开始 reviewed BRD 优化闭环。按 CLAUDE.md 执行。
使用 config\optimization_loops\reviewed_brd_remote.json
和 config\execution_profiles\local_real_aedt.json。
先 validate-loop-config，再启动 web dashboard，然后 run-loop。
不要频繁轮询；如果进入 approval 或 failed，先审计 graph-status、
worker artifacts 和 optimization_history.csv，再问我。
```

Claude Code 的职责是外层监督和接管，不是替代 graph 里的 worker。

## 使用内置 ansys-agent

ansys-agent 是项目内置的轻量专属工程编排器，比通用商业 coding agent 更窄：
它只读取 case config、做 preflight、推进 reviewed YAML graph、输出紧凑状态。

先做不启动 AEDT 的 5 分钟验证：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent preflight `
  --case config\cases\reviewed_brd.example.json `
  --no-check-paths

.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent cli `
  --case config\cases\reviewed_brd.example.json `
  --once "看状态"
```

期望结果：preflight 返回 `"status": "passed"`；CLI 输出 `状态：not_started`
并给出下一条建议命令。

真实运行前初始化本机配置：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent init `
  --case config\cases\reviewed_brd.example.json
```

然后编辑：

```text
config\cases\reviewed_brd.local.json
config\optimization_loops\reviewed_brd_remote.local.json
config\execution_profiles\local_real_aedt.local.json
D:\aedt-agent-runs\reviewed-loop\candidate_action_inventory.json
```

确认真实 AEDT 路径、`working_project_path`、`report_dir`、`channel.s4p`、
`TDRZ(Diff1)`、`simulation_runner=local_cli`、几何约束和
`candidate_action_inventory_path` 都正确后，再运行。inventory 文件可以不存在，
也可以只包含少量人工 scope seed；`candidate_inventory_builder` 会打开 working
AEDB 自动写入本轮 reviewed 模型中允许 LLM 选择的 layer、shape id、center
padstack id 和 bridge center pair。不要把某个具体可执行动作写死在 loop config
里。

旧 Windows 控制台建议先设置 UTF-8，避免 dashboard/run-loop 日志输出触发编码错误：

```powershell
$env:PYTHONUTF8 = "1"
```

路径参数请优先在 PowerShell 里运行。不要把下面的 Windows 路径命令原样粘到
Git Bash / bash / zsh：反斜杠会被当成转义符，导致
`D:\aedt-agent-runs\reviewed-loop\missions.db` 变成
`D:aedt-agent-runsreviewed-loopmissions.db`。这会让 web dashboard 和
run-loop 读到不同的 SQLite DB，典型现象是 loop 已经启动但
`http://127.0.0.1:8766/` 没有 mission/node。若必须用 Git Bash，请使用带引号的
正斜杠路径，例如：

```bash
--db 'D:/aedt-agent-runs/reviewed-loop/missions.db'
```

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent preflight `
  --case config\cases\reviewed_brd.local.json

.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent run `
  --case config\cases\reviewed_brd.local.json

.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent status `
  --case config\cases\reviewed_brd.local.json
```

想要更像 agent 的终端体验，可以直接进交互式 CLI：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent cli `
  --case config\cases\reviewed_brd.local.json
```

然后输入“开始优化”“看状态”“批准并继续”“停止”等需求，ansys-agent 会映射到
已有受控命令执行。`chat` 是同一入口的别名。

后续可以用 `resume`、`approve`、`reject`、`stop`、`web` 继续托管同一个 case。
`status` 会给出 `recommended_command`、`available_commands`、pending approvals、
最新 artifact refs、失败摘要和 dashboard URL；它不会把 raw Touchstone/TDR
曲线塞进 JSON，也不会默认建议自动批准审批。
`resume` 遇到未决审批会停在 `waiting_approval`；审完后可以用
`approve --resume --graph-run-id <id>` 明确恢复同一个 graph run。
`web` 会启动 ansys-agent operator panel，用同一组受控命令查看状态、审批、恢复和停止。

首次使用可从 example 复制：

```powershell
Copy-Item config\cases\reviewed_brd.example.json config\cases\reviewed_brd.local.json
```

完整使用和验证方法见 `ANSYS_AGENT.md`。

## 手动等价命令

先校验配置，不启动 AEDT：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  mission validate-loop-config `
  --config config\optimization_loops\reviewed_brd_remote.json
```

启动 Web dashboard：

```powershell
$env:PYTHONUTF8 = "1"
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db $db `
  mission web `
  --host 0.0.0.0 `
  --port 8766 `
  --profile config\execution_profiles\local_real_aedt.json
```

打开：

```text
http://<aedt-machine-ip>:8766
```

启动 reviewed BRD 优化 loop：

```powershell
$env:PYTHONUTF8 = "1"
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db $db `
  mission run-loop `
  --config config\optimization_loops\reviewed_brd_remote.json `
  --profile config\execution_profiles\local_real_aedt.json `
  --worker-id claude-code-orchestrator `
  --max-workers 1
```

`run-loop` 是薄推进器：创建/推进/轮询 graph。它不是隐藏的大脚本大脑。

## 输出

`report_dir` 至少应包含：

```text
optimization_history.csv
optimization_progress.html
optimization_progress.json
```

报告需要说明：

- 每轮修改了哪些结构参数；
- solve / export / score / qualify 是否成功；
- SDD11、SDD21、TDR 指标；
- TDR、SDD11、SDD21 图；
- 当前是否满足目标；
- 下一步建议或停止原因。

## 常用审计命令

```powershell
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db $db `
  mission graph-status --graph-run-id <graph_run_id>

.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db $db `
  mission graph-visualize --graph-run-id <graph_run_id>

.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db $db `
  mission advance-graph --graph-run-id <graph_run_id> --max-workers 1
```

如果 graph 进入 `waiting_approval`，先看 node output 的 `approval_reason`
和 dashboard，再决定 approve/reject。

## 本地验证

轻量验证：

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

配置 dry-run：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  mission validate-loop-config `
  --config config\optimization_loops\reviewed_brd_remote.example.json `
  --no-check-paths
```

ansys-agent example case dry-run：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent preflight `
  --case config\cases\reviewed_brd.example.json `
  --no-check-paths

.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent cli `
  --case config\cases\reviewed_brd.example.json `
  --once "看状态"
```

## 历史 demo

旧 Stage C demo、microstrip smoke、planner benchmark 仍可能存在于代码和历史文档中，
但它们不再是 README 首页主线。当前主线是 reviewed BRD optimization graph loop。
