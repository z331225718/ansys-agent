# BRD Workflow Run Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 BRD/MCM experimental workflow 不只生成独立的 `import_cutout_summary.json`，而是进入统一的 `workflow_run.json` / step status / artifact summary 体系，便于 demo UI、汇报和后续自动修复复用。

**Architecture:** 保持 BRD/Layout 节点 experimental 和 opt-in；不把重型真实 AEDT cutout 硬塞进通用 `WorkflowExecutor` 的每个节点里。新增一个 BRD model-build run 汇总层：真实/假执行仍调用现有 `run_fake_import_cutout` / `run_real_import_cutout`，但运行结束后转换成标准 `WorkflowRunResult` 兼容 JSON，并让 demo service 优先读取该 artifact。

**Tech Stack:** Python 3.12, pytest, existing `aedt_agent.demo.import_cutout`, `aedt_agent.workflow.executor`, `aedt_agent.workflow.templates`, `aedt_agent.demo.service`, JSON artifacts, PyAEDT/PyEDB gated by adapter.

---

## Current State

- `workflow_templates/import_brd_cutout_sparam_tdr.json` 已定义 BRD experimental workflow。
- `scripts/run_stage_c_import_cutout.py` 目前只输出 `import_cutout_summary.json`。
- `DemoService.start_import_cutout_run()` 已能启动 fake/real import-cutout job。
- Demo status 目前从 `import_cutout_summary.json` 合成页面数据。
- `WorkflowExecutor` 已经有统一的 `WorkflowRunResult` / `WorkflowStepRun` / `workflow_run.json` 格式。
- BRD workflow 默认不 analyze，这一点不能被本计划改变。

## File Structure

Create:

- `src/aedt_agent/layout/workflow_run.py`  
  将 `import_cutout_summary.json` 风格结果转换为标准 workflow run artifact。

- `tests/test_layout_workflow_run.py`  
  覆盖 BRD summary -> `WorkflowRunResult` 转换逻辑。

Modify:

- `scripts/run_stage_c_import_cutout.py`  
  在原有 `import_cutout_summary.json` 之外写出 `workflow_run.json`，并在 stdout 中打印统一 summary。

- `src/aedt_agent/demo/service.py`  
  BRD job status 优先读取 `workflow_run.json`，保留旧 summary 兼容。

- `tests/test_import_cutout_demo.py`  
  确认 fake runner 仍生成原 summary，不破坏现有 demo。

- `tests/test_stage_c1_demo_service.py`  
  确认 import-cutout job 完成后 status 中包含标准 workflow run steps。

- `docs/brd-experimental-workflow.md`  
  增加 artifacts 约定。

- `benchmarks/reports/aedt_agent_stage_c_progress_report.html`  
  增加“BRD model-build artifact 已统一”的展示说明。

---

## Task 1: Add BRD Summary to WorkflowRun Converter

**Files:**
- Create: `src/aedt_agent/layout/workflow_run.py`
- Create: `tests/test_layout_workflow_run.py`

- [ ] **Step 1: Write failing converter tests**

Create `tests/test_layout_workflow_run.py`:

```python
from aedt_agent.layout.workflow_run import import_cutout_summary_to_workflow_run


def test_import_cutout_summary_to_workflow_run_maps_steps_and_outputs():
    summary = {
        "status": "succeeded",
        "layout_file": "/tmp/case.brd",
        "signal_nets": ["SRDS_3_RX1_N", "SRDS_3_RX1_P"],
        "reference_nets": ["GND"],
        "edb_path": "/tmp/case_cutout.aedb",
        "aedt_project": "/tmp/case_cutout.aedt",
        "touchstone": "",
        "tdr": "",
        "steps": [
            {"id": "import_layout_file", "label": "Import", "status": "succeeded"},
            {"id": "select_layout_nets", "label": "Select nets", "status": "succeeded"},
            {"id": "create_layout_cutout", "label": "Cutout", "status": "succeeded"},
        ],
    }

    run = import_cutout_summary_to_workflow_run(summary)
    data = run.to_dict()

    assert data["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert data["status"] == "succeeded"
    assert [step["step_id"] for step in data["steps"]] == [
        "import_layout_file",
        "select_layout_nets",
        "create_layout_cutout",
    ]
    assert data["outputs"]["aedt_project"] == "/tmp/case_cutout.aedt"
    assert data["outputs"]["edb_path"] == "/tmp/case_cutout.aedb"
    assert data["outputs"]["signal_nets"] == ["SRDS_3_RX1_N", "SRDS_3_RX1_P"]
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_workflow_run.py -q
```

