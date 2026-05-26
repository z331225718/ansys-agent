# BRD Demo Live Progress and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 BRD/MCM experimental demo 从“最终生成 artifact”推进到“可实时观察、可解释、可验收”的演示闭环。

**Architecture:** 保持 BRD/MCM workflow 为 experimental、model-build-only，不默认暴露给普通 HFSS 节点路径。新增一个轻量 progress writer，让 fake 和 real import-cutout 都能持续写出 `workflow_run.json`；demo 页面只消费统一的 workflow step/status，不直接理解 PyEDB/AEDT 内部细节。

**Tech Stack:** Python 3.12, pytest, existing `aedt_agent.demo.import_cutout`, `aedt_agent.layout.workflow_run`, `aedt_agent.demo.service`, `aedt_agent.demo.web`, JSON run artifacts.

---

## Current State

- `scripts/run_stage_c_import_cutout.py` 已在结束时写出 `workflow_run.json`。
- `DemoService` 已优先读取 `workflow_run.json`，但 fake path 仍只写 `import_cutout_summary.json`。
- real import-cutout 子进程运行期间不会逐步更新 `workflow_run.json`，页面多数情况下只能在结束后看到所有节点变更。
- web 中 BRD 展示 step id 使用 `discover_file/import_layout/select_nets/...`，而统一 artifact 使用 `import_layout_file/select_layout_nets/create_layout_cutout/...`，两者未对齐。
- `renderTdr()` 内部存在 `valid` 未定义的渲染风险，应纳入本轮修正。
- 本轮不做新的 AEDT 物理功能，不开启 heavy board analyze。

## File Structure

Create:

- `src/aedt_agent/layout/progress.py`  
  BRD workflow progress writer。负责把阶段性步骤转换为标准 `workflow_run.json`。

- `tests/test_layout_progress.py`  
  覆盖 progress writer 的 running/succeeded/failed artifact 写入。

Modify:

- `src/aedt_agent/demo/import_cutout.py`  
  给 `run_fake_import_cutout()`、`run_real_import_cutout()`、`import_brd_with_pyedb_cutout()` 增加可选 `progress_callback`，在真实 PyEDB/HFSS3DLayout 阶段发出步骤状态。

- `scripts/run_stage_c_import_cutout.py`  
  创建 progress writer，真实/假执行期间持续写 `workflow_run.json`，stdout 输出简短 step heartbeat。

- `src/aedt_agent/demo/service.py`  
  fake import-cutout job 也写 `workflow_run.json`；status 中从 running artifact 读取当前步骤。

- `src/aedt_agent/demo/web.py`  
  BRD step id 与 workflow artifact 对齐；修复 TDR 图变量；页面根据 `running/succeeded/failed` 正确更新节点，不再等全部完成后一次性 done。

- `tests/test_import_cutout_demo.py`  
  验证 fake/real progress callback 被调用。

- `tests/test_stage_c_demo_scripts.py`  
  验证 script fake path 在结束后保留标准 artifact，并 stdout 包含 progress heartbeat。

- `tests/test_stage_c1_demo_service.py`  
  验证 fake import-cutout service job 产生 `workflow_run.json`，status 中步骤可读。

- `tests/test_stage_c1_demo_web.py`  
  验证 web 使用真实 BRD step id，并包含 TDR 渲染修复。

- `docs/brd-experimental-workflow.md`  
  记录 live progress artifact 约定和 model-build-only 边界。

---

## Task 1: Add BRD Workflow Progress Writer

**Files:**
- Create: `src/aedt_agent/layout/progress.py`
- Create: `tests/test_layout_progress.py`

- [ ] **Step 1: Write failing progress writer tests**

Create `tests/test_layout_progress.py`:

