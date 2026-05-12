# Stage A Online LLM Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Stage A so A/B/C benchmark runs can generate candidate code through an OpenAI-compatible API before applying the existing offline graders.

**Architecture:** Keep the current offline grading pipeline intact and add one generation phase in front of it. `generator.py` becomes the OpenAI-compatible backend boundary, `runner.py` becomes responsible for optionally generating and persisting A/B/C candidate files, and `cli.py` exposes flags for online generation versus replay.

**Tech Stack:** Python 3.12, standard library `urllib`/`json`, pytest, existing Stage A knowledge/context pipeline.

---

### Task 1: Add failing tests for online generation path

**Files:**
- Modify: `tests/test_generator.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1: Write failing OpenAI-compatible generator test**
- [ ] **Step 2: Run focused generator test and verify failure**
- [ ] **Step 3: Write failing runner generation test**
- [ ] **Step 4: Run focused runner test and verify failure**

### Task 2: Implement OpenAI-compatible generator

**Files:**
- Modify: `src/aedt_agent/benchmark/generator.py`

- [ ] **Step 1: Add HTTP-backed OpenAI-compatible generator**
- [ ] **Step 2: Support env-driven base URL, key, model, timeout**
- [ ] **Step 3: Keep file/default backends working**
- [ ] **Step 4: Run generator tests**

### Task 3: Thread online generation through runner and CLI

**Files:**
- Modify: `src/aedt_agent/benchmark/runner.py`
- Modify: `src/aedt_agent/benchmark/context_builder.py`
- Modify: `src/aedt_agent/cli.py`

- [ ] **Step 1: Build prompt context per group**
- [ ] **Step 2: Generate and persist candidate code when requested**
- [ ] **Step 3: Record model/backend metadata in report**
- [ ] **Step 4: Expose CLI flags for db path and generate mode**
- [ ] **Step 5: Run runner tests**

### Task 4: Verify end-to-end and publish

**Files:**
- Modify: `benchmarks/reports/stage_a_sample_report.json`

- [ ] **Step 1: Run full pytest suite**
- [ ] **Step 2: Rebuild sample report**
- [ ] **Step 3: Check `go_nogo` and generation metadata**
- [ ] **Step 4: Publish repository with `gh`**
