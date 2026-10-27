# AI-HFSS Workstation（DSH 界面插件）

把 **AI-HFSS 工作站** 注册成 DSH Web GUI 的顶级界面入口，与内置的
**插件**（plugin-manager）和 **自动化任务**（schedule）平级；页面里
**点一下就启动/停止** Mission 工作台，不用先手动起服务。

> **当前状态**：导航入口、启停面板、控制接口、一键回滚都已打通；面板起的
> dashboard 默认读空库，所以嵌入的工作台**还没有项目数据**。
> **下一步主线：把工作台接到真实项目**（见下方「现状 vs 下一步」）。

| 位置 | 注册点 | 效果 |
|------|--------|------|
| 左侧主导航 | `ctx.slots` → `sidebar.panellist` | 「AI-HFSS 工作站」面板图标（order 30） |
| 主区域页面 | `ctx.slots` → `main`（key 与面板 id 相同） | 启停控制 + 只读嵌入 SI/PI 工作台 |

## 页面怎么用

页面是一个状态机，**启动服务由按钮完成**：

| 状态 | 页面显示 | 可用操作 |
|------|----------|----------|
| 检查中 | 正在检查工作台服务… | — |
| 未运行 | 「工作台服务未运行」+ 端口/库/日志路径 | **启动服务**、重试、查看日志 |
| 启动中 | 正在启动工作台服务…（自动轮询） | — |
| 运行中 | 直接嵌入 `<workbench-url>` | 停止服务、重新加载、在浏览器打开 |
| 启动失败/超时 | 失败原因 + dashboard 日志 | 重试、查看日志 |

点「启动服务」→ 后台 `POST /api/ai-hfss/start` → host 半边 spawn dashboard
→ 页面每秒轮询 `/status` 直到 `reachable` 才嵌入 iframe。**没有伪造状态**：
不可达就不嵌入空白页，退出码和日志会直接显示出来。

跨 DSH 重启也认得：host 用 `dashboard.pid` 记住自己起过的进程，
重启后面板显示为运行中（标记"不是由本面板启动"），仍可点「停止服务」把它杀掉
（会先校验命令行里确实含 `aedt_agent.agent mission web`，不是就不杀）。

## 现状 vs 下一步

### 已完成（本插件负责的部分）

| 能力 | 状态 |
|------|------|
| 与「插件 / 自动化任务」平级的导航入口 + 主页面 | ✅ |
| 面板内**一键启停** dashboard（状态机 / 轮询 / 日志 / 超时处理） | ✅ |
| 控制接口 `/api/ai-hfss/*` + loopback、同源、命令行校验护栏 | ✅ |
| 安装 / 校验 / **字节级回滚** | ✅ |
| 嵌入工作台页面本身 | ✅（但读的是**空库**） |

### 下一步（唯一未完成的主线）：工作台接真实项目

现在面板起的 dashboard 用的是默认空库
`~/.dsh/ai-hfss-workstation/missions.db`，所以 `/workbench` 打开后 Mission
列表是空的——**链路通了，数据还没接**。接下来按这个顺序做：

1. **指向真实 Mission 库**（必须是真实 reviewed-loop 运行写入的那个 SQLite）：

   ~~~powershell
   .\scripts\dsh\ai_hfss_workstation.ps1 apply -MissionsDb D:\aedt-agent-runs\reviewed-loop\missions.db
   ~~~

2. 重启 DSH → 面板点「启动服务」。
3. 工作台里点 **「连接项目」** → 选真实的 Mission / Graph。
4. **验收标准**：
   - Mission 列表出现真实运行，不再是空列表；
   - 指标区显示 worker 上报的 SDD11 / SDD21 最差值、TDR 峰值偏差、objective
     cost，并标记为 **LIVE**；
   - 历史轮次与审批（human_gate）状态可读；
   - 没有几何 inventory 时明确显示「几何预览未接入」，没有 plot artifact 时
     明确显示「真实曲线 artifact 未注册」——**不混入演示值**。

### 明确不在这一步之内

