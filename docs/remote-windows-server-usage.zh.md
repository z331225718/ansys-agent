# Ansys Assistant 远端 Windows Server 使用说明

本文给出一条可以直接落地的使用路径，适用于已经安装 AEDT 2024 R2、Claude Code 和对应模型，
并且可以通过 `pip` 联网安装 Python 包的 Windows Server。目标是从 AEDT Desktop 内点击
`Ansys Agent`，复用当前已经打开的工程和设计，通过自然语言完成只读查询、受控修改、求解与导出。

本文不介绍 Claude Code 或模型的安装。完整能力和维护者说明见
[Ansys Assistant 中文使用手册](ansys-assistant-user-guide.zh.md)，完全离线环境见
[Windows Server 离线部署](offline-windows-server-deployment.md)。

## 1. 使用前先理解四件事

### 1.1 助手复用当前 AEDT，不另开工程

推荐链路是：

```text
人工打开 AEDT 和目标工程
  -> 人工激活目标设计
  -> 从 Automation Tab 点击 Ansys Agent
  -> launcher 绑定来源端口、工程和设计
  -> Claude Code 通过 Runtime MCP 操作该会话
```

助手不会为了“修复连接”而创建同名设计，也不会自动选择另一 AEDT 进程。HFSS 3D Layout 的
内部名称可能包含 `0;` 前缀，但对话和 Runtime 只能使用 AEDT 显示的规范设计名。

### 1.2 查询和修改走不同路径

- 查询可以直接调用只读 Harness。
- 修改必须先 `preview`，再由当前 Windows 桌面的原生确认框审批，然后才能 `apply`。
- `apply` 后必须逐项回读；批量任务中途失败时必须报告 rollback 状态。
- 修改默认只发生在 AEDT 内存中，不自动保存工程。
- 保存是独立的持久化动作，需要单独 preview 和单独审批。

### 1.3 能力选择有固定优先级

```text
严格 Workflow
  -> 已注册的 typed Harness
  -> API Memory + 受控 Exploration
  -> 明确报告暂不支持
```

API Memory 基于当前项目虚拟环境中的 PyAEDT 和 PyEDB 源码建图，只提供知识证据。它不能直接操作
AEDT，也不能绕过 Runtime schema、目标快照、审批和回读。

### 1.4 一个 PowerShell 只绑定一次来源会话

从 AEDT 按钮打开的 PowerShell 绑定当时的端口、工程和设计。切换工程、设计或 AEDT 进程后，退出旧
PowerShell，再从新的活动设计点击按钮。不要跨窗口复用 `live_session_id`、preview id 或 approval token。

## 2. 推荐目录和版本基线

推荐目录：

```text
D:\ansys-agent              当前批准的项目安装目录
D:\ansys-agent-runs         Workflow 输出和 evidence
D:\ansys-agent-releases     发布包、SHA256 和上线记录
D:\aedt-project-backups     人工维护的工程备份或副本
```

当前依赖基线：

| 组件 | 要求 |
|---|---|
| Windows | x64，使用交互式桌面或 RDP session |
| AEDT | 目标部署 2024 R2 |
| 外部 Python | CPython 3.12 x64 |
| PyAEDT | 1.3.0 |
| PyEDB | 0.80.2，包含 DotNet 后端 |
| FastMCP | 3.4.4 |
| codebase-memory-mcp | 0.9.0 |

即使服务器全局 Python 已经安装 PyAEDT，也要使用项目自己的 `.venv`。不要混用系统 Python、用户
`site-packages`、AEDT 内嵌 Python 和项目虚拟环境。

## 3. 首次安装

### 3.1 检查 Python 和 AEDT

在 PowerShell 中执行：

```powershell
py -3.12 -c "import struct,sys; print(sys.executable); print(sys.version); print(struct.calcsize('P')*8)"
Test-Path "C:\Program Files\ANSYS Inc\v242\AnsysEM\ansysedt.exe"
```

