from __future__ import annotations

import json
from pathlib import Path


def run_fake_real_solve_worker(job, context):
    artifact_dir = Path(context.artifacts_dir)
    project_checkpoint = artifact_dir / "approved.aedt"
    solved_project = artifact_dir / "approved.solved.aedt"
    touchstone = artifact_dir / "channel.s2p"
    tdr = artifact_dir / "ChannelTDR.csv"
    manifest = artifact_dir / "solve_manifest.json"
    project_checkpoint.write_text(
        "approved project",
        encoding="utf-8",
    )
    solved_project.write_text("solved project", encoding="utf-8")
    touchstone.write_text(
        "# GHz S MA R 50\n"
        "0 0.05 0 0.9 0 0.9 0 0.05 0\n"
        "18 0.45 0 0.8 0 0.8 0 0.05 0\n",
        encoding="utf-8",
    )
    tdr.write_text(
        "time_ps,impedance_ohm\n0,100\n10,105\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps({"status": "succeeded"}),
        encoding="utf-8",
    )
    refs = [
        str(project_checkpoint),
        str(solved_project),
        str(touchstone),
        str(tdr),
        str(manifest),
    ]
    return {
        "status": "succeeded",
        "solve_summary": {
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
        },
        "touchstone_path": str(touchstone),
        "tdr_path": str(tdr),
        "solve_manifest": str(manifest),
        "artifact_dir": str(artifact_dir),
        "frequency_start_ghz": float(
            job.input_payload.get("frequency_start_ghz", 0.0)
        ),
        "frequency_stop_ghz": float(
            job.input_payload.get("frequency_stop_ghz", 67.0)
        ),
        "rl_target_db": float(
            job.input_payload.get("rl_target_db", -20.0)
        ),
        "tdr_target_ohm": float(
            job.input_payload.get("tdr_target_ohm", 100.0)
        ),
        "evidence_summary": {
            "status": "solve_completed",
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
            "artifact_refs": refs,
        },
        "artifact_refs": refs,
    }