- AEDB / native inventory 的 3D 几何渲染；
- 从面板触发 mission create / advance / human_gate 审批（按守卫，仍走受控
  CLI / dashboard）；
- 任何 AEDT 写操作。

## 边界

只读视图：不启动求解、不推进 graph、不批准 human_gate、不写 AEDT。生产
start/advance/approval 仍由受控本地 CLI/dashboard 执行（与
`production-guard.mjs` 的口径一致）。面板**只**负责本机 dashboard 进程的启停。

## 主题配色（踩过的坑）

按钮不要用 `--dsw-alias-brand-primary` 配硬编码白字。默认主题里它是
`--dsw-static-neutral-bluish-1000`（近黑），**暗色主题里翻转成
`neutral-bluish-50`（近白）** —— 白底白字，按钮直接"消失"。

正确做法是照抄平台自带 Button 的成对令牌，填充与前景一起翻转：

| 用途 | 令牌 |
|------|------|
| 主按钮填充 | `--dsw-alias-button-primary-fill` |
| 主按钮文字 | `--dsw-alias-label-primary-foreground` |
| 主按钮悬停 | `--dsw-alias-button-primary-hover` |
| 正文 / 次要 / 弱化文字 | `--dsw-alias-label-primary` / `-secondary` / `-tertiary` |
| 描边 | `--dsw-alias-border-l2` / `-l3` |
| 悬停 / 按下底色 | `--dsw-alias-interactive-bg-hover` / `-active` |

`ai_hfss_client_test.mjs` 里有回归断言：主按钮必须用上面这对令牌，CSS 里
不得出现 `brand-primary` 或硬编码 `#fff` 文字色。

## 控制接口（host 半边）

