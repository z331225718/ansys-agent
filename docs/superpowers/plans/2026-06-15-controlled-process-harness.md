# Controlled Process Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Agent Runtime 增加受控本地子进程执行、工作区与环境策略、超时取消、心跳恢复、资源门控和可审计 artifact。

**Architecture:** `aedt_agent.infrastructure.harness` 实现版本化文件协议和本地进程机制；Worker Registry 保存 `in_process`/`local_process` 执行规格；AgentRuntime 仍是唯一 Job 执行入口，并把 Harness 事实映射到现有 JobAttempt、JobError、retry 和 DAG 语义。容器只保留接口，不在本计划启动 Docker。

**Tech Stack:** Python 3.11+、dataclasses、JSON、`subprocess.Popen`、`threading`、`pathlib`、SQLite、pytest。

---

## 文件结构

- `src/aedt_agent/infrastructure/harness/contracts.py`：Harness 协议和状态。
- `src/aedt_agent/infrastructure/harness/workspace.py`：Attempt 目录、路径约束、环境白名单。
- `src/aedt_agent/infrastructure/harness/resources.py`：资源分类 semaphore。
- `src/aedt_agent/infrastructure/harness/child_main.py`：子进程协议入口。
- `src/aedt_agent/infrastructure/harness/local_process.py`：进程生命周期、日志、超时、取消。
- `src/aedt_agent/infrastructure/harness/recovery.py`：Attempt 扫描和中断分类。
- `src/aedt_agent/agent/workers/registry.py`：WorkerRegistration 与执行路由。
- `src/aedt_agent/agent/orchestrator/runtime.py`：Harness/JobAttempt/Artifact 集成。
- `src/aedt_agent/agent/mission/contracts.py`：Attempt metadata 与 canceled error。
- `src/aedt_agent/infrastructure/sqlite_mission_store.py`：Attempt metadata migration。
- `src/aedt_agent/agent/policies/execution_profile.py`：Harness 配置。
- `src/aedt_agent/agent/cli.py`：恢复命令和状态输出。

## Task 1：定义 Harness 协议契约

**Files:**
- Create: `src/aedt_agent/infrastructure/harness/__init__.py`
- Create: `src/aedt_agent/infrastructure/harness/contracts.py`
- Create: `tests/test_agent_harness_contracts.py`
- Modify: `src/aedt_agent/infrastructure/__init__.py`

- [ ] **Step 1：写失败测试**

```python
def test_harness_request_round_trips_json():
    request = HarnessRequest.create(
        harness_run_id="run-1",
        mission_id="m1",
        job_id="j1",
        attempt_id="a1",
        worker_id="w1",
        capability="fake.echo",
        entrypoint="tests.fixtures.process_workers:echo_worker",
        timeout_seconds=10,
        heartbeat_interval_seconds=1,
        input_payload={"value": 2},
        workspace="C:/tmp/a1",
    )
    assert HarnessRequest.from_json_dict(request.to_json_dict()) == request


def test_harness_result_rejects_wrong_protocol_version():
    with pytest.raises(HarnessProtocolError, match="protocol_version"):
        HarnessResult.from_json_dict({"protocol_version": 99})
```

再覆盖非法 status、空 entrypoint、非正 timeout、run ID 不匹配。

- [ ] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_contracts.py
```

Expected: `aedt_agent.infrastructure.harness` 不存在。

- [ ] **Step 3：实现最小协议**

```python
class HarnessStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class HarnessRequest:
    protocol_version: int
    harness_run_id: str
    mission_id: str
    job_id: str
    attempt_id: str
    worker_id: str
    capability: str
    entrypoint: str
    timeout_seconds: int
    heartbeat_interval_seconds: int
    input_payload: dict[str, Any]
    workspace: str
```

`HarnessResult` 包含 `output_payload`、`artifact_refs`、`error`、时间、exit code 和 termination reason。所有 `from_json_dict()` 拒绝未知协议版本和错误类型。

- [ ] **Step 4：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_contracts.py
git add src/aedt_agent/infrastructure/harness src/aedt_agent/infrastructure/__init__.py tests/test_agent_harness_contracts.py
git commit -m "feat: define process harness protocol"
```

