# Ansys Assistant 中文使用手册

本文面向在 Windows 工作站或 Windows Server 上使用 AEDT、HFSS、HFSS 3D Layout 的工程师。
目标是让使用者从安装、连接现有 AEDT 会话开始，通过对话完成查询、预览、审批、修改和验证，
同时清楚知道助手会做什么、不会做什么，以及出现异常时如何恢复。

本文假定目标机器上的 Claude Code 和对应模型已经可用，不包含它们的安装或模型配置过程。

文中的 `D:\ansys-agent`、`50051`、`Board`、`Layout1` 都是示例值，执行前必须替换为目标机器的
实际安装目录、实际发现端口、活动工程和活动设计。除非命令明确写出“保存”，本文所有 live 修改示例
都以“只修改 AEDT 内存、完成回读、默认不保存工程”为前提。

## 快速导航

| 你的目标 | 建议先看 |
|---|---|
| 在一台新 Windows Server 上安装 | 第 3～6 节 |
| 核对部署提交、依赖版本并形成移交记录 | 第 4.3 节 |
| 连接已经打开的 AEDT 工程 | 第 7～9 节 |
| 用自然语言查询或修改当前工程 | 第 10～14 节 |
| 操作文件副本而不是当前工程 | 第 15 节 |
| 使用可暂停、可恢复的严格 Workflow | 第 16 节 |
| Harness 没有现成能力 | 第 17 节 |
| 做上线前真实 AEDT 验收 | 第 18、21 节 |
| 遇到端口、设计名、审批或 PyEDB 问题 | 第 19 节 |

## 0. 推荐操作方式

这一节是日常使用的标准作业流程。管理员完成一次安装后，工程师通常只需要操作 AEDT、点击入口、
描述任务和检查审批内容，不需要手工启动 MCP，也不需要编写 PyAEDT 脚本。

### 0.1 角色与职责

| 角色 | 负责什么 | 不需要做什么 |
|---|---|---|
| 部署管理员 | 安装项目虚拟环境、验签、构建 API Memory、安装 Automation Tab、执行只读 smoke | 不配置或替换远端已有 Claude Code 和模型 |
| 仿真工程师 | 打开工程、激活设计、描述任务、核对 preview、审批、检查 readback、决定是否保存 | 不选择 gRPC 实现、不手写 MCP JSON、不直接改 Harness |
| Harness 维护者 | 新增受控能力、编写回读和 rollback、在真实目标 AEDT 版本验收 | 不能只靠模型自述或 mock test 宣称能力可用 |

推荐固定以下目录，便于升级和回滚：

```text
D:\ansys-agent                 当前批准的安装目录
D:\ansys-agent-runs            Workflow 结果和 evidence，不放进源码目录
D:\ansys-agent-releases        下载的 ZIP、SHA256 和解压包
D:\aedt-project-backups        人工管理的工程副本或备份
```

项目 Python 只使用 `D:\ansys-agent\.venv`。系统 Python、AEDT 内嵌 Python 和用户全局
`site-packages` 中即使已经装有 PyAEDT，也不应与项目环境混用。

### 0.2 管理员首次上线顺序

首次部署不要直接从生产工程尝试写操作。按以下顺序逐级放行：

1. 选择第 4 节的一种部署方式，安装到新的空目录。
2. 执行 `pip check`、真实 import preflight 和 API Memory `status`。
3. 正常打开 AEDT GUI 和一个测试工程副本，不要让安装脚本代替人工打开生产工程。
4. 用 `live-sessions` 发现实际端口，再用 `live-info` 只读核对工程、设计和版本。
5. 安装 `Automation -> Ansys Agent` 入口并从 AEDT 内点击启动。
6. 完成一次只读 inventory，确认设计名没有内部 `0;` 前缀。
7. 完成一次 preview-only smoke，确认目标数量、旧值和预期新值正确。
8. 在测试工程副本上分别验证审批 `No`、审批 `Yes`、apply readback 和“不保存”。
9. 关闭测试工程不保存，重新打开确认磁盘文件未被意外修改。
10. 保存 smoke JSON、SHA256、项目 commit、AEDT/PyAEDT/PyEDB 版本，作为该服务器的上线记录。

前一步失败时不要跳级。例如 `live-info` 的设计名不正确，就不应继续安装入口后尝试写操作。

### 0.3 工程师每天的启动顺序

每次操作一个工程或设计都执行下面的短流程：

1. 在同一个交互式 RDP 会话中启动 AEDT。
2. 打开目标工程，等待工程完整加载，然后在 Project Manager 中单击激活目标设计。
3. 在 AEDT 中点击 `Automation -> Ansys Agent`，保留新打开的 PowerShell 窗口。
4. 等待助手只 attach 一次，并报告端口、工程、设计、设计类型和 AEDT 版本。
5. 人工把报告内容与 AEDT GUI 逐项比较；任一项不一致就关闭本次 PowerShell。
6. 先发只读任务，确认对象清单和筛选条件能命中合理数量。
7. 写任务明确要求 `preview -> 审批 -> apply -> readback`，并明确是否保存。未说明时默认不保存。
8. apply 后检查 `status=verified`、目标数量、失败数量、rollback 状态和 `project_saved=false`。
9. 需要保存时另行明确提出保存，让保存走独立 preview 和审批。
10. 任务结束后让助手 release session，或直接退出本次 Claude Code；确认 AEDT 和工程仍保持打开。

PowerShell 是本次按钮来源会话的安全边界。不要把一个 PowerShell 长期复用于后来切换的工程或设计，也不要
把其中的 `live_session_id`、preview id 或 approval token 复制到另一窗口继续使用。

### 0.4 切换工程、设计或 AEDT 进程

当前会话绑定按钮点击时的 gRPC 端口、project 和 design。需要切换时：

1. 等待正在执行的只读调用结束；如果正在求解，先按任务约定处理，不要直接杀 AEDT。
2. 退出旧 PowerShell 中的 Claude Code，使助手释放 wrapper。
3. 回到 AEDT，激活新的工程和设计。
4. 如果切换到了另一个 AEDT 进程，重新用 `live-sessions` 确认端口，并为该端口重新安装入口。
5. 再次点击 `Automation -> Ansys Agent`，重新核对身份。

旧会话返回 `project_forbidden` 或 `design_forbidden` 是预期保护，不是应该通过反复 attach 绕过的错误。

### 0.5 可直接使用的对话模板

只读盘点：

```text
先核对当前 AEDT 工程、设计和设计类型。只读列出当前设计的 <对象类型>，
返回名称、关键属性和总数。不要修改、不要求解、不要保存；完成后复用当前会话等待。
```

一次受控修改：

```text
在当前设计中，将 <明确对象或筛选条件> 的 <属性> 改为 <目标值>。
优先使用已注册的严格 Workflow 或 typed Harness。先读取 inventory 并列出精确目标，
再 preview；等我在 Windows 原生确认框批准后才能 apply。apply 后逐项回读，
失败时报告 rollback 是否完整。不要保存工程。
```

线宽参数化：

```text
在当前 HFSS 3D Layout 中找出 LineWidth=4.3mil 的所有 Path，先列出名称、net、layer
和原始 width expression。使用 layout_live_parameterize_width，把它们参数化为设计变量
W_line=4.3mil。每一步都走 preview 和审批，apply 后回读验证，不要保存工程。
```

Harness 未覆盖的能力：

```text
先检查 capability catalog。若没有现成 Harness，不要猜 API，也不要生成或执行任意脚本。
使用 ansys-api-memory 查询当前已安装 PyAEDT/PyEDB 的源码证据；如果受控 Exploration
仍不支持，请报告缺少的 operation/schema/readback 能力和建议新增的 Harness，不要修改工程。
```

求解与导出：

```text
对当前设计使用已注册的 live solve/export Workflow。先核对 setup、sweep、variation、
结果目录和已有结果新鲜度；提交求解、监控、导出分别按 Workflow 推进并等待对应审批。
只把带新鲜度检查和 SHA256 的产物报告为本次结果，不要自动保存工程。
```

### 0.6 如何判断助手真的完成了任务

不要只看自然语言中的“成功”。至少检查：

| 阶段 | 必须看到的证据 |
|---|---|
| attach | 实际端口、规范 project/design、设计类型和版本与 GUI 一致 |
| inventory | 总数、返回数、是否截断、目标对象的原始属性 |
| preview | action、resource、目标清单、旧值、新值、snapshot digest、`project_dirty=false` |
| approval | Windows 原生确认框；Claude Code 自己的工具确认不能代替它 |
| apply | `status=verified`、实际回读值、成功/失败数量、`project_saved=false` |
| failure | rollback 是否 complete，以及回滚后的 inventory/digest 是否恢复 |
| solve/export | 本次结果的新鲜度、variation、文件大小、时间和 SHA256 |
| save | 独立保存 action、独立审批，以及保存后的路径/状态回读 |

任何写操作如果没有 preview、原生审批和 typed readback，就不属于本项目承诺的受控路径。

## 十分钟上手

已经完成安装、AEDT 2024 R2 正在运行且 Claude Code 可用时，最短路径如下。

1. 在 AEDT GUI 中打开目标工程，并单击激活目标设计。
2. 在项目安装目录发现实际会话：

```powershell
Set-Location D:\ansys-agent
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
```

3. 用返回的端口做一次只读身份核对：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-info `
  --port 50051 `
  --aedt-version 2024.2
```

4. 首次部署时安装 AEDT 入口：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.desktop install `
  --port 50051 `
  --version 2024.2
```

5. 在 AEDT 中点击 `Automation -> Ansys Agent`。确认 PowerShell 中报告的工程、设计和设计类型与
   AEDT GUI 完全一致，设计名不得带内部 `0;` 前缀。
6. 先发一个只读请求：

```text
只读列出当前 3D Layout 的 Path 总数、net、layer 和 width expression。
不要修改、不要保存；完成后继续复用当前会话并等待。
```

7. 再发一个带明确边界的修改请求：

```text
使用 layout_live_parameterize_width 严格工作流，找出 LineWidth=4.3mil 的 Path，
把它们参数化为 W_line=4.3mil。先给出目标清单和 preview；每个 graph step 都要审批，
真正修改还要独立 operation 审批。apply 后回读验证，但不要保存工程。
```

只读查询没有产生 Windows 审批框是正常的；任何修改、求解或保存如果没有 preview 和原生审批，
都应停止操作并检查版本或 MCP 配置。

## 1. 适用范围

当前助手包含两类入口：

1. **AEDT Desktop 对话入口**：从 AEDT 的 `Automation -> Ansys Agent` 打开 PowerShell 和
   Claude Code，自动绑定当前 AEDT 端口、活动工程和活动设计。这是日常操作的推荐入口。
2. **命令行入口**：用于部署验收、会话发现、只读检查、文件副本操作和故障诊断。

Desktop 对话入口当前适合：

- 连接并复用已经运行的 AEDT，不重复启动 AEDT；
- 核对当前工程、设计、设计类型和 AEDT 版本；
- 查询 HFSS 3D Layout 的 Path/line，并按名称、网络、层和线宽过滤；
- 将一组指定线宽的 line 参数化；
- 在 HFSS/3D Layout 中按依赖顺序原子创建或更新一批 design/project variable；
- 查询 HFSS Global/relative coordinate system，并创建一个相对 Axis/Position 坐标系后恢复原活动 WCS；
- 查询 HFSS 3D 的对象、面、材料、mesh、far-field setup、setup、port、boundary 和 report，受控创建 typed primitive batch、已有几何上的 Wave/Lumped Port、五类表面边界/等效器件、数值型各向同性电磁材料，为显式 solid batch 分配已有工程材料或 Length Based Mesh，并创建有界 Infinite Sphere；在 3D Layout 中原子创建工程材料并分配给一个明确 stackup layer 字段，基于既有 padstack、signal layer 和 net 批量创建精确 Via，批量移动、旋转、改网和锁定已有 Via，或严格删除可完整重建的 Via；
- 通过预览和原生审批创建受支持的 setup、boundary、port、report；
- 通过批准链路启动、查询、取消求解以及导出受控结果；
- 对 Harness 尚未覆盖的能力查询 PyAEDT/PyEDB API Memory，并走受控 Exploration。

它不是自由脚本执行器。Desktop 模式不会开放任意 Python、PowerShell、COM、`eval`、`exec`、
文件编辑或浏览器工具，也不会因为 API Memory 找到了源码就自动获得写权限。

## 2. 系统工作方式

推荐链路如下：

```text
AEDT 中点击 Ansys Agent
  -> launcher 读取来源端口、工程和设计
  -> 启动会话专用 Runtime MCP
  -> 可选启动只读 API Memory MCP
  -> Claude Code 只连接来源 AEDT 会话
  -> 读取操作直接执行
  -> 写操作先 preview
  -> Windows 原生确认框审批
  -> apply
  -> readback/rollback
  -> 默认不保存工程
```

能力选择顺序固定为：

```text
严格 Workflow
  -> 已注册 Harness
  -> 受控 Exploration
  -> 不支持并明确报告
```

API Memory 只回答“某个版本的 PyAEDT/PyEDB 有什么 API、源码和示例”，不能直接操作 AEDT。

## 3. 版本基线

当前发布基线：

| 组件 | 版本或要求 |
|---|---|
| 操作系统 | Windows x64 |
| 外部 Python | CPython 3.11 x64 |
| AEDT | 目标为 2024 R2；开发验收覆盖 2026 R1 |
| PyAEDT | 1.3.0 |
| PyEDB | 0.80.2 |
| PyEDB 2024 R2 后端 | `pyedb[dotnet]` / `ansys-pythonnet` |
| MCP | FastMCP 3.4.4 |

AEDT 2024 R2 使用 PyEDB DotNet 路径。只安装基础 `pyedb` 而没有 `[dotnet]` 依赖，会导致
`clr` 导入或 EDB 初始化失败。

## 4. 选择部署方式

根据目标服务器的联网条件选择一种方式，不要把两套安装方法混在同一个目录里：

| 条件 | 推荐方式 | 适用场景 |
|---|---|---|
| 服务器可访问 GitHub 和 PyPI | 源码联网安装 | 需要尽快使用当前分支的最新 Harness；后续升级最方便 |
| 服务器只能访问 PyPI，不能访问 GitHub | 先传源码 ZIP，再联网安装依赖 | 内网允许 Python 包下载，但限制代码托管站点 |
| 服务器完全离线 | 离线发布包 | 使用带 wheelhouse、清单和 SHA256 的冻结版本 |

无论选择哪种方式，Claude Code 和模型都由服务器现有环境提供，本项目不会安装、替换或修改它们。
项目自己的 Python 依赖必须安装进 `D:\ansys-agent\.venv`，不要装进 AEDT 内嵌 Python，也不要依赖
用户全局 `site-packages`。

### 4.1 服务器可联网：首次安装当前代码

下面是远端服务器可以访问 GitHub 和 PyPI 时的推荐路径。当前开发验收分支是
`codex/ansys-assistant-runtime`；正式发布后应把 `$Ref` 替换为已批准的 release tag 或主分支 commit，
不要在生产服务器上无条件跟随一个会变化的分支。

```powershell
$Root = "D:\ansys-agent"
$Ref = "codex/ansys-assistant-runtime"

git clone --branch $Ref --single-branch `
  https://github.com/z331225718/ansys-agent.git $Root

Set-Location $Root
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --editable ".[desktop]"
.\.venv\Scripts\python.exe -m pip check
```

`.[desktop]` 会按项目锁定的兼容基线安装 PyAEDT、PyEDB DotNet 后端、FastMCP 和
codebase-memory-mcp。不要再执行无版本约束的 `pip install -U pyaedt pyedb`，否则服务器环境可能
领先于已经实测的 Harness 契约。

安装完成后构建本机 API Memory，并检查能力目录：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli prepare --force
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli status
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2
```

最后确认关键依赖来自项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -c `
  "import importlib.metadata as m,sys; print(sys.executable); print('pyaedt',m.version('pyaedt')); print('pyedb',m.version('pyedb')); print('fastmcp',m.version('fastmcp'))"
```

Python 路径必须位于 `D:\ansys-agent\.venv`。版本应与第 3 节一致。随后继续执行第 7 节的会话发现、
第 8 节的 Desktop 入口安装和第 21 节的上线验收。

如果服务器不能访问 GitHub，可以在联网机下载该 ref 的源码 ZIP，传到服务器并解压到
`D:\ansys-agent`，然后从 `py -3.11 -m venv .venv` 开始执行相同命令。源码 ZIP 不包含 wheelhouse，
这种方式仍要求服务器能够访问 PyPI。

### 4.2 服务器完全离线：获取发布包

发布包位于 GitHub Releases，不放进 Git 历史，避免每次 clone 都携带约 100 MB wheelhouse。

打开发布页 <https://github.com/z331225718/ansys-agent/releases>，选择目标 commit 对应的
`Ansys Assistant Offline` 版本，并同时下载：

- `ansys-agent-offline-0.1.0-win-amd64-py311.zip`；
- `ansys-agent-offline-0.1.0-win-amd64-py311.zip.sha256`。

具体 SHA256 以对应 Release 描述和同一 Release 下的 `.sha256` 文件为准。
“最新 Release”不一定等于“当前开发分支 HEAD”。部署前应核对 Release 的 target commit、包内
`bundle.json` 的 `project.git_revision` 和本次准备验收的代码版本；缺少目标 Harness 时不要用旧包硬跑。

联网中转机可以使用：

```powershell
$Tag = "v0.1.0-ansys-assistant-preview.3"  # 示例；按发布页选择实际版本
gh release download $Tag `
  --repo z331225718/ansys-agent `
  --pattern "ansys-agent-offline-*.zip*" `
  --dir C:\AnsysAgentTransfer
```

把 ZIP 和 `.zip.sha256` 一起传到目标机器。不要只传 ZIP 而丢掉旁路校验文件。

### 4.3 核对部署版本并形成移交记录

“目录叫 ansys-agent”不能证明运行的是本次发布。上线、升级和回滚都应记录代码来源、依赖版本、
API Memory 状态和真实 AEDT smoke 证据。这样遇到模型行为异常时，能先排除旧源码、旧 `.venv` 或旧
Automation Tab 入口，而不是直接怀疑工程数据。

源码安装先执行：

```powershell
$Root = "D:\ansys-agent"
Set-Location $Root

git status --short --branch
git rev-parse HEAD
git remote get-url origin

.\.venv\Scripts\python.exe -c `
  "import importlib.metadata as m,sys; print(sys.executable); print('aedt-agent',m.version('aedt-agent')); print('pyaedt',m.version('pyaedt')); print('pyedb',m.version('pyedb')); print('fastmcp',m.version('fastmcp'))"

.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli status
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2
```

正式交付的源码工作区应指向批准的 commit，且 `git status --short` 没有未解释的修改。服务器本地配置
如确实需要保留，应单独列入移交记录，不能用 `git reset --hard` 清理。

