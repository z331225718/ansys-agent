# AEDT Desktop Claude Code 入口

AEDT 2024 R2 离线 Windows Server 的完整打包、验签、项目安装和只读验收步骤见
[`offline-windows-server-deployment.md`](offline-windows-server-deployment.md)。目标服务器上的 Claude Code
和对应模型应已由现有环境提供，本项目不负责安装、替换或修改它们。
安装完成后的工程师日常使用和对话示例见
[`ansys-assistant-operations-guide.zh.md`](ansys-assistant-operations-guide.zh.md)；完整能力参考见
[`ansys-assistant-user-guide.zh.md`](ansys-assistant-user-guide.zh.md)。

## 目标

在 AEDT 2023 R2 及以上版本的 Automation Tab 中安装 `Ansys Agent` 按钮。点击后：

1. 使用 AEDT Extension 环境提供的 `PYAEDT_DESKTOP_PORT` 和版本。
2. 读取当前活动 project/design，并立即释放探测 wrapper，不关闭 AEDT 或工程。
3. 在 `.aedt-agent/desktop/sessions/<id>` 生成本次会话专用的 MCP 配置、最小 Claude settings 和 system context。
4. 打开一个可见 Git Bash，工作目录为本项目，然后启动交互式 Claude Code。
5. Claude Code 加载受控 Runtime MCP；知识图 ready 时再加载只读 API Memory MCP。
6. Runtime 被硬限制到按钮来源的 port、project 和 design。
7. 同时启动只监听 loopback 的 approval Host；Claude 退出时自动关闭该 Host。

实现采用 PyAEDT 官方 Custom Extension/Automation Tab 接口，不修改 AEDT 安装目录。

## 依赖

不需要把 PyAEDT 或 PyEDB 源码复制进仓库。项目通过 optional dependencies 声明兼容版本：

```bash
./.venv/Scripts/python.exe -m pip install -e '.[desktop]'
```

当前本机验收版本为 PyAEDT `1.3.0`、PyEDB `0.80.2`、AEDT `2026.1`。发布时应同时保留
lock file 或内部 wheelhouse，避免生产环境自动漂移到未验收版本。

首次使用可预建当前虚拟环境源码图；不需要把 PyAEDT/PyEDB 源码复制进项目：

```bash
./.venv/Scripts/python.exe -m aedt_agent.knowledge.api_memory_cli prepare
```

索引和 manifest 位于 `%LOCALAPPDATA%\AnsysAgent\knowledge`。Desktop 点击入口先执行 `status`；
只有知识图不是 ready 时才继续 `prepare -> status`。准备失败时仍保留现有 Harness，只关闭未知能力 fallback。

## 安装

先正常打开 AEDT GUI 和目标工程。不要为 GUI 入口手工拼接 `ansysedt.exe -grpcsrv <port>`；现代
Windows secure gRPC/WNUA 可能使用不同的实际 listener。先用 `ansys-assistant live-sessions` 发现
该会话的实际端口，再运行：

```bash
./.venv/Scripts/python.exe -m aedt_agent.desktop install
```

如果同时运行多个 AEDT，必须显式指定：

```bash
./.venv/Scripts/python.exe -m aedt_agent.desktop install --port 50061 --version 2026.1
```

安装器只向 PersonalLib 的 Project Automation Tab 添加一个按钮，调用
`RefreshToolkitUI` 后通常不需要重启 AEDT。卸载：

```bash
./.venv/Scripts/python.exe -m aedt_agent.desktop uninstall --port 50061
```

## 会话边界

- 需要安装 Git for Windows；launcher 使用其 `bin/bash.exe`，可用环境变量
  `AEDT_AGENT_GIT_BASH` 指向非默认安装路径。不会误用 Windows/WSL 自带的 `bash.exe`。