## Task 2：实现安全工作区与环境白名单

**Files:**
- Create: `src/aedt_agent/infrastructure/harness/workspace.py`
- Create: `tests/test_agent_harness_workspace.py`

- [ ] **Step 1：写失败测试**

```python
def test_workspace_rejects_path_escape(tmp_path):
    policy = HarnessWorkspacePolicy(tmp_path / "harness")
    with pytest.raises(HarnessWorkspaceError, match="path segment"):
        policy.create_attempt("../mission", "job", "attempt")


def test_child_environment_contains_only_base_and_allowed_names(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    monkeypatch.setenv("SECRET_TOKEN", "do-not-copy")
    monkeypatch.setenv("AWP_ROOT261", "C:/Ansys")
    env = build_child_environment(["AWP_ROOT261"])
    assert env["AWP_ROOT261"] == "C:/Ansys"
    assert "SECRET_TOKEN" not in env
```

另覆盖目录布局、resolved path 必须位于 root、请求/结果/log/artifact 路径。

- [ ] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_workspace.py
```

- [ ] **Step 3：实现策略**

```python
@dataclass(frozen=True)
class HarnessWorkspace:
    root: Path
    request_path: Path
    result_path: Path
    heartbeat_path: Path
    stdout_path: Path
    stderr_path: Path
    artifacts_dir: Path


class HarnessWorkspacePolicy:
    def create_attempt(self, mission_id, job_id, attempt_id) -> HarnessWorkspace:
        parts = [_validate_segment(value) for value in (mission_id, job_id, attempt_id)]
        root = self.root.joinpath(*parts).resolve()
        if not root.is_relative_to(self.root.resolve()):
            raise HarnessWorkspaceError("attempt workspace escapes harness root")
        root.mkdir(parents=True, exist_ok=False)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        return HarnessWorkspace(
            root=root,
            request_path=root / "request.json",
            result_path=root / "result.json",
            heartbeat_path=root / "heartbeat.json",
            stdout_path=root / "stdout.log",
            stderr_path=root / "stderr.log",
            artifacts_dir=artifacts,
        )
```

`_validate_segment()` 拒绝空字符串、`.`、`..`、路径分隔符和绝对路径。`build_child_environment()` 只复制固定基础变量与调用方 allowed names。

- [ ] **Step 4：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_workspace.py
git add src/aedt_agent/infrastructure/harness/workspace.py tests/test_agent_harness_workspace.py
git commit -m "feat: isolate harness workspaces and environment"
```

## Task 3：实现资源门控

**Files:**
- Create: `src/aedt_agent/infrastructure/harness/resources.py`
- Create: `tests/test_agent_harness_resources.py`

- [ ] **Step 1：写失败测试**

```python
def test_aedt_resource_gate_serializes_two_workers():
    gate = ResourceGate(max_concurrent_aedt=1, max_concurrent_license_jobs=1)
    entered = []
    release = threading.Event()

    def worker(name):
        with gate.acquire("aedt", timeout_seconds=2):
            entered.append(name)
            release.wait(timeout=2)

    first = threading.Thread(target=worker, args=("first",))
    second = threading.Thread(target=worker, args=("second",))
    first.start()
    while entered != ["first"]:
        time.sleep(0.01)
    second.start()
    time.sleep(0.1)
    assert entered == ["first"]
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)
```

另写 license 独立上限、未知 resource class 拒绝、等待 timeout 测试。

- [ ] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_resources.py
```

- [ ] **Step 3：实现 ResourceGate**

```python
class ResourceGate:
    def acquire(self, resource_class: str, timeout_seconds: float) -> AbstractContextManager[ResourceLease]:
        semaphore = self._semaphores.get(resource_class)
        if semaphore is None:
            raise ValueError(f"unsupported resource class: {resource_class}")
        acquired = semaphore.acquire(timeout=timeout_seconds)
        if not acquired:
            raise ResourceAcquireTimeout(resource_class, timeout_seconds)
        return ResourceLease(resource_class, semaphore)
```

`cpu` 使用通用 semaphore，`aedt` 和 `license` 使用独立 semaphore；timeout 抛 `ResourceAcquireTimeout`。

- [ ] **Step 4：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_resources.py
git add src/aedt_agent/infrastructure/harness/resources.py tests/test_agent_harness_resources.py
git commit -m "feat: gate harness execution resources"
```

