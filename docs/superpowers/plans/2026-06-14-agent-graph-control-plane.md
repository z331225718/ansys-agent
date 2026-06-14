# Agent Graph Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 ansys-agent 从“单个 queued job 执行器”推进到可恢复、可审计、按 YAML 图推进的 Mission 控制平面。

**Architecture:** 先在 Python native runtime 内建立 GraphRun、NodeRun、ArtifactManifest、EvidencePackage 和 JobAttempt 的稳定契约与 SQLite 持久化，再把 `run_graph_once` 演进成顺序 DAG runner。BRD solve、谱数据分析和受控优化动作在控制平面稳定后接入，不让复杂 AEDT 行为掩盖编排契约缺口。

**Tech Stack:** Python 3.11+、dataclasses、enum、sqlite3、json、pytest、现有 `aedt_agent.agent` / `aedt_agent.infrastructure` runtime。

---

## 背景与当前基线

当前已经完成：

- 旧应用迁入 `aedt_agent.v0`，新入口为 `aedt_agent.agent`。
- Mission、Job、Event、Checkpoint、Approval、WorkerLease 已有契约和 SQLite store。
- BRD local-cut build worker 已能走 deterministic / real_build adapter。
- `docs/agent_templates/brd_local_cut_build.yaml` 已定义 planner、validator、worker、scorecard、approval gate。
- `run_graph_once` 能校验 queued job 的 capability 属于模板并执行一次 worker，然后运行 scorecard。

当前缺口：

- 没有 GraphRun / NodeRun 持久化，图运行无法恢复和重放。
- Handoff 只做 required fields 检查，未绑定 node、edge、artifact、evidence。
- Artifact 只是字符串列表，缺 kind、sha256、producer、retention。
- Evidence summary 未形成一等对象，也没有 token budget metadata。
- `retry_limit` 只是字段，缺 JobAttempt、retry decision、attempt event。
- CLI 不能查看 graph run、node run、artifact、evidence。
- `run_graph_once` 不是 DAG runner，没有消费 YAML edges、approval wait/resume、打回或并行。

本计划第一阶段不引入 Pi，不要求 VLM，不直接做完整 solve/优化；目标是先让控制平面可信。

## 文件结构

- `src/aedt_agent/agent/mission/contracts.py`  
  增加 GraphRunRecord、NodeRunRecord、ArtifactManifest、EvidencePackage、JobAttemptRecord 及相关枚举。

- `src/aedt_agent/agent/mission/__init__.py`  
  导出新增契约。

- `src/aedt_agent/infrastructure/sqlite_mission_store.py`  
  增加 graph_runs、node_runs、artifact_manifests、evidence_packages、job_attempts 表和 CRUD。

- `src/aedt_agent/agent/graph_runner.py`  
  保留 `run_graph_once` 兼容入口，新增顺序 DAG runner 的最小入口。

- `src/aedt_agent/agent/cli.py`  
  增加 graph-run status、events、artifacts、evidence 查询命令。

- `tests/test_agent_graph_control_contracts.py`  
  覆盖新增契约 JSON-ready 行为。

- `tests/test_agent_graph_control_store.py`  
  覆盖 SQLite 持久化、重启恢复、事件审计。

- `tests/test_agent_graph_runner_dag.py`  
  覆盖顺序 DAG runner、handoff、node run 状态。

- `tests/test_agent_cli_graph_control.py`  
  覆盖新增 CLI 查询面。

---

## Task 1：定义 Graph 控制平面契约

**Files:**
- Create: `tests/test_agent_graph_control_contracts.py`
- Modify: `src/aedt_agent/agent/mission/contracts.py`
- Modify: `src/aedt_agent/agent/mission/__init__.py`

- [ ] **Step 1：编写失败测试**

Create `tests/test_agent_graph_control_contracts.py`:

```python
from __future__ import annotations

from aedt_agent.agent.mission import (
    ArtifactManifest,
    EvidencePackage,
    GraphRunRecord,
    GraphRunStatus,
    JobAttemptRecord,
    JobAttemptStatus,
    NodeRunRecord,
    NodeRunStatus,
)


def test_graph_run_record_is_json_ready():
    graph_run = GraphRunRecord.create(
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        template_id="brd_local_cut_build",
        template_version=1,
        plan_version=2,
    )

    payload = graph_run.to_json_dict()

    assert payload["graph_run_id"] == "graph-run-1"
    assert payload["mission_id"] == "mission-1"
    assert payload["template_id"] == "brd_local_cut_build"
    assert payload["template_version"] == 1
    assert payload["plan_version"] == 2
    assert payload["status"] == "created"
    assert payload["started_at"] is None
    assert payload["completed_at"] is None


def test_node_run_record_captures_handoff_and_edge_decision():
    node_run = NodeRunRecord.create(
        node_run_id="node-run-1",
        graph_run_id="graph-run-1",
        mission_id="mission-1",
        node_id="real_build_worker",
        node_role="worker",
        node_kind="worker",
        sequence=3,
        input_payload={"layout_file": "case.brd"},
    )
    completed = node_run.with_completion(
        status=NodeRunStatus.SUCCEEDED,
        output_payload={"status": "built"},
        artifact_refs=["artifacts/model.aedt"],
        evidence_package_id="evidence-1",
        edge_decision="succeeded",
    )

    payload = completed.to_json_dict()

    assert payload["status"] == "succeeded"
    assert payload["output_payload"] == {"status": "built"}
    assert payload["artifact_refs"] == ["artifacts/model.aedt"]
    assert payload["evidence_package_id"] == "evidence-1"
    assert payload["edge_decision"] == "succeeded"
    assert payload["completed_at"] is not None


def test_artifact_manifest_records_provenance_and_checksum():
    artifact = ArtifactManifest.create(
        artifact_id="artifact-1",
        mission_id="mission-1",
        producer_kind="node",
        producer_id="node-run-1",
        path="artifacts/model.aedt",
        kind="aedt_project",
        sha256="a" * 64,
        size_bytes=123,
    )

    payload = artifact.to_json_dict()

    assert payload["producer_kind"] == "node"
    assert payload["producer_id"] == "node-run-1"
    assert payload["kind"] == "aedt_project"
    assert payload["sha256"] == "a" * 64
    assert payload["retention_policy"] == "mission"


def test_evidence_package_keeps_raw_data_as_artifact_refs():
    evidence = EvidencePackage.create(
        evidence_package_id="evidence-1",
        mission_id="mission-1",
        producer_kind="node",
        producer_id="node-run-1",
        summary={"spectral_summary": {"sample_count": 1341}},
        artifact_refs=["artifacts/channel.s4p"],
        token_budget={"summary_tokens": 1200, "raw_trace_policy": "artifact_only"},
    )

    payload = evidence.to_json_dict()

    assert payload["summary"]["spectral_summary"]["sample_count"] == 1341
    assert payload["artifact_refs"] == ["artifacts/channel.s4p"]
    assert payload["token_budget"]["raw_trace_policy"] == "artifact_only"
    assert "0.0,0.1,0.2" not in str(payload["summary"])


def test_job_attempt_record_captures_retry_decision():
    attempt = JobAttemptRecord.create(
        attempt_id="attempt-1",
        mission_id="mission-1",
        job_id="job-1",
        attempt_number=1,
        worker_id="worker-1",
    ).with_completion(
        status=JobAttemptStatus.FAILED,
        error={"error_class": "license_unavailable", "retryable": True},
        retry_decision="retry_with_backoff",
    )

    payload = attempt.to_json_dict()

    assert payload["status"] == "failed"
    assert payload["attempt_number"] == 1
    assert payload["retry_decision"] == "retry_with_backoff"
    assert payload["error"]["retryable"] is True
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_graph_control_contracts.py -q
```

Expected: FAIL，原因是新增契约尚未从 `aedt_agent.agent.mission` 导出。

- [ ] **Step 3：实现最小契约**

Modify `src/aedt_agent/agent/mission/contracts.py`:

```python
class GraphRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class NodeRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_APPROVAL = "waiting_approval"


class JobAttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
```

