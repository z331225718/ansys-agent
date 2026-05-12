# Harness CLI Agent Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace prompt-only A/B generation with a local harness CLI benchmark where Group A runs without tool access and Group B runs with GitNexus/PyAEDT tooling enabled.

**Architecture:** Keep the existing AEDT non-graphical executor and validation loop. Replace the current OpenAI-only generator path with a `HarnessGenerator` that invokes a local agent CLI such as Claude Code or Codex, captures transcripts, extracts generated Python code, and records tool usage. A and B differ only by harness configuration: A has no MCP/tools; B has GitNexus MCP plus read-only official PyAEDT/examples context.

**Tech Stack:** Python stdlib, local harness CLI (`claude` or `codex`), GitNexus MCP/eval-server, PyAEDT AEDT 2026.1 non-graphical execution, existing benchmark runner/reporting.

---

## Rationale

The current B group injects precomputed evidence into a cloud LLM prompt. That measures RAG-style prompt augmentation, not agentic tool use. The benchmark goal is stronger: evaluate whether a coding harness with access to PyAEDT graph/tools can discover the right APIs, repair errors, and produce AEDT-valid scripts more reliably than the same harness without tools.

This means retrieval must happen inside the harness execution, not in our benchmark runner. The runner should provide the task and judge the result; the harness should decide whether and how to query GitNexus.

## Final A/B Definition

Group A:
- Same harness CLI and model as Group B.
- No MCP tools.
- No PyAEDT repo access beyond the task prompt.
- Receives only task requirement, prior generated code, and prior AEDT error log during repair attempts.

Group B:
- Same harness CLI and model as Group A.
- GitNexus MCP enabled.
- Read-only access to `/home/zzmjay/code/pyaedt`.
- Read-only access to `/home/zzmjay/code/pyaedt-examples`.
- Prompt requires using GitNexus/PyAEDT official context before writing code.
- Repair attempts must use the prior AEDT error log to query tools again.

Both groups:
- Same 10 task set.
- Same max attempts, default 3.
- Same AEDT non-graphical executor.
- Same validation scripts.
- Same pass/fail rule: `execution_ok and validation_ok`.

## Expected Run Layout

```text
benchmarks/runs/<run_id>/<task_id>/<group>/
  attempt_1_prompt.txt
  attempt_1_harness_stdout.txt
  attempt_1_harness_stderr.txt
  attempt_1_transcript.txt
  attempt_1_code.py
  attempt_1_exec.log
  attempt_1_validation.log
  attempt_1_tool_usage.json
  attempt_2_...
  summary.json
```

`tool_usage.json` should be best-effort. For Claude Code/Codex, if a structured trace is unavailable, parse raw transcript for GitNexus/MCP/tool-call markers and store:

```json
{
  "used_tools": true,
  "gitnexus_query_count": 2,
  "gitnexus_context_count": 1,
  "tool_call_names": ["gitnexus.query", "gitnexus.context"],
  "retrieval_before_code": true
}
```

## Metrics

Task/group metrics:
- `final_pass`
- `success_on_attempt`
- `attempt_count`
- `failure_type`
- `execution_ok`
- `validation_ok`
- `tool_usage`

Group metrics:
- `first_pass_rate`
- `pass_rate_3try`
- `avg_attempts_to_success`
- `avg_attempts_all`
- `failure_categories`
- `tool_usage_rate`
- `avg_gitnexus_queries`
- `retrieval_before_code_rate`

## Prompt Contract

Harness prompt common constraints:

```text
Generate only Python code for the benchmark harness.
Use the existing `app` object.
Do not import pyaedt, ansys.aedt.core, Hfss, or Desktop.
Do not create or release a Desktop session.
Do not wrap code in markdown fences.
```

Group A extra:

```text
You do not have access to external tools or official documentation. Use only the task description and prior error log.
```

Group B extra:

```text
Before writing code, use the available GitNexus/PyAEDT tools to inspect the official PyAEDT API and examples.
Prefer GitNexus query/context results and official examples over memory.
If this is a repair attempt, first investigate the AEDT/PyAEDT error log with the tools, then revise the code.
```

## Implementation Tasks

### Task 1: Harness Generator Interface

