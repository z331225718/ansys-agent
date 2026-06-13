# Agent Runtime Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 ansys-agent 的 Mission / Job / Event / Checkpoint / Approval / Worker 最小运行时基础，使 Mission 可持久化、可恢复、可审计，并为后续 BRD local-cut Agent 垂直切片提供稳定接口。

**Architecture:** `aedt_agent.agent` 定义运行时契约、状态机和编排服务；`aedt_agent.infrastructure` 提供 SQLite 持久化适配器；`aedt_agent.v0` 继续保持旧应用兼容，不被新运行时依赖。第一阶段只实现通用 Agent Runtime，不包装 BRD local-cut Worker，不引入 Pi，不要求 VLM。

**Tech Stack:** Python 3.11+ 标准库、`dataclasses`、`enum`、`sqlite3`、`json`、`pytest`。

---

## 当前基线

本计划基于 `codex/agent-first-namespace` 分支上的 Phase 1 结果继续执行。全量测试当前有 9 个已登记既有失败：

- `tests/test_harness_generator.py::test_harness_generator_expands_config_placeholders`
- `tests/test_import_cutout_demo.py::test_cadence_launcher_exports_aedt_roots_from_launcher`
- `tests/test_import_cutout_demo.py::test_cadence_launcher_ignores_multiline_library_assignment`
- `tests/test_node_evolution.py::test_miner_extracts_failures_and_subgraphs_from_stage_b_report`
- `tests/test_node_evolution.py::test_miner_extracts_node_usage_from_audit_jsonl`
- `tests/test_node_evolution.py::test_build_evolution_report_keeps_evidence_and_proposals`
- `tests/test_stage_b_presentation.py::test_stage_b_presentation_combines_groups_and_scrubs_artifacts`
- `tests/test_stage_c1_demo_web.py::test_dispatch_demo_request_starts_import_cutout_agent_run_with_fake_adapter`
- `tests/test_stage_c_demo_scripts.py::test_generate_node_evolution_report_writes_json`

本计划验收标准是：新增运行时测试通过，迁移重点测试通过，全量测试失败集合不得扩大。

---

## 目标文件结构

- `src/aedt_agent/agent/mission/contracts.py`  
  定义 Mission、Job、Event、Checkpoint、Approval、Worker lease 的纯数据契约和枚举。

- `src/aedt_agent/agent/mission/__init__.py`  
  导出稳定契约类型。

- `src/aedt_agent/agent/orchestrator/state_machine.py`  
  定义 Mission 状态迁移表和验证函数。

- `src/aedt_agent/agent/orchestrator/runtime.py`  
  提供最小 Mission Runtime 服务，组合 store、状态机、幂等 Job、lease recovery、approval 操作。

- `src/aedt_agent/agent/workers/registry.py`  
  定义 Worker 协议、内存 Worker 注册表、Job 执行封装和结构化错误分类。

- `src/aedt_agent/agent/approvals/service.py`  
  定义 approval wait / approve / reject / resume 的小服务。

- `src/aedt_agent/infrastructure/sqlite_mission_store.py`  
  SQLite schema、事务写入、JSON 编码、Mission/Job/Event/Checkpoint/Approval/Lease 持久化。

- `src/aedt_agent/agent/cli.py`  
  将 `runtime_unavailable` 占位替换成可用的本地 SQLite Mission CLI。

- `tests/test_agent_runtime_contracts.py`
- `tests/test_agent_sqlite_store.py`
- `tests/test_agent_state_machine.py`
- `tests/test_agent_worker_registry.py`
- `tests/test_agent_runtime_service.py`
- `tests/test_agent_approval_service.py`
- `tests/test_agent_cli_runtime.py`

---

## Task 1：定义 Runtime 契约

**Files:**
- Create: `tests/test_agent_runtime_contracts.py`
- Create: `src/aedt_agent/agent/mission/contracts.py`
- Modify: `src/aedt_agent/agent/mission/__init__.py`

- [ ] **Step 1：编写契约测试**

Create `tests/test_agent_runtime_contracts.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from aedt_agent.agent.mission import (
    ApprovalDecision,
    ApprovalRequest,
    CheckpointRecord,
    EngineeringConstraint,
    EventRecord,
    EventType,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
    WorkerLease,
    utc_now_iso,
)


def test_mission_record_is_json_ready_and_text_only_by_default():
    mission = MissionRecord.create(
        mission_id="mission-1",
        user_goal="优化 56G 通道插损",
        acceptance_criteria=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        constraints=[EngineeringConstraint(name="max_iterations", value=3)],
    )

    payload = mission.to_json_dict()

    assert payload["mission_id"] == "mission-1"
    assert payload["state"] == "created"
    assert payload["vision_required"] is False
    assert payload["acceptance_criteria"][0]["metric"] == "s21_db_at_56g"
    assert payload["constraints"][0] == {"name": "max_iterations", "value": 3}


def test_job_record_carries_idempotency_and_structured_io():
    job = JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability="fake.build_model",
        idempotency_key="mission-1:build:0",
        input_payload={"layout_file": "case.brd"},
        timeout_seconds=120,
        retry_limit=2,
    )

    payload = job.to_json_dict()

    assert payload["status"] == "queued"
    assert payload["idempotency_key"] == "mission-1:build:0"
    assert payload["input_payload"] == {"layout_file": "case.brd"}
    assert payload["output_payload"] == {}
    assert payload["error"] is None


def test_event_checkpoint_approval_and_lease_are_json_ready():
    created_at = datetime(2026, 6, 13, 12, 0, tzinfo=UTC).isoformat()
    event = EventRecord(
        event_id="event-1",
        mission_id="mission-1",
        event_type=EventType.MISSION_CREATED,
        sequence=1,
        created_at=created_at,
        payload={"state": "created"},
    )
    checkpoint = CheckpointRecord(
        checkpoint_id="checkpoint-1",
        mission_id="mission-1",
        job_id="job-1",
        created_at=created_at,
        artifact_refs=["artifacts/model.aedt"],
        payload={"model_state": "built"},
    )
    approval = ApprovalRequest.create(
        approval_id="approval-1",
        mission_id="mission-1",
        reason="端口候选不唯一",
        options=[{"id": "p1", "label": "TX0-GND"}],
    )
    lease = WorkerLease(
        lease_id="lease-1",
        job_id="job-1",
        worker_id="worker-1",
        acquired_at=created_at,
        expires_at=created_at,
        released_at=None,
    )

    assert event.to_json_dict()["event_type"] == "mission_created"
    assert checkpoint.to_json_dict()["artifact_refs"] == ["artifacts/model.aedt"]
    assert approval.to_json_dict()["decision"] == "pending"
    assert approval.decision == ApprovalDecision.PENDING
    assert lease.to_json_dict()["worker_id"] == "worker-1"


def test_utc_now_iso_is_timezone_aware():
    value = utc_now_iso()

    assert value.endswith("+00:00")
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_runtime_contracts.py -q
```

Expected: FAIL，原因是 `aedt_agent.agent.mission` 尚未导出这些契约。

- [ ] **Step 3：实现契约**