Python 检查最后一行必须是 `64`。如果 AEDT 安装在其他位置，记录实际 `ansysedt.exe` 路径，
不要为了匹配示例移动 AEDT。

### 3.2 从当前分支安装

服务器能访问 GitHub 和 PyPI 时执行：

```powershell
$Root = "D:\ansys-agent"
$Ref = "codex/ansys-assistant-runtime"

git clone --branch $Ref --single-branch `
  https://github.com/z331225718/ansys-agent.git $Root

Set-Location $Root
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --editable ".[desktop]"
.\.venv\Scripts\python.exe -m pip check
```

生产交付时，`$Ref` 应替换为批准的 commit 或 release tag。不要让生产机器长期无条件跟随一个变化的
开发分支。

如果服务器不能访问 GitHub、但可以访问 PyPI，可以在联网机下载目标 commit 的源码 ZIP，传到服务器
并解压到 `D:\ansys-agent`，然后从创建 `.venv` 开始执行相同命令。

### 3.3 验证依赖确实来自项目环境

```powershell
Set-Location D:\ansys-agent

.\.venv\Scripts\python.exe -c `
  "import importlib.metadata as m,sys; print(sys.executable); print('pyaedt',m.version('pyaedt')); print('pyedb',m.version('pyedb')); print('fastmcp',m.version('fastmcp')); print('codebase-memory-mcp',m.version('codebase-memory-mcp'))"

.\.venv\Scripts\python.exe -c "import ansys.aedt.core,pyedb,clr,fastmcp,codebase_memory_mcp; print('imports OK')"
```

`sys.executable` 必须位于 `D:\ansys-agent\.venv`。如果 `clr` 导入失败，不要改用全局 PyEDB；重新检查
是否从 `.[desktop]` 安装了 `pyedb[dotnet]` 的完整依赖。

### 3.4 构建 PyAEDT/PyEDB API Memory

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli prepare --force
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli status
```

合格状态是：

```text
status = ready
ready = true
stale_packages = []
missing_projects = []
```

清单中的 PyAEDT 和 PyEDB 版本、`source_root`、`source_digest` 应与当前 `.venv` 一致。更新依赖后必须
重新构建，不能继续使用旧版本源码图。

### 3.5 检查当前 Harness 能力

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2
```

输出是机器可读 JSON。重点检查 `unavailable_capabilities`、`defaults` 和计划使用能力的 `risk`、
`approval`、`postconditions`。写能力应显示外部审批，且默认 `live_apply_saves_project=false`。

## 4. 连接已经运行的 AEDT 2024 R2

### 4.1 在 AEDT 中准备工程

1. 使用将要看到审批框的同一个 Windows/RDP 用户启动 AEDT。
2. 打开目标 `.aedt` 工程，等待工程完整加载。
3. 在 Project Manager 中单击目标设计，使其成为活动设计。
4. 生产工程建议先做人工备份，第一次写验收只使用工程副本。

### 4.2 发现实际 gRPC 端口

```powershell
Set-Location D:\ansys-agent
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
```

根据 PID、版本和 `grpc_port` 选择目标。如果返回多个 AEDT，会话选择必须由人确认；不要让 Agent 猜。
如果返回为空，先确认 AEDT 与 PowerShell 位于同一个用户桌面，再检查 AEDT 是否以可发现的 gRPC 模式
运行。

### 4.3 只读核对工程和设计

把示例端口替换为上一步真实值：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-info `
  --port 50051 `
  --aedt-version 2024.2
