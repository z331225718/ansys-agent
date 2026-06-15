from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext
from aedt_agent.layout.channel_scoring import compare_channel_scores


BRD_EVIDENCE_COMPARE_CAPABILITY = "brd.evidence.compare"


def build_evidence_compare_job_input(
    *,
    before_score_path: str | Path,
    after_score_path: str | Path,
    artifact_dir: str | Path,
) -> dict[str, Any]:
    return {
        "before_score_path": str(before_score_path),
        "after_score_path": str(after_score_path),
        "artifact_dir": str(artifact_dir),
    }


def run_evidence_compare_worker(job: JobRecord, context: WorkerContext) -> dict[str, Any]:
    payload = dict(job.input_payload)
    before_path = Path(str(payload["before_score_path"]))
    after_path = Path(str(payload["after_score_path"]))
    for label, path in (("before", before_path), ("after", after_path)):
        if not path.exists():
            raise FileNotFoundError(f"{label}_score_path does not exist: {path}")

    artifact_dir = Path(str(payload["artifact_dir"]))
    artifact_dir.mkdir(parents=True, exist_ok=True)

    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))

    before_score = before.get("score", before)
    after_score = after.get("score", after)
    comparison = compare_channel_scores(before_score, after_score)

    comparison_artifact = artifact_dir / "before_after_comparison.json"
    comparison_payload = {
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "worker_id": context.worker_id,
        "comparison": comparison,
    }
    comparison_artifact.write_text(
        json.dumps(comparison_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    evidence_summary = {
        "status": comparison["status"],
        "rl_worst_delta_db": comparison["rl_worst_delta_db"],
        "tdr_peak_deviation_delta_ohm": comparison["tdr_peak_deviation_delta_ohm"],
        "summary": comparison["summary"],
        "raw_sparameters": "artifact_only",
        "raw_tdr": "artifact_only",
    }

    return {
        "status": "improved" if comparison["status"] == "improved" else "not_improved",
        "comparison": comparison,
        "evidence_summary": evidence_summary,
        "comparison_artifact": str(comparison_artifact),
        "artifact_refs": [str(before_path), str(after_path), str(comparison_artifact)],
    }