Create `src/aedt_agent/agent/mission/contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


JsonDict = dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MissionState(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    WAITING_WORKER = "waiting_worker"
    WAITING_APPROVAL = "waiting_approval"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class EventType(StrEnum):
    MISSION_CREATED = "mission_created"
    MISSION_STATE_CHANGED = "mission_state_changed"
    JOB_CREATED = "job_created"
    JOB_LEASED = "job_leased"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"
    CHECKPOINT_CREATED = "checkpoint_created"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ErrorClass(StrEnum):
    INVALID_INPUT = "invalid_input"
    LICENSE_UNAVAILABLE = "license_unavailable"
    WORKER_CRASH = "worker_crash"
    TIMEOUT = "timeout"
    INVALID_MODEL = "invalid_model"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EngineeringConstraint:
    name: str
    value: Any

    def to_json_dict(self) -> JsonDict:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True)
class MissionRecord:
    mission_id: str
    user_goal: str
    acceptance_criteria: list[JsonDict]
    constraints: list[EngineeringConstraint]
    state: MissionState
    plan_version: int
    created_at: str
    updated_at: str
    vision_required: bool = False
    final_outcome: JsonDict | None = None

    @classmethod
    def create(
        cls,
        mission_id: str,
        user_goal: str,
        acceptance_criteria: list[JsonDict],
        constraints: list[EngineeringConstraint] | None = None,
        vision_required: bool = False,
    ) -> "MissionRecord":
        now = utc_now_iso()
        return cls(
            mission_id=mission_id,
            user_goal=user_goal,
            acceptance_criteria=acceptance_criteria,
            constraints=constraints or [],
            state=MissionState.CREATED,
            plan_version=1,
            created_at=now,
            updated_at=now,
            vision_required=vision_required,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "mission_id": self.mission_id,
            "user_goal": self.user_goal,
            "acceptance_criteria": self.acceptance_criteria,
            "constraints": [constraint.to_json_dict() for constraint in self.constraints],
            "state": self.state.value,
            "plan_version": self.plan_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "vision_required": self.vision_required,
            "final_outcome": self.final_outcome,
        }


@dataclass(frozen=True)
class JobError:
    error_class: ErrorClass
    message: str
    retryable: bool
    details: JsonDict = field(default_factory=dict)

    def to_json_dict(self) -> JsonDict:
        return {
            "error_class": self.error_class.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    mission_id: str
    capability: str
    idempotency_key: str
    input_payload: JsonDict
    output_payload: JsonDict
    artifact_refs: list[str]
    timeout_seconds: int
    retry_limit: int
    status: JobStatus
    created_at: str
    updated_at: str
    error: JobError | None = None

    @classmethod
    def create(
        cls,
        job_id: str,
        mission_id: str,
        capability: str,
        idempotency_key: str,
        input_payload: JsonDict,
        timeout_seconds: int,
        retry_limit: int,
    ) -> "JobRecord":
        now = utc_now_iso()
        return cls(
            job_id=job_id,
            mission_id=mission_id,
            capability=capability,
            idempotency_key=idempotency_key,
            input_payload=input_payload,
            output_payload={},
            artifact_refs=[],
            timeout_seconds=timeout_seconds,
            retry_limit=retry_limit,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "job_id": self.job_id,
            "mission_id": self.mission_id,
            "capability": self.capability,
            "idempotency_key": self.idempotency_key,
            "input_payload": self.input_payload,
            "output_payload": self.output_payload,
            "artifact_refs": self.artifact_refs,
            "timeout_seconds": self.timeout_seconds,
            "retry_limit": self.retry_limit,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": None if self.error is None else self.error.to_json_dict(),
        }


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    mission_id: str
    event_type: EventType
    sequence: int
    created_at: str
    payload: JsonDict

    def to_json_dict(self) -> JsonDict:
        return {
            "event_id": self.event_id,
            "mission_id": self.mission_id,
            "event_type": self.event_type.value,
            "sequence": self.sequence,
            "created_at": self.created_at,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class CheckpointRecord:
    checkpoint_id: str
    mission_id: str
    job_id: str
    created_at: str
    artifact_refs: list[str]
    payload: JsonDict

    def to_json_dict(self) -> JsonDict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "mission_id": self.mission_id,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "artifact_refs": self.artifact_refs,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    mission_id: str
    reason: str
    options: list[JsonDict]
    decision: ApprovalDecision
    created_at: str
    resolved_at: str | None = None
    selected_option_id: str | None = None
    comment: str | None = None

    @classmethod
    def create(
        cls,
        approval_id: str,
        mission_id: str,
        reason: str,
        options: list[JsonDict],
    ) -> "ApprovalRequest":
        return cls(
            approval_id=approval_id,
            mission_id=mission_id,
            reason=reason,
            options=options,
            decision=ApprovalDecision.PENDING,
            created_at=utc_now_iso(),
        )

    def to_json_dict(self) -> JsonDict:
        return {
            "approval_id": self.approval_id,
            "mission_id": self.mission_id,
            "reason": self.reason,
            "options": self.options,
            "decision": self.decision.value,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "selected_option_id": self.selected_option_id,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class WorkerLease:
    lease_id: str
    job_id: str
    worker_id: str
    acquired_at: str
    expires_at: str
    released_at: str | None

    def to_json_dict(self) -> JsonDict:
        return {
            "lease_id": self.lease_id,
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "released_at": self.released_at,
        }
```

Modify `src/aedt_agent/agent/mission/__init__.py`:

```python
"""Mission state and lifecycle contracts."""

from aedt_agent.agent.mission.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    CheckpointRecord,
    EngineeringConstraint,
    ErrorClass,
    EventRecord,
    EventType,
    JobError,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
    WorkerLease,
    utc_now_iso,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "CheckpointRecord",
    "EngineeringConstraint",
    "ErrorClass",
    "EventRecord",
    "EventType",
    "JobError",
    "JobRecord",
    "JobStatus",
    "MissionRecord",
    "MissionState",
    "WorkerLease",
    "utc_now_iso",
]
```

- [ ] **Step 4：运行契约测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_runtime_contracts.py -q
```

Expected: PASS。

- [ ] **Step 5：提交契约**

```powershell
git add src/aedt_agent/agent/mission/contracts.py src/aedt_agent/agent/mission/__init__.py tests/test_agent_runtime_contracts.py
git commit -m "feat: define agent runtime contracts"
```

---

## Task 2：实现 SQLite Mission Store

**Files:**
- Create: `tests/test_agent_sqlite_store.py`
- Create: `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- Modify: `src/aedt_agent/infrastructure/__init__.py`

- [ ] **Step 1：编写持久化测试**

Create `tests/test_agent_sqlite_store.py`:

