from __future__ import annotations

import json
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
    ):
        self.workspace_policy = workspace_policy
        self.resource_gate = resource_gate or ResourceGate()

    def execute(
        self,
        request: HarnessRequest,
        *,
        allowed_env: tuple[str, ...] | list[str] = (),
        resource_class: str = "cpu",
    ) -> HarnessResult:
        workspace = self._workspace_for_request(request)
        workspace.request_path.write_text(
            json.dumps(request.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        started_at = _utc_now()
        try:
            lease = self.resource_gate.acquire(
                resource_class,
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
                metadata={"resource_class": resource_class},
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
                )
                exit_code = process.wait()

        metadata = {
            "workspace": str(workspace.root),
            "request_path": str(workspace.request_path),
            "result_path": str(workspace.result_path),
            "heartbeat_path": str(workspace.heartbeat_path),
            "stdout_path": str(workspace.stdout_path),
            "stderr_path": str(workspace.stderr_path),
            "resource_class": resource_class,
            "resource_wait_seconds": lease.waited_seconds,
            "pid": process.pid,
        }
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
        return HarnessResult.create(
            harness_run_id=request.harness_run_id,
            job_id=request.job_id,
            status=(
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


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
