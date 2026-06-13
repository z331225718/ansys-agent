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
