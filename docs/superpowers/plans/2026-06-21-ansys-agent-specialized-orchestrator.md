# ansys-agent Specialized Orchestrator Plan

## 目标

在当前特性分支实现一个最小可运行的 ansys-agent MVP：

```text
python -m aedt_agent.ansys_agent preflight --case ...
python -m aedt_agent.ansys_agent run --case ...
python -m aedt_agent.ansys_agent status --case ...
```

MVP 只做专属编排壳，不重写 worker，不做通用 coding agent。

## 实施步骤

- [x] 新增 spec/plan 文档，固定设计边界。
- [x] 新增 `config/cases/reviewed_brd.example.json`。
- [x] 新增 `src/aedt_agent/ansys_agent/` 对外入口包。
- [x] 保留已有受控编排实现作为兼容层，避免破坏已验证的 loop。
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
- [x] 更新 README 和架构说明，说明 ansys-agent 是内置轻量专属编排器。
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

## Phase 4 Approval / Resume Flow

- [x] `resume` 遇到 pending approval 时返回 `waiting_approval`，不重新启动 worker。
- [x] `approve` 支持 `--resume --graph-run-id <id>`，由用户明确批准后恢复同一个 graph。
- [x] `status.available_commands` 暴露 `approve_and_resume`，并携带 graph run id。
- [x] 新增测试覆盖 pending gate 保护和 approve+resume 一步恢复。

## Phase 5 ansys-agent Operator Panel

- [x] `ansys_agent web` 启动轻量 operator panel，而不是只代理通用 dashboard。
- [x] Panel 提供 `/api/status` 和受控 POST action：resume / approve / reject / stop。
- [x] 页面展示 status、recommended command、pending approvals、bounded metrics、artifact refs、failure summary。
- [x] 页面按 case `poll_interval_seconds` 低频刷新，最小 10s。
- [x] 页面不读取 raw Touchstone/TDR 内容。
- [x] 新增 web 渲染和 dispatch 测试。

## Phase 6 Interactive CLI

- [x] 新增 `ansys_agent cli` / `ansys_agent chat` 交互入口。
- [x] 支持中文/英文自然语言意图：开始优化、看状态、预检、继续、批准、批准并继续、拒绝、停止、打开页面、退出。
- [x] 交互入口只路由到已有受控命令，不直接调用 worker 内部脚本，不绕过 approval gate。
- [x] 支持 `--once`，方便远端 smoke 和脚本化验证。
- [x] 新增 intent、approval resume 和 CLI once 测试。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_ansys_agent_case_config.py `
  tests\test_ansys_agent_status.py `
  tests\test_ansys_agent_cli.py `
  -q

.\.venv\Scripts\python.exe -m aedt_agent.ansys_agent preflight `
  --case config\cases\reviewed_brd.example.json `
  --no-check-paths
```