离线包在安装前读取清单：

```powershell
$Manifest = Get-Content "$Bundle\bundle.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$Manifest.project | Format-List name,version,git_revision,source_dirty
$Manifest.target  | Format-List os,architecture,python,aedt
$Manifest.desktop_dependencies
```

必须满足：

- `git_revision` 是批准的完整 commit SHA；
- `source_dirty` 为 `False`；
- `target.python`、`target.aedt` 与服务器基线一致；
- `desktop_dependencies` 与本手册第 3 节一致；
- 外层 ZIP SHA256 和包内文件级 `SHA256SUMS` 均已通过安装器校验。

离线安装根目录不包含 Git 元数据，后续无法靠 `git rev-parse` 还原来源。因此要保留本次 ZIP、
`.zip.sha256`、`bundle.json` 和安装器 JSON 输出，不能安装完成后只留下 `D:\ansys-agent`。

建议每台机器留一份不含密钥的上线记录，至少包含：

```text
安装根目录
部署方式（源码 commit / Release tag / 离线包文件名）
完整 Git commit SHA 和 source_dirty
Python、PyAEDT、PyEDB、FastMCP 版本
AEDT 版本、PID、实际 gRPC 端口
测试工程和设计名
API Memory status
只读 smoke JSON 路径及 SHA256
preview-only smoke JSON 路径及 SHA256
副本工程 apply/readback 结果
执行人和执行时间
```

这份记录只保存版本和验收事实，不要记录 Claude Code token、模型凭据、审批 key 或生产工程敏感内容。

## 5. 离线安装

### 5.1 检查 Python

目标机器必须有外部 CPython 3.11 x64。不要使用 AEDT 内嵌 Python：

```powershell
py -3.11 -c "import struct,sys; print(sys.executable); print(sys.version); print(struct.calcsize('P')*8)"
```

最后一行必须是 `64`。如果 `py -3.11` 不存在，使用 CPython 3.11 的绝对路径，例如
`C:\Python311\python.exe`。

### 5.2 校验外层 ZIP

```powershell
$Zip = "X:\transfer\ansys-agent-offline-0.1.0-win-amd64-py311.zip"

Get-FileHash $Zip -Algorithm SHA256
Get-Content "$Zip.sha256" -Encoding UTF8
```

两边的 SHA256 必须完全相同。

### 5.3 解压并执行纯验签

```powershell
Expand-Archive `
  X:\transfer\ansys-agent-offline-0.1.0-win-amd64-py311.zip `
  X:\transfer\expanded

$Bundle = "X:\transfer\expanded\ansys-agent-offline-0.1.0-win-amd64-py311"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$Bundle\scripts\Install-AnsysAgentOffline.ps1" `
  -BundleRoot $Bundle `
  -VerifyOnly
```

成功结果应包含：

```text
status          = verified
project_version = 0.1.0
target_python   = 3.11
target_aedt     = 2024.2
```

### 5.4 正式安装

安装目录必须不存在或为空。安装器拒绝覆盖旧环境：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$Bundle\scripts\Install-AnsysAgentOffline.ps1" `
  -BundleRoot $Bundle `
  -InstallRoot D:\ansys-agent `
  -PythonExe C:\Python311\python.exe
```

安装器会：

- 创建 `D:\ansys-agent\.venv`；
- 只使用 wheelhouse，不访问 PyPI；
- 安装项目和固定版本依赖；
- 执行 `pip check`；
- 构建本机 PyAEDT/PyEDB API Memory；
- 失败时只清理由本次安装创建并带有 rollback marker 的目录。

项目安装后不要移动或改名，因为 Desktop launcher 使用安装根目录和 `.venv` 的绝对路径。

### 5.5 环境验收

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  D:\ansys-agent\scripts\offline\Test-AnsysAgentOffline.ps1 `
  -InstallRoot D:\ansys-agent `
  -BundleRoot $Bundle `
  -AedtVersion 2024.2
```

重点检查：

- `pyaedt` 为 `1.3.0`；
- `pyedb` 为 `0.80.2`；
- `ansys.aedt.core`、`pyedb`、`clr`、`fastmcp`、`codebase_memory_mcp` 均可导入；
- `api_memory` 为 `ready`；
- 找到了正确 AEDT 安装目录。

## 6. 已有源码环境的在线更新

只有在远端项目源码已经更新到目标 commit 后，才能运行依赖更新脚本。这个脚本不会替你执行
`git pull`，也不会安装 Claude Code。推荐先确认工作区没有待保留的本地修改，再做快进更新：

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

如果服务器固定在 release tag，应改为 `git checkout <approved-tag>`，不要执行分支 `pull`。如果
`git status` 显示本地修改，先审查并备份，不能用 `reset --hard` 覆盖服务器配置或工程文件。

运行前退出已经打开的 Ansys Agent PowerShell。AEDT 可以保持打开。更新完成后重新从 Automation Tab
启动助手，使 broker 和 MCP server 加载新代码。

## 7. 连接正在运行的 AEDT

### 7.1 正确打开方式

1. 使用计划操作 AEDT 的 Windows/RDP 用户登录。
2. 正常启动 AEDT GUI。
3. 打开目标工程并激活目标设计。
4. 不要手工执行 `ansysedt.exe -grpcsrv 50051` 来猜端口。
5. 使用助手发现 AEDT 实际监听端口。

```powershell
Set-Location D:\ansys-agent
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
```

输出可能包含多个 AEDT 进程。根据 PID、版本和 `grpc_port` 选择目标，不要让 Agent 自行在多个
AEDT 之间猜测。

### 7.2 只读连接验证

```powershell
D:\ansys-agent\.venv\Scripts\python.exe -m aedt_agent.interactive live-info `
  --port 50051 `
  --aedt-version 2024.2
```

该命令只执行 attach、读取工程信息、release，不关闭 AEDT 或工程。

如果 AEDT 2024 R2 的早期 Service Pack 无法使用当前 gRPC 参数，可在启动助手前设置：

```powershell
$env:PYAEDT_USE_PRE_GRPC_ARGS = "True"
```

## 8. 安装 AEDT Automation Tab 入口

确保 AEDT GUI 和工程已经打开，然后执行：

```powershell
Set-Location D:\ansys-agent

.\.venv\Scripts\python.exe -m aedt_agent.desktop install `
  --port 50051 `
  --version 2024.2
```

安装器写入当前 Windows 用户的 `PersonalLib`，不会修改 AEDT 安装目录。刷新后应在 AEDT 中看到：

```text
Automation -> Ansys Agent
```

如果同时运行多个 AEDT，安装时必须显式指定端口和版本。要卸载入口：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.desktop uninstall --port 50051
```

升级到新安装目录后，要从新目录重新执行 `install`，让按钮指向新的 launcher。

安装命令会输出 JSON。至少核对 `installed=true`、`personal_lib`、`port`、`version` 和
`restart_required`。不要只凭按钮名称判断安装成功，因为旧入口和新入口在 AEDT 中都显示为
`Ansys Agent`。

从按钮启动一次后，可以在项目根目录核对最新会话元数据：

```powershell
Set-Location D:\ansys-agent
$Session = Get-ChildItem .aedt-agent\desktop\sessions -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content (Join-Path $Session.FullName "session.json") -Raw -Encoding UTF8 |
  ConvertFrom-Json |
  Select-Object project_root,created_at,context,api_memory
```

其中 `project_root` 必须是本次批准的安装目录，`context.port/project_name/design_name/design_type` 必须
与 AEDT GUI 一致。`api_memory.ready=false` 不会禁用已有 Harness，但会禁用未知能力查询；应在写任务前
按第 19.10 节修复。`session.json` 不含 API key 或 approval secret，可以作为本次入口来源的排障证据。

## 9. 第一次启动

### 9.1 启动前检查

- 当前活动工程是要操作的工程；
- 当前活动设计是要操作的设计；
- 工程最好已有备份或版本控制副本；
- 没有遗留的旧 Ansys Agent PowerShell；
- 当前用户能看到 Windows 原生确认框。

### 9.2 从 AEDT 启动

点击 `Automation -> Ansys Agent`。launcher 会打开 PowerShell，并给 Claude Code 一条初始任务：

```text
请先连接来源端口，核对活动工程和设计，然后等待我的任务。
```

正常行为是：

1. 只调用一次 `attach_live_aedt_session`；
2. 调用 `get_live_aedt_project_info`；
3. 报告工程、设计和设计类型；
4. 等待用户任务。

成功连接后不应反复 attach。如果 Agent 在成功后不断重连，应停止该 PowerShell，确认远端已更新到
包含 session 复用修复的版本，再重新点击入口。

### 9.3 核对设计名

HFSS 3D Layout 的内部 COM 名称可能类似：

```text
0;LayoutDesign
```

助手必须显示规范设计名：

```text
LayoutDesign
```

如果初始提示或核对结果仍出现 `0;` 前缀，立即停止，不要继续查询或修改。旧版本可能把内部标识传给
PyAEDT，进而隐式创建空白设计。新版本会使用 `GetDesignName()`，并在创建任何 wrapper 前确认设计
已经存在；名称不匹配时直接失败。

## 10. 日常对话方法

一个高质量任务至少说明：

- 操作对象：工程、设计、net、layer、对象名或属性；
- 选择条件：例如 `LineWidth=4.3mil`；
- 目标动作：查询、参数化、创建 setup、求解或导出；
- 参数名和值：例如 `W_line=4.3mil`；
- 是否保存工程；如果没有明确说保存，助手默认不保存。

推荐表达：

```text
在当前 HFSS 3D Layout 设计中，找出 LineWidth=4.3mil 的所有 line。
先列出对象名、net、layer 和当前线宽，不要修改。
确认后把这些 line 的宽度参数化为设计变量 W_line，变量初值为 4.3mil。
完成后回读验证，但不要保存工程。
```

不推荐表达：

```text
把线弄一下。
```

条件不完整时，Agent 应先询问，而不是猜测 net、layer、变量值或保存策略。

### 10.1 入口怎么选

| 任务类型 | 推荐入口 | 原因 |
|---|---|---|
| 当前工程的只读查询 | 已注册 `get_live_*` / `list_live_*` Harness | 快、确定、不会改工程 |
| 当前工程的一次受控修改 | 有对应 live Workflow 时优先 Workflow，否则 `preview_live_*` / `apply_live_*` | 可冻结目标、审批和回读 |
| 多步、循环、分支、求解、评分 | 严格 Workflow | 每步可审计，可暂停和恢复 |
| 未知 PyAEDT/PyEDB API | API Memory + 受控 Exploration | 先查源码证据，再受限执行 |
| 对磁盘工程做批处理 | 文件副本 CLI | 不直接覆盖源工程 |

用户通常只需要描述工程任务，不需要手工输入 MCP 工具名。工具名适合用于明确执行边界、复核 Agent
是否走了正确路径，以及排查模型误选工具。

### 10.2 一次可靠任务的生命周期

```text
绑定来源会话
  -> 核对 project/design/type
  -> 读取 inventory
  -> 精确选择目标
  -> preview 冻结目标和 snapshot digest
  -> 用户在 Windows 原生确认框审批
  -> apply
  -> readback / scorecard
  -> 默认不保存
  -> release wrapper，AEDT 继续运行
```

看到这些字段时应这样判断：

| 字段或状态 | 含义 |
|---|---|
| `project_dirty=false` | preview 没有修改工程，不代表之后的 apply 已完成 |
| `approval_required=true` | 必须由原生审批 Host 产生一次性 token |
| `status=verified` | apply 后已完成 readback，仍需核对数量和对象 |
| `project_saved=false` | 修改只在 AEDT 内存中，尚未保存到磁盘 |
| `release_required=true` | 任务结束要释放助手 wrapper，不是关闭 AEDT |
| `target_forbidden` | 当前 MCP 会话试图访问来源端口之外的 AEDT |
| `preview_stale` | preview 后目标或 Graph 已变化，必须重新读取和预览 |

### 10.3 推荐的停止条件

出现以下任一情况就应停止，不要让 Agent 自动重试写操作：

- 工程、设计或设计类型与 AEDT GUI 不一致；
- 设计名带 `0;`，或目标设计并不存在；
- selector 命中 0 个对象或命中数量明显异常；
- preview 和审批框中的对象、变量、Setup、Sweep 或资源预算不一致；
- apply 后 `verified_count != target_count`；
- 任务失败后 rollback 状态不明确；
- 用户要求“不保存”，但 Agent 试图调用保存工具。

### 10.4 常用 MCP 工具组

| 工具组 | 代表工具 | 说明 |
|---|---|---|
| 会话 | `list_live_aedt_sessions`、`attach_live_aedt_session`、`get_live_aedt_project_info`、`release_live_aedt_session` | 发现、绑定、核对和释放 wrapper |
| HFSS 几何 | `get_live_hfss_geometry_inventory`、`preview_live_hfss_geometry_create`、`apply_live_hfss_geometry_create` | 查询 object/face，并受控创建 typed primitive batch |
| HFSS 端口 | `get_live_hfss_port_inventory`、`preview_live_hfss_boundary_create`、`apply_live_hfss_boundary_create` | 查询并受控创建 DrivenModal Wave Port 或 sheet Lumped Port |
| HFSS 表面边界 | `get_live_hfss_surface_boundary_inventory`、`preview_live_hfss_surface_boundary_create`、`apply_live_hfss_surface_boundary_create` | 查询并受控创建 Perfect E、Perfect H、Finite Conductivity、sheet Impedance 和 sheet Lumped RLC |
| HFSS 坐标系 | `get_live_hfss_coordinate_system_inventory`、`preview_live_hfss_coordinate_system_create`、`apply_live_hfss_coordinate_system_create` | 查询 Global/relative CS，并受控创建一个相对 Axis/Position 坐标系后恢复原活动 WCS |
| HFSS 材料 | `get_live_hfss_material_inventory`、`preview_live_hfss_material_create`、`apply_live_hfss_material_create`、`preview_live_hfss_material_assign`、`apply_live_hfss_material_assign` | 查询工程材料，受控创建一个数值型各向同性电磁材料，或把已有材料分配给明确 solid |
| Layout stackup 材料 | `get_live_layout_technology_inventory`、`preview_live_layout_material_create_assign`、`apply_live_layout_material_create_assign` | 原子创建一个数值型各向同性工程材料并分配给一个精确 signal/dielectric layer 字段 |
| Layout Via 创建 | `get_live_layout_technology_inventory`、`get_live_layout_connectivity_inventory`、`preview_live_layout_via_create`、`apply_live_layout_via_create` | 使用既有 padstack、signal layer 和 net 原子创建 1～32 个精确 Via |
| Layout Via 更新 | `get_live_layout_connectivity_inventory`、`preview_live_layout_via_update`、`apply_live_layout_via_update` | 对 1～32 个既有精确 Via 原子执行移动、旋转、改网或锁定更新，并验证完整原生属性边界 |
| Layout Via 删除 | `get_live_layout_connectivity_inventory`、`preview_live_layout_via_delete`、`apply_live_layout_via_delete` | 删除 1～32 个可完整重建的精确 Via，验证原生名称消失，并在失败时恢复完整属性快照 |
| HFSS 远场 | `get_live_hfss_far_field_inventory`、`preview_live_hfss_infinite_sphere_create`、`apply_live_hfss_infinite_sphere_create` | 核对辐射前置条件，并受控创建和回读有界 Infinite Sphere |
| Layout 只读 | `list_live_layout_paths`、`get_live_layout_routing_inventory`、`get_live_layout_technology_inventory`、`get_live_layout_connectivity_inventory`、`get_live_layout_port_candidate_inventory`、`get_live_layout_edge_port_candidate_inventory` | 查询 Path、技术数据库、连接关系、组件端点和 trace edge 候选 |
| 变量与 Setup | `get_live_aedt_variable_inventory`、`preview_live_aedt_variable_batch_upsert`、`apply_live_aedt_variable_batch_upsert`、`get_live_aedt_setup_inventory` | 查询变量，并按依赖顺序原子创建/更新 1～32 个 design/project variable |
| 受控写入 | `preview_live_*`、`wait_for_live_approval`、`apply_live_*` | 固定三段式；preview 和 apply 不能合并 |
| Workflow | `list_ansys_workflows`、`inspect_ansys_workflow`、`preview/apply_ansys_workflow_*` | 一步一审批地执行 YAML Graph |
| 探索 | `propose_ansys_operation`、`validate_ansys_operation`、`preview/apply_exploratory_operation` | Harness miss 后的受控兜底 |

`release_live_aedt_session` 只释放本次 PyAEDT wrapper。正常返回中 `aedt_closed=false`、
`projects_closed=false` 才符合“复用已有 AEDT”的预期。不要把 release 理解成关闭用户工程。

## 11. 示例：把 4.3mil 线宽参数化为 W_line

### 11.1 用户请求

```text
找出当前 3D Layout 设计中 LineWidth=4.3mil 的所有 line，
把它们参数化为设计变量 W_line，W_line 初值为 4.3mil。
先预览，审批后应用，回读验证，不要保存工程。
```

### 11.2 预期执行过程

推荐走已经注册的 `layout_live_parameterize_width` 严格 Workflow：

```text
attach_live_aedt_session（一次）
  -> get_live_aedt_project_info
  -> 确认 design_type = HFSS 3D Layout Design
  -> inspect_ansys_workflow("layout_live_parameterize_width")
  -> preview/apply workflow start（Graph 审批）
  -> advance: select_paths
  -> advance: preview_parameterization
  -> 展示匹配对象和 operation preview
  -> wait_for_live_approval(operation_preview_id)（修改审批）
  -> preview/apply workflow advance（Graph 审批 + operation token）
  -> advance: verify_scorecard
  -> 验证 target_count、verified_count、变量和回读表达式
  -> release_live_aedt_session
```

这个 Workflow 有两层审批。Graph step 审批只允许工作流推进一步，不能代替真正修改 AEDT 的 operation
审批。两个 token 必须分别获取，不能互换，也不能写入 Mission payload。

如果只做一次简单修改，也可以使用原子 Harness：

```text
list_live_layout_paths
  -> preview_live_parameterize_path_width
  -> wait_for_live_approval
  -> apply_live_parameterize_path_width
  -> readback
```

原子模式仍然受 preview、目标快照、原生审批和回读保护，但不提供完整 Graph 状态与 scorecard。

线宽过滤会忽略表达式中的空格和大小写差异，例如 `4.3 mil` 与 `4.3MIL`。它不会自动把所有物理等价
单位都视为同一个表达式；如果设计中同时存在 `4.3mil` 和 `0.10922mm`，应先列出结果并明确是否都要修改。

### 11.3 审批框中要检查什么

点击 `Yes` 前检查：

- 工程和设计名称正确；
- 命中对象数量合理；
- 每个对象的 name、net、layer 正确；
- 目标变量为 `W_line`；
- 初值为 `4.3mil`；
- 本次动作不包含保存工程。