Expected: FAIL because `aedt_agent.layout.workflow_run` does not exist.

- [ ] **Step 3: Implement converter**

Create `src/aedt_agent/layout/workflow_run.py`:

```python
from __future__ import annotations

from typing import Any

from aedt_agent.workflow.executor import WorkflowRunResult, WorkflowStepRun


WORKFLOW_ID = "import_brd_cutout_sparam_tdr_v1"

_STEP_TO_NODE = {
    "import_layout_file": "import_layout_file",
    "select_layout_nets": "select_layout_nets",
    "create_layout_cutout": "create_layout_cutout",
    "configure_layout_stackup": "configure_layout_stackup",
    "locate_layout_port_candidates": "locate_layout_port_candidates",
    "create_layout_ports": "create_layout_ports",
    "create_layout_setup": "create_layout_setup",
}


def import_cutout_summary_to_workflow_run(summary: dict[str, Any]) -> WorkflowRunResult:
    steps = [_summary_step_to_workflow_step(step) for step in summary.get("steps", []) if isinstance(step, dict)]
    outputs = {
        "layout_file": summary.get("layout_file", ""),
        "edb_path": summary.get("edb_path", ""),
        "aedt_project": summary.get("aedt_project", ""),
        "touchstone": summary.get("touchstone", ""),
        "tdr": summary.get("tdr", ""),
        "signal_nets": list(summary.get("signal_nets") or []),
        "reference_nets": list(summary.get("reference_nets") or []),
        "solve_skipped": summary.get("layout_solve", {}).get("status") == "skipped" if isinstance(summary.get("layout_solve"), dict) else True,
    }
    return WorkflowRunResult(
        workflow_id=WORKFLOW_ID,
        status=str(summary.get("status") or "failed"),
        validation={"passed": True, "errors": [], "warnings": [{"message": "BRD workflow run is converted from model-build summary."}]},
        model_validation={},
        model_facts={},
        steps=steps,
        outputs=outputs,
        repair_context={},
    )


def _summary_step_to_workflow_step(step: dict[str, Any]) -> WorkflowStepRun:
    step_id = str(step.get("id") or step.get("step_id") or step.get("node_id") or "unknown_step")
    status = str(step.get("status") or "failed")
    return WorkflowStepRun(
        step_id=step_id,
        node_id=_STEP_TO_NODE.get(step_id, step_id),
        inputs={},
        status=status,
        output={key: value for key, value in step.items() if key not in {"id", "step_id", "node_id", "label", "status"}},
        snapshot_summary={"label": step.get("label", step_id)},
        error_type=str(step.get("error_type") or ""),
        error_message=str(step.get("error") or step.get("error_message") or ""),
        elapsed_seconds=float(step.get("elapsed_seconds") or 0.0),
    )
```

