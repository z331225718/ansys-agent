# Ansys Assistant 部署与操作说明

本文是面向实际交付的操作手册，适用于以下环境：

- Windows Server x64；
- 已安装 Ansys Electronics Desktop 2024 R2；
- Claude Code 和对应模型已经可用；
- 服务器可以通过 pip 访问 PyPI 或组织内部 Python 镜像；
- 需要从 AEDT 中打开助手，复用当前正在运行的 AEDT 会话，并操作当前 HFSS/HFSS 3D Layout 工程。

本文不介绍 Claude Code 和模型的安装。完整能力、全部 Workflow、离线 wheelhouse 和维护者验收细节见
[Ansys Assistant 中文使用手册](ansys-assistant-user-guide.zh.md)。

## 阅读路线

这份说明按“安装一次、每天使用、出现问题时排查”的顺序组织。不同读者可以直接从对应章节开始：

| 使用者 | 建议阅读顺序 |
|---|---|
| 首次部署管理员 | 第 1～6、12～16 节 |
| 每天操作 AEDT 的工程师 | 第 1、4～11 节 |
| 只需要把 4.3mil 线宽参数化 | 第 4～8 节 |
| 需要更新/删除 HFSS 材料，或创建 Layout 材料、创建/更新/删除 Via | 第 4～7、8A～8F 节 |
| 需要严格移动已有 HFSS solid/sheet | 第 4～7、8G 节 |
| Harness 没有现成能力 | 第 10 节 |
| 升级、移交或故障恢复 | 第 12～16 节 |

全文命令中的目录、端口、版本、工程和设计名都是示例。执行前至少替换以下值：

```text
D:\ansys-agent     -> 实际项目安装目录
50051               -> live-sessions 发现的实际 gRPC 端口
2024.2              -> 目标 AEDT 版本
Board               -> AEDT GUI 中的活动工程名
Layout1             -> AEDT GUI 中的活动设计名
```

最重要的默认约定是：**只读无需审批；写入必须 preview 和自动 token；未明确要求时不保存工程。**

从 AEDT Desktop 入口启动的 Claude Code 与 Windows 原生确认均已取消；这不会跳过 Runtime 的 preview、绑定目标、
stale-state 核验、自动备份和 readback。每个 AEDT 写 preview 返回一次性自动 token，apply 只能使用该 token。会话启用 `autoCompactEnabled: true`，并以 120,000-token window 的 60% 阈值触发自动
compact；也可输入 `/compact` 手动压缩。

## 1. 使用原则

助手不是自由执行 PyAEDT 脚本的终端。它按以下顺序选择能力：

```text
严格 Workflow
  -> 已实测的 typed Harness
  -> API Memory 查询 + 受控 Exploration
  -> 明确报告暂不支持
```

日常写操作固定执行：

```text
核对 AEDT 会话
  -> 读取 inventory
  -> 精确选择目标
  -> preview
  -> 取得 automatic token
  -> apply
  -> typed readback
  -> 失败时 rollback
  -> 默认不保存工程
```

必须遵守以下边界：

1. 不自动创建不存在的工程或设计。
2. 不把 HFSS 3D Layout 的内部名称 `0;DesignName` 当作真实设计名。
3. 不在返回 0 个对象后通过创建同名设计重试。
4. API Memory 只提供 PyAEDT/PyEDB 源码证据，不直接获得工程写权限。
5. 未明确要求保存时，只修改 AEDT 内存，不保存 `.aedt` 工程。
6. 修改和保存需要各自独立 preview/token，修改 token 不能用于保存。
7. `release` 只释放助手创建的 PyAEDT wrapper，不关闭 AEDT，不关闭工程。

## 2. 推荐目录

建议服务器使用固定目录：

```text
D:\ansys-agent             当前批准的项目安装目录
D:\ansys-agent-runs        smoke、Workflow 和导出 evidence
D:\ansys-agent-releases    源码包或离线发布包
D:\aedt-project-backups    工程副本和人工备份
```

项目依赖只安装到 `D:\ansys-agent\.venv`。不要使用 AEDT 内嵌 Python，也不要依赖用户全局安装的
PyAEDT/PyEDB。即使系统 Python 已经能导入 PyAEDT，也不能证明助手环境正确。

## 3. 首次安装

### 3.1 检查外部 Python

项目使用 CPython 3.12 x64：

```powershell
py -3.12 -c "import struct,sys; print(sys.executable); print(sys.version); print(struct.calcsize('P')*8)"
```

最后一行必须为 `64`。如果服务器没有 `py` launcher，后续命令可以把 `py -3.12` 替换成组织批准的
Python 3.12 绝对路径，例如 `C:\Python312\python.exe`。

### 3.2 获取项目

服务器能访问 GitHub 时：

```powershell
$Root = "D:\ansys-agent"
$Ref = "codex/ansys-assistant-runtime"

git clone --branch $Ref --single-branch `
  https://github.com/z331225718/ansys-agent.git $Root

Set-Location $Root
git rev-parse HEAD
git status --short --branch
```

正式环境应把 `$Ref` 固定为已批准的 release tag 或完整 commit。不要让生产服务器无条件跟随变化中的分支。

服务器不能访问 GitHub、但可以访问 PyPI 时，在联网机下载批准 commit 的源码 ZIP，将其传到服务器并解压为
`D:\ansys-agent`，然后继续下一节。源码目录必须直接包含 `pyproject.toml`、`src`、`docs` 和 `scripts`。

### 3.3 创建项目虚拟环境并安装依赖

```powershell
Set-Location D:\ansys-agent

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --editable ".[desktop]"
.\.venv\Scripts\python.exe -m pip check
```

`.[desktop]` 会安装项目锁定的 PyAEDT、PyEDB DotNet 后端、FastMCP 和 codebase-memory-mcp。AEDT
2024 R2 需要 `pyedb[dotnet]`；不要只安装基础 `pyedb`。

注意：PyPI 的 `codebase-memory-mcp` wheel 自身不包含约 260 MB 的 Windows 原生程序，首次运行会访问
GitHub Releases。目标服务器不能访问 GitHub 时，必须使用项目离线 Release；安装器会从包内安装经过
SHA256 校验的原生程序，不能仅依赖 `pip install codebase-memory-mcp`。

不要在安装后执行无版本约束的：

```powershell
pip install -U pyaedt pyedb
```

Harness 是按项目锁定版本验收的。依赖升级必须与代码更新、API Memory 重建和真实 AEDT 回归一起完成。

### 3.4 检查实际依赖来源

```powershell
.\.venv\Scripts\python.exe -c `
  "import importlib.metadata as m,sys; print(sys.executable); print('pyaedt',m.version('pyaedt')); print('pyedb',m.version('pyedb')); print('fastmcp',m.version('fastmcp'))"
```

