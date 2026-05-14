from __future__ import annotations

import ast

from aedt_agent.mcp.types import GuardResult


class AstGuard:
    forbidden_imports = {"os", "sys", "subprocess", "socket", "shutil", "pathlib"}
    forbidden_names = {"eval", "exec", "compile", "__import__", "open", "input"}
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
        return GuardResult(passed=not violations, violations=sorted(set(violations)))

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            if parent:
                return f"{parent}.{node.attr}"
            return node.attr
        if isinstance(node, ast.Call):
            return self._call_name(node.func)
        return ""
