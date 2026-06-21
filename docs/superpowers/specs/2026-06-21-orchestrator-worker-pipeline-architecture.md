# Orchestrator-Worker Pipeline Architecture Spec

**Status:** draft
**Date:** 2026-06-21
**Owner:** ansys-agent
**Primary use case:** reviewed BRD high-speed differential via optimization with real AEDT workers

## Purpose

This document freezes the intended architecture so the project does not drift
back into a hidden script-style loop.

The system is an engineering agent, not a single automation script. A human or
external LLM orchestrator gives the goal. The orchestrator selects and monitors
a YAML graph. The graph runs many small workers. Each worker does one bounded
thing. Handoffs are structured JSON. Scorecard/qualifier code audits the real
database records and artifacts.

The current reviewed BRD loop is allowed to be a vertical slice, but its final
shape must be this pipeline:

```text
External Orchestrator (Codex / Claude Code / Pi / other LLM harness)
  understand goal -> select template -> launch graph -> monitor -> intervene

YAML Graph Template
  nodes + edges + handoffs + profiles + constraints + failure routes

Single-purpose Workers
  prepare -> solve -> export -> score -> decide -> approve -> edit -> qualify -> report

Scorecard / Qualifier
  inspect SQLite records, worker payloads, manifests, hashes, and artifact files
```

## Non-Negotiable Principles

1. **YAML is the core interface.** Topology, node roles, handoff schemas,
   profiles, constraints, retries, approvals, and loop limits belong in YAML,
   not in a Python loop hidden behind one command.
2. **The orchestrator is outside the graph.** Codex, Claude Code, Pi, or any
   other harness may act as the brain, but it should operate the graph through
   CLI/API commands and status records. It should not directly run worker
   internals.
3. **Workers are narrow executors.** A worker does one thing and returns
   structured evidence. It does not silently plan, judge, choose geometry, or
   call unrelated workers.
4. **LLM nodes reason; workers execute.** LLM nodes may analyze, decide,
   propose, summarize, and request approval. Standardized workers convert,
   cut, solve, export, score, edit, and report.
5. **Handoff is the only node communication channel.** Nodes do not depend on
   chat memory or free-form prose. The receiving node consumes a validated JSON
   payload.
6. **Raw curves are artifact-only.** Raw S-parameter and TDR data are never
   put into LLM context. Workers produce bounded evidence and targeted query
   artifacts.
7. **Scorecard is programmatic audit.** It does not trust LLM summaries. It
   checks persisted NodeRun/JobAttempt records, handoffs, manifests, hashes,
   artifact existence, and required metrics.
8. **The working AEDT project is controlled.** Copy a human-reviewed source
   model once into a working project, then edit that working project in place
   with manifests and checkpoints. Do not create a new AEDT project bundle per
   iteration.
9. **Human gates are first-class graph nodes.** Initial model review and any
   missing engineering limits must stop the graph until approved.
10. **A thick run-loop is technical debt.** `run-loop` may exist as a
    convenience wrapper, but it must only create/advance/monitor a graph. It
    must not become the place where engineering intelligence lives.

## System Roles

### External Orchestrator

Examples: Codex, Claude Code, Pi, or another LLM harness.

Responsibilities:

- understand the user's engineering goal;
- select a graph template;
- build the initial payload from user inputs and known config;
- launch the graph through ansys-agent CLI/API;
- poll status at an appropriate interval for long AEDT solves;
- inspect failures and decide whether to retry, takeover, or ask the user;
- handle approval prompts when policy allows;
- summarize progress from scorecard/report artifacts.

The orchestrator may use LLM reasoning, but it should not bypass the graph by
calling worker scripts directly for production workflows.

### ansys-agent Runtime

Responsibilities:

- load YAML graph templates and persist a template snapshot per GraphRun;
- validate node, edge, profile, and handoff definitions;
- persist GraphRun, NodeRun, GraphHandoff, Job, JobAttempt, Approval, Event,
  and Evidence records;
- schedule ready nodes;
- execute program, worker, agent, scorecard, and human-gate nodes;
- enforce graph budgets, edge traversal limits, node max runs, retries,
  approval blocking, and failure outcomes;
- expose CLI/API/web status for the external orchestrator.

### YAML Graph Template

The template is the product contract between the orchestrator and the runtime.
It must define:

- `nodes`: id, role, kind, capability/handler, profile, constraints,
  input_schema, output_schema, max_runs;
