from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import JobStatus


def score_mission(runtime, mission_id: str, *, template_id: str = "") -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    mission = runtime.get_mission(mission_id)
    checks.append(_check("mission_exists", True, {"state": mission.state.value}))

    events = runtime.list_events(mission_id)
    checks.append(_check("event_stream_present", bool(events), {"event_count": len(events)}))

    jobs = runtime.list_jobs(mission_id)
    checks.append(_check("job_created", bool(jobs), {"job_count": len(jobs)}))
    checks.append(
        _check(
            "jobs_have_capability",
            all(bool(job.capability) for job in jobs),
            {"capabilities": [job.capability for job in jobs]},
        )
    )

    succeeded_jobs = [job for job in jobs if job.status == JobStatus.SUCCEEDED]
    checks.append(_check("succeeded_job_present", bool(succeeded_jobs), {"succeeded_job_count": len(succeeded_jobs)}))
    checks.append(
        _check(
            "succeeded_jobs_have_artifacts",
            all(bool(job.artifact_refs) for job in succeeded_jobs),
            {"artifact_refs": [ref for job in succeeded_jobs for ref in job.artifact_refs]},
        )
    )
    checks.append(
        _check(
            "succeeded_jobs_have_evidence_summary",
            all(isinstance(job.output_payload.get("evidence_summary"), dict) for job in succeeded_jobs),
            {"job_ids": [job.job_id for job in succeeded_jobs]},
        )
    )

    if template_id == "brd_real_solve_evidence":
        checks.extend(
            _real_solve_checks(runtime, mission_id, jobs)
        )

    status = "passed" if all(check["passed"] for check in checks) else "failed"
    return {
        "status": status,
        "mission_id": mission_id,
        "template_id": template_id,
        "checks": checks,
    }


def _check(check_id: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"id": check_id, "passed": passed, "details": details}


def _real_solve_checks(
    runtime,
    mission_id: str,
    jobs,
) -> list[dict[str, Any]]:
    solve_jobs = [
        job
        for job in jobs
        if job.capability == "brd.local_cut.solve"
        and job.status == JobStatus.SUCCEEDED
    ]
    score_jobs = [
        job
        for job in jobs
        if job.capability == "brd.channel.score"
        and job.status == JobStatus.SUCCEEDED
    ]
    solve_job = solve_jobs[-1] if solve_jobs else None
    score_job = score_jobs[-1] if score_jobs else None

    approvals = runtime.store.list_approvals(mission_id)
    approved = [
        approval
        for approval in approvals
        if approval.decision.value == "approved"
        and approval.reason == "approve_real_brd_solve"
    ]
    approval_details = {
        "approval_ids": [
            approval.approval_id for approval in approved
        ],
        "approved_count": len(approved),
    }

    attempts = (
        []
        if solve_job is None
        else runtime.store.list_job_attempts(solve_job.job_id)
    )
    local_process_attempts = [
        attempt
        for attempt in attempts
        if attempt.status.value == "succeeded"
        and attempt.metadata.get("execution_mode") == "local_process"
        and attempt.metadata.get("harness_status") == "succeeded"
    ]
    process_details = {
        "solve_job_id": None if solve_job is None else solve_job.job_id,
        "attempt_ids": [
            attempt.attempt_id
            for attempt in local_process_attempts
        ],
    }

    manifest_ok, manifest_details = _verify_solve_manifest(
        solve_job
    )
    artifacts_ok, artifact_details = _verify_registered_artifacts(
        runtime,
        mission_id,
        solve_job,
    )
    bounded_ok = (
        solve_job is not None
        and score_job is not None
        and solve_job.output_payload.get(
            "solve_summary",
            {},
        ).get("raw_sparameters")
        == "artifact_only"
        and solve_job.output_payload.get(
            "solve_summary",
            {},
        ).get("raw_tdr")
        == "artifact_only"
        and score_job.output_payload.get(
            "evidence_summary",
            {},
        ).get("raw_sparameters")
        == "artifact_only"
        and score_job.output_payload.get(
            "evidence_summary",
            {},
        ).get("raw_tdr")
        == "artifact_only"
        and not _contains_unbounded_list(
            solve_job.output_payload
        )
        and not _contains_unbounded_list(
            score_job.output_payload
        )
    )
    bounded_details = {
        "raw_trace_policy": (
            "artifact_only" if bounded_ok else "invalid"
        ),
        "max_inline_list_length": 128,
    }

    lineage_ok = (
        solve_job is not None
        and score_job is not None
        and score_job.input_payload.get("touchstone_path")
        == solve_job.output_payload.get("touchstone_path")
        and score_job.input_payload.get("tdr_path")
        == solve_job.output_payload.get("tdr_path")
        and score_job.input_payload.get("artifact_dir")
        == solve_job.output_payload.get("artifact_dir")
    )
    lineage_details = {
        "solve_job_id": None if solve_job is None else solve_job.job_id,
        "score_job_id": None if score_job is None else score_job.job_id,
    }

    return [
        _check(
            "model_approval_resolved",
            bool(approved),
            approval_details,
        ),
        _check(
            "solve_used_local_process",
            bool(local_process_attempts),
            process_details,
        ),
        _check(
            "solve_manifest_verified",
            manifest_ok,
            manifest_details,
        ),
        _check(
            "solve_artifacts_verified",
            artifacts_ok,
            artifact_details,
        ),
        _check(
            "raw_arrays_excluded",
            bounded_ok,
            bounded_details,
        ),
        _check(
            "channel_score_bound_to_solve",
            lineage_ok,
            lineage_details,
        ),
    ]