第一行必须位于 `D:\ansys-agent\.venv`。随后执行真实 import 检查：

```powershell
.\.venv\Scripts\python.exe -c `
  "import ansys.aedt.core,pyedb,clr,fastmcp,codebase_memory_mcp; print('imports: OK')"
```

### 3.5 构建本机 API Memory

无需把 PyAEDT/PyEDB 源码复制到项目。助手直接索引 `.venv\Lib\site-packages` 中已安装包的源码：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli prepare --force
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli status
```

`status` 应返回 ready。构图失败不会禁用已有 Harness，但会关闭 Harness 未覆盖能力的源码查询与受控探索。

API Memory MCP 会在单个 Claude 会话内复用受限的 `codebase-memory-mcp` stdio 子进程，不会把该原生
MCP 直接暴露给 Agent。它只允许项目 facade 已登记的源码查询工具，并且子进程环境不继承审批密钥或 LLM
密钥。已验证状态会短暂缓存以避免每个查询重复扫描两份源码；最终 operation evidence 校验始终强制重新计算
源码 digest 和索引状态，因此缓存不能放宽写工程的审批或证据要求。

### 3.6 检查能力目录

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2
```

这一步用于确认当前安装确实包含计划使用的能力。不要仅凭模型回答“支持”判断部署是否完整。

## 4. 连接已经运行的 AEDT

### 4.1 在 AEDT 中准备工程

1. 使用与助手相同的 Windows/RDP 用户启动 AEDT 2024 R2。
2. 在 AEDT GUI 中打开目标工程。
3. 等待工程完全加载。
4. 在 Project Manager 中单击并激活目标设计。
5. 第一次写操作必须使用测试工程副本。

不要为 GUI 会话手工执行 `ansysedt.exe -grpcsrv 50051`。现代 Windows secure gRPC/WNUA 下，实际
listener 可能与手工指定值不同，应使用会话发现命令读取真实端口。

### 4.2 发现实际端口

```powershell
Set-Location D:\ansys-agent
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
```

如果只有一个 AEDT，记录其 PID、版本和 `grpc_port`。如果同时运行多个 AEDT，必须结合 PID、版本和
活动工程确认，不能只选择列表中的第一项。

下面假设真实端口为 `50051`。

### 4.3 只读核对工程和设计

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-info `
  --port 50051 `
  --aedt-version 2024.2
```

输出中的工程、设计和设计类型必须与 AEDT GUI 一致。HFSS 3D Layout 设计名应显示为：

```text
LayoutDesign
```

不能显示为：

```text
0;LayoutDesign
```

如果出现 `0;` 前缀，立即停止，不要继续查询或修改。更新项目代码并重新安装 AEDT 入口；同时人工检查工程
是否被旧版本意外创建了空设计。

## 5. 安装 AEDT 内的入口

使用发现到的真实端口安装当前用户的 Automation Tab 扩展：

```powershell
Set-Location D:\ansys-agent
.\.venv\Scripts\python.exe -m aedt_agent.desktop install `
  --port 50051 `
  --version 2024.2
```

安装器使用 PyAEDT Custom Extension/Automation Tab 接口写入当前用户的 PersonalLib，不修改 AEDT
安装目录。刷新后应在 AEDT 中看到：

```text
Automation -> Ansys Agent
```

如果同时运行多个 AEDT，会话端口变化或切换到另一个 AEDT 进程，应重新执行会话发现，并为正确端口重新
安装入口。升级项目到新目录后，也必须从新目录重新执行 `install`，否则按钮可能继续加载旧代码。

卸载入口：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.desktop uninstall --port 50051
```

## 6. 第一次从 AEDT 启动

### 6.1 点击前检查

- AEDT 当前工程和当前设计正确；
- 没有尚未结束的旧 Ansys Agent Git Bash；
- 当前是可见的交互式 RDP 桌面，不是 Windows 服务或纯 SSH 会话；
- 当前工程已有人工备份或使用的是副本；
- 本次是否允许保存已经事先明确，未明确时视为不保存。

### 6.2 点击入口

在 AEDT 中点击 `Automation -> Ansys Agent`。入口会打开可见 Git Bash，并启动隔离的 Claude Code
会话。正常首次行为是：

1. 连接按钮来源的 AEDT 端口一次；
2. 读取活动工程和设计；
3. 报告端口、工程、设计、设计类型和 AEDT 版本；
4. 等待用户任务。

核对报告内容与 AEDT GUI。任一项不一致，关闭本次 Git Bash，不要让 Agent 通过反复 attach 自行修复。

看到下面两类行为应立即停止：

- 设计名带 `0;`；
- attach 成功后仍反复调用 attach，或尝试创建同名设计。

### 6.3 会话隔离信息

每次点击会在项目目录生成独立审计目录：

```text
.aedt-agent\desktop\sessions\<session-id>\
  mcp.json
  context.md
  claude-settings.json
  launch-claude.sh
  session.json
```

检查最新会话：

```powershell
$Session = Get-ChildItem D:\ansys-agent\.aedt-agent\desktop\sessions -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content (Join-Path $Session.FullName "session.json") -Raw -Encoding UTF8 |
  ConvertFrom-Json |
  Format-List