```

逐项与 AEDT GUI 比较：

- PID 和端口；
- AEDT 版本；
- 活动工程；
- 活动设计；
- 设计类型。

设计名如果出现 `0;Layout1` 而 GUI 显示 `Layout1`，立即停止，不要尝试创建、查询或修改同名设计。
`live-info` 结束时只释放 PyAEDT wrapper，不应关闭 AEDT 或工程。

AEDT 2024 R2 的个别早期 Service Pack 如果不能使用当前 gRPC 参数，可在启动本次 PowerShell 前设置：

```powershell
$env:PYAEDT_USE_PRE_GRPC_ARGS = "True"
```

## 5. 安装 AEDT Desktop 入口

保持目标 AEDT 和工程打开，执行：

```powershell
Set-Location D:\ansys-agent

.\.venv\Scripts\python.exe -m aedt_agent.desktop install `
  --port 50051 `
  --version 2024.2
```

正常 JSON 至少包含：

```text
installed = true
extension_name = Ansys Agent
product = Project
port = 50051
version = 2024.2
restart_required = false
```

入口写入当前 Windows 用户的 `PersonalLib`，不修改 AEDT 安装目录。刷新后应看到：

```text
Automation -> Ansys Agent
```

如果同时运行多个 AEDT，安装和卸载都显式指定端口。卸载命令：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.desktop uninstall `
  --port 50051 `
  --version 2024.2
```

项目切换安装目录、升级 launcher 或回滚版本后，都要从目标目录重新执行 `install`。按钮名称相同不能证明
入口已经指向新代码。

## 6. 第一次从 AEDT 启动

### 6.1 点击入口

回到 AEDT，在目标设计仍处于活动状态时点击 `Automation -> Ansys Agent`。入口会新开 PowerShell，
自动生成本次专用的：

```text
.aedt-agent\desktop\sessions\<session-id>\mcp.json
.aedt-agent\desktop\sessions\<session-id>\context.md
.aedt-agent\desktop\sessions\<session-id>\claude-settings.json
.aedt-agent\desktop\sessions\<session-id>\launch-claude.ps1
.aedt-agent\desktop\sessions\<session-id>\session.json
```

这些文件只服务当前会话。launcher 会启动 Runtime MCP、可用时启动 API Memory MCP，并启动只监听
`127.0.0.1` 的审批 Host。它不会加载项目中的任意 MCP 配置来扩大工具权限。

### 6.2 核对首次输出

正常行为是：

1. 调用一次 `attach_live_aedt_session`；
2. 调用 `get_live_aedt_project_info`；
3. 报告端口、工程、设计、设计类型和 AEDT 版本；
4. 等待用户任务。

成功 attach 后不应反复 attach。以下任一情况出现时，关闭本次 PowerShell，不要继续写任务：

- 工程、设计、设计类型或端口与 GUI 不一致；
- 设计名带 `0;`；
- 连接成功后不断重新 attach；
- 出现 `Computer` 或 `Chrome` permission deny rule 警告；
- Agent 表示要另开 AEDT、创建设计或运行任意 Python/PowerShell 脚本。

### 6.3 核对入口来源

```powershell
Set-Location D:\ansys-agent
$Session = Get-ChildItem .aedt-agent\desktop\sessions -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content (Join-Path $Session.FullName "session.json") -Raw -Encoding UTF8 |
  ConvertFrom-Json |
  Select-Object project_root,created_at,context,api_memory
```

`project_root` 必须是批准的项目目录；`context` 中的 port、project、design 和 type 必须与 AEDT GUI
一致。`api_memory.ready=false` 不影响已知 Harness，但未知能力查询会被禁用。

## 7. 日常对话的标准流程

### 7.1 先做只读盘点

首次连接一个设计，先发：

```text
先核对当前 AEDT 工程、设计和设计类型。只读列出当前设计的对象清单，
返回对象类型、名称、关键属性、总数和是否截断。不要修改、不要求解、不要保存。
完成后继续复用当前会话并等待。
```

对 HFSS 3D Layout 可以更具体：

```text
只读列出当前 3D Layout 的所有 Path，返回 name、net、layer、width expression 和总数。
不要修改，不要保存。
```

对 HFSS 3D 可以说：