`index.js` 在 DSH web 服务的 `/api/ai-hfss` 前缀下注册：

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/status` | 运行状态、owned、pid、workbench URL、库/日志路径、上次退出码 |
| GET | `/log?tail=160` | dashboard 日志尾部（有界） |
| POST | `/start` | 幂等启动（已在跑就直接返回 alreadyRunning） |
| POST | `/stop` | 停止（先校验命令行，再 `taskkill /T /F`） |

护栏（都写进测试）：

1. **只接受 loopback** socket；
2. POST 必须带 `x-ai-hfss: 1` 且 `sec-fetch-site` 不是 `cross-site`、`Origin` 同源
   —— 跨站表单/脚本无法偷偷拉起进程；
3. `stop` 只杀命令行里含 `aedt_agent.agent` + `mission` + `web` 的进程；
4. 缺 `service.json` 时返回 500 并说明，不静默降级。

## service.json（机器配置）

安装器按本机渲染，host 半边启动时读取（仓库模板里没有它）：

~~~json
{
  "repoRoot": "C:/Users/z3312/code/ansys-agent",
  "python": "C:/Users/z3312/code/ansys-agent/.venv/Scripts/python.exe",
  "host": "127.0.0.1",
  "port": 8766,
  "workbenchPath": "/workbench",
  "missionsDb": "C:/Users/z3312/.dsh/ai-hfss-workstation/missions.db",
  "logFile": "C:/Users/z3312/.dsh/ai-hfss-workstation/dashboard.log",
  "pidFile": "C:/Users/z3312/.dsh/ai-hfss-workstation/dashboard.pid",
  "profileConfig": "config/execution_profiles/local_real_aedt.json",
  "startupTimeoutMs": 45000
}
~~~

`--workbench-url` 的 host/port **同时**决定面板嵌入地址和后台绑定端口（不会错配）。
`profileConfig` 指向的 reviewed-loop profile 常在机器上不存在，
host 半边只在文件存在时才传 `--profile`（dashboard 不需要它也能跑）。

## 运行时契约（为什么能挂上去）

DSH 浏览器端是"由插件组装"的 Cordis 应用。第三方 bundle 想加界面入口，需要同时满足：

1. `package.json` 声明 `dsh.bundle.patch`（把 host loader row 挂进 profile）；
2. 包的 `dsh.client.platform === "web"`，且导出 `./client`（浏览器半边）；
3. host loader row 会 `import()` 包根，所以 `exports["."]` 必须存在；
4. 浏览器半边是 lazy-CJS：`window.__ModuleLoader__.load({ id, factory })`，
   `factory` 里 `exports.apply(ctx)` / `exports.inject = ["slots","locale"]`；
5. 只依赖平台 seed 模块（`react`）——`dsh.client.external: ["react"]` 表明它是静态
   seed，不是包行。

运行时链路：profile `dsh.profile.bundles` → 本包 patch 插入 host row →
`client-modules` 扫描该 row 的包，读取 `dsh.client` + `./client` → 通过
`/plugins` 路由把 `client.js` 送到浏览器 → `apply(ctx)` 注册 slot；
控制接口则由 host row 的 `apply(ctx)` 注册到 web 服务。

## 安装 / 校验 / 一键回滚

在仓库根目录执行（Windows PowerShell）：

~~~powershell
# 1) 只读校验：报告将要做什么，不写任何文件
.\.venv\Scripts\python.exe scripts\dsh\install_ai_hfss_workstation.py check --repo-root . --dsh-profile "$env:USERPROFILE\.dsh\profiles\desktop"

# 2) 安装（可重复执行，幂等）
.\.venv\Scripts\python.exe scripts\dsh\install_ai_hfss_workstation.py apply --repo-root . --dsh-profile "$env:USERPROFILE\.dsh\profiles\desktop"

# 3) 一键回滚：删除本插件的全部产物并把 package.json 还原成安装前字节
.\.venv\Scripts\python.exe scripts\dsh\install_ai_hfss_workstation.py rollback --repo-root . --dsh-profile "$env:USERPROFILE\.dsh\profiles\desktop"
~~~

或用一个词：

~~~powershell
.\scripts\dsh\ai_hfss_workstation.ps1 check
.\scripts\dsh\ai_hfss_workstation.ps1 apply
.\scripts\dsh\ai_hfss_workstation.ps1 rollback
~~~

重启 DSH 后生效（客户端插件在启动时装配）。

**回滚是字节级还原**：`apply` 在改 `package.json` 前先快照成
`package.json.bak-ai-hfss-<ts>`，并把快照名写进
`ai-hfss-workstation-install.json`；`rollback` 直接复制回来，而不是"反向编辑"。
快照丢失时退化为外科式移除（只删本 bundle 的登记项），并明确报告。

profile 的 `cordis.patch.yml` **完全不会被修改**：loader row 写在本包自己的
`cordis.patch.yml` 里，作为 bundle 层生效。

### 换 dashboard 地址 / Mission 库

~~~powershell
.\scripts\dsh\ai_hfss_workstation.ps1 apply `
  -WorkbenchUrl http://10.0.0.5:9001/workbench `
  -MissionsDb D:\aedt-agent-runs\reviewed-loop\missions.db
~~~

URL 只接受 `http/https`；其余字符会被拒绝（它会被渲染进 JS 字符串字面量）。

## 排查

先用固化脚本把"插件接线"和"dashboard 进程"分开（不经过 DSH，直接驱动已安装的
host 半边）：

~~~powershell
node scripts\dsh\verify_ai_hfss_service.mjs
~~~

它会打 status → start → 轮询 → workbench HTTP → stop，任何一步失败都会打印
dashboard 日志尾部，并以非 0 退出。

| 现象 | 处理 |
|------|------|
| 点启动后一直"启动中" | 打开「查看日志」看 traceback；常见是 `--db` 目录不可写或 `.venv` 被移动 |
| 启动立即失败（退出码非 0） | 面板会直接显示 `服务启动后立即退出（code=N）`，日志里有 traceback |
| 停止报 409 | 该进程不是本面板/本机服务拉起（命令行不含 `aedt_agent.agent mission web`），按提示手动处理 |
| 页面显示"控制接口不可用" | host 半边没加载：确认已 apply 并重启 DSH |
| 服务已运行，但工作台里 **Mission 列表是空的** | 这是预期：默认空库。按「现状 vs 下一步」第 1 步指向真实 Mission 库，再点「连接项目」 |
| 重启后 DSH 停在 "Failed to load plugins" | 执行一键回滚再重启 |

