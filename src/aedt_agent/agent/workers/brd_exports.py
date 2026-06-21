from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext
from aedt_agent.layout.channel_scoring import parse_tdr_csv, parse_touchstone


BRD_TOUCHSTONE_EXPORT_CAPABILITY = "brd.touchstone.export"
BRD_TDR_EXPORT_CAPABILITY = "brd.tdr.export"


def build_brd_touchstone_export_job_input(
    *,
    touchstone_path: str | Path,
    solve_manifest: str | Path = "",
    artifact_dir: str | Path = "",
    sparameter_mode: str = "auto",
    loop_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "touchstone_path": str(touchstone_path),
        "solve_manifest": str(solve_manifest),
        "artifact_dir": str(artifact_dir),
        "sparameter_mode": str(sparameter_mode),
        "loop_context": dict(loop_context or {}),
    }


def build_brd_tdr_export_job_input(
    *,
    tdr_path: str | Path,
    touchstone_path: str | Path = "",
    solve_manifest: str | Path = "",
    touchstone_export_manifest: str | Path = "",
    artifact_dir: str | Path = "",
    tdr_expression: str = "",
    tdr_observation_port: str = "",
    tdr_report_name: str = "",
    loop_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tdr_path": str(tdr_path),
        "touchstone_path": str(touchstone_path),
        "solve_manifest": str(solve_manifest),
        "touchstone_export_manifest": str(touchstone_export_manifest),
        "artifact_dir": str(artifact_dir),
        "tdr_expression": str(tdr_expression),
        "tdr_observation_port": str(tdr_observation_port),
        "tdr_report_name": str(tdr_report_name),
        "loop_context": dict(loop_context or {}),
    }