## Task 4：实现子进程入口与测试 Worker

**Files:**
- Create: `src/aedt_agent/infrastructure/harness/child_main.py`
- Create: `tests/fixtures/process_workers.py`
- Create: `tests/test_agent_harness_child.py`

- [ ] **Step 1：写失败测试**

```python
def test_child_main_executes_entrypoint_and_writes_atomic_result(tmp_path):
    request = _request(tmp_path, "tests.fixtures.process_workers:echo_worker")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request.to_json_dict()), encoding="utf-8")

    exit_code = child_main.run(request_path)

    result = HarnessResult.from_json_dict(json.loads((tmp_path / "result.json").read_text()))
    assert exit_code == 0
    assert result.output_payload == {"value": 3}
    assert not (tmp_path / "result.json.tmp").exists()
```

另覆盖 entrypoint 不存在、Worker 抛异常、artifact_refs 规范化、heartbeat 写入。

- [ ] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_child.py
```

- [ ] **Step 3：实现 child_main**

入口命令：

```powershell
python -m aedt_agent.infrastructure.harness.child_main --request <request.json>
```

`module:function` 使用 `importlib.import_module` 导入。构造 `JobRecord` 和 `WorkerContext`，启动 daemon heartbeat thread，执行后通过 `os.replace()` 原子提交结果。

- [ ] **Step 4：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_child.py
git add src/aedt_agent/infrastructure/harness/child_main.py tests/fixtures/process_workers.py tests/test_agent_harness_child.py
git commit -m "feat: execute worker entrypoints in child process"
```

## Task 5：实现本地 Process Harness 成功、日志与错误路径

**Files:**
- Create: `src/aedt_agent/infrastructure/harness/local_process.py`
- Create: `tests/test_agent_local_process_harness.py`

- [ ] **Step 1：写失败测试**

```python
def test_local_process_harness_captures_logs_and_result(tmp_path):
    harness = LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "runs"))
    result = harness.execute(
        _request("tests.fixtures.process_workers:logging_worker"),
        allowed_env=[],
        resource_class="cpu",
    )
    assert result.status == HarnessStatus.SUCCEEDED
    assert "worker stdout" in Path(result.metadata["stdout_path"]).read_text()
    assert "worker stderr" in Path(result.metadata["stderr_path"]).read_text()
```

另覆盖非零退出且无结果、损坏 result、run ID 不一致、Worker 结构化失败。

- [ ] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_local_process_harness.py
```

- [ ] **Step 3：实现 LocalProcessHarness**

使用：

```python
subprocess.Popen(
    [sys.executable, "-m", "aedt_agent.infrastructure.harness.child_main", "--request", str(request_path)],
    cwd=workspace.root,
    env=child_env,
    stdout=stdout_handle,
    stderr=stderr_handle,
    stdin=subprocess.DEVNULL,
    shell=False,
    **process_group_options,
)
```

请求、stdout、stderr 和结果文件全部加入 HarnessResult metadata/artifact_refs。

- [ ] **Step 4：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_local_process_harness.py
git add src/aedt_agent/infrastructure/harness/local_process.py tests/test_agent_local_process_harness.py
git commit -m "feat: run workers in controlled local processes"
```

## Task 6：实现 timeout、cancel 与进程树清理

**Files:**
- Modify: `src/aedt_agent/infrastructure/harness/local_process.py`
- Modify: `tests/fixtures/process_workers.py`
- Modify: `tests/test_agent_local_process_harness.py`

- [ ] **Step 1：写失败测试**

```python
def test_local_process_timeout_terminates_worker(tmp_path):
    result = _harness(tmp_path).execute(
        _request("tests.fixtures.process_workers:sleep_worker", timeout_seconds=1)
    )
    assert result.status == HarnessStatus.TIMED_OUT
    assert result.termination_reason == "wall_timeout"


def test_cancel_terminates_spawned_child_process_tree(tmp_path):
    marker = tmp_path / "child.pid"
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    try:
        result = _harness(tmp_path).execute(
            _request(
                "tests.fixtures.process_workers:spawn_child_worker",
                input_payload={"pid_path": str(marker)},
                timeout_seconds=10,
            ),
            cancel_requested=cancel.is_set,
        )
    finally:
        timer.cancel()
    assert result.status == HarnessStatus.CANCELED
    assert not process_is_alive(int(marker.read_text()))
```

