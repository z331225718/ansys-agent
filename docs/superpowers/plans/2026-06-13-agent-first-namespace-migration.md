# Agent-First 命名空间迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将现有 Stage A/B/C 应用层迁入 `aedt_agent.v0`，让 `aedt-agent` 成为新 Agent 产品入口，同时保持旧 import、旧脚本、旧测试和旧 CLI 可运行。

**架构：** 采用“旧应用归档、领域能力共享”。`benchmark/chat/demo/evolution` 与旧 CLI 移入 `aedt_agent.v0`；`workflow/nodes/layout/validation/mcp/knowledge/reporting` 保持原路径。兼容层通过集中式模块别名把旧 import 映射到同一个 `v0` 模块对象，避免复制实现和类型身份分裂。

**技术栈：** Python 3.11、setuptools、argparse、pytest、标准库 `importlib` / `sys.modules` / `ast` / `tomllib`

---

## 范围说明

本计划只完成设计规格中的 Phase 1：

- 建立 `agent`、`domain`、`infrastructure`、`v0` 顶层边界；
- 迁移旧应用包；
- 保持旧 import 兼容；
- 分离新旧 CLI；
- 增加依赖方向测试；
- 保持全量测试通过。

本计划不实现 Mission、Job、SQLite、Worker、Evaluator、Approval 或 BRD Agent 闭环。这些能力必须在命名空间迁移完成后使用独立计划实现，避免把机械迁移与运行时行为变更混在一个提交序列中。

## 目标文件结构

```text
src/aedt_agent/
├── _compat.py
├── __init__.py
├── cli.py
├── agent/
│   ├── __init__.py
│   ├── cli.py
│   ├── approvals/__init__.py
│   ├── evaluation/__init__.py
│   ├── mission/__init__.py
│   ├── orchestrator/__init__.py
│   ├── planning/__init__.py
│   ├── policies/__init__.py
│   └── workers/__init__.py
├── domain/__init__.py
├── infrastructure/__init__.py
├── v0/
│   ├── __init__.py
│   ├── cli.py
│   ├── benchmark/
│   ├── chat/
│   ├── demo/
│   └── evolution/
├── benchmark/__init__.py
├── chat/__init__.py
├── demo/__init__.py
└── evolution/__init__.py
```

兼容目录 `benchmark/chat/demo/evolution` 中只保留 `__init__.py`，不保留产品逻辑。

---

### Task 1：建立迁移前基线和失败测试

**Files:**
- Create: `tests/test_v0_namespace_compatibility.py`
- Create: `tests/test_agent_cli_boundary.py`
- Create: `tests/test_architecture_dependencies.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1：运行迁移前基线**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: 当前全量测试通过。若已有测试失败，先记录失败列表，不得通过本迁移顺手修改无关行为。

- [ ] **Step 2：编写 v0 模块身份兼容测试**

Create `tests/test_v0_namespace_compatibility.py`:

```python
from __future__ import annotations

import importlib

import pytest


PACKAGE_MODULES = {
    "benchmark": [
        "aedt_executor",
        "config",
        "context_builder",
        "generator",
        "go_nogo",
        "graders",
        "harness_generator",
        "models",
        "node_plan_parser",
        "node_readiness",
        "official_retriever",
        "prompt_templates",
        "repair",
        "report_html",
        "report_html_stage_b",
        "report_html_v2",
        "runner",
        "runner_stage_b",
        "runner_v2",
        "semantic_lite",
        "stage_b_models",
        "stage_b_presentation",
        "stage_b_validation",
        "task_sets",
        "tool_usage",
        "v2_models",
    ],
    "chat": ["repair_context", "workflow_planner"],
    "demo": [
        "config",
        "import_cutout",
        "layout_ports",
        "planner",
        "planner_benchmark",
        "preflight",
        "service",
        "tuning",
        "web",
    ],
    "evolution": ["evaluator", "miner", "models", "policy", "proposer"],
}