Add frozen dataclasses with `create()`, `to_json_dict()`, and completion helpers:

```python
@dataclass(frozen=True)
class GraphRunRecord:
    graph_run_id: str
    mission_id: str
    template_id: str
    template_version: int
    plan_version: int
    status: GraphRunStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    current_node_id: str | None = None
    error: JsonDict | None = None
```

```python
@dataclass(frozen=True)
class NodeRunRecord:
    node_run_id: str
    graph_run_id: str
    mission_id: str
    node_id: str
    node_role: str
    node_kind: str
    sequence: int
    status: NodeRunStatus
    input_payload: JsonDict
    output_payload: JsonDict
    artifact_refs: list[str]
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    evidence_package_id: str | None = None
    edge_decision: str | None = None
    error: JsonDict | None = None
```

```python
@dataclass(frozen=True)
class ArtifactManifest:
    artifact_id: str
    mission_id: str
    producer_kind: str
    producer_id: str
    path: str
    kind: str
    sha256: str
    size_bytes: int
    created_at: str
    metadata: JsonDict = field(default_factory=dict)
    retention_policy: str = "mission"
```

```python
@dataclass(frozen=True)
class EvidencePackage:
    evidence_package_id: str
    mission_id: str
    producer_kind: str
    producer_id: str
    summary: JsonDict
    artifact_refs: list[str]
    token_budget: JsonDict
    created_at: str
    metadata: JsonDict = field(default_factory=dict)
```

```python
@dataclass(frozen=True)
class JobAttemptRecord:
    attempt_id: str
    mission_id: str
    job_id: str
    attempt_number: int
    worker_id: str
    status: JobAttemptStatus
    started_at: str
    updated_at: str
    completed_at: str | None = None
    error: JsonDict | None = None
    retry_decision: str | None = None
```

Modify `src/aedt_agent/agent/mission/__init__.py` to export all new classes and enums.

- [ ] **Step 4：运行测试确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_graph_control_contracts.py -q
```

Expected: PASS。

- [ ] **Step 5：提交契约切片**

Run:

```powershell
git add src/aedt_agent/agent/mission/contracts.py src/aedt_agent/agent/mission/__init__.py tests/test_agent_graph_control_contracts.py
git commit -m "feat: define graph control plane contracts"
```

---

## Task 2：持久化 GraphRun、NodeRun、Artifact 和 Evidence

**Files:**
- Create: `tests/test_agent_graph_control_store.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`

- [ ] **Step 1：编写失败测试**

Create `tests/test_agent_graph_control_store.py` with tests for:

- `create_graph_run()` survives reopening the store.
- `create_node_run()` and `complete_node_run()` persist node status, output, artifact refs and edge decision.
- `create_artifact_manifest()` records checksum and producer.
- `create_evidence_package()` keeps raw trace data as artifact refs and persists token budget.
- graph control writes auditable events with monotonic mission sequence.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_graph_control_store.py -q
```

Expected: FAIL because store methods do not exist.

- [ ] **Step 2：实现 SQLite schema**

Modify `_init_schema()` and add tables:

```sql
CREATE TABLE IF NOT EXISTS graph_runs (...);
CREATE TABLE IF NOT EXISTS node_runs (...);
CREATE TABLE IF NOT EXISTS artifact_manifests (...);
CREATE TABLE IF NOT EXISTS evidence_packages (...);
CREATE TABLE IF NOT EXISTS job_attempts (...);
```

Use JSON text columns for payload, summary, token budget, metadata and errors.

- [ ] **Step 3：实现 CRUD 和 row mappers**

Add methods:

```python
create_graph_run(record)
get_graph_run(graph_run_id)
list_graph_runs(mission_id)
update_graph_run_status(...)
create_node_run(record)
complete_node_run(...)
list_node_runs(graph_run_id)
create_artifact_manifest(record)
list_artifact_manifests(mission_id)
create_evidence_package(record)
get_evidence_package(evidence_package_id)
list_evidence_packages(mission_id)
create_job_attempt(record)
complete_job_attempt(...)
list_job_attempts(job_id)
```

Each create/update appends an EventRecord using existing mission sequence logic.

