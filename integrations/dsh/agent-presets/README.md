# DSH Ansys 模式

## 单一模式

只有一个 **ansys-engineering（Ansys 工程优化模式）**：
- 官方 PyAEDT-MCP + ansys-assistant + ansys-api-memory；
- shell、文件系统、PTC；
- 两阶段 preview/apply/readback 领域修改；
- 生产 Mission 的 start/advance/approval 由 `production-guard.mjs` 在执行级拒绝，仍走本地 CLI/dashboard。

reviewed / explore 两个模式已废弃：Ans​​ys 操作只保留一个入口，避免反复切换。

## 安装

~~~powershell
.\.venv\Scripts\python.exe scripts\dsh\install_ansys_dsh.py check --repo-root . --dsh-profile "$env:USERPROFILE\.dsh\profiles\desktop" --python .venv\Scripts\python.exe
~~~

确认 check 通过后把 `check` 换成 `apply`。安装器会：
- 安装本地 bundle `@local/dsh-ansys-presets`；
- 注册 profile 的 `dsh.profile.bundles`；
- 只在内容变化时备份和写入；
- 移除旧的 managed block；
- 遇到未标记的旧 `preset-ansys-engineering` 时在写入前失败。

## 验证

- 安装器：`tests/dsh/test_install_ansys_dsh.py`
- 模式内生产 guard：bundle 中的 `production-guard.mjs`
