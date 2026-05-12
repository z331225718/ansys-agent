from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Protocol


class AttemptExecutor(Protocol):
    def execute(self, code_path: Path, validation_script: Path, work_dir: Path) -> dict: ...


class AEDTSubprocessExecutor:
    def __init__(
        self,
        python_executable: str | None = None,
        timeout: int = 900,
        version: str = "2026.1",
        non_graphical: bool = True,
        ansysem_root: str = "",
        awp_root: str = "",
        subprocess_runner: Callable = subprocess.run,
    ) -> None:
        self.python_executable = python_executable or sys.executable
        self.timeout = timeout
        self.version = version
        self.non_graphical = non_graphical
        self.ansysem_root = ansysem_root
        self.awp_root = awp_root
        self.subprocess_runner = subprocess_runner

    def execute(self, code_path: Path, validation_script: Path, work_dir: Path) -> dict:
        work_dir = work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        wrapper = work_dir / "aedt_attempt_wrapper.py"
        wrapper.write_text(
            _build_wrapper(code_path.resolve(), validation_script.resolve(), self.version, self.non_graphical),
            encoding="utf-8",
        )
        env = dict(os.environ)
        if self.ansysem_root:
            env["ANSYSEM_ROOT261"] = self.ansysem_root
            env["PATH"] = f"{self.ansysem_root}{os.pathsep}{env.get('PATH', '')}"
        if self.awp_root:
            env["AWP_ROOT261"] = self.awp_root
        result = self.subprocess_runner(
            [self.python_executable, str(wrapper.resolve())],
            timeout=self.timeout,
            cwd=work_dir,
            capture_output=True,
            text=True,
            env=env,
        )
        combined_log = "\n".join(part for part in [getattr(result, "stdout", ""), getattr(result, "stderr", "")] if part)
        parsed = _parse_last_json_line(getattr(result, "stdout", "") or "")
        if parsed is None:
            parsed = {
                "execution_ok": False,
                "validation_ok": False,
                "failure_type": "runtime_error",
                "log": combined_log,
            }
        if getattr(result, "returncode", 1) != 0 and parsed.get("execution_ok", True):
            parsed["execution_ok"] = False
            parsed["validation_ok"] = False
            parsed["failure_type"] = parsed.get("failure_type") or "runtime_error"
        parsed.setdefault("log", combined_log)
        parsed.setdefault("failure_type", "" if parsed.get("validation_ok") else "validation_fail")
        return parsed


def _build_wrapper(code_path: Path, validation_script: Path, version: str, non_graphical: bool) -> str:
    return f'''from __future__ import annotations

import importlib.util
import json
import traceback
from pathlib import Path

code_path = Path({str(code_path)!r})
validation_script = Path({str(validation_script)!r})
app = None

try:
    from ansys.aedt.core import Hfss
    app = Hfss(version={version!r}, non_graphical={non_graphical!r}, new_desktop=True)
    namespace = {{"app": app}}
    exec(compile(code_path.read_text(encoding="utf-8"), str(code_path), "exec"), namespace)

    spec = importlib.util.spec_from_file_location("task_validation", validation_script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    validation_result = module.validate(
        "local-aedt",
        getattr(app, "project_name", ""),
        getattr(app, "design_name", ""),
    )
    validation_ok = bool(validation_result.get("passed")) if isinstance(validation_result, dict) else bool(validation_result)
    print(json.dumps({{
        "execution_ok": True,
        "validation_ok": validation_ok,
        "validation_result": validation_result,
        "failure_type": "" if validation_ok else "validation_fail",
        "log": "",
    }}))
except Exception:
    print(json.dumps({{
        "execution_ok": False,
        "validation_ok": False,
        "failure_type": "runtime_error",
        "log": traceback.format_exc(),
    }}))
finally:
    if app is not None:
        try:
            app.release_desktop(close_projects=True, close_desktop=True)
        except Exception:
            pass
'''


def _parse_last_json_line(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None
