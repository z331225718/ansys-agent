# Ansys Assistant 中文使用手册

本文面向在 Windows 工作站或 Windows Server 上使用 AEDT、HFSS、HFSS 3D Layout 的工程师。
目标是让使用者从安装、连接现有 AEDT 会话开始，通过对话完成查询、预览、审批、修改和验证，
同时清楚知道助手会做什么、不会做什么，以及出现异常时如何恢复。

本文假定目标机器上的 Claude Code 和对应模型已经可用，不包含它们的安装或模型配置过程。

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

当前预发布版本：

- 发布页：<https://github.com/z331225718/ansys-agent/releases/tag/v0.1.0-ansys-assistant-preview.1>
- ZIP：`ansys-agent-offline-0.1.0-win-amd64-py311.zip`
- SHA256：`6b0a9dee7900346600e34ebda1e890778b5624188f8c0de5e16894cc2b22dfdd`

联网中转机可以使用：

```powershell
gh release download v0.1.0-ansys-assistant-preview.1 `
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

## 11. 示例：把 4.3mil 线宽参数化为 W_line

### 11.1 用户请求

```text
找出当前 3D Layout 设计中 LineWidth=4.3mil 的所有 line，
把它们参数化为设计变量 W_line，W_line 初值为 4.3mil。
先预览，审批后应用，回读验证，不要保存工程。
```

### 11.2 预期执行过程

```text
attach_live_aedt_session（一次）
  -> get_live_aedt_project_info
  -> 确认 design_type = HFSS 3D Layout Design
  -> list_live_layout_paths(selector.target_width="4.3mil")
  -> 展示匹配对象
  -> preview_live_parameterize_path_width(
       selector.target_width="4.3mil",
       variable_name="W_line",
       variable_value="4.3mil")
  -> Windows 原生审批框
  -> wait_for_live_approval
  -> apply_live_parameterize_path_width
  -> 验证 target_count、verified_count 和回读表达式
  -> release_live_aedt_session
```

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

受支持的写操作都必须经过 preview 和原生审批。例如创建 setup：

```text
检查当前 HFSS 设计现有 setup。
如果不存在 Setup_10G，预览创建 Driven setup，频率 10GHz、MaximumPasses=10。
审批后应用并回读，但不要保存工程。
```

生产求解推荐流程：

```text
preview_live_hfss_analysis_start
  -> 原生审批
  -> apply_live_hfss_analysis_start
  -> get_live_hfss_analysis_status
  -> 按需 preview/apply cancel
  -> preview/apply results export
```

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

## 16. 未知能力与 API Memory

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

## 17. 常用诊断命令

查看能力：

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities-v2
```

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

## 18. 故障排查

### 18.1 出现 `Computer` 或 `Chrome` permission deny 警告

这是旧 launcher 向 Claude Code 传入了已经不存在的 deny tool 名称。更新到当前发布版本，并从新安装
目录重新安装 Automation Tab 入口。当前版本不会再传这两个名称。

### 18.2 设计名出现 `0;`

立即停止当前 PowerShell，不要继续调用 layout/HFSS wrapper。更新并重新安装入口。检查工程中是否已经
出现空白的 `0;...` 设计：

- 如果工程没有保存且没有其他修改，关闭不保存并重新打开；
- 如果有其他修改，先手工确认错误设计为空，再从 AEDT UI 删除；
- 不要让助手自动删除设计。

### 18.3 走线列表返回 0

依次检查：

1. 当前设计类型是否为 `HFSS 3D Layout Design`；
2. 工程和设计名是否与 AEDT UI 一致且没有 `0;`；
3. Agent 是否误用了 HFSS 3D inventory；
4. 不带 selector 先列出 line 总数；
5. 检查宽度表达式是 `4.3mil`、`4.3 mil`、变量名还是等价毫米值；
6. 检查对象是否真的是 Path/line，而不是 polygon、via 或其他 primitive。

不要在返回 0 后通过创建同名设计来“重试”。当前 Runtime 会拒绝任何不存在的设计，防止 PyAEDT
隐式创建设计。

### 18.4 成功连接后反复 attach

停止会话并更新 launcher。当前 system context 明确要求成功 attach 后复用同一个 `live_session_id`。
如果 attach 本身失败，再检查端口、AEDT 版本和 MCP 日志，不要无条件循环。

### 18.5 `target_forbidden`、`project_forbidden` 或 `design_forbidden`

Desktop 会话被绑定到按钮来源：

- `target_forbidden`：调用了其他 PID 或端口；
- `project_forbidden`：活动工程已经切换；
- `design_forbidden`：活动设计已经切换或名称不一致。

回到 AEDT，激活正确工程和设计，然后关闭旧 PowerShell，从 Automation Tab 重新启动新会话。

### 18.6 找不到端口或连接超时

- 确认 AEDT 正在同一个 Windows/RDP 用户会话中运行；
- 使用 `live-sessions` 读取实际端口；
- 不要假定固定端口一直有效；
- 多 AEDT 并行时按 PID 核对；
- 检查本机安全软件是否阻止 loopback；
- AEDT 2024 R2 早期 SP 可尝试 `PYAEDT_USE_PRE_GRPC_ARGS=True`。

### 18.7 原生审批框不可见

审批框属于当前交互式 Windows 用户。Windows 服务、计划任务、纯 SSH 会话或另一个 RDP session
可能看不到它。确保 AEDT、Ansys Agent PowerShell 和用户桌面属于同一个会话。

### 18.8 审批过期

审批 token 默认五分钟过期，只能使用一次，并绑定 action、resource、preview 和 snapshot digest。
过期后不要复用 token；让用户明确要求重新预览。

### 18.9 `clr` 或 PyEDB 导入失败

确认安装的是：

```text
pyedb[dotnet]==0.80.2
ansys-pythonnet==3.1.0rc8
```

然后运行环境验收脚本。不要把系统 Python 中能 import PyEDB 当作项目 `.venv` 已正确安装的证据。

### 18.10 API Memory 不可用

已知 Harness 仍可工作，只是未知能力 fallback 关闭。运行：

```powershell
D:\ansys-agent\.venv\Scripts\python.exe `
  -m aedt_agent.knowledge.api_memory_cli prepare --force
```

不要从另一台机器直接复制知识图，除非包版本、源码 digest 和路径全部一致。

## 19. 升级与回滚

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

## 20. 上线验收清单

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

## 21. 安全边界摘要

- 不自动选择其他 AEDT 进程；
- 不自动创建不存在的 project/design；
- 不关闭 AEDT 或工程；
- 不把源码证据当作执行权限；
- 不开放任意 shell、Python 或 COM；
- 不允许模型自行签发审批 token；
- 不把修改审批扩大为保存审批；
- 不自动把探索结果写进 Harness；
- 不在未验证 readback 时报告成功。

## 22. 相关文档

- [Windows Server 离线部署](offline-windows-server-deployment.md)
- [AEDT Desktop Claude Code 入口](aedt-desktop-claude-entry.md)
- [通用交互式 Ansys 助手](interactive-ansys-assistant.md)
- [Ansys 助手能力分层与自进化架构](ansys-capability-evolution.md)
- [MCP 对比与 benchmark](ansys-mcp-comparison-2026-07-17.md)
