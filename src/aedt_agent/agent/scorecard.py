from __future__ import annotations

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

    status = "passed" if all(check["passed"] for check in checks) else "failed"
    return {
        "status": status,
        "mission_id": mission_id,
        "template_id": template_id,
        "checks": checks,
    }


def _check(check_id: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"id": check_id, "passed": passed, "details": details}