- `edges`: from, to, condition, retry/back edge, max_traversals;
- `handoffs`: required fields and bounded payload contracts;
- `profiles`: model/cost profile names such as `low_cost`, `standard`,
  `high_reasoning`;
- approval gates and scorecard/qualifier nodes;
- graph-level limits such as `max_steps` and default poll interval.

### Worker

A worker is an executable capability, usually reached through the process
harness or a local/remote simulation runner.

Worker rules:

- accepts one validated input payload;
- performs one capability;
- writes manifests and artifacts;
- returns one bounded output payload;
- records enough evidence for scorecard audit;
- does not make broad engineering decisions;
- is idempotent or records enough state to recover safely;
- never hides raw S-parameters or TDR arrays inline in LLM handoffs.

### Agent Node

An agent node is an LLM-backed graph node. It may be cheap or expensive by
profile.

Allowed responsibilities:

- analyze a goal;
- choose among bounded candidate actions;
- produce a structured proposal;
- explain why approval is needed;
- decide whether to continue, complete, rollback, or ask for human review.

Forbidden responsibilities:

- invent missing stackup values, component groups, layer evidence, geometry
  limits, or pass/fail metrics;
- read raw curve arrays directly into context;
- call AEDT or mutate project files;
- silently skip required approval or scorecard checks.

### Scorecard / Qualifier

The scorecard is a deterministic program node. It verifies that the graph did
the claimed work.

It must check at least:

- graph terminal state and node outcomes;
- required handoffs exist and consumed schemas match;
- solve worker ran through the expected runner/profile;
- AEDT solve/export manifests exist;
- artifact files exist and hashes match when available;
- scoring evidence is bound to the solve artifacts it claims to score;
- raw S-parameters and raw TDR are artifact-only;
- differential scoring uses `s4p`, `SDD11`, `SDD21`, and the declared TDR
  observation port;
- geometry edits are recorded in manifests and satisfy constraints;
- optimization history CSV is present and includes incomplete rounds;
- final report contains TDR, SDD11, and SDD21 plots or explicit missing-artifact
  reasons.

## Target Reviewed BRD Optimization Pipeline

For the current reviewed AEDT model entry point, the target graph should be
expressed as separate nodes:

```text
prepare_working_project
  -> solve_aedt
  -> export_touchstone
  -> export_tdr
  -> score_channel
  -> decide_next_action
  -> constraint_gate
  -> action_approval_gate
  -> apply_geometry_edit
  -> qualify_iteration
  -> update_history
  -> loop_to_solve or final_report
```

The current implementation may combine some of these operations, but that is
only transitional. The graph must expose these boundaries because they are the
engineering control points.

## Target Worker Capabilities

| Capability | Kind | Single responsibility | Output |
| --- | --- | --- | --- |
| `brd.project.prepare_working_copy` | worker/program | Copy reviewed source AEDT bundle once into controlled working path | project manifest |
| `brd.local_cut.solve` | worker | Open AEDT, run setup/sweep, wait for solve | solve manifest |
| `brd.touchstone.export` | worker | Export differential `channel.s4p` | touchstone artifact + manifest |
| `brd.tdr.export` | worker | Export TDR CSV/report for declared port such as `Diff1` | TDR artifact + manifest |
| `brd.channel.score` | worker | Read artifacts and emit bounded SDD11/SDD21/TDR evidence | score evidence JSON |
| `brd.action.propose` | agent | Choose one bounded candidate or request review | proposal handoff |
| `brd.geometry.validate` | program | Validate layer, shape, center, radius, constraints | validation result |
| `brd.model.edit` | worker | Apply exactly the approved edit to the working AEDT model | edit manifest |
| `brd.iteration.qualify` | scorecard | Audit one loop iteration from DB/artifacts | qualifier report |
| `brd.history.update` | program | Append row to optimization history CSV | CSV path + row |
| `brd.report.final` | program | Produce final HTML/JSON report with plots and history | report refs |

## Handoff Contracts

All graph boundaries must be structured. The following contracts are required
for the reviewed BRD loop.

### `solve_request`

Required fields:

- `project_path`
- `setup_name`
- `sweep_name`
- `expected_port_count`
- `touchstone_name`
- `tdr_observation_port`
- `loop_context`

### `solve_result`

Required fields:

- `status`
- `project_path`
- `solve_manifest`
- `artifact_dir`
- `loop_context`

The solve result should not pretend export has happened unless the worker has
actually produced export artifacts.

