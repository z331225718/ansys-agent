from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from aedt_agent.pi_agent.case_config import PiAgentCase, PiAgentCaseError


def initialize_local_case(
    case: PiAgentCase,
    *,
    target_case: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if case.source_path is None:
        raise PiAgentCaseError("case source_path is required for init")
    target_case_path = _target_local_path(case.source_path, target_case)
    target_loop = _local_config_path(case.loop_config)
    target_profile = _local_config_path(case.execution_profile)

    copied = [
        _copy_once(case.loop_config, target_loop, force=force),
        _copy_once(case.execution_profile, target_profile, force=force),
        _copy_case(case.source_path, target_case_path, target_loop, target_profile, force=force),
    ]
    return {
        "status": "initialized",
        "case_id": case.case_id,
        "files": copied,
        "next_commands": {
            "preflight": (
                ".\\.venv\\Scripts\\python.exe -m aedt_agent.pi_agent "
                f"preflight --case {target_case_path}"
            ),
            "run": (
                ".\\.venv\\Scripts\\python.exe -m aedt_agent.pi_agent "
                f"run --case {target_case_path}"
            ),
            "status": (
                ".\\.venv\\Scripts\\python.exe -m aedt_agent.pi_agent "
                f"status --case {target_case_path}"
            ),
        },
    }


def _target_local_path(source: Path, explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve(strict=False)
    return _local_config_path(source)


def _local_config_path(path: Path) -> Path:
    name = path.name
    if ".example." in name:
        name = name.replace(".example.", ".local.")
    elif name.endswith(".example.json"):
        name = name.removesuffix(".example.json") + ".local.json"
    elif name.endswith(".json") and not name.endswith(".local.json"):
        name = name.removesuffix(".json") + ".local.json"
    return path.with_name(name)


def _copy_once(source: Path, target: Path, *, force: bool) -> dict[str, str]:
    if not source.is_file():
        raise PiAgentCaseError(f"source file does not exist: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        return {
            "source": str(source),
            "target": str(target),
            "status": "exists",
        }
    shutil.copy2(source, target)
    return {
        "source": str(source),
        "target": str(target),
        "status": "copied" if not target.exists() else "written",
    }


def _copy_case(
    source: Path,
    target: Path,
    target_loop: Path,
    target_profile: Path,
    *,
    force: bool,
) -> dict[str, str]:
    if target.exists() and not force:
        return {
            "source": str(source),
            "target": str(target),
            "status": "exists",
        }
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PiAgentCaseError(f"{source} must contain a JSON object")
    payload["loop_config"] = _relative_or_absolute(target_loop)
    payload["execution_profile"] = _relative_or_absolute(target_profile)
    payload["check_paths"] = True
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "source": str(source),
        "target": str(target),
        "status": "written",
    }


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve(strict=False).relative_to(Path.cwd().resolve(strict=False)))
    except ValueError:
        return str(path)