```

重点检查 `project_root`、`context.port`、`project_name`、`design_name` 和 `design_type`。这些会话文件不含
模型 API key 或 approval secret，可以保留用于排障。

## 7. 日常对话方法

### 7.1 先做只读查询

第一次连接某个工程时先发送：

```text
先核对当前 AEDT 工程、设计、设计类型和版本。只读列出当前设计的主要对象、总数和关键属性。
不要修改、不要创建工程或设计、不要保存；完成后复用当前会话并等待。
```

对于 3D Layout：

```text
只读列出当前 HFSS 3D Layout 的 Path 总数，并返回每条 Path 的名称、net、layer 和
width expression。不要修改、不要保存。
```

### 7.2 提交受控写任务

推荐模板：

```text
在当前设计中，将 <明确对象或筛选条件> 的 <属性> 改为 <目标值>。
优先使用已注册的严格 Workflow 或 typed Harness。先读取 inventory 并列出精确目标，
再 preview；只有我在 Windows 原生确认框批准后才能 apply。apply 后逐项回读，
失败时报告 rollback 是否完整。不要保存工程。
```

一次可靠请求至少说明：

- 当前设计中的对象类型；
- 对象名、net、layer 或其他筛选条件；
- 原值或期望命中条件；
- 目标属性和值；
- 是否需要求解或导出；
- 是否保存工程。

条件不完整时，助手应追问，不能猜测目标或保存策略。

### 7.3 理解审批

只读工具不会弹审批框。写操作必须先返回 preview，再出现 Windows 原生确认框。

点击 `Yes` 前检查：

- 工程和设计名称；
- action 和 resource；
- 目标对象数量及对象名；
- 旧值和新值；
- 是否包含求解、导出或保存；
- preview 是否仍对应当前工程状态。

点击 `No` 或等待超时后，助手必须停止，不应自动生成新的 preview 重试。审批 token 默认五分钟过期、仅可
使用一次，并绑定 action、resource 和 snapshot digest。Claude Code 自己的工具授权不能代替 Windows
原生审批。

### 7.4 判断任务是否真正完成

不要只看自然语言中的“成功”。写任务至少应满足：

```text
status = verified
verified_count = target_count
failed_count = 0
project_saved = false     # 除非本次另行批准了保存
```

失败时还要看到明确的 rollback 状态。若 rollback 不完整，立即停止后续操作并保留 AEDT 现场，不要自动保存。

### 7.5 一轮标准对话示例

下面是一轮推荐的完整对话。重点不是逐字照抄，而是让每个阶段都有明确停止条件。

第一步，只核对身份：

```text
请复用 AEDT 按钮来源的会话，只连接一次。报告实际端口、AEDT 版本、活动工程、活动设计和设计类型。
不要创建或打开其他工程、不要创建设计、不要修改、不要保存。报告后等待我的下一条指令。
```

此时人工把五项信息与 AEDT GUI 比较。设计名带 `0;`、工程不一致或连接后继续反复 attach，均应退出当前
PowerShell，而不是让模型继续尝试。

第二步，只读盘点目标：

```text
只读列出当前 3D Layout 的所有 Path。返回总数，以及每条 Path 的 name、net、layer 和 width expression。
如果结果被截断请明确说明。不要修改、不要保存，完成后等待。
```

第三步，提出有边界的写任务：

```text
只处理 net=DDR_DQ0、layer=L1 且 width expression=4.3mil 的 Path。
把命中的 Path 参数化为设计变量 W_line=4.3mil。使用已注册的 typed Harness 或严格 Workflow：
先给出精确目标和 preview，等待 Windows 原生审批；批准后 apply 并逐项 readback。
其他 Path、变量、工程和设计不得修改。失败时报告 rollback 是否完整。不要保存工程。
```

第四步，检查 preview 和原生审批框。数量、名称、原值或新值任一项不符合预期就点击 `No`，然后改写任务条件；
不要先批准再要求助手“改回来”。

第五步，检查结果：

```text
请汇总本次 action、目标数、verified 数、failed 数、rollback 状态、工程是否 dirty、是否保存，
并再次只读回报 W_line 和所有目标 Path 的 width expression。不要执行新的修改。
```

只有结果回读正确后，才决定是否另起一次保存审批。测试工程通常直接保持未保存，由工程师在 AEDT GUI 中检查。

### 7.6 复杂任务怎么描述

复杂请求建议按下面七项组织。信息不足时先让助手只读查询，不要让它自行猜测：

| 项目 | 应提供的内容 | 示例 |
|---|---|---|
| 范围 | 当前工程、当前设计或文件副本 | 当前活动 3D Layout 设计 |
| 对象 | 对象类型和精确筛选条件 | Path，net=DDR_DQ0，layer=L1 |
| 前置条件 | 必须已存在或必须不存在的对象 | `W_line` 不存在；padstack 已存在 |
| 修改 | 属性和目标表达式 | width 改为 `W_line` |
| 验证 | apply 后必须回读的字段 | Path width、变量值、目标数量 |
| 失败策略 | 停止、rollback 和现场保留要求 | 任一失败则全批 rollback |
| 保存策略 | 不保存或单独审批保存 | 不保存 |

可复用模板：

```text
在当前 <设计类型> 中，只处理 <对象类型和精确筛选条件>。
前置条件是 <必须存在/不存在的依赖>；目标修改为 <属性和值>。
先 inventory，再 preview；Windows 原生审批后才能 apply。apply 后回读 <验证字段>。
任一对象失败时 <全批回滚/停止并报告>。不得修改 <排除范围>。不要保存工程。
```

当一个任务同时包含“创建对象、求解、导出、保存”时，应拆成多个审批阶段。不要用一句“全部完成”授权所有写入；
每一阶段完成 readback 或 evidence 检查后，再批准下一阶段。

## 8. 示例：把 4.3mil 线宽参数化为 W_line

### 8.1 推荐请求

```text
在当前 HFSS 3D Layout 中，找出 LineWidth=4.3mil 的所有 Path。
先列出名称、net、layer 和原始 width expression。
使用 layout_live_parameterize_width 严格 Workflow，把这些 Path 参数化为设计变量
W_line，初值为 4.3mil。每一步都走 preview 和审批，apply 后逐项回读验证。
不要创建新设计，不要求解，不要保存工程。
```

如果只修改某个 net 或某一层，应把条件写进请求，例如：

```text
只处理 net=DDR_DQ0 且 layer=L1、width expression=4.3mil 的 Path，其他对象不得修改。
```

### 8.2 预期过程

```text
复用已连接的 live_session_id
  -> 确认 design_type = HFSS 3D Layout Design
  -> 读取 Path inventory
  -> 精确筛选 4.3mil
  -> preview 参数化目标
  -> Windows 原生审批
  -> apply
  -> 回读 W_line 和每条 Path 的 width expression
  -> 报告未保存
