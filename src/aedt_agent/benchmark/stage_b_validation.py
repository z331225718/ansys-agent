from __future__ import annotations

import importlib.util
import inspect
import traceback
from pathlib import Path
from typing import Any


def run_stage_b_validation(
    validation_script: Path,
    session_id: str,
    project_id: str,
    design_id: str,
    model_info: dict[str, Any],
    expected_outputs: list[str],
) -> dict[str, Any]:
    try:
        module = _load_validation_module(validation_script)
        validate = getattr(module, "validate")
        result = _call_validate(
            validate=validate,
            session_id=session_id,
            project_id=project_id,
            design_id=design_id,
            model_info=model_info,
            expected_outputs=expected_outputs,
        )
        if isinstance(result, dict):
            passed = bool(result.get("passed"))
            return {"passed": passed, **result}
        return {"passed": bool(result), "checks": ["validation_returned_bool"]}
    except Exception:
        return {
            "passed": False,
            "checks": [],
            "failure_type": "validation_error",
            "log": traceback.format_exc(),
        }


def _load_validation_module(path: Path):
    spec = importlib.util.spec_from_file_location("stage_b_task_validation", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load validation script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_validate(
    validate,
    session_id: str,
    project_id: str,
    design_id: str,
    model_info: dict[str, Any],
    expected_outputs: list[str],
) -> Any:
    signature = inspect.signature(validate)
    kwargs: dict[str, Any] = {}
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    if accepts_kwargs or "model_info" in signature.parameters:
        kwargs["model_info"] = model_info
    if accepts_kwargs or "expected_outputs" in signature.parameters:
        kwargs["expected_outputs"] = expected_outputs
    return validate(session_id, project_id, design_id, **kwargs)
