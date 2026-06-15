# Real BRD Solve Mission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让审批后的 BRD local-cut AEDT 工程通过受控子进程完成真实求解，导出 Touchstone/TDR，生成 bounded EvidencePackage，并在同一持久化 GraphRun 中完成 scorecard。

**Architecture:** `aedt_agent.infrastructure.brd_real_solve` 封装 AEDT 生命周期、checkpoint 副本、analyze 和结果导出；`brd.local_cut.solve` 作为 importable local-process Worker 运行。Harness 增加组合资源门控，Evidence 层增加 artifact window query，YAML DAG 只传 artifact refs 和 bounded summary。真实 AEDT smoke 默认跳过，fake adapter 覆盖持续集成。

**Tech Stack:** Python 3.11+、PyAEDT 2026.1 API、dataclasses、JSON/YAML、SQLite、Process Harness、pytest。

---

## 官方 API 依据

计划中的真实 adapter 使用以下 PyAEDT stable API：

- `Hfss3dLayout.analyze_setup(name, blocking=True)`：
  https://aedt.docs.pyansys.com/version/stable/API/_autosummary/ansys.aedt.core.hfss3dlayout.Hfss3dLayout.html
- `Hfss3dLayout.export_touchstone(setup, sweep, output_file)`：
  https://aedt.docs.pyansys.com/version/stable/API/_autosummary/ansys.aedt.core.hfss.Hfss.export_touchstone.html
- `PostProcessor3DLayout.create_report(expressions, setup_sweep_name, domain, context, plot_name)`：
  https://aedt.docs.pyansys.com/version/stable/API/visualization/_autosummary/ansys.aedt.core.visualization.post.post_3dlayout.PostProcessor3DLayout.create_report.html
- `PostProcessor3D.export_report_to_file(output_dir, plot_name, ".csv")`：
  https://aedt.docs.pyansys.com/version/stable/API/visualization/_autosummary/ansys.aedt.core.visualization.post.post_common_3d.PostProcessor3D.export_report_to_file.html

Context7 在制定计划时返回 403，因此实现前如 API 版本发生变化，应再次核对上述 Ansys 官方 stable 文档，不使用博客或未验证示例替代。

## 文件结构

- `src/aedt_agent/agent/workers/registry.py`：WorkerContext workspace、组合资源注册。
- `src/aedt_agent/infrastructure/harness/child_main.py`：注入受控 workspace/artifacts_dir，并保留 Worker 上报的结构化错误。
- `src/aedt_agent/infrastructure/harness/resources.py`：组合 semaphore lease。
- `src/aedt_agent/infrastructure/harness/local_process.py`：组合资源执行和 metadata。
- `src/aedt_agent/infrastructure/brd_real_solve.py`：真实 AEDT solve adapter。
- `src/aedt_agent/agent/workers/brd_real_solve.py`：local-process solve Worker。
- `src/aedt_agent/agent/evaluation/artifact_query.py`：S 参数/TDR bounded window query。
- `src/aedt_agent/agent/evaluation/query_service.py`：Mission artifact 校验和 query Event。
- `src/aedt_agent/infrastructure/sqlite_mission_store.py`：公共 append Event 接口。
- `docs/agent_templates/brd_real_solve_evidence.yaml`：approval -> solve -> score -> scorecard。
- `src/aedt_agent/agent/policies/execution_profile.py`：AEDT runtime profile。
- `src/aedt_agent/agent/scorecard.py`：真实 solve 审计检查。
- `src/aedt_agent/agent/cli.py`：Mission 创建、artifact query、Worker 注册。
- `tests/fixtures/process_workers.py`：workspace 与 fake solve fixtures。
- `tests/fixtures/fake_real_solve.py`：子进程可导入的 fake solve entrypoint。
- `tests/test_infrastructure_brd_real_solve.py`：adapter 契约。
- `tests/test_agent_brd_real_solve_worker.py`：Worker 契约。
- `tests/test_agent_artifact_query.py`：bounded query。
- `tests/test_agent_brd_real_solve_graph.py`：Graph 端到端。
- `tests/test_agent_brd_real_solve_smoke.py`：显式真实 AEDT smoke。

## Task 1：把受控 workspace 注入 WorkerContext

**Files:**
- Modify: `src/aedt_agent/agent/workers/registry.py`
- Modify: `src/aedt_agent/infrastructure/harness/child_main.py`
- Modify: `tests/fixtures/process_workers.py`
- Modify: `tests/test_agent_harness_child.py`
- Modify: `tests/test_agent_worker_registry.py`

- [x] **Step 1：写失败测试**

在 `tests/fixtures/process_workers.py` 增加：

```python
def workspace_worker(job, context):
    artifact = Path(context.artifacts_dir) / "workspace.json"
    artifact.write_text(
        json.dumps(
            {
                "workspace": context.workspace,
                "artifacts_dir": context.artifacts_dir,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "workspace": context.workspace,
        "artifacts_dir": context.artifacts_dir,
        "artifact_refs": [str(artifact)],
    }
```

在 `tests/test_agent_harness_child.py` 增加：

```python
def test_child_main_injects_verified_workspace_into_worker_context(tmp_path):
    (tmp_path / "artifacts").mkdir()
    request = _request(
        tmp_path,
        "tests.fixtures.process_workers:workspace_worker",
    )

    child_main.run(_write_request(tmp_path, request))

    result = _read_result(tmp_path)
    assert result.output_payload["workspace"] == str(tmp_path.resolve())
    assert result.output_payload["artifacts_dir"] == str(
        (tmp_path / "artifacts").resolve()
    )
    assert result.artifact_refs == [
        str((tmp_path / "artifacts/workspace.json").resolve())
    ]
```

在 `tests/test_agent_worker_registry.py` 增加：

```python
def test_in_process_worker_context_has_no_workspace():
    seen = {}

    def worker(job, context):
        seen["workspace"] = context.workspace
        seen["artifacts_dir"] = context.artifacts_dir
        return {}

    registry = InMemoryWorkerRegistry()
    registry.register("fake.echo", worker)
    registry.execute(_job(), WorkerContext("worker-1"))

    assert seen == {"workspace": None, "artifacts_dir": None}
```

- [x] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_child.py tests/test_agent_worker_registry.py -k "workspace"
```

Expected: `WorkerContext` 没有 `workspace`/`artifacts_dir`。

- [x] **Step 3：实现最小契约**

把 `WorkerContext` 改为：

```python
@dataclass(frozen=True)
class WorkerContext:
    worker_id: str
    workspace: str | None = None
    artifacts_dir: str | None = None
```

`child_main.run()` 调用 Worker 时使用：

```python
artifacts_dir = (workspace / "artifacts").resolve()
if not artifacts_dir.is_dir():
    raise HarnessProtocolError(
        f"harness artifacts directory does not exist: {artifacts_dir}"
    )
output = worker(
    job,
    WorkerContext(
        request.worker_id,
        workspace=str(workspace),
        artifacts_dir=str(artifacts_dir),
    ),
)
```

Registry 的 in-process 路径继续由 Runtime 传入 `WorkerContext(worker_id)`，保持兼容。

- [x] **Step 4：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_child.py tests/test_agent_worker_registry.py
git add src/aedt_agent/agent/workers/registry.py src/aedt_agent/infrastructure/harness/child_main.py tests/fixtures/process_workers.py tests/test_agent_harness_child.py tests/test_agent_worker_registry.py
git commit -m "feat: expose harness workspace to process workers"
```

## Task 2：实现组合资源门控

**Files:**
- Modify: `src/aedt_agent/infrastructure/harness/resources.py`
- Modify: `src/aedt_agent/infrastructure/harness/local_process.py`
- Modify: `src/aedt_agent/agent/workers/registry.py`
- Modify: `tests/test_agent_harness_resources.py`
- Modify: `tests/test_agent_local_process_harness.py`
- Modify: `tests/test_agent_worker_registry.py`

- [x] **Step 1：写 ResourceGate 失败测试**

