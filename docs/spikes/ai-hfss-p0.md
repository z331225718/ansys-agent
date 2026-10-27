# P0 探针结论：AI-HFSS 工作台任务化 + AEDT 内嵌轻对话

状态：**P0 完成**
日期：2026-10-28
对应计划：[docs/ai-hfss-workbench-task-mode-plan.md](../ai-hfss-workbench-task-mode-plan.md)
复现脚本：`scripts/spikes/p0_multiruntime.py`、`scripts/spikes/p0_samedb.py`（已入库，可重复执行）

---

## 结论摘要

| # | 问题 | 结论 | 证据强度 |
|---|------|------|----------|
| Q1 | 宿主能否**程序化提交首条用户消息** | **能**：`sessionController` 服务提供 `create()` + `prompt()` | 代码级（精确 API），运行时待 P1 首日验 |
| Q2 | focus 模式能否稳定收起侧栏 | **能**：`ctx.layout.toggleSidebar()`；状态用 DOM `data-sidebar-collapsed` 判定 | 代码级（含实现与渲染） |
| Q3 | 一进程多 `AgentRuntime`/DB 是否稳定 | **稳定**：两库并发推进 0.13s、零串扰；**但同库并发会抛错** | 运行时实证 ✅ |
| N1 | （新发现）同库并发推进的后果 | `sqlite3.IntegrityError: UNIQUE constraint failed: node_runs.graph_run_id, node_runs.sequence` → **每任务必须单驱动** | 运行时实证 ✅ |

**对计划的影响**：§7.2 从"两套方案二选一"收敛为**主方案**（宿主程序化
create + prompt）；§5 的"单任务单线程"从设计偏好升级为**硬约束 + 需服务端串行化**。

---

## Q1 宿主程序化提交首条消息

### 判定：可行（代码级证据）

宿主侧 `SessionController`（`dsh-api-session-controller/lib/index.js`）：

| 能力 | 位置 | 形状 |
|------|------|------|
| 注册为 Cordis 服务 | `index.js:2952` `super(ctx, "sessionController", { namespace: "session" })` | 插件可 `ctx.inject(["sessionController"], …)` |
| 建会话 | `async create(request)`（`:755`） | `{ workspaceId? \| cwd?, sessionId?, agentPreset? }` → `{ sessionId, agentPreset? }` |
| 提交消息 | `async prompt(request, source)`（`:925`） | `request={sessionId, requestId, content:[{type:"text",text}], mode?}`，`source={kind:"user", rpcId:requestId}` |
| 投递语义 | `:949-950` | `mode==="steer"` → `agent.steer`；否则 `agent.followup` |
| 去重 | `:927` `hasPromptRequest(agent, requestId)` | 同一 requestId 重复提交只会接受一次 |

浏览器侧对应的 Remote 层（`:3218`）构造的 `source` 就是
`{kind:"user", rpcId: request.requestId, clientTimeZone?, principal?}`（`:3224-3229`），
我方宿主插件构造同样形状即可。

### 对 P1 的实现（约 15 行）

```js
// host half: ai-hfss-workstation/index.js
ctx.inject(["sessionController"], (ctxWithSession) => {
  // POST /api/ai-hfss/aedt/open 内：
  const { sessionId } = await ctxWithSession.sessionController.create({
    cwd: workbenchRoot,            // 或 workspaceId
    agentPreset: "ansys-embedded",
  });
  const requestId = crypto.randomUUID();
  await ctxWithSession.sessionController.prompt(
    { sessionId, requestId, content: [{ type: "text", text: firstPrompt }] },
    { kind: "user", rpcId: requestId }
  );
  // firstPrompt 由 AEDT 上报的工程路径拼出：
  // "当前 AEDT 工程：<path>。请先 mcp__aedt__check_aedt_status →
  //  connect_to_aedt，报告工程/设计/求解设置，然后等待我的问题。"
});
```

### 仍待运行时确认（P1 首日，30 分钟）

1. Web/桌面 profile 里 `sessionController` 是否**已挂载**（`dsh-web-app` 包含
   `dsh-api-session-controller`，预期是）。
2. `content` 的 part 形状是否只接受 `{type:"text",text}`（`:928` 里出现了
   `part.type === "image"`，文本形状需实测一次）。
3. `create({cwd})` 的 `cwd` 是否必须是已注册 workspace（`:756` 显示
   `workspaceId` 与 `cwd` 二选一，`cwd` 直接可用）。

若 1–3 任一不成立，退回计划 §7.2 兜底：预设 persona 强制"第一动作连接"+
宿主探测状态条。**兜底不需要改数据模型，只影响 P3 的 1 天工作量。**

---

## Q2 focus 模式收起侧栏

### 判定：可行（代码级证据）

- 服务方法：`ctx.layout.toggleSidebar()` → `panels.toggleSidebar()`
  （`dsh-client-ui-layout/lib/client.js:484`）。
- 行为：宽屏时 `sidebar = sidebar === 0 ? 280 : 0`；**viewport < 1024px** 时改为
  切 `narrowExpanded`（`:417-421`）。