点击 `No` 或等待超时后，Agent 不应自动创建新 preview。需要重新执行时由用户明确提出。

### 11.4 成功标准

只有同时满足下列条件才能报告完成：

- `status=verified`；
- `verified_count=target_count`；
- 每个目标 line 的宽度表达式回读为 `W_line`；
- 变量 `W_line` 已存在且值为 `4.3mil`；
- 没有修改其他 line；
- 工程没有被自动保存。

## 12. 查询任务示例

### 12.1 按网络和层查询走线

```text
列出当前 3D Layout 设计里 net=DDR_DQ0 且 layer=L1 的所有 line，
返回对象名和宽度表达式，只读，不要修改。
```

需要先了解可选网络、层、线宽表达式和现有变量时，可以说：

```text
请只读获取当前 3D Layout 的 routing inventory，列出 Path 数、net、layer、width expression，
以及 design/project variable；不要修改工程。
```

这会调用 `get_live_layout_routing_inventory`，适合在构造精确 selector 前使用。

如果不确定设计里到底有哪些对象类型，先调用 `get_live_layout_object_inventory`。它只读返回 component、pin、
via、net、line、polygon、rectangle、circle 和各种 void 的名称与数量；某个 PyAEDT 版本不提供某类集合时，
会在 `unavailable_categories` 中明确列出，而不是把整个设计误报为空。

Via 和 component 可以继续下钻到精确属性：

```text
只读查看 via V1 的起止层、孔径、net、位置、角度和锁定状态。
然后把 component U1 移到 [5.0, 6.0]（当前设计 model unit）、旋转到 90deg 并锁定位置；
先 preview，等我审批。
```

先调用 `get_live_layout_object_property_inventory`。当前稳定写属性 allowlist 为：

| 对象 | 可修改属性 |
|---|---|
| via | `net_name`、`location`、`angle`、`lock_position` |
| component | `enabled`、`placement_layer`、`location`、`angle`、`lock_position` |

孔径、起止层、器件料号和器件类型目前只读，不能通过通用 setter 猜测修改。写操作必须调用
`preview_live_layout_object_property_update -> wait_for_live_approval -> apply_live_layout_object_property_update`。
目标必须是显式名称列表；apply 前重新读取所有目标属性，任何对象变化都会让 preview 失效。批量更新中任意对象
readback 失败时，Harness 会尝试把所有已触及对象恢复到 preview 快照。

也可以单独查询或修改 HFSS/3D Layout 变量：

```text
先只读列出当前 Layout 的 design variable 和 project variable。然后 preview：把 design variable
W_line 创建或更新为 4.3mil。不要保存工程，等我批准后再 apply。
```

变量读取调用 `get_live_aedt_variable_inventory`。创建或更新必须走
`preview_live_aedt_variable_upsert -> wait_for_live_approval -> apply_live_aedt_variable_upsert`。
变量名只接受 AEDT 标识符，project variable 使用 `$` 前缀；apply 前会检查原值未变化，失败时恢复原值或删除
本次新建变量，成功后执行 readback，但仍不会自动保存工程。

### 12.2 批量创建或更新 AEDT 变量

需要一次设置多个相互依赖的变量时，优先使用严格 Workflow：

```text
aedt_live_variable_batch_upsert
```

例如：

```text
在当前 Layout 设计中按顺序创建 W_main=4.3mil、W_double=2*W_main，
并把 project variable $BoardScale 更新为 1.0。使用 aedt_live_variable_batch_upsert，
先展示每个变量的 create/update/noop 分类和原值；审批后原子应用并回读，不要保存工程。
```

对应 `variables` 输入为有序列表：

```json
[
  {"name": "W_main", "expression": "4.3mil"},
  {"name": "W_double", "expression": "2*W_main"},
  {"name": "$BoardScale", "expression": "1.0"}
]
```

严格执行链路为：

```text
get_live_aedt_variable_inventory
  -> inspect_ansys_workflow("aedt_live_variable_batch_upsert")
  -> preview/apply workflow start（Graph 审批）
  -> advance: preview_variables
  -> 核对 product、变量顺序、scope、create/update/noop、before/after expression
  -> wait_for_live_approval(operation_preview_id)（变量修改审批）
  -> advance: apply_variables（Graph 审批 + operation token）
  -> advance: verify_scorecard
  -> 再次读取 variable inventory
```

契约边界如下：

- `product` 只能为 `hfss` 或 `layout`，并且必须与当前设计类型匹配；
- 一批接受 1～32 个变量，名称按不区分大小写去重，project variable 使用 `$` 前缀；
- 输入顺序就是写入顺序，因此被依赖变量必须放在依赖它的表达式之前；
- 表达式必须是 1～512 字符的单行 AEDT expression；Harness 不执行 Python、COM 字符串或任意脚本；
- preview 冻结当前设计类型、solution type 以及全部 design/project variable 的名称、scope 和 expression；
  任意无关变量变化也会让 apply 返回 stale；
- 已有变量只修改 `Value`，不会用 PyAEDT 默认参数覆盖原有 Sweep、Description、ReadOnly 或 Hidden；
  新变量使用 PyAEDT 的受测默认值，其中 Layout design variable 创建为 Definition Parameter；
- `3.0mm` 回读为 AEDT 规范化的 `3mm` 仍算验证通过；普通依赖表达式则按去空格和大小写后的表达式回读；
- 全批完全相同会拒绝；部分 noop 可以保留在清单中，但不会重复写入；求解运行时拒绝修改；
- 任一变量创建或回读失败时，先恢复已有变量，再按逆序删除本批新变量，并把完整变量清单与 preview 前比较；
- 成功后 `project_saved=false`，保存仍需独立审批。

项目变量不能引用 design variable。不要把 `$Bad=W_main` 当作合法跨 scope 依赖；AEDT 会拒绝这类表达式。

该 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上同时实测 HFSS 与 HFSS 3D Layout：单变量更新、
有序 design/project variable 批量创建和更新、依赖表达式、`3.0 -> 3` 规范化回读、已有 HFSS 变量的
Sweep/Description 保留、无关变量触发 stale，以及利用 `$RollbackBad=W_main` 制造真实第二项失败后删除第一项
新变量并恢复完整清单。测试还证明已保存临时工程 SHA-256 不变，结束后没有 live session 或 AEDT 进程残留。
AEDT 2024 R2 仍必须按第 18.1 节在目标服务器复验。

### 12.3 查询 HFSS 3D 几何

```text
列出当前 HFSS 3D 设计中的对象、材料、face id 和已有 boundary，只读。
```

HFSS 3D Layout 和 HFSS 3D 是不同数据模型。设计类型为 `HFSS 3D Layout Design` 时，不应调用
HFSS 3D object/face inventory；应使用 layout path inventory。

### 12.4 查询求解状态

```text
查询当前 HFSS 设计 Setup1 的求解状态和资源信息，不启动新求解。
```

`get_live_hfss_analysis_status` 只回答运行状态。要确认 Setup/Sweep 是否已有可读取的 solution data，并取得
结果目录的有界快照，使用：

```text
调用 get_live_aedt_solution_inventory，只读检查当前 Layout 的 Setup1。
报告 setup_is_solved、target_solution_names、results file_count、latest_mtime 和 snapshot_digest。
不要因为存在旧 solution 就声称本次求解成功。
```

该工具最多扫描 20000 个结果文件，返回 `truncated` 和 `scan_error`。发生截断、访问失败或结果目录不存在时，
Harness 会保守地把新鲜度视为未验证，不会通过猜测补齐证据。

### 12.5 查询 Layout 技术数据库

需要确认层叠、过孔定义、端口顺序或差分对时，不要从对象名称猜测，调用：

```text
get_live_layout_technology_inventory(
  live_session_id,
  project_name,
  design_name,
  max_items=500,
  include_padstack_layers=false)
```

返回内容包括：

- 按 AEDT 顺序排列的 signal/dielectric/via stackup layer，以及厚度、标高、材料、fill、粗糙度和 etch；
- Padstack 的孔形、尺寸、镀层、hole range 和 layer 名称；需要 pad/antipad/thermal 细节时打开
  `include_padstack_layers`；
- AEDT 原始端口顺序和来源属性；
- 差分对的正负端、active/matched、差分/共模名称和参考阻抗；
- `snapshot_digest`、各部分计数和 `unavailable_sections`。

`max_items` 范围为 1 到 2000。读取差分对时 PyAEDT 需要短暂导出一个临时 CSV，Harness 在系统临时目录中完成
并立即删除，不修改 Layout 工程。某个 PyAEDT API 不可用时只将对应 section 标为 unavailable，不伪造空数据。

### 12.6 查询网络、器件、Pin 和 Via 连接关系

需要回答“某个器件的引脚接到哪些网络”“某个网络上有哪些器件和过孔”时，使用：

```text
get_live_layout_connectivity_inventory(
  live_session_id,
  project_name,
  design_name,
  selector={"nets": ["DDR_DQ0"], "components": ["U1"]},
  max_items=500,
  include_geometry_names=false)
```

`selector.nets` 和 `selector.components` 都是精确名称列表，不能使用通配符。只给 `nets` 时返回该网络上的
器件、Pin 和 Via；只给 `components` 时返回器件 Pin 所连接的网络及这些网络上的 Via；两者同时提供时返回
两组条件的交集。名称不存在时 Harness 直接报错，不把拼写错误伪装成空连接关系。

返回采用便于 Agent 连接和筛选的扁平结构：

- `nets`：网络名、power/ground/signal 分类、器件数、Pin 数、Via 数；
- `components`：refdes、part、part type、使能状态、放置层、位置、角度和 Pin 数；
- `pins`：Pin 名、所属器件、网络、起止层、位置和孔径；
- `vias`：Via 名、网络、起止层、位置和孔径；
- `counts`、`returned_counts`、`truncated_sections`、`unavailable_sections` 和 `snapshot_digest`。

`max_items` 范围为 1 到 2000，并分别限制四个扁平 section 的返回条数；`counts` 保留过滤后、截断前数量。
默认不枚举每个 net 的 geometry 名称，避免在大型 Layout 中触发大量 COM 查询。确有需要时设置
`include_geometry_names=true`；所有网络共享一个 `max_items` 名称预算，`geometry_status=truncated` 表示还需缩小
net selector 后重查。位置使用返回的 `model_units`。该工具只读，不修改或保存工程。

`layout_live_audit` v3 已把这项连接关系清单加入只读审计。若审计请求中的 routing selector 含 `nets`，Workflow
会自动用相同网络限制 connectivity；也可以在初始 payload 中单独提供：

```json
{
  "connectivity_selector": {
    "components": ["U1", "J1"]
  }
}
```

审计 scorecard 会报告过滤后的 net、component、pin、via 数量，以及连接清单是否截断或有不可用 section。

### 12.7 查找 Layout 端口候选

原 BRD Workflow 中的组件端点评分已经作为只读 Harness 复用到当前 AEDT 会话：

```text
get_live_layout_port_candidate_inventory(
  live_session_id,
  project_name,
  design_name,
  signal_nets=["PCIE_TX0_P", "PCIE_TX0_N"],
  reference_nets=["GND"],
  max_candidates=100)
```

Harness 会先精确验证所有 net，再从 component pin 连接关系、器件类型、封装、参考网络和空间位置评分。组件
bounding box 和 Pin 位置会从 Layout `model_units` 转成米后送入旧评分器，避免把 10 mm 误当成 10 m 或
0.01 mm。返回 `candidates`、最多两个 `recommended_endpoints`、score、confidence、reason、截断状态和快照摘要。

这个工具只提供候选事实，不授权创建端口。`status=needs_user_hint` 不代表查询失败，通常表示只找到一个端点、
候选过近或需要工程师明确目标。Agent 不得仅根据最高分自动选择写入对象；创建端口时仍要求显式
`component_name`。

### 12.8 查找 bbox 边界附近的 Trace Edge

对 cutout 或 uniform-line 端点，使用原 Workflow 的 bbox-side 算法查询每条信号网最靠近指定边界的 edge：

```text
get_live_layout_edge_port_candidate_inventory(
  live_session_id,
  project_name,
  design_name,
  signal_nets=["PCIE_TX0_P", "PCIE_TX0_N"],
  local_cut_region={
    "type": "bbox", "unit": "mm",
    "x_min": 0, "y_min": 0, "x_max": 10, "y_max": 8
  },
  side="right",
  layer="L1",
  max_candidates=100)
```

Harness 精确验证 net 和 layer，将 AEDT line edge 坐标从 `source_model_units` 转换到 bbox 的 `coordinate_unit`，
再复用旧 `find_uniform_line_edge_candidates` 排序。每个候选包含 primitive、edge number、net、layer、中点和到
bbox side 的距离。

结果状态：

- `ready`：每条信号网都有明确的最佳 edge；
- `ambiguous`：同一网络有多个距离过近的候选，需要缩小 layer/bbox 或人工选择；
- `needs_user_hint`：至少一条 signal net 没有候选；
- `incomplete`：候选数超过 `max_candidates`，结果已截断，禁止自动写入。

候选查询只读；即使 `ready`，创建前仍要冻结 primitive、edge number、端点坐标和现有 port order。

## 13. 创建和求解任务

### 13.1 受控创建 HFSS 3D 几何

原 `wave_port_setup`、`radiation_airbox_setup`、`microstrip_sparameter` 和
`dipole_antenna_s11_farfield` 都依赖同一类基础动作：先创建明确的 HFSS primitive。Assistant 已将这部分
提升为通用严格 Workflow：

```text
hfss_live_geometry_create
```

示例：一次创建 substrate、trace、via 和最后的 air region：

```json
{
  "max_new_objects": 8,
  "primitives": [
    {
      "kind": "box",
      "name": "Substrate",
      "origin": ["-25mm", "-10mm", "0mm"],
      "size": ["50mm", "20mm", "1.6mm"],
      "material": "FR4_epoxy",
      "solve_inside": true
    },
    {
      "kind": "rectangle",
      "name": "Trace",
      "orientation": "XY",
      "origin": ["-20mm", "-1mm", "1.6mm"],
      "size": ["40mm", "2mm"]
    },
    {
      "kind": "cylinder",
      "name": "Via1",
      "axis": "Z",
      "origin": ["0mm", "0mm", "0mm"],
      "radius": "0.2mm",
      "height": "1.6mm",
      "num_sides": 12,
      "material": "copper"
    },
    {
      "kind": "region",
      "name": "AirBox",
      "padding": ["10mm", "10mm", "10mm", "10mm", "10mm", "10mm"],
      "padding_type": "Absolute Offset"
    }
  ]
}
```

支持的 primitive 和必需字段：

| `kind` | 必需字段 | 可选字段 |
|---|---|---|
| `box` | `name`、三维 `origin`、三维 `size` | `material`、`solve_inside` |
| `rectangle` | `name`、`orientation`、三维 `origin`、二维 `size` | 无 |
| `cylinder` | `name`、`axis`、三维 `origin`、`radius`、`height` | `num_sides`、`material`、`solve_inside` |
| `region` | `name`、`padding` | `padding_type` |

`orientation` 只接受 `XY/YZ/XZ/ZX`，cylinder `axis` 只接受 `X/Y/Z`。数字按当前 HFSS model unit
解释；推荐显式使用带单位的 AEDT expression，例如 `1.6mm` 或设计变量 `substrate_h`。表达式长度和字符集受限，
不会传递任意 Python。每批最多 32 个对象，默认 16；名称必须唯一且不能与现有对象冲突。
`material` 和 `solve_inside` 只允许用于 solid `box/cylinder`。AEDT 的 rectangle sheet 没有可可靠
回读的体材料和 `Solve Inside` 属性，preview 会直接拒绝；需要描述 sheet 电气属性时，应使用后续 boundary workflow。

一个 batch 最多包含一个 `region`，并且必须放在列表最后，因为 region 的外包络取决于创建它时已经存在的几何。
`padding_type` 只支持 PyAEDT 公共 API 的 `Absolute Offset`、`Percentage Offset` 和
`Transverse Percentage Offset`。

Workflow 依次执行：

```text
preview_geometry（冻结完整 object/face snapshot）
  -> operation 原生审批
  -> apply_geometry（按输入顺序调用 PyAEDT 公共 modeler API）
  -> 对象名、数量、材料和 solve_inside 回读
  -> scorecard
```

任意一个 primitive 创建或回读失败时，Harness 会删除本批次已经创建的全部对象，并核对旧对象没有消失；
如果删除失败或旧对象异常，会明确报告 rollback incomplete。成功后工程保持 dirty，但默认不保存。原子调用为：

```text
preview_live_hfss_geometry_create
  -> wait_for_live_approval
  -> apply_live_hfss_geometry_create
```

该 Workflow 只负责几何创建，不会顺便分配 Boundary/Port、创建 Setup、启动求解或保存工程。需要让新几何与
Boundary/Port 成为同一失败域时，使用下一节的原子 Workflow，不要人工串联两个写操作。

### 13.2 原子创建 HFSS 几何和 Boundary/Port

当 Boundary 或 Port 依赖本次新建的 sheet、box 或 region 时，推荐严格 Workflow：

```text
hfss_live_geometry_boundary_create
```

它在一次 operation 审批和一个 backend transaction 中完成几何创建、typed face selector 解析、Boundary/Port
分配和回读。示例：为 box 的 X 最小端面创建 wave port，并为新 region 的全部外表面创建 radiation boundary：

```json
{
  "max_new_objects": 4,
  "max_new_boundaries": 4,
  "primitives": [
    {
      "kind": "box",
      "name": "PortBody",
      "origin": ["0mm", "0mm", "0mm"],
      "size": ["10mm", "5mm", "1mm"],
      "material": "vacuum"
    },
    {
      "kind": "region",
      "name": "AirRegion",
      "padding": ["5mm", "5mm", "5mm", "5mm", "5mm", "5mm"],
      "padding_type": "Absolute Offset"
    }
  ],
  "boundaries": [
    {
      "boundary_kind": "wave_port",
      "boundary_name": "P1",
      "assignment_object": "PortBody",
      "face_selector": "x_min",
      "references": [],
      "options": {}
    },
    {
      "boundary_kind": "radiation",
      "boundary_name": "Radiation1",
      "assignment_object": "AirRegion",
      "face_selector": "all_faces"
    }
  ]
}
```

每个 boundary 支持以下字段：

| 字段 | 规则 |
|---|---|
| `boundary_kind` | `radiation`、`wave_port` 或 `lumped_port` |
| `boundary_name` | 必须是未使用的安全 AEDT 名称；同批次不区分大小写去重 |
| `assignment_object` | 必须引用本次原子 batch 中创建的对象，不允许偷偷改已有对象 |
| `face_selector` | `only_face`、`all_faces`、`x_min/x_max`、`y_min/y_max`、`z_min/z_max` |
| `references` | 可选，只接受已存在对象或同批次新对象的名称 |
| `options` | 仍使用现有 boundary allowlist；radiation 不接受额外选项 |