```python
import json

from aedt_agent.layout.progress import BrdWorkflowProgressWriter


def test_brd_progress_writer_writes_running_and_succeeded_artifacts(tmp_path):
    writer = BrdWorkflowProgressWriter(
        tmp_path / "workflow_run.json",
        layout_file="/tmp/case.brd",
        signal_nets=["SRDS_3_RX1_P", "SRDS_3_RX1_N"],
        reference_nets=["GND"],
    )

    writer.step_running("import_layout_file", "Open BRD/MCM with PyEDB")
    running = json.loads((tmp_path / "workflow_run.json").read_text(encoding="utf-8"))
    assert running["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert running["status"] == "running"
    assert running["steps"][0]["step_id"] == "import_layout_file"
    assert running["steps"][0]["status"] == "running"

    writer.step_succeeded("import_layout_file", "Open BRD/MCM with PyEDB", {"source_edb_path": "/tmp/source.aedb"})
    writer.finish_succeeded({"edb_path": "/tmp/cutout.aedb", "aedt_project": "/tmp/cutout.aedt"})
    done = json.loads((tmp_path / "workflow_run.json").read_text(encoding="utf-8"))
    assert done["status"] == "succeeded"
    assert done["steps"][0]["status"] == "succeeded"
    assert done["outputs"]["edb_path"] == "/tmp/cutout.aedb"
    assert done["outputs"]["signal_nets"] == ["SRDS_3_RX1_P", "SRDS_3_RX1_N"]


def test_brd_progress_writer_records_failed_step(tmp_path):
    writer = BrdWorkflowProgressWriter(tmp_path / "workflow_run.json", layout_file="/tmp/case.brd")

    writer.step_running("select_layout_nets", "Select Nets")
    writer.step_failed("select_layout_nets", "Select Nets", "ValueError", "no signal nets matched")

    failed = json.loads((tmp_path / "workflow_run.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["steps"][0]["status"] == "failed"
    assert failed["steps"][0]["error_type"] == "ValueError"
    assert "no signal nets matched" in failed["steps"][0]["error_message"]
    assert failed["repair_context"]["failed_step_id"] == "select_layout_nets"
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_progress.py -q
```

Expected: FAIL because `aedt_agent.layout.progress` does not exist.

- [ ] **Step 3: Implement progress writer**

Create `src/aedt_agent/layout/progress.py`:

```python
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from aedt_agent.layout.workflow_run import WORKFLOW_ID
from aedt_agent.workflow.executor import WorkflowRunResult, WorkflowStepRun


class BrdWorkflowProgressWriter:
    def __init__(
        self,
        artifact_path: Path,
        *,
        layout_file: str = "",
        signal_nets: list[str] | None = None,
        reference_nets: list[str] | None = None,
    ) -> None:
        self.artifact_path = artifact_path
        self.outputs: dict[str, Any] = {
            "layout_file": layout_file,
            "signal_nets": list(signal_nets or []),
            "reference_nets": list(reference_nets or []),
            "solve_skipped": True,
        }
        self.steps: list[WorkflowStepRun] = []
        self._started: dict[str, float] = {}

    def step_running(self, step_id: str, label: str, output: dict[str, Any] | None = None) -> None:
        self._started.setdefault(step_id, time.time())
        self._upsert_step(step_id, label, "running", output or {})
        self._write("running")

    def step_succeeded(self, step_id: str, label: str, output: dict[str, Any] | None = None) -> None:
        self._upsert_step(step_id, label, "succeeded", output or {})
        self.outputs.update(output or {})
        self._write("running")

    def step_failed(self, step_id: str, label: str, error_type: str, error_message: str) -> None:
        self._upsert_step(step_id, label, "failed", {}, error_type=error_type, error_message=error_message)
        self._write("failed", repair_context={"failed_step_id": step_id, "error_message": error_message})

    def finish_succeeded(self, outputs: dict[str, Any] | None = None) -> None:
        self.outputs.update(outputs or {})
        self._write("succeeded")

    def finish_failed(self, error_type: str, error_message: str) -> None:
        self._write("failed", repair_context={"error_type": error_type, "error_message": error_message})

    def _upsert_step(
        self,
        step_id: str,
        label: str,
        status: str,
        output: dict[str, Any],
        *,
        error_type: str = "",
        error_message: str = "",
    ) -> None:
        elapsed = round(time.time() - self._started.get(step_id, time.time()), 3)
        step = WorkflowStepRun(
            step_id=step_id,
            node_id=step_id,
            inputs={},
            status=status,
            output=output,
            snapshot_summary={"label": label},
            error_type=error_type,
            error_message=error_message,
            elapsed_seconds=elapsed,
        )
        for index, existing in enumerate(self.steps):
            if existing.step_id == step_id:
                self.steps[index] = step
                return
        self.steps.append(step)

    def _write(self, status: str, *, repair_context: dict[str, Any] | None = None) -> None:
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        WorkflowRunResult(
            workflow_id=WORKFLOW_ID,
            status=status,
            validation={"passed": True, "errors": [], "warnings": []},
            model_validation={},
            model_facts={},
            steps=list(self.steps),
            outputs=dict(self.outputs),
            repair_context=repair_context or {},
        ).write_json(self.artifact_path)
```

