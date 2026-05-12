# AEDT Stage B Controlled MCP Nodes 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 Stage A 数据资产和离线 Benchmark 基座之上，实现受控 AEDT MCP 执行层与静态节点原型。正式路径只开放 `execute_node`，开发期仅保留受限 `execute_script_restricted`。

**架构：** Stage B 不做完整 DAG Runtime、不做可视化编辑器、不接 GitNexus。它实现一个可测试的 MCP 服务内核：session manager 管理 HFSS/pyaedt 长连接，execution queue 串行化所有 AEDT 写操作，AST guard 阻断危险 Python，node executor 读取节点 catalog 并执行受控节点，validation 层对节点 postcheck 和任务级规则做统一记录。真实 AEDT 不可用时，测试使用 fake AEDT adapter 覆盖行为。

**技术栈：** Python 3.11+、标准库 `ast`/`asyncio`/`dataclasses`/`json`/`time`/`uuid`、PyYAML、pytest。FastMCP 作为运行时依赖延后到集成任务，核心逻辑先保持框架无关。

---

## 文件结构

本计划假设 Stage A 已创建以下文件：

```text
pyproject.toml
src/aedt_agent/knowledge/*
src/aedt_agent/nodes/models.py
src/aedt_agent/nodes/registry.py
nodes/catalog/*.yaml
tests/
```

本计划新增或修改：

```text
src/aedt_agent/mcp/__init__.py
src/aedt_agent/mcp/types.py
src/aedt_agent/mcp/ast_guard.py
src/aedt_agent/mcp/session_manager.py
src/aedt_agent/mcp/fake_aedt.py
src/aedt_agent/mcp/execution_queue.py
src/aedt_agent/mcp/audit_log.py
src/aedt_agent/mcp/node_executor.py
src/aedt_agent/mcp/tools.py
src/aedt_agent/mcp/server.py
src/aedt_agent/validation/__init__.py
src/aedt_agent/validation/state_snapshot.py
src/aedt_agent/validation/rules.py
src/aedt_agent/cli.py
tests/test_ast_guard.py
tests/test_session_manager.py
tests/test_execution_queue.py
tests/test_audit_log.py
tests/test_node_executor.py
tests/test_mcp_tools.py
tests/test_validation_rules.py
```

职责边界：

- `mcp/types.py`：共享请求、结果、状态类型。
- `mcp/ast_guard.py`：只做 Python AST 安全审计，不知道 AEDT。
- `mcp/session_manager.py`：只管理 session 生命周期和 adapter，不执行节点逻辑。
- `mcp/execution_queue.py`：只做串行队列、锁、timeout、失败传播。
- `mcp/node_executor.py`：只把节点定义、输入、session、validation 串起来。
- `mcp/tools.py`：提供 MCP 工具函数的纯 Python 内核，便于测试。
- `mcp/server.py`：FastMCP 外壳，尽量薄。
- `validation/*`：只做状态快照和规则判定。

---

### 任务 1：创建 Stage B MCP 包和共享类型

**文件：**
- 创建：`src/aedt_agent/mcp/__init__.py`
- 创建：`src/aedt_agent/mcp/types.py`
- 创建：`tests/test_session_manager.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_session_manager.py`：

```python
from aedt_agent.mcp.types import ExecutionStatus, SessionRef


def test_session_ref_has_required_ids():
    ref = SessionRef(
        session_id="session-1",
        project_id="project-1",
        design_id="design-1",
    )

    assert ref.session_id == "session-1"
    assert ref.project_id == "project-1"
    assert ref.design_id == "design-1"


def test_execution_status_values_are_stable():
    assert ExecutionStatus.QUEUED.value == "queued"
    assert ExecutionStatus.SUCCEEDED.value == "succeeded"
    assert ExecutionStatus.FAILED.value == "failed"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_session_manager.py -q
```

预期：FAIL，报错找不到 `aedt_agent.mcp.types`。

- [ ] **步骤 3：实现包初始化和共享类型**

创建 `src/aedt_agent/mcp/__init__.py`：

```python
"""Controlled AEDT MCP execution layer."""
```

创建 `src/aedt_agent/mcp/types.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class SessionRef:
    session_id: str
    project_id: str
    design_id: str


@dataclass(frozen=True)
class TransactionRef:
    transaction_id: str
    session: SessionRef
    node_id: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    transaction_id: str
    output: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    traceback: str = ""
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_session_manager.py -q
```

预期：PASS，2 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp tests/test_session_manager.py
git commit -m "feat: add mcp execution types"
```

---

### 任务 2：实现 AST Guard

**文件：**
- 创建：`src/aedt_agent/mcp/ast_guard.py`
- 创建：`tests/test_ast_guard.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_ast_guard.py`：

```python
from aedt_agent.mcp.ast_guard import AstGuard


def test_ast_guard_accepts_simple_aedt_calls():
    guard = AstGuard()

    result = guard.validate("app.modeler.create_box([0,0,0], [1,1,1], name='box')")

    assert result.passed is True
    assert result.violations == []


def test_ast_guard_rejects_forbidden_import():
    guard = AstGuard()

    result = guard.validate("import subprocess\nsubprocess.run(['dir'])")

    assert result.passed is False
    assert "forbidden import: subprocess" in result.violations


