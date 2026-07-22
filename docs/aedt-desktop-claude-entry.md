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
7. Desktop Runtime 使用会话级 automatic policy，不启动 Windows approval Host。

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
  命令行工具白名单、Runtime target binding、preview 或自动备份。
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
- Claude Code 以 `bypassPermissions` 启动，并显式启用 `allow-dangerously-skip-permissions`：Claude 不会为任何已注册 MCP 调用再弹一层确认框。Desktop Runtime 同时使用会话级 automatic policy，不启动 Windows 审批 Host，也不显示原生确认框。
- 会话专用 `claude-settings.json` 固定写入 `autoCompactEnabled: true`，并将 auto-compact window 固定为 120,000 tokens、阈值设为 60%；长对话可自动 compact，也保留内建 `/compact`。
- live edit、solve、cancel、export、save 仍遵循 preview/apply；preview 产生绑定 action/resource/digest 的五分钟一次性 automatic token，apply 必须使用同一 preview 返回的 token。
- 属性查询、对象查找、inventory 等只读操作直接调用注册的 read tool，不弹审批框；未知的 3D Layout 查询先使用 `get_controlled_live_layout_read_schema` / `execute_controlled_live_layout_read`，该程序不能执行 Python、COM 或方法调用。
- 对没有 typed Harness 的 AEDT/PyAEDT **修改或不确定操作**，Desktop Runtime 全局提供 `preview_live_open_aedt_python` / `apply_live_open_aedt_python`。它不按对象类型、属性或 COM 方法再设 allowlist：preview 必须传入简洁 `change_summary`，然后将返回的 automatic token 原样传给 apply。Runtime 先保存工程并复制 `.aedt`/`.aedb`，再在绑定 AEDT broker 中执行该**完全访问** Python。
- 这项开放能力不是 sandbox，也不承诺自动 rollback 或通用 readback；代码拥有当前 AEDT Desktop 用户的同等权限。失败或结果异常时必须停止后续编辑，在 AEDT GUI 核对，并按返回的 backup 目录手动恢复工程。
- automatic token 绑定 action/resource/digest、五分钟过期且 verify 后立即失效；它不是人工批准，也不能跨 preview、会话、工程或设计复用。
- release 只释放 PyAEDT wrapper，AEDT 与工程保持打开。

每次点击都会生成独立审计目录，包含：

```text
mcp.json
context.md
claude-settings.json
launch-claude.sh
session.json
```

这些文件不包含 API key、approval secret 或 automatic token。Desktop 自动模式不启动 loopback approval Host。

## 未知能力

Claude 必须按固定顺序处理 Harness 尚未覆盖的任务：

```text
确认 typed Harness capability miss
  -> API Memory search + inspect（用于准确写代码）
  -> 修改/不确定操作：`preview_live_open_aedt_python` + 简洁 `change_summary`
  -> 取得 preview 自动 token
  -> Runtime 保存并备份工程
  -> `apply_live_open_aedt_python`
  -> AEDT GUI / 针对性代码核验
```

API Memory 用于获得与当前版本一致的源码证据；它不是执行权限。开放代码仍必须经预览、绑定目标
复核和自动工程备份，且完成并不等于已验证业务结果。

成功 trace 可调用 `promote_ansys_capability`，或使用 `ansys-capability-promoter` Skill 生成
`.aedt-agent/capability-candidates` 下的禁用候选。该步骤不会应用 patch、注册 tool、commit 或热加载。

## 自动执行体验

Claude 可以直接完成发现、连接、inventory 和状态读取。写操作流程为：

```text
preview tool
  -> 返回 action/resource/digest 绑定的一次性 automatic token
  -> Claude 立即调用 apply
  -> apply 再次核对目标和 preview 状态，随后备份、执行和回读
```

automatic token 不是通用执行权限：它只允许匹配的单次 apply，不能跨 preview 或会话复用。