- [ ] **Step 4: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_progress.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aedt_agent/layout/progress.py tests/test_layout_progress.py
git commit -m "feat: add BRD workflow progress writer"
```

---

## Task 2: Emit Progress from Import-Cutout Pipeline

**Files:**
- Modify: `src/aedt_agent/demo/import_cutout.py`
- Test: `tests/test_import_cutout_demo.py`

- [ ] **Step 1: Write failing callback tests**

Append to `tests/test_import_cutout_demo.py`:

```python
def test_fake_import_cutout_emits_progress_events(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    request = build_import_cutout_request(
        {"layout_file": str(layout_file), "signal_nets": "*tx0*", "reference_nets": "gnd", "artifact_dir": str(tmp_path / "run")}
    )
    events = []

    run_fake_import_cutout(request, progress_callback=lambda event: events.append(event))

    assert [event["step_id"] for event in events if event["status"] == "running"] == [
        "import_layout_file",
        "select_layout_nets",
        "create_layout_cutout",
        "configure_layout_stackup",
        "locate_layout_port_candidates",
        "create_layout_ports",
        "create_layout_setup",
    ]
    assert events[-1]["status"] == "succeeded"


def test_real_import_cutout_reports_failed_progress_when_open_layout_fails(monkeypatch, tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    request = build_import_cutout_request({"layout_file": str(layout_file), "artifact_dir": str(tmp_path / "run")})
    events = []

    def fail_open(*args, **kwargs):
        raise RuntimeError("cannot open board")

    monkeypatch.setattr(import_cutout, "_open_layout_with_pyedb", fail_open)

    try:
        import_cutout.import_brd_with_pyedb_cutout(
            request,
            aedt_version="2026.1",
            non_graphical=False,
            progress_callback=lambda event: events.append(event),
        )
    except RuntimeError:
        pass

    assert events[0]["step_id"] == "import_layout_file"
    assert events[0]["status"] == "running"
    assert events[-1]["status"] == "failed"
    assert events[-1]["step_id"] == "import_layout_file"
    assert "cannot open board" in events[-1]["error_message"]
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_import_cutout_demo.py::test_fake_import_cutout_emits_progress_events tests/test_import_cutout_demo.py::test_real_import_cutout_reports_failed_progress_when_open_layout_fails -q
```

Expected: FAIL because functions do not accept `progress_callback`.

- [ ] **Step 3: Add callback plumbing**

Modify signatures in `src/aedt_agent/demo/import_cutout.py`:

```python
from collections.abc import Callable

ProgressCallback = Callable[[dict[str, Any]], None]


def _emit_progress(
    progress_callback: ProgressCallback | None,
    step_id: str,
    label: str,
    status: str,
    **payload: Any,
) -> None:
    if progress_callback is None:
        return
    event = {"step_id": step_id, "label": label, "status": status}
    event.update(payload)
    progress_callback(event)
```

Change:

```python
def run_fake_import_cutout(request: ImportCutoutRequest, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
```

At the start of each fake stage emit running/succeeded for these stages:

```python
_emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "running")
_emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "succeeded", layout_file=str(request.layout_file))
_emit_progress(progress_callback, "select_layout_nets", "Select Nets", "running")
_emit_progress(progress_callback, "select_layout_nets", "Select Nets", "succeeded", signal_nets=signal_nets, reference_nets=reference_nets)
_emit_progress(progress_callback, "create_layout_cutout", "Create PyEDB Cutout", "running")
_emit_progress(progress_callback, "create_layout_cutout", "Create PyEDB Cutout", "succeeded", edb_path=summary["edb_path"])
_emit_progress(progress_callback, "configure_layout_stackup", "Load Stackup XML", "running")
_emit_progress(progress_callback, "configure_layout_stackup", "Load Stackup XML", "succeeded")
_emit_progress(progress_callback, "locate_layout_port_candidates", "Locate Port Candidates", "running")
_emit_progress(progress_callback, "locate_layout_port_candidates", "Locate Port Candidates", "succeeded")
_emit_progress(progress_callback, "create_layout_ports", "Create Ports", "running")
_emit_progress(progress_callback, "create_layout_ports", "Create Ports", "succeeded")
_emit_progress(progress_callback, "create_layout_setup", "Create Setup/Sweep", "running")
_emit_progress(progress_callback, "create_layout_setup", "Create Setup/Sweep", "succeeded", aedt_project=summary["aedt_project"])
_emit_progress(progress_callback, "validate_layout_model", "Validate Model", "succeeded")
_emit_progress(progress_callback, "workflow", "BRD/MCM model build", "succeeded", outputs=summary)
```

Change:

```python
def run_real_import_cutout(..., progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
```

and pass it to:

```python
return import_brd_with_pyedb_cutout(
    request,
    aedt_version=aedt_version,
    non_graphical=non_graphical,
    progress_callback=progress_callback,
)
```

Change:

```python
def import_brd_with_pyedb_cutout(
    request: ImportCutoutRequest,
    *,
    aedt_version: str,
    non_graphical: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
```

Wrap major real stages:

```python
_emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "running")
try:
    edb, source_edb_path = _open_layout_with_pyedb(...)
except Exception as exc:
    _emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "failed", error_type=type(exc).__name__, error_message=str(exc))
    raise
_emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "succeeded", source_edb_path=str(source_edb_path))
```

Repeat the same pattern for:

- `select_layout_nets`
- `create_layout_cutout`
- `configure_layout_stackup`
- `locate_layout_port_candidates`
- `create_layout_ports`
- `create_layout_setup`
- `validate_layout_model`

For failures after the exact failing stage is known, emit `status="failed"` before re-raising.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_import_cutout_demo.py::test_fake_import_cutout_emits_progress_events tests/test_import_cutout_demo.py::test_real_import_cutout_reports_failed_progress_when_open_layout_fails -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aedt_agent/demo/import_cutout.py tests/test_import_cutout_demo.py
git commit -m "feat: emit BRD import-cutout progress events"
```

