# Ansys Assistant 中文使用手册

本文面向在 Windows 工作站或 Windows Server 上使用 AEDT、HFSS、HFSS 3D Layout 的工程师。
目标是让使用者从安装、连接现有 AEDT 会话开始，通过对话完成查询、预览、审批、修改和验证，
同时清楚知道助手会做什么、不会做什么，以及出现异常时如何恢复。

本文假定目标机器上的 Claude Code 和对应模型已经可用，不包含它们的安装或模型配置过程。

## 快速导航

| 你的目标 | 建议先看 |
|---|---|
| 在一台新 Windows Server 上安装 | 第 3～6 节 |
| 连接已经打开的 AEDT 工程 | 第 7～9 节 |
| 用自然语言查询或修改当前工程 | 第 10～14 节 |
| 操作文件副本而不是当前工程 | 第 15 节 |
| 使用可暂停、可恢复的严格 Workflow | 第 16 节 |
| Harness 没有现成能力 | 第 17 节 |
| 做上线前真实 AEDT 验收 | 第 18、21 节 |
| 遇到端口、设计名、审批或 PyEDB 问题 | 第 19 节 |

## 十分钟上手

已经完成离线安装、AEDT 2024 R2 正在运行且 Claude Code 可用时，最短路径如下。

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
- 查询 HFSS 3D 的对象、面、setup、port、boundary 和 report；
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

## 4. 获取离线发布包

发布包位于 GitHub Releases，不放进 Git 历史，避免每次 clone 都携带约 100 MB wheelhouse。

打开发布页 <https://github.com/z331225718/ansys-agent/releases>，选择最新的
`Ansys Assistant Offline` 预发布版本，并同时下载：

- `ansys-agent-offline-0.1.0-win-amd64-py311.zip`；
- `ansys-agent-offline-0.1.0-win-amd64-py311.zip.sha256`。

具体 SHA256 以对应 Release 描述和同一 Release 下的 `.sha256` 文件为准。

联网中转机可以使用：

```powershell
gh release download <release-tag> `
  --repo z331225718/ansys-agent `
  --pattern "ansys-agent-offline-*.zip*" `
  --dir C:\AnsysAgentTransfer
```

把 ZIP 和 `.zip.sha256` 一起传到目标机器。不要只传 ZIP 而丢掉旁路校验文件。

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

只有在远端项目源码已经更新到目标 commit 后，才能单独运行依赖更新脚本。这个脚本不会替你更新
项目源码：

```powershell
Set-Location D:\ansys-agent

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\online\Update-AnsysAgentDependencies.ps1 `
  -InstallRoot D:\ansys-agent
```

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
| Layout 只读 | `list_live_layout_paths`、`get_live_layout_routing_inventory`、`get_live_layout_object_inventory` | 查询 Path、net、layer、宽度和对象分类 |
| 变量与 Setup | `get_live_aedt_variable_inventory`、`get_live_aedt_setup_inventory` | 构造写操作前的准确事实 |
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

### 12.2 查询 HFSS 3D 几何

```text
列出当前 HFSS 3D 设计中的对象、材料、face id 和已有 boundary，只读。
```

HFSS 3D Layout 和 HFSS 3D 是不同数据模型。设计类型为 `HFSS 3D Layout Design` 时，不应调用
HFSS 3D object/face inventory；应使用 layout path inventory。

### 12.3 查询求解状态

```text
查询当前 HFSS 设计 Setup1 的求解状态和资源信息，不启动新求解。
```

## 13. 创建和求解任务

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

结果导出只能写入 `AEDT_AGENT_EXPORT_ROOT` 下的 server-managed 目录，并生成 SHA256 evidence manifest。

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
| `layout_live_audit` | 复用当前已 attach 会话，对活动 3D Layout 执行 routing/object/variable/setup 只读审计 |
| `layout_live_parameterize_width` | 在当前 3D Layout 中选择 Path、冻结线宽参数化 preview、审批后 apply 并生成 readback scorecard |
| `layout_live_solve_start` | 核对当前 3D Layout Setup/Sweep 和资源预算，审批后非阻塞启动求解并验证提交状态 |
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
`layout_live_parameterize_width` 则把本手册的核心线宽参数化用例提升为四步 live Workflow。
`layout_live_solve_start` 使用相同的 server-owned binding 和双层审批模型：先验证 Setup/Sweep，再冻结 cores、
tasks、gpus 和 auto settings，最后以非阻塞方式提交求解并读取运行状态。它不会把“API 返回成功”单独当成
通过，scorecard 还会检查 run id、资源预算、非阻塞标志和项目未保存状态。
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

- [Windows Server 离线部署](offline-windows-server-deployment.md)
- [AEDT Desktop Claude Code 入口](aedt-desktop-claude-entry.md)
- [通用交互式 Ansys 助手](interactive-ansys-assistant.md)
- [Ansys 助手能力分层与自进化架构](ansys-capability-evolution.md)
- [MCP 对比与 benchmark](ansys-mcp-comparison-2026-07-17.md)