**Files:**
- Create: `src/aedt_agent/benchmark/harness_generator.py`
- Test: `tests/test_harness_generator.py`

- [x] Write tests for invoking a fake CLI command and capturing stdout/stderr/transcript.
- [x] Implement `HarnessGenerator.generate()` with timeout, cwd, env, and transcript paths.
- [x] Implement robust code extraction from raw harness output:
  - fenced Python block if present
  - otherwise full stdout after trimming harness metadata
  - fail with `generation_error` if no plausible code exists

### Task 2: Harness Group Configuration

**Files:**
- Modify: `src/aedt_agent/benchmark/config.py`
- Modify: `config/benchmark_config.json`
- Create: `config/harness/group_a.json`
- Create: `config/harness/group_b.json`
- Test: `tests/test_config.py`

- [x] Add `harness` config section:

```json
{
  "harness": {
    "backend": "codex",
    "command": "codex",
    "timeout": 600,
    "group_a_config": "config/harness/group_a.json",
    "group_b_config": "config/harness/group_b.json",
    "work_dir": "benchmarks/harness_work"
  }
}
```

- [x] Keep API-key-based generator available, but make harness backend selectable.
- [x] Ensure public configs contain no secrets.

### Task 3: Runner Integration

**Files:**
- Modify: `src/aedt_agent/benchmark/runner_v2.py`
- Test: `tests/test_runner_v2.py`

- [x] Replace runner-side B retrieval with group-specific generator configuration.
- [x] Pass `group`, `attempt`, `task_id`, `previous_code`, and `previous_log` into `HarnessGenerator`.
- [x] Store harness stdout/stderr/transcript/tool usage per attempt.
- [x] Preserve existing stop rule: stop after first `final_pass`.

### Task 4: Tool Usage Extraction

**Files:**
- Create: `src/aedt_agent/benchmark/tool_usage.py`
- Test: `tests/test_tool_usage.py`

- [x] Parse transcript text for GitNexus and MCP usage.
- [x] Detect whether retrieval happened before code emission.
- [x] Store best-effort tool usage metrics even when transcript is unstructured.

### Task 5: HTML Report Update

**Files:**
- Modify: `src/aedt_agent/benchmark/report_html_v2.py`
- Test: `tests/test_report_html_v2.py`

- [x] Rename report wording from RAG/evidence to harness/tool benchmark.
- [x] Add A/B explanation:
  - A: no tools
  - B: GitNexus MCP + official repos
- [x] Add tool usage metrics to Group B.
- [x] Link per-attempt transcript/log/code artifacts.

### Task 6: One-Task PoC

**Files:**
- Add script option in `scripts/run_stage_a_benchmark.py`

- [x] Add CLI options:

```bash
--task L1_create_wave_port
--groups A B
--max-attempts 3
```

- [ ] Run only `L1_create_wave_port` first.
- [ ] Verify B actually uses GitNexus before code generation.
- [ ] Verify AEDT execution and validation are the final judge.

### Task 7: Three-Task Smoke

**Files:**
- No new files unless bugs are found.

- [ ] Run:

```bash
gitnexus eval-server -p 4848 --idle-timeout 0
```

- [ ] Run 3 tasks:
  - `L1_create_substrate`
  - `L1_create_wave_port`
  - `L2_microstrip_line`

- [ ] Inspect report and transcripts.
- [ ] Only after this passes, expand to the fixed 10-task benchmark.

## Open Decisions

1. Primary harness CLI:
   - Prefer `codex` if it supports deterministic non-interactive execution and local config selection.
   - Use `claude` if Claude Code MCP/tool configuration is easier to isolate per group.

2. Tool isolation:
   - Group A must not accidentally inherit global MCP config.
   - If the harness cannot disable global tools, create temporary isolated config/home directories per group.

3. Transcript format:
   - Prefer structured JSON logs if the harness supports them.
   - Otherwise parse raw stdout/stderr.

## Success Criteria

- A and B use the same harness/model.
- A cannot call GitNexus or inspect repos.
- B can call GitNexus MCP or equivalent local graph tools.
- Every attempt stores prompt, transcript, code, AEDT log, validation log.
- Success stops further attempts.
- HTML report clearly shows first-pass rate, 3-attempt pass rate, average attempts, and B tool usage.