### `touchstone_export_result`

Required fields:

- `status`
- `touchstone_path`
- `touchstone_kind`
- `sparameter_mode`
- `return_loss_trace`
- `insertion_loss_trace`
- `export_manifest`
- `loop_context`

For differential reviewed models, expected values are `touchstone_kind=s4p`,
`return_loss_trace=SDD11`, and `insertion_loss_trace=SDD21`.

### `tdr_export_result`

Required fields:

- `status`
- `tdr_path`
- `tdr_expression`
- `tdr_observation_port`
- `tdr_report_name`
- `tdr_export_manifest`
- `loop_context`

### `score_result`

Required fields:

- `status`
- `score`
- `evidence_summary`
- `evidence_artifact`
- `artifact_refs`
- `loop_context`

Bounded evidence must include:

- worst `SDD11` in the target band;
- worst `SDD21` in the target band;
- TDR peak/valley deviation from target impedance;
- TDR proximity and flatness metrics;
- RL violation sum/max/count;
- `optimization_objective.total_cost`;
- pass/fail reason;
- artifact refs for raw Touchstone, raw TDR, and plots.

### `model_edit_request`

Required fields:

- `project_path`
- `project_copy_mode`
- `actions`
- `loop_context`

Each action must be bounded and reversible. For this first optimization pass:

- `anti_pad.enlarge` must not exceed radius `22mil`
  (`max_diameter=44mil`);
- `non_functional_pad.add_or_enlarge` must use radius from `7.875mil` to
  `10mil` (`min_diameter=15.75mil`, `max_diameter=20mil`);
- anti-pad edits must identify explicit layers, selected plane shape IDs,
  parasitic target, center source, and center padstack instance IDs or
  reviewed coordinates;
- non-functional pads must be explicit signal-net circle shapes, not default
  padstack edits.

### `iteration_qualification`

Required fields:

- `status`
- `checks`
- `score_evidence_artifact`
- `solve_manifest`
- `edit_manifest` when an edit occurred
- `history_row`
- `loop_context`

### `final_report`

Required fields:

- `status`
- `optimization_history_csv`
- `report_html`
- `report_json`
- `final_score`
- `checks`
- `artifact_refs`

## LLM Profile Policy

Not every node needs an expensive model.

- `low_cost`: formatting, extraction, simple triage, report wording.
- `standard`: planner and routine decision support.
- `high_reasoning`: optimization decider, failure takeover, ambiguous physical
  interpretation, and approval recommendation.

Workers should not consume LLM tokens unless their job is explicitly an agent
node.

## Web Visibility Requirements

The web view should show the graph, not just a running process.

Minimum display:

- current GraphRun status;
- active node and recent NodeRuns;
- pending approvals;
- latest solve/export/score/edit artifacts;
- optimization history rows;
- final or in-progress report link;
- failure reason with node ID and error class.

## Acceptance Criteria

The architecture is considered implemented when:

1. A reviewed BRD optimization run can be started from a YAML graph template
   without hardcoding the pipeline in a Python loop.
2. The external orchestrator can launch, advance, inspect, approve, and take
   over the graph through CLI/API.
3. Each AEDT-related step is represented as a separate worker or program node
   with a single responsibility.
4. `brd.local_cut.solve`, Touchstone export, TDR export, scoring, model edit,
   qualifier, history update, and final report each produce auditable records.
5. Scorecard can fail the graph if required artifacts, handoffs, metrics, or
   constraints are missing.
6. Long AEDT solves do not cause aggressive polling; the default loop/status
   poll interval is configurable and starts at 30 seconds.
7. The final report contains optimization history and TDR/SDD11/SDD21 plot refs
   or explicit reasons for missing plots.
8. The implementation still supports switching execution profiles between
   `local_cli` and `ssh_remote` without changing the YAML graph topology.

## Explicit Non-Goals

- Do not build a general autonomous optimizer with unbounded actions.
- Do not let the LLM write arbitrary AEDT automation code for production BRD
  edits.
- Do not optimize route traces in the first BRD loop.
- Do not remove human review from first model construction.
- Do not require VLM as a core dependency.
- Do not make AEDT gRPC cross-machine connectivity a production requirement.

## Migration Note

`docs/agent_templates/brd_reviewed_model_optimize_loop.yaml` is close to the
target direction, but current program handlers still carry too much loop logic.
The next implementation phase should split that logic into the worker and
qualifier boundaries named in this spec.
