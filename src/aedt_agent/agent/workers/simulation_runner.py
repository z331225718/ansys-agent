from __future__ import annotations

import json
import os
import shutil
import subprocess
import base64
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from aedt_agent.infrastructure.harness import (
    HarnessProtocolError,
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
    HarnessWorkspacePolicy,
    LocalProcessHarness,
)


class SimulationRunner(Protocol):
    workspace_policy: HarnessWorkspacePolicy

    def submit(
        self,
        request: HarnessRequest,
        *,
        allowed_env: tuple[str, ...] | list[str],
        resource_classes: tuple[str, ...] | list[str],
        cancel_requested,
    ) -> HarnessResult:
        ...

    def status(self, harness_run_id: str) -> HarnessStatus | None:
        ...

    def fetch(self, artifact_ref: str, destination: Path | str) -> Path:
        ...

    def cancel(self, harness_run_id: str) -> None:
        ...


class LocalCliRunner:
    def __init__(self, harness: LocalProcessHarness):
        self.harness = harness
        self.workspace_policy = harness.workspace_policy
        self._statuses: dict[str, HarnessStatus] = {}

    def submit(
        self,
        request: HarnessRequest,
        *,
        allowed_env: tuple[str, ...] | list[str],
        resource_classes: tuple[str, ...] | list[str],
        cancel_requested,
    ) -> HarnessResult:
        result = self.harness.execute(
            request,
            allowed_env=allowed_env,
            resource_classes=resource_classes,
            cancel_requested=cancel_requested,
        )
        self._statuses[result.harness_run_id] = result.status
        return result

    def status(self, harness_run_id: str) -> HarnessStatus | None:
        return self._statuses.get(harness_run_id)

    def fetch(self, artifact_ref: str, destination: Path | str) -> Path:
        source = Path(artifact_ref)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def cancel(self, harness_run_id: str) -> None:
        self._statuses[harness_run_id] = HarnessStatus.CANCELED


@dataclass(frozen=True)
class SshCliRunnerConfig:
    host: str
    user: str
    remote_root: str
    identity_file: str = ""
    python: str = "python"
    repo_root: str = ""
    ssh_exe: str = "ssh"
    scp_exe: str = "scp"
    remote_timeout_grace_seconds: int = 300

    def target(self) -> str:
        return f"{self.user}@{self.host}"


