# Stage C BRD Production Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Stage C 的 BRD/MCM experimental demo 从“能生成模型”推进到“可在生产环境验收、复盘、交接”的工作流，为后续真实 via TDR/S11 优化打基础。

**Architecture:** 继续保持 BRD/MCM 节点为 experimental、默认 model-build-only。新增一个 production acceptance layer：运行前保存 preflight 和输入快照，运行后汇总 workflow、cutout summary、端口计划、AEDT project、日志和可选 Touchstone/TDR，输出一个统一 `acceptance_report.json` 和中文 HTML 报告。这个阶段不做自动优化，不默认 analyze 大板工程。

**Tech Stack:** Python 3.12, pytest, existing `aedt_agent.demo.preflight`, `aedt_agent.demo.import_cutout`, `aedt_agent.layout.workflow_run`, JSON artifacts, static HTML report.

---

## Scope

本计划只做生产验收与可复盘能力：

- 保留当前 BRD/MCM workflow 的 model-build-only 默认行为。
- 不修改端口物理规则，除非是为了把已有端口策略写入报告。
- 不启动自动 via 优化循环。
- 不默认运行 heavy analyze。
- 支持以后接入真实生产全仿真：如果用户显式传入已有 Touchstone/TDR 或显式开启 solve，报告能够展示结果。

## File Structure

- Create `src/aedt_agent/layout/acceptance.py`  
  读取 preflight、workflow_run、import_cutout_summary、stdout/stderr log，生成结构化 acceptance summary。

- Create `src/aedt_agent/reporting/stage_c_brd_report.py`  
  将 acceptance summary 渲染为中文 HTML。重点解释：输入、环境、节点状态、端口策略、输出文件、风险项、下一步动作。

- Create `scripts/package_stage_c_brd_run.py`  
  对已有 run directory 做离线打包，不重新跑 AEDT。用于生产环境跑完后补生成报告。

- Create `scripts/run_stage_c_brd_acceptance.py`  
  一站式入口：读取 params/config，先跑 preflight，再调用现有 import-cutout 脚本或函数，最后打包报告。

- Create `tests/test_stage_c_brd_acceptance.py`  
  覆盖 acceptance summary 和 HTML 报告。

- Modify `docs/brd-experimental-workflow.md`  
  补充 acceptance artifact 约定。

- Modify `docs/stage-c1-demo-readme.md`  
  补充生产运行命令和报告查看方式。

## Acceptance Artifacts

每次 BRD/MCM production acceptance run 应至少输出：

- `preflight.json`：运行前环境检查结果。
- `params.json`：本次 BRD/MCM 输入参数快照。
- `workflow_run.json`：统一节点执行状态。
- `import_cutout_summary.json`：PyEDB/HFSS 3D Layout 原始摘要。
- `acceptance_report.json`：生产验收摘要。
- `acceptance_report.html`：中文汇报/交接报告。
- `stdout.log` 和 `stderr.log`：真实运行日志，失败时用于定位。

可选输出：

- `.aedb` cutout 目录。
- `.aedt` project。
- Touchstone `.s2p`。
- TDR `.csv`。

## Task 1: Acceptance Summary Model

**Files:**
- Create: `src/aedt_agent/layout/acceptance.py`
- Create: `tests/test_stage_c_brd_acceptance.py`

- [ ] **Step 1: Write failing tests for summary packaging**

Add tests that build a fake run directory containing:

```text
preflight.json
params.json
workflow_run.json
import_cutout_summary.json
stdout.log
stderr.log
```

Expected behavior:

- `build_brd_acceptance_summary(run_dir)` returns `status="succeeded"` when workflow status succeeded and no failed preflight checks exist.
- Summary includes `layout_file`, `signal_nets`, `reference_nets`, `aedt_project`, `edb_path`, `port_action_count`, `step_statuses`, `warnings`, and `artifacts`.
- Missing optional Touchstone/TDR is reported as `not_available`, not failure.
- Failed workflow or failed preflight produces `status="failed"` and `blocking_issues`.

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_brd_acceptance.py -q
```

Expected: fail because `aedt_agent.layout.acceptance` does not exist.

- [ ] **Step 3: Implement minimal summary builder**

Implement:

```python
def build_brd_acceptance_summary(run_dir: Path) -> dict[str, Any]:
    ...
```

Rules:

- Read JSON files only if they exist.
- Preserve original file paths as strings.
- Count port actions from `import_cutout_summary["port_action_plan"]["port_actions"]`.
- Extract step statuses from `workflow_run["steps"]`.
- Treat failed preflight checks and failed workflow steps as blocking.
- Treat warning preflight checks as warnings.
- Include all existing artifact file paths under `artifacts`.

- [ ] **Step 4: Verify summary tests pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_brd_acceptance.py -q
```

Expected: pass.

## Task 2: Offline Packaging Script

**Files:**
- Create: `scripts/package_stage_c_brd_run.py`
- Modify: `tests/test_stage_c_brd_acceptance.py`

- [ ] **Step 1: Write failing script test**

Add a test that invokes:

```bash
.venv/bin/python scripts/package_stage_c_brd_run.py --run-dir <tmp_run_dir>
```

Expected:

- Writes `acceptance_report.json`.
- Writes `acceptance_report.html`.
- Exits `0` for succeeded summary.
- Exits `1` for failed summary unless `--allow-failed` is provided.

- [ ] **Step 2: Implement script wrapper**

CLI flags:

- `--run-dir`: required.
- `--allow-failed`: optional, keeps exit code `0` for failed runs when generating postmortem reports.
- `--json`: print `acceptance_report.json` payload to stdout.

- [ ] **Step 3: Verify script test**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_brd_acceptance.py -q
```

Expected: pass.

## Task 3: Chinese HTML Acceptance Report

**Files:**
- Create: `src/aedt_agent/reporting/stage_c_brd_report.py`
- Modify: `tests/test_stage_c_brd_acceptance.py`

- [ ] **Step 1: Write failing HTML assertions**

Assert the generated report contains:

- `Stage C BRD/MCM 生产验收报告`
- `环境预检`
- `节点执行状态`
- `端口策略`
- `输出文件`
- Signal/reference net names.
- AEDT project path.
- Blocking issue text when status failed.

- [ ] **Step 2: Implement HTML renderer**

Implement:

```python
def render_brd_acceptance_html(summary: Mapping[str, Any]) -> str:
    ...
```

Constraints:

- Static HTML only.
- No external assets.
- Chinese text.
- Use tables for checks, workflow steps, artifacts, and port strategy.
- Use subdued colors suitable for engineering review.

- [ ] **Step 3: Verify HTML test**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_brd_acceptance.py -q
```

Expected: pass.

## Task 4: One-Shot Production Acceptance Runner

**Files:**
- Create: `scripts/run_stage_c_brd_acceptance.py`
- Modify: `tests/test_stage_c_brd_acceptance.py`

- [ ] **Step 1: Write failing fake-run test**

Run the new script with fake adapter:

```bash
.venv/bin/python scripts/run_stage_c_brd_acceptance.py \
  --adapter fake \
  --params <params.json> \
  --run-dir <run_dir> \
  --config config/demo_config.example.json \
  --local-config <missing-local-config>
```

Expected:

- Writes `preflight.json` before workflow execution.
- Writes `params.json`.
- Writes `workflow_run.json`.
- Writes `import_cutout_summary.json`.
- Writes `acceptance_report.json`.
- Writes `acceptance_report.html`.
- Prints a short terminal summary with status and report path.

- [ ] **Step 2: Implement one-shot runner**

Behavior:

- Load config with existing `load_demo_config()`.
- Load params JSON and copy it to `run_dir/params.json`.
- Run `run_stage_c_preflight()` and write `preflight.json`.
- If preflight has failed checks, stop before AEDT unless `--allow-preflight-fail` is passed.
- For `--adapter fake`, call `run_fake_import_cutout()`.
- For `--adapter real`, call `run_real_import_cutout()` with config AEDT values.
- Always generate acceptance report when possible.
- Default `--adapter` should be `real`.

- [ ] **Step 3: Verify fake-run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_brd_acceptance.py -q
```

Expected: pass.

## Task 5: Docs and Contract Guard

**Files:**
- Modify: `docs/brd-experimental-workflow.md`
- Modify: `docs/stage-c1-demo-readme.md`
- Modify: `tests/test_brd_experimental_docs.py`

- [ ] **Step 1: Write failing doc assertions**

Extend doc tests to require:

- `acceptance_report.json`
- `acceptance_report.html`
- `preflight.json`
- `params.json`
- `model-build-only`
- `默认不 analyze`

- [ ] **Step 2: Update docs**

Add a short “生产验收运行方式” section:

```bash
.venv/bin/python scripts/run_stage_c_brd_acceptance.py \
  --adapter real \
  --params D:/boards/stage_c_brd_params.json \
  --run-dir D:/aedt-agent-runs/brd_case_001 \
  --config config/demo_config.example.json \
  --local-config config/demo_config.local.json
```

Also document offline packaging:

```bash
.venv/bin/python scripts/package_stage_c_brd_run.py --run-dir D:/aedt-agent-runs/brd_case_001
```

- [ ] **Step 3: Verify docs tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_brd_experimental_docs.py -q
```

Expected: pass.

## Task 6: Full Verification and Commit

- [ ] **Step 1: Run focused tests**

```bash
.venv/bin/python -m pytest \
  tests/test_stage_c_brd_acceptance.py \
  tests/test_stage_c_demo_preflight.py \
  tests/test_import_cutout_demo.py \
  tests/test_brd_experimental_docs.py \
  -q
```

- [ ] **Step 2: Run full suite**

```bash
.venv/bin/python -m pytest -q
```

- [ ] **Step 3: Run contract check**

```bash
.venv/bin/python scripts/check_contract_stabilization.py
```

- [ ] **Step 4: Run diff check**

```bash
git diff --check
```

- [ ] **Step 5: Commit**

```bash
git add \
  src/aedt_agent/layout/acceptance.py \
  src/aedt_agent/reporting/stage_c_brd_report.py \
  scripts/package_stage_c_brd_run.py \
  scripts/run_stage_c_brd_acceptance.py \
  tests/test_stage_c_brd_acceptance.py \
  tests/test_brd_experimental_docs.py \
  docs/brd-experimental-workflow.md \
  docs/stage-c1-demo-readme.md

git commit -m "feat: add Stage C BRD production acceptance reports"
```

## Done Criteria

- Production user can run one command to create a BRD/MCM model-build acceptance package.
- Existing demo server behavior is not broken.
- BRD/MCM remains experimental and opt-in.
- Large board analyze remains disabled unless explicitly requested in a future plan.
- Generated report is usable for internal engineering review and failure triage.