def _verify_solve_manifest(job) -> tuple[bool, dict[str, Any]]:
    if job is None:
        return False, {"reason": "solve job is missing"}
    manifest_path = Path(
        str(job.output_payload.get("solve_manifest") or "")
    )
    if not manifest_path.is_file():
        return False, {
            "reason": "solve manifest is missing",
            "path": str(manifest_path),
        }
    try:
        payload = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        outputs = dict(payload.get("outputs") or {})
    except (OSError, TypeError, ValueError) as exc:
        return False, {
            "reason": "solve manifest is invalid",
            "error_type": type(exc).__name__,
        }

    required = ("solved_project", "touchstone", "tdr")
    verified: dict[str, bool] = {}
    for key in required:
        entry = outputs.get(key)
        if not isinstance(entry, dict):
            verified[key] = False
            continue
        path = Path(str(entry.get("path") or ""))
        expected = str(entry.get("sha256") or "")
        verified[key] = (
            path.is_file()
            and bool(expected)
            and _sha256(path) == expected
        )
    return all(verified.values()), {
        "manifest_path": str(manifest_path),
        "outputs": verified,
    }


def _verify_registered_artifacts(
    runtime,
    mission_id: str,
    solve_job,
) -> tuple[bool, dict[str, Any]]:
    if solve_job is None:
        return False, {"reason": "solve job is missing"}
    required = [
        str(solve_job.output_payload.get(key) or "")
        for key in (
            "touchstone_path",
            "tdr_path",
            "solve_manifest",
        )
    ]
    manifests = runtime.store.list_artifact_manifests(mission_id)
    by_path = {
        str(Path(manifest.path).resolve()): manifest
        for manifest in manifests
    }
    verified: dict[str, bool] = {}
    for value in required:
        path = Path(value).resolve()
        manifest = by_path.get(str(path))
        verified[str(path)] = (
            manifest is not None
            and path.is_file()
            and bool(manifest.sha256)
            and _sha256(path) == manifest.sha256
        )
    return all(verified.values()), {"artifacts": verified}


def _contains_unbounded_list(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 128 or any(
            _contains_unbounded_list(item) for item in value
        )
    if isinstance(value, dict):
        return any(
            _contains_unbounded_list(item)
            for item in value.values()
        )
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(
            lambda: source.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)
    return digest.hexdigest()
