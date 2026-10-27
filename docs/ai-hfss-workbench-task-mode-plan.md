# AI-HFSS：工作台任务化 + AEDT 内嵌轻对话

状态：**P0 ✅ / P1 ✅ / P2 ✅ / P3 ✅ / P4 ✅（本轮交付完成）**（P0 见 [docs/spikes/ai-hfss-p0.md](spikes/ai-hfss-p0.md)）

P1 已交付：`workbench_tasks.py`（工作区/任务/每任务库/锁/自动驾驶）、`/api/workbench/*` 新接口、
`tests/test_agent_workbench_tasks.py`。
真机验证：E:\ai-hfss-test 建工作区 → 建任务 → 推进 → AEDT 1 GHz 单点求解 38.3 s →
`channel_1ghz.s1p`(1347 B) 回读，节点 succeeded（一次求解）。

P3 已交付：focus 页（无侧栏 + AEDT 状态条）、`/api/ai-hfss/aedt/*`、
`ansys-embedded` 预设、AEDT 脚本 `integrations/aedt/ai_hfss_chat.py`。

P2 已交付：左侧「工作区 / 优化任务」面板（新建工作区 = 选目录）、新建任务表单
（goal/源工程/模板/Setup/频率/轮次）、任务状态条（刷新 / 推进 / 自动 / 批准 / 拒绝），
并复用 applySnapshot 把指标、轮次、约束、产物切到该任务真实数据。
浏览器验收：Playwright 无 JS 错误；建工作区 → 建任务 → 状态条 `created · graph running · step 0/64`；
真实工程复验：工作区 `E:/ai-hfss-test` → 任务 `1GHz single-point smoke` →
`任务 succeeded · graph succeeded · step 1/64`，徽标 LIVE。
日期：2026-10-28
相关代码：`src/aedt_agent/agent/web.py`、`src/aedt_agent/agent/workbench.py`、
`src/aedt_agent/agent/cli.py`、`src/aedt_agent/infrastructure/sqlite_mission_store.py`、
`integrations/si-workbench/`、`integrations/dsh/bundles/ai-hfss-workstation/`

---

## 0. 已确认的决策

| # | 决策 | 结论 |
|---|------|------|
| 1 | 每任务 DB 位置 | **由用户在工作台里指定**：左侧新增"工作区"（= 一个工程，指定目录）；任务库落在该目录下 |
| 2 | AEDT 入口形态 | **脚本按钮**（不做 .NET add-in 内嵌面板） |
| 3 | "只有对话"页面 | 同一个 DSH 应用内的 **focus 模式**（收起侧栏），**打开即默认连上当前 AEDT 工程** |
| 4 | 工作台权限 | 工作台接管推进与审批（从只读变为唯一控制面） |

---

## 1. 现状（改造前）

```text
mission create / advance-graph / run-loop   ← CLI，单 DB（--db）
        │
        ▼
SQLiteMissionStore(missions.db)  ← 一个进程一个库
        │
        ├── mission web 首页（/api/missions、advance、approvals…）
        └── /workbench（只读投影：/api/workbench/missions、/api/workbench/graphs/<id>）
```

现有约束（本节是改造的根据）：

1. `run_agent_window(db_path)` 构造**一个** `AgentRuntime` → 一个 `SQLiteMissionStore`，
   所有路由都用这个单例（`web.py:1084`、`workbench.py:26/34`）。
2. 工作台是**只读**的：`workbench_snapshot` 明确回报
   `capabilities.workflow_start/advance/human_gate_approve = false`。
3. DSH 插件那侧（`integration/dsh/bundles/ai-hfss-workstation`）现在只有一个
   面板：启停 `mission web` 并嵌入 `/workbench`，DB 路径写在 `service.json` 的
   `missionsDb` 里 —— **本次要移除这个全局库概念**。
4. `mission web` 以读写方式打开库，**路径不存在会自动建空库**（`_init_schema`），
   所以"面板打开是空的"不是故障，是连到了空库。

---

## 2. 目标与非目标

### 目标
- **G1** 工作台成为**唯一**的严格流程控制面：建任务 → 推进 DAG → 审批 → 看证据。
- **G2** 库是**任务产物**：每建一个优化任务，在用户指定的工作区目录下生成独立的
  `missions.db` 与运行目录；不再有全局 `--db`。