- [ ] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_local_process_harness.py -k "timeout or cancel"
```

- [ ] **Step 3：实现进程控制器**

定义可测试接口：

```python
class ProcessTreeController:
    def popen_options(self) -> dict[str, Any]:
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    def terminate_tree(self, process, grace_seconds: float) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
```

Windows 使用 `CREATE_NEW_PROCESS_GROUP`，强制阶段执行 `taskkill /PID <pid> /T /F`；POSIX 使用 `start_new_session=True` 和 `os.killpg()`。不使用 shell。

- [ ] **Step 4：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_local_process_harness.py
git add src/aedt_agent/infrastructure/harness/local_process.py tests/fixtures/process_workers.py tests/test_agent_local_process_harness.py
git commit -m "feat: terminate timed out harness process trees"
```

## Task 7：扩展 Worker Registry 并接入 AgentRuntime

**Files:**
- Modify: `src/aedt_agent/agent/workers/registry.py`
- Modify: `src/aedt_agent/agent/workers/__init__.py`
- Modify: `src/aedt_agent/agent/orchestrator/runtime.py`
- Modify: `src/aedt_agent/agent/mission/contracts.py`
- Modify: `src/aedt_agent/agent/mission/__init__.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- Modify: `tests/test_agent_runtime_service.py`
- Create: `tests/test_agent_runtime_harness.py`

- [ ] **Step 1：写 WorkerRegistration 失败测试**

```python
def test_registry_routes_local_process_registration_to_harness(tmp_path):
    registry = InMemoryWorkerRegistry(harness=_fake_harness())
    registry.register_process(
        "fake.echo",
        "tests.fixtures.process_workers:echo_worker",
        resource_class="cpu",
        allowed_env=("TEST_VALUE",),
    )
    result = registry.execute(job, WorkerContext("w"), attempt_id="a1")
    assert result.status == JobStatus.SUCCEEDED
```

覆盖 mode/handler/entrypoint/resource class 校验，保留现有 `register()` 兼容。

- [ ] **Step 2：写 Attempt metadata 持久化失败测试**

```python
def test_job_attempt_persists_harness_metadata(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "echo:1", {})
    attempt = JobAttemptRecord.create(
        attempt_id="attempt-1",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_number=1,
        worker_id="worker-1",
        metadata={"harness_run_id": "h1"},
    )
    store.create_job_attempt(attempt)
    assert store.get_job_attempt(attempt.attempt_id).metadata["harness_run_id"] == "h1"