- [ ] **Step 4: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_workflow_run.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aedt_agent/layout/workflow_run.py tests/test_layout_workflow_run.py
git commit -m "feat: convert BRD summary to workflow run artifact"
```

---

## Task 2: Write workflow_run.json from Import-Cutout Script

**Files:**
- Modify: `scripts/run_stage_c_import_cutout.py`
- Test: `tests/test_stage_c_demo_scripts.py`

- [ ] **Step 1: Write failing script test**

Add to `tests/test_stage_c_demo_scripts.py`:

```python
def test_import_cutout_script_writes_workflow_run_artifact(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(
        __import__("json").dumps(
            {
                "layout_file": str(layout_file),
                "signal_nets": "*tx0*",
                "reference_nets": "gnd",
            }
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"

    result = __import__("subprocess").run(
        [
            __import__("sys").executable,
            "scripts/run_stage_c_import_cutout.py",
            "--adapter",
            "fake",
            "--params",
            str(params),
            "--run-dir",
            str(run_dir),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    workflow_run = __import__("json").loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    assert workflow_run["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert workflow_run["status"] == "succeeded"
    assert workflow_run["outputs"]["signal_nets"] == ["56G_TX0_P", "56G_TX0_N"]
    assert '"workflow_id": "import_brd_cutout_sparam_tdr_v1"' in result.stdout
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_demo_scripts.py::test_import_cutout_script_writes_workflow_run_artifact -q
```

Expected: FAIL because `workflow_run.json` is not written.

- [ ] **Step 3: Update script**

In `scripts/run_stage_c_import_cutout.py`, add:

```python
from aedt_agent.layout.workflow_run import import_cutout_summary_to_workflow_run
```

After writing `import_cutout_summary.json`, add:

```python
workflow_run = import_cutout_summary_to_workflow_run(result)
workflow_run.write_json(run_dir / "workflow_run.json")
print(json.dumps(workflow_run.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
if not workflow_run.succeeded:
    raise SystemExit(1)
```

Remove or replace the old final `print(json.dumps(result, ...))` so stdout shows the unified workflow run artifact.

- [ ] **Step 4: Run script test**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_demo_scripts.py::test_import_cutout_script_writes_workflow_run_artifact -q
```

Expected: PASS.

- [ ] **Step 5: Run related script tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_demo_scripts.py tests/test_import_cutout_demo.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_stage_c_import_cutout.py tests/test_stage_c_demo_scripts.py
git commit -m "feat: emit BRD workflow run artifact"
```

---

## Task 3: Make Demo Service Prefer Unified WorkflowRun Artifact

**Files:**
- Modify: `src/aedt_agent/demo/service.py`
- Test: `tests/test_stage_c1_demo_service.py`

- [ ] **Step 1: Write failing demo service test**

Add to `tests/test_stage_c1_demo_service.py`:

```python
def test_demo_service_import_cutout_status_prefers_workflow_run_artifact(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")
    job = DemoRunJob(
        job_id="job1",
        template_id="import_brd_cutout_sparam_tdr",
        adapter="fake",
        run_dir=tmp_path / "run",
        run_kind="import_cutout",
        stream_to_terminal=False,
    )
    job.run_dir.mkdir()
    job.status = "succeeded"
    job.returncode = 0
    workflow_run = {
        "workflow_id": "import_brd_cutout_sparam_tdr_v1",
        "status": "succeeded",
        "steps": [
            {"step_id": "import_layout_file", "node_id": "import_layout_file", "status": "succeeded", "output": {}},
            {"step_id": "create_layout_cutout", "node_id": "create_layout_cutout", "status": "succeeded", "output": {}},
        ],
        "outputs": {
            "aedt_project": "demo.aedt",
            "edb_path": "demo.aedb",
            "signal_nets": ["P", "N"],
            "reference_nets": ["GND"],
        },
    }
    (job.run_dir / "workflow_run.json").write_text(__import__("json").dumps(workflow_run), encoding="utf-8")
    service._jobs[job.job_id] = job

    status = service.real_run_status(job.job_id)

    assert status["workflow_run"]["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert [step["step_id"] for step in status["steps"]] == ["import_layout_file", "create_layout_cutout"]
    assert status["artifacts"]["workflow_run"].endswith("workflow_run.json")
    assert status["aedt_project"] == "demo.aedt"
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c1_demo_service.py::test_demo_service_import_cutout_status_prefers_workflow_run_artifact -q
```

Expected: FAIL because import-cutout status currently prioritizes `import_cutout_summary.json` only.

- [ ] **Step 3: Update status aggregation**

In `src/aedt_agent/demo/service.py`, locate the helper that reads run artifacts for `job.to_dict()` / `real_run_status`. Update it so:

```python
workflow_run_path = run_dir / "workflow_run.json"
if workflow_run_path.exists():
    workflow_run = _read_json(workflow_run_path)
    data["workflow_run"] = workflow_run
    data["steps"] = workflow_run.get("steps", data.get("steps", []))
    outputs = workflow_run.get("outputs", {}) if isinstance(workflow_run.get("outputs"), dict) else {}
    data["aedt_project"] = outputs.get("aedt_project", data.get("aedt_project", ""))
    data["edb_path"] = outputs.get("edb_path", data.get("edb_path", ""))
    data.setdefault("artifacts", {})["workflow_run"] = str(workflow_run_path)
```

Keep the existing `import_cutout_summary.json` fallback for compatibility. Do not remove `import_cutout` from status payload.

- [ ] **Step 4: Run demo service tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c1_demo_service.py::test_demo_service_import_cutout_status_prefers_workflow_run_artifact tests/test_stage_c1_demo_service.py::test_demo_service_agent_run_starts_import_cutout_job_with_fake_adapter tests/test_stage_c1_demo_service.py::test_demo_service_real_import_cutout_runs_in_subprocess_main_thread -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aedt_agent/demo/service.py tests/test_stage_c1_demo_service.py
git commit -m "feat: surface BRD workflow run in demo status"
```

---

## Task 4: Add BRD Artifact Documentation and Report Update

**Files:**
- Modify: `docs/brd-experimental-workflow.md`
- Modify: `docs/aedt-agent-stage-c-progress-report.md`
- Modify: `benchmarks/reports/aedt_agent_stage_c_progress_report.html`
- Test: `tests/test_brd_experimental_docs.py`

- [ ] **Step 1: Write failing docs test**

Create `tests/test_brd_experimental_docs.py`:

```python
from pathlib import Path


def test_brd_experimental_docs_define_unified_artifacts():
    text = Path("docs/brd-experimental-workflow.md").read_text(encoding="utf-8")

    assert "workflow_run.json" in text
    assert "import_cutout_summary.json" in text
    assert "model-build only" in text
    assert "不运行 analyze" in text
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_brd_experimental_docs.py -q
```

Expected: FAIL because docs do not yet state unified artifacts.

- [ ] **Step 3: Update BRD document**

Append to `docs/brd-experimental-workflow.md`:

```markdown
## Artifact 约定

BRD/MCM experimental workflow 同时保留两个 artifact：

- `import_cutout_summary.json`：板级 model-build 的原始执行摘要，包含 PyEDB cutout、stackup、端口、setup、AEDT project path。
- `workflow_run.json`：转换后的统一 workflow artifact，包含标准 `workflow_id`、`status`、`steps`、`outputs`，供 demo UI、报告和后续 repair/evolution 使用。

当前仍是 model-build only：默认不运行 analyze，不承诺 S 参数/TDR solve 结果。
```

- [ ] **Step 4: Update Stage C report**

In `docs/aedt-agent-stage-c-progress-report.md`, update the BRD Experimental Track paragraph to mention:

```markdown
BRD job now emits both `import_cutout_summary.json` and `workflow_run.json`, so demo/report consumers can use the same artifact shape as HFSS core workflows.
```

In `benchmarks/reports/aedt_agent_stage_c_progress_report.html`, add the same idea in the BRD Experimental Track section.

- [ ] **Step 5: Run docs test**

Run:

```bash
.venv/bin/python -m pytest tests/test_brd_experimental_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/brd-experimental-workflow.md docs/aedt-agent-stage-c-progress-report.md benchmarks/reports/aedt_agent_stage_c_progress_report.html tests/test_brd_experimental_docs.py
git commit -m "docs: document BRD workflow artifacts"
```

---

## Task 5: Verification and Push

**Files:**
- No new source files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_layout_workflow_run.py \
  tests/test_stage_c_demo_scripts.py \
  tests/test_stage_c1_demo_service.py \
  tests/test_import_cutout_demo.py \
  tests/test_brd_experimental_docs.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS with existing skipped tests only.

- [ ] **Step 3: Run contract check**

Run:

```bash
.venv/bin/python scripts/check_contract_stabilization.py
```

Expected: output contains:

```json
"default_layout_nodes": []
```

- [ ] **Step 4: Check worktree**

Run:

```bash
git status --short
```

Expected: only intended files are staged/modified. Do not commit `session`. Do not commit `config/*.local.json`.

- [ ] **Step 5: Push**

Run:

```bash
git push
```

Expected: branch `stage-a-grounding-benchmark` pushed successfully.

---

## Self-Review

- Spec coverage: This plan unifies BRD model-build artifacts without promoting Layout/BRD nodes out of experimental and without requiring heavy solve.
- Scope control: It does not build a new UI, does not add S-parameter/TDR solve, and does not change the existing real PyEDB/PyAEDT model-build path.
- Placeholder scan: No `TBD`, `TODO`, or unspecified implementation steps remain.
- Type consistency: The plan reuses existing `WorkflowRunResult` and `WorkflowStepRun`, and uses existing demo service artifact keys.
