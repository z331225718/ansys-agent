# BRD Local-Cut Mission Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 Agent Runtime 接上第一个真实工程垂直切片：BRD local-cut build Mission，从创建 Mission 到 Job 执行、artifact 持久化、候选端口审批、审批后恢复，形成可审计闭环。

**Architecture:** 新能力只放在 `aedt_agent.agent` 与共享 `aedt_agent.layout` 之间，不依赖 `aedt_agent.v0`。第一版 Worker 使用共享 local-cut/port/workflow artifact 模型生成 deterministic build artifact 与 bounded summary；后续真实 PyEDB/AEDT adapter 通过同一 Worker capability 接入。

**Tech Stack:** Python 3.11+ 标准库、`dataclasses`、`json`、`pathlib`、现有 SQLite Runtime、现有 `aedt_agent.layout` 工具、`pytest`。

---

## 当前基线

本计划基于 `2026-06-13-agent-runtime-foundation.md` 完成后的 Runtime：

- `AgentRuntime` 已支持 Mission、Job、Event、Checkpoint、Worker lease。
- `ApprovalService` 已支持 request / approve / reject。
- `aedt-agent mission create/status/cancel` 已使用本地 SQLite。
- 全量测试仍有 9 个已登记既有失败，本计划不得扩大失败集合。

---

## 目标文件结构

- `src/aedt_agent/agent/workers/brd_local_cut.py`
  定义 `brd.local_cut.build` capability、输入校验、artifact 写入、bounded summary、approval requirement。

- `src/aedt_agent/agent/workers/__init__.py`
  导出 BRD worker 注册函数与 capability 常量。

- `src/aedt_agent/agent/orchestrator/runtime.py`
  增加 approval-aware job 执行结果处理：Worker 可请求 approval，Runtime 写 checkpoint 并把 Mission 置为 waiting_approval。

- `src/aedt_agent/agent/cli.py`
  扩展 `mission create/run/status/approve`，支持 BRD local-cut 参数与审批恢复。

- `tests/test_agent_brd_local_cut_worker.py`
  覆盖 Worker 输入、artifact、bounded summary、不依赖 v0。

- `tests/test_agent_brd_mission_runtime.py`
  覆盖 Runtime 执行 job、生成 checkpoint、ambiguous port 进入 approval、approve 后恢复。

- `tests/test_agent_cli_brd_mission.py`
  覆盖 CLI create/run/status/approve 的最小闭环。

---

## Task 1：定义 BRD local-cut Worker 合同和 artifact 输出

**Files:**
- Create: `tests/test_agent_brd_local_cut_worker.py`
- Create: `src/aedt_agent/agent/workers/brd_local_cut.py`
- Modify: `src/aedt_agent/agent/workers/__init__.py`

- [ ] **Step 1：编写 Worker 合同测试**

Create `tests/test_agent_brd_local_cut_worker.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    build_brd_local_cut_job_input,
    run_brd_local_cut_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext


def _job(tmp_path: Path, **overrides) -> JobRecord:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    payload = build_brd_local_cut_job_input(
        layout_file=layout_file,
        signal_nets=["56G_TX0_P", "56G_TX0_N"],
        reference_nets=["GND"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        artifact_dir=tmp_path / "artifacts",
        target_metrics=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        port_candidates={"status": "ready", "recommended_endpoints": [{"name": "U1"}, {"name": "J1"}]},
        **overrides,
    )
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=BRD_LOCAL_CUT_BUILD_CAPABILITY,
        idempotency_key="mission-1:brd-local-cut:0",
        input_payload=payload,
        timeout_seconds=300,
        retry_limit=1,
    )


def test_brd_local_cut_worker_writes_artifacts_and_bounded_summary(tmp_path):
    result = run_brd_local_cut_worker(_job(tmp_path), WorkerContext("worker-1"))

    summary_path = Path(result["artifact_refs"][0])
    workflow_path = Path(result["artifact_refs"][1])

    assert result["status"] == "model_review"
    assert summary_path.name == "brd_local_cut_summary.json"
    assert workflow_path.name == "workflow_run.json"
    assert json.loads(summary_path.read_text(encoding="utf-8"))["local_cut_region"]["unit"] == "mil"
    assert result["evidence_summary"]["raw_sparameters"] == "artifact_only"
    assert len(json.dumps(result["evidence_summary"])) < 2000


def test_brd_local_cut_worker_requires_user_bbox(tmp_path):
    with pytest.raises(ValueError, match="local_cut_region is required"):
        run_brd_local_cut_worker(_job(tmp_path, local_cut_region=None), WorkerContext("worker-1"))


def test_ambiguous_port_candidates_request_approval(tmp_path):
    job = _job(
        tmp_path,
        port_candidates={
            "status": "ambiguous",
            "candidates": [{"id": "p1", "label": "TX0-GND"}, {"id": "p2", "label": "TX1-GND"}],
        },
    )

    result = run_brd_local_cut_worker(job, WorkerContext("worker-1"))

    assert result["status"] == "waiting_approval"
    assert result["approval_required"]["reason"] == "port_candidates_ambiguous"
    assert [option["id"] for option in result["approval_required"]["options"]] == ["p1", "p2"]
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_local_cut_worker.py -q
```

