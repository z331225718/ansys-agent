# DSH Agent Preset: ansys-engineering

把本目录安装到 DSH 即得到“Ansys 工程优化模式”自建模式：

```powershell
Copy-Item -Recurse integrations/dsh/agent-presets/ansys-engineering "$env:USERPROFILE\.dsh\.agent-presets\"
```

配套：DSH profile 的 `cordis.patch.yml` 需注册两个 stdio MCP（已在本机完成）：

- `mcp-ansys-assistant` → `.venv\Scripts\python.exe -m aedt_agent.interactive.server`
- `mcp-ansys-api-memory` → `.venv\Scripts\python.exe -m aedt_agent.knowledge.server`

架构依据：docs/adr/0001-adopt-official-pyaedt-mcp.md 与
docs/adr/0002-replace-pi-operator-with-deepseek-harness.md。
本预设只承载 persona、工具目录与技能；所有 AEDT 物理执行代码留在
src/aedt_agent/harness/，经 MCP 暴露。
