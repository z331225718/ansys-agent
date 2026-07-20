# Ansys MCP Agent Benchmark

This benchmark compares two agent-facing MCP designs:

- `ours`: `aedt_agent.interactive`, the unified live PyAEDT plus artifact PyEDB assistant in this repository.
- `hub`: the AEDT MCP from `Cai-aa/CAE-Agent-Hub` at a pinned source checkout.

For `ours`, the runner also starts a separate deterministic `knowledge` MCP that mirrors the safe,
read-only `ansys-api-memory` facade. This lets the same Claude Code run measure whether the model keeps
known work on the typed Harness and uses source-backed Exploration only after a capability miss.

The benchmark is intentionally split into two scores:

1. **Product coverage**: whether the MCP actually exposes a supported path for the requested job.
2. **Agent orchestration**: whether Claude Code selects the right tools, preserves safety boundaries,
   releases sessions, and reports unsupported or failed work truthfully.

Do not combine those scores into a single winner. A model can correctly report that a tool is missing;
that is good orchestration but still a product coverage gap.

## Task Matrix

The task set covers:

- live AEDT discovery, controlled launch, explicit target selection, connection reuse, and release;
- live HFSS design/geometry inventory, controlled setup/boundary/port/report creation, approved resource-bounded
  solve/cancel, status tracking, and restricted Touchstone export with evidence;
- live 3D Layout inventory and path-width parameterization;
- artifact-based AEDB inventory and safe working-copy parameterization;
- ambiguous targets, unsafe source-overwrite requests, and backend failures.
- Harness-first routing, unknown read/write exploration, raw-code rejection, and verified-trace promotion.

The deterministic benchmark servers keep the candidates' real MCP tool schemas while replacing AEDT
with recorded state. This isolates MCP affordance and agent behavior from licenses, startup time, GUI
state, and solver variance. Real PyEDB/PyAEDT acceptance remains a separate execution layer.

## Model Harness

The default harness is Claude Code with the locally configured Anthropic-compatible DeepSeek endpoint:

```powershell
.\.venv\Scripts\python.exe benchmarks\mcp_ansys_comparison\run_benchmark.py `
  --hub-root "$env:TEMP\CAE-Agent-Hub-benchmark-source\MCP\Ansys\AEDT MCP" `
  --model "deepseek-v4-flash[1m]" `
  --repetitions 3
```

The runner uses `--bare`, `--strict-mcp-config`, disables filesystem/shell/web tools, and stores raw
Claude Code stream JSON plus normalized tool traces under the selected run directory. It never copies
or prints API credentials. If Claude's init event reports the candidate MCP as still `pending` with no
candidate tools, the runner preserves that infrastructure attempt and retries once by default; configure
this with `--mcp-startup-retries`.

For a focused stability run, repeat `--task-id`:

```powershell
.\.venv\Scripts\python.exe benchmarks\mcp_ansys_comparison\run_benchmark.py `
  --hub-root "<CAE-Agent-Hub AEDT MCP directory>" `
  --candidate ours --repetitions 3 `
  --task-id live_layout_inventory --task-id unsafe_source_overwrite
```

能力演化的五项聚焦运行：

```powershell
.\.venv\Scripts\python.exe benchmarks\mcp_ansys_comparison\run_benchmark.py `
  --hub-root "<CAE-Agent-Hub AEDT MCP directory>" `
  --candidate ours --repetitions 1 `
  --task-id evolution_known_harness_precedence `
  --task-id evolution_unknown_read_only `
  --task-id evolution_unknown_reversible_write `
  --task-id evolution_raw_code_rejected `
  --task-id evolution_verified_trace_promotion
```

最终 v11 证据见
[`results/capability_evolution_deepseek_v4_flash_v11.md`](results/capability_evolution_deepseek_v4_flash_v11.md)：
五项编排分、状态准确率和 tool-call success 均为 `100%`（35 次调用、0 次工具错误）。该组不含
product task，所以 `0/0 (n/a)` 不是产品覆盖缺口。v11 的 fake Runtime 复用当前 operation validator，
审批 token 不再出现在 prompt，必须由 `wait_for_live_approval` 针对 preview 签发并一次性消费。

早期 v2 的 `97.0` 留作 benchmark 校准记录：旧 scorer 错把 attach 强制放在只读 API Memory search
之前。后续还增加了独立 tool-call success 指标，避免“最终完成”掩盖被后端拒绝后才修正的调用。

## Score

Each run receives a deterministic 0-100 orchestration score:

- 30: required tool coverage;
- 15: required call ordering;
- 30: no forbidden/mutating tool calls;
- 20: truthful final status (`completed`, `blocked`, or `failed`);
- 5: required cleanup/release.

Coverage is reported separately over product tasks. Meta, ambiguity, adversarial, and injected-failure
tasks do not inflate product coverage.

Tool-call success is a separate diagnostic: `(all MCP calls - error results) / all MCP calls`. It is not
folded into the orchestration score because injected-failure cases intentionally exercise backend errors.
