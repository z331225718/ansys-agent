# Dipole Resonance Tuning Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Stage C demo loop where the agent tunes dipole arm length from S11 resonance feedback.

**Architecture:** Add a focused tuning module for resonance extraction, length update, and synthetic fake sweep generation. Wire it through `DemoService` and a new `/api/tune-dipole` endpoint, then add a visible Web control and iteration log.

**Tech Stack:** Python stdlib, existing `DemoService`, existing Web HTML/JS, pytest.

---

### Task 1: Tuning Core

**Files:**
- Create: `src/aedt_agent/demo/tuning.py`
- Test: `tests/test_dipole_tuning.py`

- [ ] Write failing tests for resonance extraction and length update.
- [ ] Implement `find_s11_resonance`, `next_dipole_arm_length`, `run_fake_dipole_tuning`.
- [ ] Run `pytest tests/test_dipole_tuning.py -q`.

### Task 2: Demo Service/API

**Files:**
- Modify: `src/aedt_agent/demo/service.py`
- Modify: `src/aedt_agent/demo/web.py`
- Test: `tests/test_stage_c1_demo_service.py`
- Test: `tests/test_stage_c1_demo_web.py`

- [ ] Write failing service and dispatch tests for `tune_dipole`.
- [ ] Add `DemoService.tune_dipole`.
- [ ] Add `/api/tune-dipole` dispatch route.
- [ ] Run targeted tests.

### Task 3: Web Demo UX

**Files:**
- Modify: `src/aedt_agent/demo/web.py`
- Test: `tests/test_stage_c1_demo_web.py`

- [ ] Add “Tune Resonance” button for dipole workflow.
- [ ] Render target frequency, each tuning round, resonance frequency, error, arm length, and S11 curve.
- [ ] Keep existing microstrip path unchanged.

### Task 4: Verification and Commit

- [ ] Run targeted tests.
- [ ] Run full pytest suite.
- [ ] Commit and push the implementation.
