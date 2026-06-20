# AEDT Agent Stage C.2 Demo README

## 这个 Demo 展示什么

Stage C Demo 用来展示 AEDT Agent 的产品化闭环。默认首页现在固定演示一个真实 AEDT graphical microstrip S-parameter workflow，避免把 catalog、planner、benchmark 调试入口混在一起：

```text
固定 Microstrip S-Parameter Workflow
        ↓
Workflow JSON
        ↓
Validator
        ↓
Controlled NodeExecutor
        ↓
Real AEDT / Offline Fake Adapter
        ↓
Artifact / Validation / Report
```

它的重点不是完整拖拽 UI，而是让其他团队能直观看到：

- 节点 catalog 已经结构化。
- workflow template 可以复用。
- 首页可以先用 LLM planner 把自然语言需求生成 workflow，再一键启动真实 AEDT graphical workflow。
- Advanced 工作台仍保留 planner、catalog 和 API 调试入口。
- workflow 会先 validation，再执行。
- demo run 会创建空气盒、辐射边界、PEC ground/trace、两个与 trace 等宽的 lumped port，执行 solve，并生成 S 参数报告和 Touchstone artifact。
- 真实 AEDT smoke 结果可以从报告入口查看。
- Stage C.2 增加 planner mode 和 repair loop 展示：主模型只能生成 workflow JSON，后端 validator 决定是否可执行。

## 启动命令

在仓库根目录运行：

