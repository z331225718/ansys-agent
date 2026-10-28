"""Workspace/task service tests: one mission database per optimization task."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workbench_tasks import (
    LOCK_FILE,
    LOOP_FILE,
    SYSTEM_DIR_NAME,
    TASK_DB,
    TASK_FILE,
    WORKSPACE_FILE,
    TaskError,
    TaskService,
    WorkspaceRegistry,
)
from aedt_agent.infrastructure import SQLiteMissionStore

TEMPLATE_MAPPING = {
    "id": "test_program_graph",
    "version": 1,
    "nodes": [
        {"id": "source", "role": "planner", "kind": "program"},
        {"id": "validator", "role": "validator", "kind": "program"},
    ],
    "edges": [{"id": "source-validator", "from": "source", "to": "validator", "on": "succeeded"}],
    "handoffs": {},
}


def _runtime_factory(db: Path, profile: object) -> AgentRuntime:
    return AgentRuntime(SQLiteMissionStore(db))


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskService:
    template = graph_template_from_mapping(TEMPLATE_MAPPING)
    monkeypatch.setattr("aedt_agent.agent.graph_template.load_graph_template", lambda _id: template)
    monkeypatch.setattr("aedt_agent.agent.graph_template.resolve_template_path", lambda _id: Path("unused.yaml"))
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    return TaskService(registry, runtime_factory=_runtime_factory)


def _workspace(service: TaskService, tmp_path: Path, name: str = "proj") -> dict:
    return service.open_workspace(tmp_path / name)


def _task(service: TaskService, workspace_id: str, **overrides: object) -> dict:
    params = {"goal": "reduce via stub", "name": "stub task", "template_id": "test_program_graph"}
    params.update(overrides)
    return service.create_task(workspace_id, params)


def test_open_workspace_writes_manifest_and_registry(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    root = Path(workspace["root"])
    manifest = json.loads((root / SYSTEM_DIR_NAME / WORKSPACE_FILE).read_text(encoding="utf-8"))
    assert manifest["schema"] == "aihfss-workspace/v1"
    assert manifest["workspace_id"] == workspace["workspace_id"]
    assert workspace["workspace_id"].startswith("ws-")
    assert [item["workspace_id"] for item in service.list_workspaces()] == [workspace["workspace_id"]]
    assert service.forget_workspace(workspace["workspace_id"]) is True
    assert service.list_workspaces() == []


def test_open_workspace_is_idempotent(service: TaskService, tmp_path: Path) -> None:
    first = _workspace(service, tmp_path)
    (Path(first["root"]) / "some.txt").write_text("keep me", encoding="utf-8")
    second = service.open_workspace(first["root"])
    assert second["workspace_id"] == first["workspace_id"]
    assert len(service.list_workspaces()) == 1
    assert (Path(first["root"]) / "some.txt").read_text(encoding="utf-8") == "keep me"


def test_each_task_gets_its_own_database(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    one = _task(service, workspace["workspace_id"], goal="task one")
    two = _task(service, workspace["workspace_id"], goal="task two")

    assert one["task_id"] != two["task_id"]
    assert one["mission_id"] != two["mission_id"]
    assert one["graph_run_id"] != two["graph_run_id"]

    workspace_root = Path(workspace["root"])
    first_dir = workspace_root / SYSTEM_DIR_NAME / "tasks" / one["task_id"]
    second_dir = workspace_root / SYSTEM_DIR_NAME / "tasks" / two["task_id"]
    for directory in (first_dir, second_dir):
        assert (directory / TASK_FILE).is_file()
        assert (directory / TASK_DB).is_file()
        assert (directory / LOOP_FILE).is_file()
        assert (directory / "runs").is_dir()
        assert (directory / "artifacts").is_dir()

    # The two databases hold only their own mission/graph rows.
    for directory, task in ((first_dir, one), (second_dir, two)):
        store = SQLiteMissionStore.open_read_only(directory / TASK_DB)
        assert [m.mission_id for m in store.list_missions()] == [task["mission_id"]]
        assert [g.graph_run_id for g in store.list_graph_runs(task["mission_id"])] == [task["graph_run_id"]]

    listed = service.list_tasks(workspace["workspace_id"])
    assert {item["task_id"] for item in listed} == {one["task_id"], two["task_id"]}


def test_loop_config_paths_stay_inside_the_task(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    task = _task(service, workspace["workspace_id"], source_project_path="input/case.aedt")
    task_dir = Path(workspace["root"]) / SYSTEM_DIR_NAME / "tasks" / task["task_id"]
    config = json.loads((task_dir / LOOP_FILE).read_text(encoding="utf-8"))
    for key in ("run_root", "report_dir", "artifact_dir", "working_project_path"):
        value = str(config.get(key) or "")
        assert value, key
        assert value.replace("\\", "/").startswith(task_dir.as_posix().replace("\\", "/")), (key, value)
    assert config["graph_run_id"] == task["graph_run_id"]
    assert config["mission_id"] == task["mission_id"]


def test_advance_runs_one_wave_and_records_status(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    task = _task(service, workspace["workspace_id"])
    first = service.advance(task["task_id"])
    assert first["status"] in {"running", "succeeded"}
    second = service.advance(task["task_id"])
    assert second["status"] == "succeeded"
    refreshed = service.get_task(task["task_id"])
    assert refreshed["status"] == "succeeded"


def test_advance_refuses_when_another_process_drives_the_task(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    task = _task(service, workspace["workspace_id"])
    task_dir = Path(workspace["root"]) / SYSTEM_DIR_NAME / "tasks" / task["task_id"]

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (task_dir / LOCK_FILE).write_text(json.dumps({"pid": sleeper.pid, "started_at": "now"}), encoding="utf-8")
        with pytest.raises(TaskError) as excinfo:
            service.advance(task["task_id"])
        assert excinfo.value.code == "task/driver-locked"
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=10)


def test_stale_driver_lock_is_cleared(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    task = _task(service, workspace["workspace_id"])
    task_dir = Path(workspace["root"]) / SYSTEM_DIR_NAME / "tasks" / task["task_id"]
    # subprocess.run closes its handle, so this pid is genuinely dead afterwards.
    completed = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    dead_pid = int(completed.stdout.strip())
    (task_dir / LOCK_FILE).write_text(json.dumps({"pid": dead_pid, "started_at": "now"}), encoding="utf-8")

    result = service.advance(task["task_id"])
    assert result["status"] in {"running", "succeeded"}
    assert not (task_dir / LOCK_FILE).exists()


def test_auto_driver_runs_to_terminal_state(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    task = _task(service, workspace["workspace_id"])
    started = service.start_auto(task["task_id"], poll_seconds=1)
    assert started["ok"] is True

    deadline = time.time() + 60
    while time.time() < deadline:
        record = service.get_task(task["task_id"])
        if record.get("status") in {"succeeded", "failed"}:
            break
        time.sleep(0.2)
    service.stop_auto(task["task_id"])
    assert service.get_task(task["task_id"])["status"] == "succeeded"


def test_decide_rejects_unknown_decision(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    task = _task(service, workspace["workspace_id"])
    with pytest.raises(TaskError) as excinfo:
        service.decide(task["task_id"], "approval-1", "maybe")
    assert excinfo.value.code == "task/invalid-decision"


def test_invalid_task_id_is_rejected(service: TaskService) -> None:
    with pytest.raises(TaskError) as excinfo:
        service.get_task("../../etc/passwd")
    assert excinfo.value.code == "task/invalid-id"


def test_relative_source_outside_workspace_is_rejected(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    with pytest.raises(TaskError) as excinfo:
        _task(service, workspace["workspace_id"], source_project_path="../../../outside.aedt")
    assert excinfo.value.code == "task/path-escape"


def test_goal_is_required(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    with pytest.raises(TaskError) as excinfo:
        service.create_task(workspace["workspace_id"], {"goal": "   "})
    assert excinfo.value.code == "task/goal-required"


def test_unknown_workspace_is_reported(service: TaskService) -> None:
    with pytest.raises(TaskError) as excinfo:
        service.list_tasks("ws-does-not-exist")
    assert excinfo.value.code == "workspace/not-found"

# ── HTTP API layer ──────────────────────────────────────────────────────────


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskService:
    from aedt_agent.agent import web as web_module

    template = graph_template_from_mapping(TEMPLATE_MAPPING)
    monkeypatch.setattr("aedt_agent.agent.graph_template.load_graph_template", lambda _id: template)
    monkeypatch.setattr("aedt_agent.agent.graph_template.resolve_template_path", lambda _id: Path("unused.yaml"))
    service = TaskService(
        WorkspaceRegistry(tmp_path / "workspaces.json"),
        runtime_factory=_runtime_factory,
    )
    monkeypatch.setattr(web_module, "_workbench_task_service", service)
    return service


def _call(method: str, route: str, body: dict | None = None) -> tuple[int, dict]:
    from aedt_agent.agent.web import _handle_workbench_tasks

    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    status, _headers, payload = _handle_workbench_tasks(method, route, raw)
    return status, json.loads(payload.decode("utf-8"))


def test_api_workspace_and_task_roundtrip(api: TaskService, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    status, payload = _call("POST", "/api/workbench/workspaces", {"root": str(root), "name": "p"})
    assert status == 201
    workspace_id = payload["workspace"]["workspace_id"]

    status, payload = _call("GET", "/api/workbench/workspaces")
    assert status == 200
    assert [item["workspace_id"] for item in payload["workspaces"]] == [workspace_id]

    status, payload = _call(
        "POST",
        f"/api/workbench/workspaces/{workspace_id}/tasks",
        {"goal": "api goal", "template_id": "test_program_graph"},
    )
    assert status == 201
    task_id = payload["task"]["task_id"]

    status, payload = _call("GET", f"/api/workbench/workspaces/{workspace_id}/tasks")
    assert status == 200
    assert [item["task_id"] for item in payload["tasks"]] == [task_id]

    status, payload = _call("POST", f"/api/workbench/tasks/{task_id}/advance")
    assert status == 200
    assert payload["status"] in {"running", "succeeded"}

    status, payload = _call("GET", f"/api/workbench/tasks/{task_id}")
    assert status == 200
    assert payload["task"]["task_id"] == task_id


def test_api_reports_unknown_routes_and_errors(api: TaskService) -> None:
    status, payload = _call("GET", "/api/workbench/workspaces/nope/tasks")
    assert status == 404
    assert payload["code"] == "workspace/not-found"

    # Path traversal never reaches the service: unknown route shape is a 404.
    status, _payload = _call("GET", "/api/workbench/tasks/../../etc")
    assert status == 404

    status, payload = _call("GET", "/api/workbench/unknown-thing")
    assert status == 404


def test_api_forget_workspace(api: TaskService, tmp_path: Path) -> None:
    status, payload = _call("POST", "/api/workbench/workspaces", {"root": str(tmp_path / "p2")})
    workspace_id = payload["workspace"]["workspace_id"]
    status, payload = _call("POST", "/api/workbench/workspaces/forget", {"workspace_id": workspace_id})
    assert status == 200
    assert payload["forgotten"] is True

def test_delete_task_requires_confirmation_and_removes_only_that_task(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    keep = _task(service, workspace["workspace_id"], name="keep me")
    doomed = _task(service, workspace["workspace_id"], name="delete me")
    doomed_dir = Path(workspace["root"]) / SYSTEM_DIR_NAME / "tasks" / doomed["task_id"]
    assert (doomed_dir / TASK_DB).is_file()

    with pytest.raises(TaskError) as missing:
        service.delete_task(doomed["task_id"])
    assert missing.value.code == "task/confirm-required"
    with pytest.raises(TaskError) as wrong:
        service.delete_task(doomed["task_id"], confirm="T-00000000-000000-0000")
    assert wrong.value.code == "task/confirm-required"
    assert doomed_dir.is_dir(), "a mismatched confirm must not delete anything"

    result = service.delete_task(doomed["task_id"], confirm=doomed["task_id"])
    assert result == {
        "ok": True,
        "deleted": True,
        "task_id": doomed["task_id"],
        "workspace_id": workspace["workspace_id"],
        "path": str(doomed_dir.resolve()),
    }
    assert not doomed_dir.exists()
    assert [item["task_id"] for item in service.list_tasks(workspace["workspace_id"])] == [keep["task_id"]]
    with pytest.raises(TaskError) as gone:
        service.get_task(doomed["task_id"])
    assert gone.value.code == "task/not-found"


def test_delete_task_refuses_while_the_auto_driver_runs(service: TaskService, tmp_path: Path) -> None:
    workspace = _workspace(service, tmp_path)
    task = _task(service, workspace["workspace_id"])
    service.start_auto(task["task_id"], poll_seconds=1)
    try:
        with pytest.raises(TaskError) as busy:
            service.delete_task(task["task_id"], confirm=task["task_id"])
        assert busy.value.code == "task/busy"
    finally:
        service.stop_auto(task["task_id"])
    # After the driver stops the task can be removed.
    deadline = time.time() + 5
    while service.driver_state(task["task_id"])["running"] and time.time() < deadline:
        time.sleep(0.05)
    service.delete_task(task["task_id"], confirm=task["task_id"])
    assert service.list_tasks(workspace["workspace_id"]) == []


def test_api_delete_task(api: TaskService, tmp_path: Path) -> None:
    status, payload = _call("POST", "/api/workbench/workspaces", {"root": str(tmp_path / "del")})
    workspace_id = payload["workspace"]["workspace_id"]
    status, payload = _call(
        "POST",
        f"/api/workbench/workspaces/{workspace_id}/tasks",
        {"goal": "delete me", "template_id": "test_program_graph"},
    )
    task_id = payload["task"]["task_id"]

    status, payload = _call("POST", f"/api/workbench/tasks/{task_id}/delete", {})
    assert status == 400 and payload["code"] == "task/confirm-required"

    status, payload = _call("POST", f"/api/workbench/tasks/{task_id}/delete", {"confirm": task_id})
    assert status == 200 and payload["deleted"] is True

    status, payload = _call("GET", f"/api/workbench/tasks/{task_id}")
    assert status == 404 and payload["code"] == "task/not-found"
    status, payload = _call("GET", f"/api/workbench/workspaces/{workspace_id}/tasks")
    assert payload["tasks"] == []

def test_snapshot_and_artifact_route_expose_the_stackup_plot(api: TaskService, tmp_path: Path) -> None:
    from aedt_agent.agent.web import _handle_workbench_tasks

    status, payload = _call("POST", "/api/workbench/workspaces", {"root": str(tmp_path / "plot")})
    workspace_id = payload["workspace"]["workspace_id"]
    status, payload = _call(
        "POST",
        f"/api/workbench/workspaces/{workspace_id}/tasks",
        {"goal": "plot task", "template_id": "test_program_graph"},
    )
    task_id = payload["task"]["task_id"]
    artifacts = tmp_path / "plot" / SYSTEM_DIR_NAME / "tasks" / task_id / "artifacts"
    (artifacts / "stackup_plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    status, payload = _call("GET", f"/api/workbench/tasks/{task_id}")
    assert status == 200
    plot = payload["model"]["stackup_plot"]
    assert plot["available"] is True and plot["url"].endswith("/stackup_plot.png")

    status, headers, body = _handle_workbench_tasks(
        "GET", f"/api/workbench/tasks/{task_id}/artifact/stackup_plot.png", b""
    )
    assert status == 200 and headers["content-type"] == "image/png"
    assert body.startswith(b"\x89PNG")

    status, _headers, body = _handle_workbench_tasks(
        "GET", f"/api/workbench/tasks/{task_id}/artifact/evil.png", b""
    )
    assert status == 400 and json.loads(body.decode("utf-8"))["code"] == "task/artifact-not-allowed"
    status, _headers, body = _handle_workbench_tasks(
        "GET", f"/api/workbench/tasks/{task_id}/artifact/other_plot.png", b""
    )
    assert status == 400