`only_face` 只适用于回读后恰好一个面的对象，例如 rectangle sheet；如果对象有多个面会因歧义失败。
`all_faces` 只允许 radiation。轴向 extreme selector 根据真实 AEDT face center 选择唯一极值面；多个面共享同一
极值时不会猜测，而是停止并要求换 selector 或几何。wave/lumped port 的 selector 都必须先唯一解析到一个
face；Lumped Port 还必须来自本批次新建的单面 sheet，apply 会把已验证的 sheet 对象名传给 PyAEDT，而不是
错误地把 face ID 当作 geometry assignment。wave port 的 `modes` 限制为 1～16，`deembed` 必须是非负有限数；
lumped port 的 `deembed` 是布尔值。两类 port 的 `renormalize` 均为布尔值，`integration_line` 只接受轴枚举
整数 0～5，或两个由有限数构成的三维点。

执行链路为：

```text
preview_geometry_boundaries（冻结 geometry、boundary name、model unit）
  -> Windows 原生 operation 审批
  -> 创建全部 primitive
  -> 回读新对象和 face
  -> 解析 face selector
  -> 创建并回读全部 Boundary/Port
  -> scorecard
```

任意 primitive、selector、Boundary/Port 创建或回读失败时，Harness 先删除本批次 boundary，再删除本批次几何，
并检查旧 boundary 和旧对象没有变化；任一清理不完整都会报告 rollback incomplete。成功后工程 dirty 但不保存。
直接 MCP 调用链为：

```text
preview_live_hfss_geometry_boundary_create
  -> wait_for_live_approval
  -> apply_live_hfss_geometry_boundary_create
```

这项能力已在隔离 AEDT 2026 R1 上实测 box、rectangle sheet、region、`x_min` wave port、sheet Lumped Port、
`all_faces` radiation、回读、
stale 拒绝和工程文件摘要不变。部分写入失败的双层 rollback 已由 fault-injection unit test 覆盖；当前尚未找到
不会污染环境且跨 AEDT 版本稳定的真实失败注入方式，因此不把它冒充真实 rollback 证据。目标服务器是 AEDT
2024 R2 时，仍必须按第 18.1 节在目标版本复验。

### 13.3 原子创建 HFSS Setup 和 Sweep

当新 Setup 和频率 Sweep 必须成对出现时，优先使用严格 Workflow：

```text
hfss_live_setup_sweep_create
```

不要让 Agent 先调用一次 Setup create、再单独调用 Sweep create。后一步失败会留下不完整 Setup，而且两次
preview 之间的设计状态可能已经变化。原子 Workflow 在一次 operation 审批和一个 backend transaction 中完成
Setup、Sweep、回读和失败清理。

推荐直接对 Claude Code 说：

```text
在当前 HFSS 设计中，用 hfss_live_setup_sweep_create 创建 AtomicSetup 和 AtomicSweep。
Setup 类型 HFSSDriven，Frequency=10GHz，MaximumPasses=3，MinimumPasses=1，
MaxDeltaS=0.05；Sweep 使用 1GHz 到 20GHz、101 点、Interpolating、保存场。
先显示 preview 和当前已有 Setup/Port，审批后才 apply；回读名称、属性和 Sweep，默认不要保存工程。
```

Workflow 初始 payload：

```json
{
  "setup": {
    "name": "AtomicSetup",
    "type": "HFSSDriven",
    "properties": {
      "Frequency": "10GHz",
      "MaximumPasses": 3,
      "MinimumPasses": 1,
      "MaxDeltaS": 0.05
    }
  },
  "sweep": {
    "name": "AtomicSweep",
    "range_type": "LinearCount",
    "sweep_type": "Interpolating",
    "unit": "GHz",
    "start_frequency": 1,
    "stop_frequency": 20,
    "count": 101,
    "save_fields": true
  }
}
```

Setup 约束：

| 字段 | 规则 |
|---|---|
| `name` | 安全且尚未使用的 AEDT 名称 |
| `type` | `HFSSDriven` 或 `HFSSDrivenAuto` |
| `Frequency` | 有界 AEDT expression，例如 `10GHz` 或已存在设计变量 |
| `MaximumPasses`、`MinimumPasses` | 1～1000，且 Minimum 不大于 Maximum |
| `MinimumConvergedPasses` | 0～1000 |
| `MaxDeltaS` | 大于 0 且不大于 1 |
| `PercentRefinement` | 大于 0 且不大于 100 |
| `BasisOrder` | `-1`、`1` 或 `2` |

Sweep 约束：

| 字段 | 规则 |
|---|---|
| `name` | 安全且在目标 Setup 中未使用的 AEDT 名称 |
| `range_type` | `LinearCount` 或 `LinearStep` |
| `sweep_type` | `Discrete`、`Interpolating` 或 `Fast` |
| `unit` | `Hz`、`kHz`、`MHz`、`GHz` 或 `THz` |
| `start_frequency`、`stop_frequency` | 正有限数，且 stop 大于 start |
| `count` | LinearCount 必填，2～100001 |
| `step_size` | LinearStep 必填，正有限数；估算点数不能超过 100001 |
| `save_fields` | 明确的布尔值 |

`Interpolating` 和 `Fast` 需要当前设计中至少已有一个 Port。Harness 会在 preview 阶段检查，不会把已知必失败
请求交给 AEDT；`Discrete` 不受此限制。preview 同时冻结现有 Setup 名称、Port 名称、目标设计身份和操作参数。
审批后如果有人新增或删除 Setup/Port，apply 会以 stale preview 拒绝，要求重新 preview。

执行链路：

```text
preview_setup_sweep_create（只读，冻结 Setup/Port snapshot）
  -> Windows 原生 operation 审批
  -> create_setup
  -> create_frequency_sweep
  -> 回读 Setup 类型、属性和 Sweep 名称
  -> scorecard
```

直接 MCP 调用链为：

```text
preview_live_hfss_setup_sweep_create
  -> wait_for_live_approval
  -> apply_live_hfss_setup_sweep_create
```

成功结果必须同时满足 `status=verified`、名称保持、Setup 属性回读一致、Sweep 出现在 Setup inventory、
`atomic_setup_sweep_transaction=true`、`project_saved=false`。Sweep 创建或回读失败时，Harness 会删除本次新建的
Setup；清理不完整会明确报告 rollback incomplete。成功后内存中的工程会变 dirty，但不会自动保存；保存仍需
另一次 `preview_live_project_save`、原生审批和 apply。

这项 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测 `HFSSDriven + Interpolating` 创建、关键属性和
Sweep 回读、stale 拒绝、无 Port 预检以及工程文件 SHA-256 不变。失败 rollback 由确定性 fault-injection unit
test 覆盖；真实 AEDT 的无 Port Interpolating 错误会污染该 gRPC 会话，因此采用 preview 拒绝而不把它当作真实
rollback 注入。部署到 AEDT 2024 R2 时必须按第 18.1 节在目标机复验。

### 13.4 创建 HFSS 数值型各向同性电磁材料

当前工程缺少所需材料定义时，使用严格 Workflow：

```text
hfss_live_material_create
```

推荐对话：

```text
在当前 HFSS 设计中创建材料 HarnessLaminate：相对介电常数 4.2、相对磁导率 1.01、
电导率 0.005S/m、介质损耗角正切 0.018、磁损耗角正切 0.002，外观为
RGB(10,20,30)、透明度 0.4。使用 hfss_live_material_create；先读取完整工程材料目录，
preview 后等我审批，apply 后逐项 typed readback。不要分配给对象，不要保存工程。
```

Workflow 初始 payload：

```json
{
  "material_name": "HarnessLaminate",
  "permittivity": 4.2,
  "permeability": 1.01,
  "conductivity": 0.005,
  "dielectric_loss_tangent": 0.018,
  "magnetic_loss_tangent": 0.002,
  "appearance": [10, 20, 30, 0.4]
}
```

当前版本只接受可确定回读的数值型各向同性电磁属性：

| 字段 | 规则 |
|---|---|
| `material_name` | 1～128 个安全 AEDT 字符；不能与当前工程或 AEDT 系统/个人/用户材料库名称大小写不敏感地冲突 |
| `permittivity`、`permeability` | 有限数，范围 `1e-12`～`1e9`，默认 `1.0` |
| `conductivity` | 有限数，范围 `0`～`1e12`，默认 `0.0` |
| 两个 loss tangent | 有限数，范围 `0`～`1e6`，默认 `0.0` |
| `appearance` | 可省略；提供时必须是 `[R,G,B,transparency]`，RGB 为 0～255 整数，透明度为 0～1 |
| 求解状态 | AEDT 正在求解时拒绝 |
| 保存 | apply 只修改内存中的工程，默认不保存 |

当前 Harness 不接受表达式、频散模型、各向异性张量、非线性、温度依赖、铁磁磁滞、热学或机械属性。
这些能力不是通过字符串塞进现有接口，而应分别增加有明确 schema、API 证据和真实 AEDT readback 的 Harness。

执行链路：

```text
get_live_hfss_material_inventory
  -> preview_live_hfss_material_create（完整工程材料目录快照）
  -> Windows 原生 operation 审批
  -> PyAEDT Materials.add_material
  -> 可选 material_appearance 更新
  -> 回读五个 simple property、介质分类、外观和 definition digest
  -> scorecard
```

apply 前会重新读取 AEDT Definition Manager 的真实 project material names，而不只相信 PyAEDT 的旧缓存。
如果 preview 后另一个 wrapper 或人工操作新增/删除材料，会返回 `stale HFSS material create preview`；这项检查
已在真实 AEDT 中验证，能识别其他 PyAEDT wrapper 新增但当前 wrapper 缓存尚未刷新的材料。

成功结果必须同时满足 `status=verified`、名称精确一致、五个属性均以 `type=simple` 回读且数值一致、
指定外观回读一致、definition digest 非空、`project_saved=false`。创建或回读失败时，Harness 只删除本次新增
材料，再比较完整工程材料目录 digest；目录没有恢复时明确报告 rollback incomplete。

创建材料和把材料分配给几何是两个独立写操作。需要二者时，先完成本 Workflow 的审批和回读，再使用下一节
`hfss_live_material_assign`，不能用一次审批同时扩大为“创建并分配”。

这项 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测：介质与高电导率导体分类、五个电磁属性和外观 typed readback、
preview 无修改、其他 wrapper 新增材料后的 stale 拒绝、工程/系统库重名和非法参数拒绝、已保存临时工程 SHA-256 不变；
并在材料真实创建之后注入 readback 失败，确认新材料被删除且完整目录 digest 恢复。测试结束后无 live session、
live worker 或 AEDT 进程残留。部署到 AEDT 2024 R2 时仍必须按第 18.1 节在目标机复验。

### 13.4A 创建并分配 3D Layout stackup 材料

需要在当前 HFSS 3D Layout 中新增一个工程材料，并立即用于一个明确的 stackup layer 时，使用严格 Workflow：

```text
layout_live_material_create_assign
```

推荐对话：

```text
在当前 3D Layout 工程中创建材料 HarnessLayoutLaminate：permittivity=3.7、
permeability=1.0、conductivity=0.001S/m、dielectric_loss_tangent=0.012。
把它分配给精确层 D1 的 material 字段。使用 layout_live_material_create_assign；
先展示完整材料目录和 stackup 快照摘要，等我审批后原子 apply，回读材料定义和层字段，
失败时恢复层并删除新材料，不要保存工程。
```

支持的层角色：

| 层类型 | `assignment_field` | 要求 |
|---|---|---|
| `dielectric` | `material` | 新材料必须按 PyAEDT 100000 S/m 阈值分类为介质 |
| `signal` | `fill_material` | 新材料必须分类为介质 |
| `signal` | `material` | 新材料必须分类为导体 |

不支持 via 或 drawing layer，也不允许给 dielectric 层写 `fill_material`。层名必须精确匹配大小写，材料名不能与
当前工程或 AEDT 系统/个人/用户材料库名称冲突。当前版本只接受与 HFSS 材料创建相同的五个数值型 simple
电磁属性和可选 `[R,G,B,transparency]` 外观，不接受色散、各向异性或任意材料脚本。

preview 冻结完整 Definition Manager 工程材料目录、完整 stackup layer 顺序及每层 ID/type/material/fill/厚度等
属性。apply 前任何材料或 stackup 变化都会返回 stale。成功时只允许出现一个新材料和一个目标层字段变化；
其余 stackup 字段发生变化会触发失败。PyAEDT `ChangeLayer` 对 `Thickness0` 的 base-unit 文本重排按 typed
物理值比较，不按原始字符串误判；signal 层写入时 wrapper 可能覆盖的显示颜色会被 Harness 恢复并做原生回读。

失败回滚顺序固定为：先恢复目标层原字段，再删除本次新材料，使 PyAEDT 材料名称缓存失效，最后比较完整材料
目录和完整 stackup digest。这样同名材料在完整回滚后可以重新 preview，不会被 wrapper 的旧缓存误报成材料库冲突。

该 Harness 已在隔离 AEDT 2026.1 + PyAEDT 1.3.0 中真实验证 dielectric `material`、signal
`fill_material`、signal 导体 `material`、系统库冲突、其他 wrapper 外部新增材料后的 stale、已保存工程
SHA-256 不变，以及真实分配完成后的故障注入全回滚。测试结束后 `ansysedt`、live session 和 worker 均无残留。
目标 AEDT 2024 R2 仍需在部署服务器单独复验。

### 13.4B 批量创建 3D Layout Via

需要在既有 Layout 技术数据库上新增一批明确 Via 时，使用严格 Workflow：

```text
layout_live_via_create
```

推荐对话：

```text
在当前 3D Layout 中创建 HarnessVia1 和 HarnessVia2，均使用现有 PlanarEMVia padstack，
从 TOP 到 BOT，并连接到现有 N_EXISTING。位置分别是 [1.0,2.0]mm 和 [3.0,4.0]mm；
第一个旋转 45deg、孔径 override 为 0.25mm 并锁定，第二个旋转 -30deg、使用 padstack 默认孔径。
使用 layout_live_via_create。先 preview 全部目标和依赖，等我审批后原子创建并做原生属性回读；
任意一项失败时删除本批全部新 Via。不要保存工程。
```

`vias` 是 1～32 个对象的列表。每项 schema：

| 字段 | 必需 | 说明 |
|---|---|---|
| `name` | 是 | 必须在整个 Layout 中不存在，并精确保留大小写 |
| `padstack` | 是 | 当前设计已存在的精确 padstack 名 |
| `x`、`y` | 是 | 有限数值，单位为当前 `model_units` |
| `top_layer`、`bottom_layer` | 是 | 两个不同的既有 signal layer |
| `net_name` | 是 | 已存在的精确 net 名；Harness 不隐式创建 net |
| `rotation_degrees` | 否 | 有限角度，默认 `0`，允许负值 |
| `hole_diameter` | 否 | 正数，单位为 `model_units`；省略时保持 padstack 默认孔径 |
| `lock_position` | 否 | 布尔值，默认 `false` |

preview 会冻结设计类型、solution type、model unit、完整 stackup、所选 padstack definition、signal layer、net，
并通过 AEDT 原生 `FindObjects("Name", ...)` 确认所有名称尚未占用。apply 前任一依赖变化或外部同名对象出现，
都返回 stale，不会删除外部对象。

成功回读直接来自 AEDT `BaseElementTab`，包含 Type、Name、Net、Padstack Definition、Start/Stop Layer、
Location、Angle、LockPosition、OverrideHoleDiameter 和 HoleDiameter，并为完整原生属性生成 digest。PyAEDT 1.3.0
的 `create_via(rotation=...)` 在 AEDT 2026.1 实测不会可靠写入 Angle，所以 Harness 创建后显式设置公开 `angle`
属性，再按模 360 规则验证。回滚删除后不信任 PyAEDT `_vias` cache，而以 AEDT 原生对象查找确认名称消失。

该 Harness 已在隔离 AEDT 2026.1 + PyAEDT 1.3.0 中验证双 Via 成功、正负旋转、孔径 override/默认孔径、
锁定状态、外部同名对象 stale、磁盘工程 SHA-256 不变，以及真实创建后的故障注入全批回滚。目标 AEDT
2024 R2 上线前仍需按第 18.1 节执行同一真实准入。

### 13.4C 批量更新已有 3D Layout Via

需要移动、旋转、改网或锁定一批已经存在的 Via 时，使用严格 Workflow：

```text
layout_live_via_update
```

推荐对话：

```text
在当前 3D Layout 中更新 V1 和 V2。V1 改到 N2、位置 [5.0,6.0]mm、旋转 45deg 并锁定；
V2 改到位置 [-2.0,8.0]mm、旋转 -30deg，保持它原来的锁定状态。
使用 layout_live_via_update。先冻结完整 stackup、net 目录和两个目标的全部原生属性；
审批后原子 apply，只允许请求的 Net/Location/Angle/LockPosition 改变。失败时恢复完整目标快照。
不要保存工程。
```

`updates` 是 1～32 个对象的列表。每项 schema：

| 字段 | 必需 | 说明 |
|---|---|---|
| `name` | 是 | 已存在的精确 Via 名，必须保留大小写且批内不重复 |
| `net_name` | 否 | 已存在的精确 net 名；不隐式创建 net |
| `location` | 否 | `[x,y]` 两个有限数值，单位为当前 `model_units` |
| `rotation_degrees` | 否 | 有限数值，允许负值，回读按模 360 比较 |
| `lock_position` | 否 | 最终锁定状态 |

除 `name` 外至少提供一个字段；若一项的所有请求值都与当前状态语义相同，preview 会拒绝 no-op。preview 冻结
设计类型、solution type、model unit、完整 stackup、完整 net 名称目录，以及每个目标的全部
`BaseElementTab` 属性。apply 前目标、依赖或任一原生属性变化都返回 stale。

对于锁定且需要移动/旋转的 Via，Harness 先临时解锁，再设置 net、location 和 angle，最后恢复原锁定状态或
写入用户明确指定的最终状态。readback 只允许请求对应的 `Net`、`Location`、`Angle`、`LockPosition` 发生
变化；Name、Type、Padstack Definition、Start/Stop Layer、OverrideHoleDiameter、HoleDiameter 和所有其他
原生属性必须保持一致。失败时按相反顺序恢复所有已触碰 Via，并要求完整原生 snapshot digest 与 preview 相同。
如果本批把旧源网络上的最后一个对象移走，AEDT 可以自动删除该空网络；Harness 只允许本批实际发生改网的
旧源网络消失，禁止新增网络或删除无关网络。rollback 必须把被恢复 Via 的旧网络关系和完整预览快照一起恢复。

该 Harness 已在隔离 AEDT 2026.1 + PyAEDT 1.3.0 中验证双 Via 成功、锁定 Via 临时解锁、正负旋转、改网、空旧源网络清理、
外部修改 stale、磁盘工程 SHA-256 不变，以及真实写入后的故障注入完整回滚。目标 AEDT 2024 R2 上线前仍需
按第 18.1 节执行同一真实准入。