```

严格 Workflow 可能同时出现 Graph step 审批和真正写 AEDT 的 operation 审批。Graph 审批只允许 Workflow
推进一步，不能代替 operation 审批。

### 8.3 单位与表达式

`4.3mil`、`4.3 mil` 和 `4.3MIL` 可以按规范化表达式匹配，但 `0.10922mm` 虽然物理等价，不一定自动归入
同一选择条件。设计中混用单位时，应先只读列出全部表达式，再由用户明确范围。

### 8.4 成功标准

- 命中数量与 preview 一致；
- 设计变量 `W_line` 存在且值为 `4.3mil`；
- 每条目标 Path 回读为 `W_line`；
- 未修改其他 Path；
- 没有创建新工程或新设计；
- 工程没有自动保存。

## 8A. 示例：严格批量更新已有 HFSS 工程材料

推荐请求：

```text
使用 hfss_live_material_update 更新当前 HFSS 工程中的两个已有材料：
1. HarnessLaminateA 的 permittivity 改为 4.4，appearance 改为 [44,55,66,0.6]；
2. HarnessLaminateB 的 conductivity 改为 0.5S/m，dielectric_loss_tangent 改为 0.021。
材料名必须精确匹配。先冻结完整工程材料目录和所有引用实体；Windows 原生审批后批量 apply，
只允许请求字段变化，材料分类、引用和 Solve Inside 必须保持不变。失败时按原生定义整批恢复。
不要保存工程。
```

每批支持 1～32 个已有工程材料。每个条目至少有一个实际变化；大小写不一致、重复名称、no-op、非 simple
数值材料、求解进行中或跨越 `100000S/m` 介质/导体分类边界都会在 preview 阶段拒绝。可写字段仅限五个
数值型电磁属性和 `[R,G,B,transparency]` 外观。

preview 冻结完整 project material catalog、目标材料原生 `GetData`、PyAEDT 对象 identity，并扫描最多
5000 个对象以冻结全部引用实体。成功结果必须证明 `updated_material_names` 与请求顺序一致、请求字段 typed
readback 正确、未请求字段和非目标材料不变、`references_before == references_after`、
`automatic_rollback_on_failure=true`、`project_saved=false`。

失败恢复使用冻结的原生定义调用 `EditMaterial`，再刷新 PyAEDT 材料缓存并比较完整 catalog digest；不是简单
地把几个显示数值写回。该能力已通过隔离 AEDT 2026.1 + PyAEDT 1.3.0 实测，包括双材料批量更新、已有实体
引用保持、stale、分类边界拒绝、磁盘工程 SHA-256 不变，以及真实写入故障后的原生 definition digest 精确恢复。
AEDT 2024 R2 上线前仍要执行中文使用手册第 18.1 节的真实测试。

## 8B. 示例：严格批量删除未引用的 HFSS 工程材料

推荐请求：

```text
使用 hfss_live_material_delete 删除当前 HFSS 工程中精确名称为
HarnessUnusedA、HarnessUnusedB 的两个工程材料。先冻结完整工程材料目录、两个材料的原生定义、
所有 solid 引用和全部 boundary 属性；只允许删除零实体引用、零边界引用的材料。Windows 原生审批后
按请求顺序批量删除，并从 Definition Manager 回读确认名称不存在。任一删除或最终验证失败时，
使用冻结的原生定义重建本批已删除材料，并要求完整材料目录和边界快照恢复。不要保存工程。
```

`names` 必须包含 1～32 个精确、大小写匹配且批内不重复的已有工程材料名。preview 会扫描最多 5000 个
HFSS 对象，并检查所有 boundary 的原生属性；任何实体材料引用或例如 Finite Conductivity 边界中的材料引用
都会在调用 AEDT 删除 API 前被拒绝。不要先把引用材料强行删除再观察 AEDT 报错，这在真实 AEDT 中会让
当前 gRPC wrapper 进入不可靠状态。

成功结果必须包含 `status=verified`、与请求顺序一致的 `deleted_material_names`、精确删除数量、零引用计数、
非空 `absence_digest`、`automatic_rollback_on_failure=true` 和 `project_saved=false`。成功只表示材料已从
当前 AEDT 内存工程删除；是否写入 `.aedt` 文件仍需使用独立的保存 preview 和审批。

失败回滚使用 preview 冻结的 Definition Manager `GetData` 调用原生 `AddMaterial`，再刷新 PyAEDT cache，
并比较完整 catalog digest、全部 boundary 快照和零引用状态。若外部操作已经用同名材料占位，Harness 不会
覆盖它，而会明确报告 rollback incomplete。

该能力已通过隔离 AEDT 2026.1 + PyAEDT 1.3.0 实测，包括双材料删除、实体引用拒绝、Finite Conductivity
边界引用拒绝、外部目录变化后的 stale、磁盘工程 SHA-256 不变，以及真实删除后的故障注入和原生定义整批
重建。AEDT 2024 R2 上线前仍需执行中文使用手册第 18.1 节的同名真实测试。

## 8C. 示例：创建 3D Layout 材料并分配 stackup 层

推荐请求：

```text
在当前 HFSS 3D Layout 工程中创建材料 HarnessLayoutLaminate：permittivity=3.7、
permeability=1.0、conductivity=0.001S/m、dielectric_loss_tangent=0.012。
使用 layout_live_material_create_assign，把它分配给精确层 D1 的 material 字段。
先 preview 完整材料目录和 stackup 目标；Windows 原生审批后原子 apply，回读材料定义和层字段。
失败时先恢复层、再删除新材料并验证完整快照。不要保存工程。
```

允许的组合：

| 层类型 | 字段 | 材料角色 |
|---|---|---|
| dielectric | `material` | 介质 |
| signal | `fill_material` | 介质 |
| signal | `material` | 导体 |

材料角色按 PyAEDT 的 100000 S/m 导体阈值检查。层名必须精确匹配，材料名不能与工程或 AEDT 材料库冲突。
成功结果应包含 `status=verified`、材料五个 simple 电磁属性、definition digest、层 ID/type、指定字段回读、
材料目录 digest、stackup digest 和 `project_saved=false`。

PyAEDT 的 `ChangeLayer` 会把 `Thickness0` 从例如 `0.035mm` 重排为物理等价的
`3.5e-05meter`，并可能用 wrapper 默认值覆盖 signal 层显示颜色。Harness 对厚度按 typed 数值和单位做
语义比较，同时在写入后恢复原生层颜色；这两项均已纳入真实 AEDT 回读，不会把 UI 颜色漂移或物理厚度变化
误当成成功。

该能力已在隔离 AEDT 2026.1 + PyAEDT 1.3.0 中实测，包括三种层角色、外部 stale、库冲突、磁盘工程
SHA-256 不变、真实写入后的故障注入回滚，以及回滚后同名重新 preview。AEDT 2024 R2 服务器上线前仍需在
测试工程副本上复验。

## 8D. 示例：批量创建 3D Layout Via

推荐请求：

```text
使用 layout_live_via_create，在当前 3D Layout 中创建两个 Via：
1. HarnessVia1，padstack=PlanarEMVia，位置 [1.0,2.0]mm，TOP 到 BOT，net=N_EXISTING，
   rotation=45deg，hole override=0.25mm，创建后锁定；
