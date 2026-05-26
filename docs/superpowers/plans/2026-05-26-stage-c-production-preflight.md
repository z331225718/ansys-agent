# Stage C Production Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production preflight command that checks whether Stage C demo workflows can run on an internal Windows/Linux AEDT machine before launching a long BRD/MCM job.

**Architecture:** Add a small `aedt_agent.demo.preflight` module that reads the existing demo config shape, checks filesystem inputs and AEDT/Cadence environment assumptions without launching AEDT by default, and returns structured check results. Add a script wrapper for terminal/CI usage and document the intended production checklist.

**Tech Stack:** Python 3.12, pathlib, argparse, pytest, existing `aedt_agent.demo.config`.

---

## File Structure

- Create `src/aedt_agent/demo/preflight.py`: pure preflight check functions and JSON-serializable result model.
- Create `scripts/check_stage_c_demo_environment.py`: CLI wrapper around the preflight module.
- Create `tests/test_stage_c_demo_preflight.py`: focused tests for blank defaults, explicit path failures, env reuse, and input-file checks.
- Modify `docs/stage-c1-demo-readme.md`: add the production preflight command and expected workflow.

## Task 1: Preflight Result Model and Checks

- [ ] Write tests showing blank AEDT roots are allowed when matching versioned env vars exist.
- [ ] Write tests showing explicit missing `ansysem_root`, `awp_root`, `layout_file`, and `stackup_xml` produce failed checks.
- [ ] Implement `run_stage_c_preflight()` returning `ok`, `checks`, and `summary`.
- [ ] Verify with `pytest tests/test_stage_c_demo_preflight.py -q`.

## Task 2: CLI Wrapper

- [ ] Write a script test that runs `scripts/check_stage_c_demo_environment.py` against a temporary config and params JSON.
- [ ] Implement CLI flags:
  - `--config`
  - `--local-config`
  - `--params`
  - `--json`
  - `--strict`
- [ ] Make non-strict mode report warnings for missing optional AEDT env but fail for invalid explicit paths or required input files.
- [ ] Verify with the script test.

## Task 3: Documentation and Full Verification

- [ ] Document the Windows/Linux production preflight flow in `docs/stage-c1-demo-readme.md`.
- [ ] Run focused tests, full pytest, contract check, and `git diff --check`.
- [ ] Commit and push the completed change.