```python
def test_resource_gate_acquires_multiple_resources_in_stable_order():
    gate = ResourceGate(
        max_concurrent_cpu=1,
        max_concurrent_aedt=1,
        max_concurrent_license_jobs=1,
    )

    with gate.acquire_many(
        ("aedt", "license", "aedt"),
        timeout_seconds=1,
    ) as lease:
        assert lease.resource_classes == ("license", "aedt")
        assert set(lease.waited_seconds) == {"license", "aedt"}


def test_resource_gate_releases_partial_acquisition_on_timeout():
    gate = ResourceGate(
        max_concurrent_cpu=1,
        max_concurrent_aedt=1,
        max_concurrent_license_jobs=1,
    )
    held = gate.acquire("aedt", timeout_seconds=1)
    try:
        with pytest.raises(ResourceAcquireTimeout, match="aedt"):
            gate.acquire_many(("license", "aedt"), timeout_seconds=0.01)
    finally:
        held.release()

    with gate.acquire("license", timeout_seconds=1):
        pass
```

- [x] **Step 2：写 Harness/Registry 失败测试**

`tests/test_agent_local_process_harness.py`：

```python
def test_local_process_records_composite_resource_metadata(tmp_path):
    result = _harness(tmp_path).execute(
        _request("tests.fixtures.process_workers:echo_worker"),
        resource_classes=("license", "aedt"),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.metadata["resource_classes"] == ["license", "aedt"]
    assert set(result.metadata["resource_wait_seconds"]) == {"license", "aedt"}
```

`tests/test_agent_worker_registry.py`：

```python
def test_process_registration_accepts_composite_resources():
    registration = WorkerRegistration(
        capability="brd.local_cut.solve",
        execution_mode="local_process",
        entrypoint="aedt_agent.agent.workers.brd_real_solve:run_brd_real_solve_worker",
        resource_classes=("license", "aedt"),
    )

    assert registration.validate().resource_classes == ("license", "aedt")
```

- [x] **Step 3：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_resources.py tests/test_agent_local_process_harness.py tests/test_agent_worker_registry.py
```

- [x] **Step 4：实现 CompositeResourceLease**

```python
RESOURCE_ORDER = {"license": 0, "aedt": 1, "cpu": 2}


@dataclass
class CompositeResourceLease:
    leases: tuple[ResourceLease, ...]

    @property
    def resource_classes(self) -> tuple[str, ...]:
        return tuple(lease.resource_class for lease in self.leases)

    @property
    def waited_seconds(self) -> dict[str, float]:
        return {
            lease.resource_class: lease.waited_seconds
            for lease in self.leases
        }

    def __enter__(self) -> "CompositeResourceLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def release(self) -> None:
        for lease in reversed(self.leases):
            lease.release()
```

`ResourceGate.acquire_many()`：

```python
def acquire_many(
    self,
    resource_classes: tuple[str, ...] | list[str],
    timeout_seconds: float,
) -> CompositeResourceLease:
    if timeout_seconds < 0:
        raise ValueError("resource timeout_seconds must be non-negative")
    normalized = tuple(
        sorted(set(resource_classes), key=lambda name: RESOURCE_ORDER.get(name, 99))
    )
    if not normalized:
        raise ValueError("resource_classes must not be empty")
    unknown = [name for name in normalized if name not in RESOURCE_ORDER]
    if unknown:
        raise ValueError(f"unsupported resource class: {unknown[0]}")
    deadline = time.monotonic() + timeout_seconds
    acquired: list[ResourceLease] = []
    try:
        for name in normalized:
            remaining = max(0.0, deadline - time.monotonic())
            acquired.append(self.acquire(name, remaining))
    except Exception:
        for lease in reversed(acquired):
            lease.release()
        raise
    return CompositeResourceLease(tuple(acquired))
```

- [x] **Step 5：接入 Harness 和 Registry**

`WorkerRegistration` 使用：

```python
resource_classes: tuple[str, ...] = ("cpu",)
```

`register_process()` 参数改为：

```python
resource_classes: tuple[str, ...] = ("cpu",)
```

为兼容当前调用，临时保留可选 `resource_class: str | None = None`；若提供则转换为单元素 tuple，若同时提供两者则拒绝。

`LocalProcessHarness.execute()` 改为：

```python
resource_classes: tuple[str, ...] | list[str] = ("cpu",)
```

并用：

```python
lease = self.resource_gate.acquire_many(
    resource_classes,
    timeout_seconds=request.timeout_seconds,
)
```

metadata 写：

```python
"resource_classes": list(lease.resource_classes),
"resource_wait_seconds": lease.waited_seconds,
```

- [x] **Step 6：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_harness_resources.py tests/test_agent_local_process_harness.py tests/test_agent_worker_registry.py tests/test_agent_runtime_harness.py
git add src/aedt_agent/infrastructure/harness/resources.py src/aedt_agent/infrastructure/harness/local_process.py src/aedt_agent/agent/workers/registry.py tests/test_agent_harness_resources.py tests/test_agent_local_process_harness.py tests/test_agent_worker_registry.py
git commit -m "feat: gate process workers on composite resources"
```

## Task 3：实现真实 AEDT Solve Adapter

**Files:**
- Create: `src/aedt_agent/infrastructure/brd_real_solve.py`
- Modify: `src/aedt_agent/infrastructure/__init__.py`
- Create: `tests/test_infrastructure_brd_real_solve.py`

- [x] **Step 1：写请求验证失败测试**

```python
def test_real_solve_rejects_non_aedt_project(tmp_path):
    project = tmp_path / "model.txt"
    project.write_text("not a project", encoding="utf-8")

    with pytest.raises(ValueError, match="project_path must end with .aedt"):
        BrdRealSolveAdapter().run(_request(tmp_path, project_path=project))


@pytest.mark.parametrize(
    "expression",
    ["", "dB(S(1,1))", "TDRZt(P1,P2);DeleteProject()", "TDRZt(P1)"],
)
def test_real_solve_rejects_unapproved_tdr_expression(tmp_path, expression):
    with pytest.raises(ValueError, match="tdr_expression"):
        BrdRealSolveAdapter().run(_request(tmp_path, tdr_expression=expression))
```

请求 helper 使用真实 `.aedt` 空 fixture 和 attempt artifacts 目录。

- [x] **Step 2：写 fake AEDT 生命周期失败测试**

定义 `FakeHfss3dLayout`，提供：

```python
class FakePost:
    def create_report(
        self,
        expressions,
        setup_sweep_name,
        domain,
        variations,
        primary_sweep_variable,
        plot_name,
        context,
    ):
        FakeHfss3dLayout.calls.append(
            (
                "create_report",
                {
                    "expressions": expressions,
                    "setup_sweep_name": setup_sweep_name,
                    "domain": domain,
                    "primary_sweep_variable": primary_sweep_variable,
                    "plot_name": plot_name,
                    "context": context,
                },
            )
        )
        return object()

    def export_report_to_file(self, output_dir, plot_name, extension):
        path = Path(output_dir) / f"{plot_name}{extension}"
        path.write_text(
            "Time [ps],TDRZt(P1,P1)\n0,100\n10,105\n",
            encoding="utf-8",
        )
        return str(path)
```

`FakeHfss3dLayout` 还必须提供：

```python
setup_names = ["Setup1"]
setup_sweeps_names = ["Setup1 : Sweep1"]
port_list = ["P1", "P2"]

def analyze_setup(self, name, blocking):
    self.calls.append(("analyze_setup", {"name": name, "blocking": blocking}))
    return True

def export_touchstone(self, setup, sweep, output_file):
    Path(output_file).write_text(
        "# GHz S MA R 50\n"
        "0 0.05 0 0.9 0 0.9 0 0.05 0\n"
        "18 0.45 0 0.8 0 0.8 0 0.05 0\n",
        encoding="utf-8",
    )
    self.calls.append(
        (
            "export_touchstone",
            {"setup": setup, "sweep": sweep, "output_file": output_file},
        )
    )
    return output_file

def save_project(self, file_name):
    Path(file_name).write_text("solved project", encoding="utf-8")
    self.calls.append(("save_project", file_name))
    return True

def release_desktop(self, close_projects, close_desktop):
    self.calls.append(
        (
            "release_desktop",
            {
                "close_projects": close_projects,
                "close_desktop": close_desktop,
            },
        )
    )
```

