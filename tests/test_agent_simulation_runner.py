from __future__ import annotations

import json
from pathlib import Path

from aedt_agent.infrastructure.harness import (
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
    HarnessWorkspacePolicy,
)


def _request(workspace: Path) -> HarnessRequest:
    return HarnessRequest.create(
        harness_run_id="run-1",
        mission_id="mission-1",
        job_id="job-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        capability="fake.echo",
        entrypoint="tests.fixtures.process_workers:echo_worker",
        timeout_seconds=30,
        heartbeat_interval_seconds=1,
        input_payload={"value": 41},
        workspace=str(workspace),
    )


class FakeSshTransport:
    def __init__(self) -> None:
        self.created_dirs: list[str] = []
        self.uploads: list[tuple[Path, str]] = []
        self.commands: list[dict] = []
        self.downloads: list[tuple[str, Path]] = []

    def mkdir(self, remote_path: str) -> None:
        self.created_dirs.append(remote_path)

    def upload(self, local_path: Path, remote_path: str) -> None:
        self.uploads.append((Path(local_path), remote_path))

    def run(
        self,
        command: list[str],
        *,
        cwd: str | None,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> None:
        self.commands.append(
            {
                "command": list(command),
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "env": dict(env or {}),
            }
        )

    def download(self, remote_path: str, local_path: Path) -> None:
        self.downloads.append((remote_path, Path(local_path)))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if remote_path.endswith("result.json"):
            local_path.write_text(
                json.dumps(
                    HarnessResult.create(
                        harness_run_id="run-1",
                        job_id="job-1",
                        status=HarnessStatus.SUCCEEDED,
                        output_payload={"value": 42},
                        artifact_refs=[
                            r"D:\aedt-agent-runs\mission-1\job-1\attempt-1\artifacts\out.json"
                        ],
                    ).to_json_dict()
                ),
                encoding="utf-8",
            )
        else:
            local_path.write_text("artifact", encoding="utf-8")


def test_ssh_cli_runner_rewrites_workspace_and_runs_remote_child(tmp_path):
    from aedt_agent.agent.workers.simulation_runner import (
        SshCliRunner,
        SshCliRunnerConfig,
    )

    workspace = tmp_path / "local" / "mission-1" / "job-1" / "attempt-1"
    workspace.mkdir(parents=True)
    transport = FakeSshTransport()
    runner = SshCliRunner(
        HarnessWorkspacePolicy(tmp_path / "local"),
        SshCliRunnerConfig(
            host="192.168.71.51",
            user="z3312",
            identity_file=r"C:\Users\z3312\.ssh\ansys_agent_ed25519",
            remote_root=r"D:\aedt-agent-runs",
            python="python",
            repo_root=r"D:\ansys-agent",
        ),
        transport=transport,
    )

    result = runner.submit(
        _request(workspace),
        allowed_env=("PYTHONPATH",),
        resource_classes=("license", "aedt"),
        cancel_requested=None,
    )

    request_payload = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
    assert request_payload["workspace"] == r"D:\aedt-agent-runs\mission-1\job-1\attempt-1"
    assert result.status == HarnessStatus.SUCCEEDED
    assert result.output_payload == {"value": 42}
    assert transport.created_dirs == [
        r"D:\aedt-agent-runs\mission-1\job-1\attempt-1",
        r"D:\aedt-agent-runs\mission-1\job-1\attempt-1\artifacts",
    ]
    assert transport.uploads[0][1].endswith(r"\request.json")
    assert transport.commands[0]["command"] == [
        "python",
        "-m",
        "aedt_agent.infrastructure.harness.child_main",
        "--request",
        r"D:\aedt-agent-runs\mission-1\job-1\attempt-1\request.json",
    ]
    assert transport.commands[0]["cwd"] == r"D:\ansys-agent"
    assert transport.commands[0]["timeout_seconds"] == 330

    fetched = runner.fetch(
        r"D:\aedt-agent-runs\mission-1\job-1\attempt-1\artifacts\out.json",
        tmp_path / "fetched" / "out.json",
    )
    assert fetched.read_text(encoding="utf-8") == "artifact"


def test_ssh_cli_runner_forwards_allowed_environment_to_remote_command(tmp_path, monkeypatch):
    from aedt_agent.agent.workers.simulation_runner import (
        SshCliRunner,
        SshCliRunnerConfig,
    )

    monkeypatch.setenv("PYTHONPATH", "src")
    workspace = tmp_path / "local" / "mission-1" / "job-1" / "attempt-1"
    workspace.mkdir(parents=True)
    transport = FakeSshTransport()
    runner = SshCliRunner(
        HarnessWorkspacePolicy(tmp_path / "local"),
        SshCliRunnerConfig(
            host="192.168.71.51",
            user="z3312",
            remote_root=r"D:\aedt-agent-runs",
            python="python",
            repo_root=r"D:\ansys-agent",
        ),
        transport=transport,
    )

    runner.submit(
        _request(workspace),
        allowed_env=("PYTHONPATH",),
        resource_classes=("cpu",),
        cancel_requested=None,
    )

    assert transport.commands[0]["env"] == {"PYTHONPATH": "src"}
