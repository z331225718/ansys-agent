from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext
from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region
from aedt_agent.layout.workflow_run import import_cutout_summary_to_workflow_run


BRD_LOCAL_CUT_BUILD_CAPABILITY = "brd.local_cut.build"


def build_brd_local_cut_job_input(
    *,
    layout_file: str | Path,
    signal_nets: list[str],
    reference_nets: list[str],
    local_cut_region: dict[str, Any] | None,
    artifact_dir: str | Path,
    target_metrics: list[dict[str, Any]] | None = None,
    port_candidates: dict[str, Any] | None = None,
    approved_port_selection: dict[str, Any] | None = None,
    adapter_mode: str = "deterministic",
    stackup_xml: str | Path | None = None,
    recorded_layout_settings: dict[str, Any] | None = None,
    uniform_line_port_hint: dict[str, Any] | None = None,
    aedt: dict[str, Any] | None = None,
    solve_enabled: bool = False,
) -> dict[str, Any]:
    return {
        "adapter_mode": adapter_mode,
        "layout_file": str(layout_file),
        "signal_nets": list(signal_nets),
        "reference_nets": list(reference_nets),
        "local_cut_region": local_cut_region,
        "artifact_dir": str(artifact_dir),
        "target_metrics": list(target_metrics or []),
        "port_candidates": port_candidates or {"status": "ready", "recommended_endpoints": []},
        "approved_port_selection": approved_port_selection or {},
        "stackup_xml": str(stackup_xml) if stackup_xml else "",
        "recorded_layout_settings": dict(recorded_layout_settings or {}),
        "uniform_line_port_hint": dict(uniform_line_port_hint or {}),
        "aedt": dict(aedt or {}),
        "solve_enabled": bool(solve_enabled),
    }


