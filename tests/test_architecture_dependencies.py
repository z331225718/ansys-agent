from __future__ import annotations

import ast
from importlib.util import resolve_name
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


def _module_name(path: Path) -> str:
    parts = path.resolve().parts
    source_index = max(index for index, part in enumerate(parts) if part.lower() == "src")
    module_parts = list(parts[source_index + 1 :])
    module_parts[-1] = Path(module_parts[-1]).stem
    if module_parts[-1] == "__init__":
        module_parts.pop()
    return ".".join(module_parts)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_name = _module_name(path)
    package_name = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported_module = resolve_name(
                    f"{'.' * node.level}{node.module or ''}",
                    package_name,
                )
            else:
                imported_module = node.module or ""

            if imported_module:
                names.append(imported_module)
            names.extend(
                f"{imported_module}.{alias.name}"
                for alias in node.names
                if imported_module and alias.name != "*"
            )
    return names


def test_imports_normalizes_imported_members(tmp_path):
    source_root = tmp_path / "src"
    module_path = source_root / "aedt_agent" / "workflow" / "sample.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        "from aedt_agent import agent, v0\n",
        encoding="utf-8",
    )

    assert set(_imports(module_path)) >= {
        "aedt_agent.agent",
        "aedt_agent.v0",
    }


def test_imports_resolves_package_relative_imports(tmp_path):
    source_root = tmp_path / "src"
    module_path = source_root / "aedt_agent" / "workflow" / "sample.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        "from .. import agent, v0\nfrom ..agent import cli\n",
        encoding="utf-8",
    )

    assert set(_imports(module_path)) >= {
        "aedt_agent.agent",
        "aedt_agent.v0",
        "aedt_agent.agent.cli",
    }


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