```

- [ ] **Step 3：写 Runtime timeout retry 失败测试**

```python
def test_runtime_requeues_retryable_harness_timeout(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = FakeHarness(
        HarnessResult.timed_out("h1", job_id="placeholder", termination_reason="wall_timeout")
    )
    registry = InMemoryWorkerRegistry(harness=harness)
    registry.register_process("fake.slow", "tests.fixtures.process_workers:sleep_worker")
    runtime = AgentRuntime(store, registry=registry)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(
        mission.mission_id,
        "fake.slow",
        "slow:1",
        {},
        timeout_seconds=1,
        retry_limit=1,
    )
    harness.result = replace(harness.result, job_id=job.job_id)
    result = runtime.execute_job(job.job_id, "worker")
    assert result.error.error_class == ErrorClass.TIMEOUT
    assert runtime.get_job(job.job_id).status == JobStatus.QUEUED
    assert store.list_job_attempts(job.job_id)[0].retry_decision == "retry_available"
```

- [ ] **Step 4：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_runtime_service.py tests/test_agent_runtime_harness.py tests/test_agent_job_attempts.py
```

- [ ] **Step 5：实现注册与 Runtime 路由**

新增：

```python
@dataclass(frozen=True)
class WorkerRegistration:
    capability: str
    execution_mode: str
    handler: WorkerFn | None = None
    entrypoint: str = ""
    resource_class: str = "cpu"
    allowed_env: tuple[str, ...] = ()
```

`registry.execute(job, context, attempt_id=attempt.attempt_id)` 为 local process 构造 HarnessRequest。`WorkerExecutionResult` 增加 `metadata`。

`JobAttemptRecord` 增加 `metadata: JsonDict`；SQLite 增加 `metadata_json TEXT NOT NULL DEFAULT '{}'` 并使用 `_ensure_column()` 迁移。`ErrorClass` 增加 `CANCELED`。

- [ ] **Step 6：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_runtime_service.py tests/test_agent_runtime_harness.py tests/test_agent_job_attempts.py
git add src/aedt_agent/agent/workers src/aedt_agent/agent/orchestrator/runtime.py src/aedt_agent/agent/mission src/aedt_agent/infrastructure/sqlite_mission_store.py tests/test_agent_runtime_service.py tests/test_agent_runtime_harness.py tests/test_agent_job_attempts.py
git commit -m "feat: route registered workers through process harness"
```

## Task 8：实现 heartbeat recovery 与 CLI

**Files:**
- Create: `src/aedt_agent/infrastructure/harness/recovery.py`
- Modify: `src/aedt_agent/agent/orchestrator/runtime.py`
- Modify: `src/aedt_agent/agent/cli.py`
- Create: `tests/test_agent_harness_recovery.py`
- Create: `tests/test_agent_cli_harness.py`

- [ ] **Step 1：写 recovery 分类失败测试**

```python
def test_recovery_classifies_missing_process_as_interrupted(tmp_path):
    workspace = _write_attempt(tmp_path, pid=999999, heartbeat_age_seconds=120)
    scanner = HarnessRecoveryScanner(
        tmp_path,
        process_controller=FakeProcessController(alive_pids=set()),
        heartbeat_timeout_seconds=30,
    )
    record = scanner.inspect(workspace.root)
    assert record.classification == "interrupted"
```

覆盖 completed、active、stale、invalid。

- [ ] **Step 2：写 Runtime recovery 失败测试**

```python
def test_runtime_recovers_interrupted_harness_attempt_and_requeues_job(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store, harness_root=tmp_path / "harness")
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(mission.mission_id, "fake.echo", "echo:1", {}, retry_limit=1)
    lease = store.acquire_job_lease(job.job_id, "worker", lease_seconds=60)
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-1",
            mission.mission_id,
            job.job_id,
            1,
            "worker",
            metadata={"workspace": str(_write_attempt(tmp_path / "harness", pid=999999).root)},
        )
    )
    store.release_job_lease(lease.lease_id)
    report = runtime.recover_harness_attempts(mission_id)
    assert report["requeued_job_ids"] == [job.job_id]
    assert store.get_job_attempt(attempt.attempt_id).status == JobAttemptStatus.FAILED
```

- [ ] **Step 3：写 CLI 失败测试**

```python
def test_cli_recover_harness_reports_interrupted_attempt(tmp_path):
    result = _run(tmp_path, "mission", "recover-harness", "--mission-id", mission_id)
    assert result.returncode == 0
    assert json.loads(result.stdout)["interrupted_attempt_ids"] == [attempt_id]
```

- [ ] **Step 4：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_recovery.py tests/test_agent_cli_harness.py
```

- [ ] **Step 5：实现 scanner、Runtime 与 CLI**

```python
class HarnessRecoveryScanner:
    def scan(self, mission_id: str | None = None) -> list[HarnessRecoveryRecord]:
        roots = [self.root / mission_id] if mission_id else list(self.root.iterdir())
        attempt_dirs = [
            attempt
            for mission_root in roots
            if mission_root.exists()
            for job_root in mission_root.iterdir()
            if job_root.is_dir()
            for attempt in job_root.iterdir()
            if attempt.is_dir()
        ]
        return [self.inspect(path) for path in sorted(attempt_dirs)]
```

`AgentRuntime.recover_harness_attempts()` 只自动处理 `interrupted`；`active/stale` 返回报告。`--terminate-stale` 必须显式提供才调用进程树控制器。

