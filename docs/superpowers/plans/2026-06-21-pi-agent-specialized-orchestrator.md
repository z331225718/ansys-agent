# Pi Agent Specialized Orchestrator Plan

## 目标

在 `codex/pi-agent-ansys-agent` 分支实现一个最小可运行的 Pi Agent MVP：

```text
python -m aedt_agent.pi_agent preflight --case ...
python -m aedt_agent.pi_agent run --case ...
python -m aedt_agent.pi_agent status --case ...
```

MVP 只做专属编排壳，不重写 worker，不做通用 coding agent。

## 实施步骤

- [x] 新增 spec/plan 文档，固定设计边界。
- [x] 新增 `config/cases/reviewed_brd.example.json`。
- [x] 新增 `src/aedt_agent/pi_agent/` 包：
  - `case_config.py`：读取和校验 case config。
  - `supervisor.py`：构建 runtime，调用 loop runner。
  - `status.py`：从 graph/status/history/report artifact 生成紧凑状态。
  - `__main__.py`：CLI 入口。
- [x] 安全策略：
  - 默认拒绝 `ssh_remote`。
  - 默认 `max_workers=1`。
  - 默认 30s 轮询。
  - 不输出 raw S 参数/TDR。
- [x] 新增测试：
  - case config 默认和路径解析。
  - local profile 通过，ssh profile 默认拒绝。
  - preflight 调用 reviewed loop 校验。
  - status 从 graph report / history CSV 中抽取紧凑字段。
  - CLI 输出 JSON。
- [x] 更新 README 和架构说明，说明 Pi Agent 是内置轻量专属编排器。
- [x] 运行 targeted tests。

## MVP 不做

- 不加后台 daemon。
- 不新增 LLM provider。
- 不新增真实 AEDT 几何优化算法。
- 不改现有 worker contract。
- 不改变 Claude Code/Codex 仍可作为外层 harness 的兼容性。

## Phase 2 控制命令

- [x] `init`：从 example 生成 local case/profile/loop config，不覆盖已有文件除非 `--force`。
- [x] `resume`：继续最新或指定 graph run。
- [x] `approve` / `reject`：走既有 `ApprovalService`。
- [x] `stop`：取消 graph run 和 mission。
- [x] `web`：按 case 的 db/profile/dashboard 配置启动现有 dashboard。
- [x] `resume` / `web` 复用 profile 安全检查，默认拒绝 `ssh_remote`。

## Phase 3 Operator Status

- [x] `status` 输出 pending approvals，而不是只看最新 waiting node。
- [x] 输出 `recommended_command`、`available_commands` 和 `dashboard_url`。
- [x] `waiting_approval` 时不默认推荐 approve；approve/reject 只作为可选命令暴露。
- [x] 输出失败摘要：graph error 和 failed node errors。
- [x] 输出 latest artifact refs 和 artifact kind/existence，不读取 raw S 参数/TDR 内容。
- [x] 保留原有 `metrics`、`approval`、`artifacts` 字段以兼容轻量 harness。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_pi_agent_case_config.py `
  tests\test_pi_agent_status.py `
  tests\test_pi_agent_cli.py `
  -q

.\.venv\Scripts\python.exe -m aedt_agent.pi_agent preflight `
  --case config\cases\reviewed_brd.example.json `
  --no-check-paths
```
