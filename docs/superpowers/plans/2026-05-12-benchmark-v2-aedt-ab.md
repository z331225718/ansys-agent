# Benchmark V2 AEDT A/B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Stage A benchmark's primary judgment with a 10-task A/B comparison using local AEDT execution, validation scripts, and up to three repair attempts.

**Architecture:** Add a v2 benchmark path beside the existing offline runner. V2 selects a fixed 10-task set, runs groups A and B only, records every attempt under `benchmarks/runs/<run_id>/`, and renders a report based on execution and validation outcomes. Group B must retrieve official knowledge before generation.

**Tech Stack:** Python stdlib, PyAEDT via subprocess execution, existing OpenAI-compatible generator, existing task YAML and validation scripts.

---

### Task 1: V2 Data Model And Task Selection

**Files:**
- Create: `src/aedt_agent/benchmark/v2_models.py`
- Create: `src/aedt_agent/benchmark/task_sets.py`
- Test: `tests/test_benchmark_v2.py`

- [ ] **Step 1: Write failing tests for 10-task selection and attempt metrics.**
- [ ] **Step 2: Implement dataclasses for attempt, group, task, and report summaries.**
- [ ] **Step 3: Implement fixed Stage A v2 task IDs with 4 L1, 4 L2, and 2 Trap tasks.**
- [ ] **Step 4: Run `python3 -m pytest tests/test_benchmark_v2.py -q`.**

### Task 2: Executor And Validation Boundary

**Files:**
- Create: `src/aedt_agent/benchmark/aedt_executor.py`
- Test: `tests/test_aedt_executor.py`

- [ ] **Step 1: Write tests for subprocess command construction and result parsing using a fake subprocess runner.**
- [ ] **Step 2: Implement `AEDTSubprocessExecutor` that writes a wrapper and executes generated code plus validation.**
- [ ] **Step 3: Keep AEDT execution opt-in in tests by using fakes.**

### Task 3: A/B Attempt Loop

**Files:**
- Create: `src/aedt_agent/benchmark/runner_v2.py`
- Modify: `src/aedt_agent/benchmark/prompt_templates.py`
- Test: `tests/test_runner_v2.py`

- [ ] **Step 1: Write failing tests proving max 3 attempts, repair prompt includes prior log, and Group B retrieves before generation.**
- [ ] **Step 2: Implement attempt loop with per-attempt artifacts.**
- [ ] **Step 3: Compute `first_pass_rate`, `pass_rate_3try`, `avg_attempts_to_success`, and failure categories.**

### Task 4: Script And HTML Report

**Files:**
- Modify: `scripts/run_stage_a_benchmark.py`
- Create: `src/aedt_agent/benchmark/report_html_v2.py`
- Test: `tests/test_run_stage_a_script.py`
- Test: `tests/test_report_html_v2.py`

- [ ] **Step 1: Make the script run v2 by default with `--max-attempts 3`.**
- [ ] **Step 2: Render A/B metrics and attempt summaries; remove Group C and node readiness from the main report.**
- [ ] **Step 3: Keep progress output in terminal.**

### Task 5: Verification

**Files:**
- Modify as needed based on failing tests.

- [ ] **Step 1: Run targeted v2 tests.**
- [ ] **Step 2: Run `python3 -m pytest -q`.**
- [ ] **Step 3: Do not run real AEDT automatically during unit tests.**