## 测试

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\dsh -q
~~~

- `test_install_ai_hfss_workstation.py`：check 只读、apply 幂等、rollback 字节还原、
  半清理可回滚、重复 row 冲突拒绝、URL 校验、service.json 渲染与端口一致性、契约静态校验。
- `ai_hfss_client_test.mjs`：迷你 hooks 运行时 + stub fetch，**真的点按钮**：
  启动请求带同源头、轮询到可达才嵌入 iframe、停止回到未运行、超时给日志、
  接口不可用给重试。
- `ai_hfss_host_test.mjs`：挂载真实 `index.js`，用假 req/res 驱动 `/api/ai-hfss/*`：
  状态形状、loopback/header/origin 护栏、`stop` 拒绝杀非目标进程、子进程立即退出的检测、
  缺 `service.json` 报 500。
- `test_ai_hfss_client.py`：把上面两个 node 测试接进 pytest。

## 文件

| 文件 | 作用 |
|------|------|
| `package.json` | 包声明：`exports`（`.` host 半边 + `./client` 浏览器半边）、`dsh.bundle.patch`、`dsh.client` |
| `index.js` | host 半边：控制接口 `/api/ai-hfss/*` + dashboard 启停 |
| `client.js` | 浏览器半边：lazy-CJS，注册 `sidebar.panellist` + `main`，页面状态机 |
| `cordis.patch.yml` | 插入 host loader row（`id: ai-hfss-workstation`） |

---

## 装到哪里了

`apply` 在目标 profile 下产生/修改：

| 位置 | 内容 | 回滚时 |
|------|------|--------|
| `ai-hfss-workstation-bundle/` | 渲染后的 4 个模板 + `service.json` | 整目录删除 |
| `node_modules/@local/dsh-ai-hfss-workstation` | 指向上面目录的 junction | 删除 |
| `package.json` | `dependencies` + `dsh.profile.bundles` 各加一项 | 用快照还原为安装前字节 |
| `package.json.bak-ai-hfss-<ts>` | 安装前快照 | 保留（供人工核查） |
| `ai-hfss-workstation-install.json` | 安装清单（含快照名） | 删除 |

运行时状态（不属于安装产物，回滚不删）：

| 位置 | 内容 |
|------|------|
| `~/.dsh/ai-hfss-workstation/missions.db` | 面板启动的 dashboard 使用的 Mission 库 |
| `~/.dsh/ai-hfss-workstation/dashboard.log` | dashboard 的 stdout/stderr |
| `~/.dsh/ai-hfss-workstation/dashboard.pid` | 跨 DSH 重启记住的进程号 |

## 生效与验收

客户端插件在 DSH 进程启动时装配，所以**必须重启 DSH**（桌面版：退出应用再打开）。

重启后按顺序验收：

1. 左侧主导航出现「AI-HFSS 工作站」图标，与「插件」「自动化任务」同级；
2. 点击后面板显示「工作台服务未运行」（此时**没有**手动起过服务）；
3. 点「启动服务」→ 十秒内自动变成嵌入的工作台页面；
4. 点「停止服务」→ 回到未运行状态，`http://127.0.0.1:8766/workbench` 不再响应。

若第 1 步就失败（DSH 停在 "Failed to load plugins"），执行一键回滚再重启即可。

## 扩展时参考官方运行时

需要再次核对 DSH 客户端插件契约时，可从已安装的 asar 解包官方源码（约 400MB，
用完删掉即可）：

~~~powershell
npx --yes @electron/asar extract "$env:LOCALAPPDATA\Programs\DeepSeek Harness\resources\app.asar" tmp\dsh-extract
~~~

浏览器半边看 `tmp\dsh-extract\dsh\node_modules\@deepseek-ai\dsh-client-modules\lib\client.js`；
"与内置界面齐平"的现成范例看 `dsh-client-ui-schedule`（自动化任务）与
`dsh-client-ui-plugin-manager`（插件）；第三方 bundle 范例看 profile 里的 `dsh-agy`；
host 侧 HTTP 路由契约看 `dsh-host-webserver`。