```text
只读列出当前 HFSS 设计的 solid、sheet、material、boundary、mesh operation、setup 和 sweep。
对象多时先返回分类总数和截断状态，不要修改工程。
```

### 7.2 再提交精确写任务

一个可靠请求至少包含对象、筛选条件、目标值、审批要求、回读要求和保存策略：

```text
在当前设计中，将 <精确对象名或筛选条件> 的 <属性> 改为 <目标值>。
优先使用已注册的严格 Workflow 或 typed Harness。先读取 inventory 并列出精确目标，
再 preview；等我在 Windows 原生确认框批准后才能 apply。apply 后逐项回读，
失败时报告 rollback 是否完整。不要保存工程。
```

不要只说“把这些改一下”。条件不足时，助手应该提问，而不是猜 net、layer、单位、对象名或保存策略。

### 7.3 审批框中检查什么

点击 `Yes` 前至少检查：

- project、design 和 design type 正确；
- action 和 resource 正确；
- 目标对象数量合理，名称没有被截断或模糊替换；
- 每个对象的旧值和新值正确；
- 单位、变量名、Setup、Sweep 和求解预算正确；
- preview 不包含未请求的保存或导出动作。

点击 `No` 或 token 过期后，Agent 不应自动创建新 preview。需要重试时，由用户明确提出。

### 7.4 如何判断真正完成

不要只看 Claude Code 的自然语言总结。写任务至少要满足：

```text
status = verified
verified_count = target_count
failed_count = 0
rollback 不处于 unknown/partial
project_saved = false   # 用户没有明确要求保存时
```

还要核对每个目标的 typed readback。求解和导出任务还要核对本次结果的新鲜度、variation、文件大小、
生成时间和 SHA256。

## 8. 完整示例：把 4.3mil 线宽参数化为 W_line

### 8.1 推荐请求

```text
在当前 HFSS 3D Layout 设计中，找出 LineWidth=4.3mil 的所有 Path。
先列出对象名、net、layer 和原始 width expression，不要修改。
确认目标后使用 layout_live_parameterize_width 严格 Workflow，把它们参数化为设计变量
W_line，初值为 4.3mil。每个 graph step 都走审批，真正修改还要独立 operation 审批。
apply 后逐项回读验证，不要保存工程。
```

### 8.2 预期工具链

```text
attach_live_aedt_session             仅一次
  -> get_live_aedt_project_info
  -> inspect_ansys_workflow
  -> preview/apply workflow start
  -> select_paths
  -> preview_parameterization
  -> wait_for_live_approval           Windows 原生审批
  -> preview/apply workflow advance
  -> verify_scorecard
  -> release_live_aedt_session
```

Graph step 审批和真正修改 AEDT 的 operation 审批是两种不同授权，token 不能互换。

### 8.3 选择与成功标准

线宽筛选会忽略表达式空格和大小写，例如 `4.3 mil` 与 `4.3MIL`。它不会默认把所有物理等价单位视为
同一个表达式；设计中若同时有 `4.3mil` 和 `0.10922mm`，先列清单再决定是否都修改。

成功必须同时满足：

- 目标数大于 0，且与 preview 一致；
- `W_line` 存在，初值为 `4.3mil`；
- 每个目标 Path 的宽度表达式回读为 `W_line`；
- 非目标 Path 没有变化；
- 工程没有自动保存。

## 9. 其他常用请求模板

### 9.1 HFSS 几何平移

```text
在当前 HFSS 设计的 Global WCS 中，将 solid Box1 平移 [1.0, -0.5, 0.0]，
将 sheet PortSheet 平移 [0.0, 0.0, 0.2]，单位使用当前 model unit。
使用 hfss_live_geometry_move，先 preview。审批后 apply，验证 object/face identity、
bounding box、face center、material、boundary 和 mesh 均符合契约。不要保存工程。
```

### 9.2 HFSS 几何旋转