- **G3** AEDT 侧提供**脚本按钮**：点一下打开 DSH **只有对话**的页面，
  并**默认连上当前 AEDT 工程**；该模式只加载 MCP 工具 + 优化 skill，不加载 DAG。
- **G4** 现有安全口径不倒退：`/workbench` 的读取仍只放行白名单 artifact；
  写操作只发生在工作台显式的用户操作（建任务/推进/审批）里。

### 非目标
- 不做 AEDT .NET add-in 内嵌面板（只做脚本按钮 + 浏览器/应用窗口）。
- 不做"多用户/远程调度"；仍是单机、单用户。
- 不在本次重构 DAG 模板本身（`docs/agent_templates/*.yaml` 保持）。
- 不改变 `production-guard.mjs` 的语义：DSH 的工具仍然不能起/推进 Mission；
  只有工作台的本地服务能。

---

## 3. 总体架构（改造后）

```text
┌──────────────────────────── 面 A：AEDT 内嵌（轻） ────────────────────────────┐
│ AEDT 脚本按钮  ──HTTP──►  工作台/DSH 宿主  ──►  打开 focus 页面               │
│                                              （无侧栏 / 预设 ansys-embedded） │
│  工具：mcp__aedt__* / mcp__ansys_api_memory__* / 优化 skill                  │
│  不加载：DAG、mission 生命周期、human_gate                                    │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── 面 B：工作台（重） ──────────────────────────────┐
│ 左侧：工作区树（工程）                                                        │
│   └─ 任务列表（每个任务 = 一个目录 + 一个 missions.db）                       │
│ 主区：任务详情（指标/轮次/几何/证据） + 推进 / 审批 / 自动循环                 │
│                                                                              │
│ 后端：WorkbenchTaskService                                                   │
│   workspace registry → task registry → 每任务 AgentRuntime(独立 DB)           │
│   DAG 驱动：advance_graph / run_loop_from_config                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 数据模型与目录约定

### 4.1 工作区（workspace）= 一个工程

用户在工作台左侧"新建工作区"，指定一个**目录**（必须为空或只有 `.ai-hfss/`）：

```text
<workspace-root>/                      ← 用户指定，例如 D:\proj\102-006060501
├── .ai-hfss/
│   ├── workspace.json                 ← {schema, workspace_id, name, created_at, template_defaults}
│   └── tasks/
│       └── <task_id>/                 ← 每建一个优化任务生成
│           ├── task.json              ← 建任务时的输入与状态
│           ├── missions.db            ← 该任务专属 SQLite（17 张表）
│           ├── loop.json              ← 渲染后的 run_loop 配置（含路径重写）
│           ├── runs/                  ← run_root：working 工程副本、报告、进度 HTML
│           ├── artifacts/             ← touchstone / TDR / plot / evidence
│           └── logs/
│               └── driver.log         ← 自动循环驱动的日志
```

**约定**
- `task_id` = `T-<yyyymmdd-HHMMSS>-<4hex>`，人可读、目录安全。
- `missions.db` 由 `SQLiteMissionStore` 在该路径创建（WAL）。
- 工作区目录被移动 = 任务跟着走（`loop.json` 与 `task.json` 里的路径**全部相对工作区**，
  启动时按 `workspace_root` 解析，避免绝对路径失效）。
- 工作区可被"打开/关闭"，关闭只影响 UI，不删文件。

### 4.2 task.json

```json
{
  "schema": "aihfss-task/v1",
  "task_id": "T-20261028-101500-9f3a",
  "workspace_id": "ws-...",
  "name": "CLK0/CLK1 反焊盘优化",
  "template_id": "brd_reviewed_model_optimize_loop",
  "status": "created | running | waiting_approval | paused | succeeded | failed | canceled",
  "created_at": "...",
  "params": {
    "goal": "...",
    "source_project_path": "source/xxx.aedt",
    "working_project_path": "runs/working/xxx.aedt",
    "setup_name": "Setup1", "sweep_name": "Sweep1",
    "signal_nets": ["CLK0","CLK1"], "bbox": "0,0,10,10",
    "frequency_start_ghz": 0.0, "frequency_stop_ghz": 28.0,
    "rl_target_db": -17.0, "tdr_target_ohm": 90.0,
    "tdr_observation_port": "Diff1", "expected_port_count": 4,
    "max_rounds": 3, "max_steps": 64
  },
  "graph_run_id": "gr-..."
}
```

`params` 与 `config/optimization_loops/reviewed_brd_remote.example.json` 的字段
一一对应（已核对：goal / run_root / source_project_path / working_project_path /
report_dir / setup / sweep / tdr_* / expected_port_count / frequencies / targets /
max_rounds / max_steps）。工作台表单只需要收集"工程、网络、bbox、频段、目标"这几项，
其余用模板默认值。

### 4.3 工作区注册表

`~/.dsh/ai-hfss-workstation/workspaces.json`（用户级，仅记录**打开过的工作区**，
不含任务数据）：

```json
{ "schema": "aihfss-workspaces/v1",
  "workspaces": [{ "workspace_id": "ws-...", "root": "D:/proj/x", "name": "x", "last_opened": "..." }] }
