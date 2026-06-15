from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


class ExecutionProfileError(ValueError):
    """Raised when an execution profile is unsafe or malformed."""


@dataclass(frozen=True)
class ExecutionProfile:
    profile_id: str
    max_iterations: int
    max_job_attempts: int
    max_wall_seconds: int
    max_evidence_query_calls: int
    max_evidence_tokens: int
    max_consecutive_no_improvement: int
    max_duplicate_actions: int
    retry_backoff_seconds: list[int]
    solve_timeout_seconds: int
    max_concurrent_aedt: int
    max_concurrent_license_jobs: int
    allow_real_aedt: bool
    execution_mode: str
    harness_root: str
    heartbeat_interval_seconds: int
    heartbeat_timeout_seconds: int
    termination_grace_seconds: int
    allowed_env: list[str]

    def __post_init__(self) -> None:
        positive_fields = (
            "max_iterations",
            "max_job_attempts",
            "max_wall_seconds",
            "max_evidence_query_calls",
            "max_evidence_tokens",
            "max_consecutive_no_improvement",
            "max_duplicate_actions",
            "solve_timeout_seconds",
            "max_concurrent_aedt",
            "max_concurrent_license_jobs",
            "heartbeat_interval_seconds",
            "heartbeat_timeout_seconds",
            "termination_grace_seconds",
        )
        for field_name in positive_fields:
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ExecutionProfileError(f"{field_name} must be a positive integer")
        if not self.profile_id.strip():
            raise ExecutionProfileError("profile_id is required")
        if not self.harness_root.strip():
            raise ExecutionProfileError("harness_root is required")
        if self.execution_mode not in {"recorded", "local", "container"}:
            raise ExecutionProfileError(f"execution_mode is unsupported: {self.execution_mode}")
        if not isinstance(self.allow_real_aedt, bool):
            raise ExecutionProfileError("allow_real_aedt must be boolean")
        if not self.retry_backoff_seconds:
            raise ExecutionProfileError("retry_backoff_seconds must not be empty")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self.retry_backoff_seconds
        ):
            raise ExecutionProfileError("retry_backoff_seconds must contain non-negative integers")
        if self.heartbeat_timeout_seconds <= self.heartbeat_interval_seconds:
            raise ExecutionProfileError(
                "heartbeat_timeout_seconds must be greater than heartbeat_interval_seconds"
            )
        if not isinstance(self.allowed_env, list):
            raise ExecutionProfileError("allowed_env must be a list")
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        if any(not isinstance(name, str) or not pattern.fullmatch(name) for name in self.allowed_env):
            raise ExecutionProfileError("allowed_env must contain valid environment variable names")

    @classmethod
    def safe_recorded(cls) -> "ExecutionProfile":
        return cls(
            profile_id="safe-recorded",
            max_iterations=12,
            max_job_attempts=16,
            max_wall_seconds=3600,
            max_evidence_query_calls=24,
            max_evidence_tokens=24000,
            max_consecutive_no_improvement=3,
            max_duplicate_actions=2,
            retry_backoff_seconds=[0, 5, 30],
            solve_timeout_seconds=900,
            max_concurrent_aedt=1,
            max_concurrent_license_jobs=1,
            allow_real_aedt=False,
            execution_mode="recorded",
            harness_root="harness",
            heartbeat_interval_seconds=5,
            heartbeat_timeout_seconds=30,
            termination_grace_seconds=2,
            allowed_env=[
                "PYTHONPATH",
                "AWP_ROOT261",
                "ANSYSEM_ROOT261",
                "LM_LICENSE_FILE",
                "CDSROOT",
                "CDS_LIC_FILE",
            ],
        )

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> "ExecutionProfile":
        known_fields = set(cls.__dataclass_fields__)
        unknown = sorted(set(payload) - known_fields)
        if unknown:
            raise ExecutionProfileError(f"unknown profile fields: {', '.join(unknown)}")
        missing = sorted(known_fields - set(payload))
        if missing:
            raise ExecutionProfileError(f"missing profile fields: {', '.join(missing)}")
        return cls(**payload)

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)