- [ ] **Step 4：运行测试确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_graph_control_store.py tests\test_agent_sqlite_store.py -q
```

Expected: PASS。

- [ ] **Step 5：提交持久化切片**

Run:

```powershell
git add src/aedt_agent/infrastructure/sqlite_mission_store.py tests/test_agent_graph_control_store.py
git commit -m "feat: persist graph control records"
```

---

## Task 3：把 worker 执行绑定到 JobAttempt 与 ArtifactManifest

**Files:**
- Modify: `src/aedt_agent/agent/orchestrator/runtime.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- Create: `tests/test_agent_job_attempts.py`

- [ ] **Step 1：编写失败测试**

Create `tests/test_agent_job_attempts.py`:

- successful worker execution creates one succeeded JobAttempt.
- failed worker execution creates one failed JobAttempt with retry decision.
- artifact refs returned by worker become ArtifactManifest records when files exist.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_job_attempts.py -q
```

Expected: FAIL because runtime does not create attempts or manifests.

- [ ] **Step 2：实现 attempt lifecycle**

In `AgentRuntime.execute_next_job()`:

- create JobAttempt before calling registry.
- complete JobAttempt after worker returns.
- use `retry_decision="none"` for success.
- use `retry_decision="retry_available"` when failed result is retryable and attempts remain.
- use `retry_decision="no_retry"` otherwise.

- [ ] **Step 3：实现 artifact manifest registration**

For each returned artifact ref:

- if path exists, compute sha256 and size.
- if path does not exist, record sha256 as empty string and size as `0`, with metadata `{"exists": false}`.
- producer kind is `job`, producer id is `job_id`.

- [ ] **Step 4：运行测试确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_job_attempts.py tests\test_agent_runtime_service.py tests\test_agent_brd_local_cut_worker.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 attempt/artifact 切片**

Run:

```powershell
git add src/aedt_agent/agent/orchestrator/runtime.py src/aedt_agent/infrastructure/sqlite_mission_store.py tests/test_agent_job_attempts.py
git commit -m "feat: audit worker attempts and artifacts"
```

---

## Task 4：实现顺序 DAG Graph Runner v1

**Files:**
- Modify: `src/aedt_agent/agent/graph_runner.py`
- Create: `tests/test_agent_graph_runner_dag.py`

- [ ] **Step 1：编写失败测试**

Create `tests/test_agent_graph_runner_dag.py`:

- `run_graph_sequential()` creates a GraphRun.
- worker node execution creates a NodeRun linked to the GraphRun.
- output artifact refs and scorecard evidence are attached to the node.
- unknown capability fails before node execution.
- no queued job records a failed graph run with explicit error.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_graph_runner_dag.py -q
```

Expected: FAIL because `run_graph_sequential` does not exist.

- [ ] **Step 2：实现最小顺序 DAG**

Add `run_graph_sequential(runtime, mission_id, template, worker_id="graph")`.

Minimum behavior:

- create GraphRunRecord.
- find ready worker nodes in template order.
- for the first worker node with a queued job capability match, create NodeRunRecord.
- execute job via runtime.
- complete NodeRunRecord with job output, artifacts and edge decision.
- run scorecard and create EvidencePackage from scorecard report.
- complete GraphRunRecord as succeeded or failed.

Keep `run_graph_once()` as compatibility wrapper calling the new runner and returning the old report shape.

- [ ] **Step 3：运行测试确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_graph_runner.py tests\test_agent_graph_runner_dag.py tests\test_agent_cli_graph.py -q
```

Expected: PASS。

- [ ] **Step 4：提交 DAG runner 切片**

Run:

```powershell
git add src/aedt_agent/agent/graph_runner.py tests/test_agent_graph_runner_dag.py tests/test_agent_graph_runner.py
git commit -m "feat: run graph with auditable node records"
```

---

## Task 5：新增 Graph 控制面 CLI 查询

**Files:**
- Modify: `src/aedt_agent/agent/cli.py`
- Create: `tests/test_agent_cli_graph_control.py`

- [ ] **Step 1：编写失败测试**

Create `tests/test_agent_cli_graph_control.py`:

- `mission events --mission-id` returns ordered events.
- `mission graph-runs --mission-id` returns graph runs.
- `mission node-runs --graph-run-id` returns node runs.
- `mission artifacts --mission-id` returns artifact manifests.
- `mission evidence --mission-id` returns evidence packages.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_graph_control.py -q
```

