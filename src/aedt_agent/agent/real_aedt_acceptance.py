from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import (
    GraphRunStatus,
    JobAttemptStatus,
    JobStatus,
    MissionState,
    NodeRunStatus,
    utc_now_iso,
)
from aedt_agent.agent.scorecard import _real_solve_checks


_REAL_TEMPLATE_ID = "brd_real_solve_evidence"
_SOLVE_CAPABILITY = "brd.local_cut.solve"
_SCORE_CAPABILITY = "brd.channel.score"
_REQUIRED_ATTESTATION = {
    "kind": "real_aedt",
    "adapter": "BrdRealSolveAdapter",
    "backend": "ansys.aedt.core.Hfss3dLayout",
    "analyze_executed": True,
}


def validate_real_aedt_acceptance(runtime, graph_run_id: str) -> dict[str, Any]:
    """Validate that one succeeded graph contains attested real AEDT evidence."""
    try:
        return _validate_real_aedt_acceptance(runtime, graph_run_id)
    except Exception as exc:
        return _report(
            graph_run_id,
            "",
            [
                _check(
                    "acceptance_validation_completed",
                    False,
                    _error_details(exc),
                )
            ],
            [],
        )


def _validate_real_aedt_acceptance(
    runtime,
    graph_run_id: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    artifact_refs: list[str] = []

    try:
        graph_run = runtime.store.get_graph_run(graph_run_id)
    except Exception as exc:
        graph_run = None
        checks.append(_check("graph_run_exists", False, _error_details(exc)))
    else:
        checks.append(
            _check(
                "graph_run_exists",
                graph_run is not None,
                {"graph_run_id": graph_run_id},
            )
        )
    if graph_run is None:
        return _report(graph_run_id, "", checks, artifact_refs)

    mission_id = graph_run.mission_id
    eligible = graph_run.template_id == _REAL_TEMPLATE_ID or _eligible_snapshot(
        graph_run.template_snapshot
    )
    checks.append(
        _check(
            "eligible_real_solve_graph",
            eligible,
            {
                "template_id": graph_run.template_id,
                "explicit_snapshot": _eligible_snapshot(graph_run.template_snapshot),
            },
        )
    )
    checks.append(
        _check(
            "graph_succeeded",
            graph_run.status == GraphRunStatus.SUCCEEDED,
            {"status": graph_run.status.value},
        )
    )

    try:
        mission = runtime.get_mission(mission_id)
    except Exception as exc:
        mission = None
        checks.append(_check("mission_exists", False, _error_details(exc)))
    else:
        checks.append(
            _check(
                "mission_exists",
                True,
                {"mission_id": mission_id},
            )
        )
    checks.append(
        _check(
            "mission_completed",
            mission is not None and mission.state == MissionState.COMPLETED,
            {"state": None if mission is None else mission.state.value},
        )
    )

    bound_jobs = _bound_jobs(runtime, graph_run_id)
    solve_jobs = [job for job in bound_jobs if job.capability == _SOLVE_CAPABILITY]
    score_jobs = [job for job in bound_jobs if job.capability == _SCORE_CAPABILITY]
    solve_job = _latest(solve_jobs)
    score_job = _latest(score_jobs)
    checks.append(
        _check(
            "graph_bound_solve_job_present",
            solve_job is not None,
            {"job_ids": [job.job_id for job in solve_jobs]},
        )
    )
    checks.append(
        _check(
            "graph_bound_score_job_present",
            score_job is not None,
            {"job_ids": [job.job_id for job in score_jobs]},
        )
    )
    checks.append(
        _check(
            "solve_job_succeeded",
            solve_job is not None and solve_job.status == JobStatus.SUCCEEDED,
            {
                "job_id": None if solve_job is None else solve_job.job_id,
                "status": None if solve_job is None else solve_job.status.value,
            },
        )
    )

    attempts = (
        []
        if solve_job is None
        else runtime.store.list_job_attempts(solve_job.job_id)
    )
    latest_attempt = attempts[-1] if attempts else None
    attempt_passed = (
        latest_attempt is not None
        and latest_attempt.status == JobAttemptStatus.SUCCEEDED
        and latest_attempt.metadata.get("execution_mode") == "local_process"
        and latest_attempt.metadata.get("harness_status") == "succeeded"
    )
    checks.append(
        _check(
            "latest_solve_attempt_local_process_succeeded",
            attempt_passed,
            {
                "attempt_id": (
                    None if latest_attempt is None else latest_attempt.attempt_id
                ),
                "status": (
                    None if latest_attempt is None else latest_attempt.status.value
                ),
                "execution_mode": (
                    None
                    if latest_attempt is None
                    else latest_attempt.metadata.get("execution_mode")
                ),
                "harness_status": (
                    None
                    if latest_attempt is None
                    else latest_attempt.metadata.get("harness_status")
                ),
            },
        )
    )

    scorecard_runs = [
        node_run
        for node_run in runtime.store.list_node_runs(graph_run_id)
        if node_run.node_role == "scorecard"
    ]
    scorecard_run = scorecard_runs[-1] if scorecard_runs else None
    scorecard_passed = (
        scorecard_run is not None
        and scorecard_run.status == NodeRunStatus.SUCCEEDED
        and scorecard_run.output_payload.get("status") == "passed"
    )
    checks.append(
        _check(
            "graph_scorecard_passed",
            scorecard_passed,
            {
                "node_run_id": (
                    None if scorecard_run is None else scorecard_run.node_run_id
                ),
                "node_status": (
                    None if scorecard_run is None else scorecard_run.status.value
                ),
                "scorecard_status": (
                    None
                    if scorecard_run is None
                    else scorecard_run.output_payload.get("status")
                ),
            },
        )
    )

    if mission is not None:
        try:
            reused_checks = _real_solve_checks(
                runtime,
                mission_id,
                bound_jobs,
            )
        except Exception as exc:
            checks.append(
                _check("score_mission.reused_gate", False, _error_details(exc))
            )
        else:
            for existing in reused_checks:
                checks.append(
                    _check(
                        f"score_mission.{existing.get('id', 'unknown')}",
                        bool(existing.get("passed")),
                        dict(existing.get("details") or {}),
                    )
                )

    manifest, manifest_error = _load_manifest(solve_job)
    checks.append(
        _check(
            "solve_manifest_version_1",
            manifest is not None and manifest.get("version") == 1,
            {
                "version": None if manifest is None else manifest.get("version"),
                **({"reason": manifest_error} if manifest_error else {}),
            },
        )
    )

    summary_attestation = _attestation_from_job(solve_job)
    manifest_attestation = _attestation_from_manifest(manifest)
    requested = _requested_environment(solve_job)
    attestation_passed = (
        isinstance(summary_attestation, dict)
        and summary_attestation == manifest_attestation
        and all(
            summary_attestation.get(key) == value
            for key, value in _REQUIRED_ATTESTATION.items()
        )
        and bool(summary_attestation.get("aedt_version"))
        and isinstance(summary_attestation.get("non_graphical"), bool)
        and (
            not requested.get("aedt_version")
            or summary_attestation.get("aedt_version")
            == requested["aedt_version"]
        )
        and (
            "non_graphical" not in requested
            or summary_attestation.get("non_graphical")
            == requested["non_graphical"]
        )
    )
    checks.append(
        _check(
            "real_execution_attestation_verified",
            attestation_passed,
            {
                "solve_summary": summary_attestation,
                "manifest_summary": manifest_attestation,
                "requested": requested,
            },
        )
    )

    outputs_passed, output_details, output_refs = _verify_required_outputs(
        manifest,
        solve_job,
    )
    artifact_refs.extend(output_refs)
    checks.append(
        _check(
            "required_solve_outputs_verified",
            outputs_passed,
            output_details,
        )
    )
    if solve_job is not None:
        artifact_refs.extend(solve_job.artifact_refs)
        manifest_path = str(solve_job.output_payload.get("solve_manifest") or "")
        if manifest_path:
            artifact_refs.append(manifest_path)
    if score_job is not None:
        artifact_refs.extend(score_job.artifact_refs)
    if scorecard_run is not None:
        artifact_refs.extend(scorecard_run.artifact_refs)
    return _report(graph_run_id, mission_id, checks, artifact_refs)


def write_real_aedt_acceptance_report(
    report: dict[str, Any],
    path: str | Path,
) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return output_path


def _eligible_snapshot(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list):
        return False
    mappings = [node for node in nodes if isinstance(node, dict)]
    return any(
        node.get("capability") == _SOLVE_CAPABILITY for node in mappings
    ) and any(node.get("role") == "scorecard" for node in mappings)


def _bound_jobs(runtime, graph_run_id: str) -> list[Any]:
    jobs: list[Any] = []
    for job_id in runtime.store.list_graph_bound_job_ids(graph_run_id):
        try:
            jobs.append(runtime.get_job(job_id))
        except KeyError:
            continue
    return sorted(jobs, key=lambda job: (job.created_at, job.job_id))


def _latest(jobs: list[Any]) -> Any | None:
    return jobs[-1] if jobs else None


def _load_manifest(solve_job) -> tuple[dict[str, Any] | None, str]:
    if solve_job is None:
        return None, "solve_job_missing"
    path = Path(str(solve_job.output_payload.get("solve_manifest") or ""))
    if not path.is_file():
        return None, "solve_manifest_missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        return None, f"solve_manifest_invalid:{type(exc).__name__}"
    if not isinstance(value, dict):
        return None, "solve_manifest_not_object"
    return value, ""


def _attestation_from_job(solve_job) -> dict[str, Any] | None:
    if solve_job is None:
        return None
    summary = solve_job.output_payload.get("solve_summary")
    if not isinstance(summary, dict):
        return None
    value = summary.get("execution_attestation")
    return dict(value) if isinstance(value, dict) else None


def _attestation_from_manifest(
    manifest: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if manifest is None or not isinstance(manifest.get("summary"), dict):
        return None
    value = manifest["summary"].get("execution_attestation")
    return dict(value) if isinstance(value, dict) else None


def _requested_environment(solve_job) -> dict[str, Any]:
    if solve_job is None:
        return {}
    aedt = solve_job.input_payload.get("aedt")
    if not isinstance(aedt, dict):
        return {}
    requested: dict[str, Any] = {}
    if aedt.get("version"):
        requested["aedt_version"] = str(aedt["version"])
    if isinstance(aedt.get("non_graphical"), bool):
        requested["non_graphical"] = aedt["non_graphical"]
    return requested


def _verify_required_outputs(
    manifest: dict[str, Any] | None,
    solve_job,
) -> tuple[bool, dict[str, Any], list[str]]:
    outputs = manifest.get("outputs") if isinstance(manifest, dict) else None
    outputs = outputs if isinstance(outputs, dict) else {}
    job_paths = {
        "solved_project": (
            "" if solve_job is None else str(solve_job.output_payload.get("solved_project") or "")
        ),
        "touchstone": (
            "" if solve_job is None else str(solve_job.output_payload.get("touchstone_path") or "")
        ),
        "tdr": (
            "" if solve_job is None else str(solve_job.output_payload.get("tdr_path") or "")
        ),
    }
    details: dict[str, Any] = {}
    refs: list[str] = []
    for key in ("solved_project", "touchstone", "tdr"):
        entry = outputs.get(key)
        verified, entry_details = _verify_output_entry(entry, job_paths[key])
        details[key] = {"passed": verified, **entry_details}
        if entry_details.get("path"):
            refs.append(str(entry_details["path"]))
    return all(item["passed"] for item in details.values()), {"outputs": details}, refs


def _verify_output_entry(
    entry: Any,
    expected_job_path: str,
) -> tuple[bool, dict[str, Any]]:
    if not isinstance(entry, dict):
        return False, {"reason": "manifest_entry_missing"}
    path = Path(str(entry.get("path") or ""))
    expected_hash = str(entry.get("sha256") or "")
    expected_size = entry.get("size_bytes")
    details = {
        "path": str(path),
        "sha256": expected_hash,
        "size_bytes": expected_size,
    }
    if not path.is_file():
        return False, {**details, "reason": "file_missing"}
    if not expected_hash:
        return False, {**details, "reason": "sha256_missing"}
    if not isinstance(expected_size, int) or isinstance(expected_size, bool):
        return False, {**details, "reason": "size_bytes_invalid"}
    try:
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
    except OSError as exc:
        return False, {**details, **_error_details(exc)}
    same_job_path = bool(expected_job_path) and (
        Path(expected_job_path).resolve() == path.resolve()
    )
    passed = (
        actual_size == expected_size
        and actual_hash == expected_hash
        and same_job_path
    )
    return passed, {
        **details,
        "actual_size_bytes": actual_size,
        "actual_sha256": actual_hash,
        "matches_job_output": same_job_path,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(check_id: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "details": details}


def _report(
    graph_run_id: str,
    mission_id: str,
    checks: list[dict[str, Any]],
    artifact_refs: list[str],
) -> dict[str, Any]:
    failed = sorted(check["id"] for check in checks if not check["passed"])
    return {
        "status": "failed" if failed else "passed",
        "graph_run_id": graph_run_id,
        "mission_id": mission_id,
        "checks": checks,
        "failed_check_ids": failed,
        "artifact_refs": sorted({ref for ref in artifact_refs if ref}),
        "generated_at": utc_now_iso(),
    }


def _error_details(exc: Exception) -> dict[str, Any]:
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