2. HarnessVia2，同一 padstack/layer/net，位置 [3.0,4.0]mm，rotation=-30deg，使用 padstack 默认孔径。
先核对名称不存在，并冻结 padstack、完整 stackup、signal layer、net 和 model unit；审批后原子创建，
用 AEDT 原生属性逐项回读。任何一项失败时删除本批全部新 Via。不要保存工程。
```

每个 Via 必须显式提供 `name`、`padstack`、`x`、`y`、`top_layer`、`bottom_layer` 和 `net_name`；可选
`rotation_degrees`、`hole_diameter`、`lock_position`。一次最多 32 个。Harness 不会顺手新建 padstack、层或 net，
名称大小写也必须与 AEDT 完全一致。

PyAEDT 1.3.0 的 `create_via(rotation=...)` 在 AEDT 2026.1 实测不会可靠写入 Angle，因此 Harness 创建后会
显式写入公开 `angle` 属性，再从 `BaseElementTab` 回读 Name、Net、Padstack Definition、Start/Stop Layer、
Location、Angle、LockPosition 和 HoleDiameter。删除后不信任 PyAEDT 的旧 Via cache，而是再次调用 AEDT 原生
`FindObjects` 确认对象确实消失。

该能力已通过隔离 AEDT 2026.1 + PyAEDT 1.3.0 实测：两 Via 原子成功、孔径 override/默认孔径、正负旋转、
锁定状态、外部同名对象导致 stale、磁盘工程 SHA-256 不变，以及真实创建后的故障注入全批回滚。目标
AEDT 2024 R2 仍需按第 12 节在测试工程副本复验。

## 8E. 示例：批量移动、旋转、改网或锁定已有 Via

推荐请求：

```text
使用 layout_live_via_update 更新当前 3D Layout 的两个既有 Via：
1. V1 改到 net=N2、位置 [5.0,6.0]mm、旋转 45deg，并保持锁定；
2. V2 改到位置 [-2.0,8.0]mm、旋转 -30deg，不改变当前锁定状态。
先冻结完整 stackup、net 名称目录和两个 Via 的全部 BaseElementTab 原生属性；审批后批量 apply。
只允许 Net、Location、Angle 和明确请求的 LockPosition 改变，其他原生属性必须保持一致。
任一项失败时恢复本批所有已触碰 Via 的完整原生快照。不要保存工程。
```

每项必须有精确 `name`，并至少提供一个可写字段：`net_name`、`location`、`rotation_degrees` 或
`lock_position`。一次最多 32 个 Via。`net_name` 必须已经存在且大小写完全一致；`location` 是当前
`model_units` 下的两个有限数值；旋转为有限角度，按模 360 语义回读。完全等于当前状态的 no-op 会在
preview 阶段拒绝，不会为了制造“成功”而把相同值再写一遍。

锁定 Via 需要移动或旋转时，Harness 会在事务内临时解锁，完成更新后恢复原锁定状态或设置用户明确请求的
最终状态。readback 直接读取 AEDT `BaseElementTab`；除 `Net`、`Location`、`Angle`、`LockPosition` 中
实际请求的字段外，Name、Type、Padstack、Start/Stop Layer、孔径以及其他原生字段必须与 preview 完全一致。
若本批移走了某个网络上的最后一个对象，AEDT 可以删除变空的旧源网络；Harness 只允许本批实际改网的旧源
网络消失，禁止新增网络或删除无关网络。

该能力已通过隔离 AEDT 2026.1 + PyAEDT 1.3.0 实测：双 Via 批量移动、正负旋转、改网、锁定/保持锁定、
空旧源网络清理、外部修改导致 stale、磁盘工程 SHA-256 不变，以及真实写入后的故障注入完整快照回滚。测试专属 AEDT 和
live session 均已清理。目标 AEDT 2024 R2 仍需在测试工程副本上复验。

## 8F. 示例：严格批量删除已有 Via

推荐请求：

```text
使用 layout_live_via_delete 删除当前 3D Layout 中精确名称为 V_BAD1、V_BAD2 的两个 Via。
先回读并冻结完整 stackup、所用 padstack、net 名称目录和两个目标的全部 BaseElementTab 原生属性；
仅接受能够由公开 PyAEDT API 完整重建、没有自定义 backdrill 的 Via。Windows 原生审批后逐个删除，
并用 AEDT FindObjects 验证两个名称都不存在。任一删除或最终验证失败时，按 preview 原生快照重建本批
已删除 Via，并要求完整属性逐字恢复。成功删除后不要重建，也不要保存工程。
```

请求字段为 `names`，包含 1～32 个精确、大小写匹配且批内不重复的既有 Via 名。preview 会拒绝普通对象、
缺失对象、名称冲突，以及带自定义 top/bottom backdrill 或非零 backdrill offset 的 Via，因为当前公开
`create_via` 无法保证恢复这些加工属性。普通 Via 可以包含锁定、正负旋转、孔径 override、默认孔径和无网络状态。

apply 逐个调用 AEDT 原生 Delete，并在每次调用后清理 PyAEDT `_vias` cache、通过 `FindObjects` 确认名称消失。
成功结果中的 `deleted_names` 必须与请求顺序完全一致，并返回非空 `absence_digest`。若某个旧源网络因最后一个
对象被删除而变空，允许 AEDT 清理该网络；禁止新增网络或删除本批无关网络。

这里的“自动 rollback”只适用于事务失败。用户批准且 `status=verified` 后，Via 会保持从 AEDT 内存工程中删除；
需要保留到磁盘仍必须另行申请保存。失败时 Harness 使用 preview 中的 padstack、层、net、位置、角度、锁定和
孔径状态重建，并比较完整 `BaseElementTab` 属性；外部对象抢占原名称时不会覆盖或删除外部对象，而会明确报告
rollback incomplete。

该能力已通过隔离 AEDT 2026.1 + PyAEDT 1.3.0 实测：锁定和孔径 override Via、负角度 Via、无网络 Via、
空源网络清理、外部 stale、磁盘工程 SHA-256 不变，以及锁定、独占网络和无网络三种 Via 在真实删除后的完整
重建回滚。测试专属 AEDT 和 live session 均已清理。目标 AEDT 2024 R2 仍需在测试工程副本上复验。

## 8G. 示例：严格批量平移已有 HFSS solid/sheet

推荐请求：

```text
使用 hfss_live_geometry_move 在当前 HFSS 设计中移动两个既有对象：
1. 精确名称 HarnessMoveBox，沿 Global 坐标移动 [1.25,-2.5,3.75]，单位使用当前 model_units；
2. 精确名称 HarnessMoveSheet，沿 Global 坐标移动 [-4,5,0.25]。
先冻结完整 geometry、全部 boundary、全部 mesh operation 和活动坐标系；仅在活动 WCS 为 Global 时继续。
Windows 原生审批后按顺序移动，回读 bounding box、每个 face center、对象/面 ID、材料和 Solve Inside。
Boundary 与 mesh assignment 必须完全不变；失败时用逆向量倒序恢复。不要保存工程。
```

`moves` 包含 1～32 个条目，每项只有精确 `name` 和三个有限数值组成的 `vector`。数值按当前 HFSS
`model_units` 解释，例如模型单位为 `mm` 时 `[1,0,0]` 表示沿 Global X 移动 `1mm`。当前严格 Harness
不接受带单位字符串、变量表达式、零向量、模糊名称、重复名称、line/unclassified object 或非 Global WCS；
这些场景需要独立的表达式求值或坐标变换契约，不能混在本审批中猜测。

preview 最多冻结 5000 个对象、500 个 mesh operation，以及所有 boundary 的原生属性 digest。apply 后要求
对象 ID、face ID、材料、Solve Inside、volume/area、boundary 和 mesh 不变；只有目标对象的 bounding box 与
face center 可以严格按各自向量平移。AEDT 会把例如 `5.999999999999999` 的面积规范化为 `6.0`，Harness
按 12 位有界数值快照比较物理状态，不会因无意义的浮点重排误报失败。

该能力已通过隔离 AEDT 2026.1 + PyAEDT 1.3.0 实测：solid/sheet 使用不同正负向量、对象/面 identity、
Perfect E、Length Mesh、外部移动后的 stale、磁盘工程 SHA-256 不变，以及真实双对象移动后的故障注入和
逆平移完整恢复。目标 AEDT 2024 R2 上线前仍需执行中文使用手册第 18.1 节的同名真实测试。

## 8H. 示例：绕 Global 原点严格批量旋转已有 HFSS solid/sheet

推荐请求：

```text
使用 hfss_live_geometry_rotate 在当前 HFSS 设计中旋转两个既有对象：
1. 精确名称 HarnessRotateBox，绕 Global Z 轴旋转 +90deg；
2. 精确名称 HarnessRotateSheet，绕 Global X 轴旋转 -30deg。
旋转中心固定为 Global 原点 [0,0,0]，仅在活动 WCS 为 Global 时继续。先冻结完整 geometry、全部
boundary、全部 mesh operation 和活动坐标系。Windows 原生审批后按顺序旋转，逐点回读 face center 和
vertex position，保持 object/face/vertex ID、材料、Solve Inside、volume/area、boundary 和 mesh。
失败时倒序应用负角度并要求完整快照恢复。不要保存工程。
```

`rotations` 包含 1～32 个条目，每项只有精确 `name`、`axis` 和 `angle_degrees`。`axis` 只允许 X/Y/Z；
角度只允许 `-360～360` 的有限数值，并拒绝 0 和 ±360 度语义空操作。当前严格 Harness 不支持任意旋转中心、
角度表达式、相对 WCS、line/unclassified object，或无法通过 face/vertex 观察变化的对称旋转。

preview 最多冻结 5000 个对象、500 个 mesh operation，以及所有 boundary 的原生属性 digest。apply 后按
右手系 Global 旋转矩阵验证每个 face center 和 vertex position；对象、面、顶点 identity、材料、Solve Inside、
volume/area、face planarity、非目标 geometry、boundary、mesh 和 WCS 必须保持。回读 bounding box 还必须
包含全部回读点。有限浮点规范到 12 位并使用有界逐点容差。

该能力已通过隔离 AEDT 2026.1 + PyAEDT 1.3.0 实测：solid 的 Z `+90deg`、sheet 的 X `-30deg`、
Perfect E、Length Mesh、外部旋转后的 stale、磁盘工程 SHA-256 不变，以及真实双对象旋转后注入 readback
故障并逆旋转完整恢复。目标 AEDT 2024 R2 上线前仍需执行中文使用手册第 18.1 节的同名真实测试。

## 9. 保存工程

完成修改后，如果确认要保存，应单独发送：

```text
先 preview 保存当前工程，显示工程名、设计名、目标文件路径和当前 dirty 状态。
等待新的 Windows 原生审批后再保存，保存后回读文件状态。不要复用刚才的修改审批。
```

测试或探索阶段推荐不保存，关闭工程时由工程师在 AEDT GUI 决定是否放弃内存修改。生产工程保存前应确认：

- readback 全部通过；
- 没有误创建的设计或对象；
- rollback 状态不是 pending/unknown；
- 工程路径和备份策略正确。

## 10. Harness 没有覆盖的任务

可以这样要求助手：

```text
先检查 capability catalog。若没有现成 Harness，使用 ansys-api-memory 查询当前安装版本的源码证据，然后用
属性查询、对象查找和 inventory 直接使用只读 Harness，不需要审批；未知 3D Layout 查询先用受控 read schema。只有修改或
不确定操作才通过 `preview_live_open_aedt_python` 提交精确的 PyAEDT/AEDT COM 代码，并填写一句简洁的 `change_summary`。
原生确认框只显示这句修改摘要、绑定工程/设计、backup 目录和代码 hash，不显示完整代码；批准后才能用对应 `preview_id`
调用 `apply_live_open_aedt_python`。
```

这是完全访问模式：Runtime 会先保存工程并复制 `.aedt`/`.aedb`，再在绑定 AEDT broker 中执行代码；它不是 sandbox，
也不承诺自动 rollback 或通用 readback。异常或核验不符时停止后续编辑，按返回的 backup 目录手动恢复工程。API Memory
仍只是用于写准确代码的证据；走通的过程仍可生成 Harness 或 Skill 候选，但不会自动改代码、注册 MCP 工具、提交或热加载。

## 11. 切换工程或设计

Desktop 会话绑定按钮点击时的端口、工程和设计。切换时：

1. 等待当前调用结束；
2. 退出旧 PowerShell 中的 Claude Code；
3. 回到 AEDT 激活新工程和新设计；
4. 如果切换了 AEDT 进程，重新执行 `live-sessions`；
5. 再次点击 `Automation -> Ansys Agent`；
6. 重新核对工程、设计、类型和版本。

旧会话返回 `target_forbidden`、`project_forbidden` 或 `design_forbidden` 是安全保护，不能通过反复 attach
绕过。

## 12. 上线前 smoke

### 12.1 只读 Workflow smoke

先打开一个 3D Layout 测试工程副本：

```powershell
$Run = "D:\ansys-agent-runs\acceptance-$(Get-Date -Format yyyyMMdd-HHmmss)"

