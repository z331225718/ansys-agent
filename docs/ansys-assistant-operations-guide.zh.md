# Ansys Assistant 部署与操作说明

本文是面向实际交付的操作手册，适用于以下环境：

- Windows Server x64；
- 已安装 Ansys Electronics Desktop 2024 R2；
- Claude Code 和对应模型已经可用；
- 服务器可以通过 pip 访问 PyPI 或组织内部 Python 镜像；
- 需要从 AEDT 中打开助手，复用当前正在运行的 AEDT 会话，并操作当前 HFSS/HFSS 3D Layout 工程。

本文不介绍 Claude Code 和模型的安装。完整能力、全部 Workflow、离线 wheelhouse 和维护者验收细节见
[Ansys Assistant 中文使用手册](ansys-assistant-user-guide.zh.md)。

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
  -> Windows 原生审批
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
6. 修改审批和保存审批相互独立，批准修改不等于批准保存。
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

项目使用 CPython 3.11 x64：

```powershell
py -3.11 -c "import struct,sys; print(sys.executable); print(sys.version); print(struct.calcsize('P')*8)"
```

最后一行必须为 `64`。如果服务器没有 `py` launcher，后续命令可以把 `py -3.11` 替换成组织批准的
Python 3.11 绝对路径，例如 `C:\Python311\python.exe`。

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

py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --editable ".[desktop]"
.\.venv\Scripts\python.exe -m pip check
```

`.[desktop]` 会安装项目锁定的 PyAEDT、PyEDB DotNet 后端、FastMCP 和 codebase-memory-mcp。AEDT
2024 R2 需要 `pyedb[dotnet]`；不要只安装基础 `pyedb`。

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
- 没有尚未结束的旧 Ansys Agent PowerShell；
- 当前是可见的交互式 RDP 桌面，不是 Windows 服务或纯 SSH 会话；
- 当前工程已有人工备份或使用的是副本；
- 本次是否允许保存已经事先明确，未明确时视为不保存。

### 6.2 点击入口

在 AEDT 中点击 `Automation -> Ansys Agent`。入口会打开可见 PowerShell，并启动隔离的 Claude Code
会话。正常首次行为是：

1. 连接按钮来源的 AEDT 端口一次；
2. 读取活动工程和设计；
3. 报告端口、工程、设计、设计类型和 AEDT 版本；
4. 等待用户任务。

核对报告内容与 AEDT GUI。任一项不一致，关闭本次 PowerShell，不要让 Agent 通过反复 attach 自行修复。

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
  launch-claude.ps1
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

## 8A. 示例：创建 3D Layout 材料并分配 stackup 层

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
先检查 capability catalog。若没有现成 Harness，不要猜 PyAEDT/PyEDB API，也不要生成或执行
任意 Python、PowerShell 或 COM 脚本。使用 ansys-api-memory 查询当前安装版本的源码证据，
然后判断受控 Exploration 是否支持。若仍不支持，报告缺少的 operation、schema、readback 和
rollback 能力，不要修改工程。
```

API Memory 找到源码并不表示可以直接写工程。受控 Exploration 只能接受声明式 operation plan，仍需服务端
校验证据、preview、原生审批、apply 和回读。走通的探索可以生成禁用状态的 Harness 候选，但不会自动改代码、
注册 MCP 工具、提交或热加载。

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

## 15. 交付验收清单

### 安装

- [ ] 安装目录来自批准 commit 或 release；
- [ ] Python 为 CPython 3.11 x64；
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