```bash
.venv/bin/python scripts/run_stage_c1_demo_server.py --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

默认配置来自：

```text
config/demo_config.example.json
```

本地私有配置可以写到：

```text
config/demo_config.local.json
```

`config/*.local.json` 已被 `.gitignore` 忽略。不要把 API key、base URL 或本机绝对路径写入可提交文件。

## Planner Mode 和本地配置

Demo 默认使用 `deterministic` planner，不需要 LLM API。要测试 LLM planner，可以在本地创建 `config/demo_config.local.json`：

```json
{
  "planner": {
    "mode": "llm",
    "provider": "openai-compatible",
    "model": "",
    "base_url": "",
    "api_key": "",
    "max_repair_attempts": 3
  }
}
```

提交仓库时这些字段必须保持空值。LLM planner 的边界是：

- 只允许输出 workflow JSON。
- 不允许输出 PyAEDT Python。
- 每一轮输出都必须经过 backend validator。
- 如果 validation 失败，会把错误作为 repair context 进入下一轮。

## 页面怎么演示

推荐演示顺序：

1. 打开首页，说明主链路是 `用户需求 -> LLM planner -> workflow validation -> AEDT 执行`。
2. 在“用户需求”里输入微带线需求，例如“求解频率 2.4GHz，扫频到 10GHz”，页面会把频率同步到 workflow 参数。
3. 点击 `Plan with LLM`，展示 planner mode、repair count 和 validator 接受的 workflow JSON。
4. 点击 `Run Real AEDT`，页面会把 planner 生成的 workflow 交给真实 AEDT graphical smoke job，并轮询每个节点的完成状态。
5. 查看结果区的 `Status`、`Validation Result`、S11/S21 和 expected outputs。
6. 打开 artifact 链接，展示每次运行都会落盘：
   - `workflow_run`
   - `validation`
   - `audit`
   - `report`
   - `microstrip_demo.s2p`
7. 点击真实 AEDT smoke 和节点进化 review 链接，展示 Stage C 已跑过的真实 AEDT artifact 和受控节点进化机制。
8. 如需展示 planner、node catalog、API 调试入口，打开：

```text
http://127.0.0.1:8765/advanced
```

## API 示例

查看状态：

```bash
curl -s http://127.0.0.1:8765/api/status
```

查看模板：

```bash
curl -s http://127.0.0.1:8765/api/templates
```

自然语言规划：

```bash
curl -s -X POST http://127.0.0.1:8765/api/plan \
  -H 'content-type: application/json' \
  -d '{"user_request":"create a microstrip s-parameter simulation at 5GHz"}'
```

指定 planner mode：

```bash
curl -s -X POST http://127.0.0.1:8765/api/plan \
  -H 'content-type: application/json' \
  -d '{"planner_mode":"deterministic","user_request":"create a wave port setup"}'
```

启动真实 AEDT demo job：

```bash
curl -s -X POST http://127.0.0.1:8765/api/run-real \
  -H 'content-type: application/json' \
  -d '{"template_id":"microstrip_sparameter"}'
```

查询真实 job 状态：

```bash
curl -s http://127.0.0.1:8765/api/run-real/<job_id>
```

运行 offline fake adapter demo：

```bash
curl -s -X POST http://127.0.0.1:8765/api/run \
  -H 'content-type: application/json' \
  -d '{"template_id":"microstrip_sparameter"}'
```

## 真实 AEDT 与离线模式

浏览器首页默认主按钮会启动真实 AEDT graphical smoke。它依赖本机 AEDT 2026.1、license、桌面环境和 `~/ansys_inc` 安装路径，运行时间明显长于 fake adapter。离线 fake adapter 仍保留为 fallback，因为真实 AEDT：

- 依赖本机安装和 license。
- 启动慢，容易受进程状态影响。
- 需要独立记录 stdout、stderr、workflow_run、validation、audit 和 report artifact。

命令行仍可以直接运行真实 AEDT smoke：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py --template microstrip_sparameter --adapter real
```

如果要强制打开 AEDT GUI：

```bash
.venv/bin/python scripts/run_stage_c_real_workflow_smoke.py --template microstrip_sparameter --adapter real --graphical
```

Stage C 中也已经跑通过 3 个真实 workflow：

- `microstrip_sparameter`
- `wave_port_setup`
- `radiation_airbox_setup`

查看汇总报告：

```text
http://127.0.0.1:8765/reports/stage_c_real_smoke_dashboard.html
```

## Planner Benchmark

运行 5 条内置自然语言请求的小型 planner benchmark：

```bash
.venv/bin/python scripts/run_stage_c2_planner_benchmark.py
```

输出：

```text
benchmarks/reports/stage_c2_planner_benchmark.html
benchmarks/reports/stage_c2_planner_benchmark.json
```

启动 demo server 后也可以直接打开：

```text
http://127.0.0.1:8765/reports/stage_c2_planner_benchmark.html
```

## 当前边界

Stage C.1 不做：

- 完整拖拽节点编辑器。
- 浏览器启动真实 AEDT。
- 多用户任务队列。
- 云部署。
- 电磁物理正确性证明。
- 自动发布 stable 节点。
- 通用优化 agent。

## 验证命令

运行 Stage C.1 相关测试：

```bash
.venv/bin/python -m pytest \
  tests/test_stage_c1_demo_config.py \
  tests/test_stage_c1_demo_service.py \
  tests/test_stage_c1_demo_web.py \
  tests/test_stage_c2_planner.py \
  tests/test_stage_c2_planner_benchmark.py \
  -q
```

运行全量测试：

```bash
.venv/bin/python -m pytest -q
```

## 远端 reviewed BRD 优化闭环用法

这条链路的日常入口不是人工手动逐个 worker 跑命令，而是在 AEDT 远端机器上让 Claude Code 作为外层编排者。Claude Code 读取仓库里的 `AGENTS.md` / `CLAUDE.md`，负责理解任务、选择 `brd_reviewed_model_optimize_loop` 模板、启动 loop、监控 DAG、审计失败、处理审批点，并把每轮的优化历史和报告交给用户。

远端机器准备：

```powershell
cd D:\ansys-agent
git pull
.\.venv\Scripts\python.exe -m pip install -e .
```

复制示例配置并按机器实际路径修改：

```powershell
Copy-Item config\execution_profiles\local_real_aedt.example.json config\execution_profiles\local_real_aedt.json
Copy-Item config\optimization_loops\reviewed_brd_remote.example.json config\optimization_loops\reviewed_brd_remote.json
```

重点检查 `reviewed_brd_remote.json` 里的 `source_project_path`、`working_project_path`、`report_dir`、`max_rounds`、`poll_interval_seconds`、`touchstone_name`、`tdr_expression` 和几何约束。`source_project_path` 指向人工检查过的 AEDT 模型；`working_project_path` 是 loop 复制后反复修改的工作模型，避免每轮生成一堆中间 AEDT 项目。

按成本配置不同 LLM profile：

```powershell
$env:AEDT_AGENT_LLM_API_KEY = "sk-..."
$env:AEDT_AGENT_LLM_BASE_URL = "https://api.openai.com/v1"
$env:AEDT_AGENT_LLM_LOW_COST_MODEL = "gpt-4.1-mini"
$env:AEDT_AGENT_LLM_STANDARD_MODEL = "gpt-4.1-mini"
$env:AEDT_AGENT_LLM_HIGH_REASONING_MODEL = "gpt-4.1"
```

然后在 `D:\ansys-agent` 打开 Claude Code，直接用自然语言下达任务，例如：

```text
开始 reviewed BRD 优化闭环。使用 config\optimization_loops\reviewed_brd_remote.json
和 config\execution_profiles\local_real_aedt.json。先启动 web dashboard，再启动 run-loop。
轮询不要太频繁，按配置 30 秒一次；如果失败，先审计 graph-status、worker 输出和报告 artifact。
每轮向我汇报 optimization_history.csv 的关键指标、修改了哪些结构、下一步建议。
```

Claude Code 应执行的等价命令如下。先开可视化页面：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db D:\aedt-agent-runs\reviewed-loop\missions.db `
  mission web `
  --host 0.0.0.0 `
  --port 8766 `
  --profile config\execution_profiles\local_real_aedt.json
```

浏览器打开：

```text
http://<aedt-machine-ip>:8766
```

再启动闭环：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db D:\aedt-agent-runs\reviewed-loop\missions.db `
  mission run-loop `
  --config config\optimization_loops\reviewed_brd_remote.json `
  --profile config\execution_profiles\local_real_aedt.json `
  --worker-id claude-code-orchestrator `
  --max-workers 1
```

loop 输出目录由 `report_dir` 控制，至少应包含：

```text
optimization_history.csv
optimization_progress.html
optimization_progress.json
```

报告需要写清楚每轮修改、最终结果、是否满足 TDR / SDD11 / SDD21 指标，并保留 TDR、SDD11、SDD21 图。原始 S 参数和 TDR 曲线保持 artifact-only，不直接塞进 LLM 上下文。