.\.venv\Scripts\python.exe -m aedt_agent.interactive live-workflow-smoke `
  --port 50051 `
  --aedt-version 2024.2 `
  --expected-project Board `
  --expected-design Layout1 `
  --output-dir $Run `
  --confirm-read-only
```

该命令只读，不 edit、solve、save 或关闭 AEDT。保存生成的 JSON 和 SHA256 作为部署证据。

### 12.2 线宽 preview-only smoke

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-width-preview-smoke `
  --port 50051 `
  --aedt-version 2024.2 `
  --expected-project Board `
  --expected-design Layout1 `
  --target-width 4.3mil `
  --variable-name W_line `
  --variable-value 4.3mil `
  --output-dir $Run `
  --confirm-preview-only
```

检查结果中：

```text
apply_executed = false
project_dirty = false
```

preview 命中 0 个对象时不要改成 apply，应先检查设计类型、Path 类型、单位表达式、net 和 layer。

## 13. 更新依赖和项目

更新前退出所有 Ansys Agent PowerShell；AEDT 可以保持打开。

源码目录已经更新到批准 commit 后执行：

```powershell
Set-Location D:\ansys-agent
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\online\Update-AnsysAgentDependencies.ps1 `
  -InstallRoot D:\ansys-agent
```

脚本会更新项目 editable desktop 依赖、执行 `pip check` 和真实 import，并强制重建 API Memory。完成后：