---

## Task 3: Wire Progress Writer into Script and Demo Service

**Files:**
- Modify: `scripts/run_stage_c_import_cutout.py`
- Modify: `src/aedt_agent/demo/service.py`
- Test: `tests/test_stage_c_demo_scripts.py`
- Test: `tests/test_stage_c1_demo_service.py`

- [ ] **Step 1: Write failing script heartbeat test**

Append to `tests/test_stage_c_demo_scripts.py`:

```python
def test_import_cutout_script_prints_progress_heartbeat(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(
        __import__("json").dumps({"layout_file": str(layout_file), "signal_nets": "*tx0*", "reference_nets": "gnd"}),
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

    assert "[brd-progress] import_layout_file running" in result.stdout
    assert "[brd-progress] create_layout_setup succeeded" in result.stdout
    workflow_run = __import__("json").loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    assert workflow_run["status"] == "succeeded"
```

Append to `tests/test_stage_c1_demo_service.py`:

```python
def test_demo_service_fake_import_cutout_writes_workflow_run(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    service = DemoService(Path("."), run_dir=tmp_path / "demo")

    started = service.start_import_cutout_run(
        {
            "adapter": "fake",
            "stream_to_terminal": False,
            "parameters": {"layout_file": str(layout_file), "signal_nets": "*tx0*", "reference_nets": "gnd"},
        }
    )
    status = _wait_for_job(service, started["job_id"])

    workflow_run = Path(status["artifacts"]["workflow_run"])
    assert workflow_run.exists()
    assert status["workflow_run"]["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert any(step["step_id"] == "create_layout_setup" for step in status["steps"])
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_demo_scripts.py::test_import_cutout_script_prints_progress_heartbeat tests/test_stage_c1_demo_service.py::test_demo_service_fake_import_cutout_writes_workflow_run -q
```

Expected: FAIL because script/service are not yet using the progress writer.

- [ ] **Step 3: Add script progress writer**

Modify `scripts/run_stage_c_import_cutout.py`:

```python
from aedt_agent.layout.progress import BrdWorkflowProgressWriter
```

After `request = build_import_cutout_request(parameters)`:

```python
progress = BrdWorkflowProgressWriter(
    run_dir / "workflow_run.json",
    layout_file=str(request.layout_file),
    signal_nets=request.signal_net_patterns,
    reference_nets=request.reference_net_patterns,
)

def on_progress(event: dict[str, object]) -> None:
    step_id = str(event.get("step_id") or "")
    label = str(event.get("label") or step_id)
    status = str(event.get("status") or "running")
    output = {key: value for key, value in event.items() if key not in {"step_id", "label", "status", "error_type", "error_message"}}
    if status == "running":
        progress.step_running(step_id, label, output)
    elif status == "succeeded":
        progress.step_succeeded(step_id, label, output)
    elif status == "failed":
        progress.step_failed(step_id, label, str(event.get("error_type") or ""), str(event.get("error_message") or ""))
    print(f"[brd-progress] {step_id} {status} {label}", flush=True)
```

