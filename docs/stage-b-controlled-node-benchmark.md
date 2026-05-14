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

The benchmark runner parses the plan, calls `execute_node` for each step, records JSONL audit events, and rejects free-code fallback.

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