1. 执行 `git rev-parse HEAD` 并记录 commit；
2. 重新执行 `capabilities-v2`；
3. 重新发现 AEDT 端口；
4. 从当前目录重新安装 Automation Tab 入口；
5. 依次完成只读 smoke、preview-only smoke、测试副本 apply/readback；
6. 最后才在生产工程使用新版本。

推荐把大版本升级安装到新目录，验收完成后再切换入口，而不是覆盖旧环境。回滚时从旧目录重新执行 Desktop
`install`。

## 14. 常见问题

### 14.1 出现 `Computer` 或 `Chrome` permission deny

旧 launcher 配置了 Claude Code 已不存在的工具名。更新项目，并从新目录重新执行 Desktop `install`。
当前 launcher 不应再产生这两个警告。

### 14.2 设计名出现 `0;`

立即停止，不要继续 attach、inventory 或写操作。关闭当前 PowerShell并更新项目。人工检查工程中是否已有旧
版本误创建的空设计；助手不得自动删除设计。

### 14.3 Path 列表为 0

按顺序检查：

1. 当前设计类型是否为 `HFSS 3D Layout Design`；
2. 工程和设计名是否与 GUI 一致；
3. 设计名是否带 `0;`；
4. 是否误用了 HFSS 3D inventory；
5. 不加 selector 时 Path 总数是否仍为 0；
6. 目标是否为 Path/line，而不是 polygon、via 或其他 primitive；
7. 线宽是数值、带空格的单位表达式、变量名还是等价毫米值。