Expected: FAIL，原因是 BRD worker 尚未存在。

- [ ] **Step 3：实现 Worker**

Create `src/aedt_agent/agent/workers/brd_local_cut.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext
from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region
from aedt_agent.layout.workflow_run import import_cutout_summary_to_workflow_run


BRD_LOCAL_CUT_BUILD_CAPABILITY = "brd.local_cut.build"


def build_brd_local_cut_job_input(
    *,
    layout_file: str | Path,
    signal_nets: list[str],
    reference_nets: list[str],
    local_cut_region: dict[str, Any] | None,
    artifact_dir: str | Path,
    target_metrics: list[dict[str, Any]] | None = None,
    port_candidates: dict[str, Any] | None = None,
    approved_port_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "layout_file": str(layout_file),
        "signal_nets": list(signal_nets),
        "reference_nets": list(reference_nets),
        "local_cut_region": local_cut_region,
        "artifact_dir": str(artifact_dir),
        "target_metrics": list(target_metrics or []),
        "port_candidates": port_candidates or {"status": "ready", "recommended_endpoints": []},
        "approved_port_selection": approved_port_selection or {},
    }


def run_brd_local_cut_worker(job: JobRecord, context: WorkerContext) -> dict[str, Any]:
    payload = dict(job.input_payload)
    region = parse_local_cut_region(payload.get("local_cut_region"))
    artifact_dir = Path(str(payload["artifact_dir"]))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    port_candidates = dict(payload.get("port_candidates") or {})
    approval_required = _approval_required(port_candidates)

    summary = _summary_payload(job, context, payload, region, approval_required)
    summary_path = artifact_dir / "brd_local_cut_summary.json"
    workflow_path = artifact_dir / "workflow_run.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    import_cutout_summary_to_workflow_run(summary).write_json(workflow_path)

    output = {
        "status": "waiting_approval" if approval_required else "model_review",
        "artifact_refs": [str(summary_path), str(workflow_path)],
        "summary_path": str(summary_path),
        "workflow_run_path": str(workflow_path),
        "evidence_summary": _bounded_evidence_summary(summary),
    }
    if approval_required:
        output["approval_required"] = approval_required
    return output


def _approval_required(port_candidates: dict[str, Any]) -> dict[str, Any] | None:
    if port_candidates.get("status") not in {"ambiguous", "needs_user_hint"}:
        return None
    options = list(port_candidates.get("candidates") or port_candidates.get("recommended_endpoints") or [])
    return {"reason": "port_candidates_ambiguous", "options": options}


def _summary_payload(
    job: JobRecord,
    context: WorkerContext,
    payload: dict[str, Any],
    region: dict[str, Any],
    approval_required: dict[str, Any] | None,
) -> dict[str, Any]:
    artifact_dir = Path(str(payload["artifact_dir"]))
    status = "waiting_approval" if approval_required else "succeeded"
    return {
        "status": status,
        "adapter": "agent_brd_local_cut",
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "worker_id": context.worker_id,
        "layout_file": str(payload["layout_file"]),
        "signal_nets": list(payload.get("signal_nets") or []),
        "reference_nets": list(payload.get("reference_nets") or []),
        "local_cut_region": region,
        "local_cut_polygon": bbox_to_polygon(region),
        "port_candidates": dict(payload.get("port_candidates") or {}),
        "approved_port_selection": dict(payload.get("approved_port_selection") or {}),
        "target_metrics": list(payload.get("target_metrics") or []),
        "edb_path": str(artifact_dir / "local_cut.aedb"),
        "aedt_project": str(artifact_dir / "local_cut.aedt"),
        "touchstone": str(artifact_dir / "model_review.s2p"),
        "tdr": str(artifact_dir / "model_review_tdr.csv"),
        "layout_solve": {"status": "skipped", "reason": "model_review_only"},
        "steps": _steps(status),
    }


def _steps(status: str) -> list[dict[str, Any]]:
    return [
        {"id": "import_layout_file", "label": "Record BRD file", "status": "succeeded"},
        {"id": "select_layout_nets", "label": "Record target nets", "status": "succeeded"},
        {"id": "create_layout_cutout", "label": "Record local cut bbox", "status": "succeeded"},
        {"id": "locate_layout_port_candidates", "label": "Evaluate port candidates", "status": status},
    ]


def _bounded_evidence_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": summary["status"],
        "layout_file": summary["layout_file"],
        "signal_nets": summary["signal_nets"],
        "reference_nets": summary["reference_nets"],
        "local_cut_region": summary["local_cut_region"],
        "port_candidate_status": summary.get("port_candidates", {}).get("status", "unknown"),
        "target_metrics": summary["target_metrics"],
        "aedt_project": summary["aedt_project"],
        "touchstone": summary["touchstone"],
        "tdr": summary["tdr"],
        "raw_sparameters": "artifact_only",
        "raw_tdr": "artifact_only",
    }
```