def test_ast_guard_rejects_file_delete():
    guard = AstGuard()

    result = guard.validate("Path('x').unlink()")

    assert result.passed is False
    assert "forbidden call: Path.unlink" in result.violations


def test_ast_guard_rejects_open_write_outside_session():
    guard = AstGuard()

    result = guard.validate("open('C:/Users/file.txt', 'w')")

    assert result.passed is False
    assert "forbidden call: open" in result.violations
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_ast_guard.py -q
```

预期：FAIL，报错找不到 `AstGuard`。

- [ ] **步骤 3：实现 AST Guard**

创建 `src/aedt_agent/mcp/ast_guard.py`：

```python
from __future__ import annotations

import ast

from aedt_agent.mcp.types import GuardResult


class AstGuard:
    forbidden_imports = {"os", "sys", "subprocess", "socket", "shutil"}
    forbidden_names = {"eval", "exec", "compile", "__import__", "open"}
    forbidden_attribute_calls = {
        "Path.unlink",
        "Path.rmdir",
        "Path.rename",
        "Path.replace",
        "shutil.rmtree",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.system",
        "subprocess.run",
        "subprocess.Popen",
    }

    def validate(self, code: str) -> GuardResult:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return GuardResult(False, [f"syntax error: {exc.msg}"])

        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in self.forbidden_imports:
                        violations.append(f"forbidden import: {root}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in self.forbidden_imports:
                    violations.append(f"forbidden import: {root}")
            elif isinstance(node, ast.Call):
                call_name = self._call_name(node.func)
                if call_name in self.forbidden_names:
                    violations.append(f"forbidden call: {call_name}")
                if call_name in self.forbidden_attribute_calls:
                    violations.append(f"forbidden call: {call_name}")
                if call_name.endswith(".unlink"):
                    violations.append("forbidden call: Path.unlink")

        return GuardResult(not violations, sorted(set(violations)))

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            if parent == "Path":
                return f"Path.{node.attr}"
            if parent:
                return f"{parent}.{node.attr}"
            return node.attr
        if isinstance(node, ast.Call):
            return self._call_name(node.func)
        return ""
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_ast_guard.py -q
```

预期：PASS，4 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp/ast_guard.py tests/test_ast_guard.py
git commit -m "feat: add restricted script ast guard"
```

---

### 任务 3：实现 Fake AEDT Adapter

**文件：**
- 创建：`src/aedt_agent/mcp/fake_aedt.py`
- 修改：`tests/test_session_manager.py`

- [ ] **步骤 1：追加失败测试**

在 `tests/test_session_manager.py` 末尾追加：

```python
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter


def test_fake_aedt_adapter_executes_known_calls_and_snapshots_state():
    adapter = FakeAedtAdapter(project_id="p1", design_id="d1")

    result = adapter.execute_code(
        "app.modeler.create_box(['0mm','0mm','0mm'], ['1mm','1mm','1mm'], name='box', material='FR4_epoxy')\n"
        "app.assign_material('box', 'FR4_epoxy')"
    )

    snapshot = adapter.snapshot_state()
    assert result["created_objects"] == ["box"]
    assert snapshot["objects"]["box"]["material"] == "FR4_epoxy"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_session_manager.py::test_fake_aedt_adapter_executes_known_calls_and_snapshots_state -q
```

预期：FAIL，报错找不到 `FakeAedtAdapter`。

- [ ] **步骤 3：实现 Fake Adapter**

创建 `src/aedt_agent/mcp/fake_aedt.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeModeler:
    objects: dict[str, dict[str, Any]]

    def create_box(self, origin, sizes, name=None, material=None):
        object_name = name or f"Box{len(self.objects) + 1}"
        self.objects[object_name] = {
            "type": "box",
            "origin": origin,
            "sizes": sizes,
            "material": material or "",
            "faces": [f"{object_name}_face_{index}" for index in range(6)],
        }
        return object_name

    def create_rectangle(self, origin, sizes, name=None, material=None):
        object_name = name or f"Rectangle{len(self.objects) + 1}"
        self.objects[object_name] = {
            "type": "rectangle",
            "origin": origin,
            "sizes": sizes,
            "material": material or "",
            "faces": [f"{object_name}_face_0"],
        }
        return object_name

    def get_object_faces(self, object_name):
        return self.objects[object_name]["faces"]

    def get_face_center(self, face_id):
        return [0, 0, 0]


@dataclass
class FakeAedtApp:
    project_id: str
    design_id: str
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    ports: dict[str, dict[str, Any]] = field(default_factory=dict)
    boundaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    setups: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        self.modeler = FakeModeler(self.objects)

    def assign_material(self, assignment, material):
        if assignment not in self.objects:
            raise KeyError(f"object not found: {assignment}")
        self.objects[assignment]["material"] = material
        return True

    def create_wave_port(self, assignment, reference=None, name=None, **kwargs):
        port_name = name or f"Port{len(self.ports) + 1}"
        self.ports[port_name] = {
            "assignment": assignment,
            "reference": reference,
            "kwargs": kwargs,
        }
        return port_name

    def assign_radiation_boundary_to_objects(self, assignment, name=None):
        boundary_name = name or f"Rad{len(self.boundaries) + 1}"
        self.boundaries[boundary_name] = {"assignment": assignment, "type": "radiation"}
        return boundary_name

    def create_setup(self, name="Setup1", setup_type=None, **kwargs):
        self.setups[name] = {"setup_type": setup_type, "kwargs": kwargs}
        return name

    def create_linear_count_sweep(self, setup, units, start_frequency, stop_frequency, num_of_freq_points, **kwargs):
        sweep_name = kwargs.get("name", f"{setup}_Sweep")
        self.setups.setdefault(setup, {})["sweep"] = {
            "name": sweep_name,
            "units": units,
            "start_frequency": start_frequency,
            "stop_frequency": stop_frequency,
            "num_of_freq_points": num_of_freq_points,
        }
        return sweep_name


class FakeAedtAdapter:
    def __init__(self, project_id: str, design_id: str):
        self.app = FakeAedtApp(project_id=project_id, design_id=design_id)

    def health_check(self) -> bool:
        return True

    def execute_code(self, code: str) -> dict[str, Any]:
        before = set(self.app.objects)
        namespace = {"app": self.app}
        exec(code, {"__builtins__": {}}, namespace)
        created = sorted(set(self.app.objects) - before)
        return {"created_objects": created}

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "project_id": self.app.project_id,
            "design_id": self.app.design_id,
            "objects": self.app.objects,
            "ports": self.app.ports,
            "boundaries": self.app.boundaries,
            "setups": self.app.setups,
        }
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_session_manager.py -q
```

预期：PASS，3 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp/fake_aedt.py tests/test_session_manager.py
git commit -m "test: add fake aedt adapter"
```

---

### 任务 4：实现 Session Manager

**文件：**
- 创建：`src/aedt_agent/mcp/session_manager.py`
- 修改：`tests/test_session_manager.py`

- [ ] **步骤 1：追加失败测试**

在 `tests/test_session_manager.py` 末尾追加：

```python
from aedt_agent.mcp.session_manager import SessionManager


def test_session_manager_creates_and_returns_session():
    manager = SessionManager(adapter_factory=lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))

    session = manager.create_session(project_id="project-a", design_id="design-a")
    same_session = manager.get_session(session.session_id)

    assert same_session.ref.project_id == "project-a"
    assert same_session.adapter.health_check() is True


