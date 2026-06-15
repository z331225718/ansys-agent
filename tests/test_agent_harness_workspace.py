from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.infrastructure.harness.workspace import (
    HarnessWorkspaceError,
    HarnessWorkspacePolicy,
    build_child_environment,
)


def test_workspace_creates_fixed_attempt_layout(tmp_path):
    policy = HarnessWorkspacePolicy(tmp_path / "harness")

    workspace = policy.create_attempt("mission-1", "job-1", "attempt-1")

    assert workspace.root == (tmp_path / "harness/mission-1/job-1/attempt-1").resolve()
    assert workspace.request_path == workspace.root / "request.json"
    assert workspace.result_path == workspace.root / "result.json"
    assert workspace.heartbeat_path == workspace.root / "heartbeat.json"
    assert workspace.stdout_path == workspace.root / "stdout.log"
    assert workspace.stderr_path == workspace.root / "stderr.log"
    assert workspace.artifacts_dir.is_dir()


@pytest.mark.parametrize(
    "segment",
    ["", ".", "..", "../mission", "mission/job", r"mission\job", str(Path.cwd().anchor)],
)
def test_workspace_rejects_unsafe_path_segments(tmp_path, segment):
    policy = HarnessWorkspacePolicy(tmp_path / "harness")

    with pytest.raises(HarnessWorkspaceError, match="path segment"):
        policy.create_attempt(segment, "job", "attempt")


def test_workspace_rejects_reusing_attempt_directory(tmp_path):
    policy = HarnessWorkspacePolicy(tmp_path / "harness")
    policy.create_attempt("mission", "job", "attempt")

    with pytest.raises(HarnessWorkspaceError, match="already exists"):
        policy.create_attempt("mission", "job", "attempt")


def test_child_environment_contains_only_base_and_allowed_names(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    monkeypatch.setenv("AWP_ROOT261", r"C:\Ansys")
    monkeypatch.setenv("SECRET_TOKEN", "do-not-copy")

    env = build_child_environment(["AWP_ROOT261"])

    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["PATH"] == r"C:\Windows\System32"
    assert env["AWP_ROOT261"] == r"C:\Ansys"
    assert "SECRET_TOKEN" not in env


def test_child_environment_drops_missing_allowed_names(monkeypatch):
    monkeypatch.delenv("OPTIONAL_ENV", raising=False)

    assert "OPTIONAL_ENV" not in build_child_environment(["OPTIONAL_ENV"])


@pytest.mark.parametrize("name", ["BAD-NAME", "A=B", "", " has_space"])
def test_child_environment_rejects_invalid_variable_names(name):
    with pytest.raises(HarnessWorkspaceError, match="environment variable name"):
        build_child_environment([name])