```python
from __future__ import annotations

from aedt_agent.agent.mission import (
    EngineeringConstraint,
    EventType,
    JobStatus,
    MissionRecord,
    MissionState,
)
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def test_mission_survives_store_restart(tmp_path):
    db_path = tmp_path / "mission.db"
    store = SQLiteMissionStore(db_path)
    mission = MissionRecord.create(
        mission_id="mission-1",
        user_goal="构建 BRD local cut",
        acceptance_criteria=[{"metric": "s11_db_max", "op": "<=", "value": -10}],
        constraints=[EngineeringConstraint(name="max_jobs", value=5)],
    )

    store.create_mission(mission)

    reopened = SQLiteMissionStore(db_path)
    loaded = reopened.get_mission("mission-1")

    assert loaded is not None
    assert loaded.user_goal == "构建 BRD local cut"
    assert loaded.constraints[0].name == "max_jobs"
    assert reopened.list_events("mission-1")[0].event_type == EventType.MISSION_CREATED


def test_job_creation_is_idempotent_per_mission_and_key(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))

    first = store.create_job(
        mission_id="mission-1",
        capability="fake.build",
        idempotency_key="mission-1:build:0",
        input_payload={"x": 1},
        timeout_seconds=30,
        retry_limit=1,
    )
    second = store.create_job(
        mission_id="mission-1",
        capability="fake.build",
        idempotency_key="mission-1:build:0",
        input_payload={"x": 1},
        timeout_seconds=30,
        retry_limit=1,
    )

    assert second.job_id == first.job_id
    assert store.list_jobs("mission-1") == [first]
    assert [event.event_type for event in store.list_events("mission-1")].count(EventType.JOB_CREATED) == 1


def test_state_change_and_checkpoint_are_audited(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    job = store.create_job("mission-1", "fake.build", "k1", {}, 30, 1)

    updated = store.update_mission_state("mission-1", MissionState.WAITING_WORKER)
    checkpoint = store.create_checkpoint(
        mission_id="mission-1",
        job_id=job.job_id,
        artifact_refs=["artifacts/model.aedt"],
        payload={"ok": True},
    )

    assert updated.state == MissionState.WAITING_WORKER
    assert checkpoint.artifact_refs == ["artifacts/model.aedt"]
    assert [event.sequence for event in store.list_events("mission-1")] == [1, 2, 3, 4]


def test_job_completion_persists_output_and_error(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    store.create_mission(MissionRecord.create("mission-1", "goal", [], []))
    job = store.create_job("mission-1", "fake.build", "k1", {}, 30, 1)

    succeeded = store.complete_job(job.job_id, output_payload={"result": "ok"}, artifact_refs=["a.json"])

    assert succeeded.status == JobStatus.SUCCEEDED
    assert succeeded.output_payload == {"result": "ok"}
    assert succeeded.artifact_refs == ["a.json"]
    assert store.get_job(job.job_id).status == JobStatus.SUCCEEDED
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_sqlite_store.py -q
```

Expected: FAIL，原因是 `SQLiteMissionStore` 尚未存在。

- [ ] **Step 3：实现 SQLite store**

Create `src/aedt_agent/infrastructure/sqlite_mission_store.py` with these public methods:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from aedt_agent.agent.mission import (
    ApprovalDecision,
    ApprovalRequest,
    CheckpointRecord,
    EngineeringConstraint,
    ErrorClass,
    EventRecord,
    EventType,
    JobError,
    JobRecord,
    JobStatus,
    MissionRecord,
    MissionState,
    WorkerLease,
    utc_now_iso,
)


class SQLiteMissionStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    user_goal TEXT NOT NULL,
                    acceptance_criteria_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    vision_required INTEGER NOT NULL,
                    final_outcome_json TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    event_type TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(mission_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    capability TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    input_payload_json TEXT NOT NULL,
                    output_payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL,
                    retry_limit INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_json TEXT,
                    UNIQUE(mission_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    created_at TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                    reason TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    selected_option_id TEXT,
                    comment TEXT
                );
                CREATE TABLE IF NOT EXISTS worker_leases (
                    lease_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    worker_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    released_at TEXT
                );
                """
            )

    def create_mission(self, mission: MissionRecord) -> MissionRecord:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO missions (
                    mission_id, user_goal, acceptance_criteria_json, constraints_json,
                    state, plan_version, created_at, updated_at, vision_required, final_outcome_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission.mission_id,
                    mission.user_goal,
                    _dump(mission.acceptance_criteria),
                    _dump([constraint.to_json_dict() for constraint in mission.constraints]),
                    mission.state.value,
                    mission.plan_version,
                    mission.created_at,
                    mission.updated_at,
                    int(mission.vision_required),
                    _dump(mission.final_outcome) if mission.final_outcome is not None else None,
                ),
            )
            self._append_event_in_tx(
                db,
                mission.mission_id,
                EventType.MISSION_CREATED,
                {"state": mission.state.value, "user_goal": mission.user_goal},
            )
        return mission

    def get_mission(self, mission_id: str) -> MissionRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
        return None if row is None else _mission_from_row(row)

    def update_mission_state(self, mission_id: str, state: MissionState) -> MissionRecord:
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                "UPDATE missions SET state = ?, updated_at = ? WHERE mission_id = ?",
                (state.value, now, mission_id),
            )
            self._append_event_in_tx(db, mission_id, EventType.MISSION_STATE_CHANGED, {"state": state.value})
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        return mission

    def create_job(
        self,
        mission_id: str,
        capability: str,
        idempotency_key: str,
        input_payload: dict,
        timeout_seconds: int,
        retry_limit: int,
    ) -> JobRecord:
        existing = self.get_job_by_idempotency_key(mission_id, idempotency_key)
        if existing is not None:
            return existing
        job = JobRecord.create(str(uuid4()), mission_id, capability, idempotency_key, input_payload, timeout_seconds, retry_limit)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO jobs (
                    job_id, mission_id, capability, idempotency_key, input_payload_json,
                    output_payload_json, artifact_refs_json, timeout_seconds, retry_limit,
                    status, created_at, updated_at, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.mission_id,
                    job.capability,
                    job.idempotency_key,
                    _dump(job.input_payload),
                    _dump(job.output_payload),
                    _dump(job.artifact_refs),
                    job.timeout_seconds,
                    job.retry_limit,
                    job.status.value,
                    job.created_at,
                    job.updated_at,
                    None,
                ),
            )
            self._append_event_in_tx(db, mission_id, EventType.JOB_CREATED, {"job_id": job.job_id, "capability": capability})
        return job

    def get_job(self, job_id: str) -> JobRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job not found: {job_id}")
        return _job_from_row(row)

    def get_job_by_idempotency_key(self, mission_id: str, idempotency_key: str) -> JobRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE mission_id = ? AND idempotency_key = ?",
                (mission_id, idempotency_key),
            ).fetchone()
        return None if row is None else _job_from_row(row)

    def list_jobs(self, mission_id: str) -> list[JobRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE mission_id = ? ORDER BY created_at, job_id", (mission_id,)).fetchall()
        return [_job_from_row(row) for row in rows]

    def complete_job(self, job_id: str, output_payload: dict, artifact_refs: list[str]) -> JobRecord:
        job = self.get_job(job_id)
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                """
                UPDATE jobs
                SET status = ?, output_payload_json = ?, artifact_refs_json = ?, updated_at = ?, error_json = NULL
                WHERE job_id = ?
                """,
                (JobStatus.SUCCEEDED.value, _dump(output_payload), _dump(artifact_refs), now, job_id),
            )
            self._append_event_in_tx(db, job.mission_id, EventType.JOB_SUCCEEDED, {"job_id": job_id})
        return self.get_job(job_id)

    def fail_job(self, job_id: str, error: JobError) -> JobRecord:
        job = self.get_job(job_id)
        now = utc_now_iso()
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error_json = ? WHERE job_id = ?",
                (JobStatus.FAILED.value, now, _dump(error.to_json_dict()), job_id),
            )
            self._append_event_in_tx(db, job.mission_id, EventType.JOB_FAILED, {"job_id": job_id, "error": error.to_json_dict()})
        return self.get_job(job_id)

    def create_checkpoint(self, mission_id: str, job_id: str, artifact_refs: list[str], payload: dict) -> CheckpointRecord:
        checkpoint = CheckpointRecord(str(uuid4()), mission_id, job_id, utc_now_iso(), artifact_refs, payload)
        with self._connect() as db:
            db.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?)",
                (
                    checkpoint.checkpoint_id,
                    checkpoint.mission_id,
                    checkpoint.job_id,
                    checkpoint.created_at,
                    _dump(checkpoint.artifact_refs),
                    _dump(checkpoint.payload),
                ),
            )
            self._append_event_in_tx(db, mission_id, EventType.CHECKPOINT_CREATED, {"checkpoint_id": checkpoint.checkpoint_id})
        return checkpoint

    def create_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self._connect() as db:
            db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.approval_id,
                    approval.mission_id,
                    approval.reason,
                    _dump(approval.options),
                    approval.decision.value,
                    approval.created_at,
                    approval.resolved_at,
                    approval.selected_option_id,
                    approval.comment,
                ),
            )
            self._append_event_in_tx(db, approval.mission_id, EventType.APPROVAL_REQUESTED, {"approval_id": approval.approval_id})
        return approval

    def resolve_approval(self, approval_id: str, decision: ApprovalDecision, selected_option_id: str | None, comment: str | None) -> ApprovalRequest:
        now = utc_now_iso()
        with self._connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(f"approval not found: {approval_id}")
            db.execute(
                """
                UPDATE approvals
                SET decision = ?, resolved_at = ?, selected_option_id = ?, comment = ?
                WHERE approval_id = ?
                """,
                (decision.value, now, selected_option_id, comment, approval_id),
            )
            self._append_event_in_tx(
                db,
                row["mission_id"],
                EventType.APPROVAL_RESOLVED,
                {"approval_id": approval_id, "decision": decision.value, "selected_option_id": selected_option_id},
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        with self._connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"approval not found: {approval_id}")
        return _approval_from_row(row)

    def list_events(self, mission_id: str) -> list[EventRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE mission_id = ? ORDER BY sequence", (mission_id,)).fetchall()
        return [_event_from_row(row) for row in rows]

    def _append_event_in_tx(self, db: sqlite3.Connection, mission_id: str, event_type: EventType, payload: dict) -> EventRecord:
        row = db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE mission_id = ?", (mission_id,)).fetchone()
        sequence = int(row["next_sequence"])
        event = EventRecord(str(uuid4()), mission_id, event_type, sequence, utc_now_iso(), payload)
        db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (event.event_id, event.mission_id, event.event_type.value, event.sequence, event.created_at, _dump(event.payload)),
        )
        return event


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _load(value: str | None, default: object) -> object:
    if value is None:
        return default
    return json.loads(value)
```

Continue the file with row mappers:

```python
def _mission_from_row(row: sqlite3.Row) -> MissionRecord:
    return MissionRecord(
        mission_id=row["mission_id"],
        user_goal=row["user_goal"],
        acceptance_criteria=list(_load(row["acceptance_criteria_json"], [])),
        constraints=[EngineeringConstraint(**item) for item in _load(row["constraints_json"], [])],
        state=MissionState(row["state"]),
        plan_version=row["plan_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        vision_required=bool(row["vision_required"]),
        final_outcome=_load(row["final_outcome_json"], None),
    )


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    error_payload = _load(row["error_json"], None)
    error = None
    if error_payload is not None:
        error = JobError(
            error_class=ErrorClass(error_payload["error_class"]),
            message=error_payload["message"],
            retryable=error_payload["retryable"],
            details=error_payload.get("details", {}),
        )
    return JobRecord(
        job_id=row["job_id"],
        mission_id=row["mission_id"],
        capability=row["capability"],
        idempotency_key=row["idempotency_key"],
        input_payload=dict(_load(row["input_payload_json"], {})),
        output_payload=dict(_load(row["output_payload_json"], {})),
        artifact_refs=list(_load(row["artifact_refs_json"], [])),
        timeout_seconds=row["timeout_seconds"],
        retry_limit=row["retry_limit"],
        status=JobStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error=error,
    )


def _event_from_row(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=row["event_id"],
        mission_id=row["mission_id"],
        event_type=EventType(row["event_type"]),
        sequence=row["sequence"],
        created_at=row["created_at"],
        payload=dict(_load(row["payload_json"], {})),
    )


def _approval_from_row(row: sqlite3.Row) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=row["approval_id"],
        mission_id=row["mission_id"],
        reason=row["reason"],
        options=list(_load(row["options_json"], [])),
        decision=ApprovalDecision(row["decision"]),
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        selected_option_id=row["selected_option_id"],
        comment=row["comment"],
    )
```

Modify `src/aedt_agent/infrastructure/__init__.py`:

```python
"""Persistence, process, artifact, and AEDT infrastructure adapters."""

from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore

__all__ = ["SQLiteMissionStore"]
```

- [ ] **Step 4：运行 store 测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_sqlite_store.py tests\test_agent_runtime_contracts.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 SQLite store**

```powershell
git add src/aedt_agent/infrastructure/sqlite_mission_store.py src/aedt_agent/infrastructure/__init__.py tests/test_agent_sqlite_store.py
git commit -m "feat: persist agent missions in sqlite"
```

---

## Task 3：实现状态机

**Files:**
- Create: `tests/test_agent_state_machine.py`
- Create: `src/aedt_agent/agent/orchestrator/state_machine.py`
- Modify: `src/aedt_agent/agent/orchestrator/__init__.py`

- [ ] **Step 1：编写状态迁移测试**

Create `tests/test_agent_state_machine.py`:

```python
from __future__ import annotations

import pytest

from aedt_agent.agent.mission import MissionState
from aedt_agent.agent.orchestrator.state_machine import InvalidMissionTransition, assert_transition, can_transition


def test_allowed_mission_transitions():
    assert can_transition(MissionState.CREATED, MissionState.PLANNING)
    assert can_transition(MissionState.PLANNING, MissionState.WAITING_WORKER)
    assert can_transition(MissionState.WAITING_WORKER, MissionState.EVALUATING)
    assert can_transition(MissionState.EVALUATING, MissionState.WAITING_APPROVAL)
    assert can_transition(MissionState.WAITING_APPROVAL, MissionState.WAITING_WORKER)
    assert can_transition(MissionState.EVALUATING, MissionState.COMPLETED)
    assert can_transition(MissionState.WAITING_WORKER, MissionState.FAILED)
    assert can_transition(MissionState.PLANNING, MissionState.CANCELED)


def test_terminal_states_do_not_transition():
    assert not can_transition(MissionState.COMPLETED, MissionState.PLANNING)
    assert not can_transition(MissionState.FAILED, MissionState.WAITING_WORKER)
    assert not can_transition(MissionState.CANCELED, MissionState.WAITING_APPROVAL)


def test_invalid_transition_raises_clear_error():
    with pytest.raises(InvalidMissionTransition) as error:
        assert_transition(MissionState.CREATED, MissionState.COMPLETED)

    assert "created -> completed" in str(error.value)
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_state_machine.py -q
```

Expected: FAIL，原因是 `state_machine` 尚未存在。

- [ ] **Step 3：实现状态机**

Create `src/aedt_agent/agent/orchestrator/state_machine.py`:

```python
from __future__ import annotations

from aedt_agent.agent.mission import MissionState


class InvalidMissionTransition(ValueError):
    """Raised when a Mission state transition is not allowed."""


ALLOWED_TRANSITIONS: dict[MissionState, set[MissionState]] = {
    MissionState.CREATED: {MissionState.PLANNING, MissionState.CANCELED},
    MissionState.PLANNING: {MissionState.WAITING_WORKER, MissionState.WAITING_APPROVAL, MissionState.FAILED, MissionState.CANCELED},
    MissionState.WAITING_WORKER: {MissionState.EVALUATING, MissionState.WAITING_APPROVAL, MissionState.FAILED, MissionState.CANCELED},
    MissionState.WAITING_APPROVAL: {MissionState.WAITING_WORKER, MissionState.PLANNING, MissionState.FAILED, MissionState.CANCELED},
    MissionState.EVALUATING: {MissionState.WAITING_WORKER, MissionState.WAITING_APPROVAL, MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELED},
    MissionState.COMPLETED: set(),
    MissionState.FAILED: set(),
    MissionState.CANCELED: set(),
}


def can_transition(current: MissionState, target: MissionState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: MissionState, target: MissionState) -> None:
    if not can_transition(current, target):
        raise InvalidMissionTransition(f"Invalid Mission transition: {current.value} -> {target.value}")
```

Modify `src/aedt_agent/agent/orchestrator/__init__.py`:

```python
"""Mission state transition and job dispatch orchestration."""

from aedt_agent.agent.orchestrator.state_machine import InvalidMissionTransition, assert_transition, can_transition

__all__ = ["InvalidMissionTransition", "assert_transition", "can_transition"]
```

- [ ] **Step 4：运行状态机测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_state_machine.py tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 5：提交状态机**

```powershell
git add src/aedt_agent/agent/orchestrator/state_machine.py src/aedt_agent/agent/orchestrator/__init__.py tests/test_agent_state_machine.py
git commit -m "feat: validate mission state transitions"
```

---

## Task 4：实现 Worker 注册、执行和错误分类

**Files:**
- Create: `tests/test_agent_worker_registry.py`
- Create: `src/aedt_agent/agent/workers/registry.py`
- Modify: `src/aedt_agent/agent/workers/__init__.py`

- [ ] **Step 1：编写 Worker 测试**

Create `tests/test_agent_worker_registry.py`:

```python
from __future__ import annotations

import pytest

from aedt_agent.agent.mission import ErrorClass, JobRecord, JobStatus
from aedt_agent.agent.workers import InMemoryWorkerRegistry, WorkerContext, classify_worker_error


def _job(capability: str = "fake.echo") -> JobRecord:
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=capability,
        idempotency_key="k1",
        input_payload={"value": 3},
        timeout_seconds=30,
        retry_limit=1,
    )


def test_worker_registry_executes_registered_worker():
    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", lambda job, context: {"value": job.input_payload["value"], "worker": context.worker_id})

    result = registry.execute(_job(), WorkerContext(worker_id="worker-1"))

    assert result.status == JobStatus.SUCCEEDED
    assert result.output_payload == {"value": 3, "worker": "worker-1"}
    assert result.error is None


def test_worker_registry_rejects_unknown_capability():
    registry = InMemoryWorkerRegistry()

    result = registry.execute(_job("missing.capability"), WorkerContext(worker_id="worker-1"))

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert result.error.error_class == ErrorClass.INVALID_INPUT


def test_worker_errors_are_classified_without_llm_authority():
    assert classify_worker_error(TimeoutError("solver timed out")).error_class == ErrorClass.TIMEOUT
    assert classify_worker_error(RuntimeError("license unavailable")).error_class == ErrorClass.LICENSE_UNAVAILABLE
    assert classify_worker_error(ValueError("bad input")).error_class == ErrorClass.INVALID_INPUT
    assert classify_worker_error(Exception("boom")).error_class == ErrorClass.WORKER_CRASH


def test_duplicate_registration_is_rejected():
    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", lambda job, context: {})

    with pytest.raises(ValueError):
        registry.register("fake.echo", lambda job, context: {})
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_worker_registry.py -q
```

Expected: FAIL，原因是 Worker registry 尚未存在。

- [ ] **Step 3：实现 Worker registry**

Create `src/aedt_agent/agent/workers/registry.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aedt_agent.agent.mission import ErrorClass, JobError, JobRecord, JobStatus, utc_now_iso


WorkerFn = Callable[[JobRecord, "WorkerContext"], dict[str, Any]]


@dataclass(frozen=True)
class WorkerContext:
    worker_id: str


@dataclass(frozen=True)
class WorkerExecutionResult:
    job_id: str
    status: JobStatus
    output_payload: dict[str, Any]
    artifact_refs: list[str]
    error: JobError | None


class InMemoryWorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerFn] = {}

    def register(self, capability: str, worker: WorkerFn) -> None:
        if capability in self._workers:
            raise ValueError(f"worker already registered for capability: {capability}")
        self._workers[capability] = worker

    def execute(self, job: JobRecord, context: WorkerContext) -> WorkerExecutionResult:
        worker = self._workers.get(job.capability)
        if worker is None:
            return WorkerExecutionResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                output_payload={},
                artifact_refs=[],
                error=JobError(ErrorClass.INVALID_INPUT, f"No worker registered for capability: {job.capability}", False),
            )
        try:
            output = worker(job, context)
            artifact_refs = list(output.pop("artifact_refs", [])) if "artifact_refs" in output else []
            return WorkerExecutionResult(job.job_id, JobStatus.SUCCEEDED, output, artifact_refs, None)
        except Exception as exc:
            return WorkerExecutionResult(job.job_id, JobStatus.FAILED, {}, [], classify_worker_error(exc))


def classify_worker_error(error: Exception) -> JobError:
    message = str(error)
    lowered = message.lower()
    if isinstance(error, TimeoutError):
        return JobError(ErrorClass.TIMEOUT, message, retryable=True)
    if "license" in lowered and ("unavailable" in lowered or "denied" in lowered):
        return JobError(ErrorClass.LICENSE_UNAVAILABLE, message, retryable=True)
    if isinstance(error, ValueError):
        return JobError(ErrorClass.INVALID_INPUT, message, retryable=False)
    return JobError(ErrorClass.WORKER_CRASH, message, retryable=True, details={"error_type": type(error).__name__, "observed_at": utc_now_iso()})
```

Modify `src/aedt_agent/agent/workers/__init__.py`:

```python
"""Leaseable worker contracts and adapters."""

from aedt_agent.agent.workers.registry import (
    InMemoryWorkerRegistry,
    WorkerContext,
    WorkerExecutionResult,
    classify_worker_error,
)

__all__ = [
    "InMemoryWorkerRegistry",
    "WorkerContext",
    "WorkerExecutionResult",
    "classify_worker_error",
]
```

- [ ] **Step 4：运行 Worker 测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_worker_registry.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 Worker registry**

```powershell
git add src/aedt_agent/agent/workers/registry.py src/aedt_agent/agent/workers/__init__.py tests/test_agent_worker_registry.py
git commit -m "feat: add worker registry and error classification"
```

---

## Task 5：实现 Runtime 服务、Job 幂等和 Lease 恢复

**Files:**
- Create: `tests/test_agent_runtime_service.py`
- Create: `src/aedt_agent/agent/orchestrator/runtime.py`
- Modify: `src/aedt_agent/agent/orchestrator/__init__.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`

- [ ] **Step 1：编写 Runtime 服务测试**

Create `tests/test_agent_runtime_service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aedt_agent.agent.mission import EngineeringConstraint, JobStatus, MissionState
from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def test_runtime_creates_restartable_mission(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)

    mission = runtime.create_mission(
        user_goal="构建 local cut",
        acceptance_criteria=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        constraints=[EngineeringConstraint("max_jobs", 4)],
    )

    restarted = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"))
    loaded = restarted.get_mission(mission.mission_id)

    assert loaded.user_goal == "构建 local cut"
    assert loaded.state == MissionState.CREATED


def test_runtime_prevents_duplicate_job_creation(tmp_path):
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"))
    mission = runtime.create_mission("goal", [], [])

    first = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {"x": 1})
    second = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {"x": 1})

    assert second.job_id == first.job_id
    assert len(runtime.list_jobs(mission.mission_id)) == 1


def test_runtime_executes_job_once_and_records_checkpoint(tmp_path):
    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", lambda job, context: {"value": job.input_payload["x"], "artifact_refs": ["artifact.json"]})
    runtime = AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {"x": 7})

    result = runtime.execute_next_job(mission.mission_id, worker_id="worker-1")

    assert result.job_id == job.job_id
    assert result.status == JobStatus.SUCCEEDED
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED
    assert any(event.event_type.value == "checkpoint_created" for event in runtime.list_events(mission.mission_id))


def test_expired_worker_lease_can_be_recovered(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "step-1", {})
    expired = datetime.now(UTC) - timedelta(seconds=5)

    lease = store.acquire_job_lease(job.job_id, worker_id="worker-old", lease_seconds=1, now=expired)
    recovered = runtime.recover_expired_leases(now=datetime.now(UTC))

    assert lease.released_at is None
    assert recovered == [job.job_id]
    assert runtime.get_job(job.job_id).status == JobStatus.QUEUED
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_runtime_service.py -q
```

Expected: FAIL，原因是 `AgentRuntime` 和 lease store 方法尚未存在。

- [ ] **Step 3：给 SQLite store 增加 lease 方法**

Modify `src/aedt_agent/infrastructure/sqlite_mission_store.py`:

```python
from datetime import UTC, datetime, timedelta
```

Add methods to `SQLiteMissionStore`:

```python
    def acquire_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        job = self.get_job(job_id)
        current = now or datetime.now(UTC)
        lease = WorkerLease(
            lease_id=str(uuid4()),
            job_id=job_id,
            worker_id=worker_id,
            acquired_at=current.isoformat(),
            expires_at=(current + timedelta(seconds=lease_seconds)).isoformat(),
            released_at=None,
        )
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (JobStatus.LEASED.value, utc_now_iso(), job_id),
            )
            db.execute(
                "INSERT INTO worker_leases VALUES (?, ?, ?, ?, ?, ?)",
                (lease.lease_id, lease.job_id, lease.worker_id, lease.acquired_at, lease.expires_at, lease.released_at),
            )
            self._append_event_in_tx(db, job.mission_id, EventType.JOB_LEASED, {"job_id": job_id, "worker_id": worker_id})
        return lease

    def release_job_lease(self, lease_id: str) -> WorkerLease:
        now = utc_now_iso()
        with self._connect() as db:
            db.execute("UPDATE worker_leases SET released_at = ? WHERE lease_id = ?", (now, lease_id))
            row = db.execute("SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)).fetchone()
        if row is None:
            raise KeyError(f"lease not found: {lease_id}")
        return _lease_from_row(row)

    def recover_expired_leases(self, now: datetime) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT worker_leases.*
                FROM worker_leases
                JOIN jobs ON jobs.job_id = worker_leases.job_id
                WHERE worker_leases.released_at IS NULL
                  AND worker_leases.expires_at < ?
                  AND jobs.status = ?
                """,
                (now.isoformat(), JobStatus.LEASED.value),
            ).fetchall()
            job_ids = [row["job_id"] for row in rows]
            for job_id in job_ids:
                db.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                    (JobStatus.QUEUED.value, utc_now_iso(), job_id),
                )
            db.executemany(
                "UPDATE worker_leases SET released_at = ? WHERE lease_id = ?",
                [(now.isoformat(), row["lease_id"]) for row in rows],
            )
        return job_ids

    def next_queued_job(self, mission_id: str) -> JobRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE mission_id = ? AND status = ? ORDER BY created_at, job_id LIMIT 1",
                (mission_id, JobStatus.QUEUED.value),
            ).fetchone()
        return None if row is None else _job_from_row(row)
