from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def check_syntax(code: str) -> CheckResult:
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return CheckResult(False, [f"syntax_error: {exc.msg}"])
    return CheckResult(True, [])


def check_restricted_python(code: str) -> CheckResult:
    tree = ast.parse(code)
    violations: list[str] = []
    banned_imports = {"os", "subprocess", "socket", "shutil"}
    banned_calls = {"eval", "exec", "open", "__import__", "system", "popen"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in banned_imports:
                    violations.append(f"restricted_import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in banned_imports:
                violations.append(f"restricted_import: {node.module}")
        elif isinstance(node, ast.Call):
            func_name = _call_name(node.func)
            if func_name.split(".")[-1] in banned_calls:
                violations.append(f"restricted_call: {func_name}")
    return CheckResult(len(violations) == 0, violations)


def check_allowed_api_usage(code: str, allowed_apis: list[str]) -> CheckResult:
    tree = ast.parse(code)
    allowed_suffixes = {_normalize_api_name(api) for api in allowed_apis}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = _call_name(node.func)
            if not func_name.startswith("app."):
                continue
            suffix = _normalize_api_name(func_name[4:])
            if suffix not in allowed_suffixes:
                violations.append(f"api_not_allowed: {func_name}")
    return CheckResult(len(violations) == 0, violations)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _normalize_api_name(value: str) -> str:
    normalized = value.replace("Hfss.", "").replace("SetupHFSS.", "")
    if normalized.startswith("modeler."):
        return normalized
    return normalized