```

---

## 5. 面 B 后端：`WorkbenchTaskService`

新增模块 `src/aedt_agent/agent/workbench_tasks.py`（约 600–800 行），职责：

| 职责 | 实现要点 |
|------|----------|
| 工作区管理 | 校验目录可写、不允许嵌套已存在工作区；读写 `workspace.json` |
| 任务创建 | 建目录 → 渲染 `loop.json` → `SQLiteMissionStore(task db)` → `runtime.create_mission(goal, [], [])` → `create_graph_run(runtime, mission_id, template, initial_payload=...)`（与 `web.py:1037` 现有实现一致）→ 写 `task.json` |
| 运行时缓存 | `task_id → AgentRuntime`；`_runtime_with_workers(task_db, profile)` 可复用；LRU 上限（如 4）+ 空闲回收 |
| 推进 | `advance_graph(runtime, graph_run_id)`（现有 `graph_runner`） |
| 审批 | 现有 `approvals` 表 + `decide` 逻辑（`web.py` 已有实现可提炼） |
| 自动循环 | 后台线程 `run_loop_from_config(runtime, config, worker_id, max_workers)`；单任务单线程；可暂停（`stop_event`） |
| 状态投影 | 复用 `workbench_snapshot`，但签名改为 `(task_runtime, graph_run_id)` |
| 并发 | 每个任务独立 DB → 天然隔离；**每任务必须单驱动**：P0 实测同库并发 advance 会抛 `sqlite3.IntegrityError: UNIQUE constraint failed: node_runs.graph_run_id, node_runs.sequence`（见 `scripts/spikes/p0_samedb.py`）。实现：per-task 锁 + `driver.lock` advisory 文件（防工作台与 CLI 同时驱动）+ 只读侧用 `open_read_only` |

### 5.1 API 契约（挂同一 web 服务）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/workbench/workspaces` | 已打开工作区列表 |
| POST | `/api/workbench/workspaces` | `{root, name}` → 创建/打开工作区 |
| DELETE | `/api/workbench/workspaces/<ws>` | 从列表移除（不删文件） |
| GET | `/api/workbench/workspaces/<ws>/tasks` | 任务列表（读各 task.json，不加载 runtime） |
| POST | `/api/workbench/workspaces/<ws>/tasks` | 建任务，返回 `task_id` + `graph_run_id` |
| GET | `/api/workbench/tasks/<task>` | 任务快照（= 现有 workbench_snapshot + task 元数据） |
| POST | `/api/workbench/tasks/<task>/advance` | 推进一个节点 |
| POST | `/api/workbench/tasks/<task>/auto` | `{enabled: bool}` 启停自动循环 |
| GET | `/api/workbench/tasks/<task>/approvals` | 待审批列表 |
| POST | `/api/workbench/tasks/<task>/approvals/<id>/decide` | `{decision}` |
| GET | `/api/workbench/tasks/<task>/artifacts/<path>` | 有界 artifact 读取（沿用白名单） |

**兼容**：旧的 `/api/workbench/missions` 与 `/api/workbench/graphs/<id>` 保留一版
（映射到"默认工作区"），便于只读页面不立刻坏；下个版本删除。