测试：

```python
def test_real_solve_copies_checkpoint_solves_and_exports_artifacts(tmp_path):
    adapter = BrdRealSolveAdapter(hfss3dlayout_factory=FakeHfss3dLayout)

    result = adapter.run(_request(tmp_path))

    assert Path(result.project_checkpoint).read_text() == "approved project"
    assert Path(result.solved_project).exists()
    assert Path(result.touchstone_path).stat().st_size > 0
    assert Path(result.tdr_path).stat().st_size > 0
    manifest = json.loads(Path(result.solve_manifest_path).read_text())
    assert manifest["outputs"]["touchstone"]["sha256"]
    assert manifest["outputs"]["tdr"]["sha256"]
    assert [name for name, _ in FakeHfss3dLayout.calls] == [
        "init",
        "analyze_setup",
        "save_project",
        "export_touchstone",
        "create_report",
        "export_report_to_file",
        "release_desktop",
    ]
```

- [x] **Step 3：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_infrastructure_brd_real_solve.py
```

Expected: module 不存在。

- [x] **Step 4：实现契约与验证**

```python
class ArtifactExportError(RuntimeError):
    """Raised when AEDT did not create a required output artifact."""


class ArtifactValidationError(ValueError):
    """Raised when an exported artifact is empty or malformed."""


TDR_EXPRESSION = re.compile(
    r"^TDRZt\(([A-Za-z_][A-Za-z0-9_.:-]*),([A-Za-z_][A-Za-z0-9_.:-]*)\)$"
)


@dataclass(frozen=True)
class BrdRealSolveRequest:
    project_path: Path
    artifact_dir: Path
    setup_name: str
    sweep_name: str
    solution_name: str
    touchstone_name: str
    tdr_report_name: str
    tdr_expression: str
    expected_port_count: int
    environment: RealAedtEnvironment


@dataclass(frozen=True)
class BrdRealSolveResult:
    project_checkpoint: str
    solved_project: str
    touchstone_path: str
    tdr_path: str
    solve_manifest_path: str
    summary: dict[str, Any]
```

验证 artifact_dir resolved 后必须存在，不在 adapter 内接受外部任意输出根。

adapter 在打开 AEDT 前创建受控 checkpoint：

```python
checkpoint_dir = request.artifact_dir / "input_checkpoint"
checkpoint_dir.mkdir(parents=True, exist_ok=False)
project_checkpoint = checkpoint_dir / request.project_path.name
shutil.copy2(request.project_path, project_checkpoint)
source_results = Path(f"{request.project_path}results")
if source_results.is_dir():
    shutil.copytree(
        source_results,
        Path(f"{project_checkpoint}results"),
        copy_function=shutil.copy2,
    )
solved_project = request.artifact_dir / f"{request.project_path.stem}.solved.aedt"
shutil.copy2(project_checkpoint, solved_project)
checkpoint_results = Path(f"{project_checkpoint}results")
if checkpoint_results.is_dir():
    shutil.copytree(
        checkpoint_results,
        Path(f"{solved_project}results"),
        copy_function=shutil.copy2,
    )
```

首期只复制 `.aedt` 和相邻的 `<project>.aedtresults`；若真实 smoke 证明工程依赖额外外部目录，再以显式 allowlist 增补，不能递归复制任意父目录。

- [x] **Step 5：实现 adapter**

关键调用必须写成：

```python
app = self._hfss3dlayout_class()(
    project=str(solved_project),
    version=request.environment.version,
    non_graphical=request.environment.non_graphical,
    new_desktop=True,
    close_on_exit=request.environment.non_graphical,
    remove_lock=False,
)
try:
    if request.setup_name not in set(app.setup_names):
        raise ValueError(f"setup not found: {request.setup_name}")
    if request.solution_name not in set(app.setup_sweeps_names):
        raise ValueError(f"setup sweep not found: {request.solution_name}")
    if len(list(app.port_list)) != request.expected_port_count:
        raise ValueError(
            f"expected {request.expected_port_count} ports, "
            f"found {len(list(app.port_list))}"
        )
    if app.analyze_setup(name=request.setup_name, blocking=True) is not True:
        raise ArtifactExportError(f"AEDT solve failed: {request.setup_name}")
    app.save_project(file_name=str(solved_project))
    exported = app.export_touchstone(
        setup=request.setup_name,
        sweep=request.sweep_name,
        output_file=str(touchstone_path),
    )
    if not exported:
        raise ArtifactExportError("AEDT touchstone export failed")
    report = app.post.create_report(
        expressions=request.tdr_expression,
        setup_sweep_name=request.solution_name,
        domain="Time",
        variations={"Time": ["All"]},
        primary_sweep_variable="Time",
        plot_name=request.tdr_report_name,
        context={
            "pulse_rise_time": "10ps",
            "step_time": "1ps",
            "time_windowing": 4,
            "maximum_time": "10ns",
            "use_pulse_in_tdr": True,
            "differential_pairs": False,
        },
    )
    if not report:
        raise ArtifactExportError("AEDT TDR report creation failed")
    raw_tdr_dir = request.artifact_dir / "_aedt_report_tmp"
    raw_tdr_dir.mkdir(parents=False, exist_ok=False)
    tdr_export = app.post.export_report_to_file(
        str(raw_tdr_dir),
        request.tdr_report_name,
        ".csv",
    )
    if not tdr_export:
        raise ArtifactExportError("AEDT TDR report export failed")
finally:
    app.release_desktop(
        close_projects=request.environment.non_graphical,
        close_desktop=request.environment.non_graphical,
    )
```

AEDT report CSV 先规范化，再交给现有 parser：

```python
def _normalize_tdr_report_csv(
    exported_path: Path,
    normalized_path: Path,
    expression: str,
) -> None:
    with exported_path.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as source:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        time_column = next(
            (
                name
                for name in fieldnames
                if name.casefold() in {"time", "time [ps]", "time_ps"}
            ),
            None,
        )
        value_column = next(
            (
                name
                for name in fieldnames
                if name == expression
                or "tdrzt" in name.casefold()
                or "impedance" in name.casefold()
            ),
            None,
        )
        if time_column is None or value_column is None:
            raise ArtifactValidationError(
                "AEDT TDR report does not contain time and impedance columns"
            )
        rows = [
            {
                "time_ps": float(row[time_column]),
                "impedance_ohm": float(row[value_column]),
            }
            for row in reader
            if row.get(time_column) not in {None, ""}
            and row.get(value_column) not in {None, ""}
        ]
    if not rows:
        raise ArtifactValidationError("AEDT TDR report contains no samples")
    with normalized_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=["time_ps", "impedance_ohm"],
        )
        writer.writeheader()
        writer.writerows(rows)
```

`export_report_to_file()` 的返回文件必须 resolve 后位于 `_aedt_report_tmp/`，作为临时输入规范化到请求指定的 TDR 文件。规范化完成后在 `finally` 中删除该临时目录，不能把 AEDT 的不稳定列名暴露为正式 artifact。随后使用现有 `parse_touchstone()` 和 `parse_tdr_csv()` 做格式验证；文件缺失/为空抛 `ArtifactExportError`，格式错误或解析为空抛 `ArtifactValidationError`。manifest 先写 `.tmp` 再 `os.replace()`。

- [x] **Step 6：补失败与 finally 测试**

覆盖：

- analyze 返回 `False`；
- Touchstone 空文件；
- TDR CSV 无有效行；
- setup/sweep/port count 不匹配；
- 任一阶段异常仍调用 `release_desktop`；
- 输入 project 原件 SHA-256 和内容不变。

- [x] **Step 7：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_infrastructure_brd_real_solve.py tests/test_infrastructure_brd_real_build.py
git add src/aedt_agent/infrastructure/brd_real_solve.py src/aedt_agent/infrastructure/__init__.py tests/test_infrastructure_brd_real_solve.py
git commit -m "feat: solve approved brd projects with aedt adapter"
```