### 13.4D 严格批量删除已有 3D Layout Via

需要删除一批明确的普通 Via 时，使用严格 Workflow：

```text
layout_live_via_delete
```

推荐对话：

```text
删除当前 3D Layout 中精确名称为 V_BAD1、V_BAD2 的 Via。使用 layout_live_via_delete；
先冻结完整 stackup、padstack、net 目录和目标的全部 BaseElementTab 属性，只接受可以完整重建的普通 Via。
原生审批后逐项删除并用 FindObjects 验证名称不存在。任一步失败时重建本批已删除目标并比较完整原生快照。
成功后保持内存中的删除状态，但不要保存工程。
```

初始 payload：

```json
{
  "names": ["V_BAD1", "V_BAD2"],
  "max_vias": 4
}
```

`names` 必须包含 1～32 个精确、大小写匹配且不重复的既有 Via。preview 读取设计类型、solution type、
model unit、完整 stackup、所有目标使用的完整 padstack definition、完整 net 名称目录，以及目标的全部
`BaseElementTab` 属性。apply 前任何目标或依赖变化都返回 stale。

删除前会检查 Via 可重建性。当前允许普通 padstack Via，包括锁定、任意有限旋转、孔径 override、默认孔径和
无网络状态；带自定义 `Backdrill Top/Bottom` 或非零 `Top/Bottom Offset` 的 Via 会在 preview 阶段拒绝，因为
PyAEDT 1.3.0 的公开 `create_via` 不能完整表达这些加工属性。出现未知原生字段也拒绝，不能以“多数属性相同”
冒充可回滚。

apply 使用 AEDT 原生 Delete 逐项删除，清理 PyAEDT `_vias` cache，并用原生 `FindObjects` 验证全部名称消失。
成功返回的 `deleted_names` 必须与请求顺序一致，同时提供 `absence_digest`。只允许目标 Via 的旧源网络在变空时
由 AEDT 删除，禁止新增或删除无关网络。

事务失败时，只重建已经确认删除的名称。重建使用 preview 中的 padstack、起止 signal layer、net、位置、角度、
锁定、孔径 override 和孔径，再逐字比较完整原生属性。若外部对象在 rollback 前抢占同名，Harness 不覆盖、
不删除外部对象，而返回 rollback incomplete。成功删除不会自动撤销；保存仍走独立 preview 和审批。

该 Harness 已在隔离 AEDT 2026.1 + PyAEDT 1.3.0 中验证三目标成功删除、锁定/孔径 override、正负旋转、
无网络 Via、空源网络清理、外部 stale、磁盘工程 SHA-256 不变，以及三类 Via 真实删除后的完整重建回滚。
目标 AEDT 2024 R2 上线前仍需按第 18.1 节执行同一真实准入。

### 13.5 为已有 HFSS Solid 批量分配材料

需要把已经存在的 HFSS solid 明确改成某种工程材料时，使用严格 Workflow：

```text
hfss_live_material_assign
```

推荐对话：

```text
在当前 HFSS 设计中，把 MaterialTarget1 和 MaterialTarget2 的材料改成 copper。
使用 hfss_live_material_assign；先读取对象 inventory，确认它们都是 solid，并显示当前材料、
Solve Inside、对象 ID 和目标材料摘要。preview 后等我审批，apply 后回读材料和 Solve Inside，
不要保存工程。
```

Workflow 初始 payload：

```json
{
  "object_names": ["MaterialTarget1", "MaterialTarget2"],
  "material_name": "copper",
  "max_objects": 4
}
```

当前契约有意保持严格：

| 项目 | 规则 |
|---|---|
| `object_names` | 1～32 个显式、精确、大小写匹配且不重复的 HFSS 对象名 |
| 对象类型 | 只支持真实体积大于 0 的 solid；sheet/rectangle 在 preview 阶段拒绝 |
| `material_name` | 必须已存在于当前工程的 PyAEDT material catalog，不隐式导入或创建材料 |
| 已使用目标材料的对象 | 拒绝，避免一次“看似无变化”的分配重置 `Solve Inside` 或外观 |
| 求解状态 | AEDT 正在求解时拒绝 |
| 保存 | apply 只修改内存中的工程，默认不保存 |

如果目标材料只存在于 AEDT 系统库、尚未进入当前工程材料目录，可以先在 AEDT Material Manager 中显式添加并复核；
需要自定义数值型各向同性材料时，先单独执行上一节 `hfss_live_material_create`。材料分配 Harness 本身不会因为
Agent 拼出一个材料名就偷偷创建材料定义。
使用 `get_live_hfss_material_inventory` 可以先有界列出当前工程材料目录；返回 canonical name、导体/介质分类、
主要电磁属性、材料定义 digest、总数和是否截断，全程只读。

preview 会冻结每个目标的精确名称、object ID、原材料、`Solve Inside`、颜色、透明度、bounding box、volume，
并冻结目标材料的 canonical name、导体/介质分类、主要电磁属性和材料定义 digest。apply 前任一目标或材料定义变化，
都会返回 `stale HFSS material assignment preview`，要求重新审阅。

执行链路：

```text
preview_material_assignment
  -> Windows 原生 operation 审批
  -> PyAEDT Hfss.assign_material（单批最多 32 个对象）
  -> 逐对象回读 material_name 和 Solve Inside
  -> scorecard
```

直接 MCP 调用链：

```text
get_live_hfss_material_inventory
  -> get_live_hfss_geometry_inventory
  -> preview_live_hfss_material_assign
  -> wait_for_live_approval
  -> apply_live_hfss_material_assign
```

材料分配会按 PyAEDT/AEDT 材料分类同步更新 `Solve Inside`，并可能让对象外观跟随材料；这不是隐藏副作用，preview
和 capability catalog 会明确报告。成功结果必须满足 `status=verified`、`verified_count=target_count`、每个对象
材料回读一致、每个对象 `Solve Inside` 与目标材料分类一致、`project_saved=false`。

任一分配或回读失败时，Harness 会逐对象恢复原材料、原 `Solve Inside`、颜色和透明度，然后重新回读；无法完整
恢复时明确报告 rollback incomplete。部分写入 rollback 已由 deterministic fault-injection unit test 覆盖。

这项 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测两个 vacuum solid 原子改为 copper、
`Solve Inside=False` 回读、外部材料变更 stale、sheet/未知材料预检、已保存临时工程 SHA-256 不变，以及测试后
无 live session 和 AEDT 进程残留。真实 AEDT 的稳定部分写入故障注入仍未找到，因此不把 unit rollback 冒充
真实 rollback 证据。部署到 AEDT 2024 R2 时必须按第 18.1 节在目标机复验。

### 13.6 为明确 HFSS Solid 创建 Length Based Mesh

需要在求解前对一组明确 solid 施加局部长度网格约束时，使用严格 Workflow：

```text
hfss_live_length_mesh_create
```

推荐对话：

```text
在当前 HFSS 设计中，为 TraceBody 和 ViaBody 创建名为 SignalLengthMesh 的 Length Based
Mesh Operation。Refine Inside=true，最大长度 0.2mm，最大单元数 200000。
先列出现有 Mesh Operation 和目标 solid 的精确清单，preview 后等我审批；apply 后回读
Type、Assignment、Region、Max Length 和 Max Elems，不要保存工程。
```

Workflow 初始 payload：

```json
{
  "mesh_name": "SignalLengthMesh",
  "object_names": ["TraceBody", "ViaBody"],
  "inside_selection": true,
  "maximum_length": "0.2mm",
  "maximum_elements": 200000,
  "max_objects": 8
}
```

输入约束：

| 字段 | 规则 |
|---|---|
| `mesh_name` | 安全且尚未使用的精确 AEDT Mesh Operation 名称；不允许 PyAEDT 静默改名 |
| `object_names` | 1～32 个显式、大小写匹配、不重复的 HFSS solid 名称 |
| `inside_selection` | 明确布尔值；`true` 对应 Inside Selection，`false` 对应 On Selection |
| `maximum_length` | 正长度 expression，例如 `0.2mm` 或已审核设计变量；纯数字必须显式带单位；可为 `null` 关闭该限制 |
| `maximum_elements` | 1～10000000 的整数；可为 `null` 关闭该限制 |
| 两项限制 | `maximum_length` 和 `maximum_elements` 不能同时为 `null` |

当前 Harness 只接受 solid object assignment，不接受 sheet 或自由 face ID。sheet 的表面网格、skin-depth mesh、
surface approximation 和 face-based length mesh 是不同电磁语义，不能借用本 Workflow 的审批。Agent 遇到这些
需求应明确报告尚无对应 Harness，而不是偷偷改成 object length mesh。

先调用 `get_live_hfss_mesh_inventory` 可以有界读取现有 Mesh Operation；返回 operation name、type、对象清单、
Inside/On Selection、Enabled、长度/单元限制、属性 digest、总数和截断状态，全程只读。

preview 会冻结目标 object ID、材料、`Solve Inside`、bounding box、volume、外观，以及当前全部 Mesh Operation
的规范化属性。apply 前目标几何或任一 Mesh Operation 变化，会返回 `stale HFSS length mesh create preview`。
设计中已有超过 500 个 operation 时，写入 preview 会拒绝，因为无法在当前有界契约内完整冻结状态。

执行链路：

```text
get_live_hfss_mesh_inventory
  -> preview_length_mesh
  -> Windows 原生 operation 审批
  -> PyAEDT Mesh.assign_length_mesh
  -> 清空 PyAEDT mesh cache 后从 AEDT Object-Oriented tree 重新回读
  -> scorecard
```

直接 MCP 调用链：

```text
get_live_hfss_geometry_inventory
  -> get_live_hfss_mesh_inventory
  -> preview_live_hfss_length_mesh_create
  -> wait_for_live_approval
  -> apply_live_hfss_length_mesh_create
```

成功必须满足 operation 名称未被改写、`Type=Length Based`、对象清单和顺序一致、Region/Enabled 正确、启用的
Max Length/Max Elems 与请求一致、仅新增一个 operation、`project_saved=false`。创建或回读失败时 Harness 删除
本次 operation，并确认全部旧 Mesh Operation 完整恢复；否则报告 rollback incomplete。

这项 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测两个 solid 的 Length Based operation 创建、
重新连接式属性回读、外部新增 operation stale、重复名和 sheet 拒绝、已保存临时工程 SHA-256 不变，以及测试后
无 live session/AEDT 进程残留。确定性 readback fault injection unit test 覆盖创建后回滚；真实 AEDT 尚无稳定且
无污染的部分写入故障注入，因此不把 unit rollback 冒充真实 rollback 证据。AEDT 2024 R2 仍需按第 18.1 节复验。

### 13.7 创建 HFSS Infinite Sphere 远场设置

天线或辐射问题需要定义远场角度网格时，使用严格 Workflow：

```text
hfss_live_infinite_sphere_create
```

推荐对话：

```text
在当前 HFSS 设计中创建名为 AntennaSphere 的 Infinite Sphere，使用 Theta-Phi 定义；
Theta 从 -90deg 到 90deg、步长 5deg，Phi 从 0deg 到 360deg、步长 10deg；
使用 Slant polarization，角度 45deg。先检查 Radiation/PML/Hybrid 前置条件和现有
Field Setup，限制总采样点不超过 5000；preview 后等我审批，apply 后完整回读，不要保存工程。
```

Workflow 初始 payload：

```json
{
  "sphere_name": "AntennaSphere",
  "definition": "Theta-Phi",
  "angle1_start": -90,
  "angle1_stop": 90,
  "angle1_step": 5,
  "angle2_start": 0,
  "angle2_stop": 360,
  "angle2_step": 10,
  "units": "deg",
  "polarization": "Slant",
  "polarization_angle": 45,
  "max_samples": 5000
}
```

`angle1`、`angle2` 的物理含义由 definition 明确决定：

| `definition` | `angle1` | `angle2` |
|---|---|---|
| `Theta-Phi` | Theta | Phi |
| `El Over Az` | Azimuth | Elevation |
| `Az Over El` | Elevation | Azimuth |

角度输入必须是有限数值，单位统一为 `deg` 或 `rad`；每条轴都要求 `stop > start`、`step > 0` 且
步长不大于跨度。Harness 计算两轴包含起点的最大网格数量并检查乘积不超过 `max_samples`；默认上限
200000，调用者可收紧但不能放宽到 1000000 以上。当前只允许 `Global` coordinate system，不接受自定义
radiation surface、自由角度表达式或自动选择某个局部坐标系。

AEDT 只有在设计已经存在 Radiation、PML 或 free-standing hybrid region 时才能计算 radiated fields。
`get_live_hfss_far_field_inventory` 会只读返回 Field Setup 清单、属性 digest、可用辐射边界、solution type、
`creation_ready` 和 blockers。没有前置边界时，本 Harness 明确拒绝；如确实需要新建 Region 和 Radiation，
先单独使用 `hfss_live_geometry_boundary_create`，完成它自己的 preview、审批和回读，不能把两个写操作合并到
一次远场审批中。EigenMode 和 CharacteristicMode 也会在 preview 阶段拒绝。

执行链路：

```text
get_live_hfss_far_field_inventory
  -> preview_infinite_sphere
  -> 冻结 solution type、全部 Boundary 和 Field Setup
  -> Windows 原生 operation 审批
  -> PyAEDT insert_infinite_sphere
  -> 从 AEDT Radiation Object-Oriented tree 重新回读
  -> scorecard
```

直接 MCP 调用链：

```text
get_live_hfss_far_field_inventory
  -> preview_live_hfss_infinite_sphere_create
  -> wait_for_live_approval
  -> apply_live_hfss_infinite_sphere_create
```

成功必须满足名称未被 PyAEDT 静默改写、Type 为 Infinite Sphere、definition 和两条物理角度轴一致、六个
角度边界/步长带正确单位、coordinate system 为 Global、polarization 回读一致、只新增一个 Field Setup、
`project_saved=false`。apply 前任何 Boundary 或 Field Setup 变化都会触发 stale 拒绝。创建或回读失败时
Harness 删除本次 Field Setup，并确认旧清单和属性 digest 完整恢复；否则报告 rollback incomplete。

这项 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测 `Theta-Phi`、`El Over Az`、`Az Over El`、
degree/radian、Linear/Slant 创建和重新连接式属性回读，并覆盖外部新增 Field Setup stale、重复名拒绝、
已保存临时工程 SHA-256 不变以及测试后无 live session/AEDT 进程残留。真实 AEDT 首次探测还证实缺少
Radiation/PML/Hybrid 时 AEDT 会直接拒绝创建；确定性 readback fault injection unit test 覆盖创建后回滚。
AEDT 2024 R2 仍需按第 18.1 节在目标服务器复验。

### 13.8 创建 HFSS 表面边界

需要为已有 HFSS 几何分配常用电磁表面边界或 sheet 等效 RLC 时，使用严格 Workflow：

```text
hfss_live_surface_boundary_create
```

推荐先让 Agent 做只读 inventory，再描述一个边界。例如：

```text
在当前 HFSS 设计中，只读列出对象、face ID 和现有表面边界。然后在面 1234 上创建名为
TraceCopper35um 的 Finite Conductivity，材料使用当前工程已有的 copper，启用厚度 35um，
粗糙度 0.5um，非无限地、非双面、内部边界。先 preview 并展示目标面、材料定义和旧边界清单；
我批准后再 apply，回读全部属性，不要保存工程。
```

对应 Workflow 初始 payload：

```json
{
  "boundary_kind": "finite_conductivity",
  "boundary_name": "TraceCopper35um",
  "face_ids": [1234],
  "options": {
    "material_name": "copper",
    "use_thickness": true,
    "thickness": "35um",
    "roughness": "0.5um",
    "is_infinite_ground": false,
    "is_two_sided": false,
    "is_internal": true,
    "is_shell_element": false
  },
  "max_assignments": 8
}
```

当前 Harness 支持五类明确语义：

| `boundary_kind` | 可用 selector | 关键 options |
|---|---|---|
| `perfect_e` | `object_names` 或 `face_ids` | `is_infinite_ground` |
| `perfect_h` | `object_names` 或 `face_ids` | 无 |
| `finite_conductivity` | `object_names` 或 `face_ids` | `material_name`、厚度、粗糙度、无限地、双面、内部和 shell 选项 |
| `impedance` | 只允许显式 sheet `object_names` | `resistance`、`reactance`、`is_infinite_ground` |
| `lumped_rlc` | 只允许一个显式 planar sheet `object_names` | `rlc_type`、`integration_line_direction`、可选 R/L/C 数值 |

输入和物理约束：

- `object_names` 与 `face_ids` 必须二选一，不能同时提供；每次最多 64 个显式 assignment，不做模糊名称猜测；
- `impedance` 只接受 sheet 对象，不能传 solid 或 face ID；当前支持 Modal/Terminal、Driven Modal/Terminal、
  Transient 和 EigenMode 等 PyAEDT/AEDT 回读名称；
- `lumped_rlc` 只接受一个 planar sheet，不能传 solid、face ID 或多个对象。`rlc_type` 只能是
  `Parallel`/`Serial`，integration line 方向只能是 `XNeg/YNeg/ZNeg/XPos/YPos/ZPos`；preview 会从 sheet
  几何解析并展示实际 Start/End，方向反转会改变等效电流正方向，审批时必须核对；支持 Modal/Terminal、
  Transient、SBR 和 EigenMode 对应的 AEDT/PyAEDT solution type 回读名称；
- Lumped RLC 的 `resistance`、`inductance`、`capacitance` 分别按 Ω、H、F 解释，至少启用一项且必须是
  正有限数值。Harness 不接受无单位猜测后的任意表达式，也不会把 `0` 偷偷解释为禁用；未提供字段才表示禁用；
- `is_infinite_ground=true` 只允许已确认的平面 sheet 对象或 planar face。三维实体对象和曲面会在 preview
  阶段拒绝，不把 AEDT 的 `selection must be planar` 错误留到 apply；
- Finite Conductivity 的 `material_name` 必须已存在于当前工程 material catalog。API Memory 中能搜到
  `copper` 不等于该材料已进入当前工程；先用 `get_live_hfss_material_inventory` 核对；
- `thickness` 和 `roughness` 必须是带显式单位的受限 AEDT expression，例如 `35um`、`0.5um`；
  纯数字 `35` 会拒绝，避免把模型单位误当成微米；
- boundary 名称必须尚未使用。PyAEDT 若静默改名，readback 会失败并触发回滚，而不是把改名后的对象报告为成功。

五种典型对话可直接这样写：