不得通过创建同名设计解决 0 条结果。

### 14.4 成功连接后反复 attach

停止当前 PowerShell并更新 launcher。成功 attach 后必须复用同一个 `live_session_id`。只有 attach 真正失败时，
才检查端口、版本和 MCP 日志。

### 14.5 找不到 AEDT 端口

- 确认 AEDT 与命令属于同一 Windows/RDP 用户会话；
- 重新运行 `live-sessions`，不要假定端口永久为 `50051`；
- 多 AEDT 时按 PID 核对；
- 检查安全软件是否拦截 loopback；
- 2024 R2 早期 Service Pack 可在启动前显式设置 `$env:PYAEDT_USE_PRE_GRPC_ARGS = "True"`。

### 14.6 看不到审批框

审批框属于当前交互式 Windows 用户。确保 AEDT、PowerShell 和 Claude Code 位于同一个可见 RDP session。
Windows 服务、计划任务或纯 SSH 会话不能用于需要审批的写操作。

### 14.7 API Memory 不是 ready

```powershell
D:\ansys-agent\.venv\Scripts\python.exe `
  -m aedt_agent.knowledge.api_memory_cli prepare --force
```

已知 Harness 仍可使用。不要从另一台机器复制知识图，除非 Python 包版本、源码 digest 和安装路径全部一致。

### 14.8 `clr` 或 PyEDB 导入失败

确认项目 `.venv` 安装了当前锁定的 `pyedb[dotnet]` 和 `ansys-pythonnet`，然后重新运行依赖更新脚本与
import preflight。系统 Python 可以 import 不代表项目环境可以 import。

### 14.9 命令提示找不到模块或使用了错误 Python

不要直接运行裸命令 `python`、`pip` 或依赖全局 PATH。先确认工作目录和解释器：

```powershell
Set-Location D:\ansys-agent
Test-Path .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
.\.venv\Scripts\python.exe -m pip check
```

解释器必须位于当前批准安装目录的 `.venv`。如果按钮仍从旧目录启动，应在新目录重新执行 Desktop `install`，
而不是修改旧会话生成的 `launch-claude.ps1`。

### 14.10 Agent 声称成功，但没有 preview 或 readback

把该次任务视为未验收，不要保存工程。先只读检查工程、设计、对象数量和属性，再确认
`capabilities-v2` 中是否存在对应能力。对于写操作，缺少以下任一项都不能视为项目 Harness 的成功结果：

```text
preview + Windows 原生审批 + apply + typed readback
```

如果能力目录中不存在该操作，按第 10 节转入 API Memory 和受控 Exploration；不能让模型改用任意 Python、
PowerShell 或 COM 绕过审批链路。

## 15. 交付验收清单

### 安装

- [ ] 安装目录来自批准 commit 或 release；
- [ ] Python 为 CPython 3.12 x64；
- [ ] `pip check` 成功；
- [ ] PyAEDT、PyEDB、`clr`、FastMCP 可以从项目 `.venv` 导入；
- [ ] API Memory 状态为 ready；
- [ ] `capabilities-v2` 包含预期能力。

### AEDT 连接

- [ ] `live-sessions` 找到正确 PID 和实际端口；
- [ ] `live-info` 返回正确工程、设计、类型和版本；
- [ ] 设计名没有 `0;` 前缀；
- [ ] release 后 AEDT 和工程仍保持打开。

### Desktop 入口

- [ ] `Automation -> Ansys Agent` 可见；
- [ ] PowerShell 从当前批准目录启动；
- [ ] 初始阶段只 attach 一次；
- [ ] `session.json` 的端口、工程和设计与 GUI 一致；
- [ ] 没有 `Computer`/`Chrome` deny rule 警告；
- [ ] 切换工程或设计后，旧会话会拒绝继续操作。

### 写操作

- [ ] preview 不修改工程；
- [ ] 点击 No 后没有 apply；
- [ ] 点击 Yes 后 apply 只执行一次；
- [ ] typed readback 数量和目标一致；
- [ ] 故障注入或失败时 rollback 状态明确；
- [ ] 未明确要求时没有保存；
- [ ] 保存使用独立 preview 和独立审批。

## 16. 常用命令速查

```powershell
# 会话发现
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions

# 只读核对
.\.venv\Scripts\python.exe -m aedt_agent.interactive live-info --port 50051 --aedt-version 2024.2

# 安装 AEDT 入口
.\.venv\Scripts\python.exe -m aedt_agent.desktop install --port 50051 --version 2024.2

# 卸载 AEDT 入口
.\.venv\Scripts\python.exe -m aedt_agent.desktop uninstall --port 50051

# 查看能力
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2

# 构建和检查 API Memory
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli prepare --force
.\.venv\Scripts\python.exe -m aedt_agent.knowledge.api_memory_cli status

# 查看命令参数
.\.venv\Scripts\python.exe -m aedt_agent.interactive --help
.\.venv\Scripts\python.exe -m aedt_agent.desktop install --help
```

## 17. 相关文档

- [Ansys Assistant 中文使用手册](ansys-assistant-user-guide.zh.md)
- [Windows Server 离线部署](offline-windows-server-deployment.md)
- [AEDT Desktop Claude Code 入口](aedt-desktop-claude-entry.md)
- [通用交互式 Ansys 助手](interactive-ansys-assistant.md)
- [能力分层、API Memory 与 Harness 晋升](ansys-capability-evolution.md)
- [MCP 对比与 benchmark](ansys-mcp-comparison-2026-07-17.md)
