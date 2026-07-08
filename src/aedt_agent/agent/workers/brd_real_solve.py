from __future__ import annotations

from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import ErrorClass, JobRecord
from aedt_agent.agent.workers.registry import (
    WorkerContext,
    WorkerReportedError,
)
from aedt_agent.infrastructure import (
    ArtifactExportError,
    ArtifactValidationError,
    BrdRealSolveAdapter,
    BrdRealSolveRequest,
    RealAedtEnvironment,
)


BRD_REAL_SOLVE_CAPABILITY = "brd.local_cut.solve"


def build_brd_real_solve_job_input(
    *,
    project_path: str | Path,
    setup_name: str,
    sweep_name: str,
    tdr_expression: str,
    expected_port_count: int,
    frequency_start_ghz: float = 0.0,
    frequency_stop_ghz: float = 67.0,
    rl_target_db: float = -20.0,
    tdr_target_ohm: float = 100.0,
    touchstone_name: str = "channel.s2p",
    tdr_report_name: str = "ChannelTDR",
    run_analyze: bool = True,
    export_tdr: bool = True,
    tdr_differential_pairs: bool = False,
    tdr_observation_port: str = "",
    tdr_reference_impedance_ohm: float | None = None,
    sparameter_mode: str = "auto",
    project_copy_mode: str = "checkpoint_copy",
    aedt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "project_path": str(project_path),
        "setup_name": str(setup_name),
        "sweep_name": str(sweep_name),
        "solution_name": f"{setup_name} : {sweep_name}",
        "touchstone_name": touchstone_name,
        "tdr_report_name": tdr_report_name,
        "tdr_expression": str(tdr_expression),
        "expected_port_count": int(expected_port_count),
        "frequency_start_ghz": float(frequency_start_ghz),
        "frequency_stop_ghz": float(frequency_stop_ghz),
        "rl_target_db": float(rl_target_db),
        "tdr_target_ohm": float(tdr_target_ohm),
        "run_analyze": bool(run_analyze),
        "export_tdr": bool(export_tdr),
        "tdr_differential_pairs": bool(tdr_differential_pairs),
        "tdr_observation_port": str(tdr_observation_port),
        "tdr_reference_impedance_ohm": (
            float(tdr_reference_impedance_ohm)
            if tdr_reference_impedance_ohm is not None
            else float(tdr_target_ohm)
        ),
        "sparameter_mode": str(sparameter_mode),
        "project_copy_mode": str(project_copy_mode),
        "aedt": dict(aedt or {}),
        "approval_reason": "approve_real_brd_solve",
        "approval_options": [
            {
                "id": "approve",
                "label": "批准真实 BRD local-cut 求解",
            },
            {"id": "reject", "label": "拒绝真实求解"},
        ],
    }


def run_brd_real_solve_worker(
    job: JobRecord,
    context: WorkerContext,
    *,
    solve_adapter: Any | None = None,
) -> dict[str, Any]:
    if not context.artifacts_dir:
        raise ValueError(
            "brd.local_cut.solve requires process harness artifacts_dir"
        )
    payload = dict(job.input_payload)
    aedt = dict(payload.get("aedt") or {})
    request = BrdRealSolveRequest(
        project_path=Path(str(payload["project_path"])),
        artifact_dir=Path(context.artifacts_dir),
        setup_name=str(payload["setup_name"]),
        sweep_name=str(payload["sweep_name"]),
        solution_name=str(payload["solution_name"]),
        touchstone_name=str(
            payload.get("touchstone_name") or "channel.s2p"
        ),
        tdr_report_name=str(
            payload.get("tdr_report_name") or "ChannelTDR"
        ),
        tdr_expression=str(payload["tdr_expression"]),
        expected_port_count=int(payload["expected_port_count"]),
        environment=RealAedtEnvironment(
            version=str(aedt.get("version") or "2026.1"),
            non_graphical=bool(
                aedt.get("non_graphical", True)
            ),
            edb_backend=str(aedt.get("edb_backend") or "auto"),
            cadence_launcher=str(
                aedt.get("cadence_launcher") or ""
            ),
            ansysem_root=str(aedt.get("ansysem_root") or ""),
            awp_root=str(aedt.get("awp_root") or ""),
        ),
        run_analyze=bool(payload.get("run_analyze", True)),
        export_tdr=bool(payload.get("export_tdr", True)),
        tdr_differential_pairs=bool(
            payload.get("tdr_differential_pairs", False)
        ),
        tdr_observation_port=str(payload.get("tdr_observation_port") or ""),
        tdr_reference_impedance_ohm=float(
            payload.get(
                "tdr_reference_impedance_ohm",
                payload.get("tdr_target_ohm", 100.0),
            )
        ),
        project_copy_mode=str(
            payload.get("project_copy_mode") or "checkpoint_copy"
        ),
    )
    try:
        result = (solve_adapter or BrdRealSolveAdapter()).run(
            request
        )
    except ArtifactExportError as exc:
        raise WorkerReportedError(
            ErrorClass.ARTIFACT_MISSING.value,
            str(exc),
            retryable=False,
            details={"error_type": type(exc).__name__},
        ) from exc
    except ArtifactValidationError as exc:
        raise WorkerReportedError(
            ErrorClass.ARTIFACT_INVALID.value,
            str(exc),
            retryable=False,
            details={"error_type": type(exc).__name__},
        ) from exc
    except ValueError as exc:
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
        result.project_checkpoint,
        result.solved_project,
        result.touchstone_path,
        result.tdr_path,
        result.solve_manifest_path,
    ]
    loop_context = _loop_context(payload)
    _append_unique(loop_context, "solve_manifest_paths", result.solve_manifest_path)
    loop_context["latest_project_path"] = result.solved_project
    loop_context["last_solve_manifest_path"] = result.solve_manifest_path
    loop_context["last_touchstone_path"] = result.touchstone_path
    loop_context["last_tdr_path"] = result.tdr_path
    return {
        "status": "succeeded",
        "project_path": result.solved_project,
        "source_project_path": str(payload.get("project_path") or ""),
        "project_checkpoint": result.project_checkpoint,
        "solved_project": result.solved_project,
        "solve_summary": {
            **result.summary,
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
        },
        "touchstone_path": result.touchstone_path,
        "tdr_path": result.tdr_path,
        "solve_manifest": result.solve_manifest_path,
        "artifact_dir": str(
            Path(result.solve_manifest_path).parent
        ),
        "frequency_start_ghz": float(
            payload.get("frequency_start_ghz", 0.0)
        ),
        "frequency_stop_ghz": float(
            payload.get("frequency_stop_ghz", 67.0)
        ),
        "rl_target_db": float(
            payload.get("rl_target_db", -20.0)
        ),
        "tdr_target_ohm": float(
            payload.get("tdr_target_ohm", 100.0)
        ),
        "tdr_reference_impedance_ohm": float(
            payload.get(
                "tdr_reference_impedance_ohm",
                payload.get("tdr_target_ohm", 100.0),
            )
        ),
        "tdr_observation_port": str(
            payload.get("tdr_observation_port") or ""
        ),
        "sparameter_mode": str(payload.get("sparameter_mode") or "auto"),
        "loop_context": loop_context,
        "evidence_summary": {
            "status": "solve_completed",
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
            "tdr_observation_port": str(
                payload.get("tdr_observation_port") or ""
            ),
            "tdr_reference_impedance_ohm": float(
                payload.get(
                    "tdr_reference_impedance_ohm",
                    payload.get("tdr_target_ohm", 100.0),
                )
            ),
            "sparameter_mode": str(payload.get("sparameter_mode") or "auto"),
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