```text
在当前 HFSS 设计的 Global WCS 中，将 solid Box1 绕 Global Z 轴旋转 +90deg，
将 sheet PortSheet 绕 Global X 轴旋转 -30deg。旋转中心固定为 [0,0,0]。
使用 hfss_live_geometry_rotate，先 preview。审批后 apply，逐点验证 face center、vertex position、
object/face/vertex identity、material、boundary 和 mesh；失败时逆旋转恢复。不要保存工程。
```

当前不支持任意旋转中心、角度表达式或相对 WCS。每个对象的轴只能是 X/Y/Z，角度必须在
`-360～360` 度内，且不能是 0 或 ±360 度语义空操作。

### 9.3 批量创建变量

```text
在当前设计中按顺序创建或更新 W_main=4.3mil、W_double=2*W_main 和项目变量
$BoardScale=1.0。使用 aedt_live_variable_batch_upsert，先展示 create/update/noop、原值和依赖顺序，
审批后原子应用并逐项回读。不要保存工程。
```

### 9.4 创建或更新 Layout Via

```text
只读列出当前 Layout 的 padstack、signal layer、net 和现有 Via。
然后使用 layout_live_via_create，在精确坐标创建名称为 V_NEW_1 的 Via，明确 padstack、
start layer、stop layer、net、rotation 和 lock_position。先 preview，审批后 apply 并回读，不要保存。
```

更新或删除已有 Via 时，明确给出 Via 名称列表。不要通过相近位置或模糊名称选择删除目标。

### 9.5 创建 HFSS Setup 和 Sweep

```text
在当前 HFSS 设计中创建 Setup1 和 Sweep1。明确 solution frequency、pass 数、收敛条件、
sweep 起止频率、步长或点数和 sweep type。使用 hfss_live_setup_sweep_create，先检查现有名称避免冲突，
preview 后等待审批，apply 后回读 setup 和 sweep。不要自动求解，不要保存工程。
```

### 9.6 求解与导出

```text
对当前设计的 Setup1/Sweep1 启动受控非阻塞求解。先核对 setup digest、variation、并发和超时预算，
preview 并等待审批。启动后只轮询状态，不重复提交。完成后按已注册 Workflow 导出结果，
报告文件路径、大小、修改时间和 SHA256。不要把旧结果当作本次结果。
```

## 10. Harness 没覆盖时怎么做

推荐请求：

```text
先检查 capability catalog。若没有现成 Harness，不要猜 PyAEDT/PyEDB API，也不要执行任意脚本。
使用 ansys-api-memory 查询当前已安装版本的源码证据。若受控 Exploration 支持该操作，
按 propose -> validate -> preview -> 原生审批 -> apply -> readback/rollback 执行；
否则明确报告缺少的 operation、schema 和 readback 能力，不要修改工程。
```

成功走通的 Exploration 可以记录 capability trace，并生成供代码审查的 Harness 或 Skill 候选，但不会：

- 自动修改仓库；
- 自动注册新工具；
- 自动提交或推送代码；
- 把一次成功直接当成生产能力。

新增或改变 AEDT 写行为的 Harness，必须在隔离临时工程和目标 AEDT 版本上完成真实
`preview -> approval -> apply -> readback -> stale -> rollback` 验收后才能发布。mock/unit test 不能替代
真实 AEDT 证据。

## 11. 保存和结束会话

### 11.1 保存工程

需要保存时单独说：

```text
先确认前一项修改已经 verified。现在单独 preview 保存当前工程，显示工程路径和保存动作；
等我在原生确认框批准后再保存，并回读保存状态。
```

修改审批不能自动扩大为保存审批。如果只是测试，直接关闭工程不保存，并重新打开核对磁盘工程没有变化。

### 11.2 结束会话

```text
释放当前 live session，保持 AEDT 进程和所有工程打开，然后结束本次助手会话。
```

正常 release 应显示 `aedt_closed=false`、`projects_closed=false`。结束 Claude Code 后审批 Host 会随本次
PowerShell 清理。