def run_brd_touchstone_export_worker(
    job: JobRecord,
    context: WorkerContext,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    touchstone_path = _artifact_path(payload, "touchstone_path", "touchstone")
    samples = parse_touchstone(touchstone_path)
    if not samples:
        raise ValueError(f"touchstone_path has no samples: {touchstone_path}")

    port_count = int(samples[0].get("port_count") or 0)
    touchstone_kind = (
        f"s{port_count}p" if port_count else _touchstone_kind(touchstone_path)
    )
    sparameter_mode = str(payload.get("sparameter_mode") or "auto")
    if sparameter_mode == "auto":
        sparameter_mode = (
            "differential" if touchstone_kind == "s4p" else "single_ended"
        )
    return_loss_trace, insertion_loss_trace = _trace_names(
        touchstone_kind,
        sparameter_mode,
    )

    loop_context = _loop_context(payload)
    manifest_path = _manifest_path(
        context,
        payload,
        "touchstone_export_manifest.json",
    )
    manifest = {
        "version": 1,
        "capability": BRD_TOUCHSTONE_EXPORT_CAPABILITY,
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "input": {
            "solve_manifest": str(payload.get("solve_manifest") or ""),
        },
        "outputs": {
            "touchstone": _artifact_record(touchstone_path),
        },
        "summary": {
            "status": "succeeded",
            "touchstone_kind": touchstone_kind,
            "sparameter_mode": sparameter_mode,
            "return_loss_trace": return_loss_trace,
            "insertion_loss_trace": insertion_loss_trace,
            "touchstone_sample_count": len(samples),
            "raw_sparameters": "artifact_only",
        },
    }
    _write_json(manifest_path, manifest)
    _append_unique(
        loop_context,
        "touchstone_export_manifest_paths",
        str(manifest_path),
    )
    loop_context["last_touchstone_export_manifest_path"] = str(manifest_path)
    loop_context["last_touchstone_path"] = str(touchstone_path)

    artifact_dir = str(
        payload.get("artifact_dir")
        or touchstone_path.parent
    )
    output = {
        **payload,
        "status": "succeeded",
        "touchstone_path": str(touchstone_path),
        "touchstone_kind": touchstone_kind,
        "sparameter_mode": sparameter_mode,
        "return_loss_trace": return_loss_trace,
        "insertion_loss_trace": insertion_loss_trace,
        "touchstone_export_manifest": str(manifest_path),
        "export_manifest": str(manifest_path),
        "artifact_dir": artifact_dir,
        "loop_context": loop_context,
        "evidence_summary": {
            "status": "touchstone_exported",
            "touchstone_kind": touchstone_kind,
            "sparameter_mode": sparameter_mode,
            "return_loss_trace": return_loss_trace,
            "insertion_loss_trace": insertion_loss_trace,
            "raw_sparameters": "artifact_only",
            "artifact_refs": [str(touchstone_path), str(manifest_path)],
        },
        "artifact_refs": [str(touchstone_path), str(manifest_path)],
    }
    return output


def run_brd_tdr_export_worker(
    job: JobRecord,
    context: WorkerContext,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    tdr_path = _artifact_path(payload, "tdr_path", "tdr")
    samples = parse_tdr_csv(tdr_path)
    if not samples:
        raise ValueError(f"tdr_path has no samples: {tdr_path}")

    loop_context = _loop_context(payload)
    manifest_path = _manifest_path(context, payload, "tdr_export_manifest.json")
    manifest = {
        "version": 1,
        "capability": BRD_TDR_EXPORT_CAPABILITY,
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "input": {
            "solve_manifest": str(payload.get("solve_manifest") or ""),
            "touchstone_export_manifest": str(
                payload.get("touchstone_export_manifest") or ""
            ),
        },
        "outputs": {
            "tdr": _artifact_record(tdr_path),
        },
        "summary": {
            "status": "succeeded",
            "tdr_expression": str(payload.get("tdr_expression") or ""),
            "tdr_observation_port": str(
                payload.get("tdr_observation_port") or ""
            ),
            "tdr_report_name": str(payload.get("tdr_report_name") or ""),
            "tdr_sample_count": len(samples),
            "raw_tdr": "artifact_only",
        },
    }
    _write_json(manifest_path, manifest)
    _append_unique(loop_context, "tdr_export_manifest_paths", str(manifest_path))
    loop_context["last_tdr_export_manifest_path"] = str(manifest_path)
    loop_context["last_tdr_path"] = str(tdr_path)

    artifact_dir = str(payload.get("artifact_dir") or tdr_path.parent)
    output = {
        **payload,
        "status": "succeeded",
        "tdr_path": str(tdr_path),
        "tdr_export_manifest": str(manifest_path),
        "artifact_dir": artifact_dir,
        "loop_context": loop_context,
        "evidence_summary": {
            "status": "tdr_exported",
            "tdr_expression": str(payload.get("tdr_expression") or ""),
            "tdr_observation_port": str(
                payload.get("tdr_observation_port") or ""
            ),
            "raw_tdr": "artifact_only",
            "artifact_refs": [str(tdr_path), str(manifest_path)],
        },
        "artifact_refs": [str(tdr_path), str(manifest_path)],
    }
    return output


def _artifact_path(
    payload: Mapping[str, Any],
    payload_key: str,
    manifest_key: str,
) -> Path:
    value = str(payload.get(payload_key) or "").strip()
    if value:
        path = Path(value)
    else:
        path = _path_from_solve_manifest(payload, manifest_key)
    if not path.is_file():
        raise ValueError(f"{payload_key} does not exist: {path}")
    return path


def _path_from_solve_manifest(
    payload: Mapping[str, Any],
    key: str,
) -> Path:
    manifest_path = Path(str(payload.get("solve_manifest") or ""))
    if not manifest_path.is_file():
        raise ValueError(f"solve_manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = dict((manifest.get("outputs") or {}).get(key) or {})
    raw_path = str(entry.get("path") or "").strip()
    if not raw_path:
        raise ValueError(f"solve_manifest output missing: {key}")
    path = Path(raw_path)
    expected = str(entry.get("sha256") or "")
    if expected and path.is_file() and _sha256(path) != expected:
        raise ValueError(f"solve_manifest hash mismatch: {key}")
    return path


def _trace_names(touchstone_kind: str, sparameter_mode: str) -> tuple[str, str]:
    if sparameter_mode == "differential" or touchstone_kind == "s4p":
        return "SDD11", "SDD21"
    return "S11", "S21"


def _touchstone_kind(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix if suffix.startswith("s") and suffix.endswith("p") else "unknown"


def _manifest_path(
    context: WorkerContext,
    payload: Mapping[str, Any],
    filename: str,
) -> Path:
    base = (
        Path(context.artifacts_dir)
        if context.artifacts_dir
        else Path(str(payload.get("artifact_dir") or "."))
    )
    base.mkdir(parents=True, exist_ok=True)
    return base / filename


def _artifact_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _loop_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _append_unique(payload: dict[str, Any], key: str, value: str) -> None:
    values = list(payload.get(key) or [])
    if value and value not in values:
        values.append(value)
    payload[key] = values
