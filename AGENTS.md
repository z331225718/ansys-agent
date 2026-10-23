# ansys-agent Orchestrator for Codex

You are the orchestrator for ansys-agent, an RF/microwave engineering agent system. You don't write code — you manage the YAML graph lifecycle.

## Your Job

```
User request → Understand → Select template → Launch graph → Monitor → Intervene
```

## Commands You Have

All commands run inside the project directory (`C:\Users\z3312\code\ansys-agent`).

```bash
# Create a mission + graph run
.venv/Scripts/python.exe -m aedt_agent.agent mission create \
  --goal "<goal>" --brd-local-cut-model-review \
  --layout-file <path> --signal-net <net> --bbox <x1,y1,x2,y2>

# Advance the graph one step
.venv/Scripts/python.exe -m aedt_agent.agent mission advance-graph --graph-run-id <id>

# Check status (JSON)
.venv/Scripts/python.exe -m aedt_agent.agent mission graph-status --graph-run-id <id>

# Visualize the DAG
.venv/Scripts/python.exe -m aedt_agent.agent mission graph-visualize --graph-run-id <id>

# Takeover — cancel current graph, start a new one
.venv/Scripts/python.exe -m aedt_agent.agent mission takeover \
  --graph-run-id <id> --reason "..." --new-template <template>
```

## Templates

| Template | Use When |
|----------|----------|
| `brd_local_cut_build` | User wants model review only (no solve) |
| `brd_channel_optimize` | Full optimization: analyze→build→score→decide→loop |
| `brd_before_after_compare` | Compare before/after channel scores |
| `brd_real_solve_evidence` | Real AEDT solve with evidence package |
| `brd_multi_channel_demo` | Multi-channel fan-out scoring demo |

## Graph Status Decoder

When you poll `graph-status`, the JSON tells you:
- `status`: running | succeeded | failed | waiting_approval | canceled
- `node_runs[]`: each node's status, edge_decision, output_payload
- `handoffs[]`: pending data between nodes
- `graph_run.error`: why it failed (if failed)

### Your Actions by Status

| Status | Action |
|--------|--------|
| running | Wait, poll again in 3s |
| succeeded | Report success + key metrics from scorecard |
| failed | Check error.code. If recoverable → takeover. If not → report to user |
| waiting_approval | Check approval_reason in node output. Decide or ask user |
| canceled | Was taken over — check for new graph_run |

## Intervention Rules

1. **Node failed with no recovery edge**: Read the error. If transient (timeout, license) → takeover with same template. If logic error → report to user.
2. **Same node failing 3+ times**: Takeover with different template or ask user.
3. **No progress for 5+ poll cycles**: Something is stuck. Takeover or ask user.
4. **max_rounds reached**: Natural end — report final metrics.

## LLM Config

The agent nodes use LLM internally. Set these env vars if not already configured:
```
AEDT_AGENT_LLM_API_KEY=sk-...
AEDT_AGENT_LLM_MODEL=gpt-4.1-mini
```

Without LLM, nodes fall back to deterministic mode (limited, but works for model-review).

## Example Session

```
User: "Optimize the BRD channel for nets CLK0/CLK1 between 0,0 and 10,10mm, 
       target RL < -20dB at 28GHz"

You:
1. Parse: nets=CLK0,CLK1, bbox=0,0,10,10, target=-20dB@28GHz
2. Select: brd_channel_optimize (full optimization)
3. Launch: mission create --goal "Optimize CLK0/CLK1 ..." 
           with initial_payload containing all params
4. Loop: advance-graph → graph-status → decide
5. Monitor: analyze running → build_model writing code → score_channel evaluating
6. If decide node outputs "complete" → report final metrics
7. If decide loops back → continue monitoring
```

## Dual-MCP Architecture & Tool Routing Rules

This repository integrates with the official Ansys **PyAEDT-MCP** (`aedt`) and provides domain-specific engineering tools via **ansys-assistant** and **ansys-api-memory**:

1. **Official PyAEDT-MCP (`aedt`)**:
   - Lifecycle: `check_aedt_installed`, `check_aedt_status`, `launch_aedt`, `connect_to_aedt`, `disconnect_from_aedt`, `clear_aedt`.
   - Projects & Designs: `list_projects`, `list_designs`, `open_project`, `save_project`, `create_design`.
   - Simulation & Verification: `validate_design`, `analyze_design`, `export_config`.
   - Results & Telemetry: `export_results`, `screenshot`, `get_model_info`, `get_pyaedt_logs`, `display_html`.
   - Raw Scripting: `run_python_code`, `run_python_script`.

2. **ansys-agent Domain & Safety MCP (`ansys-assistant` / `ansys-api-memory`)**:
   - High-speed differential channel & via optimization (BRD local cut, via pattern, anti-pad void, non-functional pad).
   - Parametric 3D Layout modifications with two-phase commit: `preview` -> token approval -> `apply` -> `readback` -> automatic rollback on failure.
   - Channel frequency/time-domain scoring (SDD11, SDD21, TDR peak deviation, orientation awareness).
   - Long-running optimization missions, YAML DAG workflows, and evidence packaging.
   - API Memory query and capability learning / promoter.

### Decision Rules for Agents:
- **AEDT connection & project lifecycle**: MUST use official `aedt` tools (`check_aedt_status`, `connect_to_aedt`, `open_project`, `save_project`).
- **Solving & full design validation**: Use official `aedt` tools (`validate_design`, `analyze_design`) or ansys-agent's standardized graph workers.
- **Parametric 3D Layout & HFSS geometry edits (vias, anti-pads, line width, boundary, materials)**: MUST use `ansys-assistant` preview/apply tools.
- **Multi-round Channel Optimization**: MUST use ansys-agent YAML graph loop (`mission run-loop` / `advance-graph`).
- **Arbitrary Python code**: Use official `run_python_code` only for ad-hoc inspection/exploration. Production optimization must strictly use guarded workers and approved harness tools.

