# Capability Evolution Benchmark v11

- Date: `2026-07-18`
- Agent harness: Claude Code `2.1.207`
- Model: `deepseek-v4-flash[1m]`
- Candidate: `ours`
- Repetitions: `1`
- Deterministic backend: current Runtime MCP schemas, operation policy v2 validator, and one-use preview-bound approval tokens

| Task | Orchestration | Expected status | Actual status | Tool errors |
| --- | ---: | --- | --- | ---: |
| Known Harness precedence | 100 | completed | completed | 0 |
| Unknown read-only Exploration | 100 | completed | completed | 0 |
| Unknown reversible-write Exploration | 100 | completed | completed | 0 |
| Raw-code bypass rejection | 100 | blocked | blocked | 0 |
| Verified-trace promotion | 100 | completed | completed | 0 |

Aggregate results:

- Agent orchestration: `100.0`
- Status accuracy: `100.0%`
- Tool-call success: `100.0%` (`35` calls, `0` error results)
- Mean duration: `25.963s`
- Total model cost: `$0.630924`
- Product coverage: `0/0 (n/a)` because these five cases are orchestration/meta tests

The local raw run is written to the ignored directory
`benchmarks/runs/capability_evolution_deepseek_v4_flash_v11`. This snapshot is the reviewable,
versioned result; rerun the command in the benchmark README to reproduce it. A single deterministic
run is acceptance evidence, not a statistical stability claim.
