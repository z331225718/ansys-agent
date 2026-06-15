from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def echo_worker(job, context):
    return {"value": int(job.input_payload["value"]) + 1}


def artifact_worker(job, context):
    artifact = Path(job.input_payload["artifact_path"])
    artifact.write_text("artifact", encoding="utf-8")
    return {"value": 1, "artifact_refs": [str(artifact)]}


def logging_worker(job, context):
    print("worker stdout", flush=True)
    print("worker stderr", file=sys.stderr, flush=True)
    return {"worker_id": context.worker_id}


def failing_worker(job, context):
    raise RuntimeError("fixture worker failed")


def abrupt_exit_worker(job, context):
    os._exit(7)


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