MODULE_PAIRS = [
    (f"aedt_agent.{package}.{module}", f"aedt_agent.v0.{package}.{module}")
    for package, modules in PACKAGE_MODULES.items()
    for module in modules
]


@pytest.mark.parametrize(("legacy_name", "v0_name"), MODULE_PAIRS)
def test_legacy_import_resolves_to_same_v0_module(legacy_name: str, v0_name: str):
    legacy_module = importlib.import_module(legacy_name)
    v0_module = importlib.import_module(v0_name)

    assert legacy_module is v0_module


def test_shared_domain_packages_remain_at_existing_paths():
    for module_name in [
        "aedt_agent.workflow.executor",
        "aedt_agent.nodes.registry",
        "aedt_agent.layout.local_cut",
        "aedt_agent.validation.rules",
        "aedt_agent.mcp.node_executor",
        "aedt_agent.knowledge.sqlite_provider",
        "aedt_agent.reporting.channel_scoring_report",
    ]:
        assert importlib.import_module(module_name) is not None
```

- [ ] **Step 3：编写新旧 CLI 边界测试**

Create `tests/test_agent_cli_boundary.py`:

```python
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from aedt_agent.agent.cli import run


def test_pyproject_exposes_new_and_v0_console_scripts():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"]["aedt-agent"] == "aedt_agent.agent.cli:main"
    assert project["project"]["scripts"]["aedt-agent-v0"] == "aedt_agent.v0.cli:main"


