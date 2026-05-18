# Stage C.2 LLM Planner Repair Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional LLM workflow planner mode with validation-driven repair loop, expose attempts in the demo API/UI, and generate a small planner benchmark report.

**Architecture:** Keep the existing deterministic planner as the default. Add `aedt_agent.demo.planner` as a narrow boundary where LLM clients can propose workflow JSON but cannot execute PyAEDT code. `DemoService.plan()` delegates to this runner, and the runner validates every attempt before returning. A separate benchmark script calls the same service API and renders a small HTML report.

**Tech Stack:** Python standard library, existing workflow validator/templates/catalog, pytest. Optional OpenAI-compatible HTTP client using `urllib`; no new dependency.

---

## Task 1: Planner runner and repair loop

**Files:**
- Create: `src/aedt_agent/demo/planner.py`
- Modify: `src/aedt_agent/demo/service.py`
- Modify: `src/aedt_agent/demo/config.py`
- Modify: `config/demo_config.example.json`
- Test: `tests/test_stage_c2_planner.py`

Steps:

- [ ] Write failing tests for deterministic output including `planner_mode`, `attempts`, and `repair_count`.
- [ ] Write failing tests for LLM fallback when API key/client is missing.
- [ ] Write failing tests for LLM repair loop using a fake client that returns an invalid workflow first and a valid workflow second.
- [ ] Implement `PlannerRunner`, `PlannerAttempt`, `OpenAICompatibleWorkflowClient`, and config field `max_repair_attempts`.
- [ ] Run `tests/test_stage_c2_planner.py` until green.

## Task 2: API and Web visibility

**Files:**
- Modify: `src/aedt_agent/demo/web.py`
- Test: `tests/test_stage_c1_demo_web.py`

Steps:

- [ ] Add failing test that `render_demo_page()` contains planner mode and repair attempt text.
- [ ] Update web page to include mode selector, repair-loop language, and show plan attempt JSON in the status panel.
- [ ] Run `tests/test_stage_c1_demo_web.py` until green.

## Task 3: Planner benchmark report

**Files:**
- Create: `src/aedt_agent/demo/planner_benchmark.py`
- Create: `scripts/run_stage_c2_planner_benchmark.py`
- Create: `tests/test_stage_c2_planner_benchmark.py`

Steps:

- [ ] Write failing test that benchmark runs 5 built-in requests and writes HTML/JSON.
- [ ] Implement benchmark runner with requests for microstrip, wave port, radiation airbox, setup-only, and ambiguous request.
- [ ] Render HTML showing success rate, average repair attempts, and per-task result.
- [ ] Run benchmark tests until green.

## Task 4: Docs, verification, and push

**Files:**
- Modify: `README.md`
- Modify: `docs/stage-c1-demo-readme.md`

Steps:

- [ ] Document planner modes, local config, and benchmark command.
- [ ] Run focused Stage C.1/C.2 tests.
- [ ] Run full pytest.
- [ ] Run whitespace and secret scans.
- [ ] Commit and push to `stage-a-grounding-benchmark`.
