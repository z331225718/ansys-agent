"""Workspace and task service for the AI-HFSS workbench.

A **workspace** is one engineering project directory chosen by the user. Every
optimization **task** created inside it owns its own SQLite mission database, so
tasks never share mission state and a workspace can be archived by copying one
directory:

    <workspace>/
        .ai-hfss/
            workspace.json                 # identity + creation time
            tasks/
                <task_id>/
                    task.json              # user input + status projections
                    loop.json              # rendered run-loop config (all paths relative)
                    missions.db            # THIS task's mission store (SQLite, WAL)
                    runs/                  # working project copy, reports, progress
                    artifacts/             # touchstone / TDR / plots / evidence
                    logs/driver.log        # auto-driver output
                    driver.lock            # advisory: one driver per task database

Design constraints proven by the P0 spike (docs/spikes/ai-hfss-p0.md):

* two tasks with separate databases advance concurrently in one process without
  cross-contamination, so one `AgentRuntime` per task is safe;
* two drivers on the SAME database raise `sqlite3.IntegrityError: UNIQUE
  constraint failed: node_runs.graph_run_id, node_runs.sequence`, so exactly one
  driver may touch a task database. This module enforces that with a per-task
  lock plus a `driver.lock` advisory file so a workbench auto-driver and a CLI
  `mission run-loop` cannot fight over the same task.

Only the workbench writes here. Read-side projections reuse
`workbench.workbench_snapshot` so the UI keeps one response shape.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

WORKSPACE_SCHEMA = "aihfss-workspace/v1"
TASK_SCHEMA = "aihfss-task/v1"
SYSTEM_DIR_NAME = ".ai-hfss"
WORKSPACE_FILE = "workspace.json"
TASKS_DIR = "tasks"
TASK_FILE = "task.json"
LOOP_FILE = "loop.json"
TASK_DB = "missions.db"
LOCK_FILE = "driver.lock"
DRIVER_LOG = "driver.log"
TASK_ID_RE = re.compile(r"^T-\d{8}-\d{6}-[0-9a-f]{4}$")
# Terminal graph states: the auto-driver stops here.
TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
DEFAULT_TEMPLATE = "brd_reviewed_model_optimize_loop"
DEFAULT_POLL_SECONDS = 5
MAX_POLL_SECONDS = 300


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_task_id() -> str:
    import uuid

    return "T-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


class TaskError(RuntimeError):
    """A workspace/task operation failed with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def to_json(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "error": str(self), **self.details}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TaskError("task/invalid-json", f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def pid_alive(pid: int) -> bool:
    """Portable liveness probe (os.kill(pid, 0) is unreliable on Windows)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _rel(path: Path, base: Path) -> str:
    """Render a path relative to the workspace when possible (portable archives)."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


@dataclass
class Workspace:
    root: Path
    workspace_id: str
    name: str
    created_at: str

    @property
    def system_dir(self) -> Path:
        return self.root / SYSTEM_DIR_NAME

    @property
    def tasks_dir(self) -> Path:
        return self.system_dir / TASKS_DIR

    def to_json(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "root": self.root.as_posix(),
            "created_at": self.created_at,
        }


class WorkspaceRegistry:
    """The user-level list of workspaces this machine has opened.

    The registry holds **pointers only**; task data always lives inside each
    workspace directory, so forgetting a workspace never deletes work.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path.home() / ".dsh" / "ai-hfss-workstation" / "workspaces.json")
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            payload = _read_json(self.path)
        except (OSError, json.JSONDecodeError, TaskError):
            return []
        entries = payload.get("workspaces")
        return [item for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        _write_json(self.path, {"schema": "aihfss-workspaces/v1", "workspaces": entries})

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._load()
        result = []
        for entry in entries:
            root = Path(str(entry.get("root") or ""))
            result.append({**entry, "exists": root.is_dir()})
        return result

    def register(self, workspace: Workspace) -> dict[str, Any]:
        with self._lock:
            entries = self._load()
            record = {**workspace.to_json(), "last_opened": _now()}
            replaced = False
            for index, entry in enumerate(entries):
                if entry.get("workspace_id") == workspace.workspace_id:
                    entries[index] = record
                    replaced = True
                    break
            if not replaced:
                entries.append(record)
            self._save(entries)
            return record

    def find(self, workspace_id: str) -> dict[str, Any] | None:
        for entry in self.list():
            if entry.get("workspace_id") == workspace_id:
                return entry
        return None

    def forget(self, workspace_id: str) -> bool:
        with self._lock:
            entries = self._load()
            remaining = [entry for entry in entries if entry.get("workspace_id") != workspace_id]
            if len(remaining) == len(entries):
                return False
            self._save(remaining)
            return True


class TaskService:
    """Workspace/task lifecycle, one runtime per task database."""

    def __init__(
        self,
        registry: WorkspaceRegistry | None = None,
        *,
        profile: Any = None,
        runtime_factory: Callable[[Path, Any], Any] | None = None,
        max_cached_runtimes: int = 4,
    ) -> None:
        self.registry = registry or WorkspaceRegistry()
        self.profile = profile
        self._runtime_factory = runtime_factory
        self._max_cached = max_cached_runtimes
        self._runtimes: dict[str, Any] = {}
        self._runtime_order: list[str] = []
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._drivers: dict[str, dict[str, Any]] = {}

    # ── workspaces ──────────────────────────────────────────────────────────

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.registry.list()

    def open_workspace(self, root: str | Path, name: str | None = None) -> dict[str, Any]:
        root_path = Path(root).expanduser()
        if not str(root_path).strip():
            raise TaskError("workspace/invalid-root", "workspace root is required")
        root_path = root_path.resolve()
        if root_path.exists() and not root_path.is_dir():
            raise TaskError("workspace/not-a-directory", f"not a directory: {root_path}")
        # Refuse nesting so one workspace cannot leave files inside another.
        for entry in self.registry.list():
            other = Path(str(entry.get("root") or ""))
            if other == root_path:
                continue
            if _is_within(root_path, other / SYSTEM_DIR_NAME) or _is_within(other, root_path / SYSTEM_DIR_NAME):
                raise TaskError(
                    "workspace/nested",
                    f"workspace {root_path} overlaps {other}; choose a separate directory",
                )
        root_path.mkdir(parents=True, exist_ok=True)
        manifest_path = root_path / SYSTEM_DIR_NAME / WORKSPACE_FILE
        if manifest_path.is_file():
            manifest = _read_json(manifest_path)
            if str(manifest.get("root") or "") not in {"", root_path.as_posix()}:
                raise TaskError(
                    "workspace/manifest-mismatch",
                    f"{manifest_path} belongs to {manifest.get('root')}; refusing to reuse it",
                )
            workspace = Workspace(
                root=root_path,
                workspace_id=str(manifest.get("workspace_id") or ""),
                name=str(manifest.get("name") or root_path.name),
                created_at=str(manifest.get("created_at") or _now()),
            )
            if not workspace.workspace_id:
                raise TaskError("workspace/invalid-manifest", f"{manifest_path} has no workspace_id")
        else:
            workspace = Workspace(
                root=root_path,
                workspace_id="ws-" + new_task_id().removeprefix("T-").lower(),
                name=name or root_path.name,
                created_at=_now(),
            )
        (workspace.tasks_dir).mkdir(parents=True, exist_ok=True)
        _write_json(manifest_path, {**workspace.to_json(), "schema": WORKSPACE_SCHEMA})
        return self.registry.register(workspace)

    def forget_workspace(self, workspace_id: str) -> bool:
        return self.registry.forget(workspace_id)

    def _workspace(self, workspace_id: str) -> Workspace:
        entry = self.registry.find(workspace_id)
        if entry is None:
            raise TaskError("workspace/not-found", f"workspace {workspace_id!r} is not open", workspace_id=workspace_id)
        root = Path(str(entry.get("root") or ""))
        if not root.is_dir():
            raise TaskError("workspace/missing", f"workspace directory is gone: {root}", root=root.as_posix())
        manifest_path = root / SYSTEM_DIR_NAME / WORKSPACE_FILE
        if not manifest_path.is_file():
            raise TaskError("workspace/invalid-manifest", f"{manifest_path} is missing")
        manifest = _read_json(manifest_path)
        return Workspace(
            root=root,
            workspace_id=str(manifest.get("workspace_id") or workspace_id),
            name=str(manifest.get("name") or root.name),
            created_at=str(manifest.get("created_at") or ""),
        )

    # ── tasks ───────────────────────────────────────────────────────────────

    def list_tasks(self, workspace_id: str) -> list[dict[str, Any]]:
        workspace = self._workspace(workspace_id)
        tasks: list[dict[str, Any]] = []
        if not workspace.tasks_dir.is_dir():
            return tasks
        for task_dir in sorted(workspace.tasks_dir.iterdir(), key=lambda p: p.name):
            if not task_dir.is_dir():
                continue
            task_file = task_dir / TASK_FILE
            if not task_file.is_file():
                continue
            try:
                tasks.append(_read_json(task_file))
            except (OSError, json.JSONDecodeError, TaskError):
                tasks.append({"task_id": task_dir.name, "status": "invalid", "error": "task.json unreadable"})
        return tasks

    def create_task(self, workspace_id: str, params: dict[str, Any]) -> dict[str, Any]:
        from aedt_agent.agent.graph_runner import create_graph_run
        from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path

        workspace = self._workspace(workspace_id)
        if not isinstance(params, dict):
            raise TaskError("task/invalid-params", "task params must be an object")
        goal = str(params.get("goal") or "").strip()
        if not goal:
            raise TaskError("task/goal-required", "goal is required")
        template_id = str(params.get("template_id") or DEFAULT_TEMPLATE).strip()
        try:
            template = load_graph_template(resolve_template_path(template_id))
        except FileNotFoundError as exc:
            raise TaskError("task/template-not-found", f"template {template_id!r} was not found") from exc

        task_id = new_task_id()
        task_dir = workspace.tasks_dir / task_id
        runs_dir = task_dir / "runs"
        artifacts_dir = task_dir / "artifacts"
        logs_dir = task_dir / "logs"
        for directory in (task_dir, runs_dir, artifacts_dir, logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        db_path = task_dir / TASK_DB

        config = self._build_loop_config(task_dir, workspace, params, template_id=template_id, goal=goal)
        _write_json(task_dir / LOOP_FILE, config)

        runtime = self._runtime_for(task_id, db_path)
        mission = runtime.create_mission(goal, [], [])
        graph_run = create_graph_run(
            runtime,
            mission.mission_id,
            template,
            initial_payload=dict(config),
            max_steps=int(config.get("max_steps") or 64),
        )
        config["mission_id"] = mission.mission_id
        config["graph_run_id"] = graph_run.graph_run_id
        _write_json(task_dir / LOOP_FILE, config)

        task = {
            "schema": TASK_SCHEMA,
            "task_id": task_id,
            "workspace_id": workspace.workspace_id,
            "name": str(params.get("name") or goal[:60]),
            "template_id": template_id,
            "status": "created",
            "created_at": _now(),
            "updated_at": _now(),
            "goal": goal,
            "mission_id": mission.mission_id,
            "graph_run_id": graph_run.graph_run_id,
            "params": self._portable_params(params, task_dir),
            "paths": {
                "db": _rel(db_path, workspace.root),
                "loop": _rel(task_dir / LOOP_FILE, workspace.root),
                "runs": _rel(runs_dir, workspace.root),
                "artifacts": _rel(artifacts_dir, workspace.root),
            },
        }
        _write_json(task_dir / TASK_FILE, task)
        return task

    def get_task(self, task_id: str) -> dict[str, Any]:
        _, _, task = self._resolve_task(task_id)
        return task

    # ── runtime cache ───────────────────────────────────────────────────────

    def _runtime_for(self, task_id: str, db_path: Path) -> Any:
        runtime = self._runtimes.get(task_id)
        if runtime is not None:
            return runtime
        if self._runtime_factory is not None:
            runtime = self._runtime_factory(db_path, self.profile)
        else:
            from aedt_agent.agent.cli import _runtime_with_workers

            runtime = _runtime_with_workers(db_path, self.profile)
        self._runtimes[task_id] = runtime
        self._runtime_order.append(task_id)
        while len(self._runtime_order) > self._max_cached:
            evicted = self._runtime_order.pop(0)
            if evicted != task_id:
                self._runtimes.pop(evicted, None)
        return runtime

    def _task_runtime(self, task_id: str, task: dict[str, Any]) -> Any:
        workspace = self._workspace(str(task.get("workspace_id") or ""))
        task_dir = workspace.tasks_dir / task_id
        db_path = task_dir / TASK_DB
        if not db_path.is_file():
            raise TaskError("task/missing-db", f"task database is missing: {db_path}", task_id=task_id)
        return self._runtime_for(task_id, db_path)

    def _resolve_task(self, task_id: str) -> tuple[Workspace, Path, dict[str, Any]]:
        if not TASK_ID_RE.match(task_id):
            raise TaskError("task/invalid-id", f"invalid task id: {task_id!r}")
        for entry in self.registry.list():
            workspace = self._workspace(str(entry.get("workspace_id") or ""))
            task_dir = workspace.tasks_dir / task_id
            if (task_dir / TASK_FILE).is_file():
                return workspace, task_dir, _read_json(task_dir / TASK_FILE)
        raise TaskError("task/not-found", f"task {task_id!r} was not found in any open workspace", task_id=task_id)

    def _lock(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(task_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[task_id] = lock
            return lock

    # ── driving ─────────────────────────────────────────────────────────────

    def advance(self, task_id: str) -> dict[str, Any]:
        from aedt_agent.agent.graph_runner import advance_graph

        workspace, task_dir, task = self._resolve_task(task_id)
        runtime = self._task_runtime(task_id, task)
        with self._lock(task_id):
            self._assert_driver_free(task_id, task_dir)
            report = advance_graph(runtime, str(task["graph_run_id"]))
            self._record_status(task_dir, task, str(report.get("status") or ""))
        return {"status": report.get("status"), "node_runs": report.get("node_runs", [])}

    def start_auto(self, task_id: str, *, poll_seconds: int = DEFAULT_POLL_SECONDS) -> dict[str, Any]:
        from aedt_agent.agent.graph_runner import advance_graph

        workspace, task_dir, task = self._resolve_task(task_id)
        with self._lock(task_id):
            existing = self._drivers.get(task_id)
            if existing and existing.get("running"):
                return {"ok": True, "alreadyRunning": True, **self.driver_state(task_id)}
            runtime = self._task_runtime(task_id, task)
            graph_run_id = str(task["graph_run_id"])
            stop = threading.Event()
            interval = max(1, min(int(poll_seconds), MAX_POLL_SECONDS))
            self._write_driver_lock(task_dir)
            log_path = task_dir / "logs" / DRIVER_LOG

            def run() -> None:
                try:
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write(f"[{_now()}] driver start graph={graph_run_id} poll={interval}s\n")
                        log.flush()
                        while not stop.is_set():
                            with self._lock(task_id):
                                report = advance_graph(runtime, graph_run_id)
                            status = str(report.get("status") or "")
                            log.write(f"[{_now()}] advance -> {status}\n")
                            log.flush()
                            self._record_status(task_dir, task, status)
                            if status in TERMINAL_STATUSES:
                                break
                            stop.wait(interval)
                        log.write(f"[{_now()}] driver stop\n")
                except BaseException as exc:  # noqa: BLE001 - recorded for the UI
                    self._record_status(task_dir, task, "failed", error=str(exc))
                    try:
                        with log_path.open("a", encoding="utf-8") as log:
                            log.write(f"[{_now()}] driver error: {exc}\n")
                    except OSError:
                        pass
                finally:
                    self._drivers.pop(task_id, None)
                    self._clear_driver_lock(task_dir)

            thread = threading.Thread(target=run, name=f"driver-{task_id}", daemon=True)
            self._drivers[task_id] = {"running": True, "thread": thread, "stop": stop, "started_at": _now()}
            thread.start()
            return {"ok": True, "started": True, **self.driver_state(task_id)}

    def stop_auto(self, task_id: str) -> dict[str, Any]:
        driver = self._drivers.get(task_id)
        if driver is None:
            return {"ok": True, "running": False}
        stop = driver.get("stop")
        if isinstance(stop, threading.Event):
            stop.set()
        return {"ok": True, "stopping": True}

    def driver_state(self, task_id: str) -> dict[str, Any]:
        driver = self._drivers.get(task_id) or {}
        thread = driver.get("thread")
        return {
            "running": bool(driver.get("running") and getattr(thread, "is_alive", lambda: False)()),
            "startedAt": driver.get("started_at"),
        }

    # ── projections / approvals ─────────────────────────────────────────────

    def snapshot(self, task_id: str) -> dict[str, Any]:
        from aedt_agent.agent.workbench import workbench_snapshot

        workspace, _, task = self._resolve_task(task_id)
        runtime = self._task_runtime(task_id, task)
        payload = workbench_snapshot(runtime, str(task["graph_run_id"]))
        payload["task"] = task
        payload["driver"] = self.driver_state(task_id)
        # The legacy read-only projection reports workflow_* false; this task
        # surface is where those actions ARE allowed (the workbench owns the
        # strict workflow), so state it explicitly instead of overloading it.
        payload["task_capabilities"] = {
            "task_advance": True,
            "task_auto": True,
            "approval_decide": True,
            "task_create": True,
            "aedt_write": True,
        }
        return payload

    def list_approvals(self, task_id: str) -> list[dict[str, Any]]:
        _, _, task = self._resolve_task(task_id)
        runtime = self._task_runtime(task_id, task)
        return [item.to_json_dict() for item in runtime.store.list_approvals(str(task["mission_id"]))]

    def decide(self, task_id: str, approval_id: str, decision: str, comment: str | None = None) -> dict[str, Any]:
        from aedt_agent.agent.approvals import ApprovalService

        workspace, task_dir, task = self._resolve_task(task_id)
        runtime = self._task_runtime(task_id, task)
        service = ApprovalService(runtime.store)
        normalized = decision.strip().lower()
        with self._lock(task_id):
            if normalized in {"approved", "approve"}:
                approval = service.approve(approval_id, "approve", comment)
            elif normalized in {"rejected", "reject"}:
                approval = service.reject(approval_id, comment)
            else:
                raise TaskError("task/invalid-decision", f"unknown decision {decision!r}")
            self._record_status(task_dir, task, "waiting_approval")
        return {"approval_id": approval_id, "decision": approval.decision.value}

    # ── helpers ─────────────────────────────────────────────────────────────

    def _build_loop_config(
        self,
        task_dir: Path,
        workspace: Workspace,
        params: dict[str, Any],
        *,
        template_id: str,
        goal: str,
    ) -> dict[str, Any]:
        runs_dir = task_dir / "runs"
        config: dict[str, Any] = {
            "schema": "aihfss-loop/v1",
            "goal": goal,
            "template_id": template_id,
            "run_root": runs_dir.as_posix(),
            "report_dir": (runs_dir / "optimization_progress").as_posix(),
            "artifact_dir": (task_dir / "artifacts").as_posix(),
            "candidate_action_inventory_path": (runs_dir / "candidate_action_inventory.json").as_posix(),
            "max_rounds": int(params.get("max_rounds") or 1),
            "max_steps": int(params.get("max_steps") or 64),
            "poll_interval_seconds": int(params.get("poll_interval_seconds") or DEFAULT_POLL_SECONDS),
        }
        working = params.get("working_project_path")
        if working:
            config["working_project_path"] = str(self._absolute(str(working), workspace.root, task_dir))
        else:
            source = params.get("source_project_path")
            if source:
                config["working_project_path"] = (
                    runs_dir / ("working/" + Path(str(source)).name)
                ).as_posix()
        source_path = params.get("source_project_path")
        if source_path:
            config["source_project_path"] = str(self._absolute(str(source_path), workspace.root, task_dir))
        for key in (
            "setup_name",
            "sweep_name",
            "tdr_expression",
            "tdr_report_name",
            "tdr_observation_port",
            "tdr_differential_pairs",
            "expected_port_count",
            "touchstone_name",
            "sparameter_mode",
            "run_analyze",
            "export_tdr",
            "frequency_start_ghz",
            "frequency_stop_ghz",
            "rl_target_db",
            "tdr_target_ohm",
            "tdr_reference_impedance_ohm",
            "reference_impedance_ohm",
            "tdr_tolerance_ohm",
            "tdr_plot_time_stop_ps",
            "geometry_constraints",
            "candidate_action_policy",
            "signal_nets",
            "reference_nets",
            "layout_file",
            "bbox",
            "reset_working_project",
            "design_name",
            "frequency_ghz",
            "smoke_sweep_name",
            "solve_timeout_seconds",
            "aedt_port",
        ):
            if key in params and params[key] is not None:
                config[key] = params[key]
        return config

    @staticmethod
    def _absolute(value: str, workspace_root: Path, task_dir: Path) -> Path:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate
        # Relative inputs resolve against the workspace, never against the process cwd.
        resolved = (workspace_root / candidate).resolve()
        if not _is_within(resolved, workspace_root):
            raise TaskError("task/path-escape", f"path escapes the workspace: {value}")
        return resolved

    def _portable_params(self, params: dict[str, Any], task_dir: Path) -> dict[str, Any]:
        portable: dict[str, Any] = {}
        for key, value in params.items():
            if key in {"source_project_path", "working_project_path", "layout_file"} and isinstance(value, str):
                portable[key] = value
            elif isinstance(value, (str, int, float, bool)) or value is None:
                portable[key] = value
            elif isinstance(value, (list, dict)):
                portable[key] = value
        return portable

    def _record_status(
        self,
        task_dir: Path,
        task: dict[str, Any],
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        task["status"] = status or task.get("status") or "unknown"
        task["updated_at"] = _now()
        if error is not None:
            task["error"] = error
        try:
            _write_json(task_dir / TASK_FILE, task)
        except OSError:
            pass

    def _write_driver_lock(self, task_dir: Path) -> None:
        lock_path = task_dir / LOCK_FILE
        _write_json(lock_path, {"pid": os.getpid(), "started_at": _now()})

    def _clear_driver_lock(self, task_dir: Path) -> None:
        try:
            (task_dir / LOCK_FILE).unlink(missing_ok=True)
        except OSError:
            pass

    def _assert_driver_free(self, task_id: str, task_dir: Path) -> None:
        """Refuse a manual advance while another process drives this task."""
        driver = self._drivers.get(task_id)
        if driver and driver.get("running"):
            return  # our own thread re-enters through the same RLock
        lock_path = task_dir / LOCK_FILE
        if not lock_path.is_file():
            return
        try:
            payload = _read_json(lock_path)
        except (OSError, json.JSONDecodeError, TaskError):
            return
        pid = payload.get("pid")
        if isinstance(pid, int) and pid != os.getpid():
            if not pid_alive(pid):
                # Stale lock from a dead process: safe to clear.
                self._clear_driver_lock(task_dir)
                return
            raise TaskError(
                "task/driver-locked",
                f"task is being driven by process {pid}",
                pid=pid,
            )
