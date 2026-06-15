from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aedt_agent.infrastructure.harness.contracts import (
    HarnessError,
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
)
from aedt_agent.infrastructure.harness.resources import (
    ResourceAcquireTimeout,
    ResourceGate,
)
from aedt_agent.infrastructure.harness.workspace import (
    HarnessWorkspace,
    HarnessWorkspaceError,
    HarnessWorkspacePolicy,
    build_child_environment,
)


class LocalProcessHarness:
    def __init__(
        self,
        workspace_policy: HarnessWorkspacePolicy,
        *,
        resource_gate: ResourceGate | None = None,
        process_controller: "ProcessTreeController | None" = None,
        heartbeat_timeout_seconds: int = 30,
        termination_grace_seconds: float = 2.0,
        poll_interval_seconds: float = 0.05,
    ):
        self.workspace_policy = workspace_policy
        self.resource_gate = resource_gate or ResourceGate()
        self.process_controller = process_controller or ProcessTreeController()
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.termination_grace_seconds = termination_grace_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def execute(
        self,
        request: HarnessRequest,
        *,
        allowed_env: tuple[str, ...] | list[str] = (),
        resource_classes: tuple[str, ...] | list[str] | None = None,
        resource_class: str | None = None,
        cancel_requested=None,
    ) -> HarnessResult:
        if resource_classes is not None and resource_class is not None:
            raise ValueError(
                "provide resource_classes or resource_class, not both"
            )
        selected_resources = (
            (resource_class,)
            if resource_class is not None
            else tuple(resource_classes or ("cpu",))
        )
        execution_started = time.monotonic()
        workspace = self._workspace_for_request(request)
        workspace.request_path.write_text(
            json.dumps(request.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        started_at = _utc_now()
        try:
            lease = self.resource_gate.acquire_many(
                selected_resources,
                timeout_seconds=request.timeout_seconds,
            )
        except ResourceAcquireTimeout as exc:
            return self._failure_result(
                request,
                workspace,
                started_at=started_at,
                error_class="timeout",
                message=str(exc),
                retryable=True,
                termination_reason="resource_timeout",
                metadata={
                    "resource_classes": list(selected_resources),
                },
            )

        with lease:
            environment = build_child_environment(allowed_env)
            with workspace.stdout_path.open("w", encoding="utf-8") as stdout_handle, workspace.stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "aedt_agent.infrastructure.harness.child_main",
                        "--request",
                        str(workspace.request_path),
                    ],
                    cwd=workspace.root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    shell=False,
                    **self.process_controller.popen_options(),
                )
                termination_status: HarnessStatus | None = None
                termination_reason = ""
                while process.poll() is None:
                    if cancel_requested is not None and cancel_requested():
                        termination_status = HarnessStatus.CANCELED
                        termination_reason = "cancel_requested"
                        break
                    if time.monotonic() - execution_started >= request.timeout_seconds:
                        termination_status = HarnessStatus.TIMED_OUT
                        termination_reason = "wall_timeout"
                        break
                    if (
                        workspace.heartbeat_path.exists()
                        and time.time() - workspace.heartbeat_path.stat().st_mtime
                        > self.heartbeat_timeout_seconds
                    ):
                        termination_status = HarnessStatus.INTERRUPTED
                        termination_reason = "heartbeat_timeout"
                        break
                    time.sleep(self.poll_interval_seconds)
                if termination_status is not None:
                    self.process_controller.terminate_tree(
                        process,
                        self.termination_grace_seconds,
                    )
                    exit_code = process.poll()
                else:
                    exit_code = process.wait()

        metadata = {
            "workspace": str(workspace.root),
            "request_path": str(workspace.request_path),
            "result_path": str(workspace.result_path),
            "heartbeat_path": str(workspace.heartbeat_path),
            "stdout_path": str(workspace.stdout_path),
            "stderr_path": str(workspace.stderr_path),
            "resource_classes": list(lease.resource_classes),
            "resource_wait_seconds": lease.waited_seconds,
            "pid": process.pid,
        }
        if termination_status is not None:
            error_class = (
                "canceled"
                if termination_status == HarnessStatus.CANCELED
                else "timeout"
                if termination_status == HarnessStatus.TIMED_OUT
                else "worker_crash"
            )
            return self._failure_result(
                request,
                workspace,
                started_at=started_at,
                status=termination_status,
                error_class=error_class,
                message=f"harness execution terminated: {termination_reason}",
                retryable=termination_status != HarnessStatus.CANCELED,
                exit_code=exit_code,
                termination_reason=termination_reason,
                metadata=metadata,
            )
        if not workspace.result_path.exists():
            return self._failure_result(
                request,
                workspace,
                started_at=started_at,
                error_class="worker_crash",
                message=f"child process exited without result.json (exit_code={exit_code})",
                retryable=True,
                exit_code=exit_code,
                termination_reason="missing_result",
                metadata=metadata,
            )
        try:
            result = HarnessResult.from_json_dict(
                json.loads(workspace.result_path.read_text(encoding="utf-8"))
            )
            result.assert_identity(request.harness_run_id, request.job_id)
        except Exception as exc:
            return self._failure_result(
                request,
                workspace,
                started_at=started_at,
                error_class="worker_crash",
                message=str(exc),
                retryable=True,
                exit_code=exit_code,
                termination_reason="invalid_result",
                metadata=metadata,
            )
        return replace(
            result,
            exit_code=exit_code,
            artifact_refs=_unique(
                [*result.artifact_refs, *workspace.protocol_artifacts()]
            ),
            metadata={**result.metadata, **metadata},
        )

    def _workspace_for_request(self, request: HarnessRequest) -> HarnessWorkspace:
        root = Path(request.workspace).resolve()
        expected = self.workspace_policy.root.joinpath(
            request.mission_id,
            request.job_id,
            request.attempt_id,
        ).resolve()
        if root != expected or not root.is_relative_to(self.workspace_policy.root):
            raise HarnessWorkspaceError(
                f"request workspace does not match attempt identity: {root} != {expected}"
            )
        if not root.is_dir():
            raise HarnessWorkspaceError(f"request workspace does not exist: {root}")
        return HarnessWorkspace(
            root=root,
            request_path=root / "request.json",
            result_path=root / "result.json",
            heartbeat_path=root / "heartbeat.json",
            stdout_path=root / "stdout.log",
            stderr_path=root / "stderr.log",
            artifacts_dir=root / "artifacts",
        )

    def _failure_result(
        self,
        request: HarnessRequest,
        workspace: HarnessWorkspace,
        *,
        started_at: str,
        status: HarnessStatus | None = None,
        error_class: str,
        message: str,
        retryable: bool,
        termination_reason: str,
        exit_code: int | None = None,
        metadata: dict | None = None,
    ) -> HarnessResult:
        existing_artifacts = [
            path
            for path in workspace.protocol_artifacts()
            if Path(path).exists()
        ]
        result = HarnessResult.create(
            harness_run_id=request.harness_run_id,
            job_id=request.job_id,
            status=status
            or (
                HarnessStatus.TIMED_OUT
                if error_class == "timeout"
                else HarnessStatus.FAILED
            ),
            artifact_refs=existing_artifacts,
            error=HarnessError(
                error_class=error_class,
                message=message,
                retryable=retryable,
            ),
            started_at=started_at,
            completed_at=_utc_now(),
            exit_code=exit_code,
            termination_reason=termination_reason,
            metadata=dict(metadata or {}),
        )
        _atomic_write_json(workspace.result_path, result.to_json_dict())
        return replace(
            result,
            artifact_refs=_unique(
                [*result.artifact_refs, str(workspace.result_path)]
            ),
        )


class ProcessTreeController:
    def popen_options(self) -> dict:
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                flags |= subprocess.CREATE_NO_WINDOW
            return {"creationflags": flags}
        return {"start_new_session": True}

    def terminate_tree(
        self,
        process: subprocess.Popen,
        grace_seconds: float,
    ) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=max(grace_seconds, 0.1))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    def terminate_pid_tree(self, pid: int, grace_seconds: float) -> None:
        if not self.is_alive(pid):
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while self.is_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.is_alive(pid):
            os.killpg(pid, signal.SIGKILL)

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _atomic_write_json(path: Path, payload: dict) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
