from __future__ import annotations

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
    assert profile.execution_mode == "recorded"


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