Call:

```python
result = run_fake_import_cutout(request, progress_callback=on_progress)
```

and:

```python
result = run_real_import_cutout(..., progress_callback=on_progress)
```

After final summary conversion, keep writing final `workflow_run.json` with `import_cutout_summary_to_workflow_run(result)` so final artifact remains canonical.

- [ ] **Step 4: Add service fake writer**

Modify fake branch in `DemoService._run_import_cutout_job()`:

```python
from aedt_agent.layout.progress import BrdWorkflowProgressWriter
from aedt_agent.layout.workflow_run import import_cutout_summary_to_workflow_run
```

Before fake call:

```python
progress = BrdWorkflowProgressWriter(
    job.run_dir / "workflow_run.json",
    layout_file=str(request.layout_file),
    signal_nets=request.signal_net_patterns,
    reference_nets=request.reference_net_patterns,
)

def on_progress(event: dict[str, object]) -> None:
    step_id = str(event.get("step_id") or "")
    label = str(event.get("label") or step_id)
    status = str(event.get("status") or "running")
    output = {key: value for key, value in event.items() if key not in {"step_id", "label", "status", "error_type", "error_message"}}
    if status == "running":
        progress.step_running(step_id, label, output)
    elif status == "succeeded":
        progress.step_succeeded(step_id, label, output)
    elif status == "failed":
        progress.step_failed(step_id, label, str(event.get("error_type") or ""), str(event.get("error_message") or ""))
```

Then:

```python
result = run_fake_import_cutout(request, progress_callback=on_progress)
import_cutout_summary_to_workflow_run(result).write_json(job.run_dir / "workflow_run.json")
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c_demo_scripts.py::test_import_cutout_script_prints_progress_heartbeat tests/test_stage_c1_demo_service.py::test_demo_service_fake_import_cutout_writes_workflow_run -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_stage_c_import_cutout.py src/aedt_agent/demo/service.py tests/test_stage_c_demo_scripts.py tests/test_stage_c1_demo_service.py
git commit -m "feat: stream BRD workflow progress artifacts"
```

---

## Task 4: Align Demo Web Step IDs and Fix TDR Rendering

**Files:**
- Modify: `src/aedt_agent/demo/web.py`
- Test: `tests/test_stage_c1_demo_web.py`

- [ ] **Step 1: Write failing web assertions**

Update `test_render_demo_page_contains_workspace_sections()` in `tests/test_stage_c1_demo_web.py`:

```python
    assert "import_layout_file" in html
    assert "select_layout_nets" in html
    assert "create_layout_cutout" in html
    assert "configure_layout_stackup" in html
    assert "locate_layout_port_candidates" in html
    assert "create_layout_ports" in html
    assert "create_layout_setup" in html
    assert "validate_layout_model" in html
    assert "valid.map" not in html
    assert "tdrSamples.map" in html
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c1_demo_web.py::test_render_demo_page_contains_workspace_sections -q
```

Expected: FAIL because BRD web steps use old ids and TDR renderer still references `valid`.

- [ ] **Step 3: Align BRD step ids**

Modify the `import_brd_cutout_sparam_tdr.steps` array in `src/aedt_agent/demo/web.py`:

```javascript
steps: [
  ['import_layout_file','Open BRD/MCM with PyEDB','打开用户指定文件并读取 board nets'],
  ['select_layout_nets','Select Nets','展开 signal/reference net 通配符并给出候选'],
  ['create_layout_cutout','Create PyEDB Cutout','多线程创建 cutout AEDB，并复制给 HFSS 3D Layout'],
  ['configure_layout_stackup','Load Stackup XML','导入 stackup XML 并保存 cutout AEDB'],
  ['locate_layout_port_candidates','Locate Port Candidates','识别信号两端 component/pin/ball 候选'],
  ['create_layout_ports','Create Ports','按 board rule 创建端口'],
  ['create_layout_setup','Create Setup/Sweep','创建 3D Layout setup 和 DC-67GHz 扫频'],
  ['validate_layout_model','Validate Model','校验 cutout、叠层、端口、setup 和工程文件']
]
```

