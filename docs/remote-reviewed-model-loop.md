# Reviewed-model optimization loop on the AEDT machine

This is the production-shaped entry point for the back half of the BRD via
optimization flow. Run it on the AEDT machine, where Claude Code acts as the
orchestrator and the workers run through the local process harness.

The filename still contains `remote` because early bring-up used a split
orchestrator/AEDT setup. That is not the default production mode. Most runs use
`config\execution_profiles\local_real_aedt.json` with `simulation_runner =
local_cli`, and SSH is not started. Use `ssh_remote` only when the orchestrator
is intentionally running on a different machine from AEDT.

Read `docs/orchestrator-worker-architecture.zh.md` first if there is any
confusion about LLM nodes versus worker nodes, and read
`docs/agent_playbooks/brd-local-cut-optimization.md` for the BRD local-cut
engineering procedure and geometry rules. In short: `kind: agent` nodes use LLM
reasoning; `kind: worker` nodes are standardized engineering executors by
default.

## 1. Pull and prepare

```powershell
cd D:\ansys-agent
git pull
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Copy and edit these files for the machine:

```powershell
Copy-Item config\execution_profiles\local_real_aedt.example.json config\execution_profiles\local_real_aedt.json
Copy-Item config\optimization_loops\reviewed_brd_remote.example.json config\optimization_loops\reviewed_brd_remote.json
```

The loop config must point to a human-reviewed source AEDT project and a
separate working project path. The source project is copied once; the loop then
edits the working project in place.

The score/report section uses conventional engineering plots by default:
S-parameter figures show the configured score band, usually `0-28 GHz`, with
return loss on a `-40 to 0 dB` style axis and the RL target line marked. TDR
plots default to the early `0-120 ps` window so the near-transition features
are readable. Override the TDR window with:

```json
"tdr_plot_time_stop_ps": 120.0
```

For the current s19 bring-up, the source project is usually:

```text
D:\aedt-agent-runs\source\102-006060501_R01_0610-3-s19.aedt
```

Use the actual path on the AEDT machine. Do not keep the old double-directory
form if the `.aedt` file is directly under `D:\aedt-agent-runs\source`.

Before running a long solve, validate the config:

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  mission validate-loop-config `
  --config config\optimization_loops\reviewed_brd_remote.json
```

Use `--no-check-paths` only when reviewing a config on a machine that does not
have the AEDT project files mounted. On the AEDT machine, path checks should
pass before the loop starts.

## 1.1 Windows shell path warning

Run the commands in PowerShell by default. Do not paste the Windows path
examples into Git Bash, bash, or zsh with raw backslashes. In those shells,
backslashes are escape characters, so:

```text
D:\aedt-agent-runs\reviewed-loop\missions.db
```

can become:

```text
D:aedt-agent-runsreviewed-loopmissions.db
```

That creates or reads a different SQLite database. The common symptom is that
`mission run-loop` is active, but the web dashboard at port `8766` shows no
missions or nodes because `mission web` is connected to another DB path.

If Git Bash must be used, quote forward-slash paths instead:

```bash
--db 'D:/aedt-agent-runs/reviewed-loop/missions.db'
```

For long runs, define one PowerShell variable and use it for both `mission web`
and `mission run-loop`:

```powershell
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
```

## 1.2 Windows worker environment

The local process harness preserves the Windows base environment required by
AEDT/PyAEDT, including `APPDATA`, `LOCALAPPDATA`, `USERPROFILE`, `TEMP`, `TMP`,
`SYSTEMROOT`, `PATH`, and `PATHEXT`. If a worker fails with `KeyError:
'APPDATA'`, the worker was launched with an old harness or an over-restricted
profile; pull the latest code and keep these variables in the local AEDT
profile.

Do not apply this rule blindly to `ssh_remote`. In split-machine mode, the SSH
PowerShell session already has its own remote Windows environment, and
`allowed_env` should only forward variables that intentionally come from the
orchestrator side, such as license, Python, or LLM configuration.

## 2. LLM profiles

The graph can use different model profiles by node. Small planner/utility
reasoning can use a low-cost model, while the optimization decider can use a
stronger model.

```powershell
$env:AEDT_AGENT_LLM_API_KEY = "sk-..."
$env:AEDT_AGENT_LLM_BASE_URL = "https://api.openai.com/v1"