```

Add mapper:

```python
def _lease_from_row(row: sqlite3.Row) -> WorkerLease:
    return WorkerLease(
        lease_id=row["lease_id"],
        job_id=row["job_id"],
        worker_id=row["worker_id"],
        acquired_at=row["acquired_at"],
        expires_at=row["expires_at"],
        released_at=row["released_at"],
    )
```

- [ ] **Step 4：实现 Runtime 服务**

Create `src/aedt_agent/agent/orchestrator/runtime.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from aedt_agent.agent.mission import EngineeringConstraint, EventRecord, JobRecord, JobStatus, MissionRecord
from aedt_agent.agent.workers import InMemoryWorkerRegistry, WorkerContext, WorkerExecutionResult


class AgentRuntime:
    def __init__(self, store, registry: InMemoryWorkerRegistry | None = None, default_lease_seconds: int = 60):
        self.store = store
        self.registry = registry or InMemoryWorkerRegistry()
        self.default_lease_seconds = default_lease_seconds

    def create_mission(
        self,
        user_goal: str,
        acceptance_criteria: list[dict],
        constraints: list[EngineeringConstraint],
        vision_required: bool = False,
    ) -> MissionRecord:
        mission = MissionRecord.create(str(uuid4()), user_goal, acceptance_criteria, constraints, vision_required)
        return self.store.create_mission(mission)

    def get_mission(self, mission_id: str) -> MissionRecord:
        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        return mission

    def create_job(
        self,
        mission_id: str,
        capability: str,
        idempotency_key: str,
        input_payload: dict,
        timeout_seconds: int = 300,
        retry_limit: int = 1,
    ) -> JobRecord:
        return self.store.create_job(mission_id, capability, idempotency_key, input_payload, timeout_seconds, retry_limit)

    def list_jobs(self, mission_id: str) -> list[JobRecord]:
        return self.store.list_jobs(mission_id)

    def get_job(self, job_id: str) -> JobRecord:
        return self.store.get_job(job_id)

    def list_events(self, mission_id: str) -> list[EventRecord]:
        return self.store.list_events(mission_id)

    def execute_next_job(self, mission_id: str, worker_id: str) -> WorkerExecutionResult:
        job = self.store.next_queued_job(mission_id)
        if job is None:
            raise ValueError(f"no queued job for mission: {mission_id}")
        lease = self.store.acquire_job_lease(job.job_id, worker_id, self.default_lease_seconds)
        leased_job = self.store.get_job(job.job_id)
        result = self.registry.execute(leased_job, WorkerContext(worker_id))
        if result.status == JobStatus.SUCCEEDED:
            self.store.complete_job(job.job_id, result.output_payload, result.artifact_refs)
            self.store.create_checkpoint(mission_id, job.job_id, result.artifact_refs, {"output": result.output_payload})
        else:
            assert result.error is not None
            self.store.fail_job(job.job_id, result.error)
        self.store.release_job_lease(lease.lease_id)
        return result

    def recover_expired_leases(self, now: datetime | None = None) -> list[str]:
        return self.store.recover_expired_leases(now or datetime.now(UTC))