### 5.2 已核实的接口（供实现直接引用）

| 能力 | 证据 |
|------|------|
| 建任务/建图 | `runtime.create_mission(goal, [], [])` + `graph_runner.create_graph_run(runtime, mission_id, template, initial_payload=...)`（`web.py:1037-1040`） |
| 推进一个节点 | `graph_runner.advance_graph(runtime, graph_run_id)`（`web.py:13` 已导入） |
| 图状态投影 | `graph_runner.graph_status(runtime, graph_run_id)`（`workbench.py:8` 已用） |
| 每任务运行时 | `cli._runtime_with_workers(db_path, profile)`：按库构造带 worker registry 的 `AgentRuntime`（`cli.py:901`） |
| 自动循环 | `loop_runner.run_loop_from_config(runtime, config, worker_id=, max_workers=, poll_interval_seconds=)`（`cli.py:649`） |
| 会话指定预设 | `sessions.create({workspaceId, agentPreset})`（`workspace.js:1429`） |
| 侧栏收起 | `ctx.layout.toggleSidebar()`（`layout.js:484`） |

### 5.3 与 CLI 的关系

`mission run-loop` 等 CLI **保留**（用于 CI/脚本化），但工作台不再依赖它；
两者可以同时读同一任务的 DB（SQLite WAL + busy_timeout 已设）。

---

## 6. 面 B 前端：`integrations/si-workbench/`

现有页面已具备：模型/参数/评审面板、TDR/SDD 图、优化历史、连接项目。
改造只加**外壳**与**控制**，不动指标渲染：

1. **左侧工作区栏**（新增，替代现在的"连接项目"弹窗）：
   - 工作区列表 + "新建工作区"（目录选择 + 名称）
   - 选中工作区 → 任务列表（状态徽标：运行中/待审批/完成）
   - "新建优化任务"按钮 → 表单
2. **任务创建表单**（字段映射见 §4.2）：工程文件、信号网络、bbox、频段、
   目标（RL/TDR）、最大轮次；必填校验 + 路径存在性校验。
3. **任务详情**（复用现有三栏）：顶部状态条（当前节点、轮次、自动循环开关）+
   右侧"推进 / 审批 / 停止"控件。
4. **空状态**：没有工作区/任务时显示引导，不再显示"连接项目"。
5. 保留 `demo: true` 演示数据路径，但**新建任务时永不写 demo 值**。

---

## 7. 面 A：AEDT 内嵌轻对话

### 7.1 focus 页面（DSH client plugin 扩展）

在现有 bundle `@local/dsh-ai-hfss-workstation` 里加一个"focus 模式"：

- 判定：URL 带 `?aihfss=focus`（或 `#focus`；宿主路由不改，纯前端判断）。
- 行为：
  1. `ctx.layout.toggleSidebar()` 收起侧栏（已核实该方法存在，`layout.js:484`）；
     仅当`sidebar != 0` 时调用一次，用户手动展开后不再强制。
  2. 隐藏我们的"AI-HFSS 工作站"面板入口（focus 下不需要）。
  3. 顶部一条细状态条：当前工程/设计 + 连接状态（数据来自 §7.3 的宿主接口）。
- **不做**新页面/新路由：复用 DSH 的对话界面，只是布局切换。

### 7.2 默认连上 AEDT 工程（本期最关键的不确定点）

已核实：
- 会话创建支持指定预设：`sessions.create({workspaceId, agentPreset})`
  （`workspace.js:1429`）→ 可以固定到 `ansys-embedded` 预设。
- **未发现**客户端可调用的"发送第一条消息"接口
  （chat/workspace 包里没有 submit/sendMessage 之外露 API）。

**P0 结论（已确认，代码级）**：宿主侧 `sessionController` 服务提供
`create({cwd|workspaceId, agentPreset})` 与 `prompt({sessionId, requestId, content}, {kind:"user", rpcId})`，
可直接程序化建会话并提交首条消息 → **主方案成立**，无需依赖模型自觉。
（`dsh-api-session-controller/lib/index.js:755, 925, 2952`；P1 首日做 30 分钟真机复核。）

因此采用**宿主侧先行**方案：

