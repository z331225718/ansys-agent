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
    return {
        "status": "succeeded",
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
        "evidence_summary": {
            "status": "solve_completed",
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
            "artifact_refs": refs,
        },
        "artifact_refs": refs,
    }
