from __future__ import annotations

from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import ErrorClass, JobRecord
from aedt_agent.agent.workers.registry import (
    WorkerContext,
    WorkerReportedError,
)
from aedt_agent.infrastructure import (
    BrdModelEditAdapter,
    BrdModelEditRequest,
    RealAedtEnvironment,
)


BRD_MODEL_EDIT_CAPABILITY = "brd.model.edit"


def build_brd_model_edit_job_input(
    *,
    project_path: str | Path,
    actions: list[dict[str, Any]],
    edited_project_name: str = "",
    project_copy_mode: str = "checkpoint_copy",
    aedt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "project_path": str(project_path),
        "actions": list(actions),
        "edited_project_name": edited_project_name,
        "project_copy_mode": project_copy_mode,
        "aedt": dict(aedt or {}),
    }


def run_brd_model_edit_worker(
    job: JobRecord,
    context: WorkerContext,
    *,
    edit_adapter: Any | None = None,
) -> dict[str, Any]:
    if not context.artifacts_dir:
        raise ValueError("brd.model.edit requires process harness artifacts_dir")
    payload = dict(job.input_payload)
    aedt = dict(payload.get("aedt") or {})
    request = BrdModelEditRequest(
        project_path=Path(str(payload["project_path"])),
        artifact_dir=Path(context.artifacts_dir),
        actions=list(payload.get("actions") or []),
        edited_project_name=str(payload.get("edited_project_name") or ""),
        project_copy_mode=str(
            payload.get("project_copy_mode") or "checkpoint_copy"
        ),
        environment=RealAedtEnvironment(
            version=str(aedt.get("version") or "2026.1"),
            non_graphical=bool(aedt.get("non_graphical", True)),
            edb_backend=str(aedt.get("edb_backend") or "auto"),
            cadence_launcher=str(aedt.get("cadence_launcher") or ""),
            ansysem_root=str(aedt.get("ansysem_root") or ""),
            awp_root=str(aedt.get("awp_root") or ""),
        ),
    )
    try:
        result = (edit_adapter or BrdModelEditAdapter()).run(request)
    except (FileNotFoundError, ValueError) as exc:
        raise WorkerReportedError(
            ErrorClass.INVALID_INPUT.value,
            str(exc),
            retryable=False,
            details={"error_type": type(exc).__name__},
        ) from exc
    except Exception as exc:
        lowered = str(exc).lower()
        if "license" in lowered and (
            "unavailable" in lowered or "denied" in lowered
        ):
            raise WorkerReportedError(
                ErrorClass.LICENSE_UNAVAILABLE.value,
                str(exc),
                retryable=True,
                details={"error_type": type(exc).__name__},
            ) from exc
        raise

    refs = [
        result.edited_project_path,
        result.edited_edb_path,
        result.manifest_path,
    ]
    loop_context = _loop_context(payload)
    _append_unique(
        loop_context,
        "model_edit_manifest_paths",
        result.manifest_path,
    )
    loop_context["latest_project_path"] = result.edited_project_path
    loop_context["last_model_edit_manifest_path"] = result.manifest_path
    return {
        "status": "succeeded",
        "edited_project_path": result.edited_project_path,
        "edited_edb_path": result.edited_edb_path,
        "model_edit_manifest": result.manifest_path,
        "artifact_dir": str(Path(result.manifest_path).parent),
        "project_path": result.edited_project_path,
        "loop_context": loop_context,
        "edit_summary": {
            **result.summary,
            "raw_project": "artifact_only",
        },
        "evidence_summary": {
            "status": "model_edit_completed",
            "action_count": result.summary.get("action_count", 0),
            "change_count": result.summary.get("change_count", 0),
            "raw_project": "artifact_only",
            "artifact_refs": refs,
        },
        "artifact_refs": refs,
    }


def _loop_context(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _append_unique(payload: dict[str, Any], key: str, value: str) -> None:
    values = list(payload.get(key) or [])
    if value and value not in values:
        values.append(value)
    payload[key] = values
