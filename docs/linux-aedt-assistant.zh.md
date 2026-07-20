# Linux AEDT Assistant 使用说明

本文说明 `ansys-agent` 的 Linux x86_64 本地 AEDT 路径。它适合在安装了商业版 AEDT、Python 3.12 和 Claude Code 的 Linux 工程机或计算节点上，通过本机 gRPC 端口驱动 HFSS 与 HFSS 3D Layout。

Windows 的 `Automation -> Ansys Agent` 按钮、PowerShell 启动器和 Windows 原生确认框仍是 Windows-only 功能，不受本路径影响。

## 支持边界

- Linux x86_64，CPython 3.12。
- AEDT 与 harness 在同一台 Linux 主机上运行，只连接 `127.0.0.1` 的显式端口。
- 已打开的 AEDT 可复用；也可用 `ansys-assistant live-launch --non-graphical` 启动本机 AEDT。
- 支持 Runtime Harness、API Memory、preview/apply/readback 和独立审批。
- 首次真实验收组合为 AEDT 2026 R1+、PyAEDT 1.3.0、PyEDB 0.80.2。较早 AEDT 可用于只读验收，但写入能力必须逐项完成真实验收后才可投入生产。
- 不支持跨主机 AEDT gRPC、把监听端口暴露到公网，或从 MCP 执行任意 shell/Python 脚本。

AEDT 官方文档说明商业版 Electronics Desktop 可在 Linux 启动；不同版本、设计类型与许可功能仍应按 Ansys Platform Support 逐项确认。

## 在线安装

```bash
git clone https://github.com/z331225718/ansys-agent.git
cd ansys-agent
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --editable '.[linux]'
.venv/bin/python -m pip check
.venv/bin/python -c 'import ansys.aedt.core, pyedb; print("OK")'
```

设置 AEDT 根目录。以 2026 R1 为例：

```bash
export ANSYSEM_ROOT261=/ansys_inc/v261/AnsysEM
export AEDT_INSTALL_DIR="$ANSYSEM_ROOT261"
```

先准备 PyAEDT/PyEDB API Memory。该步骤只构建本机知识索引，不能授予 AEDT 写操作权限：

```bash
.venv/bin/ansys-api-memory prepare
.venv/bin/ansys-api-memory status
```

## 连接运行中的 AEDT

在 Linux 主机本机启动 AEDT，并使 gRPC 仅监听本机。可以由你自己的启动方式提供端口，也可使用助手的受控启动：

```bash
.venv/bin/ansys-assistant live-launch \
  --aedt-version 2026.1 \
  --non-graphical
```

它会输出实际端口。连接前先进行只读核对：

```bash
.venv/bin/ansys-assistant live-info --port 50051 --aedt-version 2026.1
```

输出中的活动工程、设计和类型必须与你预期一致。端口或工程不一致时停止，不要让 Agent 反复 attach。

## 启动 Claude harness

```bash
.venv/bin/ansys-agent-linux launch \
  --port 50051 \
  --version 2026.1
```

命令会创建一次性 session 目录，启动受限的 Claude Code，并固定：端口、项目、设计、版本、MCP 工具列表和审批通道。它不会扫描其他 AEDT 进程，也不会启动远程连接。

可以先只生成配置并人工查看：

```bash
.venv/bin/ansys-agent-linux prepare --port 50051 --version 2026.1
```

开始对话时，先让 Agent 连接并核对活动工程。例如：

```text
连接端口 50051，核对当前工程和设计。找到 LineWidth=4.3mil 的所有 line，
先返回参数化为 W_line 的 preview；不要 apply，等我批准。
```

## Linux 审批

读操作可以直接执行。写、求解、取消、导出和保存必须经历：`preview -> 独立审批 -> apply -> readback`。

当 Agent 返回 preview 后，它会给出 session 的 approval socket。请在**第二个终端**运行：

```bash
.venv/bin/ansys-agent-linux approvals --socket /run/user/1000/aedt-agent/<session>.sock
```

命令展示 action、resource、snapshot digest 和精简 preview；确认后才会向本机审批 host 发出一次性决定。不要把 token 粘贴给 Agent。没有 TTY、socket 不存在、审批超时、digest 不匹配或 token 重放时，apply 都会失败并保持 fail-closed。

可拒绝操作：

```bash
.venv/bin/ansys-agent-linux approvals \
  --socket /run/user/1000/aedt-agent/<session>.sock \
  --reject
```

Unix socket 仅在当前用户的私有运行目录中创建，目录权限为 `0700`，socket 权限为 `0600`。不要把该目录放在共享 NFS 路径，也不要改变权限。

## 离线发布包

在一台可联网的 Linux x86_64 CPython 3.12 构建机上，准备与 `codebase-memory-mcp==0.9.0` 匹配的 Linux 原生可执行文件，然后构建包：

```bash
scripts/linux/New-AnsysAgentLinuxBundle.sh \
  --output-directory ./dist \
  --codebase-memory-binary /path/to/codebase-memory-mcp
```

输出为 `ansys-agent-<version>-linux-x86_64-py312.tar.gz` 及 SHA256 文件。压缩包包含 wheelhouse、Linux 原生 `codebase-memory-mcp`、源码、manifest 与校验表；目标机无需访问 GitHub。

在离线 Linux 目标机解压后先验签：

```bash
tar -xzf ansys-agent-*-linux-x86_64-py312.tar.gz
cd ansys-agent-*-linux-x86_64-py312
scripts/Install-AnsysAgentLinux.sh --bundle-root . --verify-only
```

再安装：

```bash
scripts/Install-AnsysAgentLinux.sh \
  --bundle-root . \
  --install-root "$HOME/ansys-agent" \
  --python /usr/bin/python3.12
```

## 上线验收

1. `ansys-api-memory status` 返回 `ready`。
2. `ansys-assistant live-info --port ...` 返回预期工程和设计。
3. 对 HFSS、3D Layout 各跑一次只读 inventory。
4. 选择一个可回滚的低风险变更，确认 preview、第二终端审批、apply 与 readback 都通过。
5. 验证拒绝、过期和重复审批 token 均不能 apply。
6. 释放 session 后确认没有遗留 Python worker 或 AEDT 子进程，也没有持续占用许可证。

真实 HFSS/3D Layout 验收在持牌 Linux runner 上执行；普通 CI 只能覆盖 fake backend、MCP handshake、安装包和审批协议。