def test_new_cli_exposes_mission_command_surface(capsys):
    exit_code = run(["mission", "status", "--mission-id", "mission-test"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "command": "mission.status",
        "message": "Mission Runtime 尚未安装；当前版本只完成 Agent-First 架构迁移。",
        "status": "runtime_unavailable",
    }


def test_root_cli_module_points_to_new_agent_cli():
    from aedt_agent import cli
    from aedt_agent.agent import cli as agent_cli

    assert cli.run is agent_cli.run
    assert cli.main is agent_cli.main
```

- [ ] **Step 4：编写依赖方向测试**

Create `tests/test_architecture_dependencies.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path


SHARED_PACKAGE_DIRS = [
    Path("src/aedt_agent/workflow"),
    Path("src/aedt_agent/nodes"),
    Path("src/aedt_agent/layout"),
    Path("src/aedt_agent/validation"),
    Path("src/aedt_agent/mcp"),
    Path("src/aedt_agent/knowledge"),
    Path("src/aedt_agent/reporting"),
]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_shared_packages_do_not_depend_on_agent_runtime():
    violations = []
    for package_dir in SHARED_PACKAGE_DIRS:
        for path in package_dir.rglob("*.py"):
            for imported_name in _imports(path):
                if imported_name == "aedt_agent.agent" or imported_name.startswith("aedt_agent.agent."):
                    violations.append(f"{path}: {imported_name}")

    assert violations == []


def test_agent_runtime_does_not_depend_on_v0():
    violations = []
    agent_dir = Path("src/aedt_agent/agent")
    for path in agent_dir.rglob("*.py"):
        for imported_name in _imports(path):
            if imported_name == "aedt_agent.v0" or imported_name.startswith("aedt_agent.v0."):
                violations.append(f"{path}: {imported_name}")

    assert violations == []
```

- [ ] **Step 5：把旧 CLI 行为测试改为明确指向 v0**

In `tests/test_runner.py`, replace the old CLI import and executable name:

```python
def test_cli_run_benchmark_with_config(tmp_path, monkeypatch):
    from aedt_agent.v0 import cli

    monkeypatch.setattr(
        "sys.argv",
        ["aedt-agent-v0", "run-benchmark", "--config", str(config_path), "--generate"],
    )

    cli.main()

    assert report_path.exists()
```

只执行两处文本替换：

```text
from aedt_agent import cli
-> from aedt_agent.v0 import cli

["aedt-agent", "run-benchmark", ...]
-> ["aedt-agent-v0", "run-benchmark", ...]
```

该函数其余代码逐字保留。

- [ ] **Step 6：运行新测试并确认按预期失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_v0_namespace_compatibility.py `
  tests\test_agent_cli_boundary.py `
  tests\test_architecture_dependencies.py `
  tests\test_runner.py::test_cli_run_benchmark_with_config -q
```

Expected: FAIL，主要原因是 `aedt_agent.v0`、`aedt_agent.agent` 和新 console scripts 尚不存在。

- [ ] **Step 7：提交测试**

```powershell
git add tests/test_v0_namespace_compatibility.py tests/test_agent_cli_boundary.py tests/test_architecture_dependencies.py tests/test_runner.py
git commit -m "test: define agent-first migration boundaries"
```

---

### Task 2：移动旧应用包到 v0 并更新内部 import

**Files:**
- Create: `src/aedt_agent/v0/__init__.py`
- Move: `src/aedt_agent/benchmark/` → `src/aedt_agent/v0/benchmark/`
- Move: `src/aedt_agent/chat/` → `src/aedt_agent/v0/chat/`
- Move: `src/aedt_agent/demo/` → `src/aedt_agent/v0/demo/`
- Move: `src/aedt_agent/evolution/` → `src/aedt_agent/v0/evolution/`
- Move: `src/aedt_agent/cli.py` → `src/aedt_agent/v0/cli.py`

- [ ] **Step 1：创建 v0 包根**

Create `src/aedt_agent/v0/__init__.py`:

```python
"""Preserved Stage A/B/C application namespace."""
```

- [ ] **Step 2：使用 Git 移动旧应用包**

Run:

```powershell
git mv src/aedt_agent/benchmark src/aedt_agent/v0/benchmark
git mv src/aedt_agent/chat src/aedt_agent/v0/chat
git mv src/aedt_agent/demo src/aedt_agent/v0/demo
git mv src/aedt_agent/evolution src/aedt_agent/v0/evolution
git mv src/aedt_agent/cli.py src/aedt_agent/v0/cli.py
```

Expected: `git status --short` 将这些文件识别为 rename，而不是删除后重新创建。

- [ ] **Step 3：更新 v0 包内部的自引用**

在移动后的文件中进行以下精确替换：

```text
aedt_agent.benchmark  -> aedt_agent.v0.benchmark
aedt_agent.chat       -> aedt_agent.v0.chat
aedt_agent.demo       -> aedt_agent.v0.demo
aedt_agent.evolution  -> aedt_agent.v0.evolution
```

只修改 `src/aedt_agent/v0/**/*.py`。不得替换以下共享包：

```text
aedt_agent.workflow
aedt_agent.nodes
aedt_agent.layout
aedt_agent.validation
aedt_agent.mcp
aedt_agent.knowledge
aedt_agent.reporting
```

修改后运行：

```powershell
rg -n "aedt_agent\.(benchmark|chat|demo|evolution)" src\aedt_agent\v0
```

Expected: 无输出。

- [ ] **Step 4：修改旧 CLI 的程序名和 v0 import**

In `src/aedt_agent/v0/cli.py`:

```python
from aedt_agent.v0.benchmark.config import load_benchmark_config
from aedt_agent.v0.benchmark.generator import create_generator_from_env
from aedt_agent.v0.benchmark.runner import run_offline_benchmark
from aedt_agent.knowledge.build_sqlite import build_api_semantics_db
```

并把：

```python
parser = argparse.ArgumentParser(prog="aedt-agent")
```

改为：

```python
parser = argparse.ArgumentParser(prog="aedt-agent-v0")
```

- [ ] **Step 5：验证 v0 模块可以直接导入**

Run:

```powershell
.\.venv\Scripts\python.exe -c "from aedt_agent.v0.benchmark.config import load_benchmark_config; from aedt_agent.v0.demo.service import DemoService; from aedt_agent.v0.evolution.models import NodeEvolutionProposal; print('v0-import-ok')"
```

Expected:

```text
v0-import-ok
```

- [ ] **Step 6：提交 v0 移动**

```powershell
git add src/aedt_agent/v0
git commit -m "refactor: archive legacy application under v0"
```

---

### Task 3：增加旧 import 兼容层

**Files:**
- Create: `src/aedt_agent/_compat.py`
- Create: `src/aedt_agent/benchmark/__init__.py`
- Create: `src/aedt_agent/chat/__init__.py`
- Create: `src/aedt_agent/demo/__init__.py`
- Create: `src/aedt_agent/evolution/__init__.py`

- [ ] **Step 1：实现集中式模块别名安装器**

Create `src/aedt_agent/_compat.py`:

```python
from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Iterable


def install_package_aliases(
    legacy_package: str,
    target_package: str,
    module_names: Iterable[str],
) -> ModuleType:
    """Map legacy submodule imports to the same v0 module objects."""
    legacy_module = sys.modules[legacy_package]
    target_module = import_module(target_package)

    for module_name in module_names:
        target_name = f"{target_package}.{module_name}"
        legacy_name = f"{legacy_package}.{module_name}"
        module = import_module(target_name)
        sys.modules[legacy_name] = module
        setattr(legacy_module, module_name, module)

    return target_module
```

- [ ] **Step 2：创建 benchmark 兼容包**

Create `src/aedt_agent/benchmark/__init__.py`:

```python
"""Compatibility imports for the preserved v0 benchmark package."""

from aedt_agent._compat import install_package_aliases

_target = install_package_aliases(
    __name__,
    "aedt_agent.v0.benchmark",
    [
        "aedt_executor",
        "config",
        "context_builder",
        "generator",
        "go_nogo",
        "graders",
        "harness_generator",
        "models",
        "node_plan_parser",
        "node_readiness",
        "official_retriever",
        "prompt_templates",
        "repair",
        "report_html",
        "report_html_stage_b",
        "report_html_v2",
        "runner",
        "runner_stage_b",
        "runner_v2",
        "semantic_lite",
        "stage_b_models",
        "stage_b_presentation",
        "stage_b_validation",
        "task_sets",
        "tool_usage",
        "v2_models",
    ],
)

__all__ = getattr(_target, "__all__", [])


def __getattr__(name: str):
    return getattr(_target, name)
```

- [ ] **Step 3：创建 chat 兼容包**

Create `src/aedt_agent/chat/__init__.py`:

```python
"""Compatibility imports for the preserved v0 chat package."""

from aedt_agent._compat import install_package_aliases

_target = install_package_aliases(
    __name__,
    "aedt_agent.v0.chat",
    ["repair_context", "workflow_planner"],
)

__all__ = getattr(_target, "__all__", [])


def __getattr__(name: str):
    return getattr(_target, name)
```

- [ ] **Step 4：创建 demo 兼容包**

Create `src/aedt_agent/demo/__init__.py`:

```python
"""Compatibility imports for the preserved v0 demo package."""

from aedt_agent._compat import install_package_aliases

_target = install_package_aliases(
    __name__,
    "aedt_agent.v0.demo",
    [
        "config",
        "import_cutout",
        "layout_ports",
        "planner",
        "planner_benchmark",
        "preflight",
        "service",
        "tuning",
        "web",
    ],
)

__all__ = getattr(_target, "__all__", [])


def __getattr__(name: str):
    return getattr(_target, name)
```

- [ ] **Step 5：创建 evolution 兼容包**

Create `src/aedt_agent/evolution/__init__.py`:

```python
"""Compatibility imports for the preserved v0 evolution package."""

from aedt_agent._compat import install_package_aliases

_target = install_package_aliases(
    __name__,
    "aedt_agent.v0.evolution",
    ["evaluator", "miner", "models", "policy", "proposer"],
)

__all__ = getattr(_target, "__all__", [])


def __getattr__(name: str):
    return getattr(_target, name)
```

- [ ] **Step 6：运行兼容身份测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_v0_namespace_compatibility.py -q
```

Expected: PASS。

- [ ] **Step 7：运行依赖这些旧 import 的代表测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_config.py `
  tests\test_chat_workflow_planner.py `
  tests\test_stage_c1_demo_config.py `
  tests\test_node_evolution.py -q
```

Expected: PASS。

- [ ] **Step 8：提交兼容层**

```powershell
git add src/aedt_agent/_compat.py src/aedt_agent/benchmark src/aedt_agent/chat src/aedt_agent/demo src/aedt_agent/evolution
git commit -m "feat: preserve legacy application imports"
```

---

### Task 4：建立 Agent、Domain、Infrastructure 新边界

**Files:**
- Create: `src/aedt_agent/agent/__init__.py`
- Create: `src/aedt_agent/agent/approvals/__init__.py`
- Create: `src/aedt_agent/agent/evaluation/__init__.py`
- Create: `src/aedt_agent/agent/mission/__init__.py`
- Create: `src/aedt_agent/agent/orchestrator/__init__.py`
- Create: `src/aedt_agent/agent/planning/__init__.py`
- Create: `src/aedt_agent/agent/policies/__init__.py`
- Create: `src/aedt_agent/agent/workers/__init__.py`
- Create: `src/aedt_agent/domain/__init__.py`
- Create: `src/aedt_agent/infrastructure/__init__.py`
- Modify: `src/aedt_agent/__init__.py`

- [ ] **Step 1：创建 Agent 包说明**

Create `src/aedt_agent/agent/__init__.py`:

```python
"""Goal-driven AEDT Agent product runtime."""
```

- [ ] **Step 2：创建 Agent 子边界**

Create the following files with exactly these module docstrings:

`src/aedt_agent/agent/mission/__init__.py`

```python
"""Mission state and lifecycle contracts."""
```

`src/aedt_agent/agent/orchestrator/__init__.py`

```python
"""Mission state transition and job dispatch orchestration."""
```

`src/aedt_agent/agent/planning/__init__.py`

```python
"""Mission planning and bounded replanning interfaces."""
```

`src/aedt_agent/agent/workers/__init__.py`

```python
"""Leaseable worker contracts and adapters."""
```

`src/aedt_agent/agent/evaluation/__init__.py`

```python
"""Deterministic evidence evaluation interfaces."""
```

`src/aedt_agent/agent/policies/__init__.py`

```python
"""Retry, recovery, rollback, and optimization policies."""
```

`src/aedt_agent/agent/approvals/__init__.py`

```python
"""Human approval request and resume contracts."""
```

- [ ] **Step 3：创建未来领域与基础设施边界**

Create `src/aedt_agent/domain/__init__.py`:

```python
"""Stable electromagnetic domain contracts shared by Agent capabilities."""
```

Create `src/aedt_agent/infrastructure/__init__.py`:

```python
"""Persistence, process, artifact, and AEDT infrastructure adapters."""
```

- [ ] **Step 4：更新根包定位**

Replace `src/aedt_agent/__init__.py` with:

```python
"""Goal-driven AEDT engineering agent with a preserved v0 application."""

__all__ = ["__version__"]
__version__ = "0.1.0"
```

- [ ] **Step 5：运行依赖方向测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 6：提交新边界**

```powershell
git add src/aedt_agent/agent src/aedt_agent/domain src/aedt_agent/infrastructure src/aedt_agent/__init__.py
git commit -m "feat: establish agent runtime package boundaries"
```

---

### Task 5：分离新旧 CLI

**Files:**
- Create: `src/aedt_agent/agent/cli.py`
- Create: `src/aedt_agent/cli.py`
- Modify: `src/aedt_agent/v0/cli.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_agent_cli_boundary.py`

- [ ] **Step 1：实现新 Agent CLI 命令边界**

Create `src/aedt_agent/agent/cli.py`:

```python
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any


MISSION_COMMANDS = ("create", "run", "status", "resume", "approve", "cancel")
RUNTIME_UNAVAILABLE_MESSAGE = "Mission Runtime 尚未安装；当前版本只完成 Agent-First 架构迁移。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aedt-agent")
    subparsers = parser.add_subparsers(dest="group", required=True)

    mission = subparsers.add_parser("mission", help="Manage persistent engineering missions.")
    mission_commands = mission.add_subparsers(dest="mission_command", required=True)

    for command in MISSION_COMMANDS:
        command_parser = mission_commands.add_parser(command)
        if command in {"status", "resume", "approve", "cancel"}:
            command_parser.add_argument("--mission-id", required=True)

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload: dict[str, Any] = {
        "command": f"{args.group}.{args.mission_command}",
        "message": RUNTIME_UNAVAILABLE_MESSAGE,
        "status": "runtime_unavailable",
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
```

这里明确返回非零状态，不伪装 Mission 已执行成功。下一阶段实现 Mission Runtime 时，用真实 command handlers 替换该边界。

- [ ] **Step 2：让根 CLI 模块转发到新 Agent CLI**

Create `src/aedt_agent/cli.py`:

```python
"""Default CLI compatibility module for the Agent product."""

from aedt_agent.agent.cli import build_parser, main, run

__all__ = ["build_parser", "main", "run"]
```

- [ ] **Step 3：更新项目 metadata 与 console scripts**

In `pyproject.toml`, change:

```toml
description = "Goal-driven Agent runtime for controlled Ansys AEDT engineering workflows"
```

Replace `[project.scripts]` with:

```toml
[project.scripts]
aedt-agent = "aedt_agent.agent.cli:main"
aedt-agent-v0 = "aedt_agent.v0.cli:main"
```

- [ ] **Step 4：运行 CLI 边界测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_cli_boundary.py -q
```

Expected: PASS。

- [ ] **Step 5：验证实际模块入口**

Run:

```powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent.cli mission status --mission-id mission-test
```

Expected: 输出 JSON，`status` 为 `runtime_unavailable`，进程退出码为 `2`。

Run:

```powershell
.\.venv\Scripts\python.exe -c "import sys; from aedt_agent.v0.cli import main; sys.argv=['aedt-agent-v0','--help']; main()"
```

Expected: 输出旧 CLI 帮助，包含 `build-db` 和 `run-benchmark`。

- [ ] **Step 6：提交 CLI 分流**

```powershell
git add src/aedt_agent/agent/cli.py src/aedt_agent/cli.py src/aedt_agent/v0/cli.py pyproject.toml tests/test_agent_cli_boundary.py
git commit -m "feat: split agent and v0 command line entrypoints"
```

---

### Task 6：验证旧脚本继续通过兼容路径工作

**Files:**
- Modify: `tests/test_stage_c_demo_scripts.py`
- Modify: `tests/test_run_stage_a_script.py`

- [ ] **Step 1：增加脚本使用旧 import 的兼容断言**

Append to `tests/test_stage_c_demo_scripts.py`:

```python
def test_stage_c_scripts_keep_legacy_import_paths_for_compatibility():
    from pathlib import Path

    script_paths = {
        "scripts/run_stage_c1_demo_server.py": "from aedt_agent.demo",
        "scripts/run_stage_c_import_cutout.py": "from aedt_agent.demo",
        "scripts/run_stage_c_brd_acceptance.py": "from aedt_agent.demo",
        "scripts/run_stage_c5_local_cut_build.py": "from aedt_agent.demo",
    }

    for path, expected_import in script_paths.items():
        assert expected_import in Path(path).read_text(encoding="utf-8")
```

该测试记录第一阶段的兼容承诺：旧脚本不需要与包迁移同时改写。后续若脚本整体迁入 `v0`，应在新的迁移计划中更新该断言。

- [ ] **Step 2：将动态 benchmark import 测试改为 v0 权威路径**

In `tests/test_run_stage_a_script.py`, replace both occurrences of:

```python
__import__("aedt_agent.benchmark.config", fromlist=["load_benchmark_config"])
```

with:

```python
__import__("aedt_agent.v0.benchmark.config", fromlist=["load_benchmark_config"])
```

旧 import 的兼容性由 `tests/test_v0_namespace_compatibility.py` 单独覆盖；直接测试旧应用内部时使用 v0 权威路径。

- [ ] **Step 3：运行脚本和 v0 应用代表测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_stage_c_demo_scripts.py `
  tests\test_run_stage_a_script.py `
  tests\test_runner.py::test_cli_run_benchmark_with_config `
  tests\test_stage_c1_demo_service.py `
  tests\test_stage_c5_local_cut_build.py -q
```

Expected: PASS。

- [ ] **Step 4：运行旧脚本 smoke**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\list_workflow_templates.py
.\.venv\Scripts\python.exe scripts\list_node_catalog.py
```

Expected: 第一条输出 workflow template IDs；第二条输出 node IDs。

- [ ] **Step 5：提交脚本兼容验证**

```powershell
git add tests/test_stage_c_demo_scripts.py tests/test_run_stage_a_script.py
git commit -m "test: preserve legacy script compatibility"
```

---

### Task 7：执行全量回归与迁移审计

**Files:**
- Modify only if verification finds migration-specific defects.

- [ ] **Step 1：检查旧应用目录没有残留产品代码**

Run:

```powershell
Get-ChildItem src\aedt_agent\benchmark,src\aedt_agent\chat,src\aedt_agent\demo,src\aedt_agent\evolution -Recurse
```

Expected: 每个兼容目录只包含 `__init__.py` 和 Python 生成的 `__pycache__`；不得存在迁移前的产品 `.py` 文件。

- [ ] **Step 2：检查 v0 内部没有回指旧命名空间**

Run:

```powershell
rg -n "aedt_agent\.(benchmark|chat|demo|evolution)" src\aedt_agent\v0
```

Expected: 无输出。

- [ ] **Step 3：检查 Agent 与共享包依赖方向**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_architecture_dependencies.py -q
```

Expected: PASS。

- [ ] **Step 4：运行迁移重点测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_v0_namespace_compatibility.py `
  tests\test_agent_cli_boundary.py `
  tests\test_architecture_dependencies.py `
  tests\test_config.py `
  tests\test_chat_workflow_planner.py `
  tests\test_stage_c1_demo_service.py `
  tests\test_node_evolution.py `
  tests\test_stage_c_demo_scripts.py -q
```

Expected: PASS。

- [ ] **Step 5：运行全量测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: 全量 PASS。

- [ ] **Step 6：检查 Git 变更范围**

Run:

```powershell
git status --short
git diff --check
git diff --stat HEAD~6..HEAD
```

Expected:

- 迁移相关变更只涉及 `src/aedt_agent`、`tests` 和 `pyproject.toml`；
- 用户原有的 `README.md`、RFC、截图脚本和未跟踪目录不被加入提交；
- `git diff --check` 无空白错误。

- [ ] **Step 7：提交迁移收口修复（仅在确有修复时）**

若验证发现并修复了迁移问题：

```powershell
git add src/aedt_agent tests pyproject.toml
git commit -m "fix: complete agent-first namespace migration"
```

若无修复，不创建空提交。

---

## 完成定义

本计划完成时必须同时满足：

1. `aedt_agent.v0` 是旧应用的唯一权威实现位置。
2. `aedt_agent.benchmark/chat/demo/evolution` 仅为兼容入口。
3. 旧 import 与对应 v0 import 返回同一个模块对象。
4. `aedt-agent` 指向新 Agent CLI。
5. `aedt-agent-v0` 保留旧 Benchmark CLI。
6. 旧脚本无需修改即可继续运行。
7. 共享领域包不依赖 `aedt_agent.agent`。
8. `aedt_agent.agent` 不依赖 `aedt_agent.v0`。
9. 全量测试通过。
10. 本阶段没有实现或伪造 Mission Runtime。

## 后续计划

命名空间迁移完成后，下一份独立实施计划应覆盖：

```text
Mission / Job / Event / Checkpoint / Approval contracts
    -> SQLite Store
    -> 状态机
    -> Worker Registry 与 lease
    -> BRD local-cut build Worker
    -> 审批后恢复
```

该计划必须以本次建立的 `aedt_agent.agent`、`aedt_agent.domain` 和 `aedt_agent.infrastructure` 边界为基础，不再把新行为加入 `aedt_agent.v0.demo.service`。