```text
给平面 sheet GroundSheet 创建 Perfect E，名称 PEC_Ground，Infinite Ground Plane=true。
给 face ID 4102 创建 Perfect H，名称 PMC_Symmetry。
给 face ID 5201 创建 Finite Conductivity，使用工程已有 copper，厚度 18um，粗糙度 0um。
给 sheet ResistiveFilm 创建 Impedance，名称 Zs_Film，R=75 ohm，X=-10 ohm，非无限地。
给 planar sheet TerminationSheet 创建 Serial Lumped RLC，名称 RLC_Term，integration line=XPos，
R=50ohm、L=1nH、C=2pF。输入 payload 中分别使用 50、1e-9、2e-12。
每次都先 inventory 和 preview，审批后 apply，回读验证且不要保存。
```

Lumped RLC 的完整 payload 示例：

```json
{
  "boundary_kind": "lumped_rlc",
  "boundary_name": "RLC_Term",
  "object_names": ["TerminationSheet"],
  "options": {
    "rlc_type": "Serial",
    "integration_line_direction": "XPos",
    "resistance": 50,
    "inductance": 1e-9,
    "capacitance": 2e-12
  },
  "max_assignments": 1
}
```

preview 中应看到类似 `Start=[16.0mm, 6.5mm, 0.0mm]`、`End=[12.0mm, 6.5mm, 0.0mm]`
的实际 integration line；这些坐标由目标 sheet 和当前 `model_units` 计算，不由 Agent 自行编造。

严格执行链路：

```text
get_live_hfss_geometry_inventory
  -> get_live_hfss_surface_boundary_inventory
  -> Finite Conductivity 时再调用 get_live_hfss_material_inventory
  -> preview_surface_boundary
  -> 冻结 solution type、目标几何、现有 Boundary 和材料定义
  -> Windows 原生 operation 审批
  -> PyAEDT typed boundary API
  -> 从 BoundaryObject/OO tree 回读 assignment 和 options
  -> scorecard
```

直接 MCP 调用链：

```text
get_live_hfss_surface_boundary_inventory
  -> preview_live_hfss_surface_boundary_create
  -> wait_for_live_approval
  -> apply_live_hfss_surface_boundary_create
```

运行 Workflow 时，Graph start/advance 审批和真正写 AEDT 的 operation 审批是两套独立批准；前者不能代替
后者。preview 返回 `project_dirty=false`、apply 成功返回 `status=verified` 和 `project_saved=false`。apply 前
几何、solution type、材料定义或任一 Boundary 变化，都会以 stale 拒绝并要求重新 inventory/preview。

创建或属性回读失败时，Harness 删除本次新边界并比较完整旧边界快照；不能完整恢复时明确报告 rollback
incomplete。不要在这种状态下继续创建下一个边界，应先由工程师在 AEDT GUI 中核对模型。

这项 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测五类边界/等效器件：平面 sheet Perfect E、face
Perfect H、带 copper/35um/0.5um 的 Finite Conductivity、`75-j10` ohm sheet Impedance，以及 Serial
`50ohm + 1e-9H + 2e-12F` Lumped RLC 的 XPos integration line 和启用位回读；同时覆盖
外部新增边界 stale、重复名、solid Impedance、solid/空值 Lumped RLC、solid Infinite Ground 拒绝、
已保存临时工程 SHA-256 不变，
以及测试后无 live session/AEDT 进程残留。确定性 readback fault injection unit test 覆盖创建后回滚；
真实 AEDT 尚无稳定且无污染的部分写入故障注入，因此不把 unit rollback 冒充真实 rollback 证据。
AEDT 2024 R2 仍必须按第 18.1 节在目标服务器复验。

### 13.9 在已有 HFSS 几何上创建 Wave/Lumped Port

需要在当前 DrivenModal HFSS 设计的已有几何上创建端口时，使用严格 Workflow：

```text
hfss_live_port_create
```

不要把 HFSS 3D 的端口与 3D Layout 的 component/edge port 混用。HFSS typed Port Harness 先调用
`get_live_hfss_port_inventory` 回读现有 Wave/Lumped Port，再根据端口类型使用不同 selector：

| `boundary_kind` | assignment | 主要 options |
|---|---|---|
| `wave_port` | 一个明确的 planar `assignment_face_ids` | `modes`、`renormalize`、毫米制 `deembed`、integration line 方向、特征阻抗类型 |
| `lumped_port` | 一个明确的 planar sheet `assignment_object_name` | 欧姆制 `impedance`、`renormalize`、布尔 `deembed`、integration line 方向 |

Wave Port 示例：

```json
{
  "boundary_kind": "wave_port",
  "boundary_name": "P1",
  "assignment_face_ids": [4102],
  "options": {
    "modes": 2,
    "renormalize": false,
    "deembed": 1.25,
    "integration_line_direction": "YPos",
    "characteristic_impedance": "Zwave"
  }
}
```

Lumped Port 示例：

```json
{
  "boundary_kind": "lumped_port",
  "boundary_name": "P_Lumped",
  "assignment_object_name": "PortSheet",
  "options": {
    "impedance": 60,
    "renormalize": false,
    "deembed": true,
    "integration_line_direction": "XPos"
  }
}
```

输入约束如下：

- 当前 typed Port Harness 只支持 `DrivenModal`。DrivenTerminal 的 reference conductor、terminal rename 和
  terminal readback 语义不同，不能借用本审批链路；`references` 非空会明确拒绝；
- Wave Port 必须传一个正整数 face ID，且 geometry inventory 必须确认该 face 为 planar；Lumped Port 必须传
  一个 sheet 对象名，不能传 solid、face ID 或多个对象；
- integration line 方向只能是 `XNeg/YNeg/ZNeg/XPos/YPos/ZPos`。preview 使用与 PyAEDT 创建路径相同的
  几何算法解析实际 Start/End；方向与端口面法向重合而得到两个相同点时直接拒绝，要求改用面内方向；
- Wave Port 的 `modes` 为 1～16，`deembed` 按毫米解释且范围为 0～1000000，
  `characteristic_impedance` 只能为 `Zpi/Zpv/Zvi/Zwave`；
- DrivenModal Wave Port 的 PyAEDT `impedance` 参数没有稳定的端口属性回读，因此严格 Harness 不接受该字段，
  不把“调用时传入”冒充“已在 AEDT 中验证”；Lumped Port 的 `impedance` 则按欧姆回读；
- 端口名称按不区分大小写检查重复。preview 后任一几何、solution type、model unit 或 Boundary/Port 属性变化，
  apply 都会以 stale 拒绝。

严格执行链路：

```text
get_live_hfss_geometry_inventory
  -> get_live_hfss_port_inventory
  -> hfss_live_port_create / preview_port
  -> 核对 selector、实际 integration line 和 typed options
  -> Windows 原生 operation 审批
  -> apply_port
  -> 回读类型、assignment、mode、CharImp、renormalize、deembed、impedance 和 integration line
  -> scorecard
```

创建或 typed readback 失败时，Harness 删除本次端口并比较完整旧 Boundary 快照；rollback 不完整会停止并报告，
不会继续创建下一个端口。成功只表示 AEDT 内存中的工程已修改，`project_saved=false`，保存仍需独立审批。

这项 Harness 已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测：平面 face 两模 Wave Port、`Zwave`、
`renormalize=false`、`deembed=1.25mm`，以及 planar sheet `60ohm` Lumped Port、XPos integration line；同时
覆盖外部新增 Boundary stale、重复名、solid Lumped Port 拒绝、已保存临时工程 SHA-256 不变和测试后无
AEDT 进程残留。确定性 fault injection 覆盖端口创建后 typed readback 失败回滚。AEDT 2024 R2 仍必须按
第 18.1 节在目标服务器复验。

### 13.10 在明确组件上创建 Layout Port

对于一个组件上每个信号网络对应一个明确 Pin 的场景，推荐使用严格 Workflow：

```text
layout_live_component_ports_create
```

初始 payload 示例：

```json
{
  "component_name": "U1",
  "signal_nets": ["PCIE_TX0_P", "PCIE_TX0_N"],
  "reference_nets": ["GND"],
  "max_candidates": 100,
  "max_new_ports": 4,
  "allow_multiple_pins_per_net": false
}
```

Workflow 依次执行：

```text
复用旧组件端点评分
  -> 验证明示 component 确实连接全部 signal nets
  -> 冻结 component、Pin、net 和现有 port order
  -> 生成 operation preview
  -> Windows 原生审批
  -> PyAEDT create_ports_on_component_by_nets
  -> 回读新增端口数量和完整 port order
  -> scorecard
```

Graph 的每一步仍需独立 Workflow 审批；真正创建端口另需一次 operation 审批。默认要求每个 signal net 在目标
组件上只匹配一个 Pin。出现多个 Pin 时 Harness 会停止并展示 Pin 清单，只有人工确认确实需要为所有匹配 Pin
建端口后，才设置 `allow_multiple_pins_per_net=true`。`max_new_ports` 范围为 1 到 64，默认 16。

apply 必须满足新增端口数等于 preview 时匹配的 Pin 数，且所有旧端口仍存在；否则删除本次新增端口并失败。
若旧端口意外变化或新增端口无法全部删除，会明确报告 rollback incomplete，不继续操作。成功后工程保持 dirty，
但不会自动保存。

当前组件端口 Harness 只支持 PyAEDT 公共 API `create_ports_on_component_by_nets`。旧 Workflow 中的
paired-passive 组合、裸 `ToggleViaPin` 和焊球 cylinder 配置仍作为候选证据存在，但不会被这个 live Workflow
自动执行。uniform-line edge port 已拆成下一节的独立 Workflow，不能借用组件端口审批。

### 13.11 在 bbox 边界创建 Uniform-Line Edge Port

需要按 cutout bbox 的某一侧为多条信号线创建端口时，使用：

```text
layout_live_uniform_edge_ports_create
```

示例 payload：

```json
{
  "signal_nets": ["PCIE_TX0_P", "PCIE_TX0_N"],
  "local_cut_region": {
    "type": "bbox",
    "unit": "mm",
    "x_min": 0,
    "y_min": 0,
    "x_max": 10,
    "y_max": 8
  },
  "side": "right",
  "layer": "L1",
  "port_type": "circuit",
  "max_candidates": 100,
  "max_new_ports": 4
}
```

Workflow 复用旧 edge 候选算法，只在 `status=ready` 且未截断时，为每个 signal net 选择一个 edge。随后冻结
edge 坐标、现有端口顺序并生成 operation preview；审批后逐个调用 PyAEDT `create_edge_port`。每次调用必须恰好
新增一个端口，批次总数必须等于 signal net 数；失败时删除本批次已新增端口。

`port_type` 支持 `circuit` 和 `wave`。Wave port 可额外提供：

```json
{
  "wave_horizontal_extension": 5,
  "wave_vertical_extension": 3,
  "wave_launcher": "1mm"
}
```

需要显式参考边时，可按 signal net 提供：

```json
{
  "reference_edges": {
    "PCIE_TX0_P": {"primitive_name": "gnd_shape_1", "edge_number": 2},
    "PCIE_TX0_N": {"primitive_name": "gnd_shape_2", "edge_number": 4}
  }
}
```

高级场景也可以直接调用 `preview_live_layout_edge_ports_create`，传入显式 `edge_targets`；每项必须包含
`primitive_name`、非负 `edge_number` 和 `port_type`，可选 reference edge 和受限 wave 参数。重复 edge、未知
primitive、越界 edge、circuit port 携带 wave 参数、目标数超过 `max_new_ports` 都会在 preview 阶段失败。

Graph step 与真正创建端口仍使用两套独立审批。apply 后 scorecard 核对 one-port-per-net、完整 port order、
旧端口未消失和工程未保存。该 Workflow 不会调用裸 `oEditor.CreateEdgePort`。

### 13.12 创建 HFSS 相对坐标系

需要为后续局部几何、端口方向或局部建模建立参考坐标系时，使用严格 Workflow：

```text
hfss_live_coordinate_system_create
```

示例请求：

```text
在当前 HFSS 设计中创建相对坐标系 HarnessCS，参考 ParentCS，
原点为 [OX, 2mm, 3mm]，X 轴指向 [1, 1, 0]，Y 点为 [0, 0, 2]。
先列出当前坐标系和活动 WCS，再 preview；审批后创建并逐项回读，
最后把活动 WCS 恢复到创建前的坐标系，不要保存工程。
```

Workflow 初始 payload：

```json
{
  "coordinate_system_name": "HarnessCS",
  "reference_coordinate_system": "ParentCS",
  "origin": ["OX", "2mm", 3],
  "x_axis": [1, 1, 0],
  "y_axis": [0, 0, 2]
}
```

当前严格边界如下：

- 只创建 HFSS 3D 的 `Relative`、`Axis/Position` 坐标系；Euler Angle、Face CS、Object CS 和 Layout CS
  需要各自独立的语义、selector 和回读 Harness，不能借用本操作；
- `coordinate_system_name` 必须是明确安全名称，按不区分大小写检查重复，且不能为 `Global`；
- reference 只能是 `Global` 或 inventory 中已存在且类型确认为 `Relative` 的坐标系；
- origin 必须有三个分量，可使用有限数值或受限 AEDT expression。因为表达式可能引用变量，preview 会冻结
  完整 design/project variable 表达式清单；
- `x_axis`、`y_axis` 各包含三个有限数值，不能为零向量或彼此共线。数值由 AEDT 按当前 model unit 解释，
  例如输入 `1` 可能回读为 `1mm`；Harness 按 model unit 做 typed 数值比对，不做字符串猜测；
- apply 前重新读取设计类型、solution type、model unit、活动 WCS、完整坐标系属性和变量清单，任何变化都使
  preview stale。

AEDT/PyAEDT 创建坐标系时会把新坐标系设为当前 Working Coordinate System，这是一个容易被忽略的副作用。
Harness 在创建后显式执行 `SetWCS` 恢复 preview 前的活动坐标系，再回读 `Type`、`Reference CS`、`Mode`、
`Origin/X,Y,Z`、`X Axis/X,Y,Z` 和 `Y Point/X,Y,Z`。成功结果必须包含
`active_coordinate_system_restored=true` 和 `project_saved=false`。

如果创建、恢复 WCS 或 typed readback 失败，Harness 会先恢复旧 WCS，再删除新坐标系，最后比较完整旧快照；
不完整时报告 rollback incomplete。该能力已在隔离 AEDT 2026 R1、PyAEDT 1.3.0 上实测父相对坐标系、变量
origin、非单位轴、活动 ParentCS 恢复、外部新增 stale、重复名、共线轴、未知 reference、已保存工程 SHA-256
不变，并通过“坐标系已真实创建后强制 readback 失败”的真实 rollback 注入，测试结束后无 AEDT/worker 进程残留。
AEDT 2024 R2 仍必须按第 18.1 节在目标服务器复验。

已有 HFSS Setup 的常用求解参数也可以受控更新。例如：

```text
把当前 HFSS 设计 Setup1 的 Frequency 改成 28GHz、MaximumPasses 改成 8。
先读取 setup inventory，再 preview，不要创建新 setup，不要保存工程。
```

这会调用 `preview_live_hfss_setup_update`，只接受 allowlist 中的 Setup 属性。apply 前会确认 Setup 列表和原属性
没有变化，成功后 readback；失败时恢复每个属性的原值或删除本次新增属性。创建新 Setup 仍使用独立的
`preview_live_hfss_setup_create`，避免“更新拼错名称”意外创建第二个 Setup。

HFSS 3D 和 HFSS 3D Layout 都支持受控创建频率 Sweep：

```text
在 Setup1 下创建 Sweep28G：1GHz 到 40GHz，LinearCount 401 点，Interpolating。
先确认 Setup 和已有 Sweep，preview 后等待审批，不要自动保存。
```

先使用 `get_live_aedt_setup_inventory` 获取准确的 Setup/Sweep 名称，再调用
`preview_live_frequency_sweep_create -> wait_for_live_approval -> apply_live_frequency_sweep_create`。
Harness 会限制频率单位、范围、点数、step 和 sweep type；同名 Sweep、未知 Setup、负频率、反向范围和过大点数
会在 preview 阶段拒绝。apply 发生异常时会删除本次创建的 Sweep。

受支持的写操作都必须经过 preview 和原生审批。例如创建 setup：

```text
检查当前 HFSS 设计现有 setup。
如果不存在 Setup_10G，预览创建 Driven setup，频率 10GHz、MaximumPasses=10。
审批后应用并回读，但不要保存工程。
```

生产求解推荐流程：

```text
layout_live_solve_start
  -> validate_setup
  -> preview_analysis（冻结 Setup/Sweep 和资源预算）
  -> operation 原生审批
  -> apply_analysis（非阻塞提交）
  -> verify_submission
  -> 后续 get_live_hfss_analysis_status
```

推荐请求写法：

```text
使用 layout_live_solve_start 工作流启动当前 3D Layout 的 Setup1/Sweep1。
使用 8 cores、1 task、0 GPU，不使用 auto settings。先核对 Setup/Sweep，展示资源预算；
每个 graph step 都要审批，求解提交需要独立 operation 审批。非阻塞启动，不保存工程。
```

只需一次原子提交、不需要 Graph 状态时，仍可使用：

```text
preview_live_hfss_analysis_start
  -> wait_for_live_approval
  -> apply_live_hfss_analysis_start
  -> get_live_hfss_analysis_status
  -> 按需 preview/apply cancel
  -> preview/apply results export
```

`apply` 返回 `submitted` 只表示任务已经提交，不表示仿真成功完成。应继续查询 analysis status，并在求解
结束后检查 Setup/Sweep、结果文件和 evidence manifest。取消、导出和保存是三个独立动作，各自需要新的 preview
和审批；启动求解的 token 不能复用。

需要从启动一直执行到证据导出的任务，优先使用组合 Workflow `layout_live_solve_export`：

```text
validate_setup
  -> preview_analysis
  -> solve operation 审批
  -> apply_analysis
  -> poll_analysis --running--> poll_analysis（最多 64 次）
  -> validate_export
  -> preview_export
  -> export operation 审批
  -> apply_export
  -> verify_export
```

它在同一个 Graph 内包含两次互相独立的 operation 审批。第一次 token 只允许非阻塞启动求解，使用后立即从
server-owned binding 清除；第二次必须针对导出 preview 重新审批。Graph step token、solve token 和 export token
三者不能互换，也不会写入 Mission、node payload 或 status。最终 scorecard 同时核对求解提交、非阻塞资源、
停止状态、solution 新鲜度证据、导出文件、manifest、SHA256 和工程未保存状态。
AEDT 接受任务后可能短暂返回 `state=submitted` 但尚未报告 `running=true`。Harness 会把这个状态至少保留
5 秒并继续走监控 loop，不会因为第一次状态读取为“不在运行”就立即导出；超过保护窗口仍未观察到 running
时标记为 `not_running_unverified`，后续必须依靠真实导出和 evidence 校验，而不能声称已经观察到完整求解过程。

