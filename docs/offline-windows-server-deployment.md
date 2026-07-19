# Windows Server 离线部署

本文面向不能访问公网、已安装 AEDT 2024 R2 的 Windows Server。推荐拓扑是：

完成安装后的 AEDT 连接、Automation Tab、日常对话、严格 Workflow、审批和排障说明见
[`ansys-assistant-operations-guide.zh.md`](ansys-assistant-operations-guide.zh.md)。完整能力和维护说明见
[`ansys-assistant-user-guide.zh.md`](ansys-assistant-user-guide.zh.md)。

```text
同一 RDP 用户会话
  -> AEDT 2024 R2（本机 gRPC loopback）
  -> D:\ansys-agent\.venv（固定依赖）
```

## 发布包边界

发布包包含：

- 当前项目的运行时源码、Harness 配置、Ansys Skills 和静态 benchmark fixtures；
- `uv.lock` 导出的 desktop 依赖；
- CPython 3.11 Windows x64 wheelhouse；
- 构建、验签、安装、环境检查和 AEDT 只读 smoke 脚本；
- `bundle.json`、文件级 `SHA256SUMS` 和 ZIP SHA256。

`SHA256SUMS`、bootstrap requirements、manifest 与 ZIP sidecar 均以无 BOM UTF-8 写入；
Windows PowerShell 5.1 和 PowerShell 7 会按 UTF-8 显式读取，因此中文文档路径不会被替换或误解码。

发布包不包含：

- AEDT、许可证或工程文件；
- `*.local.*`、运行结果、知识图缓存、Git 元数据或虚拟环境。

现有 Desktop launcher 要求运行根目录同时存在 `pyproject.toml`、`src/aedt_agent` 和
`.venv/Scripts/python.exe`。因此离线安装采用 **runtime 源码 + editable install**，不是
wheel-only 安装。项目目录安装后不能移动或改名。PyAEDT/PyEDB 源码不需要另行复制；
API Memory 直接索引 `.venv/Lib/site-packages` 中已安装的包。

## 1. 在联网机制作发布包

使用 Windows x64 联网机，并安装与目标一致的 CPython 3.11 x64、`uv` 和 Git。不要用
Python 3.12/3.14 为 Python 3.11 目标下载 wheel；环境 marker 和 ABI 可能不同。

```powershell
Set-Location C:\src\ansys-agent

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\offline\New-AnsysAgentOfflineBundle.ps1 `
  -OutputDirectory C:\offline-output `
  -PythonExe C:\Python311\python.exe
```

脚本执行以下检查：

1. `uv lock --check`，不允许构建时改写 lock；
2. `uv export --frozen --extra desktop --no-emit-project`；
3. `pip download --require-hashes --only-binary=:all:`，拒绝 sdist；
4. 只复制运行 allowlist，并排除 `*.local.*`、缓存、虚拟环境和结果目录；
5. 扫描明显 token/私钥模式；
6. 生成文件级 SHA256、ZIP 和 ZIP SHA256。

输出类似：

```text
C:\offline-output\ansys-agent-offline-0.1.0-win-amd64-py311.zip
C:\offline-output\ansys-agent-offline-0.1.0-win-amd64-py311.zip.sha256
```

`bundle.json` 会记录 Git revision 和 `source_dirty`。正式发布建议从已评审的干净 commit 构建；
脚本不会悄悄丢弃工作区改动。

将 ZIP 和旁路 SHA256 文件通过批准的介质传到服务器。传输后先核对外层 ZIP：

```powershell
Get-FileHash X:\transfer\ansys-agent-offline-0.1.0-win-amd64-py311.zip -Algorithm SHA256
Get-Content  X:\transfer\ansys-agent-offline-0.1.0-win-amd64-py311.zip.sha256 -Encoding UTF8
```

## 2. 准备服务器 Python