```text
AEDT 脚本按钮
  └─ POST /api/ai-hfss/aedt/open  {project_path, design, pid, grpc_port}
       ├─ 宿主把上下文写进 ~/.dsh/ai-hfss-workstation/aedt-context.json
       ├─ 宿主创建会话（agentPreset = ansys-embedded）
       ├─ 宿主注入首条用户消息（若能通过宿主服务/turn API 提交）
       └─ 返回 focus URL（含 sessionId）
  └─ 打开浏览器/应用窗口
```

- **优先**：宿主能直接提交首条消息 → 打开即自动 `check_aedt_status → connect_to_aedt →
  报告当前工程`，完全确定。
- **兜底**（若 P0 证明宿主无法注入首条消息）：`ansys-embedded` 预设的 persona 规定
  "第一个动作必须连接 AEDT 并回报工程"，同时 focus 状态条显示宿主探测到的 AEDT
  进程/gRPC 端口（宿主用 Python 子进程探测，**不需要** MCP）。
- 两种情况都满足"打开就有 AEDT 上下文"；差别只是"自动发第一条"vs"第一次提问时连"。

### 7.3 宿主接口（工作台服务的 DSH 侧扩展）

在 `integrations/dsh/bundles/ai-hfss-workstation/index.js` 增补：

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/ai-hfss/aedt/context` | AEDT 脚本上报当前工程（PID/端口/路径） |
| GET | `/api/ai-hfss/aedt/status` | 返回上报的上下文 + 本机 AEDT 进程探测结果 |
| POST | `/api/ai-hfss/aedt/open` | 建会话 + 生成 focus URL + 打开 |

### 7.4 预设：新增 `ansys-embedded`

`integrations/dsh/bundles/ansys-engineering/cordis.patch.yml` 里加一个 preset：

- 工具：`mcp__aedt__*`（必需）、`mcp__ansys_api_memory__*`、`mcp__ansys_assistant__*`
  的**只读/预览**子集（preview 允许，apply 不暴露）、shell、fs、skill、web。
- **不加载**：`tool-goal`、`tool-todo`（可保留）、任何 DAG/workflow 工具、
  `production-guard`（本预设不暴露 workflow 工具，无需该 guard）。
- persona：内嵌助手，先连接、有界报告、原始曲线 artifact-only、写操作必须
  先 preview 且由用户确认。
- 挂载 `skills/ansys-engineering-rules`。

### 7.5 AEDT 脚本按钮

产物：`integrations/aedt/ai_hfss_chat.py`（CPython，交给 AEDT 的
Tools → Run Script；AEDT 2024+ 用 CPython 3）：

- 读取当前工程：`oProject.GetPath()` / `oDesign.GetName()`（通过 AEDT 的
  IronPython/CPython 脚本上下文）；退化用 PyAEDT `Desktop(...)`。
- POST 到 `http://127.0.0.1:<host-port>/api/ai-hfss/aedt/open`。
- 打开返回的 focus URL：优先 `os.startfile`（按应用窗口），失败回落默认浏览器。
- 全程 try/except，失败弹 AEDT 消息框并给出手动命令。

> 注：脚本按钮要"出现在 AEDT 工具栏"需要用户在 AEDT 里加一次快捷方式；
> 本期交付脚本 + 说明，不做自动注册（自动注册属于 add-in 范畴）。

---

## 8. 配置与安装变更

| 位置 | 变更 |
|------|------|
| `service.json`（DSH 插件） | **删除** `missionsDb`；新增 `workbenchHost/Port`（工作台服务地址）与 `aedtContextPath` |
| `install_ai_hfss_workstation.py` | 去掉 `--missions-db`；保留 `--workbench-url`；`check` 校验新字段 |
| `ai_hfss_workstation.ps1` | 去掉 `-MissionsDb` 参数 |
| 文档 | 重写 README 的"下一步/接真实项目"为"建工作区 + 建任务"；删除"指向真实库"指导 |
| 回滚 | 不变（bundle 级回滚，字节级还原） |

工作台服务自身仍由 DSH 面板启停（复用现有 `/api/ai-hfss/start|stop`），
但启动命令从 `mission web --db <全局库>` 改为 `workbench serve --workspaces <registry>`。

---

## 9. 分阶段实施（每阶段可独立验收、可回滚）