$env:AEDT_AGENT_LLM_LOW_COST_MODEL = "gpt-4.1-mini"
$env:AEDT_AGENT_LLM_STANDARD_MODEL = "gpt-4.1-mini"
$env:AEDT_AGENT_LLM_HIGH_REASONING_MODEL = "gpt-4.1"
```

Profile-specific API keys and base URLs are also supported:

```text
AEDT_AGENT_LLM_LOW_COST_API_KEY
AEDT_AGENT_LLM_LOW_COST_BASE_URL
AEDT_AGENT_LLM_HIGH_REASONING_API_KEY
AEDT_AGENT_LLM_HIGH_REASONING_BASE_URL
```

## 3. Start the web view

```powershell
$env:PYTHONUTF8 = "1"
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db $db `
  mission web `
  --host 0.0.0.0 `
  --port 8766 `
  --profile config\execution_profiles\local_real_aedt.json
```

Open:

```text
http://<aedt-machine-ip>:8766
```

The page shows the DAG state, node runs, events, pending approvals, key
artifacts/reports, and the latest `optimization_history.csv` rows. If an
artifact exists on the web server host, the dashboard exposes an `open` link for
the HTML report, CSV, JSON, and SVG/PNG plots. Remote paths that have not been
mirrored are still shown as paths so the orchestrator can report them without
putting raw S-parameter or TDR data into LLM context.

## 4. Run the loop

```powershell
$env:PYTHONUTF8 = "1"
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  --db $db `
  mission run-loop `
  --config config\optimization_loops\reviewed_brd_remote.json `
  --profile config\execution_profiles\local_real_aedt.json `
  --worker-id claude-code-orchestrator `
  --max-workers 1
```

Default polling is 30 seconds. The solve itself is synchronous inside the
worker harness, so the runner is not repeatedly polling AEDT during a long
solve.

`run-loop` is only a thin wrapper around the graph control plane. It creates a
mission and graph run from the YAML template, repeatedly calls
`mission advance-graph`, and returns when the graph succeeds, fails, is
canceled, or waits for approval. To resume an existing graph run, add
`graph_run_id` to the loop config and rerun the same command.

Claude Code or another orchestrator can run the same flow manually:

```powershell
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"

.\.venv\Scripts\python.exe -m aedt_agent.agent --db $db `
  mission create --goal "Reviewed BRD optimization"

.\.venv\Scripts\python.exe -m aedt_agent.agent --db $db `
  mission run-graph --mission-id <mission_id> --template brd_reviewed_model_optimize_loop `
  --initial-payload config\optimization_loops\reviewed_brd_remote.json --max-workers 1

.\.venv\Scripts\python.exe -m aedt_agent.agent --db $db `
  mission graph-status --graph-run-id <graph_run_id>

.\.venv\Scripts\python.exe -m aedt_agent.agent --db $db `
  mission advance-graph --graph-run-id <graph_run_id> --max-workers 1
```

If the graph enters approval, use `mission approve` and then
`mission resume-graph`. If the graph needs a different template or corrected
payload, use `mission takeover`.

## 5. Minimal production checklist

Run this checklist on the AEDT machine before handing the task to Claude Code:

```powershell
git status --short
.\.venv\Scripts\python.exe -m py_compile src\aedt_agent\agent\loop_runner.py src\aedt_agent\agent\web.py
.\.venv\Scripts\python.exe -m pytest tests\test_agent_loop_runner.py tests\test_agent_web.py -q
.\.venv\Scripts\python.exe -m aedt_agent.agent `
  mission validate-loop-config `
  --config config\optimization_loops\reviewed_brd_remote.json
```

The validate command should report:

```text
status: passed
template_loadable: passed
working_project_is_separate: passed
touchstone_is_s4p: passed
differential_traces: passed
tdr_diff1: passed
geometry_constraints: passed
```

## 6. Outputs

The configured `report_dir` receives:

```text
optimization_history.csv
optimization_progress.html
optimization_progress.json
```

Each row records the round, model edit, solve and score status, SDD11/SDD21/TDR
metrics, objective cost, artifact refs, and next recommendation. Raw S-parameter
and TDR files remain artifact-only.