## 12. 上线前 smoke 和验收

### 12.1 只读 Workflow smoke

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-workflow-smoke `
  --port 50051 `
  --aedt-version 2024.2 `
  --expected-project "Board" `
  --expected-design "Layout1" `
  --output-dir D:\ansys-agent-runs\smoke\layout-audit `
  --confirm-read-only
```

合格 JSON 必须同时满足 `status=passed`、`read_only=true`、`project_saved=false`，并生成对应 `.sha256`。

### 12.2 线宽 preview-only smoke

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-width-preview-smoke `
  --port 50051 `
  --aedt-version 2024.2 `
  --expected-project "Board" `
  --expected-design "Layout1" `
  --target-width 4.3mil `
  --variable-name W_line `
  --variable-value 4.3mil `
  --output-dir D:\ansys-agent-runs\smoke\width-preview `
  --confirm-preview-only
```

合格证据应显示：

```text
status = passed
stopped_after_node = preview_parameterization
approval_required = true
apply_executed = false
project_dirty = false
project_saved = false
target_count > 0
```

### 12.3 首次上线清单

- [ ] `pip check` 和全部 import 通过；
- [ ] API Memory 为 `ready`；
- [ ] `capabilities-v2` 可读取；
- [ ] `live-sessions` 找到正确 PID 和端口；
- [ ] `live-info` 的工程、设计和类型与 GUI 一致；
- [ ] 设计名不含内部 `0;` 前缀；
- [ ] Automation Tab 入口来自当前安装根；
- [ ] 点击入口后只 attach 一次；
- [ ] 只读 smoke 通过并产生 SHA256；
- [ ] preview-only smoke 不修改工程；
- [ ] 测试副本上的审批 No、审批 Yes、readback 和 rollback 已验证；
- [ ] 未明确要求时不保存；
- [ ] release 后 AEDT 和工程保持打开。

## 13. 更新和回滚

更新前退出已有 Ansys Agent PowerShell。AEDT 可以继续打开。源码安装执行：

```powershell
Set-Location D:\ansys-agent
git status --short --branch
git fetch origin
git checkout codex/ansys-assistant-runtime
git pull --ff-only origin codex/ansys-assistant-runtime

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\online\Update-AnsysAgentDependencies.ps1 `
  -InstallRoot D:\ansys-agent
```

脚本会按项目锁定版本更新依赖并重建 API Memory，但不会执行 `git pull`，也不会安装或修改 Claude Code。
更新后重新执行 Desktop `install`，再跑只读 smoke 和 preview-only smoke。

推荐用新目录并行升级，不要覆盖已批准环境：

```text
D:\ansys-agent-old
D:\ansys-agent-new
```

回滚时从旧目录重新安装 Desktop 入口。不要用 `git reset --hard` 清理服务器本地配置或工程文件。

## 14. 完全离线部署

完全离线的服务器使用 GitHub Release 中的 ZIP 和同名 `.sha256`，而不是把本地 `dist`、`.venv` 或
wheel 缓存提交进 Git。发布包包含源码、安装脚本、固定 wheelhouse、`bundle.json` 和文件级
`SHA256SUMS`。

在联网机制作发布包：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\offline\New-AnsysAgentOfflineBundle.ps1 `
  -OutputDirectory C:\AnsysAgentRelease
```

目标服务器先执行纯验签，再安装到空目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$Bundle\scripts\Install-AnsysAgentOffline.ps1" `
  -BundleRoot $Bundle `
  -VerifyOnly

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$Bundle\scripts\Install-AnsysAgentOffline.ps1" `
  -BundleRoot $Bundle `
  -InstallRoot D:\ansys-agent `
  -PythonExe C:\Python312\python.exe