- MCP 配置使用项目 `.venv` 的绝对 Python 路径，不依赖 Git Bash 当前 PATH。
- Claude Code 以 `--settings <session-file> --setting-sources= --strict-mcp-config` 启动。不会加载用户或
  项目 settings，MCP 也不能混入其他 server；不再使用 `--bare` 或 `--disable-slash-commands`，因此
  Claude Code 的内建 `/compact` 可以压缩长对话。启用内建 slash commands 不会放宽固定 MCP 配置、
  命令行工具白名单、Runtime target binding 或 native approval。
- 仅从用户 Claude settings 白名单继承 Anthropic endpoint/model/auth 和 `API_TIMEOUT_MS` 到子进程环境，
  显式进程环境优先；密钥不会写入会话文件或 metadata。
- Claude 内建工具只保留 `AskUserQuestion`；Bash、文件读写、Notebook、浏览器、子 Agent、Skill 和进程工具
  在命令行工具面被禁用。两台 MCP 均使用逐工具 allowlist，不使用会自动放行未来工具的通配符。
- Desktop-bound Runtime 不注册 artifact session、AEDT 会话发现/启动以及无 preview 的直接写工具；只保留
  绑定工程的 attach/release、inventory、preview/apply/approval、受控 Exploration、trace 和 promotion。
- MCP server 进程硬限制到来源 gRPC port；PID 或其他 port 会返回 `target_forbidden`。
- 所有带 `project_name` 的调用硬限制到来源 project；其他工程返回 `project_forbidden`。
- 所有带 `design_name` 的调用硬限制到来源 design；活动 design 变化会返回 `design_forbidden`。
- `ansys-api-memory` 只提供 search/inspect/trace/source/example 查询，不暴露索引、删除或 ADR 写工具。
- Claude 的 permission mode 为 `manual`，不会启用 `dangerously-skip-permissions`。
- live edit、solve、cancel、export、save 仍遵循 preview/apply 和外部 Host approval。
- approval Host 没有 HTTP/MCP approve 接口；批准只能来自 Windows 原生确认框。
- approved token 绑定 action/resource/digest、五分钟过期且 verify 后立即失效。
- 同一 Desktop 会话同时只允许一个 pending/approved 原生审批，避免并发 preview 造成弹窗堆积。
- release 只释放 PyAEDT wrapper，AEDT 与工程保持打开。

每次点击都会生成独立审计目录，包含：

```text
mcp.json
context.md
claude-settings.json
launch-claude.sh
session.json
```

这些文件不包含 API key 或 approval secret。approval session key 只通过本次 Git Bash 的进程环境传递。
脚本会用 `trap` 关闭 approval Host；Host 还会监测 Bash 父进程，避免终端被强制关闭后遗留 loopback 服务。

## 未知能力

Claude 必须按固定顺序处理 Harness 尚未覆盖的任务：

```text
确认 capability miss
  -> API Memory search + inspect
  -> 复制 operation_evidence
  -> get_ansys_operation_plan_schema
  -> propose -> validate -> preview
  -> 写操作等待原生审批
  -> apply + readback/rollback
  -> capture_capability_trace
```

Runtime 会重新查询本地 API Memory，逐字段核验 `query_id`、symbol、版本、project、源码路径和
snippet digest。仅在模型中伪造 evidence 不能通过。Runtime 从不接受原始 Python、shell、COM、
`eval/exec` 或生成脚本。

成功 trace 可调用 `promote_ansys_capability`，或使用 `ansys-capability-promoter` Skill 生成
`.aedt-agent/capability-candidates` 下的禁用候选。该步骤不会应用 patch、注册 tool、commit 或热加载。

## 审批体验

Claude 可以直接完成发现、连接、inventory 和状态读取。写操作流程为：

```text
preview tool
  -> approval Host 注册 action/resource/digest
  -> Windows 原生确认框展示 preview
  -> 用户点击 Yes 或 No
  -> Claude 调用 wait_for_live_approval
  -> Yes: 返回一次性 token，再调用 apply
  -> No/timeout: 停止，不允许隐式重试
```

Claude Code 自己的工具确认不能替代这次 Host approval；approval Host 也不能调用 AEDT 或 apply tool。
