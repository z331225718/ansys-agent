from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import EventType, MissionState


_TERMINAL_STATES = {
    MissionState.COMPLETED,
    MissionState.FAILED,
    MissionState.CANCELED,
}
_DELETABLE_POLICIES = {"mission", "transient"}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def prune_mission_artifacts(
    runtime,
    mission_id: str,
    root: str | Path,
    *,
    older_than_hours: float = 24.0,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Plan or apply conservative manifest-backed artifact deletion."""
    report = _empty_report(mission_id, root, apply=apply)
    try:
        hours = float(older_than_hours)
    except (TypeError, ValueError):
        return _block(report, "older_than_hours_invalid")
    if hours < 0:
        return _block(report, "older_than_hours_invalid")

    root_path, root_error = _validated_root(root)
    if root_error:
        return _block(report, root_error)
    assert root_path is not None
    report["root"] = str(root_path)

    try:
        mission = runtime.get_mission(mission_id)
    except (KeyError, ValueError):
        return _block(report, "mission_not_found")
    if mission.state not in _TERMINAL_STATES:
        report["mission_state"] = mission.state.value
        return _block(report, "mission_not_terminal")

    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    cutoff = current_time.astimezone(UTC) - timedelta(hours=hours)

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    manifests = sorted(
        runtime.store.list_artifact_manifests(mission_id),
        key=lambda item: (item.path, item.artifact_id),
    )
    groups: dict[str, list[tuple[Any, Path]]] = {}
    for manifest in manifests:
        lexical_path = _lexical_manifest_path(manifest.path, root_path)
        groups.setdefault(
            os.path.normcase(str(lexical_path)),
            [],
        ).append((manifest, lexical_path))

    for path_key in sorted(groups):
        evaluations: list[tuple[Any, dict[str, Any] | None, str]] = []
        for manifest, lexical_path in groups[path_key]:
            candidate, reason = _evaluate_manifest(
                manifest,
                root_path,
                cutoff,
                lexical_path=lexical_path,
            )
            evaluations.append((manifest, candidate, reason))

        group_reasons = [reason for _, _, reason in evaluations if reason]
        valid_candidates = [
            candidate
            for _, candidate, reason in evaluations
            if not reason and candidate is not None
        ]
        declarations = {
            (manifest.sha256, manifest.size_bytes)
            for manifest, _, _ in evaluations
        }
        resolved_paths = {
            os.path.normcase(candidate["path"])
            for candidate in valid_candidates
        }
        group_is_deletable = (
            not group_reasons
            and len(valid_candidates) == len(evaluations)
            and len(declarations) == 1
            and len(resolved_paths) == 1
        )
        if not group_is_deletable:
            for manifest, _, reason in evaluations:
                skipped.append(
                    _manifest_report_item(
                        manifest,
                        reason=reason or "path_protected_by_manifest",
                    )
                )
            continue

        candidates.append(valid_candidates[0])
        for manifest, _, _ in evaluations[1:]:
            skipped.append(
                _manifest_report_item(
                    manifest,
                    reason="duplicate_manifest_path",
                )
            )

    candidates.sort(key=lambda item: (item["path"], item["artifact_id"]))
    skipped.sort(
        key=lambda item: (
            item.get("path", ""),
            item.get("artifact_id", ""),
            item["reason"],
        )
    )
    report["candidates"] = candidates
    report["skipped"] = skipped
    report["candidate_count"] = len(candidates)
    report["candidate_bytes"] = sum(item["size_bytes"] for item in candidates)

    if not apply:
        report["status"] = "dry_run"
        return report

    deleted: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for candidate in candidates:
        payload = {
            "mission_id": mission_id,
            "artifact_id": candidate["artifact_id"],
            "path": candidate["path"],
            "hash": candidate["sha256"],
            "sha256": candidate["sha256"],
            "policy": candidate["retention_policy"],
            "root": str(root_path),
            "bytes": candidate["size_bytes"],
        }
        try:
            runtime.store.append_event(
                mission_id,
                EventType.ARTIFACT_RETENTION_PLANNED,
                payload,
            )
        except Exception as exc:
            failures.append(
                _failure_item(candidate, "planned_event_failed", exc)
            )
            continue

        verification_error = _verify_candidate_for_delete(candidate, root_path)
        if verification_error:
            failure = {
                "artifact_id": candidate["artifact_id"],
                "path": candidate["path"],
                "reason": "predelete_verification_failed",
                "error_type": "SafetyCheckFailed",
                "message": verification_error,
            }
            failures.append(failure)
            _append_failed_event_best_effort(runtime, mission_id, payload, failure)
            continue

        try:
            Path(candidate["path"]).unlink()
        except Exception as exc:
            failure = _failure_item(candidate, "delete_failed", exc)
            failures.append(failure)
            _append_failed_event_best_effort(runtime, mission_id, payload, failure)
            continue

        deleted.append(candidate)
        try:
            runtime.store.append_event(
                mission_id,
                EventType.ARTIFACT_RETENTION_APPLIED,
                payload,
            )
        except Exception as exc:
            failure = _failure_item(candidate, "applied_event_failed", exc)
            failures.append(failure)
            _append_failed_event_best_effort(runtime, mission_id, payload, failure)

    failures.sort(key=lambda item: (item["path"], item["artifact_id"], item["reason"]))
    report["deleted_count"] = len(deleted)
    report["deleted_bytes"] = sum(item["size_bytes"] for item in deleted)
    report["failures"] = failures
    report["status"] = "partial_failure" if failures else "applied"
    return report


def _empty_report(
    mission_id: str,
    root: str | Path,
    *,
    apply: bool,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "dry_run": not apply,
        "mission_id": mission_id,
        "root": str(root),
        "candidate_count": 0,
        "candidate_bytes": 0,
        "deleted_count": 0,
        "deleted_bytes": 0,
        "candidates": [],
        "skipped": [],
        "failures": [],
    }


def _block(report: dict[str, Any], reason: str) -> dict[str, Any]:
    report["status"] = "blocked"
    report["reason"] = reason
    return report


def _validated_root(root: str | Path) -> tuple[Path | None, str]:
    try:
        lexical_root = Path(os.path.abspath(os.fspath(root)))
        root_stat = lexical_root.lstat()
    except (OSError, TypeError, ValueError):
        return None, "root_missing_or_unresolvable"
    if _is_reparse(root_stat, lexical_root):
        return None, "root_is_reparse_point"
    if not stat.S_ISDIR(root_stat.st_mode):
        return None, "root_not_directory"
    try:
        return lexical_root.resolve(strict=True), ""
    except OSError:
        return None, "root_missing_or_unresolvable"


def _evaluate_manifest(
    manifest,
    root: Path,
    cutoff: datetime,
    *,
    lexical_path: Path | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if manifest.retention_policy == "keep":
        return None, "retention_keep"
    if bool(manifest.metadata.get("pinned")):
        return None, "metadata_pinned"
    if manifest.retention_policy not in _DELETABLE_POLICIES:
        return None, "retention_policy_unknown"

    created_at = _parse_datetime(manifest.created_at)
    if created_at is None:
        return None, "created_at_invalid"
    if created_at > cutoff:
        return None, "younger_than_threshold"

    lexical_path = lexical_path or _lexical_manifest_path(manifest.path, root)
    if not _is_within(lexical_path, root):
        return None, "path_outside_root_lexical"
    try:
        path_stat = lexical_path.lstat()
    except FileNotFoundError:
        return None, "path_missing"
    except OSError:
        return None, "path_unreadable"
    if _has_reparse_component(lexical_path, root):
        return None, "path_has_reparse_component"
    if not stat.S_ISREG(path_stat.st_mode):
        return None, "path_not_regular_file"
    try:
        resolved_path = lexical_path.resolve(strict=True)
    except FileNotFoundError:
        return None, "path_missing"
    except OSError:
        return None, "path_unresolvable"
    if not _is_within(resolved_path, root):
        return None, "path_outside_root_resolved"
    if not manifest.sha256:
        return None, "sha256_missing"
    try:
        actual_size = resolved_path.stat().st_size
        if actual_size != manifest.size_bytes:
            return None, "size_mismatch"
        if _sha256(resolved_path) != manifest.sha256:
            return None, "sha256_mismatch"
    except OSError:
        return None, "verification_failed"

    item = _manifest_report_item(manifest)
    item["path"] = str(resolved_path)
    return item, ""


def _lexical_manifest_path(path: str, root: Path) -> Path:
    raw_path = Path(path)
    return Path(
        os.path.abspath(
            os.fspath(root / raw_path if not raw_path.is_absolute() else raw_path)
        )
    )


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except (OSError, ValueError):
        return False


def _has_reparse_component(path: Path, root: Path) -> bool:
    current = path
    while True:
        try:
            if _is_reparse(current.lstat(), current):
                return True
        except OSError:
            return True
        if current == root:
            return False
        current = current.parent


def _is_reparse(path_stat: os.stat_result, path: Path) -> bool:
    attributes = int(getattr(path_stat, "st_file_attributes", 0))
    return path.is_symlink() or bool(attributes & _REPARSE_POINT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_candidate_for_delete(
    candidate: dict[str, Any],
    root: Path,
) -> str:
    path = Path(candidate["path"])
    if not _is_within(path, root):
        return "path_outside_root_lexical"
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return "path_missing"
    except OSError:
        return "path_unreadable"
    if _has_reparse_component(path, root):
        return "path_has_reparse_component"
    if not stat.S_ISREG(path_stat.st_mode):
        return "path_not_regular_file"
    try:
        resolved_path = path.resolve(strict=True)
        if not _is_within(resolved_path, root):
            return "path_outside_root_resolved"
        if resolved_path.stat().st_size != candidate["size_bytes"]:
            return "size_mismatch"
        if _sha256(resolved_path) != candidate["sha256"]:
            return "sha256_mismatch"
    except FileNotFoundError:
        return "path_missing"
    except OSError:
        return "verification_failed"
    return ""


def _manifest_report_item(manifest, *, reason: str = "") -> dict[str, Any]:
    item = {
        "artifact_id": manifest.artifact_id,
        "path": manifest.path,
        "sha256": manifest.sha256,
        "size_bytes": manifest.size_bytes,
        "retention_policy": manifest.retention_policy,
        "created_at": manifest.created_at,
    }
    if reason:
        item["reason"] = reason
    return item


def _failure_item(
    candidate: dict[str, Any],
    reason: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "artifact_id": candidate["artifact_id"],
        "path": candidate["path"],
        "reason": reason,
        "error_type": type(exc).__name__,
        "message": str(exc),
    }


def _append_failed_event_best_effort(
    runtime,
    mission_id: str,
    payload: dict[str, Any],
    failure: dict[str, Any],
) -> None:
    try:
        runtime.store.append_event(
            mission_id,
            EventType.ARTIFACT_RETENTION_FAILED,
            {
                **payload,
                "reason": failure["reason"],
                "error_type": failure["error_type"],
                "message": failure["message"],
            },
        )
    except Exception:
        return