- [ ] **Step 6：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_recovery.py tests/test_agent_cli_harness.py
git add src/aedt_agent/infrastructure/harness/recovery.py src/aedt_agent/agent/orchestrator/runtime.py src/aedt_agent/agent/cli.py tests/test_agent_harness_recovery.py tests/test_agent_cli_harness.py
git commit -m "feat: recover interrupted process harness attempts"
```

## Task 9：ExecutionProfile、端到端 DAG 与审计

**Files:**
- Modify: `src/aedt_agent/agent/policies/execution_profile.py`
- Modify: `src/aedt_agent/agent/cli.py`
- Create: `tests/test_agent_harness_graph_integration.py`
- Modify: `tests/test_agent_execution_profile.py`
- Create: `docs/pi-integration-evaluation-gate.md`

- [ ] **Step 1：写 Profile 失败测试**

新增字段：

```python
harness_root: str
heartbeat_interval_seconds: int
heartbeat_timeout_seconds: int
termination_grace_seconds: int
allowed_env: list[str]
```

测试正整数、环境变量名称格式和 `safe_recorded()` 默认值。

- [ ] **Step 2：写 DAG 端到端失败测试**

创建一个 local-process echo Worker，运行 planner -> worker -> scorecard 图，断言：

- GraphRun succeeded；
- Worker NodeRun 只执行一次；
- JobAttempt metadata 包含 harness_run_id/workspace；
- request/stdout/stderr/result 都有 ArtifactManifest；
- 模拟 timeout 时 Graph 不成功并按 retry 语义处理。

- [ ] **Step 3：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_execution_profile.py tests/test_agent_harness_graph_integration.py
```

- [ ] **Step 4：实现 Profile 与 CLI runtime construction**

`_runtime_with_workers()` 接收 Profile 时创建：

```python
LocalProcessHarness(
    HarnessWorkspacePolicy(Path(profile.harness_root)),
    ResourceGate(
        max_concurrent_aedt=profile.max_concurrent_aedt,
        max_concurrent_license_jobs=profile.max_concurrent_license_jobs,
    ),
    heartbeat_timeout_seconds=profile.heartbeat_timeout_seconds,
    termination_grace_seconds=profile.termination_grace_seconds,
)
```

现有 BRD Worker 默认继续 in-process。计划只证明可选择 local-process，不强制把含 store 闭包的 recorded action Worker 子进程化。

- [ ] **Step 5：写 Pi 评估门文档**

`docs/pi-integration-evaluation-gate.md` 必须记录：

- Harness 后 Pi 可接入的公共 API；
- 不允许 Pi 拥有的职责；
- PoC 指标和通过/拒绝阈值；
- 何时启动 PoC；
- 当前结论为 deferred，不是 rejected。

- [ ] **Step 6：跑 Agent 回归**

```powershell
$files = Get-ChildItem tests\test_agent_*.py | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest -q $files
```

Expected: 全部通过。

- [ ] **Step 7：静态审计**

```powershell
.\.venv\Scripts\python.exe -m compileall -q src\aedt_agent\agent src\aedt_agent\infrastructure
rg -n "aedt_agent\.v0" src\aedt_agent\agent src\aedt_agent\infrastructure
git diff --check
git status --short
```

Expected:

- compileall 成功；
- `rg` 无输出；
- 无 whitespace error；
- 只保留任务开始前的无关工作区改动。

- [ ] **Step 8：提交**

```powershell
git add src/aedt_agent/agent/policies/execution_profile.py src/aedt_agent/agent/cli.py tests/test_agent_execution_profile.py tests/test_agent_harness_graph_integration.py docs/pi-integration-evaluation-gate.md
git commit -m "feat: complete controlled process harness"
```

## 完成定义

- Worker 可选择 `in_process` 或 `local_process`；
- local process 使用版本化 JSON 协议；
- Attempt 工作区和环境白名单被测试；
- timeout/cancel 终止进程树；
- heartbeat recovery 能识别并恢复 interrupted Job；
- 资源门控限制 AEDT 与许可证并发；
- Harness 请求、结果、日志和 metadata 可审计；
- Runtime 与 DAG 使用相同 Job API；
- 现有 BRD Worker 行为不回退；
- Pi 有明确、可测量的后续评估门；
- Agent 测试全绿，Agent/Infrastructure 不依赖 `v0`。