```

Modify `src/aedt_agent/agent/orchestrator/__init__.py`:

```python
"""Mission state transition and job dispatch orchestration."""

from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.agent.orchestrator.state_machine import InvalidMissionTransition, assert_transition, can_transition

__all__ = ["AgentRuntime", "InvalidMissionTransition", "assert_transition", "can_transition"]
```

- [ ] **Step 5：运行 Runtime 服务测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_runtime_service.py tests\test_agent_sqlite_store.py -q
```

Expected: PASS。

- [ ] **Step 6：提交 Runtime 服务**

```powershell
git add src/aedt_agent/agent/orchestrator/runtime.py src/aedt_agent/agent/orchestrator/__init__.py src/aedt_agent/infrastructure/sqlite_mission_store.py tests/test_agent_runtime_service.py
git commit -m "feat: orchestrate idempotent jobs and worker leases"
```

---

## Task 6：实现 Approval 服务

**Files:**
- Create: `tests/test_agent_approval_service.py`
- Create: `src/aedt_agent/agent/approvals/service.py`
- Modify: `src/aedt_agent/agent/approvals/__init__.py`

- [ ] **Step 1：编写 Approval 测试**

Create `tests/test_agent_approval_service.py`:

```python
from __future__ import annotations

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.mission import ApprovalDecision, MissionState
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore


def test_approval_request_moves_mission_to_waiting_approval(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    service = ApprovalService(store)
    mission = store.create_mission(__import__("aedt_agent.agent.mission", fromlist=["MissionRecord"]).MissionRecord.create("mission-1", "goal", [], []))

    approval = service.request_approval(
        mission_id=mission.mission_id,
        reason="端口候选不唯一",
        options=[{"id": "p1", "label": "TX0-GND"}],
    )

    assert approval.decision == ApprovalDecision.PENDING
    assert store.get_mission(mission.mission_id).state == MissionState.WAITING_APPROVAL


def test_approve_and_reject_are_audited(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    service = ApprovalService(store)
    store.create_mission(__import__("aedt_agent.agent.mission", fromlist=["MissionRecord"]).MissionRecord.create("mission-1", "goal", [], []))
    approval = service.request_approval("mission-1", "选择端口", [{"id": "p1", "label": "P1"}])

    resolved = service.approve(approval.approval_id, selected_option_id="p1", comment="确认")

    assert resolved.decision == ApprovalDecision.APPROVED
    assert resolved.selected_option_id == "p1"
    assert store.get_mission("mission-1").state == MissionState.WAITING_WORKER
    assert any(event.event_type.value == "approval_resolved" for event in store.list_events("mission-1"))


def test_reject_moves_mission_to_failed(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    service = ApprovalService(store)
    store.create_mission(__import__("aedt_agent.agent.mission", fromlist=["MissionRecord"]).MissionRecord.create("mission-1", "goal", [], []))
    approval = service.request_approval("mission-1", "模型不可接受", [{"id": "repair", "label": "修复"}])

    rejected = service.reject(approval.approval_id, comment="模型边界错误")

    assert rejected.decision == ApprovalDecision.REJECTED
    assert store.get_mission("mission-1").state == MissionState.FAILED
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_approval_service.py -q
```

