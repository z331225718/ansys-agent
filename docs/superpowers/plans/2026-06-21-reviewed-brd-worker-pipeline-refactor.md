# Reviewed BRD Worker Pipeline Refactor Plan

**Status:** draft
**Date:** 2026-06-21
**Spec:** `docs/superpowers/specs/2026-06-21-orchestrator-worker-pipeline-architecture.md`

## Goal

Refactor the reviewed BRD optimization vertical slice into the intended
orchestrator + YAML graph + many single-purpose workers architecture.

The end state is not a thick `run-loop` command. The end state is a graph that
an external orchestrator can operate:

```text
Claude Code / Codex / Pi
  -> create graph from YAML
  -> advance/poll/status/approve/takeover
  -> read scorecard and report artifacts
```

## Current State

Already available:

- `docs/agent_templates/brd_reviewed_model_optimize_loop.yaml`
- `src/aedt_agent/agent/graph_runner.py`
- `src/aedt_agent/agent/graph_executors.py`
- `src/aedt_agent/agent/loop_runner.py`
- `src/aedt_agent/agent/optimization_handlers.py`
- worker capabilities:
  - `brd.local_cut.solve`
  - `brd.channel.score`
  - `brd.model.edit`
- execution profiles:
  - `local_cli`
  - `ssh_remote`
- reviewed-model config:
  - `config/optimization_loops/reviewed_brd_remote.example.json`
- report artifacts:
  - `optimization_history.csv`
  - `optimization_progress.html`
  - `optimization_progress.json`

Known architectural debt:

- `run-loop` is convenient but looks like the orchestrator itself;
- `optimization_handlers.py` combines preparation, decision, history, and
  report glue;
- solve/export boundaries are not explicit enough;
- scorecard is present but not yet the mandatory qualifier for every
  iteration;
- the final graph is not yet visibly the `analyze -> worker -> review ->
  qualifier -> loop/commit` style pipeline the project wants.

## Phase 1: Document And Freeze The Contract

Deliverables:

- Add the architecture spec.
- Keep the BRD engineering playbook as the domain memory.
- Update `CLAUDE.md` / `AGENTS.md` later only after the graph refactor is in
  code, so orchestrators are not told to rely on behavior that does not exist
  yet.

Acceptance:

- The spec states that YAML is the core interface.
- The spec states that workers are single-purpose.
- The spec states that scorecard/qualifier is programmatic audit.
- The spec states that `run-loop` must remain a wrapper, not the architecture.

## Phase 2: Split Solve And Export Boundaries

Problem:

The current solve worker can make the loop look like `solve` already implies
all result products. For a real engineering pipeline, solve, Touchstone export,
and TDR export are separate control points.

Implementation tasks:

1. Add or expose worker capability `brd.touchstone.export`.
2. Add or expose worker capability `brd.tdr.export`.
3. Update handoff schemas:
   - `solve_request`
   - `solve_result`
   - `touchstone_export_result`
   - `tdr_export_result`
4. Update the reviewed graph:

   ```text
   real_solve_worker
     -> touchstone_export_worker
     -> tdr_export_worker
     -> channel_score_worker
   ```

5. If the current `brd.local_cut.solve` already exports artifacts internally,
   keep compatibility by letting it return explicit sub-artifact manifests, but
   the graph should still model export as separate nodes as soon as practical.

Acceptance:

- A graph status page can show whether the run is solving, exporting S4P,
  exporting TDR, or scoring.
- Missing TDR export produces a partial history row such as
  `needs_tdr_export_before_score`.
- Score worker does not run unless required export artifacts exist or the graph
  explicitly records a degraded scoring mode.

## Phase 3: Make The Decider An Agent Node With Bounded Inputs

Problem:

`decide_next_action` currently contains deterministic selection plus optional
LLM selection. That is useful, but the architecture should show the decider as
an agent node with a strict handoff contract.

Implementation tasks:

