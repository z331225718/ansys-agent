# ansys-agent

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
.\.venv\Scripts\python.exe -m pip install -e .
```

复制本机配置：

```powershell
Copy-Item config\execution_profiles\local_real_aedt.example.json config\execution_profiles\local_real_aedt.json
Copy-Item config\optimization_loops\reviewed_brd_remote.example.json config\optimization_loops\reviewed_brd_remote.json
```

检查并修改：

```text
config\execution_profiles\local_real_aedt.json
config\optimization_loops\reviewed_brd_remote.json
```

重点确认：

```text
source_project_path   人工检查过的源 AEDT 模型
working_project_path  loop 复制后反复修改的工作模型
run_root              本次 run 的根目录
report_dir            优化历史和报告输出目录
touchstone_name       channel.s4p
tdr_expression        TDRZt(Diff1)
tdr_observation_port  Diff1
geometry_constraints  anti-pad <= 22mil; NFP radius 7.875-10mil
```

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

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent init `
  --case config\cases\reviewed_brd.example.json

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

详细说明见 `ANSYS_AGENT.md`。

## 手动等价命令

先校验配置，不启动 AEDT：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  mission validate-loop-config `
  --config config\optimization_loops\reviewed_brd_remote.json
```

启动 Web dashboard：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db D:\aedt-agent-runs\reviewed-loop\missions.db `
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
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db D:\aedt-agent-runs\reviewed-loop\missions.db `
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
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db D:\aedt-agent-runs\reviewed-loop\missions.db `
  mission graph-status --graph-run-id <graph_run_id>

.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db D:\aedt-agent-runs\reviewed-loop\missions.db `
  mission graph-visualize --graph-run-id <graph_run_id>

.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db D:\aedt-agent-runs\reviewed-loop\missions.db `
  mission advance-graph --graph-run-id <graph_run_id> --max-workers 1
```

如果 graph 进入 `waiting_approval`，先看 node output 的 `approval_reason`
和 dashboard，再决定 approve/reject。

## 本地验证

轻量验证：

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  src\aedt_agent\agent\loop_runner.py `
  src\aedt_agent\agent\web.py `
  src\aedt_agent\agent\cli.py

.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_loop_runner.py `
  tests\test_agent_cli_dag_runner.py `
  tests\test_agent_web.py `
  -q
```

配置 dry-run：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  mission validate-loop-config `
  --config config\optimization_loops\reviewed_brd_remote.example.json `
  --no-check-paths
```

## 历史 demo

旧 Stage C demo、microstrip smoke、planner benchmark 仍可能存在于代码和历史文档中，
但它们不再是 README 首页主线。当前主线是 reviewed BRD optimization graph loop。