### P0 探针 ✅ 已完成（见 [spikes/ai-hfss-p0.md](spikes/ai-hfss-p0.md)）
结论：Q1 可行（宿主 `sessionController`）、Q2 可行（`toggleSidebar` + DOM 判定）、
Q3 稳定（两库并发 0.09–0.13s 零串扰）、N1 同库并发必冲突（→ 每任务单驱动）。
以下保留原始问题描述：
1. 宿主能否**程序化提交首条用户消息**（决定 §7.2 用主方案还是兜底）。
2. focus 模式能否稳定收起侧栏（`toggleSidebar` 在 Web/桌面两端行为）。
3. 一个进程内**多个 AgentRuntime/DB** 是否稳定（建 2 个任务、并发推进、观察 SQLite 与线程）。
**产出**：`docs/spikes/ai-hfss-p0.md`（结论 + 证据），据此锁定 §7.2 方案。

### P1 后端任务化（2–3 天）
- `workbench_tasks.py`：工作区/任务/运行时缓存/推进/审批/自动循环。
- 新 API（§5.1），旧 API 保留兼容。
- pytest：建工作区、建任务（断言 DB 与 loop.json 落地）、推进、审批、并发两任务。
**验收**：命令行即可完整跑通"建工作区 → 建任务 → 推进到待审批 → 批准 → 完成"。

### P2 工作台 UI（2–3 天）
- 左侧工作区/任务树 + 新建工作区/任务表单 + 任务详情控制条。
- 前端不引入新框架，沿用现有 `app.js` 风格。
**验收**：纯 UI 完成 P1 的全部流程；刷新后状态保持；空状态有引导。

### P3 AEDT 轻入口（1.5–2 天）
- focus 模式（侧栏收起 + 状态条）。
- `/api/ai-hfss/aedt/*` 三个宿主接口。
- `ansys-embedded` 预设。
- AEDT 脚本 `ai_hfss_chat.py` + 使用说明。
**验收**：在 AEDT 里点脚本 → 打开无侧栏页面 → 自动（或首次提问即）连上当前工程 →
能问答、能调 MCP 工具；不出现 DAG/审批工具。

### P4 收尾（0.5–1 天）
- 文档重写（根 README、bundle README、本计划状态改为"已实施"）。
- 截图/验收清单；一键回滚演练。
- 清理旧 `missionsDb` 相关代码与文档表述。

**总计约 7–10 个工作日**（不含真实 AEDT 求解等待时间）。

---

## 10. 测试策略

| 层 | 内容 |
|----|------|
| 单元 | `workbench_tasks`：路径校验、任务目录生成、loop.json 渲染、状态机迁移、LRU 运行时回收 |
| 集成 | 用临时工作区建 2 个任务，各自独立 DB；并发 advance 不串库；审批走完闭环 |
| 契约 | 新旧 API 均返回 `si-forge-workbench/v1`；旧端点兼容期内不 404 |
| 前端 | 现有 Playwright 26 项检查扩展到 35+：新建工作区/任务、推进、审批、空状态 |
| DSH 侧 | 现有 31 项 pytest 全绿；新增 focus 模式断言（URL 判定 → 调 toggleSidebar）、`/api/ai-hfss/aedt/*` 护栏（loopback + 同源） |
| 安全 | artifact 白名单不变；新写接口要求 `x-ai-hfss` 头；不允许路径逃逸出工作区（`..` 拒绝） |

---

## 11. 风险与开放问题

| 风险 | 影响 | 缓解 |
|------|------|------|
| 宿主无法注入首条消息 | "打开即连"退化为"首次提问时连" | P0 先探；兜底方案已在 §7.2 |
| 一个进程多 runtime 的资源/线程问题 | 长跑不稳 | 每任务独立 DB + LRU + 单线程驱动；P0 并发验证 |
| `toggleSidebar` 在桌面端行为不同 | focus 模式失效 | P0 在桌面/Web 两端各验一次；失败则用 CSS 隐藏替代 |
| 工作区目录被用户移动 | 任务路径失效 | 全部路径相对工作区解析；打开时校验并给出修复提示 |
| 与现有"生产守卫"叙事冲突 | 认知混乱 | 文档明确：只有工作台本地服务能推进；DSH 工具仍被 guard 拦住 |
| 现有 `/api/workbench/*` 使用者 | 兼容性 | 保留一版兼容层，下版本删除 |