1. Move decider prompt and constraints into YAML.
2. Keep a deterministic fallback handler only for tests and offline mode.
3. Require decider input to be bounded evidence plus candidate actions, not raw
   curves.
4. Require decider output:
   - `decision`
   - `reason`
   - `selected_action` when continuing
   - `tdr_observation_port`
   - `tdr_port_orientation_evidence`
   - `constraints_checked`
   - `risk`
   - `rollback`
5. Add explicit edge outcomes:
   - `continue`
   - `complete`
   - `approval_required`
   - `failed`

Acceptance:

- The YAML graph identifies the optimization decider as an agent node.
- The decider cannot return an action outside allowed decisions.
- An action without required TDR/geometry evidence routes to human review or
  fails validation before edit.

## Phase 4: Add Geometry Validation Before Model Edit

Problem:

The model edit worker should execute, not decide whether a proposal is valid.

Implementation tasks:

1. Add program node `geometry_constraint_validator`.
2. Validate:
   - action type is allowed;
   - layer names are explicit;
   - anti-pad radius <= `22mil`;
   - non-functional pad radius in [`7.875mil`, `10mil`];
   - anti-pad action has plane shape IDs and parasitic target;
   - non-functional pad action uses explicit signal-net circle shapes;
   - TDR observation port and feature time/window exist for TDR-driven edits;
   - project path points to the working project, not the source model.
3. Route invalid proposals to approval/review instead of edit.

Acceptance:

- `brd.model.edit` receives only executable, validated proposals.
- Invalid proposals produce clear `geometry_validation_failed` evidence.
- Source AEDT project cannot be passed as the editable working project without
  explicit override.

## Phase 5: Add Iteration Qualifier As Mandatory Scorecard Node

Problem:

The scorecard must audit every loop, not only produce a final summary.

Implementation tasks:

1. Add `brd.iteration.qualify` program node after scoring and after editing.
2. Verify:
   - solve manifest exists;
   - Touchstone artifact exists and is `s4p`;
   - TDR artifact exists or missing state is explicit;
   - score evidence is bound to the same artifacts;
   - raw arrays are not inline;
   - edit manifest matches the selected action;
   - geometry constraints are recorded;
   - history row was appended.
3. Add failure edges:
   - transient artifact issue -> retry export/score;
   - schema/constraint issue -> human review;
   - unrecoverable issue -> graph failed.

Acceptance:

- A loop iteration cannot be considered successful until qualifier passes.
- Qualifier failure is visible in `graph-status`.
- Scorecard can be run after process restart using persisted records and
  artifacts.

## Phase 6: Make History And Report First-Class Nodes

Problem:

History and reports are user-facing progress artifacts. They should not be an
incidental side effect inside the decider.

Implementation tasks:

1. Add `optimization_history_update` node after score/qualifier.
2. Append one row per round, including partial rounds.
3. Add `final_report` node that requires:
   - optimization history CSV;
   - final bounded metrics;
   - accepted/rejected geometry changes;
   - TDR plot refs;
   - SDD11 plot refs;
   - SDD21 plot refs;
   - final recommendation.
4. Keep `optimization_progress.html/json` updated during the run.

Acceptance:

- The web page can show current progress without reading raw worker logs.
- Final report is a graph output, not just a local file produced in passing.
- Incomplete runs still show what happened and what is needed next.

## Phase 7: Keep `run-loop` As A Thin Wrapper

Problem:

The command should not hide orchestration logic.

Implementation tasks:

1. Make `mission run-loop` do only:
   - load loop config;
   - create mission;
   - create graph from YAML;
   - repeatedly call `advance_graph`;
   - sleep based on `poll_interval_seconds`;
   - return terminal graph status.
2. Ensure it does not contain node-specific engineering logic.
3. Prefer exposing examples that use:
   - `mission create` / `run-graph`;
   - `mission advance-graph`;
   - `mission graph-status`;
   - `mission approve`;
   - `mission takeover`.

Acceptance:

- Removing `run-loop` would not remove the actual graph pipeline.
- Claude Code can operate the same flow manually through graph commands.
- Long AEDT solves use 30-second or configured polling by default.

## Phase 8: Web Dashboard For Graph And Optimization History

Implementation tasks:

1. Show graph DAG and node states.
2. Show latest handoff payload summaries.
3. Show active/pending approval gates.
4. Show latest `optimization_history.csv` rows.
5. Link artifacts:
   - solve manifest;
   - S4P;
   - TDR CSV;
   - score evidence;
   - edit manifest;
   - TDR/SDD11/SDD21 plots;
   - final report.

Acceptance:

- A user can answer "what is it doing now?" from the web page.
- A user can answer "what changed and did it help?" from the history table.
- A user can decide whether to continue or stop without reading terminal logs.

## Phase 9: Remote Production Flow

Implementation tasks:

1. Keep execution profile switchable:
   - production machine: `local_cli`;
   - current LAN setup: `ssh_remote`.
2. Keep YAML topology unchanged across runner profiles.
3. Document the remote Claude Code operator flow:
   - pull main;
   - copy/edit local config;
   - start web dashboard;
   - tell Claude Code to start the graph;
   - monitor report/history.

Acceptance:

- Remote machine can pull `main`, configure paths/API keys, and run the graph.
- Changing runner profile does not require editing worker contracts.

## Suggested Target Graph

The next reviewed-model graph should look like this:

```yaml
id: brd_reviewed_model_worker_pipeline
version: 1
description: "Reviewed BRD optimization as single-purpose worker pipeline"

nodes:
  - id: prepare_working_project
    kind: worker
    capability: brd.project.prepare_working_copy

  - id: solve_aedt
    kind: worker
    capability: brd.local_cut.solve

  - id: export_touchstone
    kind: worker
    capability: brd.touchstone.export

  - id: export_tdr
    kind: worker
    capability: brd.tdr.export

  - id: score_channel
    kind: worker
    capability: brd.channel.score

  - id: decide_next_action
    kind: agent
    role: decision_maker
    profile: high_reasoning

  - id: geometry_constraint_validator
    kind: program
    role: validator

  - id: action_approval_gate
    kind: human_gate
    role: approval_gate

  - id: apply_geometry_edit
    kind: worker
    capability: brd.model.edit

  - id: qualify_iteration
    kind: program
    role: scorecard

  - id: update_history
    kind: program
    role: reporter

  - id: final_report
    kind: program
    role: reporter
```

This YAML is illustrative. The implementation should keep using the existing
loader/schema style and add the missing handoffs before turning it into a
checked-in executable template.

## Test Plan

Add or update tests for:

- YAML loader accepts the new graph and rejects missing handoff schemas.
- Graph runner executes the new pipeline with fake workers.
- Solve/export/score split creates distinct NodeRuns.
- Decider output is schema-validated and constrained.
- Geometry validator blocks invalid radius/layer/center proposals.
- Qualifier fails when artifacts are missing.
- History update writes one row per round, including partial failure rounds.
- `run-loop` wrapper returns the same terminal state as manual graph advance.
- Web API returns optimization history rows and report links.

## Milestone Order

1. Spec and plan committed.
2. Add fake-worker graph test for target topology.
3. Split export contracts.
4. Add geometry validator.
5. Add iteration qualifier.
6. Move history/report out of decider side effects.
7. Thin `run-loop`.
8. Update remote usage docs.
9. Run real reviewed BRD loop once on remote AEDT.

## Open Questions

- Should Touchstone and TDR export be separate worker capabilities immediately,
  or should the first refactor use wrapper nodes around the existing solve
  output?
- Should `brd.action.propose` be a dedicated capability name, or should the
  graph keep this as `kind: agent` with no worker capability?
- Should the web dashboard consume SQLite directly or only graph-status/report
  APIs?
- Should `stage-a-grounding-benchmark` remain as an old branch after `main`
  became default, or should it eventually be archived?