Expected: FAIL，原因是 ApprovalService 尚未存在。

- [ ] **Step 3：实现 ApprovalService**

Create `src/aedt_agent/agent/approvals/service.py`:

```python
from __future__ import annotations

from uuid import uuid4

from aedt_agent.agent.mission import ApprovalDecision, ApprovalRequest, MissionState


class ApprovalService:
    def __init__(self, store):
        self.store = store

    def request_approval(self, mission_id: str, reason: str, options: list[dict]) -> ApprovalRequest:
        approval = ApprovalRequest.create(str(uuid4()), mission_id, reason, options)
        created = self.store.create_approval(approval)
        self.store.update_mission_state(mission_id, MissionState.WAITING_APPROVAL)
        return created

    def approve(self, approval_id: str, selected_option_id: str, comment: str | None = None) -> ApprovalRequest:
        approval = self.store.resolve_approval(approval_id, ApprovalDecision.APPROVED, selected_option_id, comment)
        self.store.update_mission_state(approval.mission_id, MissionState.WAITING_WORKER)
        return approval

    def reject(self, approval_id: str, comment: str | None = None) -> ApprovalRequest:
        approval = self.store.resolve_approval(approval_id, ApprovalDecision.REJECTED, None, comment)
        self.store.update_mission_state(approval.mission_id, MissionState.FAILED)
        return approval
```

Modify `src/aedt_agent/agent/approvals/__init__.py`:

```python
"""Human approval request and resume contracts."""

from aedt_agent.agent.approvals.service import ApprovalService

__all__ = ["ApprovalService"]
```

- [ ] **Step 4：运行 Approval 测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_approval_service.py tests\test_agent_state_machine.py -q
```

Expected: PASS。

- [ ] **Step 5：提交 Approval 服务**

```powershell
git add src/aedt_agent/agent/approvals/service.py src/aedt_agent/agent/approvals/__init__.py tests/test_agent_approval_service.py
git commit -m "feat: add mission approval service"
```

---

## Task 7：接入本地 SQLite Mission CLI

**Files:**
- Create: `tests/test_agent_cli_runtime.py`
- Modify: `src/aedt_agent/agent/cli.py`
- Modify: `tests/test_agent_cli_boundary.py`

- [ ] **Step 1：编写 CLI Runtime 测试**

Create `tests/test_agent_cli_runtime.py`:

```python
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aedt_agent.agent.cli", "--db", str(tmp_path / "mission.db"), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_creates_and_reads_restartable_mission(tmp_path):
    created = _run_cli(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "构建 local cut",
        "--criterion",
        "s21_db_at_56g>=-8",
    )

    assert created.returncode == 0
    created_payload = json.loads(created.stdout)
    mission_id = created_payload["mission_id"]
    assert created_payload["state"] == "created"

    status = _run_cli(tmp_path, "mission", "status", "--mission-id", mission_id)

    assert status.returncode == 0
    status_payload = json.loads(status.stdout)
    assert status_payload["mission_id"] == mission_id
    assert status_payload["user_goal"] == "构建 local cut"


def test_cli_cancel_changes_state_and_audits_event(tmp_path):
    created = _run_cli(tmp_path, "mission", "create", "--goal", "goal")
    mission_id = json.loads(created.stdout)["mission_id"]

    canceled = _run_cli(tmp_path, "mission", "cancel", "--mission-id", mission_id)

    assert canceled.returncode == 0
    assert json.loads(canceled.stdout)["state"] == "canceled"
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_runtime.py -q
```

Expected: FAIL，原因是 CLI 仍返回 `runtime_unavailable`。

- [ ] **Step 3：实现 CLI Runtime**

Modify `src/aedt_agent/agent/cli.py`:

```python
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import MissionState
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aedt-agent")
    parser.add_argument("--db", type=Path, default=Path(".aedt-agent/missions.db"))
    subparsers = parser.add_subparsers(dest="group", required=True)

    mission = subparsers.add_parser("mission", help="Manage persistent engineering missions.")
    mission_commands = mission.add_subparsers(dest="mission_command", required=True)

    create = mission_commands.add_parser("create")
    create.add_argument("--goal", required=True)
    create.add_argument("--criterion", action="append", default=[])

    run = mission_commands.add_parser("run")
    run.add_argument("--mission-id", required=True)

    status = mission_commands.add_parser("status")
    status.add_argument("--mission-id", required=True)

    resume = mission_commands.add_parser("resume")
    resume.add_argument("--mission-id", required=True)

    approve = mission_commands.add_parser("approve")
    approve.add_argument("--mission-id", required=True)
    approve.add_argument("--approval-id", required=False)
    approve.add_argument("--option-id", required=False)
    approve.add_argument("--comment", required=False)

    cancel = mission_commands.add_parser("cancel")
    cancel.add_argument("--mission-id", required=True)

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = AgentRuntime(SQLiteMissionStore(args.db))

    if args.group == "mission" and args.mission_command == "create":
        criteria = [_parse_criterion(value) for value in args.criterion]
        mission = runtime.create_mission(args.goal, criteria, [])
        _print_json(mission.to_json_dict())
        return 0

    if args.group == "mission" and args.mission_command == "status":
        mission = runtime.get_mission(args.mission_id)
        payload: dict[str, Any] = mission.to_json_dict()
        payload["events"] = [event.to_json_dict() for event in runtime.list_events(args.mission_id)]
        _print_json(payload)
        return 0

    if args.group == "mission" and args.mission_command == "cancel":
        mission = runtime.store.update_mission_state(args.mission_id, MissionState.CANCELED)
        _print_json(mission.to_json_dict())
        return 0

    _print_json(
        {
            "command": f"{args.group}.{args.mission_command}",
            "message": "该 Mission 命令面已安装，但具体执行循环将在 BRD Worker 阶段启用。",
            "status": "runtime_command_not_enabled",
        }
    )
    return 2