需要长时间观察时，使用 `layout_live_solve_monitor`。它不是后台无限轮询，而是一个有界 Graph loop：每次
`apply_ansys_workflow_advance` 只读取一次状态；仍在运行时通过 `running` edge 回到 `poll_analysis`，停止后进入
`verify_stopped`。最多轮询 64 次，同时仍受 Graph `max_steps` 限制，因此建议启动时根据求解时长设置合理的
`max_steps`。它会读取本次 run 的 solution evidence；只有 pre/post 结果快照满足完整新鲜度条件时才返回
`solve_success_verified=true`。没有受控 start 快照、未观察到 running 或结果目录证据不足时仍返回 `false`，
不会把“不再运行”误报为“求解成功”。

求解停止后，3D Layout 结果优先使用 `layout_live_results_export`：

```text
validate_export
  -> preview_export
  -> operation 原生审批
  -> apply_export
  -> verify_export
```

Touchstone 请求需要现有 `setup_name`，可选 `sweep_name`；CSV 请求需要现有 `report_name`。导出只能写入
`AEDT_AGENT_EXPORT_ROOT` 下的 server-managed 目录。scorecard 会重新读取 artifact 和 manifest，核对文件大小、
SHA256、工程/设计、导出 spec、工程未修改及未保存状态，并把两者登记为 Graph artifact refs。
原子 Harness 也已支持同一能力；对 3D Layout 调用 `preview_live_hfss_results_export` 时必须显式传
`product="layout"`，HFSS 3D 则使用默认的 `product="hfss"`。

推荐对话：

```text
先用 layout_live_solve_monitor 观察 Setup1，单步推进直到 AEDT 不再运行，不要声称求解已成功。
然后使用 layout_live_results_export，把 Setup1/Sweep1 导出为 Touchstone，artifact_name=channel_after。
先 preview，导出需要独立审批；完成后核对 artifact SHA256 和 evidence manifest，不要保存工程。
```

## 14. 保存工程

修改成功不等于保存成功。Desktop live 修改默认只改变 AEDT 内存中的活动工程：

- 用户没有明确说保存时，不保存；
- 参数化审批不能顺便批准保存；
- 保存必须单独调用 `preview_live_project_save`；
- 用户必须再次检查并批准；
- `apply_live_project_save` 成功后才能报告已保存。

推荐分成两轮：

```text
第一轮：修改、回读、不保存
第二轮：用户检查 AEDT UI 后明确说“保存当前工程”
```

如果误操作尚未保存，可关闭工程并选择不保存；如果工程还有其他有价值的未保存修改，不要直接关闭，
应先在 AEDT UI 中检查并手工处理。

## 15. 文件副本模式

除了 live Desktop 操作，CLI 还支持对 `.aedt + .aedb` 或 `.aedb` 创建工作副本再修改，源工程不覆盖。

只读检查：

```powershell
ansys-assistant inspect-layout `
  --project C:\cases\board.aedt `
  --target-width 4.3mil `
  --tolerance 0.1um
```

仅生成预览：

```powershell
ansys-assistant parameterize-width `
  --project C:\cases\board.aedt `
  --target-width 4.3mil `
  --tolerance 0.1um `
  --variable-name W_line
```

应用到工作副本：

```powershell
ansys-assistant parameterize-width `
  --project C:\cases\board.aedt `
  --target-width 4.3mil `
  --tolerance 0.1um `
  --variable-name W_line `
  --variable-value 4.3mil `
  --workspace C:\cases\assistant-runs `
  --apply
```

检查 JSON 中的 `working_project_path`、`verified_count`、`source_unchanged` 和 evidence，再在 AEDT 中
打开工作副本复核。

## 16. 严格 Workflow

复杂任务如果包含循环、分支、多个 Worker、审批门、失败恢复或证据汇总，不应让 Claude Code 临时拼接
一串自由脚本。当前版本已经把原有 YAML Graph Workflow 接入 Assistant Runtime Harness。

### 16.1 可用工作流

在对话里说“列出可用的 Ansys 工作流”，Claude Code 会调用 `list_ansys_workflows`。当前 allowlist 包含：

| Workflow | 用途 |
|---|---|
| `aedt_live_variable_batch_upsert` | 在当前 HFSS/3D Layout 中按依赖顺序原子创建或更新 1～32 个 design/project variable，逐项回读并在真实部分失败时恢复完整变量清单 |
| `hfss_live_coordinate_system_create` | 创建一个 HFSS 相对 Axis/Position 坐标系，回读 reference/origin/axes、恢复原活动 WCS，失败时删除新坐标系 |
| `hfss_live_geometry_create` | 为活动 HFSS 3D 设计创建有界 typed primitive batch，整批回读并在失败时回滚 |
| `hfss_live_geometry_boundary_create` | 在一次审批事务中创建 HFSS primitive、解析 typed face selector、分配 Boundary/Port，任一步失败时一起回滚 |
| `hfss_live_length_mesh_create` | 为明确 HFSS solid batch 创建 Length Based Mesh Operation，回读全部约束，失败时删除新 operation |
| `hfss_live_material_create` | 创建一个数值型各向同性 HFSS 电磁材料，回读五个 simple property 和可选外观，失败时删除并恢复完整材料目录 |
| `hfss_live_infinite_sphere_create` | 检查 radiated-field 前置条件并创建有界 HFSS Infinite Sphere，回读角度轴、极化和单位，失败时删除新 Field Setup |
| `hfss_live_surface_boundary_create` | 在明确 object/face 上创建 Perfect E、Perfect H、Finite Conductivity、sheet Impedance 或 sheet Lumped RLC，回读 typed 属性和 integration line，失败时删除新 Boundary |
| `hfss_live_port_create` | 在 DrivenModal HFSS 的明确 planar face/sheet 上创建 Wave/Lumped Port，回读模式、阻抗、deembed 和 integration line，失败时删除新 Port |
| `hfss_live_material_assign` | 为显式 HFSS solid batch 分配一个已有工程材料，回读材料和 Solve Inside，失败时恢复原状态 |
| `hfss_live_setup_sweep_create` | 在一次审批事务中创建 HFSS Setup 和频率 Sweep，回读关键属性，失败时清理新 Setup |
| `layout_live_audit` | 对活动 3D Layout 执行 routing/object/variable/setup/technology/connectivity 只读审计 |
| `layout_live_material_create_assign` | 原子创建一个数值型各向同性工程材料并分配给一个明确 signal/dielectric stackup layer 字段，失败时恢复层并删除材料 |
| `layout_live_via_create` | 基于既有 padstack、signal layer 和 net 原子创建 1～32 个精确 Via，原生回读并在失败时删除整批新对象 |
| `layout_live_via_update` | 对 1～32 个既有精确 Via 原子执行移动、旋转、改网或锁定更新，只允许请求的原生字段变化，失败时恢复完整目标快照 |
| `layout_live_via_delete` | 删除 1～32 个可完整重建的精确 Via，验证原生名称消失；失败时重建已删除目标并恢复完整原生快照 |
| `layout_live_component_ports_create` | 复用旧端口候选评分，验证明确组件和信号网，审批后创建 component-net ports 并回读 |
| `layout_live_uniform_edge_ports_create` | 复用旧 bbox-side edge 选择器，为每条信号网创建一个受控 circuit/wave edge port |
| `layout_live_parameterize_width` | 在当前 3D Layout 中选择 Path、冻结线宽参数化 preview、审批后 apply 并生成 readback scorecard |
| `layout_live_parameterize_solve_touchstone_score` | 参数化线宽并验证后，继续求解、监控、导出和显式端口映射评分，包含三次独立 operation 审批 |
| `layout_live_solve_monitor` | 通过最多 64 次的有界 Graph loop 单步观察求解，直到 AEDT 不再运行；不冒充求解成功判定 |
| `layout_live_solve_start` | 核对当前 3D Layout Setup/Sweep 和资源预算，审批后非阻塞启动求解并验证提交状态 |
| `layout_live_solve_touchstone_score` | 启动并监控求解，停止后导出 Touchstone 并按显式端口映射评分，包含两次独立 operation 审批 |
| `layout_live_solve_export` | 在一个 Graph 中启动、循环监控并导出结果，求解和导出分别使用独立 operation 审批 |
| `layout_live_results_export` | 在求解停止后导出 Touchstone 或 report CSV，并复核 artifact、SHA256 和 evidence manifest |
| `layout_live_touchstone_score` | 按 AEDT 当前端口顺序和显式源/目的端口映射导出、校验并确定性评分 Touchstone |
| `brd_local_cut_build` | 构建局部裁切模型，停在模型复核，不直接求解 |
| `brd_real_solve_evidence` | 真实求解并生成证据包 |
| `brd_local_cut_solve_evidence` | 局部裁切、求解和证据链 |
| `brd_before_after_compare` | 对比修改前后的通道结果 |
| `brd_channel_optimize` | 通道分析、建模、评分和决策循环 |
| `brd_iterative_optimize` | 记录式迭代优化 |
| `brd_reviewed_model_optimize_loop` | 带模型复核、求解、导出、评分、编辑和报告的完整循环 |
| `brd_multi_channel_demo` | 多通道并行评分示例 |
| `brd_recorded_void_action` | 执行并审计已记录的 void 修改动作 |
| `via_optimize_demo` | Via 优化示例流程 |

用 `inspect_ansys_workflow` 查看完整 DAG、Worker capability、节点数、输入字段、风险和审批策略。
只能使用 allowlist 中的 ID，不能通过 MCP 传入任意 YAML 路径。

### 16.2 推荐对话方式

例如：

```text
请检查 brd_local_cut_build 工作流需要的输入。先只做 start preview，列出将绑定的 AEDT
工程、设计、初始 payload、缺失字段和风险，等我在原生确认框批准后再创建 graph run。
创建后每次只推进一个 graph step，每步都先报告当前节点、可能调用的 Worker 和副作用。
```

Claude Code 应按固定顺序调用：

```text
list_ansys_workflows
  -> inspect_ansys_workflow
  -> preview_ansys_workflow_start
  -> wait_for_live_approval
  -> apply_ansys_workflow_start
  -> get_ansys_workflow_status
  -> preview_ansys_workflow_advance
  -> wait_for_live_approval
  -> apply_ansys_workflow_advance
  -> get_ansys_workflow_status
```

`apply_ansys_workflow_start` 只创建 Mission 和 Graph Run，不执行首个节点。每次
`apply_ansys_workflow_advance` 最多推进一个调度 step；不能一次审批后静默跑完整个循环。

`layout_live_parameterize_width` 有两层独立审批：Graph step 审批控制“是否推进工作流”，线宽 operation 审批控制
“是否修改 AEDT”。执行到 `preview_parameterization` 后，其输出包含 `operation_preview_id`。Claude Code 必须：

```text
wait_for_live_approval(live_session_id, operation_preview_id)
  -> 获取 operation approval token
  -> preview_ansys_workflow_advance
  -> 等待 graph step approval
  -> apply_ansys_workflow_advance(
       approval_token=<graph token>,
       operation_approval_token=<operation token>)
```

两个 token 不能互换。`operation_approval_token` 只短暂进入 MCP 内存中的 graph binding，用完立即清除，
不会写入 Graph Run、node payload、日志或 status 输出。缺少 operation token 时 apply node 会明确失败，不会绕过审批。

#### Touchstone 显式映射评分

不要让 Agent 仅凭 `S4P` 后缀猜测差分对，也不要默认端口按字母排序。先调用
`get_live_aedt_setup_inventory`，读取其 `ports` 和 `port_order_source`，再启动
`layout_live_touchstone_score`。单端通道示例 payload：

```json
{
  "setup_name": "SetupL",
  "sweep_name": "Sweep1",
  "artifact_name": "CLK_channel",
  "expected_port_order": ["TX", "RX"],
  "sparameter_mode": "single_ended",
  "source_ports": ["TX"],
  "destination_ports": ["RX"],
  "frequency_start_ghz": 1.0,
  "frequency_stop_ghz": 28.0,
  "rl_target_db": -15.0,
  "insertion_loss_min_db": -12.0,
  "reference_impedance_ohm": 50.0
}
```

差分通道必须分别给出正、负端，且顺序有意义：

```json
{
  "setup_name": "SetupL",
  "sweep_name": "Sweep1",
  "expected_port_order": ["TX_P", "TX_N", "RX_P", "RX_N"],
  "sparameter_mode": "differential",
  "source_ports": ["TX_P", "TX_N"],
  "destination_ports": ["RX_P", "RX_N"],
  "require_defined_differential_pairs": true,
  "frequency_start_ghz": 1.0,
  "frequency_stop_ghz": 28.0,
  "rl_target_db": -15.0,
  "insertion_loss_min_db": -12.0,
  "reference_impedance_ohm": 100.0
}
```

设置 `require_defined_differential_pairs=true` 后，validate 节点会要求源端和目的端的正负极性都与 AEDT 当前
active Differential Pair 定义完全一致。定义缺失、inactive、正负端反向或只能读到 pair 名而无法取得端口映射时，
Workflow 会在导出和评分前停止。保持默认 `false` 时仍会返回 `differential_pair_validation` 供工程师复核。

Workflow 在 validate 节点再次读取端口顺序；如果 AEDT 当前顺序与 `expected_port_order` 不一致，会在导出前失败。
导出 preview 和 evidence manifest 也各自记录端口顺序，scorecard 必须三方一致才评分。单端评分使用显式
`S(destination,source)`；差分评分使用显式源差分对和目的差分对计算 `SDD11/SDD21`，支持端口位于多端口
Touchstone 中的任意位置，不再依赖“前四个端口”的隐含假设。

评分会生成原始 Touchstone、导出 evidence manifest 和 `*.touchstone-score.json` 三个受控 artifact。结果包含
RL/IL 最差点、频点、超限点数、压缩频谱 evidence 和 SHA-256。这个 Workflow 只评估频域 RL/IL，输出中
`tdr_evaluated=false`；Agent 不得把它描述成 TDR、阻抗连续性或完整求解成功证明。

已经有可复用结果时使用 `layout_live_touchstone_score`；需要从求解开始时使用
`layout_live_solve_touchstone_score`。后者依次执行 Setup/Sweep 和资源验证、求解 preview/apply、有界状态轮询、
端口映射复核、Touchstone preview/apply 和评分。求解与导出分别要求 operation token，任一个 token 都不能授权
另一个阶段。

对于“把所有 4.3mil 线宽改为 `W_line`，然后求解并评分”的完整任务，使用
`layout_live_parameterize_solve_touchstone_score`，其 payload 是线宽参数化字段和评分字段的并集：

```json
{
  "selector": {"target_width": "4.3mil"},
  "variable_name": "W_line",
  "variable_value": "4.3mil",
  "setup_name": "SetupL",
  "sweep_name": "Sweep1",
  "cores": 4,
  "tasks": 1,
  "gpus": 0,
  "expected_port_order": ["TX_P", "TX_N", "RX_P", "RX_N"],
  "sparameter_mode": "differential",
  "source_ports": ["TX_P", "TX_N"],
  "destination_ports": ["RX_P", "RX_N"],
  "frequency_start_ghz": 1.0,
  "frequency_stop_ghz": 28.0,
  "rl_target_db": -15.0,
  "insertion_loss_min_db": -12.0,
  "reference_impedance_ohm": 100.0
}
```

这个组合 Workflow 有三次互不替代的 operation 审批：线宽修改、求解提交、结果导出。每个 Graph step 仍另有
一次推进审批。线宽 readback 未通过时不会进入求解；求解还在运行或端口顺序发生变化时不会进入评分。最终
scorecard 同时报告 `parameterization_verified`、`solve_submission_verified`、轮询次数、端口映射和评分结果。
工程保持 dirty 但不会自动保存，用户复核后再决定调用项目保存 Harness。
Harness 现在会在提交求解前冻结 solution inventory 和结果目录快照，在观察到 solver 运行并停止后再次读取。
只有目标 Setup 确实可读取 solution data、结果快照发生变化、最新结果文件时间不早于本次提交，并且扫描既未
截断也未失败时，组合 scorecard 才返回 `solve_success_verified=true` 和
`result_freshness_verified=true`。同时会给出 before/after digest 和 `verification_reasons`。

任何证据缺失都会保守返回 `false`，例如从未观察到 running、只有旧 solution、结果时间未更新、目录不存在、
扫描截断或访问失败。`is_solved` 单独为真仍不足以证明本次 run；Agent 必须原样报告这些标志，不能把“求解已
提交且当前停止”升级描述为已收敛。若 AEDT 停止后仍在刷新结果文件，后续状态读取会重新评估未通过的
evidence；一旦验证通过便冻结为真，`verification_attempt` 会记录尝试次数。
为避免反复遍历大型结果目录，同一 run 最多重新评估 8 次。
求解可能持续较久时，启动这两个组合 Workflow 应显式设置 `max_steps=128`；每次轮询只消耗一个经审批的 Graph
step，绝不会在 MCP 内部无限等待。上限仍为 256，超过后必须查看状态并创建新的受控监控流程，而不是绕过上限。

### 16.3 审批和目标绑定

Workflow start preview 会冻结以下内容并计算 SHA-256 digest：

- Workflow ID 和版本；
- 用户目标、初始 payload 和 `max_steps`；
- AEDT 版本、PID、gRPC 端口；
- 当前活动工程和活动设计。

后续 advance 必须连接同一目标。Graph 已经被其它进程推进、活动工程变化、端口变化、审批过期或 token
被重放时，apply 会拒绝执行并要求重新 preview。Mission 状态持久化在
`.aedt-agent\assistant-workflows\missions.db`，可用 `get_ansys_workflow_status` 查看节点、handoff、job、
错误和 supervision 建议。

### 16.4 Process Workflow 与活动 AEDT 会话的边界