Expected: FAIL because CLI commands do not exist.

- [ ] **Step 2：实现 CLI commands**

Add subcommands under `mission`:

```text
events --mission-id
graph-runs --mission-id
node-runs --graph-run-id
artifacts --mission-id
evidence --mission-id
```

Each command prints JSON with `ensure_ascii=True` and sorted keys, matching existing CLI style.

- [ ] **Step 3：运行测试确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_graph_control.py tests\test_agent_cli_graph.py tests\test_agent_cli_runtime.py -q
```

Expected: PASS。

- [ ] **Step 4：提交 CLI 切片**

Run:

```powershell
git add src/aedt_agent/agent/cli.py tests/test_agent_cli_graph_control.py
git commit -m "feat: expose graph control cli queries"
```

---

## Task 6：阶段回归与下一阶段入口

**Files:**
- Modify only if verification finds graph-control-specific defects.

- [ ] **Step 1：运行 graph control 重点测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_graph_control_contracts.py `
  tests\test_agent_graph_control_store.py `
  tests\test_agent_job_attempts.py `
  tests\test_agent_graph_runner.py `
  tests\test_agent_graph_runner_dag.py `
  tests\test_agent_cli_graph.py `
  tests\test_agent_cli_graph_control.py `
  tests\test_agent_scorecard.py `
  tests\test_agent_brd_local_cut_worker.py `
  tests\test_agent_brd_mission_runtime.py `
  tests\test_agent_runtime_service.py `
  tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 2：检查新 Agent runtime 不依赖 v0**

Run:

```powershell
rg -n "aedt_agent\.v0" src\aedt_agent\agent src\aedt_agent\infrastructure
```

Expected: no output。

- [ ] **Step 3：检查变更范围**

Run:

```powershell
git status --short
git diff --check
```

Expected:

- 新增/修改只涉及 graph control plan、`src/aedt_agent/agent`、`src/aedt_agent/infrastructure` 和 `tests/test_agent_*`。
- 不修改 `README.md`、`docs/ansys_agent_rfc_design_v1.md`、`.codegraph/`、`.reasonix/`、`scripts/screenshot_demo.py`、`src/aedt_agent.egg-info/` 等既有未跟踪/脏文件。
- `git diff --check` 无空白错误。

- [ ] **Step 4：记录下一阶段计划入口**

下一阶段计划应从这个控制平面继续实现：

- BRD solve/extract/score worker。
- S 参数/TDR Raw Trace Store。
- extrema-preserving 多尺度摘要。
- `query_sparameter_window` 预算查询接口。
- 一个受控 Action Schema 与 approval/rollback。

---

## 完成定义

本计划完成时必须同时满足：

1. GraphRun、NodeRun、ArtifactManifest、EvidencePackage、JobAttempt 都是 JSON-ready 契约。
2. SQLite store 能持久化并在重启后读取这些控制平面记录。
3. worker 执行产生 JobAttempt，artifact refs 被登记为 ArtifactManifest。
4. graph runner 至少能以顺序 DAG 方式创建 GraphRun 和 NodeRun，并把 scorecard 作为 EvidencePackage 留痕。
5. CLI 能查询 events、graph runs、node runs、artifacts、evidence。
6. 现有 `run_graph_once` 兼容测试继续通过。
7. 新 Agent runtime 不依赖 `aedt_agent.v0`。
8. raw S 参数/TDR 仍只通过 artifact refs 进入证据，不进入 LLM summary。

## 后续计划

控制平面稳定后，进入 `BRD Solve Evidence Pipeline`：把 solve、Touchstone/TDR extraction、spectral analyzer、bounded summary、window query 和 deterministic evaluator 接入 Mission graph。Pi / 外部 agent framework 仍放在 native runtime 独立闭环之后评估。
