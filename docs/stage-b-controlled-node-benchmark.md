# Stage B Controlled Node Benchmark

Stage B starts from the Stage A result: grounded free-code generation with GitNexus/PyAEDT tools is the baseline, and node execution is evaluated as a more controlled candidate path.

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