## Task 4：实现 local-process Solve Worker

**Files:**
- Create: `src/aedt_agent/agent/workers/brd_real_solve.py`
- Modify: `src/aedt_agent/agent/workers/__init__.py`
- Modify: `src/aedt_agent/agent/workers/registry.py`
- Modify: `src/aedt_agent/agent/mission/contracts.py`
- Modify: `src/aedt_agent/infrastructure/harness/child_main.py`
- Modify: `tests/fixtures/fake_real_solve.py`
- Modify: `tests/fixtures/process_workers.py`
- Create: `tests/test_agent_brd_real_solve_worker.py`
- Modify: `tests/test_agent_harness_child.py`

- [x] **Step 1：写 Worker 输入/输出失败测试**

```python
def test_real_solve_worker_requires_harness_artifact_directory(tmp_path):
    job = _job(tmp_path)

    with pytest.raises(ValueError, match="requires process harness artifacts_dir"):
        run_brd_real_solve_worker(job, WorkerContext("worker-1"))


def test_real_solve_worker_uses_context_artifacts_and_returns_only_refs(
    tmp_path,
):
    context = WorkerContext(
        "worker-1",
        workspace=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    adapter = FakeSolveAdapter()

    output = run_brd_real_solve_worker(
        _job(tmp_path),
        context,
        solve_adapter=adapter,
    )

    assert adapter.request.artifact_dir == tmp_path / "artifacts"
    assert output["status"] == "succeeded"
    assert output["solve_summary"]["raw_sparameters"] == "artifact_only"
    assert output["solve_summary"]["raw_tdr"] == "artifact_only"
    assert "frequency_ghz" not in json.dumps(output)
    assert len(output["artifact_refs"]) == 5
```

- [x] **Step 2：写 Job input builder 测试**

```python
def test_real_solve_job_input_contains_no_output_directory(tmp_path):
    payload = build_brd_real_solve_job_input(
        project_path=tmp_path / "approved.aedt",
        setup_name="Setup1",
        sweep_name="Sweep1",
        tdr_expression="TDRZt(P1,P1)",
        expected_port_count=2,
        frequency_start_ghz=0.0,
        frequency_stop_ghz=67.0,
        rl_target_db=-20.0,
        tdr_target_ohm=100.0,
        aedt={"version": "2026.1", "non_graphical": True},
    )

    assert "artifact_dir" not in payload
    assert payload["solution_name"] == "Setup1 : Sweep1"
    assert payload["approval_reason"] == "approve_real_brd_solve"
    assert payload["approval_options"][0]["id"] == "approve"
```

- [x] **Step 3：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_brd_real_solve_worker.py
```

- [x] **Step 4：实现 Worker**

```python
BRD_REAL_SOLVE_CAPABILITY = "brd.local_cut.solve"


def run_brd_real_solve_worker(
    job: JobRecord,
    context: WorkerContext,
    *,
    solve_adapter: BrdRealSolveAdapter | None = None,
) -> dict[str, Any]:
    if not context.artifacts_dir:
        raise ValueError(
            "brd.local_cut.solve requires process harness artifacts_dir"
        )
    payload = dict(job.input_payload)
    environment = RealAedtEnvironment(**dict(payload.get("aedt") or {}))
    request = BrdRealSolveRequest(
        project_path=Path(str(payload["project_path"])),
        artifact_dir=Path(context.artifacts_dir),
        setup_name=str(payload["setup_name"]),
        sweep_name=str(payload["sweep_name"]),
        solution_name=str(payload["solution_name"]),
        touchstone_name=str(payload.get("touchstone_name") or "channel.s2p"),
        tdr_report_name=str(payload.get("tdr_report_name") or "ChannelTDR"),
        tdr_expression=str(payload["tdr_expression"]),
        expected_port_count=int(payload["expected_port_count"]),
        environment=environment,
    )
    result = (solve_adapter or BrdRealSolveAdapter()).run(request)
    refs = [
        result.project_checkpoint,
        result.solved_project,
        result.touchstone_path,
        result.tdr_path,
        result.solve_manifest_path,
    ]
    return {
        "status": "succeeded",
        "solve_summary": {
            **result.summary,
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
        },
        "touchstone_path": result.touchstone_path,
        "tdr_path": result.tdr_path,
        "solve_manifest": result.solve_manifest_path,
        "artifact_dir": str(Path(result.solve_manifest_path).parent),
        "frequency_start_ghz": float(
            payload.get("frequency_start_ghz", 0.0)
        ),
        "frequency_stop_ghz": float(
            payload.get("frequency_stop_ghz", 67.0)
        ),
        "rl_target_db": float(payload.get("rl_target_db", -20.0)),
        "tdr_target_ohm": float(payload.get("tdr_target_ohm", 100.0)),
        "evidence_summary": {
            "status": "solve_completed",
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
            "artifact_refs": refs,
        },
        "artifact_refs": refs,
    }