def run_brd_local_cut_worker(
    job: JobRecord,
    context: WorkerContext,
    *,
    real_build_adapter: Any | None = None,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    adapter_mode = str(payload.get("adapter_mode", "deterministic"))
    if adapter_mode not in {"deterministic", "real_build"}:
        raise ValueError(f"unsupported adapter_mode: {adapter_mode}")
    region = parse_local_cut_region(payload.get("local_cut_region"))
    artifact_dir = Path(str(payload["artifact_dir"]))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if adapter_mode == "real_build":
        summary = _real_build_summary(payload, region, real_build_adapter)
        approval_required = _approval_required(dict(summary.get("port_candidates") or {}))
    else:
        port_candidates = dict(payload.get("port_candidates") or {})
        approval_required = _approval_required(port_candidates)
        summary = _summary_payload(job, context, payload, region, approval_required)

    summary_path = artifact_dir / "brd_local_cut_summary.json"
    workflow_path = artifact_dir / "workflow_run.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    import_cutout_summary_to_workflow_run(summary).write_json(workflow_path)

    output = {
        "status": "waiting_approval" if approval_required else "model_review",
        "artifact_refs": [str(summary_path), str(workflow_path)],
        "summary_path": str(summary_path),
        "workflow_run_path": str(workflow_path),
        "evidence_summary": _bounded_evidence_summary(summary),
    }
    if approval_required:
        output["approval_required"] = approval_required
    return output


def _approval_required(port_candidates: dict[str, Any]) -> dict[str, Any] | None:
    if port_candidates.get("status") not in {"ambiguous", "needs_user_hint"}:
        return None
    options = list(port_candidates.get("candidates") or port_candidates.get("recommended_endpoints") or [])
    return {"reason": "port_candidates_ambiguous", "options": options}


def _real_build_summary(payload: dict[str, Any], region: dict[str, Any], adapter: Any | None) -> dict[str, Any]:
    from aedt_agent.infrastructure import BrdRealBuildAdapter, BrdRealBuildRequest, RealAedtEnvironment

    if payload.get("solve_enabled"):
        raise ValueError("solve_enabled is not supported by brd.local_cut.build real_build")
    aedt = dict(payload.get("aedt") or {})
    request = BrdRealBuildRequest(
        layout_file=Path(str(payload["layout_file"])),
        artifact_dir=Path(str(payload["artifact_dir"])),
        signal_nets=list(payload.get("signal_nets") or []),
        reference_nets=list(payload.get("reference_nets") or []),
        local_cut_region=region,
        stackup_xml=Path(str(payload["stackup_xml"])) if payload.get("stackup_xml") else None,
        recorded_layout_settings=dict(payload.get("recorded_layout_settings") or {}),
        uniform_line_port_hint=dict(payload.get("uniform_line_port_hint") or {}),
        target_metrics=list(payload.get("target_metrics") or []),
        approved_port_selection=dict(payload.get("approved_port_selection") or {}),
        solve_enabled=False,
        environment=RealAedtEnvironment(
            version=str(aedt.get("version") or "2026.1"),
            non_graphical=bool(aedt.get("non_graphical", False)),
            edb_backend=str(aedt.get("edb_backend") or "auto"),
            cadence_launcher=str(aedt.get("cadence_launcher") or ""),
            ansysem_root=str(aedt.get("ansysem_root") or ""),
            awp_root=str(aedt.get("awp_root") or ""),
        ),
    )
    runner = adapter or BrdRealBuildAdapter()
    return dict(runner.run(request).summary)


def _summary_payload(
    job: JobRecord,
    context: WorkerContext,
    payload: dict[str, Any],
    region: dict[str, Any],
    approval_required: dict[str, Any] | None,
) -> dict[str, Any]:
    artifact_dir = Path(str(payload["artifact_dir"]))
    status = "waiting_approval" if approval_required else "succeeded"
    return {
        "status": status,
        "adapter": "agent_brd_local_cut",
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "worker_id": context.worker_id,
        "layout_file": str(payload["layout_file"]),
        "signal_nets": list(payload.get("signal_nets") or []),
        "reference_nets": list(payload.get("reference_nets") or []),
        "local_cut_region": region,
        "local_cut_polygon": bbox_to_polygon(region),
        "port_candidates": dict(payload.get("port_candidates") or {}),
        "approved_port_selection": dict(payload.get("approved_port_selection") or {}),
        "target_metrics": list(payload.get("target_metrics") or []),
        "edb_path": str(artifact_dir / "local_cut.aedb"),
        "aedt_project": str(artifact_dir / "local_cut.aedt"),
        "touchstone": str(artifact_dir / "model_review.s2p"),
        "tdr": str(artifact_dir / "model_review_tdr.csv"),
        "layout_solve": {"status": "skipped", "reason": "model_review_only"},
        "steps": _steps(status),
    }


def _steps(status: str) -> list[dict[str, Any]]:
    return [
        {"id": "import_layout_file", "label": "Record BRD file", "status": "succeeded"},
        {"id": "select_layout_nets", "label": "Record target nets", "status": "succeeded"},
        {"id": "create_layout_cutout", "label": "Record local cut bbox", "status": "succeeded"},
        {"id": "locate_layout_port_candidates", "label": "Evaluate port candidates", "status": status},
    ]


def _bounded_evidence_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": summary.get("status", ""),
        "adapter": summary.get("adapter", ""),
        "layout_file": summary.get("layout_file", ""),
        "signal_nets": summary.get("signal_nets", []),
        "reference_nets": summary.get("reference_nets", []),
        "local_cut_region": summary.get("local_cut_region", {}),
        "port_candidate_status": summary.get("port_candidates", {}).get("status", "unknown"),
        "port_execution_status": summary.get("port_execution", {}).get("status", "unknown"),
        "setup_name": summary.get("layout_setup", {}).get("setup_name", ""),
        "target_metrics": summary.get("target_metrics", []),
        "edb_path": summary.get("edb_path", ""),
        "aedt_project": summary.get("aedt_project", ""),
        "touchstone": summary.get("touchstone", ""),
        "tdr": summary.get("tdr", ""),
        "raw_sparameters": "artifact_only",
        "raw_tdr": "artifact_only",
    }
