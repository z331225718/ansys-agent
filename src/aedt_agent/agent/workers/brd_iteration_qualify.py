from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aedt_agent.agent.mission import ErrorClass, JobRecord
from aedt_agent.agent.workers.registry import (
    WorkerContext,
    WorkerReportedError,
)


BRD_ITERATION_QUALIFY_CAPABILITY = "brd.iteration.qualify"


def build_brd_iteration_qualify_job_input(
    *,
    score: dict[str, Any],
    evidence_summary: dict[str, Any],
    touchstone_path: str | Path,
    tdr_path: str | Path,
    evidence_artifact: str | Path,
    artifact_refs: list[str] | None = None,
    loop_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "score": dict(score),
        "evidence_summary": dict(evidence_summary),
        "touchstone_path": str(touchstone_path),
        "tdr_path": str(tdr_path),
        "evidence_artifact": str(evidence_artifact),
        "artifact_refs": list(artifact_refs or []),
        "loop_context": dict(loop_context or {}),
    }


def run_brd_iteration_qualify_worker(
    job: JobRecord,
    context: WorkerContext,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    score = dict(payload.get("score") or {})
    evidence = dict(payload.get("evidence_summary") or {})
    if not score or not evidence:
        raise WorkerReportedError(
            ErrorClass.INVALID_INPUT.value,
            "brd.iteration.qualify requires score and evidence_summary",
            retryable=False,
        )

    checks = _qualification_checks(payload, score, evidence)
    blocking = [check for check in checks if check["status"] != "passed"]
    status = "approval_required" if blocking else "succeeded"

    loop_context = _loop_context(payload)
    manifest_path = _manifest_path(context, payload, loop_context)
    qualification = {
        "status": status,
        "check_count": len(checks),
        "blocking_count": len(blocking),
        "checks": checks,
        "raw_sparameters": "artifact_only",
        "raw_tdr": "artifact_only",
    }
    manifest = {
        "version": 1,
        "capability": BRD_ITERATION_QUALIFY_CAPABILITY,
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "summary": qualification,
    }
    _write_json(manifest_path, manifest)
    _append_unique(
        loop_context,
        "iteration_qualification_manifest_paths",
        str(manifest_path),
    )
    loop_context["last_iteration_qualification_manifest_path"] = str(manifest_path)
    loop_context["last_iteration_qualification_status"] = status

    qualified_evidence = {
        **evidence,
        "iteration_qualification_status": status,
        "iteration_qualification_manifest": str(manifest_path),
        "iteration_qualification_checks": [
            {
                "id": check["id"],
                "status": check["status"],
                "message": check["message"],
            }
            for check in checks
        ],
    }
    artifact_refs = _unique(
        [
            *list(payload.get("artifact_refs") or []),
            *list(evidence.get("artifact_refs") or []),
            str(manifest_path),
        ]
    )
    output = {
        **payload,
        "status": status,
        "score": score,
        "evidence_summary": qualified_evidence,
        "iteration_qualification": qualification,
        "iteration_qualification_manifest": str(manifest_path),
        "loop_context": loop_context,
        "artifact_refs": artifact_refs,
    }
    if blocking:
        approval_reason = _approval_reason(blocking)
        output["edge_outcome"] = "approval_required"
        output["approval_reason"] = approval_reason
        output["approval_options"] = [
            {"id": "approve", "label": "Continue anyway"},
            {"id": "reject", "label": "Stop optimization"},
        ]
        output["approval_required"] = {
            "reason": approval_reason,
            "issues": blocking,
            "options": output["approval_options"],
        }
    return output


def _qualification_checks(
    payload: Mapping[str, Any],
    score: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _check(
        checks,
        "score_status_present",
        bool(score.get("status") or evidence.get("status")),
        "score status is present",
        "score status is missing",
    )
    _check(
        checks,
        "raw_trace_policy",
        evidence.get("raw_sparameters") == "artifact_only"
        and evidence.get("raw_tdr") == "artifact_only",
        "raw S-parameter and TDR arrays are artifact-only",
        "raw S-parameter/TDR policy is not artifact-only",
    )
    _check(
        checks,
        "bounded_payload_size",
        not _contains_unbounded_list(payload),
        "handoff payload contains no unbounded inline arrays",
        "handoff payload contains an oversized inline list",
    )
    _check(
        checks,
        "differential_touchstone_contract",
        (
            str(score.get("touchstone_kind") or evidence.get("touchstone_kind"))
            .casefold()
            == "s4p"
            and str(score.get("sparameter_mode") or evidence.get("sparameter_mode"))
            .casefold()
            == "differential"
            and str(score.get("return_loss_trace") or evidence.get("return_loss_trace"))
            .upper()
            == "SDD11"
            and str(
                score.get("insertion_loss_trace")
                or evidence.get("insertion_loss_trace")
            ).upper()
            == "SDD21"
        ),
        "differential s4p/SDD11/SDD21 contract is satisfied",
        "expected differential s4p with SDD11/SDD21 traces",
    )
    tdr_port = str(
        score.get("tdr_observation_port")
        or evidence.get("tdr_observation_port")
        or ""
    )
    _check(
        checks,
        "tdr_observation_port",
        tdr_port == "Diff1",
        "TDR observation port is Diff1",
        f"TDR observation port must be Diff1, got {tdr_port or '<missing>'}",
    )
    required_metrics = [
        "rl_worst_db",
        "insertion_worst_db_in_band",
        "tdr_peak_deviation_ohm",
        "tdr_proximity_mse_ohm2",
        "tdr_flatness_msd_ohm2",
        "rl_violation_sum_db",
        "optimization_objective",
    ]
    missing_metrics = [
        key
        for key in required_metrics
        if score.get(key) is None and evidence.get(key) is None
    ]
    _check(
        checks,
        "optimization_metrics_present",
        not missing_metrics,
        "RL/TDR bounded optimization metrics are present",
        f"missing optimization metrics: {', '.join(missing_metrics)}",
        details={"missing": missing_metrics},
    )
    artifact_details = _artifact_presence_details(payload, score, evidence)
    _check(
        checks,
        "score_artifacts_exist",
        all(item["exists"] for item in artifact_details),
        "score artifacts exist on the execution host",
        "one or more score artifacts are missing on the execution host",
        details={"artifacts": artifact_details},
    )
    return checks


def _artifact_presence_details(
    payload: Mapping[str, Any],
    score: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    plot_artifacts = score.get("plot_artifacts")
    plot_refs = (
        list(plot_artifacts.values())
        if isinstance(plot_artifacts, Mapping)
        else []
    )
    refs = [
        payload.get("touchstone_path"),
        payload.get("tdr_path"),
        payload.get("evidence_artifact"),
        payload.get("solve_manifest"),
        *plot_refs,
    ]
    refs.extend(payload.get("artifact_refs") or [])
    refs.extend(evidence.get("artifact_refs") or [])
    details = []
    for ref in _unique(str(item) for item in refs if item):
        path = Path(ref)
        details.append({"path": ref, "exists": path.is_file()})
    return details or [{"path": "", "exists": False}]


def _check(
    checks: list[dict[str, Any]],
    check_id: str,
    passed: bool,
    passed_message: str,
    failed_message: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "passed" if passed else "approval_required",
            "message": passed_message if passed else failed_message,
            "details": details or {},
        }
    )


def _contains_unbounded_list(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 256 or any(_contains_unbounded_list(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_unbounded_list(item) for item in value.values())
    return False


def _manifest_path(
    context: WorkerContext,
    payload: Mapping[str, Any],
    loop_context: Mapping[str, Any],
) -> Path:
    base = (
        Path(context.artifacts_dir)
        if context.artifacts_dir
        else Path(str(payload.get("artifact_dir") or loop_context.get("report_dir") or "."))
    )
    base.mkdir(parents=True, exist_ok=True)
    round_index = int(loop_context.get("round_index") or 1)
    return base / f"iteration_{round_index}_qualification.json"


def _loop_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _append_unique(payload: dict[str, Any], key: str, value: str) -> None:
    values = list(payload.get(key) or [])
    if value and value not in values:
        values.append(value)
    payload[key] = values


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _approval_reason(checks: list[dict[str, Any]]) -> str:
    first = checks[0]["message"] if checks else "iteration qualification review"
    extra = len(checks) - 1
    if extra <= 0:
        return f"iteration_qualification:{first}"
    return f"iteration_qualification:{first}; plus {extra} more issue(s)"


def _unique(values: Any) -> list[str]:
    result = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return result