项目要求外部 CPython 3.11 x64。不要使用 AEDT 2024 R2 内嵌 Python，也不要假定“服务器已经
安装 PyAEDT”就意味着项目虚拟环境能够导入它。安装器会在项目自己的 `.venv` 中安装发布包锁定的：

```text
pyaedt==1.3.0
pyedb[dotnet]==0.80.2
fastmcp==3.4.4
codebase-memory-mcp==0.9.0
```

若服务器没有 CPython 3.11，需提前传入组织批准的 Python Windows x64 离线安装器。安装后验证：

```powershell
py -3.11 -c "import struct,sys; print(sys.executable); print(sys.version); print(struct.calcsize('P')*8)"
```

最后一行必须是 `64`。

### 服务器可访问 PyPI 时

如果远端服务器的项目源码已经更新，并且项目 `.venv` 可以访问组织允许的 PyPI 镜像，可直接运行：

```powershell
Set-Location D:\ansys-agent
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\online\Update-AnsysAgentDependencies.ps1 `
  -InstallRoot D:\ansys-agent
```

脚本按照项目当前锁定版本执行 editable desktop 更新、`pip check`、真实 import 检查，并强制重建
API Memory。当前锁定 PyAEDT `1.3.0`、PyEDB `0.80.2`。AEDT 2024 R2 必须安装
`pyedb[dotnet]`，不能只安装基础 `pyedb`，因为该版本的 DotNet 后端已经变成可选依赖。

运行前退出已经打开的 Ansys Agent 助手 PowerShell；AEDT 本身可以保持打开。更新后重新从
Automation Tab 启动助手，使新 broker/worker 加载新包。私有镜像、代理和凭据应通过 pip 配置或
环境变量提供，不要把凭据写进脚本或项目文件。

## 3. 验签并安装

解压后先运行纯验签。该步骤不创建目录、不安装包：

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

正式安装要求 `D:\ansys-agent` 不存在或为空；脚本拒绝覆盖既有目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$Bundle\scripts\Install-AnsysAgentOffline.ps1" `
  -BundleRoot $Bundle `
  -InstallRoot D:\ansys-agent `
  -PythonExe C:\Python311\python.exe
```

安装开始时会在空目录写入一次性 rollback marker。后续步骤失败时，脚本只有在绝对路径、marker、
installation id 和非 reparse-point 根目录全部吻合时才清理本次创建的文件：脚本新建的根目录会被
删除，预先存在的空根目录会恢复为空。若文件锁导致自动回滚也失败，marker 会保留并报告其路径；
此时先保留现场诊断，再由管理员移走目录并向一个新的空目录重试，不要覆盖修补半成品环境。

安装全过程使用 `--no-index`；不会访问 PyPI。默认会在远端本机构建 API Memory。构图不需要
互联网，但可能需要几分钟。构图失败不会破坏已知 Harness，只会关闭未知能力 fallback；修正后可重跑：

```powershell
D:\ansys-agent\.venv\Scripts\python.exe `
  -m aedt_agent.knowledge.api_memory_cli prepare --force
```

环境验收：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  D:\ansys-agent\scripts\offline\Test-AnsysAgentOffline.ps1 `
  -InstallRoot D:\ansys-agent `
  -BundleRoot $Bundle `
  -AedtVersion 2024.2
```

验收不只读取 wheel metadata，还会在目标 `.venv` 中实际 import `ansys.aedt.core`、`pyedb`、
`clr`、`fastmcp` 和 `codebase_memory_mcp`。其中 `clr` 用于确认 AEDT 2024 R2 所需的 DotNet
runtime 已安装。该 import preflight 不连接 AEDT、不构图、不联网；任一模块
导入失败都会阻止验收，并在结果中记录逐模块状态。为兼容 Windows PowerShell 5.1 的 native
argument quoting，探针以无 BOM UTF-8 临时 `.py` 文件执行，不把多行源码传给 `python -c`；脚本
在 `finally` 中验证临时目录、文件名前缀和扩展名后再删除它。