Modify `src/aedt_agent/agent/workers/__init__.py` to export:

```python
from aedt_agent.agent.workers.brd_local_cut import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    build_brd_local_cut_job_input,
    run_brd_local_cut_worker,
)
```

and include the names in `__all__`.

- [ ] **Step 4：运行 Worker 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_local_cut_worker.py tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 Worker**

```powershell
git add src/aedt_agent/agent/workers/brd_local_cut.py src/aedt_agent/agent/workers/__init__.py tests/test_agent_brd_local_cut_worker.py
git commit -m "feat: add brd local cut mission worker"
```

---

## Task 2：让 Runtime 识别 approval_required 输出

**Files:**
- Create: `tests/test_agent_brd_mission_runtime.py`
- Modify: `src/aedt_agent/agent/orchestrator/runtime.py`
- Modify: `src/aedt_agent/agent/workers/brd_local_cut.py`

- [ ] **Step 1：编写 Runtime 垂直切片测试**

Create `tests/test_agent_brd_mission_runtime.py`:

```python
from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.mission import ApprovalDecision, JobStatus, MissionState
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    InMemoryWorkerRegistry,
    build_brd_local_cut_job_input,
    run_brd_local_cut_worker,
)
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def _payload(tmp_path: Path, *, port_status: str = "ready") -> dict:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    return build_brd_local_cut_job_input(
        layout_file=layout_file,
        signal_nets=["56G_TX0_P", "56G_TX0_N"],
        reference_nets=["GND"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        artifact_dir=tmp_path / "artifacts",
        target_metrics=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        port_candidates={"status": port_status, "candidates": [{"id": "p1", "label": "TX0-GND"}]},
    )


def test_brd_mission_reaches_model_review_checkpoint(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("构建 local cut", [], [])
    job = runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))

    result = runtime.execute_next_job(mission.mission_id, "worker-1")

    assert result.status == JobStatus.SUCCEEDED
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert runtime.get_mission(mission.mission_id).state == MissionState.EVALUATING
    events = [event.event_type.value for event in runtime.list_events(mission.mission_id)]
    assert "checkpoint_created" in events


def test_ambiguous_ports_move_mission_to_approval_and_resume_without_duplicate_job(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("构建 local cut", [], [])
    job = runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path, port_status="ambiguous"))

    result = runtime.execute_next_job(mission.mission_id, "worker-1")

    assert result.status == JobStatus.SUCCEEDED
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert runtime.get_mission(mission.mission_id).state == MissionState.WAITING_APPROVAL

    approval_events = [event for event in runtime.list_events(mission.mission_id) if event.event_type.value == "approval_requested"]
    approval_id = approval_events[-1].payload["approval_id"]
    approved = ApprovalService(runtime.store).approve(approval_id, selected_option_id="p1", comment="确认端口")

    assert approved.decision == ApprovalDecision.APPROVED
    assert runtime.get_mission(mission.mission_id).state == MissionState.WAITING_WORKER
    assert len(runtime.list_jobs(mission.mission_id)) == 1
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_mission_runtime.py -q
```

Expected: FAIL，原因是 Runtime 尚未处理 `approval_required`，也不会更新 Mission state。

- [ ] **Step 3：扩展 Runtime**

Modify `AgentRuntime.execute_next_job`:

```python
        if result.status == JobStatus.SUCCEEDED:
            completed = self.store.complete_job(job.job_id, result.output_payload, result.artifact_refs)
            self.store.create_checkpoint(mission_id, job.job_id, result.artifact_refs, {"output": result.output_payload})
            approval_required = result.output_payload.get("approval_required")
            if isinstance(approval_required, dict):
                from aedt_agent.agent.approvals import ApprovalService

                ApprovalService(self.store).request_approval(
                    mission_id,
                    str(approval_required.get("reason") or "approval_required"),
                    list(approval_required.get("options") or []),
                )
            else:
                self.store.update_mission_state(mission_id, MissionState.EVALUATING)
```

Also import `MissionState`.

- [ ] **Step 4：运行 Runtime 垂直测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_mission_runtime.py tests\test_agent_runtime_service.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 Runtime approval path**

```powershell
git add src/aedt_agent/agent/orchestrator/runtime.py tests/test_agent_brd_mission_runtime.py
git commit -m "feat: route brd mission approvals through runtime"
```

---

## Task 3：扩展 CLI 支持 BRD Mission run/approve

**Files:**
- Create: `tests/test_agent_cli_brd_mission.py`
- Modify: `src/aedt_agent/agent/cli.py`

- [ ] **Step 1：编写 CLI 垂直切片测试**

Create `tests/test_agent_cli_brd_mission.py`:

```python
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aedt_agent.agent.cli", "--db", str(tmp_path / "mission.db"), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_runs_brd_local_cut_mission_to_model_review(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "构建 local cut",
        "--brd-local-cut",
        "--layout-file",
        str(layout_file),
        "--signal-net",
        "56G_TX0_P",
        "--signal-net",
        "56G_TX0_N",
        "--reference-net",
        "GND",
        "--bbox",
        "mil,1,2,3,4",
        "--criterion",
        "s21_db_at_56g>=-8",
    )
    mission_id = json.loads(created.stdout)["mission_id"]

    ran = _run(tmp_path, "mission", "run", "--mission-id", mission_id)
    status = _run(tmp_path, "mission", "status", "--mission-id", mission_id)

    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout)["status"] == "succeeded"
    payload = json.loads(status.stdout)
    assert payload["state"] == "evaluating"
    assert payload["jobs"][0]["capability"] == "brd.local_cut.build"
    assert payload["jobs"][0]["output_payload"]["evidence_summary"]["raw_sparameters"] == "artifact_only"
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_brd_mission.py -q
```

Expected: FAIL，原因是 CLI 尚不支持 BRD 参数和 run。

- [ ] **Step 3：实现 CLI BRD 参数和 run**

Modify `src/aedt_agent/agent/cli.py`:

- `mission create` 增加：

```python
    create.add_argument("--brd-local-cut", action="store_true")
    create.add_argument("--layout-file")
    create.add_argument("--signal-net", action="append", default=[])
    create.add_argument("--reference-net", action="append", default=[])
    create.add_argument("--bbox")
    create.add_argument("--artifact-dir")
```

- 在 create 分支中，如果 `args.brd_local_cut`，创建 mission 后创建 BRD job：

```python
        if args.brd_local_cut:
            from aedt_agent.agent.workers import BRD_LOCAL_CUT_BUILD_CAPABILITY, build_brd_local_cut_job_input

            artifact_dir = Path(args.artifact_dir) if args.artifact_dir else args.db.parent / mission.mission_id
            runtime.create_job(
                mission.mission_id,
                BRD_LOCAL_CUT_BUILD_CAPABILITY,
                "brd-local-cut:0",
                build_brd_local_cut_job_input(
                    layout_file=args.layout_file,
                    signal_nets=args.signal_net,
                    reference_nets=args.reference_net or ["GND"],
                    local_cut_region=_parse_bbox(args.bbox),
                    artifact_dir=artifact_dir,
                    target_metrics=criteria,
                ),
            )
```

- 在 run 分支中注册 worker 并执行：

```python
        from aedt_agent.agent.workers import BRD_LOCAL_CUT_BUILD_CAPABILITY, InMemoryWorkerRegistry, run_brd_local_cut_worker

        registry = InMemoryWorkerRegistry()
        registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
        runtime = AgentRuntime(SQLiteMissionStore(args.db), registry=registry)
        result = runtime.execute_next_job(args.mission_id, worker_id="cli")
        _print_json({
            "job_id": result.job_id,
            "status": result.status.value,
            "output_payload": result.output_payload,
            "artifact_refs": result.artifact_refs,
        })
        return 0 if result.status.value == "succeeded" else 2
```

- `status` payload 增加 jobs：

```python
        payload["jobs"] = [job.to_json_dict() for job in runtime.list_jobs(args.mission_id)]
```

