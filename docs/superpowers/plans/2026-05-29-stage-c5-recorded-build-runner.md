# Stage C.5 Recorded Build Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Stage C.5 build-only runner that turns a recorded AEDT workflow analysis into a structured action plan, reuses the existing PyEDT/PyEDB BRD model-build path, and records raw fallback actions for void adjustments without running a large solve.

**Architecture:** Keep the recorded AEDT script as evidence, not executable product code. Reuse `run_real_import_cutout()` for import/cutout/stackup/ports/setup because it already prefers PyEDB and PyAEDT wrappers. Add a small layout optimization action layer that translates extracted recorded facts into explicit action records, and isolate raw AEDT void commands behind one fallback module for later real execution.

**Tech Stack:** Python 3.12, dataclasses, pathlib, JSON, pytest, existing Stage C BRD model-build scripts.

---

## Files

- Create `src/aedt_agent/layout/optimization_actions.py`  
  Action schema and action-plan builder from recorded workflow analysis.

- Create `src/aedt_agent/layout/void_fallback.py`  
  Isolated raw AEDT fallback payload builder for circle/rectangle void adjustments. This module does not execute AEDT in this task.

- Create `scripts/run_stage_c5_recorded_build.py`  
  CLI that reads params and recorded analysis, runs existing import/cutout build-only path, writes `stage_c5_action_plan.json` and `stage_c5_build_summary.json`.

- Create `tests/test_stage_c5_recorded_build_runner.py`  
  TDD tests for action schema, raw fallback isolation, and CLI behavior using fake adapter.

- Modify `docs/superpowers/specs/2026-05-27-brd-via-optimization-agent-design.md`  
  Add Stage C.5 build-only command and state that analyze remains disabled for this acceptance step.

## Task 1: Action Schema

- [x] Write a failing test that calls `build_recorded_optimization_action_plan()` with a compact recorded analysis containing `r_cut_L3=15mil`, ART03 circle/rectangle voids, setup/sweep metadata, and expects:
  - plan status `ready`
  - action `build_layout_model` using `pyedb_hfss3dlayout_build`
  - action `apply_layout_void_adjustment` using `raw_aedt_void_fallback`
  - action `save_project` using `Hfss3dLayout.save_project`
  - no `solve_layout_channel` action when `solve_enabled=False`
- [x] Run the focused test and verify it fails because `aedt_agent.layout.optimization_actions` does not exist.
- [x] Implement dataclass-free JSON-safe action plan builder in `src/aedt_agent/layout/optimization_actions.py`.
- [x] Run the focused test and verify it passes.

## Task 2: Raw Void Fallback Isolation

- [x] Write a failing test for `build_void_fallback_payload()` that expects ART03 circle/rectangle operations to be represented as data:
  - `api` values `oEditor.CreateCircleVoid` and `oEditor.CreateRectangleVoid`
  - `variable` value `r_cut_L3`
  - no direct execution method or AEDT app requirement
- [x] Run the focused test and verify it fails because `aedt_agent.layout.void_fallback` does not exist.
- [x] Implement `src/aedt_agent/layout/void_fallback.py` as a pure payload builder.
- [x] Run the focused test and verify it passes.

## Task 3: Build-Only CLI

- [x] Write a failing CLI test that:
  - writes a fake params JSON with `layout_file`, `signal_nets`, `reference_nets`, `adapter=fake`
  - writes a recorded analysis JSON
  - runs `scripts/run_stage_c5_recorded_build.py --adapter fake --params ... --recorded-analysis ... --run-dir ...`
  - expects `stage_c5_action_plan.json`, `stage_c5_build_summary.json`, and `import_cutout_summary.json`
  - expects `layout_solve.status == "skipped"`
- [x] Run the focused CLI test and verify it fails because the script does not exist.
- [x] Implement the CLI by reusing `build_import_cutout_request()`, `run_fake_import_cutout()`, and `run_real_import_cutout()`.
- [x] Run the focused CLI test and verify it passes.

## Task 4: Docs and Verification

- [x] Add the Stage C.5 build-only command to the optimization spec:

```bash
.venv/bin/python scripts/run_stage_c5_recorded_build.py \
  --adapter real \
  --params D:/runs/stage_c5_params.json \
  --recorded-analysis D:/runs/recorded_workflow_analysis.json \
  --run-dir D:/runs/stage_c5_build \
  --graphical
```

- [x] Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c5_recorded_build_runner.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_contract_stabilization.py
git diff --check
```

- [x] Commit:

```bash
git add \
  src/aedt_agent/layout/optimization_actions.py \
  src/aedt_agent/layout/void_fallback.py \
  scripts/run_stage_c5_recorded_build.py \
  tests/test_stage_c5_recorded_build_runner.py \
  docs/superpowers/specs/2026-05-27-brd-via-optimization-agent-design.md \
  docs/superpowers/plans/2026-05-29-stage-c5-recorded-build-runner.md

git commit -m "feat: add Stage C5 recorded build runner"
```

## Done Criteria

- Stage C.5 can produce a build-only run directory from params plus recorded analysis.
- Existing PyEDB/Hfss3dLayout model-build logic is reused instead of duplicating recorded low-level commands.
- Void adjustment operations are represented as explicit fallback payloads, ready for the next real AEDT execution layer.
- The runner does not analyze large BRD projects by default.