Workflow 现在分为两类。`inspect_ansys_workflow` 中 `attached_live_session_reuse=true` 的 Workflow 会通过
server-owned binding 和 live graph handler 复用当前已 attach 的 AEDT 会话。首个实现是
`layout_live_audit`：它在两个受控 graph step 中读取 routing、对象分类、变量、Setup/Sweep，并输出 scorecard；
当前 v3 还会读取 stackup、padstack、端口顺序、差分对映射，以及有界的 net/component/pin/via 连接关系；
任何不可用或截断 section 都会出现在 summary 中。
`layout_live_parameterize_width` 则把本手册的核心线宽参数化用例提升为四步 live Workflow。
`layout_live_component_ports_create` 把原 BRD Workflow 的组件端点评分迁入 live Assistant，但写入面只开放经过
严格约束的 PyAEDT component-net port API；候选评分不能替代显式组件选择，paired component group 不会自动写入。
`layout_live_uniform_edge_ports_create` 则迁移旧 uniform-line bbox-side 算法，先产生有界候选，再把选中的
primitive/edge 固化为 typed target；它支持 circuit、wave 和显式 reference edge，但不开放裸 COM。
`layout_live_solve_start` 使用相同的 server-owned binding 和双层审批模型：先验证 Setup/Sweep，再冻结 cores、
tasks、gpus 和 auto settings，最后以非阻塞方式提交求解并读取运行状态。它不会把“API 返回成功”单独当成
通过，scorecard 还会检查 run id、资源预算、非阻塞标志和项目未保存状态。
`layout_live_solve_monitor` 首次把原 Graph 的 loop edge 用在活动 AEDT 会话上，每次审批只轮询一次，不在 MCP
内部静默占用线程。`layout_live_results_export` 则把 Layout 的 Touchstone/CSV 导出纳入双层审批和 artifact
scorecard；导出写文件但不修改或保存 AEDT 工程。
`layout_live_solve_export` 将这三个阶段组合成一个八节点闭环，同时保留两次独立 operation 审批和每次 Graph
advance 审批；组合并不扩大任何一次 token 的权限范围。
`layout_live_touchstone_score` 复用相同的受控导出链，并在服务端根据 Setup inventory、导出 preview 和 evidence
manifest 三次核对端口顺序，然后用明确的单端或差分端口映射评分。它不会从文件扩展名猜测差分对，也不会把
频域评分冒充 TDR 证据。
`layout_live_solve_touchstone_score` 和 `layout_live_parameterize_solve_touchstone_score` 是对上述原子节点的组合，
不是一段临时生成的自由脚本。前者把求解闭环接到评分，后者再把经过 readback 的线宽参数化接到最前面；每个
副作用阶段仍保留自己的 operation preview、原生审批和目标快照校验。
用户不能在 `initial_payload` 中伪造 `_assistant_live`。Runtime 会拒绝该保留字段，并把可执行
`live_session_id` 只保存在当前 MCP 进程的 server-owned graph binding 中，不写进 Mission payload；
Mission 只持久化端口、PID、工程和设计身份用于后续重新绑定校验。

其余原有 Mission Process Workflow 的 Worker 仍按各自的文件/进程契约运行，**不会自动复用已 attach 的
PyAEDT Desktop 对象**。原生审批绑定目标并不等于 Worker 复用了 Desktop；应以每个 Workflow 返回的
`attached_live_session_reuse` 和 `execution_backend` 为准。
需要直接修改当前打开工程的原子任务，继续使用 `list_live_*`、`preview_live_*` 和 `apply_live_*`。
需要循环和恢复的任务才选择 Workflow。后续会逐个把适合的 Worker 改造成显式 live-session handler。

默认 Workflow profile 是 `safe-recorded`，不会启动真实 AEDT Worker。要运行真实求解或模型编辑 Workflow，
先准备经过审核的 `ExecutionProfile` JSON，再在启动 AEDT 前设置：

```powershell
$env:AEDT_AGENT_WORKFLOW_PROFILE = "C:\AnsysAgent\profiles\aedt-2024r2-local.json"
```

该 profile 必须显式设置 `allow_real_aedt=true`、正确的 `aedt_version`、超时、并发、Harness 根目录和允许传入
Worker 的环境变量。修改环境变量后应关闭并重新打开本次 Ansys Agent PowerShell 会话。

### 16.5 状态处理

| 状态 | 处理方式 |
|---|---|
| `running` | 查看下一节点，重新 preview 并批准一个 step |
| `waiting_approval` | 查看节点输出中的审批原因，不要盲目重试 |
| `succeeded` | 检查 scorecard、artifact 和 evidence，再决定是否保存源工程 |
| `failed` | 阅读 `graph_run.error` 和 `supervision`，修正输入或人工 takeover |
| `canceled` | 停止推进，确认是否已有新的替代 graph run |

## 17. 未知能力与 API Memory

当已注册 Harness 不支持某项任务时，Agent 应按以下顺序处理：

```text
明确报告 capability miss
  -> search_ansys_api
  -> inspect_ansys_symbol
  -> 获取 operation_evidence
  -> propose_ansys_operation
  -> validate_ansys_operation
  -> preview_exploratory_operation
  -> 写操作等待审批
  -> apply_exploratory_operation
  -> readback/rollback
  -> capture_capability_trace
```

不要接受“我从源码里看到可以这样调用”作为跳过验证的理由。Runtime 会重新检查：

- 包名和版本；
- symbol；
- 源文件路径；
- snippet digest；
- project/design 身份；
- operation plan schema；
- policy 和风险级别。

成功走通且可重复使用的 trace 可以生成 Harness 或 Skill 候选，但不会自动修改仓库、热注册工具或提交代码。

## 18. 常用诊断命令

### 18.1 新 Harness 的真实 AEDT 准入

这一节面向维护者和部署验收人员。普通工程师使用已发布能力时不需要运行开发测试；但是任何新增或改变
AEDT 写行为的 Harness，在合入和发布前都必须通过目标 AEDT 版本的真实验收。mock/unit test 只能证明
参数校验和分支逻辑，不能证明 PyAEDT 属性、AEDT 对象类型、回读和 rollback 在真实 Desktop 中成立。

最低准入标准如下：

- 测试只连接由测试自己启动的非图形 AEDT，不连接用户正在工作的 GUI 会话；
- 测试使用专用临时工程和设计，不打开、保存或覆盖生产工程；
- 必须覆盖 `preview -> approval -> apply -> readback`；
- 必须证明 preview 不修改工程、apply 默认不保存工程；
- 必须验证对象数量、对象名和关键属性，而不是只判断 API 没有抛异常；
- 必须验证 preview 后外部状态变化会触发 stale 拒绝；
- 有部分写入风险的 Harness 必须验证失败 rollback；无法稳定制造真实故障时，要明确记录该缺口，不能用
  unit test 冒充真实 rollback 证据；
- 测试结束必须删除测试对象、关闭测试拥有的 AEDT 进程，并用 `live-sessions` 确认没有遗留会话；
- 目标部署是 AEDT 2024 R2 时，2026 R1 的通过结果不能替代 2024 R2 验收，两者应分别留证。

真实测试默认跳过，避免日常 `pytest` 意外启动 AEDT。AEDT variable batch、HFSS relative coordinate system、
geometry、atomic geometry-boundary、typed Wave/Lumped Port、material creation/assignment、Length Based Mesh、Infinite Sphere、
typed surface boundary 和 atomic setup-sweep Harness 在 AEDT 2026 R1 上的验收命令为：

```powershell
Set-Location D:\ansys-agent
$env:RUN_REAL_LIVE_AEDT = "1"
$env:REAL_AEDT_VERSION = "2026.1"
$env:ANSYSEM_ROOT261 = "C:\Program Files\ANSYS Inc\v261\AnsysEM"

.\.venv\Scripts\python.exe -m pytest -q -s `
  tests\test_live_aedt_variable_real.py `
  tests\test_live_hfss_coordinate_system_real.py `
  tests\test_live_hfss_geometry_real.py `
  tests\test_live_hfss_geometry_boundary_real.py `
  tests\test_live_hfss_port_real.py `
  tests\test_live_hfss_far_field_real.py `
  tests\test_live_hfss_surface_boundary_real.py `
  tests\test_live_hfss_length_mesh_real.py `
  tests\test_live_hfss_material_create_real.py `
  tests\test_live_hfss_material_real.py `
  tests\test_live_hfss_setup_sweep_real.py
```

在 AEDT 2024 R2 目标机上改为：

```powershell
$env:RUN_REAL_LIVE_AEDT = "1"
$env:REAL_AEDT_VERSION = "2024.2"
$env:ANSYSEM_ROOT242 = "C:\Program Files\ANSYS Inc\v242\AnsysEM"

.\.venv\Scripts\python.exe -m pytest -q -s `
  tests\test_live_aedt_variable_real.py `
  tests\test_live_hfss_coordinate_system_real.py `
  tests\test_live_hfss_geometry_real.py `
  tests\test_live_hfss_geometry_boundary_real.py `
  tests\test_live_hfss_port_real.py `
  tests\test_live_hfss_far_field_real.py `
  tests\test_live_hfss_surface_boundary_real.py `
  tests\test_live_hfss_length_mesh_real.py `
  tests\test_live_hfss_material_create_real.py `
  tests\test_live_hfss_material_real.py `
  tests\test_live_hfss_setup_sweep_real.py
```

如果 AEDT 安装路径不符合标准环境变量，可直接指定可执行文件：

```powershell
$env:REAL_AEDT_EXECUTABLE = "D:\ANSYS\v242\AnsysEM\ansysedt.exe"
```

合格结果必须是 pytest exit code `0`，并显示真实测试 `passed`。随后检查没有遗留会话：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
```

只有 `sessions` 为空，或列表中仅有测试前已存在且经 PID 核对的用户会话，清理才算完成。真实测试失败时
应按 AEDT 的实际行为修 Harness 或收紧 schema；不得删除断言、降级为“调用无异常”或直接跳过回读来换取通过。

常用 CLI 总览：

| 命令 | 是否连接 AEDT | 是否修改工程 | 用途 |
|---|---:|---:|---|
| `capabilities` / `capabilities-v2` | 否 | 否 | 查看当前 Harness 能力和风险 |
| `live-sessions` | 否 | 否 | 发现正在运行的 AEDT PID、版本和端口 |
| `live-info` | 是 | 否 | attach、核对活动工程/设计、release |
| `live-workflow-smoke` | 是 | 否 | 运行 3D Layout 只读审计 Workflow |
| `live-width-preview-smoke` | 是 | 否 | 选择 Path 并生成参数化 preview，停在 apply 前 |
| `inspect-layout` | 文件模式 | 否 | 检查磁盘工程或 AEDB 中的 Path |
| `parameterize-width` | 文件模式 | 工作副本 | 在自动创建的工作副本中预览或参数化 |

查看能力：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2
```

对正在运行的 HFSS 3D Layout 执行只读 Workflow smoke，并生成机器可读证据：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-workflow-smoke `
  --port 50051 `
  --aedt-version 2024.2 `
  --expected-project "Board" `
  --expected-design "Layout1" `
  --output-dir C:\AnsysAgent\smoke\layout-audit `
  --confirm-read-only
```

该命令只运行 `layout_live_audit`，不会执行参数化、保存或求解。它要求显式
`--confirm-read-only`，并在输出目录生成：

- `live_layout_audit_smoke.json`；
- `live_layout_audit_smoke.json.sha256`；
- `missions.db`，用于检查 Graph Run 和 node 状态。

退出时只释放 PyAEDT wrapper，不关闭 AEDT 和工程。JSON 中必须同时满足 `status=passed`、
`read_only=true`、`project_saved=false` 和 scorecard `status=passed`，才可作为真实 AEDT smoke 证据。

在真正修改前验证“4.3mil 能选中、参数化 preview 能生成、但没有 apply”，使用：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-width-preview-smoke `
  --port 50051 `
  --aedt-version 2024.2 `
  --expected-project "Board" `
  --expected-design "Layout1" `
  --target-width 4.3mil `
  --variable-name W_line `
  --variable-value 4.3mil `
  --net N1 `
  --layer L1 `
  --output-dir C:\AnsysAgent\smoke\width-preview `
  --confirm-preview-only
```

`--net` 和 `--layer` 可以重复提供；不写时只按 `--target-width` 过滤。`--variable-value` 省略时默认与
`--target-width` 相同，`--variable-name` 默认是 `W_line`。该命令只推进到
`preview_parameterization`，不会调用 operation apply，也不会保存工程。退出前会 release 当前 wrapper，
因此未批准的 operation preview 不能在另一个会话中被偷偷复用。

输出目录包含：

- `live_width_preview_smoke.json`；
- `live_width_preview_smoke.json.sha256`；
- `missions.db`。

合格证据必须满足：

```text
status = passed
stopped_after_node = preview_parameterization
approval_required = true
apply_executed = false
project_dirty = false
project_saved = false
target_count > 0
```

这个命令适合生产工程上线前的“最后一公里”验证。它证明选择和 preview 链路可用，但不能替代在测试工程
副本上完成一次人工批准、apply、readback 和不保存检查。

发现 AEDT：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
```

查看 API Memory 状态：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli status
```

强制重建 API Memory：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli prepare --force
```

检查依赖：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import ansys.aedt.core,pyedb,clr; print('OK')"
```

查看当前 Automation Tab 安装位置：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.desktop install --help
```

## 19. 故障排查

### 19.1 出现 `Computer` 或 `Chrome` permission deny 警告

这是旧 launcher 向 Claude Code 传入了已经不存在的 deny tool 名称。更新到当前发布版本，并从新安装
目录重新安装 Automation Tab 入口。当前版本不会再传这两个名称。

### 19.2 设计名出现 `0;`

立即停止当前 PowerShell，不要继续调用 layout/HFSS wrapper。更新并重新安装入口。检查工程中是否已经
出现空白的 `0;...` 设计：

- 如果工程没有保存且没有其他修改，关闭不保存并重新打开；
- 如果有其他修改，先手工确认错误设计为空，再从 AEDT UI 删除；
- 不要让助手自动删除设计。

### 19.3 走线列表返回 0

依次检查：

1. 当前设计类型是否为 `HFSS 3D Layout Design`；
2. 工程和设计名是否与 AEDT UI 一致且没有 `0;`；
3. Agent 是否误用了 HFSS 3D inventory；
4. 不带 selector 先列出 line 总数；
5. 检查宽度表达式是 `4.3mil`、`4.3 mil`、变量名还是等价毫米值；
6. 检查对象是否真的是 Path/line，而不是 polygon、via 或其他 primitive。

不要在返回 0 后通过创建同名设计来“重试”。当前 Runtime 会拒绝任何不存在的设计，防止 PyAEDT
隐式创建设计。

### 19.4 成功连接后反复 attach

停止会话并更新 launcher。当前 system context 明确要求成功 attach 后复用同一个 `live_session_id`。
如果 attach 本身失败，再检查端口、AEDT 版本和 MCP 日志，不要无条件循环。

### 19.5 `target_forbidden`、`project_forbidden` 或 `design_forbidden`

Desktop 会话被绑定到按钮来源：

- `target_forbidden`：调用了其他 PID 或端口；
- `project_forbidden`：活动工程已经切换；
- `design_forbidden`：活动设计已经切换或名称不一致。

回到 AEDT，激活正确工程和设计，然后关闭旧 PowerShell，从 Automation Tab 重新启动新会话。

### 19.6 找不到端口或连接超时

- 确认 AEDT 正在同一个 Windows/RDP 用户会话中运行；
- 使用 `live-sessions` 读取实际端口；
- 不要假定固定端口一直有效；
- 多 AEDT 并行时按 PID 核对；
- 检查本机安全软件是否阻止 loopback；
- AEDT 2024 R2 早期 SP 可尝试 `PYAEDT_USE_PRE_GRPC_ARGS=True`。

### 19.7 原生审批框不可见

审批框属于当前交互式 Windows 用户。Windows 服务、计划任务、纯 SSH 会话或另一个 RDP session
可能看不到它。确保 AEDT、Ansys Agent PowerShell 和用户桌面属于同一个会话。

### 19.8 审批过期

审批 token 默认五分钟过期，只能使用一次，并绑定 action、resource、preview 和 snapshot digest。
过期后不要复用 token；让用户明确要求重新预览。

### 19.9 `clr` 或 PyEDB 导入失败

确认安装的是：

```text
pyedb[dotnet]==0.80.2
ansys-pythonnet==3.1.0rc8
```

然后运行环境验收脚本。不要把系统 Python 中能 import PyEDB 当作项目 `.venv` 已正确安装的证据。

### 19.10 API Memory 不可用

已知 Harness 仍可工作，只是未知能力 fallback 关闭。运行：

```powershell
D:\ansys-agent\.venv\Scripts\python.exe `
  -m aedt_agent.knowledge.api_memory_cli prepare --force
```

不要从另一台机器直接复制知识图，除非包版本、源码 digest 和路径全部一致。

## 20. 升级与回滚

推荐并行安装，不覆盖旧目录：

```text
D:\ansys-agent-0.1.0-old
D:\ansys-agent-0.1.0-preview1
```

升级步骤：

1. 把新包安装到新的空目录；
2. 运行环境验收；
3. 对现有 AEDT 执行只读 smoke；
4. 打开测试工程副本；
5. 从新目录安装 Automation Tab 入口；
6. 先完成只读查询；
7. 再完成一次 preview、拒绝审批测试；
8. 最后完成副本工程的实际 apply 和 readback；
9. 验收后再停用旧目录。

回滚时从旧目录重新执行 Desktop `install` 即可。不要在同一个目录里混装两个版本的源码和 `.venv`。

## 21. 上线验收清单

### 安装

- [ ] ZIP SHA256 与发布页一致；
- [ ] `VerifyOnly` 成功；
- [ ] Python 为 3.11 x64；
- [ ] `pip check` 成功；
- [ ] PyAEDT、PyEDB、`clr` 导入成功；
- [ ] API Memory 为 ready。

### AEDT 连接

- [ ] `live-sessions` 找到正确 PID 和端口；
- [ ] `live-info` 返回正确工程、设计和设计类型；
- [ ] 设计名不含内部 `0;` 前缀；
- [ ] release 后 AEDT 和工程仍保持打开。
- [ ] `live-workflow-smoke` 通过并生成可校验的 SHA256 evidence；
- [ ] `live-width-preview-smoke` 命中预期对象，且 `apply_executed=false`、`project_dirty=false`。

### Desktop 入口

- [ ] `Automation -> Ansys Agent` 可见；
- [ ] 点击后只 attach 一次；
- [ ] PowerShell 工作目录指向当前安装根；
- [ ] 最新 `session.json` 的 `project_root` 指向批准的安装目录；
- [ ] 最新 `session.json` 的 port/project/design/type 与 AEDT GUI 一致；
- [ ] 没有 `Computer`/`Chrome` deny rule 警告；
- [ ] 切换工程或设计后旧会话会拒绝继续操作。

### 写操作

- [ ] preview 不修改工程；
- [ ] 点击 No 后不 apply；
- [ ] 点击 Yes 后一次性 token 可用；
- [ ] apply 后 readback 数量一致；
- [ ] 失败时 rollback；
- [ ] 未明确要求时不保存；
- [ ] 保存需要第二次独立审批。

## 22. 安全边界摘要

- 不自动选择其他 AEDT 进程；
- 不自动创建不存在的 project/design；
- 不关闭 AEDT 或工程；
- 不把源码证据当作执行权限；
- 不开放任意 shell、Python 或 COM；
- 不允许模型自行签发审批 token；
- 不把修改审批扩大为保存审批；
- 不自动把探索结果写进 Harness；
- 不在未验证 readback 时报告成功。

## 23. 相关文档

- [Ansys Assistant 部署与操作说明](ansys-assistant-operations-guide.zh.md)
- [Windows Server 离线部署](offline-windows-server-deployment.md)
- [AEDT Desktop Claude Code 入口](aedt-desktop-claude-entry.md)
- [通用交互式 Ansys 助手](interactive-ansys-assistant.md)
- [Ansys 助手能力分层与自进化架构](ansys-capability-evolution.md)
- [MCP 对比与 benchmark](ansys-mcp-comparison-2026-07-17.md)