def _parse_criterion(value: str) -> dict[str, Any]:
    for op in (">=", "<=", "==", ">", "<"):
        if op in value:
            metric, raw = value.split(op, 1)
            return {"metric": metric.strip(), "op": op, "value": _parse_number(raw.strip())}
    return {"metric": value, "op": "exists", "value": True}


def _parse_number(value: str) -> float | str:
    try:
        return float(value)
    except ValueError:
        return value


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4：更新旧 CLI 边界测试**

Modify `tests/test_agent_cli_boundary.py` so it no longer expects `runtime_unavailable` for `mission status`. Replace the body of `test_new_cli_exposes_mission_command_surface` with:

```python
def test_new_cli_exposes_mission_command_surface(tmp_path, capsys):
    from aedt_agent.agent.cli import run

    exit_code = run(["--db", str(tmp_path / "mission.db"), "mission", "create", "--goal", "mission-test"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["state"] == "created"
    assert payload["user_goal"] == "mission-test"
```

Replace `test_root_cli_module_executes_agent_cli` with a create command:

```python
def test_root_cli_module_executes_agent_cli(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.cli",
            "--db",
            str(tmp_path / "mission.db"),
            "mission",
            "create",
            "--goal",
            "mission-test",
        ],
        check=False,
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["state"] == "created"
```

- [ ] **Step 5：运行 CLI 测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_runtime.py tests\test_agent_cli_boundary.py -q
```

Expected: PASS。

- [ ] **Step 6：提交 CLI Runtime**

```powershell
git add src/aedt_agent/agent/cli.py tests/test_agent_cli_runtime.py tests/test_agent_cli_boundary.py
git commit -m "feat: expose local mission runtime cli"
```

---

## Task 8：最终回归与审计

**Files:**
- Modify only if verification finds runtime-specific defects.

- [ ] **Step 1：运行 Runtime 全部新测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_runtime_contracts.py `
  tests\test_agent_sqlite_store.py `
  tests\test_agent_state_machine.py `
  tests\test_agent_worker_registry.py `
  tests\test_agent_runtime_service.py `
  tests\test_agent_approval_service.py `
  tests\test_agent_cli_runtime.py `
  tests\test_agent_cli_boundary.py `
  tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 2：检查新 Agent runtime 不依赖 v0**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest tests\test_architecture_dependencies.py -q
rg -n "aedt_agent\.v0" src\aedt_agent\agent src\aedt_agent\infrastructure
```

Expected: pytest PASS；`rg` 无输出。

- [ ] **Step 3：检查 CLI 行为**

Run:

```powershell
$env:PYTHONPATH='src'
$db = Join-Path $env:TEMP "agent-runtime-smoke.db"
Remove-Item -LiteralPath $db -ErrorAction SilentlyContinue
$created = C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $db mission create --goal "smoke mission" --criterion "s21_db_at_56g>=-8"
$missionId = ($created | ConvertFrom-Json).mission_id
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m aedt_agent.agent.cli --db $db mission status --mission-id $missionId
```

Expected: 第一条输出 `state=created` 的 JSON；第二条输出同一个 Mission 和至少一个 `mission_created` event。

- [ ] **Step 4：运行迁移重点测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest `
  tests\test_v0_namespace_compatibility.py `
  tests\test_agent_cli_boundary.py `
  tests\test_architecture_dependencies.py `
  tests\test_config.py `
  tests\test_chat_workflow_planner.py `
  tests\test_stage_c1_demo_service.py `
  tests\test_node_evolution.py `
  tests\test_stage_c_demo_scripts.py -q
```

Expected: 仅保留已登记的缺失 benchmark artifact 失败；不得新增 runtime 相关失败。

- [ ] **Step 5：运行全量测试**

Run:

```powershell
C:\Users\z3312\code\ansys-agent\.venv\Scripts\python.exe -m pytest -q
```

Expected: 失败集合不得超过当前已登记 9 个基线失败。

- [ ] **Step 6：检查 Git 变更范围**

Run:

```powershell
git status --short
git diff --check
git diff --stat 32af269..HEAD
```

Expected:

- 新增/修改只涉及 `src/aedt_agent/agent`、`src/aedt_agent/infrastructure`、`tests/test_agent_*`；
- 不修改 `aedt_agent.v0`；
- 不修改 `README.md`、RFC、原始 benchmark artifacts、截图脚本或未跟踪目录；
- `git diff --check` 无空白错误。

---

## 完成定义

本计划完成时必须同时满足：

1. Mission、Job、Event、Checkpoint、Approval、WorkerLease 都有 JSON-ready 契约。
2. SQLite store 能持久化 Mission，并在重新打开 store 后读取同一 Mission。
3. `mission_id + idempotency_key` 防止重复 Job 创建。
4. Worker lease 能被 acquire、release，过期 lease 能恢复 queued Job。
5. 每个 Mission 创建、状态变化、Job 创建、Job lease、Job 完成、Checkpoint、Approval 都产生可排序 Event。
6. Worker 错误被确定性分类，不由 LLM 决定错误类别。
7. Approval 能使 Mission 进入 `waiting_approval`，approve 后恢复到 `waiting_worker`，reject 后进入 `failed`。
8. `aedt-agent mission create/status/cancel` 使用本地 SQLite runtime，不再返回 `runtime_unavailable`。
9. `mission run/resume/approve` 可以保留为 `runtime_command_not_enabled`，因为 BRD Worker 阶段尚未实现完整执行循环。
10. 新 Agent runtime 不依赖 `aedt_agent.v0`，也不要求 VLM 或 Pi。

## 后续计划

本计划之后的独立计划应实现 Phase 3：BRD local-cut Mission vertical slice。

该计划应从当前 runtime API 出发，包装现有 local-cut build pipeline 为 Worker，持久化 bbox、port candidates、action plan、model project 和 approval；并证明：

- Mission 可到达可审计的 model-review 状态；
- ambiguous port candidates 进入 Approval；
- approval 后恢复同一个 Mission，不重跑已完成 Jobs；
- dense S-parameter/TDR 证据以 artifact + bounded summary 形式进入 Evaluator，而不是整段塞入 LLM 上下文。
