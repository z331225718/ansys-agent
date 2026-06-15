from __future__ import annotations

import argparse
import importlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers import WorkerContext
from aedt_agent.infrastructure.harness.contracts import (
    HARNESS_PROTOCOL_VERSION,
    HarnessError,
    HarnessProtocolError,
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
)


def run(request_path: Path | str) -> int:
    path = Path(request_path).resolve()
    request = HarnessRequest.from_json_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )
    workspace = Path(request.workspace).resolve()
    if path.parent != workspace:
        raise HarnessProtocolError(
            f"request path is outside declared workspace: {path.parent} != {workspace}"
        )
    result_path = workspace / "result.json"
    heartbeat_path = workspace / "heartbeat.json"
    started_at = _utc_now()
    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(request, heartbeat_path, stop_heartbeat),
        name=f"harness-heartbeat-{request.harness_run_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    exit_code = 0
    try:
        worker = _load_entrypoint(request.entrypoint)
        job = JobRecord.create(
            request.job_id,
            request.mission_id,
            request.capability,
            f"harness:{request.attempt_id}",
            dict(request.input_payload),
            request.timeout_seconds,
            0,
        )
        artifacts_dir = (workspace / "artifacts").resolve()
        if not artifacts_dir.is_dir():
            raise HarnessProtocolError(
                f"harness artifacts directory does not exist: {artifacts_dir}"
            )
        output = worker(
            job,
            WorkerContext(
                request.worker_id,
                workspace=str(workspace),
                artifacts_dir=str(artifacts_dir),
            ),
        )
        if not isinstance(output, dict):
            raise TypeError("worker entrypoint must return a dict")
        normalized = dict(output)
        artifact_refs = normalized.pop("artifact_refs", [])
        if not isinstance(artifact_refs, list) or not all(
            isinstance(item, str) for item in artifact_refs
        ):
            raise TypeError("worker artifact_refs must be a list of strings")
        result = HarnessResult.create(
            harness_run_id=request.harness_run_id,
            job_id=request.job_id,
            status=HarnessStatus.SUCCEEDED,
            output_payload=normalized,
            artifact_refs=artifact_refs,
            started_at=started_at,
            completed_at=_utc_now(),
            exit_code=0,
        )
    except Exception as exc:
        exit_code = 1
        error_class = (
            "invalid_input"
            if isinstance(exc, (ImportError, AttributeError, HarnessProtocolError))
            else "worker_crash"
        )
        result = HarnessResult.create(
            harness_run_id=request.harness_run_id,
            job_id=request.job_id,
            status=HarnessStatus.FAILED,
            error=HarnessError(
                error_class=error_class,
                message=str(exc),
                retryable=error_class == "worker_crash",
                details={"error_type": type(exc).__name__},
            ),
            started_at=started_at,
            completed_at=_utc_now(),
            exit_code=exit_code,
            termination_reason="entrypoint_error",
        )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=max(request.heartbeat_interval_seconds * 2, 1))
    _atomic_write_json(result_path, result.to_json_dict())
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aedt-agent-harness-child")
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.request)


def _load_entrypoint(entrypoint: str) -> Callable[[JobRecord, WorkerContext], dict[str, Any]]:
    module_name, separator, function_name = entrypoint.partition(":")
    if not separator or not module_name or not function_name:
        raise ImportError(f"invalid worker entrypoint: {entrypoint}")
    module = importlib.import_module(module_name)
    worker = getattr(module, function_name)
    if not callable(worker):
        raise TypeError(f"worker entrypoint is not callable: {entrypoint}")
    return worker


def _heartbeat_loop(
    request: HarnessRequest,
    heartbeat_path: Path,
    stop: threading.Event,
) -> None:
    while True:
        _atomic_write_json(
            heartbeat_path,
            {
                "protocol_version": HARNESS_PROTOCOL_VERSION,
                "harness_run_id": request.harness_run_id,
                "job_id": request.job_id,
                "pid": os.getpid(),
                "updated_at": _utc_now(),
            },
        )
        if stop.wait(request.heartbeat_interval_seconds):
            return


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