```

默认 entrypoint 不注入 adapter，真实子进程导入 PyAEDT。fake 集成通过 `tests.fixtures.fake_real_solve:run_fake_real_solve_worker` 使用相同 output contract。

- [x] **Step 5：增加错误类型**

`ErrorClass` 增加：

```python
ARTIFACT_MISSING = "artifact_missing"
ARTIFACT_INVALID = "artifact_invalid"
```

`classify_worker_error()` 对 `ArtifactExportError` 映射为 `ARTIFACT_MISSING`，对 `ArtifactValidationError` 映射为 `ARTIFACT_INVALID`，两者默认不可 retry。license 文本分类保持 retryable。

仅修改父进程 `classify_worker_error()` 不够，因为 local-process 异常先被 `child_main` 捕获。增加通用、JSON-safe 的 Worker 上报异常：

```python
class WorkerReportedError(RuntimeError):
    def __init__(
        self,
        error_class: str,
        message: str,
        *,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable
        self.details = dict(details or {})
```

real solve Worker 捕获 adapter 的领域异常并转换：

```python
try:
    result = (solve_adapter or BrdRealSolveAdapter()).run(request)
except ArtifactExportError as exc:
    raise WorkerReportedError(
        ErrorClass.ARTIFACT_MISSING.value,
        str(exc),
        retryable=False,
        details={"error_type": type(exc).__name__},
    ) from exc
except ArtifactValidationError as exc:
    raise WorkerReportedError(
        ErrorClass.ARTIFACT_INVALID.value,
        str(exc),
        retryable=False,
        details={"error_type": type(exc).__name__},
    ) from exc
```

`child_main` 遇到 `WorkerReportedError` 时原样写入 `HarnessError`；其他异常继续使用现有 `invalid_input/worker_crash` 规则。`classify_worker_error()` 同样识别该类型，保证 fake/in-process 测试语义一致。

增加 fixture 与测试：

```python
def reported_error_worker(job, context):
    raise WorkerReportedError(
        "artifact_missing",
        "touchstone was not exported",
        retryable=False,
        details={"stage": "touchstone"},
    )


def test_child_main_preserves_worker_reported_error(tmp_path):
    request = _request(
        tmp_path,
        "tests.fixtures.process_workers:reported_error_worker",
    )

    child_main.run(_write_request(tmp_path, request))

    error = _read_result(tmp_path).error
    assert error.error_class == "artifact_missing"
    assert error.retryable is False
    assert error.details["stage"] == "touchstone"
```

- [x] **Step 6：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_brd_real_solve_worker.py tests/test_agent_worker_registry.py tests/test_agent_harness_child.py tests/test_agent_runtime_harness.py
git add src/aedt_agent/agent/workers/brd_real_solve.py src/aedt_agent/agent/workers/__init__.py src/aedt_agent/agent/workers/registry.py src/aedt_agent/agent/mission/contracts.py src/aedt_agent/infrastructure/harness/child_main.py tests/fixtures/fake_real_solve.py tests/fixtures/process_workers.py tests/test_agent_brd_real_solve_worker.py tests/test_agent_harness_child.py
git commit -m "feat: expose real brd solve process worker"
```

## Task 5：实现 S 参数和 TDR Artifact Query

**Files:**
- Create: `src/aedt_agent/agent/evaluation/artifact_query.py`
- Create: `src/aedt_agent/agent/evaluation/query_service.py`
- Modify: `src/aedt_agent/agent/evaluation/__init__.py`
- Modify: `src/aedt_agent/agent/mission/contracts.py`
- Modify: `src/aedt_agent/infrastructure/sqlite_mission_store.py`
- Create: `tests/test_agent_artifact_query.py`
- Create: `tests/test_agent_artifact_query_service.py`

- [ ] **Step 1：写 S 参数/TDR bounded query 失败测试**

```python
def test_query_sparameter_artifact_limits_points_and_returns_digest(tmp_path):
    touchstone = tmp_path / "dense.s2p"
    _write_dense_touchstone(touchstone, count=1341, failure_frequency=18.0)

    result = query_sparameter_artifact(
        touchstone,
        17.0,
        19.0,
        max_points=8,
        rl_target_db=-20.0,
    )

    assert result["point_count"] <= 8
    assert result["artifact_sha256"] == _sha256(touchstone)
    assert result["window_summary"]["sample_count"] == 41
    assert any(point["frequency_ghz"] == 18.0 for point in result["points"])


def test_query_tdr_artifact_preserves_peak_and_limits_points(tmp_path):
    tdr = tmp_path / "dense_tdr.csv"
    _write_dense_tdr(tdr, count=1000, peak_index=510)

    result = query_tdr_artifact(
        tdr,
        500.0,
        520.0,
        max_points=8,
        target_ohm=100.0,
    )

    assert result["point_count"] <= 8
    assert result["window_summary"]["peak_time_ps"] == 510.0
    assert result["window_summary"]["peak_deviation_ohm"] == 15.0
```

另覆盖 `max_points > 128`、空窗口、缺失 artifact、无效 CSV。

- [ ] **Step 2：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_artifact_query.py
```

- [ ] **Step 3：实现纯函数 query**

S 参数复用 `parse_touchstone()` 和 `query_sparameter_window()`，补 artifact digest。

TDR 使用：

```python
def query_tdr_artifact(
    path: str | Path,
    time_start_ps: float,
    time_stop_ps: float,
    *,
    max_points: int = 64,
    target_ohm: float = 100.0,
) -> dict[str, Any]:
    _validate_max_points(max_points)
    source = Path(path)
    samples = [
        sample
        for sample in parse_tdr_csv(source)
        if time_start_ps <= sample["time_ps"] <= time_stop_ps
    ]
    if not samples:
        raise ValueError("query window contains no TDR samples")
    peak = max(
        samples,
        key=lambda sample: abs(sample["impedance_ohm"] - target_ohm),
    )
    points = _extrema_preserving_tdr_points(samples, max_points, target_ohm)
    return {
        "artifact_ref": str(source),
        "artifact_sha256": _sha256(source),
        "time_start_ps": time_start_ps,
        "time_stop_ps": time_stop_ps,
        "point_count": len(points),
        "points": points,
        "window_summary": {
            "sample_count": len(samples),
            "target_ohm": target_ohm,
            "peak_time_ps": peak["time_ps"],
            "peak_impedance_ohm": peak["impedance_ohm"],
            "peak_deviation_ohm": abs(
                peak["impedance_ohm"] - target_ohm
            ),
        },
    }
```

点选择保留首尾、全局高/低、最大偏差，并对剩余窗口等宽分桶取高/低值。

- [ ] **Step 4：写 Query Service 失败测试**

```python
def test_query_service_rejects_unregistered_artifact(tmp_path):
    runtime, mission, artifact = _runtime_with_artifact(tmp_path)
    outside = tmp_path / "outside.s2p"
    outside.write_text("# GHz S MA R 50\n", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact is not registered"):
        ArtifactQueryService(runtime.store).query_sparameter(
            mission.mission_id,
            str(outside),
            0,
            1,
        )


def test_query_service_records_bounded_query_event(tmp_path):
    runtime, mission, artifact = _runtime_with_artifact(tmp_path)

    result = ArtifactQueryService(runtime.store).query_sparameter(
        mission.mission_id,
        artifact.path,
        17,
        19,
        max_points=8,
    )

    event = runtime.list_events(mission.mission_id)[-1]
    assert event.event_type == EventType.ARTIFACT_QUERY_COMPLETED
    assert event.payload["artifact_id"] == artifact.artifact_id
    assert event.payload["point_count"] == result["point_count"]
    assert "points" not in event.payload
```

- [ ] **Step 5：实现 Query Service 和 Event**

`EventType` 增加：

```python
ARTIFACT_QUERY_COMPLETED = "artifact_query_completed"
```

Service 只允许查询 `store.list_artifact_manifests(mission_id)` 中 path/sha256 匹配的 artifact。Event 只记录 artifact ID、query range、point_count、summary digest，不记录返回点。

Store 增加公共方法：

```python
def append_event(
    self,
    mission_id: str,
    event_type: EventType,
    payload: dict,
) -> EventRecord:
    with self._connect() as db:
        return self._append_event_in_tx(
            db,
            mission_id,
            event_type,
            payload,
        )
```

- [ ] **Step 6：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_artifact_query.py tests/test_agent_artifact_query_service.py tests/test_agent_spectral_evidence.py
git add src/aedt_agent/agent/evaluation/artifact_query.py src/aedt_agent/agent/evaluation/query_service.py src/aedt_agent/agent/evaluation/__init__.py src/aedt_agent/agent/mission/contracts.py src/aedt_agent/infrastructure/sqlite_mission_store.py tests/test_agent_artifact_query.py tests/test_agent_artifact_query_service.py
git commit -m "feat: query bounded solve artifacts"
```

## Task 6：接入 ExecutionProfile、CLI 和 Worker 注册

**Files:**
- Modify: `src/aedt_agent/agent/policies/execution_profile.py`
- Modify: `src/aedt_agent/agent/cli.py`
- Modify: `src/aedt_agent/agent/workers/registry.py`
- Modify: `tests/test_agent_execution_profile.py`
- Modify: `tests/test_agent_cli_brd_mission.py`
- Create: `tests/test_agent_cli_artifact_query.py`
- Modify: `tests/test_agent_worker_registry.py`

- [ ] **Step 1：写 Profile 失败测试**

新增字段：

```python
aedt_version: str
aedt_non_graphical: bool
```

测试：

```python
def test_safe_profile_declares_aedt_runtime_but_keeps_it_disabled():
    profile = ExecutionProfile.safe_recorded()

    assert profile.allow_real_aedt is False
    assert profile.aedt_version == "2026.1"
    assert profile.aedt_non_graphical is True


def test_execution_profile_rejects_empty_aedt_version():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["aedt_version"] = ""

    with pytest.raises(ExecutionProfileError, match="aedt_version"):
        ExecutionProfile.from_json_dict(payload)
```

`aedt_version` 和 `aedt_non_graphical` 是执行时权威值。Job payload 中的 `aedt` 只用于任务草稿和审批可见性；`advance/resume/run-graph` 加载的 profile 必须在创建 `HarnessRequest` 时覆盖这两个字段。

- [ ] **Step 2：写 Mission create 失败测试**

```python
def test_cli_creates_real_solve_job_without_output_directory(tmp_path):
    project = tmp_path / "approved.aedt"
    project.write_text("approved project", encoding="utf-8")

    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "求解 approved local cut",
        "--brd-real-solve",
        "--project",
        str(project),
        "--setup",
        "Setup1",
        "--sweep",
        "Sweep1",
        "--tdr-expression",
        "TDRZt(P1,P1)",
        "--expected-port-count",
        "2",
    )

    assert created.returncode == 0, created.stderr
    mission_id = json.loads(created.stdout)["mission_id"]
    status = json.loads(
        _run(
            tmp_path,
            "mission",
            "status",
            "--mission-id",
            mission_id,
        ).stdout
    )
    job = status["jobs"][0]
    assert job["capability"] == "brd.local_cut.solve"
    assert job["timeout_seconds"] == 7200
    assert "artifact_dir" not in job["input_payload"]
```

- [ ] **Step 3：写 safe profile 阻止执行测试**

```python
def test_cli_safe_profile_blocks_real_solve_before_process_launch(tmp_path):
    mission_id = _create_real_solve_mission(tmp_path)

    result = _run(
        tmp_path,
        "mission",
        "advance",
        "--mission-id",
        mission_id,
        "--profile",
        "safe-recorded",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["decision"]["code"] == "real_aedt_disabled"
    assert not (tmp_path / "harness").exists()
```

再增加直接 Registry/Graph 路径的测试，防止低层命令绕过 MissionLoop：

```python
def test_registry_blocks_real_aedt_process_before_harness_execution():
    harness = RecordingHarness()
    registry = InMemoryWorkerRegistry(
        harness=harness,
        allow_real_aedt=False,
    )
    registry.register_process(
        "brd.local_cut.solve",
        "tests.fixtures.fake_real_solve:run_fake_real_solve_worker",
        resource_classes=("license", "aedt"),
        requires_real_aedt=True,
    )

    result = registry.execute(
        _job("brd.local_cut.solve"),
        WorkerContext("worker-1"),
        attempt_id="attempt-1",
    )

    assert result.status == JobStatus.FAILED
    assert result.error.error_class == ErrorClass.INVALID_INPUT
    assert result.error.details["code"] == "real_aedt_disabled"
    assert harness.calls == []
```

- [ ] **Step 4：写 profile 覆盖 Harness 输入测试**

```python
def test_process_registration_overrides_aedt_environment_from_profile():
    harness = RecordingHarness()
    registry = InMemoryWorkerRegistry(
        harness=harness,
        allow_real_aedt=True,
    )
    registry.register_process(
        "brd.local_cut.solve",
        "tests.fixtures.fake_real_solve:run_fake_real_solve_worker",
        resource_classes=("license", "aedt"),
        requires_real_aedt=True,
        input_overrides={
            "aedt": {
                "version": "2025.2",
                "non_graphical": True,
            }
        },
    )

    registry.execute(
        _job(
            "brd.local_cut.solve",
            input_payload={
                "aedt": {
                    "version": "2026.1",
                    "non_graphical": False,
                }
            },
        ),
        WorkerContext("worker-1"),
        attempt_id="attempt-1",
    )

    assert harness.calls[0]["request"].input_payload["aedt"] == {
        "version": "2025.2",
        "non_graphical": True,
    }
```

- [ ] **Step 5：实现 CLI 与 Registry policy**

parser 增加：

```python
create.add_argument("--brd-real-solve", action="store_true")
create.add_argument("--project")
create.add_argument("--setup", default="Setup1")
create.add_argument("--sweep", default="Sweep1")
create.add_argument("--tdr-expression")
create.add_argument("--expected-port-count", type=int, default=2)
create.add_argument("--solve-timeout-seconds", type=int, default=7200)
```

create handler 验证必填字段，并创建：

```python
runtime.create_job(
    mission.mission_id,
    BRD_REAL_SOLVE_CAPABILITY,
    f"brd-real-solve:{Path(args.project).resolve()}:{args.setup}:{args.sweep}",
    build_brd_real_solve_job_input(
        project_path=args.project,
        setup_name=args.setup,
        sweep_name=args.sweep,
        tdr_expression=args.tdr_expression,
        expected_port_count=args.expected_port_count,
        frequency_start_ghz=args.frequency_start_ghz,
        frequency_stop_ghz=args.frequency_stop_ghz,
        rl_target_db=args.rl_target_db,
        tdr_target_ohm=args.tdr_target_ohm,
        aedt={
            "version": args.aedt_version,
            "non_graphical": args.non_graphical,
        },
    ),
    timeout_seconds=args.solve_timeout_seconds,
    retry_limit=1,
)
```

`_runtime_with_workers()` 注册：

```python
registry.register_process(
    BRD_REAL_SOLVE_CAPABILITY,
    "aedt_agent.agent.workers.brd_real_solve:run_brd_real_solve_worker",
    resource_classes=("license", "aedt"),
    requires_real_aedt=True,
    input_overrides={
        "aedt": {
            "version": profile.aedt_version,
            "non_graphical": profile.aedt_non_graphical,
        }
    },
)
```

`WorkerRegistration` 增加：

```python
requires_real_aedt: bool = False
input_overrides: dict[str, Any] = field(default_factory=dict)
```

`InMemoryWorkerRegistry.__init__()` 增加：

```python
allow_real_aedt: bool = False
```

在创建 Harness workspace 前检查：

```python
if registration.requires_real_aedt and not self.allow_real_aedt:
    return WorkerExecutionResult(
        job.job_id,
        JobStatus.FAILED,
        {},
        [],
        JobError(
            ErrorClass.INVALID_INPUT,
            "real AEDT execution is disabled by execution profile",
            False,
            {"code": "real_aedt_disabled"},
        ),
        {"execution_mode": "local_process", "process_started": False},
    )
```

构造 `HarnessRequest` 前做 JSON object 递归 merge，profile override 胜出：

```python
def _deep_merge(
    base: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged
```

```python
input_payload=_deep_merge(
    dict(job.input_payload),
    registration.input_overrides,
)
```

`_runtime_with_workers()` 构造 Registry 时传：

```python
allow_real_aedt=profile.allow_real_aedt
```

这样 `run`、`run-graph`、`advance` 和 `resume` 都不能绕过 policy。MissionLoop 现有 `_requires_real_aedt()` 仍显式增加 capability，用于在 Job attempt 前给出 bounded-loop 终态：

```python
return (
    adapter_mode in {"real_build", "real_aedt"}
    or job.capability == "brd.local_cut.solve"
    or ".real" in job.capability
)
```

- [ ] **Step 6：实现 artifact-query CLI**

parser：

```python
artifact_query = mission_commands.add_parser("artifact-query")
artifact_query.add_argument("--mission-id", required=True)
artifact_query.add_argument("--artifact-ref", required=True)
window = artifact_query.add_mutually_exclusive_group(required=True)
window.add_argument("--frequency", nargs=2, type=float)
window.add_argument("--time-ps", nargs=2, type=float)
artifact_query.add_argument("--max-points", type=int, default=64)
artifact_query.add_argument("--target", type=float)
```

handler 使用 `ArtifactQueryService`。S 参数默认 target `-20.0`，TDR 默认 target `100.0`。

- [ ] **Step 7：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_execution_profile.py tests/test_agent_cli_brd_mission.py tests/test_agent_cli_artifact_query.py tests/test_agent_cli_mission_loop.py tests/test_agent_worker_registry.py
git add src/aedt_agent/agent/policies/execution_profile.py src/aedt_agent/agent/cli.py src/aedt_agent/agent/workers/registry.py src/aedt_agent/agent/orchestrator/mission_loop.py tests/test_agent_execution_profile.py tests/test_agent_cli_brd_mission.py tests/test_agent_cli_artifact_query.py tests/test_agent_cli_mission_loop.py tests/test_agent_worker_registry.py
git commit -m "feat: configure and invoke real brd solve missions"
```

## Task 7：实现 approval -> solve -> score YAML Graph

**Files:**
- Create: `docs/agent_templates/brd_real_solve_evidence.yaml`
- Modify: `src/aedt_agent/agent/graph_executors.py`
- Modify: `src/aedt_agent/agent/scorecard.py`
- Create: `tests/test_agent_brd_real_solve_graph.py`
- Modify: `tests/test_agent_graph_template.py`
- Modify: `tests/test_agent_scorecard.py`

- [ ] **Step 1：写模板失败测试**

```python
def test_real_solve_graph_template_has_approval_before_solve():
    template = load_graph_template("brd_real_solve_evidence")

    assert [node.node_id for node in template.nodes] == [
        "model_validator",
        "model_approval_gate",
        "real_solve_worker",
        "channel_score_worker",
        "real_solve_scorecard",
    ]
    assert template.node("real_solve_worker").capability == "brd.local_cut.solve"
    assert template.node("channel_score_worker").capability == "brd.channel.score"
```

- [ ] **Step 2：写 Graph 端到端失败测试**

测试使用：

- local-process fake solve Worker；
-真实 `brd.channel.score`；
- SQLite store；
-同一个 GraphRun approval/resume。

```python
def test_real_solve_graph_resumes_same_run_and_scores_exported_artifacts(
    tmp_path,
    monkeypatch,
):
    runtime, registry = _runtime(
        tmp_path,
        monkeypatch,
        allow_real_aedt=True,
    )
    registry.register_process(
        BRD_REAL_SOLVE_CAPABILITY,
        "tests.fixtures.fake_real_solve:run_fake_real_solve_worker",
        resource_classes=("license", "aedt"),
        allowed_env=("PYTHONPATH",),
        requires_real_aedt=True,
    )
    registry.register(
        BRD_CHANNEL_SCORE_CAPABILITY,
        run_brd_channel_score_worker,
    )
    mission = runtime.create_mission("solve approved local cut", [], [])
    template = load_graph_template("brd_real_solve_evidence")
    initial = _initial_payload(tmp_path)

    waiting = run_graph(
        runtime,
        mission.mission_id,
        template,
        initial_payload=initial,
    )
    graph_run_id = waiting["graph_run"]["graph_run_id"]
    approval_run = next(
        run
        for run in waiting["node_runs"]
        if run["node_id"] == "model_approval_gate"
    )
    ApprovalService(runtime.store).approve(
        approval_run["output_payload"]["approval_id"],
        "approve",
    )
    completed = resume_graph(runtime, graph_run_id)

    assert waiting["status"] == "waiting_approval"
    assert completed["status"] == "succeeded"
    assert completed["graph_run"]["graph_run_id"] == graph_run_id
    solve_run = next(
        run
        for run in completed["node_runs"]
        if run["node_id"] == "real_solve_worker"
    )
    score_run = next(
        run
        for run in completed["node_runs"]
        if run["node_id"] == "channel_score_worker"
    )
    assert solve_run["output_payload"]["solve_summary"]["raw_sparameters"] == "artifact_only"
    assert score_run["output_payload"]["evidence_summary"]["raw_tdr"] == "artifact_only"
    assert len(runtime.store.list_evidence_packages(mission.mission_id)) >= 2
```

- [ ] **Step 3：确认红灯**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_brd_real_solve_graph.py tests/test_agent_graph_template.py
```

- [ ] **Step 4：创建 YAML**

```yaml
id: brd_real_solve_evidence
version: 1
description: Approve, solve, export, score, and audit one BRD local-cut AEDT project.
nodes:
  - id: model_validator
    role: validator
    kind: program
    input_schema: real_solve_request
    output_schema: validated_real_solve_request
    max_runs: 1
  - id: model_approval_gate
    role: approval_gate
    kind: human_gate
    input_schema: validated_real_solve_request
    output_schema: model_approval
    max_runs: 1
  - id: real_solve_worker
    role: worker
    kind: worker
    capability: brd.local_cut.solve
    input_schema: model_approval
    output_schema: real_solve_result
    max_runs: 1
  - id: channel_score_worker
    role: worker
    kind: worker
    capability: brd.channel.score
    input_schema: real_solve_result
    output_schema: channel_score_result
    max_runs: 1
  - id: real_solve_scorecard
    role: scorecard
    kind: program
    input_schema: channel_score_result
    output_schema: real_solve_scorecard
    max_runs: 1
edges:
  - id: validate-to-approval
    from: model_validator
    to: model_approval_gate
    on: succeeded
  - id: approval-to-solve
    from: model_approval_gate
    to: real_solve_worker
    on: approved
  - id: solve-to-score
    from: real_solve_worker
    to: channel_score_worker
    on: succeeded
  - id: score-to-scorecard
    from: channel_score_worker
    to: real_solve_scorecard
    on: succeeded
handoffs:
  real_solve_request:
    required_fields:
      - project_path
      - setup_name
      - sweep_name
      - solution_name
      - tdr_expression
      - expected_port_count
  validated_real_solve_request:
    required_fields:
      - project_path
      - setup_name
      - sweep_name
      - solution_name
      - tdr_expression
      - expected_port_count
      - approval_reason
      - approval_options
  model_approval:
    required_fields:
      - approval_id
      - decision
      - project_path
      - setup_name
      - sweep_name
      - solution_name
      - tdr_expression
      - expected_port_count
      - frequency_start_ghz
      - frequency_stop_ghz
      - rl_target_db
      - tdr_target_ohm
  real_solve_result:
    required_fields:
      - status
      - touchstone_path
      - tdr_path
      - solve_manifest
      - artifact_dir
      - frequency_start_ghz
      - frequency_stop_ghz
      - rl_target_db
      - tdr_target_ohm
      - solve_summary
      - artifact_refs
  channel_score_result:
    required_fields:
      - status
      - score
      - evidence_summary
      - artifact_refs
  real_solve_scorecard:
    required_fields:
      - status
      - checks
```

- [ ] **Step 5：补 Graph 数据适配**

Job input builder 必须加入：

```python
"approval_reason": "approve_real_brd_solve",
"approval_options": [
    {"id": "approve", "label": "批准真实 BRD local-cut 求解"},
    {"id": "reject", "label": "拒绝真实求解"},
],
```

generic validator 继续只做 schema 验证和 payload copy，不打开 AEDT。

Approval gate 创建请求时使用输入中的 reason/options：

```python
reason = str(
    context.input_payload.get("approval_reason")
    or f"graph_gate:{context.graph_run.graph_run_id}:{context.node.node_id}:{context.run_index}"
)
options = list(
    context.input_payload.get("approval_options")
    or [
        {"id": "approve", "label": "Approve"},
        {"id": "reject", "label": "Reject"},
    ]
)
approval = ApprovalService(context.runtime.store).request_approval(
    context.graph_run.mission_id,
    reason,
    options,
)
```

`_approval_output()` 必须透传已验证输入，而不是只保留 action 字段：

```python
def _approval_output(input_payload: dict[str, Any], approval) -> dict[str, Any]:
    output = {
        key: value
        for key, value in input_payload.items()
        if key not in {"_handoffs", "approval_reason", "approval_options"}
    }
    output["approval_id"] = approval.approval_id
    output["decision"] = approval.decision.value
    for option in approval.options:
        if not isinstance(option, dict):
            continue
        for key in ("action_id", "action_digest"):
            if key in option and key not in output:
                output[
                    "digest" if key == "action_digest" else key
                ] = option[key]
    return output
```

补回归测试，确保 Action approval 的 `action_id/digest` 行为保持不变。

real solve Worker 输出其受控 `artifact_dir` 和 score 参数，Graph 自动创建的 `brd.channel.score` Job 直接使用这些字段。评分 evidence 写入 solve attempt 的 `artifacts/`，该目录已经由 Harness 校验并持久保留，不接受用户输入的输出目录。

- [ ] **Step 6：扩展真实 solve scorecard**

`score_mission()` 在 `template_id == "brd_real_solve_evidence"` 时增加：

```python
checks.extend(
    [
        _check("model_approval_resolved", approval_ok, approval_details),
        _check("solve_used_local_process", process_ok, process_details),
        _check("solve_manifest_verified", manifest_ok, manifest_details),
        _check("solve_artifacts_verified", artifacts_ok, artifacts_details),
        _check("raw_arrays_excluded", bounded_ok, bounded_details),
        _check("channel_score_bound_to_solve", lineage_ok, lineage_details),
    ]
)
```

manifest 检查重新计算 SHA-256，不信任 manifest 自报。

- [ ] **Step 7：测试并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_brd_real_solve_graph.py tests/test_agent_graph_template.py tests/test_agent_graph_executors.py tests/test_agent_scorecard.py
git add docs/agent_templates/brd_real_solve_evidence.yaml src/aedt_agent/agent/graph_executors.py src/aedt_agent/agent/scorecard.py tests/test_agent_brd_real_solve_graph.py tests/test_agent_graph_template.py tests/test_agent_scorecard.py
git commit -m "feat: orchestrate approved brd solve evidence graph"
```

## Task 8：真实 smoke、恢复审计与阶段收口

**Files:**
- Create: `tests/test_agent_brd_real_solve_smoke.py`
- Modify: `tests/test_agent_harness_recovery.py`
- Modify: `tests/test_agent_brd_real_solve_graph.py`
- Create: `docs/real-brd-solve-smoke.md`

- [ ] **Step 1：写 completed-result 恢复测试**

```python
def test_recovery_adopts_completed_real_solve_without_second_execution(
    tmp_path,
):
    runtime, graph_run, job, attempt = _interrupted_after_result_fixture(
        tmp_path
    )

    recovery = runtime.recover_harness_attempts(
        job.mission_id,
        process_controller=FakeProcessController(set()),
    )
    report = resume_graph(runtime, graph_run.graph_run_id)

    assert recovery["adopted_completed_attempt_ids"] == [attempt.attempt_id]
    assert report["status"] == "succeeded"
    assert len(runtime.store.list_job_attempts(job.job_id)) == 1
```

fixture 写入 solve Worker 合同的 `result.json`、Touchstone/TDR/manifest artifacts，并绑定 active NodeRun。

- [ ] **Step 2：写 timeout/cancel 进程清理集成测试**

fake solve entrypoint 启动一个子进程并等待。分别：

- Job timeout；
- Mission cancel；
- stale heartbeat `--terminate-stale`。

断言：

- Job/Attempt 状态正确；
- Graph 不走 success edge；
- PID 不存活；
- stdout/stderr/request/result artifacts 仍注册。

- [ ] **Step 3：创建真实 smoke**

```python
RUN_REAL = os.getenv("ANSYS_AGENT_RUN_REAL_AEDT") == "1"


@pytest.mark.skipif(
    not RUN_REAL,
    reason="set ANSYS_AGENT_RUN_REAL_AEDT=1 to run AEDT smoke",
)
def test_real_aedt_solve_exports_touchstone_and_tdr(tmp_path):
    project = Path(os.environ["ANSYS_AGENT_REAL_AEDT_PROJECT"])
    request = BrdRealSolveRequest(
        project_path=project,
        artifact_dir=tmp_path / "artifacts",
        setup_name=os.getenv("ANSYS_AGENT_REAL_AEDT_SETUP", "Setup1"),
        sweep_name=os.getenv("ANSYS_AGENT_REAL_AEDT_SWEEP", "Sweep1"),
        solution_name=(
            f"{os.getenv('ANSYS_AGENT_REAL_AEDT_SETUP', 'Setup1')} : "
            f"{os.getenv('ANSYS_AGENT_REAL_AEDT_SWEEP', 'Sweep1')}"
        ),
        touchstone_name="channel.s2p",
        tdr_report_name="AgentTDR",
        tdr_expression=os.environ["ANSYS_AGENT_REAL_AEDT_TDR_EXPRESSION"],
        expected_port_count=int(
            os.getenv("ANSYS_AGENT_REAL_AEDT_PORT_COUNT", "2")
        ),
        environment=RealAedtEnvironment(
            version=os.getenv("ANSYS_AGENT_REAL_AEDT_VERSION", "2026.1"),
            non_graphical=True,
        ),
    )
    request.artifact_dir.mkdir(parents=True)

    result = BrdRealSolveAdapter().run(request)

    assert parse_touchstone(Path(result.touchstone_path))
    assert parse_tdr_csv(Path(result.tdr_path))
    assert Path(result.solve_manifest_path).exists()
```

- [ ] **Step 4：写真实 smoke 文档**

`docs/real-brd-solve-smoke.md` 记录：

```powershell
$env:ANSYS_AGENT_RUN_REAL_AEDT = "1"
$env:ANSYS_AGENT_REAL_AEDT_PROJECT = "D:\cases\approved_local_cut.aedt"
$env:ANSYS_AGENT_REAL_AEDT_SETUP = "Setup1"
$env:ANSYS_AGENT_REAL_AEDT_SWEEP = "Sweep1"
$env:ANSYS_AGENT_REAL_AEDT_TDR_EXPRESSION = "TDRZt(P1,P1)"
$env:ANSYS_AGENT_REAL_AEDT_PORT_COUNT = "2"
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_brd_real_solve_smoke.py
```

并说明该工程必须是 local-cut、已审批副本，不得指向生产原件。

- [ ] **Step 5：跑 Agent 全量**

```powershell
$files = Get-ChildItem tests\test_agent_*.py | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest -q $files
```

Expected: 全部通过；真实 smoke 在未设置环境变量时 skip。

- [ ] **Step 6：静态和架构审计**

```powershell
.\.venv\Scripts\python.exe -m compileall -q src\aedt_agent\agent src\aedt_agent\infrastructure
rg -n "aedt_agent\.v0" src\aedt_agent\agent src\aedt_agent\infrastructure
rg -n "raw_sparameters|raw_tdr" src\aedt_agent\agent tests\test_agent_brd_real_solve*
git diff --check
```

Expected:

- compileall 成功；
- 新 Agent/Infrastructure 无 `v0` 依赖；
- solve/evidence summary 只出现 `artifact_only` 标记，不出现 raw arrays；
- 无 whitespace error。

- [ ] **Step 7：跑全仓测试**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: 本阶段新增测试通过；现有 9 个已登记失败不扩大：

- 1 个 harness generator Windows separator；
- 2 个 Cadence launcher separator；
- 3 个缺失 Stage B benchmark fixture；
- 1 个 Stage B presentation path scrub；
- 1 个 Stage C1 Windows JSON fixture；
- 1 个 Stage C demo script 缺失 fixture。

- [ ] **Step 8：提交**

```powershell
git add tests/test_agent_brd_real_solve_smoke.py tests/test_agent_harness_recovery.py tests/test_agent_brd_real_solve_graph.py docs/real-brd-solve-smoke.md
git commit -m "test: verify recoverable real brd solve mission"
```

## 完成定义

- [ ] `brd.local_cut.solve` 只能作为 local-process Worker 运行。
- [ ] WorkerContext 的 artifact 目录来自受控 Harness workspace。
- [ ] solve 同时获取 `license` 与 `aedt` 资源，部分获取失败会释放。
- [ ] adapter 对 checkpoint 副本执行 `analyze_setup()`。
- [ ] Touchstone 和 TDR 由 AEDT 导出并经过解析验证。
- [ ] solved project、Touchstone、TDR、manifest、stdout/stderr 都注册为 artifact。
- [ ] raw S 参数和 TDR 不进入 Mission/Evidence summary。
- [ ] artifact query 每次最多返回 128 点，并记录 query Event。
- [ ] approval 后恢复同一 GraphRun，再执行 solve/score/scorecard。
- [ ] safe profile 在进程启动前阻止真实 AEDT。
- [ ] completed Harness result 恢复时不重复 solve。
- [ ] 真实 smoke 默认 skip，显式启用时可验证 AEDT 2026.1。
- [ ] Agent 测试全绿，全仓失败集合不扩大。