- **没有读接口**：`LayoutController` 只暴露动作 + `panelInfo`（主面板选择），
  插件拿不到 `layoutInfo.sidebar` 的当前值（`:452-495`）。
- 但渲染层会写状态属性：frame 上 `data-sidebar-collapsed` = 是否为收起态
  （`:323`）。**用 DOM 判定，再决定是否调用 toggle**，即可做成幂等。

### 对 P3 的实现

```js
function isSidebarCollapsed() {
  return document.querySelector("[data-sidebar-collapsed]") !== null;
}
// focus 模式挂载时：
if (!isSidebarCollapsed()) ctx.layout.toggleSidebar();
```

### 风险与兜底

| 风险 | 兜底 |
|------|------|
| 桌面端（Chromium 窗口）与 Web 端渲染分支不同 | P3 在两端各验一次；不一致则用 CSS `:has([data-sidebar-collapsed])` 或直接注入样式隐藏左栏 |
| 用户手动展开后插件重复折叠 | 只在 focus 首次挂载调用一次，之后不再干预 |
| 找不到属性（DSH 升级改名） | 用选择器列表逐级降级；最终兜底为 `[data-sidebar-collapsed]`, `[data-sidebar-collapsed="true"]` |

---

## Q3 一个进程内多个 AgentRuntime / DB

### 判定：稳定（运行时实证）

脚本：`tmp/p0_multiruntime.py`＋`tmp/p0_samedb.py`；模板为 2 个 program 节点
（source → validator），**不触达 AEDT**，因此结论只针对"运行时 + SQLite"层。

```text
== 1) 两个任务，各自独立 DB ==
A db: tmp\p0-tasks\T-A\missions.db   graph: 050f1fe4-1c0
B db: tmp\p0-tasks\T-B\missions.db   graph: e2b03360-96e
isolation at creation: OK

== 2) 两线程交替推进（每任务单独 runtime） ==
A statuses: ['running', 'succeeded']
B statuses: ['running', 'succeeded']
errors: 0
both graphs completed concurrently in 0.13s

== 3) 结果未串库 ==
A: missions=1 graphs=1(node 050f…) node_runs=2
B: missions=1 graphs=1(node e2b0…) node_runs=2
no cross-contamination: OK

== 4) 一个库写、另一个只读打开（工作台读取场景） ==
read-only missions: 1 | read-only graph runs: 1
```

### 新发现 N1：同库并发推进必然冲突

```text
('t1', 'IntegrityError',
 'UNIQUE constraint failed: node_runs.graph_run_id, node_runs.sequence')
('t0', 'ok', 'running')
```

**结论**：graph runner 对同一 DB **没有加锁**，两个驱动同时 advance 会撞
`node_runs` 的 `(graph_run_id, sequence)` 唯一约束。

**对 P1 的硬要求**
1. 每个任务**只允许一个驱动**：工作台服务内 `task_id → asyncio/thread lock`，
   `advance`/`auto`/`审批后继续` 全部走这把锁；
2. UI 侧"推进"按钮在请求在途时禁用（服务端仍兜底，不信任前端）；
3. 任务目录写一个 **advisory lock 文件**（`driver.lock` 含 pid+时间），
   防止"工作台"和"CLI run-loop"同时驱动同一任务；
4. 只读侧（工作台展示）用 `SQLiteMissionStore.open_read_only`，已验证可同时打开。

---

## 锁定给 P1/P3 的决策

| 决策 | 内容 |
|------|------|
| 打开即连 | **主方案**：宿主 `sessionController.create(agentPreset)` + `prompt(首条消息)`；兜底留待 P1 首日验证结果 |
| focus 模式 | URL 判定 + `data-sidebar-collapsed` DOM 检测 + 单次 `toggleSidebar()` |
| 驱动并发 | 每任务单驱动锁 + advisory lock 文件 + 只读快照分离 |
| 运行时缓存 | 每任务独立 `AgentRuntime`，LRU 上限 4，空闲回收（Q3 已证独立库并发安全） |

## P1 首日验证清单（30–60 分钟）

- [ ] `ctx.inject(["sessionController"])` 在 web profile 可解析（打一行日志）
- [ ] `create({cwd})` 返回 sessionId
- [ ] `prompt()` 被接受（`accepted: true`），会话里出现用户消息
- [ ] `agentPreset: "ansys-embedded"` 生效（预设需先随 bundle 安装）
- [ ] focus 页面在桌面端收起侧栏（人工看一眼）

## 复现方式

```powershell
cd C:\Users\z3312\code\ansys-agent
.venv\Scripts\python.exe scripts\spikes\p0_multiruntime.py   # Q3 主验证
.venv\Scripts\python.exe scripts\spikes\p0_samedb.py          # N1 同库冲突
```

> 说明：P0 的 Q1/Q2 是**代码级**证据（读取运行中版本 `0.2.1-rc.3` 的客户端
> 与宿主产物），不是运行时探针；因此 P1 首日保留了一条 30–60 分钟的真机
> 验证清单，未把"已确认"写成既成事实。
