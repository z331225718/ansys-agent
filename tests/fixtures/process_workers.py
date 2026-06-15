from __future__ import annotations

import sys
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
