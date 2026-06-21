from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent.policies import ExecutionProfile, ExecutionProfileError


def test_safe_recorded_profile_is_bounded_and_disables_real_aedt():
    profile = ExecutionProfile.safe_recorded()

    assert profile.profile_id == "safe-recorded"
    assert profile.max_iterations == 12
    assert profile.max_job_attempts == 16
    assert profile.max_wall_seconds == 3600
    assert profile.max_concurrent_aedt == 1
    assert profile.max_concurrent_license_jobs == 1
    assert profile.allow_real_aedt is False
    assert profile.aedt_version == "2026.1"
    assert profile.aedt_non_graphical is True
    assert profile.execution_mode == "recorded"
    assert profile.simulation_runner == "local_cli"
    assert profile.harness_root == "harness"
    assert profile.heartbeat_interval_seconds == 5
    assert profile.heartbeat_timeout_seconds == 30
    assert profile.termination_grace_seconds == 2
    assert "PYTHONPATH" in profile.allowed_env
    assert "PYTHONUTF8" in profile.allowed_env
    assert "PYTHONIOENCODING" in profile.allowed_env


def test_execution_profile_round_trips_through_json_dict():
    profile = ExecutionProfile.safe_recorded()

    loaded = ExecutionProfile.from_json_dict(profile.to_json_dict())

    assert loaded == profile
    assert loaded.retry_backoff_seconds == [0, 5, 30]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_iterations", 0),
        ("max_job_attempts", -1),
        ("max_wall_seconds", 0),
        ("max_concurrent_aedt", 0),
        ("max_concurrent_license_jobs", 0),
        ("heartbeat_interval_seconds", 0),
        ("heartbeat_timeout_seconds", 0),
        ("termination_grace_seconds", 0),
        ("retry_backoff_seconds", [0, -1]),
    ],
)
def test_execution_profile_rejects_unbounded_or_negative_limits(field, value):
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload[field] = value

    with pytest.raises(ExecutionProfileError, match=field):
        ExecutionProfile.from_json_dict(payload)


def test_execution_profile_rejects_unknown_fields():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["surprise"] = True

    with pytest.raises(ExecutionProfileError, match="unknown profile fields"):
        ExecutionProfile.from_json_dict(payload)


def test_execution_profile_accepts_ssh_remote_runner_when_fields_are_set():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload.update(
        {
            "simulation_runner": "ssh_remote",
            "ssh_host": "192.168.71.51",
            "ssh_user": "z3312",
            "ssh_identity_file": r"C:\Users\z3312\.ssh\ansys_agent_ed25519",
            "ssh_remote_root": r"D:\aedt-agent-runs",
            "ssh_python": "python",
            "ssh_repo_root": r"D:\ansys-agent",
        }
    )

    profile = ExecutionProfile.from_json_dict(payload)

    assert profile.simulation_runner == "ssh_remote"
    assert profile.ssh_host == "192.168.71.51"
    assert profile.ssh_user == "z3312"
    assert profile.ssh_remote_root == r"D:\aedt-agent-runs"


def test_ssh_remote_example_profile_loads():
    path = Path("config/execution_profiles/ssh_remote.example.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    profile = ExecutionProfile.from_json_dict(payload)

    assert profile.profile_id == "ssh-remote-aedt"
    assert profile.simulation_runner == "ssh_remote"
    assert profile.allow_real_aedt is True
    assert "ANSYSLMD_LICENSE_FILE" in profile.allowed_env
    assert "PYTHONUTF8" in profile.allowed_env


def test_execution_profile_rejects_ssh_remote_runner_without_endpoint():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["simulation_runner"] = "ssh_remote"

    with pytest.raises(ExecutionProfileError, match="ssh_host"):
        ExecutionProfile.from_json_dict(payload)


def test_execution_profile_rejects_unknown_simulation_runner():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["simulation_runner"] = "http_magic"

    with pytest.raises(ExecutionProfileError, match="simulation_runner"):
        ExecutionProfile.from_json_dict(payload)


def test_execution_profile_rejects_empty_aedt_version():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["aedt_version"] = ""

    with pytest.raises(
        ExecutionProfileError,
        match="aedt_version",
    ):
        ExecutionProfile.from_json_dict(payload)


def test_execution_profile_rejects_non_boolean_aedt_mode():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["aedt_non_graphical"] = "yes"

    with pytest.raises(
        ExecutionProfileError,
        match="aedt_non_graphical",
    ):
        ExecutionProfile.from_json_dict(payload)


def test_execution_profile_rejects_heartbeat_timeout_not_greater_than_interval():
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["heartbeat_interval_seconds"] = 10
    payload["heartbeat_timeout_seconds"] = 10

    with pytest.raises(ExecutionProfileError, match="heartbeat_timeout_seconds"):
        ExecutionProfile.from_json_dict(payload)


@pytest.mark.parametrize("name", ["BAD-NAME", "A=B", "", " has_space"])
def test_execution_profile_rejects_invalid_allowed_env_names(name):
    payload = ExecutionProfile.safe_recorded().to_json_dict()
    payload["allowed_env"] = [name]

    with pytest.raises(ExecutionProfileError, match="allowed_env"):
        ExecutionProfile.from_json_dict(payload)