**待你确认（不影响开工，P0 后一起定）**
1. 工作区目录里是否允许放别的文件（我建议允许，只要求 `.ai-hfss/` 由我们独占）。
2. 任务是否需要"复制模板工程"（现在是引用 `source_project_path`，工作副本由 loop 生成）。
3. AEDT 脚本按钮的入口位置（工具栏手工添加 vs 我们提供 `.xml` 描述）。

---

## 12. 施工顺序建议

```text
P0 探针（3 个问题）
 └─► P1 后端任务化 ──► P2 工作台 UI ──► P4 收尾
                        └─► P3 AEDT 轻入口 ──► P4
```

先 P1/P2（重的一侧先落地，工作台能独立用起来），P3 只依赖 P0 结论 + 现成
DSH 契约，可并行或紧随其后。
P4 已交付（收尾与验收证据）：

- 文档：根 README / bundle README / si-workbench README / 本计划同步为"任务化 + focus 页"口径；
  明确 `service.json.missionsDb` 是 legacy 字段（只服务旧只读投影，任务各用各的库）；
- 回滚演练（干净基线）：`apply → rollback` 后 `package.json` **字节完全一致**、
  `cordis.patch.yml` 未变、bundle/链接/清单全部移除；
- 浏览器验收（真实工程）：Playwright 打开 `/workbench` → 工作区 `E:/ai-hfss-test` →
  任务 `1GHz single-point smoke`，状态条 `任务 succeeded · graph succeeded · step 1/64`，
  徽标 LIVE，无 JS 错误；几何与产物区如实显示"未接入/未注册"；
- 已知边界（不阻塞）：focus 页需要用户重启 DSH 后点验；浏览器鉴权依赖同源已登录会话；
  AEDT 脚本只上报工程/设计（pid/grpc 待补）；prompt 失败会留下已建会话。

**真机仿真记录**：1 GHz 单点共执行 2 次（一次 API 探路 42.4 s、一次工作台驱动 38.3 s），
未执行任何宽频扫描。
## P5（计划外，用户追加）：工作台模型预览

结论：**B 档已落地**——结构化几何 + 自己渲染，不是贴图。

- 抽取模块 `src/aedt_agent/agent/aihfss_inventory.py`：层叠 / 走线 / 过孔 / 铺铜，
  统一 mm；实测该工程 9 层、42 过孔、4 走线、4 铺铜，范围 2.37 × 2.84 mm；
- 图节点 `aihfss.inventory.layout_model`（只读、附着已打开会话、不求解）+ 模板
  `aihfss_layout_inventory`；
- 前端：`renderModel` 支持 `aihfss-model-inventory/v1`，俯视（层过滤/网络配色/悬停）与
  剖面（真实层叠 + 过孔贯穿 + 走线层）双视图；
- 验收：Playwright 无 JS 错误，俯视图 101 个 SVG 节点、9 个层 chips，剖面切换正常；
  截图 tmp/p5-ui/model-top.png、tmp/p5-ui/model-section.png。

实测限制：3D Layout 的 `export_3d_model` 不产出 GLB/STL（接口返回路径但无文件），
所以交互式 3D 需要后续从 inventory 几何重建（C 档），当前不做。
### P5 修订（用户反馈：尺寸/器件/文字）

- **尺寸**：剖面改为 1:1（横纵同 mm/px），层厚/标高按真实值；俯视加比例尺 + 真实范围标注；
  局部范围 2.37 × 2.85 mm，板厚 0.69 mm（该局部 cut 只有 9 层）。
- **器件**：新增 `components` 抽取（U25，part 229-000000030，2.20 × 2.20 mm，7 pins），
  俯视画虚线 footprint + 网络色 pins，剖面画在 placement 层。
- **文字**：根因是俯视 SVG viewBox 单位是 mm（约 2.7 宽），CSS px 字号被当成用户单位放大，
  字形铺满整张图；改为按图幅比例的内联字号 + 深色描边。层 chips 颜色此前继承页面深色 token
  （rgb(39,55,70)）叠深底不可读，改为显式高对比配色。
