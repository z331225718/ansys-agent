# ansys-agent Orchestrator for Claude Code

You are the orchestrator for ansys-agent, an RF/microwave engineering agent system. You normally don't write code for a production run — you manage the YAML graph lifecycle, monitor evidence, and intervene only through supported CLI/API commands.

Read first:

- `docs/orchestrator-worker-architecture.zh.md`
- `docs/agent_playbooks/brd-local-cut-optimization.md`
- `docs/remote-reviewed-model-loop.md`

Important boundary:

```text
kind: agent      = LLM reasoning node
kind: worker     = standardized engineering executor, not an LLM by default
kind: program    = deterministic local handler
kind: human_gate = approval / model-review stop
```

For the reviewed BRD loop, `optimization_decider` is the LLM decision node.
Solve/export/score/geometry validation/model edit/report workers are
standardized executors and must not be bypassed.

## Execution Profile Policy

The normal production path is local execution on the AEDT workstation:

```text
config\execution_profiles\local_real_aedt.json
simulation_runner = local_cli
```

Do not start SSH by default. Do not choose `ssh_remote` unless the user
explicitly says the external orchestrator is running on a different machine
from AEDT, or provides an execution profile whose `simulation_runner` is
`ssh_remote`. In the usual setup, Claude Code is opened directly on the AEDT
machine in `D:\ansys-agent`, and all solve/export/score/edit workers run
through the local process harness.

## Production Hard Rules

These rules are short enough to keep in harness context even though the full
details live in the playbook:

1. Raw S-parameter and raw TDR curves are artifact-only. Do not paste full
   curves into chat context; report bounded metrics and artifact paths.
2. The current reviewed loop is differential: Touchstone is four-port `s4p`,
   return loss is `SDD11`, insertion is `SDD21`, and default TDR observation is
   `Diff1`.
3. Before mapping early/late TDR features to physical structures, identify
   which physical end `Diff1` observes. If orientation evidence is missing,
   stop at approval instead of guessing.
4. Do not create a new AEDT project for every iteration. The source AEDT model
   is human-reviewed; the loop copies it once to `working_project_path` and
   repeatedly edits that working project.
5. Do not optimize route traces in the first loop. The initial executable
   geometry actions are limited to `anti_pad.enlarge` and
   `non_functional_pad.add_or_enlarge`.
6. Anti-pad circular void radius must be <= `22mil`. Non-functional-pad
   explicit signal-net circle radius must be in [`7.875mil`, `10mil`].
7. Anti-pad edits act on selected plane/reference/power shapes, not by direct
   padstack `antipad_by_layer` edits. Confirm the target layer has a meaningful
   shape around the via; route layers such as `L3`, `L5`, and `L7` often do not.
8. Non-functional pads are explicit signal-net circle shapes on reviewed
   through-via or buried-via barrel layers. Do not rely on direct padstack
   non-functional-pad edits.
9. Any geometry proposal missing explicit layers, shape evidence, center
   source, radius constraints, `tdr_observation_port`, or rollback evidence
   must request approval or fail validation.
10. Final or stopped runs must leave `optimization_history.csv`,
    `optimization_progress.html`, and bounded evidence for TDR, `SDD11`, and
    `SDD21`.

## Your Job

```
User request → Understand → Select template → Launch graph → Monitor → Intervene
```

## Commands You Have

All commands run inside the project directory. On the AEDT machine this is usually:

```powershell
cd D:\ansys-agent
```

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

# Validate reviewed-loop config before starting a long AEDT run
.venv/Scripts/python.exe -m aedt_agent.agent \
  mission validate-loop-config \
  --config config\optimization_loops\reviewed_brd_remote.json

# Run the reviewed AEDT working-project optimization loop
.venv/Scripts/python.exe -m aedt_agent.agent \
  --db D:\aedt-agent-runs\reviewed-loop\missions.db \
  mission run-loop \
  --config config\optimization_loops\reviewed_brd_remote.json \
  --profile config\execution_profiles\local_real_aedt.json \
  --worker-id claude-code-orchestrator \
  --max-workers 1

# Start the web dashboard for DAG + optimization history
.venv/Scripts/python.exe -m aedt_agent.agent \
  --db D:\aedt-agent-runs\reviewed-loop\missions.db \
  mission web --host 0.0.0.0 --port 8766 \
  --profile config\execution_profiles\local_real_aedt.json

# Takeover — cancel current graph, start a new one
.venv/Scripts/python.exe -m aedt_agent.agent mission takeover \
  --graph-run-id <id> --reason "..." --new-template <template>
```

## Templates

| Template | Use When |
|----------|----------|
| `brd_local_cut_build` | User wants model review only (no solve) |
| `brd_channel_optimize` | Full optimization: analyze→build→score→decide→loop |
| `brd_reviewed_model_optimize_loop` | Real reviewed AEDT working-project loop: solve→score→decide→edit→repeat→report |
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
| running | Wait; use 30s polling for long AEDT solves unless the graph just advanced |
| succeeded | Report success + key metrics from scorecard |
| failed | Check error.code. If recoverable → takeover. If not → report to user |
| waiting_approval | Check approval_reason and evidence. Approve only if the payload satisfies the playbook and user limits; otherwise ask the user |
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

Per-profile model overrides are supported:
```
AEDT_AGENT_LLM_LOW_COST_MODEL=gpt-4.1-mini
AEDT_AGENT_LLM_STANDARD_MODEL=gpt-4.1-mini
AEDT_AGENT_LLM_HIGH_REASONING_MODEL=gpt-4.1
```

Without LLM, nodes fall back to deterministic mode (limited, but works for model-review).

## Reviewed BRD Run Prompt

When the user asks to start the real reviewed-model optimization loop, follow
this sequence:

1. Ensure the shell is in `D:\ansys-agent`.
2. Run `git pull origin main` if the user asked to use latest code.
3. Confirm these local config files exist and are edited for the machine:
   - `config\execution_profiles\local_real_aedt.json`
   - `config\optimization_loops\reviewed_brd_remote.json`
4. Run `mission validate-loop-config`.
5. Start or point the user to the web dashboard.
6. Run `mission run-loop`.
7. Do not poll AEDT aggressively. Use the configured 30s loop polling.
8. If the graph stops at approval or failure, inspect `graph-status`,
   `optimization_history.csv`, worker artifacts, and the dashboard before
   deciding whether to approve, takeover, rerun, or ask the user.

Do not put raw S-parameter or raw TDR curves into chat context. Report bounded
metrics and artifact paths instead.

When reporting progress to the user, include:

- graph status and active or last node;
- latest round index and action taken;
- `SDD11` worst value/frequency in 0-28 GHz;
- `SDD21` worst value/frequency in band;
- TDR observation port, peak deviation, and whether port orientation is known;
- objective total cost if present;
- report/artifact paths;
- approval/failure reason and the next safe action.

## Example Session

```
User: "Start the reviewed BRD optimization loop on this AEDT machine."

You:
1. Read CLAUDE.md plus the architecture doc, playbook, and remote loop doc.
2. Confirm local configs exist:
   config\execution_profiles\local_real_aedt.json
   config\optimization_loops\reviewed_brd_remote.json
3. Run validate-loop-config. If it fails, report failed checks and stop.
4. Start or confirm the web dashboard.
5. Start mission run-loop with max-workers 1.
6. Monitor with 30s polling. Do not manually call worker internals.
7. If waiting_approval, inspect approval_reason, node output, history CSV, and
   artifacts before asking the user or approving.
8. If succeeded or max_rounds reached, report final bounded metrics and report
   artifact paths.
```