## 4. AEDT 2024 R2 只读 smoke

`-StartAedt` 用于非图形自动化验收。它通过项目的 `live-launch --non-graphical` 调用
PyAEDT 1.3.0 对齐的 gRPC 启动参数，成功连接后只释放 PyAEDT wrapper，不关闭其启动的 AEDT：

```powershell
Set-Location D:\ansys-agent

.\scripts\offline\Invoke-Aedt2024R2Smoke.ps1 `
  -InstallRoot D:\ansys-agent `
  -AedtVersion 2024.2 `
  -Port 0 `
  -StartAedt
```

`-Port 0` 让启动器选择当前 Windows 可用端口，可规避系统 excluded port range；成功后脚本自动采用
`live-launch` 返回的实际端口。需要可复现的固定端口时可显式传入非零值。不带 `-StartAedt` 时禁止
`-Port 0`，必须传入发现到的现有会话端口。

脚本从 `ANSYSEM_ROOT242`（或 `-AedtRoot`）定位 AEDT，优先使用根下的 `ansysedt.exe`，
并兼容少数安装布局中的 `Win64\ansysedt.exe`。旧版 2024 R2 Service Pack 如果尚不支持
transport-mode 参数，可在执行前显式设置：

```powershell
$env:PYAEDT_USE_PRE_GRPC_ARGS = "True"
```

不要用 `-StartAedt` 启动需要人工操作的 GUI 会话。现代 Windows secure gRPC/WNUA 下，图形模式
不保证按命令行请求端口建立可探测的 TCP listener。GUI 场景应正常打开 AEDT 和工程，再查询实际会话：

```powershell
D:\ansys-agent\.venv\Scripts\python.exe -m aedt_agent.interactive live-sessions
```

从结果选择实际 `grpc_port`/`ports`，然后将该端口传给下面不带 `-StartAedt` 的 smoke 和 Desktop
入口安装流程；不要自行拼接 `ansysedt.exe -grpcsrv <port>`。

打开一个副本工程后，再要求活动工程并安装 Automation Tab 入口：

```powershell
.\scripts\offline\Invoke-Aedt2024R2Smoke.ps1 `
  -InstallRoot D:\ansys-agent `
  -AedtVersion 2024.2 `
  -Port 50061 `
  -RequireActiveProject `
  -InstallDesktopEntry
```

smoke 只执行 discovery、attach、project info 和 release；不会 edit、solve、save、关闭 AEDT 或关闭工程。
`-InstallDesktopEntry` 仅写当前用户 PersonalLib 的 Automation Tab 扩展。

也可以手工安装：

```powershell
D:\ansys-agent\.venv\Scripts\python.exe -m aedt_agent.desktop install `
  --port 50061 `
  --version 2024.2
```

然后在 AEDT 中点击 `Automation -> Ansys Agent`。首次应先发只读请求，确认端口、版本、工程和设计；
写操作继续遵循 preview、Windows 原生审批、apply、readback/rollback，保存工程需要独立审批。

## 运行约束

- AEDT、安装扩展和 API Memory 应使用同一 Windows 用户；PersonalLib 与
  `%LOCALAPPDATA%\AnsysAgent\knowledge` 都是用户级状态。
- 必须使用交互式 RDP 桌面。Windows 服务或纯 SSH 会话看不到原生审批框。
- gRPC 端口仅监听/访问本机；不要把 AEDT 原始 gRPC 端口暴露到业务网络。
- 对 AEDT 2024 R2 的离线 PyEDB 操作使用 `auto`/`.NET` 路径，不强制新版本才有的 EDB gRPC 后端。
- 不要复制其他机器的知识图，除非 Python 包版本、源码 digest 和路径完全一致；远端重建更稳妥。
- 升级不覆盖旧安装。请安装到新目录，完成只读与副本工程验收后再切换入口。