def test_session_manager_snapshot_contains_state():
    manager = SessionManager(adapter_factory=lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    session = manager.create_session(project_id="project-a", design_id="design-a")

    snapshot = manager.snapshot(session.session_id)

    assert snapshot["project_id"] == "project-a"
    assert snapshot["design_id"] == "design-a"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_session_manager.py::test_session_manager_creates_and_returns_session -q
```

预期：FAIL，报错找不到 `SessionManager`。

- [ ] **步骤 3：实现 Session Manager**

创建 `src/aedt_agent/mcp/session_manager.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
from uuid import uuid4

from aedt_agent.mcp.types import SessionRef


class AedtAdapter(Protocol):
    def health_check(self) -> bool:
        ...

    def execute_code(self, code: str) -> dict:
        ...

    def snapshot_state(self) -> dict:
        ...


@dataclass
class ManagedSession:
    ref: SessionRef
    adapter: AedtAdapter


class SessionManager:
    def __init__(self, adapter_factory: Callable[[str, str], AedtAdapter]):
        self._adapter_factory = adapter_factory
        self._sessions: dict[str, ManagedSession] = {}

    def create_session(self, project_id: str, design_id: str) -> ManagedSession:
        session_id = f"session-{uuid4().hex}"
        adapter = self._adapter_factory(project_id, design_id)
        session = ManagedSession(
            ref=SessionRef(session_id=session_id, project_id=project_id, design_id=design_id),
            adapter=adapter,
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> ManagedSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown session_id: {session_id}") from exc

    def health_check(self, session_id: str) -> bool:
        return self.get_session(session_id).adapter.health_check()

    def snapshot(self, session_id: str) -> dict:
        return self.get_session(session_id).adapter.snapshot_state()
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_session_manager.py -q
```

预期：PASS，5 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp/session_manager.py tests/test_session_manager.py
git commit -m "feat: add aedt session manager"
```

---

### 任务 5：实现串行 Execution Queue

**文件：**
- 创建：`src/aedt_agent/mcp/execution_queue.py`
- 创建：`tests/test_execution_queue.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_execution_queue.py`：

```python
import time

from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.types import ExecutionStatus, SessionRef


def test_execution_queue_runs_code_successfully():
    queue = ExecutionQueue(timeout_seconds=1.0)
    adapter = FakeAedtAdapter(project_id="p1", design_id="d1")
    session = SessionRef(session_id="s1", project_id="p1", design_id="d1")

    result = queue.submit_code(
        session=session,
        adapter=adapter,
        code="app.modeler.create_box([0,0,0], [1,1,1], name='box')",
        node_id="create_substrate",
    )

    assert result.status == ExecutionStatus.SUCCEEDED
    assert adapter.snapshot_state()["objects"]["box"]["type"] == "box"


def test_execution_queue_returns_failed_on_exception():
    queue = ExecutionQueue(timeout_seconds=1.0)
    adapter = FakeAedtAdapter(project_id="p1", design_id="d1")
    session = SessionRef(session_id="s1", project_id="p1", design_id="d1")

    result = queue.submit_code(
        session=session,
        adapter=adapter,
        code="app.assign_material('missing', 'copper')",
        node_id="create_substrate",
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.error_type == "KeyError"


def test_execution_queue_timeout_is_reported():
    queue = ExecutionQueue(timeout_seconds=0.01)
    adapter = FakeAedtAdapter(project_id="p1", design_id="d1")
    session = SessionRef(session_id="s1", project_id="p1", design_id="d1")

    result = queue.submit_callable(
        session=session,
        fn=lambda: time.sleep(0.05),
        node_id="slow_node",
    )

    assert result.status == ExecutionStatus.TIMEOUT
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_execution_queue.py -q
```

预期：FAIL，报错找不到 `ExecutionQueue`。

- [ ] **步骤 3：实现 Execution Queue**

创建 `src/aedt_agent/mcp/execution_queue.py`：

```python
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable
from uuid import uuid4

from aedt_agent.mcp.session_manager import AedtAdapter
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus, SessionRef


class ExecutionQueue:
    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def submit_code(
        self,
        session: SessionRef,
        adapter: AedtAdapter,
        code: str,
        node_id: str | None = None,
    ) -> ExecutionResult:
        return self.submit_callable(
            session=session,
            fn=lambda: adapter.execute_code(code),
            node_id=node_id,
        )

    def submit_callable(
        self,
        session: SessionRef,
        fn: Callable[[], object],
        node_id: str | None = None,
    ) -> ExecutionResult:
        transaction_id = f"txn-{uuid4().hex}"
        with self._lock:
            started_at = time.monotonic()
            try:
                output = fn()
            except Exception as exc:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    transaction_id=transaction_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    traceback=traceback.format_exc(),
                )
            elapsed = time.monotonic() - started_at
            if elapsed > self.timeout_seconds:
                return ExecutionResult(
                    status=ExecutionStatus.TIMEOUT,
                    transaction_id=transaction_id,
                    error_type="Timeout",
                    error_message=f"Execution exceeded {self.timeout_seconds} seconds",
                )
            if isinstance(output, dict):
                output_dict = output
            else:
                output_dict = {"result": output}
            output_dict.setdefault("session_id", session.session_id)
            if node_id is not None:
                output_dict.setdefault("node_id", node_id)
            return ExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                transaction_id=transaction_id,
                output=output_dict,
            )
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_execution_queue.py -q
```

预期：PASS，3 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp/execution_queue.py tests/test_execution_queue.py
git commit -m "feat: add serial aedt execution queue"
```

---

### 任务 6：实现状态快照与 validation 规则

**文件：**
- 创建：`src/aedt_agent/validation/__init__.py`
- 创建：`src/aedt_agent/validation/state_snapshot.py`
- 创建：`src/aedt_agent/validation/rules.py`
- 创建：`tests/test_validation_rules.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_validation_rules.py`：

```python
from aedt_agent.validation.rules import (
    ValidationOutcome,
    validate_object_exists,
    validate_object_material,
    validate_port_created,
    validate_setup_created,
)


def test_validate_object_exists_passes_when_object_present():
    state = {"objects": {"substrate": {"material": "FR4_epoxy"}}}

    outcome = validate_object_exists(state, object_id="substrate")

    assert outcome.passed is True


def test_validate_object_material_fails_on_wrong_material():
    state = {"objects": {"substrate": {"material": "vacuum"}}}

    outcome = validate_object_material(state, object_id="substrate", material="FR4_epoxy")

    assert outcome.passed is False
    assert "expected material FR4_epoxy" in outcome.message


def test_validate_port_created_checks_port_id():
    state = {"ports": {"P1": {"assignment": "face1"}}}

    outcome = validate_port_created(state, port_id="P1")

    assert outcome == ValidationOutcome(True, "port exists")


def test_validate_setup_created_checks_setup_id():
    state = {"setups": {"Setup1": {}}}

    outcome = validate_setup_created(state, setup_id="Setup1")

    assert outcome.passed is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_validation_rules.py -q
```

预期：FAIL，报错找不到 `aedt_agent.validation`。

- [ ] **步骤 3：实现 validation 包**

创建 `src/aedt_agent/validation/__init__.py`：

```python
"""Validation rules for AEDT state snapshots."""
```

创建 `src/aedt_agent/validation/state_snapshot.py`：

```python
from __future__ import annotations

from copy import deepcopy
from typing import Any


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(snapshot)
    normalized.setdefault("objects", {})
    normalized.setdefault("ports", {})
    normalized.setdefault("boundaries", {})
    normalized.setdefault("setups", {})
    return normalized
```

创建 `src/aedt_agent/validation/rules.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

from aedt_agent.validation.state_snapshot import normalize_snapshot


@dataclass(frozen=True)
class ValidationOutcome:
    passed: bool
    message: str


def validate_object_exists(state: dict, object_id: str) -> ValidationOutcome:
    snapshot = normalize_snapshot(state)
    if object_id in snapshot["objects"]:
        return ValidationOutcome(True, "object exists")
    return ValidationOutcome(False, f"object not found: {object_id}")


def validate_object_material(state: dict, object_id: str, material: str) -> ValidationOutcome:
    snapshot = normalize_snapshot(state)
    if object_id not in snapshot["objects"]:
        return ValidationOutcome(False, f"object not found: {object_id}")
    actual = snapshot["objects"][object_id].get("material", "")
    if actual == material:
        return ValidationOutcome(True, "material matches")
    return ValidationOutcome(False, f"expected material {material}, got {actual}")


def validate_port_created(state: dict, port_id: str) -> ValidationOutcome:
    snapshot = normalize_snapshot(state)
    if port_id in snapshot["ports"]:
        return ValidationOutcome(True, "port exists")
    return ValidationOutcome(False, f"port not found: {port_id}")


def validate_setup_created(state: dict, setup_id: str) -> ValidationOutcome:
    snapshot = normalize_snapshot(state)
    if setup_id in snapshot["setups"]:
        return ValidationOutcome(True, "setup exists")
    return ValidationOutcome(False, f"setup not found: {setup_id}")
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_validation_rules.py -q
```

预期：PASS，4 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/validation tests/test_validation_rules.py
git commit -m "feat: add snapshot validation rules"
```

---

### 任务 7：实现审计日志

**文件：**
- 创建：`src/aedt_agent/mcp/audit_log.py`
- 创建：`tests/test_audit_log.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_audit_log.py`：

```python
import json

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus


def test_audit_logger_writes_jsonl_event(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")

    logger.record(
        event_type="execute_node",
        session_id="s1",
        node_id="create_substrate",
        request={"length": "20mm"},
        result=ExecutionResult(status=ExecutionStatus.SUCCEEDED, transaction_id="txn-1"),
        state_before={"objects": {}},
        state_after={"objects": {"substrate": {}}},
    )

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    assert event["event_type"] == "execute_node"
    assert event["node_id"] == "create_substrate"
    assert event["result"]["status"] == "succeeded"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_audit_log.py -q
```

预期：FAIL，报错找不到 `AuditLogger`。

- [ ] **步骤 3：实现审计日志**

创建 `src/aedt_agent/mcp/audit_log.py`：

```python
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aedt_agent.mcp.types import ExecutionResult


class AuditLogger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event_type: str,
        session_id: str,
        node_id: str | None,
        request: dict[str, Any],
        result: ExecutionResult,
        state_before: dict[str, Any],
        state_after: dict[str, Any],
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "session_id": session_id,
            "node_id": node_id,
            "request": request,
            "result": _result_to_dict(result),
            "state_before": state_before,
            "state_after": state_after,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _result_to_dict(result: ExecutionResult) -> dict[str, Any]:
    data = asdict(result)
    data["status"] = result.status.value
    return data
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_audit_log.py -q
```

预期：PASS，1 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp/audit_log.py tests/test_audit_log.py
git commit -m "feat: add mcp audit logger"
```

---

### 任务 8：实现节点执行器

**文件：**
- 创建：`src/aedt_agent/mcp/node_executor.py`
- 创建：`tests/test_node_executor.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_node_executor.py`：

```python
from pathlib import Path

from aedt_agent.mcp.ast_guard import AstGuard
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionStatus
from aedt_agent.nodes.registry import NodeRegistry


def test_node_executor_runs_create_substrate_with_template(tmp_path):
    manager = SessionManager(adapter_factory=lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    session = manager.create_session("p1", "d1")
    executor = NodeExecutor(
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
        session_manager=manager,
        queue=ExecutionQueue(timeout_seconds=1.0),
        ast_guard=AstGuard(),
    )

    result = executor.execute_node(
        session_id=session.ref.session_id,
        node_id="create_substrate",
        inputs={
            "length": "20mm",
            "width": "15mm",
            "thickness": "0.8mm",
            "material": "FR4_epoxy",
            "name": "substrate",
        },
    )

    assert result.status == ExecutionStatus.SUCCEEDED
    state = manager.snapshot(session.ref.session_id)
    assert state["objects"]["substrate"]["material"] == "FR4_epoxy"


def test_node_executor_rejects_unknown_node(tmp_path):
    manager = SessionManager(adapter_factory=lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    session = manager.create_session("p1", "d1")
    executor = NodeExecutor(
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
        session_manager=manager,
        queue=ExecutionQueue(timeout_seconds=1.0),
        ast_guard=AstGuard(),
    )

    result = executor.execute_node(
        session_id=session.ref.session_id,
        node_id="not_a_node",
        inputs={},
    )

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "UnknownNode"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_node_executor.py -q
```

预期：FAIL，报错找不到 `NodeExecutor`。

- [ ] **步骤 3：实现节点执行器**

创建 `src/aedt_agent/mcp/node_executor.py`：

```python
from __future__ import annotations

from uuid import uuid4

from aedt_agent.mcp.ast_guard import AstGuard
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus
from aedt_agent.nodes.registry import NodeRegistry


class NodeExecutor:
    def __init__(
        self,
        registry: NodeRegistry,
        session_manager: SessionManager,
        queue: ExecutionQueue,
        ast_guard: AstGuard,
    ):
        self.registry = registry
        self.session_manager = session_manager
        self.queue = queue
        self.ast_guard = ast_guard

    def execute_node(self, session_id: str, node_id: str, inputs: dict) -> ExecutionResult:
        try:
            self.registry.get(node_id)
        except KeyError:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                transaction_id=f"txn-{uuid4().hex}",
                error_type="UnknownNode",
                error_message=f"Unknown node_id: {node_id}",
            )

        code = self._render_node_code(node_id, inputs)
        guard_result = self.ast_guard.validate(code)
        if not guard_result.passed:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                transaction_id=f"txn-{uuid4().hex}",
                error_type="AstGuardViolation",
                error_message="; ".join(guard_result.violations),
            )

        session = self.session_manager.get_session(session_id)
        return self.queue.submit_code(
            session=session.ref,
            adapter=session.adapter,
            code=code,
            node_id=node_id,
        )

    def _render_node_code(self, node_id: str, inputs: dict) -> str:
        if node_id == "create_substrate":
            name = inputs.get("name", "substrate")
            length = inputs["length"]
            width = inputs["width"]
            thickness = inputs["thickness"]
            material = inputs["material"]
            return (
                "app.modeler.create_box("
                f"['0mm', '0mm', '0mm'], "
                f"[{length!r}, {width!r}, {thickness!r}], "
                f"name={name!r}, material={material!r})\n"
                f"app.assign_material({name!r}, {material!r})"
            )
        if node_id == "create_setup":
            name = inputs.get("name", "Setup1")
            frequency = inputs["frequency"]
            return f"app.create_setup(name={name!r}, Frequency={frequency!r})"
        raise ValueError(f"No Stage B template for node_id: {node_id}")
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_node_executor.py -q
```

预期：PASS，2 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp/node_executor.py tests/test_node_executor.py
git commit -m "feat: execute static nodes through queue"
```

---

### 任务 9：实现 MCP 工具内核

**文件：**
- 创建：`src/aedt_agent/mcp/tools.py`
- 创建：`tests/test_mcp_tools.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_mcp_tools.py`：

```python
from pathlib import Path

from aedt_agent.mcp.tools import McpToolKernel, create_fake_kernel
from aedt_agent.mcp.types import ExecutionStatus


def test_tool_kernel_lists_nodes():
    kernel = create_fake_kernel(Path("nodes/catalog"))

    nodes = kernel.list_available_nodes()

    assert "create_substrate" in nodes
    assert "execute_script" not in nodes


def test_tool_kernel_execute_node_updates_model_info():
    kernel = create_fake_kernel(Path("nodes/catalog"))
    session = kernel.create_session(project_id="p1", design_id="d1")

    result = kernel.execute_node(
        node_id="create_substrate",
        inputs={
            "length": "20mm",
            "width": "15mm",
            "thickness": "0.8mm",
            "material": "FR4_epoxy",
            "name": "substrate",
        },
        session_id=session["session_id"],
    )

    info = kernel.get_model_info(session["session_id"])
    assert result.status == ExecutionStatus.SUCCEEDED
    assert "substrate" in info["objects"]


def test_tool_kernel_restricted_script_rejects_dangerous_code():
    kernel = create_fake_kernel(Path("nodes/catalog"))
    session = kernel.create_session(project_id="p1", design_id="d1")

    result = kernel.execute_script_restricted(
        code="import os\nos.remove('x')",
        session_id=session["session_id"],
    )

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "AstGuardViolation"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_mcp_tools.py -q
```

预期：FAIL，报错找不到 `McpToolKernel`。

- [ ] **步骤 3：实现 MCP 工具内核**

创建 `src/aedt_agent/mcp/tools.py`：

```python
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from aedt_agent.mcp.ast_guard import AstGuard
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus
from aedt_agent.nodes.registry import NodeRegistry


class McpToolKernel:
    def __init__(
        self,
        registry: NodeRegistry,
        session_manager: SessionManager,
        queue: ExecutionQueue,
        ast_guard: AstGuard,
    ):
        self.registry = registry
        self.session_manager = session_manager
        self.queue = queue
        self.ast_guard = ast_guard
        self.node_executor = NodeExecutor(
            registry=registry,
            session_manager=session_manager,
            queue=queue,
            ast_guard=ast_guard,
        )

    def create_session(self, project_id: str, design_id: str) -> dict:
        session = self.session_manager.create_session(project_id, design_id)
        return {
            "session_id": session.ref.session_id,
            "project_id": session.ref.project_id,
            "design_id": session.ref.design_id,
        }

    def list_available_nodes(self) -> list[str]:
        return [node.id for node in self.registry.list_nodes()]

    def execute_node(self, node_id: str, inputs: dict, session_id: str) -> ExecutionResult:
        return self.node_executor.execute_node(session_id=session_id, node_id=node_id, inputs=inputs)

    def execute_script_restricted(self, code: str, session_id: str) -> ExecutionResult:
        guard = self.ast_guard.validate(code)
        if not guard.passed:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                transaction_id=f"txn-{uuid4().hex}",
                error_type="AstGuardViolation",
                error_message="; ".join(guard.violations),
            )
        session = self.session_manager.get_session(session_id)
        return self.queue.submit_code(
            session=session.ref,
            adapter=session.adapter,
            code=code,
            node_id=None,
        )

    def get_model_info(self, session_id: str) -> dict:
        return self.session_manager.snapshot(session_id)


def create_fake_kernel(node_catalog_dir: Path) -> McpToolKernel:
    registry = NodeRegistry.from_directory(node_catalog_dir)
    session_manager = SessionManager(
        adapter_factory=lambda project_id, design_id: FakeAedtAdapter(project_id, design_id)
    )
    return McpToolKernel(
        registry=registry,
        session_manager=session_manager,
        queue=ExecutionQueue(timeout_seconds=1.0),
        ast_guard=AstGuard(),
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_mcp_tools.py -q
```

预期：PASS，3 个测试通过。

- [ ] **步骤 5：Commit**

```powershell
git add src/aedt_agent/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat: add controlled mcp tool kernel"
```

---

### 任务 10：实现薄 FastMCP Server 外壳

**文件：**
- 修改：`pyproject.toml`
- 创建：`src/aedt_agent/mcp/server.py`
- 修改：`tests/test_mcp_tools.py`

- [ ] **步骤 1：修改依赖声明**

在 `pyproject.toml` 的 `[project.optional-dependencies]` 中添加 `mcp` 分组：

```toml
mcp = [
    "fastmcp>=2.0.0",
]
```

- [ ] **步骤 2：追加 server import 测试**

在 `tests/test_mcp_tools.py` 末尾追加：

```python
def test_server_module_exposes_factory():
    from aedt_agent.mcp.server import create_server

    assert callable(create_server)
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_mcp_tools.py::test_server_module_exposes_factory -q
```

预期：FAIL，报错找不到 `server.py`。

- [ ] **步骤 4：实现 server 外壳**

创建 `src/aedt_agent/mcp/server.py`：

```python
from __future__ import annotations

from pathlib import Path

from aedt_agent.mcp.tools import McpToolKernel, create_fake_kernel


def create_server(node_catalog_dir: Path = Path("nodes/catalog")):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the mcp extra to run the FastMCP server: pip install -e .[mcp]") from exc

    kernel: McpToolKernel = create_fake_kernel(node_catalog_dir)
    server = FastMCP("aedt-agent")

    @server.tool()
    def create_session(project_id: str, design_id: str) -> dict:
        return kernel.create_session(project_id=project_id, design_id=design_id)

    @server.tool()
    def list_available_nodes() -> list[str]:
        return kernel.list_available_nodes()

    @server.tool()
    def execute_node(node_id: str, inputs: dict, session_id: str) -> dict:
        result = kernel.execute_node(node_id=node_id, inputs=inputs, session_id=session_id)
        return {
            "status": result.status.value,
            "transaction_id": result.transaction_id,
            "output": result.output,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "traceback": result.traceback,
        }

    @server.tool()
    def get_model_info(session_id: str) -> dict:
        return kernel.get_model_info(session_id)

    @server.tool()
    def execute_script_restricted(code: str, session_id: str) -> dict:
        result = kernel.execute_script_restricted(code=code, session_id=session_id)
        return {
            "status": result.status.value,
            "transaction_id": result.transaction_id,
            "output": result.output,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "traceback": result.traceback,
        }

    return server
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_mcp_tools.py::test_server_module_exposes_factory -q
```

预期：PASS。测试只导入 factory，不要求安装 FastMCP。

- [ ] **步骤 6：Commit**

```powershell
git add pyproject.toml src/aedt_agent/mcp/server.py tests/test_mcp_tools.py
git commit -m "feat: add fastmcp server factory"
```

---

### 任务 11：扩展 CLI 用于 fake session 节点执行演示

**文件：**
- 修改：`src/aedt_agent/cli.py`
- 创建：`tests/test_stage_b_cli.py`

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_stage_b_cli.py`：

```python
import json

from aedt_agent.cli import main


def test_cli_fake_execute_node_outputs_model_info(tmp_path, capsys):
    exit_code = main(
        [
            "fake-execute-node",
            "--node",
            "create_substrate",
            "--inputs-json",
            json.dumps(
                {
                    "length": "20mm",
                    "width": "15mm",
                    "thickness": "0.8mm",
                    "material": "FR4_epoxy",
                    "name": "substrate",
                }
            ),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "substrate" in captured.out
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest tests/test_stage_b_cli.py -q
```

预期：FAIL，CLI 不认识 `fake-execute-node`。

- [ ] **步骤 3：修改 CLI**

在 `src/aedt_agent/cli.py` 顶部增加：

```python
import json
```

在 `main()` 中创建 parser 后，为 `subparsers` 添加命令：

```python
    fake_execute = subparsers.add_parser("fake-execute-node")
    fake_execute.add_argument("--node", required=True)
    fake_execute.add_argument("--inputs-json", required=True)
```

在 `main()` 的命令分支中添加：

```python
    if args.command == "fake-execute-node":
        from aedt_agent.mcp.tools import create_fake_kernel

        kernel = create_fake_kernel(Path("nodes/catalog"))
        session = kernel.create_session(project_id="fake-project", design_id="fake-design")
        result = kernel.execute_node(
            node_id=args.node,
            inputs=json.loads(args.inputs_json),
            session_id=session["session_id"],
        )
        model_info = kernel.get_model_info(session["session_id"])
        print(
            json.dumps(
                {
                    "result": {
                        "status": result.status.value,
                        "transaction_id": result.transaction_id,
                        "output": result.output,
                        "error_type": result.error_type,
                        "error_message": result.error_message,
                    },
                    "model_info": model_info,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest tests/test_stage_b_cli.py -q
```

预期：PASS，1 个测试通过。

- [ ] **步骤 5：运行完整测试**

运行：

```powershell
python -m pytest -q
```

预期：全部测试通过。

- [ ] **步骤 6：Commit**

```powershell
git add src/aedt_agent/cli.py tests/test_stage_b_cli.py
git commit -m "feat: add fake execute node cli"
```

---

### 任务 12：Stage B 验收报告与真实 AEDT 接入说明

**文件：**
- 创建：`docs/stage-b-controlled-mcp-validation.md`

- [ ] **步骤 1：运行 fake node 演示**

运行：

```powershell
python -m aedt_agent.cli fake-execute-node --node create_substrate --inputs-json "{\"length\":\"20mm\",\"width\":\"15mm\",\"thickness\":\"0.8mm\",\"material\":\"FR4_epoxy\",\"name\":\"substrate\"}"
```

预期输出包含：

```json
"status": "succeeded"
```

以及：

```json
"substrate"
```

- [ ] **步骤 2：创建 Stage B 验收说明**

创建 `docs/stage-b-controlled-mcp-validation.md`：

```markdown
# Stage B Controlled MCP 验收说明

## 已验证能力

1. `execute_node` 是正式节点执行入口。
2. `execute_script_restricted` 仅作为开发期工具，经过 AST Guard。
3. Fake AEDT adapter 可模拟对象、材料、端口、边界、setup、sweep 状态。
4. ExecutionQueue 对同一 AEDT adapter 串行执行。
5. SessionManager 为每次执行绑定 session/project/design。
6. 节点执行结果可通过 model info 快照检查。
7. FastMCP server 保持薄外壳，业务逻辑位于 `McpToolKernel`。

## 本阶段不包含

1. 真实 AEDT Desktop 启动。
2. pyaedt 真实连接。
3. DAG Runtime。
4. 可视化节点编辑器。
5. GitNexus/Graphify 接入。

## 真实 AEDT Adapter 接入点

真实 AEDT 接入时，实现一个满足 `aedt_agent.mcp.session_manager.AedtAdapter` 协议的类：

```python
class PyaedtAdapter:
    def health_check(self) -> bool:
        ...

    def execute_code(self, code: str) -> dict:
        ...

    def snapshot_state(self) -> dict:
        ...
```

`SessionManager` 的 `adapter_factory` 替换为 `PyaedtAdapter(project_id, design_id)` 即可。其他节点执行、队列、AST Guard、审计日志和 MCP 工具内核不需要重写。

## 验收命令

```powershell
python -m pytest -q
python -m aedt_agent.cli fake-execute-node --node create_substrate --inputs-json "{\"length\":\"20mm\",\"width\":\"15mm\",\"thickness\":\"0.8mm\",\"material\":\"FR4_epoxy\",\"name\":\"substrate\"}"
```
```

- [ ] **步骤 3：运行完整测试**

运行：

```powershell
python -m pytest -q
```

预期：全部测试通过。

- [ ] **步骤 4：Commit**

```powershell
git add docs/stage-b-controlled-mcp-validation.md
git commit -m "docs: add stage b validation notes"
```

---

## 自检

规格覆盖度：

- 正式路径 `execute_node`：任务 8、9、10、11 覆盖。
- 开发期受限 `execute_script_restricted`：任务 2、9、10 覆盖。
- AST 安全规则：任务 2 覆盖。
- AEDT session/project/design 绑定：任务 1、4、9 覆盖。
- 单实例串行队列：任务 5 覆盖。
- fake AEDT adapter：任务 3 覆盖，用于无 AEDT 环境测试。
- validation/postcheck 基础规则：任务 6 覆盖。
- 审计日志：任务 7 覆盖。
- FastMCP 薄外壳：任务 10 覆盖。
- 真实 AEDT 接入边界：任务 12 明确 adapter 协议。

未完成标记扫描：

- 计划不使用未决实现描述作为步骤内容。
- 所有任务都包含具体路径、代码、命令和预期输出。

类型一致性：

- `SessionRef`、`ExecutionResult`、`ExecutionStatus` 在 queue、tools、executor 中一致。
- `AedtAdapter` 协议被 `FakeAedtAdapter` 满足。
- `NodeExecutor.execute_node(session_id, node_id, inputs)` 被 `McpToolKernel` 和测试一致调用。
- FastMCP server 只包装 `McpToolKernel`，不复制业务逻辑。

