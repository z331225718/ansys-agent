from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from aedt_agent.agent.workers import WorkerReportedError


def echo_worker(job, context):
    return {"value": int(job.input_payload["value"]) + 1}


def artifact_worker(job, context):
    artifact = Path(job.input_payload["artifact_path"])
    artifact.write_text("artifact", encoding="utf-8")
    return {"value": 1, "artifact_refs": [str(artifact)]}


def workspace_worker(job, context):
    artifact = Path(context.artifacts_dir) / "workspace.json"
    artifact.write_text(
        json.dumps(
            {
                "workspace": context.workspace,
                "artifacts_dir": context.artifacts_dir,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "workspace": context.workspace,
        "artifacts_dir": context.artifacts_dir,
        "artifact_refs": [str(artifact)],
    }


def logging_worker(job, context):
    print("worker stdout", flush=True)
    print("worker stderr", file=sys.stderr, flush=True)
    return {"worker_id": context.worker_id}


def failing_worker(job, context):
    raise RuntimeError("fixture worker failed")


def reported_error_worker(job, context):
    raise WorkerReportedError(
        "artifact_missing",
        "touchstone was not exported",
        retryable=False,
        details={"stage": "touchstone"},
    )


def abrupt_exit_worker(job, context):
    os._exit(7)


def brd_solve_artifacts_then_abrupt_exit_worker(job, context):
    _write_brd_solve_artifacts(job, context)
    os._exit(5)


def brd_solve_artifacts_then_sleep_worker(job, context):
    _write_brd_solve_artifacts(job, context)
    time.sleep(float(job.input_payload.get("sleep_seconds", 60)))


def _write_brd_solve_artifacts(job, context):
    artifacts = Path(context.artifacts_dir)
    touchstone = artifacts / str(job.input_payload.get("touchstone_name", "channel.s4p"))
    tdr = artifacts / f"{job.input_payload.get('tdr_report_name', 'ChannelTDR')}.csv"
    solved_project = artifacts / "case.solved.aedt"
    project_checkpoint = artifacts / "case.checkpoint.aedt"
    touchstone.write_text(
        "# GHz S MA R 50\n0 0.05 0 0.9 0 0.9 0 0.05 0\n",
        encoding="utf-8",
    )
    tdr.write_text("Time [ps],TDRZ(Diff1)\n0,90\n", encoding="utf-8")
    solved_project.write_text("solved", encoding="utf-8")
    project_checkpoint.write_text("checkpoint", encoding="utf-8")
    manifest = {
        "version": 1,
        "input": {
            "project_checkpoint": {
                "path": str(project_checkpoint),
                "sha256": "fixture",
                "size_bytes": project_checkpoint.stat().st_size,
            }
        },
        "outputs": {
            "solved_project": {
                "path": str(solved_project),
                "sha256": "fixture",
                "size_bytes": solved_project.stat().st_size,
            },
            "touchstone": {
                "path": str(touchstone),
                "sha256": "fixture",
                "size_bytes": touchstone.stat().st_size,
            },
            "tdr": {
                "path": str(tdr),
                "sha256": "fixture",
                "size_bytes": tdr.stat().st_size,
            },
        },
        "summary": {
            "status": "succeeded",
            "adapter": "fixture",
            "touchstone_sample_count": 1,
            "tdr_sample_count": 1,
        },
    }
    (artifacts / "solve_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def corrupt_result_worker(job, context):
    Path("result.json").write_text("{not-json", encoding="utf-8")
    os._exit(8)


def wrong_identity_worker(job, context):
    Path("result.json").write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "harness_run_id": "wrong-run",
                "job_id": job.job_id,
                "status": "succeeded",
                "output_payload": {},
                "artifact_refs": [],
                "error": None,
                "started_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:01+00:00",
                "exit_code": 0,
                "termination_reason": "",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    os._exit(0)


def sleep_worker(job, context):
    time.sleep(float(job.input_payload.get("sleep_seconds", 60)))
    return {"finished": True}


def spawn_child_worker(job, context):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(job.input_payload["pid_path"]).write_text(str(child.pid), encoding="utf-8")
    time.sleep(60)
    return {"child_pid": child.pid}


def evidence_worker(job, context):
    artifact = Path("artifacts/evidence.json")
    artifact.write_text('{"passed": true}', encoding="utf-8")
    return {
        "status": "succeeded",
        "evidence_summary": {"source": "process_harness", "passed": True},
        "artifact_refs": [str(artifact.resolve())],
    }
