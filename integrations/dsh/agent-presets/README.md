# DSH Agent Presets

## 推荐模式

- **ansys-reviewed**：M1 只读观察。独立 reviewed MCP 进程，仅暴露 workflow catalog/status + ansys-api-memory；无官方 PyAEDT-MCP、shell、文件写、PTC、preview/apply。生产启动/推进/审批仍走本地 CLI/dashboard。
- **ansys-explore**：非生产探索。保留官方 PyAEDT-MCP、受控领域工具和 PTC；所有结果都不构成生产审批证据。

## 安装

~~~powershell
.\.venv\Scripts\python.exe scripts\dsh\install_ansys_dsh.py check --repo-root . --dsh-profile "$env:USERPROFILE\.dsh\profiles\desktop" --python .venv\Scripts\python.exe --workflow-db D:\aedt-agent-runs\reviewed-loop\missions.db
~~~

确认 check 通过后，把 check 换成 apply。安装器只替换带 ansys-agent-dsh managed block 标记的块，先备份，重复执行幂等，不解析/重写含 !!js 的整份 YAML。

## 旧模式迁移

ansys-engineering 不再作为含义不清的全权限生产模式。安装器遇到未标记的旧 preset-ansys-engineering 会 fail before write；请人工确认后改名为 ansys-explore 或删除旧条目，再执行 apply。安装器不会自动覆盖未标记条目。

## 验证

- 服务端 reviewed allowlist：tests/test_interactive_ansys_assistant.py::test_reviewed_mcp_profile_exposes_only_read_only_workflow_catalog
- 安装器：tests/dsh/test_install_ansys_dsh.py
