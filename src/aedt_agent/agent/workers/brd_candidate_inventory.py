from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import ErrorClass, JobRecord
from aedt_agent.agent.workers.registry import (
    WorkerContext,
    WorkerReportedError,
)
from aedt_agent.infrastructure import (
    BrdCandidateInventoryAdapter,
    BrdCandidateInventoryRequest,
    RealAedtEnvironment,
)


BRD_CANDIDATE_INVENTORY_CAPABILITY = "brd.candidate_inventory.discover"


def build_brd_candidate_inventory_job_input(
    *,
    project_path: str | Path,
    loop_context: dict[str, Any],
    aedt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "project_path": str(project_path),
        "loop_context": dict(loop_context),
        "aedt": dict(aedt or {}),
    }


def run_brd_candidate_inventory_worker(
    job: JobRecord,
    context: WorkerContext,
    *,
    inventory_adapter: Any | None = None,
) -> dict[str, Any]:
    if not context.artifacts_dir:
        raise ValueError("brd.candidate_inventory.discover requires artifacts_dir")
    payload = dict(job.input_payload)
    loop_context = _loop_context(payload)
    seed_inventory = _seed_inventory(loop_context)
    aedt = dict(payload.get("aedt") or {})
    inventory_path = _inventory_output_path(loop_context)
    request = BrdCandidateInventoryRequest(
        project_path=Path(
            str(
                payload.get("project_path")
                or loop_context.get("latest_project_path")
                or loop_context.get("working_project_path")
            )
        ),
        artifact_dir=Path(context.artifacts_dir),
        seed_inventory=seed_inventory,
        inventory_output_path=inventory_path,
        signal_nets=_string_list(
            payload.get("signal_nets") or loop_context.get("signal_nets")
        ),
        reference_nets=_string_list(
            payload.get("reference_nets") or loop_context.get("reference_nets")
        ),
        geometry_constraints=dict(loop_context.get("geometry_constraints") or {}),
        environment=RealAedtEnvironment(
            version=str(aedt.get("version") or "2026.1"),
            non_graphical=bool(aedt.get("non_graphical", True)),
            edb_backend=str(aedt.get("edb_backend") or "auto"),
            cadence_launcher=str(aedt.get("cadence_launcher") or ""),
            ansysem_root=str(aedt.get("ansysem_root") or ""),
            awp_root=str(aedt.get("awp_root") or ""),
        ),
    )
    request.artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = (inventory_adapter or BrdCandidateInventoryAdapter()).run(request)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise WorkerReportedError(
            ErrorClass.INVALID_INPUT.value,
            str(exc),
            retryable=False,
            details={"error_type": type(exc).__name__},
        ) from exc

    loop_context["candidate_action_inventory"] = result.inventory
    loop_context["candidate_action_inventory_path"] = result.inventory_path
    loop_context["candidate_inventory_manifest"] = result.manifest_path
    loop_context["candidate_inventory_summary"] = dict(result.summary)
    refs = [result.inventory_path, result.manifest_path]
    return {
        **payload,
        "status": "succeeded",
        "project_path": str(request.project_path),
        "candidate_action_inventory_path": result.inventory_path,
        "candidate_inventory_manifest": result.manifest_path,
        "loop_context": loop_context,
        "evidence_summary": {
            **result.summary,
            "raw_project": "artifact_only",
            "artifact_refs": refs,
        },
        "artifact_refs": refs,
    }


def _loop_context(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _seed_inventory(loop_context: dict[str, Any]) -> dict[str, Any]:
    value = loop_context.get("candidate_action_inventory")
    if isinstance(value, dict):
        seed = dict(value)
    else:
        seed = {}
    inventory_path = _inventory_output_path(loop_context)
    if inventory_path and inventory_path.is_file():
        loaded = json.loads(inventory_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            nested = loaded.get("candidate_action_inventory")
            seed.update(dict(nested if isinstance(nested, dict) else loaded))
    return seed


def _inventory_output_path(loop_context: dict[str, Any]) -> Path | None:
    raw = str(
        loop_context.get("candidate_action_inventory_path")
        or loop_context.get("candidate_action_inventory_file")
        or ""
    ).strip()
    return Path(raw) if raw else None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in list(value or []) if str(item).strip()]