class SshCliTransport(Protocol):
    def mkdir(self, remote_path: str) -> None:
        ...

    def upload(self, local_path: Path, remote_path: str) -> None:
        ...

    def run(
        self,
        command: list[str],
        *,
        cwd: str | None,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> None:
        ...

    def download(self, remote_path: str, local_path: Path) -> None:
        ...


class OpenSshTransport:
    def __init__(self, config: SshCliRunnerConfig):
        self.config = config

    def mkdir(self, remote_path: str) -> None:
        script = (
            "New-Item -ItemType Directory -Force "
            f"-Path '{_ps_quote(remote_path)}' | Out-Null"
        )
        self._run_powershell(script, timeout_seconds=30)

    def upload(self, local_path: Path, remote_path: str) -> None:
        self._scp(str(local_path), f"{self.config.target()}:{_scp_remote(remote_path)}")

    def run(
        self,
        command: list[str],
        *,
        cwd: str | None,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> None:
        lines: list[str] = []
        for name, value in (env or {}).items():
            _validate_env_name(name)
            lines.append(f"$env:{name} = '{_ps_quote(value)}'")
        if cwd:
            lines.append(f"Set-Location -LiteralPath '{_ps_quote(cwd)}'")
        executable = command[0]
        args = command[1:]
        lines.append(
            "& "
            + " ".join(
                [f"'{_ps_quote(executable)}'", *[f"'{_ps_quote(arg)}'" for arg in args]]
            )
        )
        lines.append("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }")
        self._run_powershell("\n".join(lines), timeout_seconds=timeout_seconds)

    def download(self, remote_path: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._scp(f"{self.config.target()}:{_scp_remote(remote_path)}", str(local_path))

    def _ssh(self, args: list[str], *, timeout_seconds: int) -> None:
        command = [
            self.config.ssh_exe,
            *self._identity_args(),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            self.config.target(),
            *args,
        ]
        subprocess.run(command, check=True, timeout=timeout_seconds)

    def _run_powershell(self, script: str, *, timeout_seconds: int) -> None:
        script = "$ProgressPreference = 'SilentlyContinue'\n" + script
        self._ssh(
            [
                "powershell",
                "-NoProfile",
                "-EncodedCommand",
                _powershell_encoded(script),
            ],
            timeout_seconds=timeout_seconds,
        )

    def _scp(self, source: str, destination: str) -> None:
        command = [
            self.config.scp_exe,
            *self._identity_args(),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            source,
            destination,
        ]
        subprocess.run(command, check=True)

    def _identity_args(self) -> list[str]:
        return ["-i", self.config.identity_file] if self.config.identity_file else []


class SshCliRunner:
    def __init__(
        self,
        workspace_policy: HarnessWorkspacePolicy,
        config: SshCliRunnerConfig,
        *,
        transport: SshCliTransport | None = None,
    ):
        self.workspace_policy = workspace_policy
        self.config = config
        self.transport = transport or OpenSshTransport(config)
        self._statuses: dict[str, HarnessStatus] = {}

    def submit(
        self,
        request: HarnessRequest,
        *,
        allowed_env: tuple[str, ...] | list[str],
        resource_classes: tuple[str, ...] | list[str],
        cancel_requested,
    ) -> HarnessResult:
        if cancel_requested is not None and cancel_requested():
            result = HarnessResult.create(
                harness_run_id=request.harness_run_id,
                job_id=request.job_id,
                status=HarnessStatus.CANCELED,
                termination_reason="cancel_requested_before_submit",
            )
            self._statuses[request.harness_run_id] = result.status
            return result

        local_workspace = Path(request.workspace)
        local_workspace.mkdir(parents=True, exist_ok=True)
        remote_workspace = _remote_join(
            self.config.remote_root,
            request.mission_id,
            request.job_id,
            request.attempt_id,
        )
        remote_request_path = _remote_join(remote_workspace, "request.json")
        remote_artifacts = _remote_join(remote_workspace, "artifacts")
        remote_request = replace(request, workspace=remote_workspace)
        request_path = local_workspace / "request.json"
        request_path.write_text(
            json.dumps(remote_request.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

        self.transport.mkdir(remote_workspace)
        self.transport.mkdir(remote_artifacts)
        self.transport.upload(request_path, remote_request_path)
        self.transport.run(
            [
                self.config.python,
                "-m",
                "aedt_agent.infrastructure.harness.child_main",
                "--request",
                remote_request_path,
            ],
            cwd=self.config.repo_root or None,
            timeout_seconds=(
                request.timeout_seconds
                + self.config.remote_timeout_grace_seconds
            ),
            env=_selected_environment(allowed_env),
        )

        result_path = local_workspace / "result.json"
        self.transport.download(_remote_join(remote_workspace, "result.json"), result_path)
        result = HarnessResult.from_json_dict(
            json.loads(result_path.read_text(encoding="utf-8"))
        )
        result.assert_identity(request.harness_run_id, request.job_id)
        self._statuses[result.harness_run_id] = result.status
        return replace(
            result,
            metadata={
                **result.metadata,
                "runner": "ssh_remote",
                "remote_host": self.config.host,
                "remote_workspace": remote_workspace,
                "resource_classes": list(resource_classes),
                "allowed_env": list(allowed_env),
            },
        )

    def status(self, harness_run_id: str) -> HarnessStatus | None:
        return self._statuses.get(harness_run_id)

    def fetch(self, artifact_ref: str, destination: Path | str) -> Path:
        target = Path(destination)
        self.transport.download(artifact_ref, target)
        return target

    def cancel(self, harness_run_id: str) -> None:
        self._statuses[harness_run_id] = HarnessStatus.CANCELED


def _remote_join(root: str, *parts: str) -> str:
    separator = "\\" if "\\" in root or ":" in root else "/"
    value = root.rstrip("\\/")
    for part in parts:
        cleaned = str(part).strip("\\/")
        if not cleaned:
            raise HarnessProtocolError("remote path segment is empty")
        value = f"{value}{separator}{cleaned}"
    return value


def _validate_env_name(name: str) -> None:
    if (
        not name
        or not name.replace("_", "A").isalnum()
        or name[0].isdigit()
    ):
        raise HarnessProtocolError(f"invalid remote environment name: {name}")


def _selected_environment(allowed_env: tuple[str, ...] | list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in allowed_env:
        if name in os.environ:
            result[str(name)] = str(os.environ[name])
    return result


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _powershell_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _scp_remote(value: str) -> str:
    normalized = value.replace("\\", "/")
    if any(char.isspace() for char in normalized):
        return '"' + normalized.replace('"', r'\"') + '"'
    return normalized