- 添加 `_parse_bbox`：

```python
def _parse_bbox(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    unit, x_min, y_min, x_max, y_max = [item.strip() for item in value.split(",", 4)]
    return {"type": "bbox", "unit": unit, "x_min": float(x_min), "y_min": float(y_min), "x_max": float(x_max), "y_max": float(y_max)}
```

- [ ] **Step 4：运行 CLI 垂直测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_brd_mission.py tests\test_agent_cli_runtime.py tests\test_agent_cli_boundary.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 CLI BRD slice**

```powershell
git add src/aedt_agent/agent/cli.py tests/test_agent_cli_brd_mission.py
git commit -m "feat: run brd local cut missions from cli"
```

---

## Task 4：最终回归与审计

**Files:**
- Modify only if verification finds Phase 3 defects.

- [ ] **Step 1：运行 Phase 3 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_brd_local_cut_worker.py `
  tests\test_agent_brd_mission_runtime.py `
  tests\test_agent_cli_brd_mission.py `
  tests\test_agent_runtime_service.py `
  tests\test_agent_cli_runtime.py `
  tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 2：检查新 Agent 不依赖 v0**

Run:

```powershell
rg -n "aedt_agent\.v0|aedt_agent\.demo|aedt_agent\.benchmark|aedt_agent\.chat|aedt_agent\.evolution" src\aedt_agent\agent src\aedt_agent\infrastructure
```

Expected: 无输出。

- [ ] **Step 3：CLI smoke**

Run:

```powershell
$env:PYTHONPATH='src'
$db = Join-Path $env:TEMP "brd-mission-smoke.db"
$layout = Join-Path $env:TEMP "brd-mission-smoke.brd"
"brd" | Set-Content -LiteralPath $layout
$created = .\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $db mission create --goal "smoke brd" --brd-local-cut --layout-file $layout --signal-net 56G_TX0_P --signal-net 56G_TX0_N --reference-net GND --bbox "mil,1,2,3,4"
$missionId = ($created | ConvertFrom-Json).mission_id
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $db mission run --mission-id $missionId
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $db mission status --mission-id $missionId
```

Expected: run 输出 `status=succeeded`；status 输出 `state=evaluating`，jobs 中有 `brd.local_cut.build`，artifact refs 指向 `brd_local_cut_summary.json` 与 `workflow_run.json`。

- [ ] **Step 4：运行 Runtime + 迁移重点测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_runtime_contracts.py `
  tests\test_agent_sqlite_store.py `
  tests\test_agent_state_machine.py `
  tests\test_agent_worker_registry.py `
  tests\test_agent_runtime_service.py `
  tests\test_agent_approval_service.py `
  tests\test_agent_cli_runtime.py `
  tests\test_agent_cli_boundary.py `
  tests\test_v0_namespace_compatibility.py `
  tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 5：运行全量测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: 失败集合不得超过已登记 9 个基线失败。

- [ ] **Step 6：检查 Git 变更范围**

Run:

```powershell
git status --short
git diff --check
git diff --name-only HEAD~3..HEAD
```

Expected: 新增/修改只涉及 `docs/superpowers/plans`、`src/aedt_agent/agent`、`tests/test_agent_*`；不修改 `aedt_agent.v0`、README、RFC、截图脚本、原始 benchmark artifacts。

---

## 完成定义

1. `brd.local_cut.build` Worker 能从 Job 输入生成 local-cut artifact、workflow_run artifact 和 bounded evidence summary。
2. Worker 不依赖 `aedt_agent.v0`，不要求 VLM，不把 S 参数/TDR 原文塞进输出。
3. Runtime 执行 BRD Job 后写 checkpoint，并将普通 model-review Mission 推进到 `evaluating`。
4. Ambiguous port candidates 会创建 Approval，并将 Mission 置为 `waiting_approval`。
5. Approval 后 Mission 回到 `waiting_worker`，且不会重复创建已经完成的 build Job。
6. CLI 可创建 BRD local-cut Mission、运行 Job、查看 status/jobs/events。
7. 全量测试失败集合不扩大。

## 后续计划

下一份计划应实现真实 PyEDB/AEDT adapter：

```text
brd.local_cut.build(fake deterministic)
    -> brd.local_cut.build(recorded)
    -> brd.local_cut.build(real PyEDB/HFSS 3D Layout)
    -> evaluator reads artifact summaries
    -> bounded S-parameter/TDR window query
```

真实 adapter 必须继续复用本计划的 Worker capability 和 artifact summary 合同。
