from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.evaluation import build_sparameter_evidence
from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext
from aedt_agent.layout.channel_scoring import parse_touchstone, score_channel_result


BRD_CHANNEL_SCORE_CAPABILITY = "brd.channel.score"


def build_brd_channel_score_job_input(
    *,
    touchstone_path: str | Path,
    tdr_path: str | Path,
    artifact_dir: str | Path,
    frequency_start_ghz: float = 0.0,
    frequency_stop_ghz: float = 67.0,
    rl_target_db: float = -20.0,
    tdr_target_ohm: float = 100.0,
    bucket_count: int = 128,
) -> dict[str, Any]:
    return {
        "touchstone_path": str(touchstone_path),
        "tdr_path": str(tdr_path),
        "artifact_dir": str(artifact_dir),
        "frequency_start_ghz": frequency_start_ghz,
        "frequency_stop_ghz": frequency_stop_ghz,
        "rl_target_db": rl_target_db,
        "tdr_target_ohm": tdr_target_ohm,
        "bucket_count": bucket_count,
    }


def run_brd_channel_score_worker(job: JobRecord, context: WorkerContext) -> dict[str, Any]:
    payload = dict(job.input_payload)
    touchstone_path = Path(str(payload["touchstone_path"]))
    tdr_path = Path(str(payload["tdr_path"]))
    if not touchstone_path.exists():
        raise ValueError(f"touchstone_path does not exist: {touchstone_path}")
    if not tdr_path.exists():
        raise ValueError(f"tdr_path does not exist: {tdr_path}")

    artifact_dir = Path(str(payload["artifact_dir"]))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    frequency_start_ghz = float(payload.get("frequency_start_ghz", 0.0))
    frequency_stop_ghz = float(payload.get("frequency_stop_ghz", 67.0))
    rl_target_db = float(payload.get("rl_target_db", -20.0))
    tdr_target_ohm = float(payload.get("tdr_target_ohm", 100.0))
    bucket_count = int(payload.get("bucket_count", 128))

    score = score_channel_result(
        touchstone_path,
        tdr_path,
        frequency_start_ghz=frequency_start_ghz,
        frequency_stop_ghz=frequency_stop_ghz,
        rl_target_db=rl_target_db,
        tdr_target_ohm=tdr_target_ohm,
    )
    samples = [
        sample
        for sample in parse_touchstone(touchstone_path)
        if frequency_start_ghz <= sample["frequency_ghz"] <= frequency_stop_ghz
    ]
    sparameter_evidence = build_sparameter_evidence(
        trace_id=f"{job.job_id}:S11",
        samples=samples,
        artifact_ref=str(touchstone_path),
        rl_target_db=rl_target_db,
        bucket_count=bucket_count,
    )
    evidence_summary = _bounded_summary(score, sparameter_evidence)
    evidence_artifact = artifact_dir / "brd_channel_score_evidence.json"
    evidence_payload = {
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "worker_id": context.worker_id,
        "score": score,
        "sparameter_evidence": sparameter_evidence,
        "evidence_summary": evidence_summary,
    }
    evidence_artifact.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "status": "passed" if score["status"] == "pass" else "failed",
        "score": score,
        "sparameter_evidence": sparameter_evidence,
        "evidence_summary": evidence_summary,
        "evidence_artifact": str(evidence_artifact),
        "artifact_refs": [str(touchstone_path), str(tdr_path), str(evidence_artifact)],
    }


def _bounded_summary(score: dict[str, Any], sparameter_evidence: dict[str, Any]) -> dict[str, Any]:
    spectral_summary = dict(sparameter_evidence["summary"])
    spectral_summary.pop("buckets", None)
    return {
        "status": score["status"],
        "frequency_start_ghz": score["frequency_start_ghz"],
        "frequency_stop_ghz": score["frequency_stop_ghz"],
        "rl_target_db": score["rl_target_db"],
        "rl_worst_db": score["rl_worst_db"],
        "rl_worst_frequency_ghz": score["rl_worst_frequency_ghz"],
        "rl_pass_band": score["rl_pass_band"],
        "tdr_target_ohm": score["tdr_target_ohm"],
        "tdr_peak_deviation_ohm": score["tdr_peak_deviation_ohm"],
        "tdr_peak_time_ps": score["tdr_peak_time_ps"],
        "tdr_anomaly_window": score["tdr_anomaly_window"],
        "samples": score["samples"],
        "spectral_summary": spectral_summary,
        "raw_sparameters": "artifact_only",
        "raw_tdr": "artifact_only",
        "artifact_refs": list(sparameter_evidence["artifact_refs"]),
    }