```

离线包的交付位置是 GitHub Release assets。Git 分支保存源码、脚本和文档，不保存约 100 MB 的
wheelhouse，这样远端离线机仍能从 Release 下载或通过中转介质获得可验签安装包，同时避免仓库历史永久
膨胀。

## 15. 常见问题

### 15.1 出现 `Computer` 或 `Chrome` permission deny

说明旧 launcher 仍在生效。更新当前分支，从新安装目录重新执行 Desktop `install`，再关闭旧 PowerShell
并从 AEDT 重新点击入口。

### 15.2 设计名出现 `0;`

立即停止，不要继续调用 Layout/HFSS wrapper。若错误设计已经被旧版本创建且工程尚未保存，关闭不保存并
重新打开。若工程包含其他未保存修改，先人工确认错误设计为空，再从 AEDT UI 处理；不要让助手自动删除设计。

### 15.3 Path 返回 0 条

按顺序检查设计类型、规范设计名、对象类型、总 Path 数、width expression 的空格/大小写/单位、net 和
layer 过滤条件。不要通过创建同名设计“重试”。

### 15.4 找不到端口

确认 AEDT、PowerShell 和审批框属于同一个交互式 Windows 用户；重新运行 `live-sessions`；多 AEDT 时按
PID 选择；检查 loopback 和本机安全软件；早期 2024 R2 Service Pack 可尝试
`PYAEDT_USE_PRE_GRPC_ARGS=True`。

### 15.5 审批框不可见

原生审批框显示在启动 AEDT 和 Ansys Agent 的交互式桌面。Windows 服务、计划任务、纯 SSH 或另一个
RDP session 看不到该窗口。回到正确 RDP session 操作。

### 15.6 API Memory 不是 ready

已知 Harness 仍可使用，未知能力 fallback 暂停。执行：

```powershell
D:\ansys-agent\.venv\Scripts\python.exe `
  -m aedt_agent.knowledge.api_memory_cli prepare --force
```

### 15.7 Agent 说成功，但没有 preview 或 readback

把任务视为未完成。没有 `preview -> 原生审批 -> apply -> typed readback` 的写操作不属于受控路径。停止
会话，核对 `session.json` 的项目根、MCP 配置和当前 commit，再从测试工程副本重新验证。

## 16. 上线记录建议

每台服务器保存一份不含密钥的记录：

```text
安装根目录
部署方式、branch/tag 和完整 commit SHA
Python、PyAEDT、PyEDB、FastMCP、API Memory backend 版本
AEDT 版本、PID 和实际 gRPC 端口
测试工程、规范设计名和设计类型
session.json 的 project_root 和创建时间
只读 smoke JSON 路径及 SHA256
preview-only smoke JSON 路径及 SHA256
测试副本 apply/readback/rollback 结果
执行人和执行时间
```

不要记录 Claude Code token、模型凭据、审批密钥或生产工程敏感数据。

## 17. 命令速查

```powershell
# 环境检查
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2

# API Memory
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli prepare --force
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli status

# AEDT 会话
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-info --port 50051 --aedt-version 2024.2

# Desktop 入口
.\.venv\Scripts\python.exe -m aedt_agent.desktop install --port 50051 --version 2024.2
.\.venv\Scripts\python.exe -m aedt_agent.desktop uninstall --port 50051 --version 2024.2

# 查看帮助
.\.venv\Scripts\python.exe -m aedt_agent.interactive --help
.\.venv\Scripts\python.exe -m aedt_agent.desktop --help
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli --help
```

## 18. 延伸文档

- [Ansys Assistant 中文使用手册](ansys-assistant-user-guide.zh.md)
- [Ansys Assistant 部署与操作说明](ansys-assistant-operations-guide.zh.md)
- [AEDT Desktop Claude Code 入口](aedt-desktop-claude-entry.md)
- [Windows Server 离线部署](offline-windows-server-deployment.md)
- [通用交互式 Ansys 助手](interactive-ansys-assistant.md)
- [Ansys 助手能力分层与自进化架构](ansys-capability-evolution.md)
