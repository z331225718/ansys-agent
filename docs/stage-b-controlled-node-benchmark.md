# Stage B Controlled Node Benchmark

Stage B starts from the Stage A result: grounded free-code generation with GitNexus/PyAEDT tools is the baseline, and node execution is evaluated as a more controlled candidate path.

## Current 10-Task Result

The current presentation report is:

- `benchmarks/reports/stage_b_10task_compare.html`
- `benchmarks/reports/stage_b_10task_compare.json`

Latest 10-task B/C comparison:

| Group | Method | First-pass | Pass within 3 attempts | Average attempts | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| B | GitNexus + official PyAEDT/examples, free Python code | 70% | 90% | 1.50 all-task average | One unresolved task: `L2_dipole_antenna` |
| C | JSON node plan + controlled local nodes | 80% | 100% | 1.20 all-task average | Free-code execution count: 0 |

The report is a structural benchmark. Validation checks real AEDT model state, objects, materials, ports, boundaries, setups, and sweeps where available, but it is not a full electromagnetic correctness proof.

## Environment

Required local components:

- Python virtual environment: `.venv`
- PyAEDT and pyedb installed in the venv
- AEDT 2026.1 under `~/ansys_inc/v261`
- GitNexus eval server on `http://127.0.0.1:4848`
- Harness CLI when running Group B/C with a tool-enabled agent

Useful checks:

```bash
.venv/bin/python -m pytest -q
gitnexus query "Hfss wave_port"
ps -eo pid,etime,cmd | rg "gitnexus|ansysedt|run_stage_b_benchmark" || true
```

## Configuration

Public defaults live in:

- `config/benchmark_config.json`
- `config/harness/group_b.json`
- `config/harness/group_c.json`

Private local overrides live in:

- `config/benchmark_config.local.json`

This local file is ignored by git. Put API credentials and local model settings there. For OpenAI-compatible testing with DeepSeek, use this shape:

```json
{
  "generator": {
    "backend": "openai",
    "openai": {
      "base_url": "<provider-base-url>",
      "api_key": "<provider-api-key>",
      "model": "deepseek-v4-flash"
    }
  }
}
```

Do not commit `config/benchmark_config.local.json`.

## Baseline

Stage A Group B final metrics:

- First-pass success: 80.0%
- Success within three attempts: 100.0%
- Average successful attempt: 1.20

## Candidate Group C

Group C asks the harness for a JSON node plan instead of Python code:

```json
{
  "plan": [
    {
      "id": "substrate",
      "node_id": "create_substrate",
      "inputs": {
        "origin": [0, 0, 0],
        "size": [20, 15, 0.8],
        "material": "FR4_epoxy"
      }
    }
  ]
}
```

The benchmark runner parses the plan, calls `execute_node` for each step, records JSONL audit events, and rejects free-code fallback. A later node can consume an earlier node output through a reference such as:

```json
{"$ref": "feed_face.output.selected_face_id"}
```

Candidate C is counted as passing only when all nodes execute and the task validation script passes against the real model snapshot from `get_model_info()`.

## Local Plumbing Check

This command verifies the Stage B runner, parser, fake node kernel, audit log, and report path without launching AEDT or a harness CLI:

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --fake-node-kernel \
  --dry-run-node-plan \
  --groups C \
  --task L1_create_substrate \
  --max-attempts 1 \
  --run-dir benchmarks/runs/stage_b_fake_smoke
```

## Real AEDT Acceptance

`PyaedtAdapter` auto-detects the local 2026.1 install under `~/ansys_inc/v261` when `ANSYSEM_ROOT261` and `AWP_ROOT261` are not already exported. The explicit config values in `config/benchmark_config.json` are still passed by the benchmark script.

Run the real adapter smoke test:

```bash
RUN_REAL_AEDT=1 .venv/bin/python -m pytest tests/test_pyaedt_adapter_contract.py tests/test_real_aedt_nodes.py -q -s
```

Real acceptance must run without `--fake-node-kernel` and should start with the smoke set:

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --task L1_create_substrate \
  --task L1_create_setup \
  --task L1_create_wave_port \
  --task L2_microstrip_line \
  --task Trap_waveport_wrong_face \
  --groups B C \
  --max-attempts 3
```

Fake adapter tests are unit coverage only. They are not benchmark evidence.

## 10-Task Benchmark Commands

Run C-only 10-task benchmark with real AEDT node execution:

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_10task_after_node_fixes
```

Run B-only 10-task benchmark:

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups B \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_b_10task_after_node_fixes
```

Each run writes:

- `stage_b_report.json`
- `stage_b_report.html`
- Group B attempt artifacts under `baseline_b/`
- Group C node artifacts under `node_c/`
- C-group audit events in `stage_b_node_audit.jsonl`

## Build the Chinese Presentation Report

After B-only and C-only runs exist, build the presentation-safe Chinese report:

```bash
.venv/bin/python scripts/build_stage_b_report.py \
  --group-b-report benchmarks/runs/stage_b_b_10task_after_node_fixes/stage_b_report.json \
  --group-c-report benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json \
  --output-html benchmarks/reports/stage_b_10task_compare.html \
  --output-json benchmarks/reports/stage_b_10task_compare.json \
  --model-name "deepseek-v4-flash / AEDT 2026.1"
```

The report builder removes artifact path fields and scrubs local absolute paths from summaries before writing the presentation JSON/HTML.

Sanity checks:

```bash
rg -n "实验设计|判定依据|关键发现|自由代码执行次数" benchmarks/reports/stage_b_10task_compare.html
rg -n "/home/zzmjay|sk-|api\\.deepseek\\.com|deepseek-v4-flash.*sk-" benchmarks/reports/stage_b_10task_compare.html benchmarks/reports/stage_b_10task_compare.json || true
```

## Pass/Fail Criteria

A task passes only when:

- candidate generation succeeds,
- AEDT execution finishes,
- validation script passes against the real model info,
- and the task succeeds within the configured attempt limit.

A task fails when generation, JSON parsing, schema validation, node reference resolution, PyAEDT/AEDT runtime execution, timeout, or validation fails after all allowed attempts.

Group C must keep `free_code_execution_count` at `0`; otherwise it is no longer measuring the controlled-node path.