- [ ] **Step 4: Fix TDR renderer variable**

In `renderTdr(tdr)`, replace the incorrect path helper:

```javascript
const path = key => valid.map((item, index) => `${index ? 'L' : 'M'}${x(item.frequency).toFixed(2)},${y(item[key]).toFixed(2)}`).join(' ');
```

with:

```javascript
const tdrSamples = samples;
const path = () => tdrSamples.map((item, index) => `${index ? 'L' : 'M'}${x(item.time_ps).toFixed(2)},${y(item.impedance_ohm).toFixed(2)}`).join(' ');
```

Then replace the chart path body with:

```javascript
<path d="${path()}" fill="none" stroke="#b7791f" stroke-width="2.4"/>
```

Remove any references to `s11_db`, `s21_db`, `frequency`, and `valid` inside `renderTdr()`.

- [ ] **Step 5: Run web tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c1_demo_web.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aedt_agent/demo/web.py tests/test_stage_c1_demo_web.py
git commit -m "fix: align BRD demo progress display"
```

---

## Task 5: Document and Verify Acceptance Path

**Files:**
- Modify: `docs/brd-experimental-workflow.md`
- Modify: `benchmarks/reports/aedt_agent_stage_c_progress_report.html`

- [ ] **Step 1: Update BRD workflow documentation**

Add this section to `docs/brd-experimental-workflow.md`:

```markdown
## Live progress contract

BRD/MCM demo runs write `workflow_run.json` while the model-build job is still running. The page should treat this file as the single source of truth for node status.

Canonical step ids:

- `import_layout_file`
- `select_layout_nets`
- `create_layout_cutout`
- `configure_layout_stackup`
- `locate_layout_port_candidates`
- `create_layout_ports`
- `create_layout_setup`
- `validate_layout_model`

The BRD/MCM path remains `experimental` and `model-build-only` by default. Heavy board analyze is intentionally skipped unless a future explicit run mode enables it.
```

- [ ] **Step 2: Update HTML progress report**

In `benchmarks/reports/aedt_agent_stage_c_progress_report.html`, add one concise Chinese block under Stage C / BRD content:

```html
<section>
  <h2>BRD/MCM 演示可观测性</h2>
  <p>最新版本将 BRD/MCM cutout 建模过程接入统一 workflow_run.json：页面可以看到 import、net selection、cutout、stackup、port、setup、validation 的实时状态。该链路仍保持 experimental、默认只建模不求解，避免重型板级仿真影响演示稳定性。</p>
</section>
```

- [ ] **Step 3: Run focused verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_progress.py tests/test_import_cutout_demo.py tests/test_stage_c_demo_scripts.py tests/test_stage_c1_demo_service.py tests/test_stage_c1_demo_web.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_contract_stabilization.py
git diff --check
```

Expected:

- pytest passes.
- contract check still reports `default_layout_nodes: []`.
- `git diff --check` has no output.

- [ ] **Step 5: Commit docs**

```bash
git add docs/brd-experimental-workflow.md benchmarks/reports/aedt_agent_stage_c_progress_report.html
git commit -m "docs: describe BRD live progress contract"
```

---

## Task 6: Push Final Branch

**Files:**
- No source file changes beyond committed work.

- [ ] **Step 1: Inspect worktree**

Run:

```bash
git status --short
```

Expected: only intentionally ignored/untracked local scratch files may remain, such as `REASONIX.md` or `session`. Do not add those unless the user explicitly requests it.

- [ ] **Step 2: Push branch**

Run:

```bash
git push
```

Expected: branch pushes to `origin/stage-a-grounding-benchmark`.

- [ ] **Step 3: Final response**

Report:

- Plan executed.
- Commit hashes created in this plan.
- Verification commands and pass/fail status.
- GitHub push result.

---

## Self-Review

- Spec coverage: covers live progress, fake/real artifact consistency, web step update correctness, TDR display bug, docs/report updates, and final push.
- Experimental boundary: does not expose BRD nodes by default and does not enable heavy board analyze.
- No placeholders: all tasks include concrete files, tests, code snippets, commands, and expected outcomes.
- Type consistency: uses existing `WorkflowRunResult`, `WorkflowStepRun`, `import_cutout_summary_to_workflow_run()`, `DemoService`, and current workflow id `import_brd_cutout_sparam_tdr_v1`.
