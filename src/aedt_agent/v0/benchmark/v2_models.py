from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AttemptResult:
    attempt: int
    code_path: str
    prompt_path: str
    exec_log_path: str
    validation_log_path: str
    generation_ok: bool
    execution_ok: bool
    validation_ok: bool
    harness_stdout_path: str = ""
    harness_stderr_path: str = ""
    transcript_path: str = ""
    tool_usage_path: str = ""
    tool_usage: dict[str, Any] = field(default_factory=dict)
    failure_type: str = ""
    error_summary: str = ""
    elapsed_seconds: float = 0.0

    @property
    def final_pass(self) -> bool:
        return self.generation_ok and self.execution_ok and self.validation_ok

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["final_pass"] = self.final_pass
        return data


@dataclass
class GroupRunResult:
    group: str
    attempts: list[AttemptResult] = field(default_factory=list)

    @property
    def final_pass(self) -> bool:
        return any(attempt.final_pass for attempt in self.attempts)

    @property
    def success_on_attempt(self) -> int | None:
        for attempt in self.attempts:
            if attempt.final_pass:
                return attempt.attempt
        return None

    @property
    def failure_type(self) -> str:
        if self.final_pass:
            return ""
        if not self.attempts:
            return "not_run"
        return self.attempts[-1].failure_type or "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_pass": self.final_pass,
            "success_on_attempt": self.success_on_attempt,
            "failure_type": self.failure_type,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def compute_group_metrics(group_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(group_results)
    if total == 0:
        return {
            "task_count": 0,
            "first_pass_rate": 0.0,
            "pass_rate_3try": 0.0,
            "avg_attempts_to_success": 0.0,
            "avg_attempts_all": 0.0,
            "failure_categories": {},
            "tool_usage_rate": 0.0,
            "avg_gitnexus_queries": 0.0,
            "retrieval_before_code_rate": 0.0,
        }

    success_attempts = [
        int(result["success_on_attempt"])
        for result in group_results
        if result.get("success_on_attempt") is not None
    ]
    failure_categories: dict[str, int] = {}
    for result in group_results:
        if result.get("final_pass"):
            continue
        failure_type = str(result.get("failure_type") or "unknown")
        failure_categories[failure_type] = failure_categories.get(failure_type, 0) + 1

    attempt_counts = [len(result.get("attempts", [])) for result in group_results]
    per_task_tool_usage = [_combine_tool_usage(result.get("attempts", [])) for result in group_results]
    return {
        "task_count": total,
        "first_pass_rate": sum(1 for value in success_attempts if value == 1) / total,
        "pass_rate_3try": len(success_attempts) / total,
        "avg_attempts_to_success": (sum(success_attempts) / len(success_attempts)) if success_attempts else 0.0,
        "avg_attempts_all": sum(attempt_counts) / total,
        "failure_categories": failure_categories,
        "tool_usage_rate": sum(1 for usage in per_task_tool_usage if usage.get("used_tools")) / total,
        "avg_gitnexus_queries": sum(int(usage.get("gitnexus_query_count", 0)) for usage in per_task_tool_usage) / total,
        "retrieval_before_code_rate": sum(1 for usage in per_task_tool_usage if usage.get("retrieval_before_code")) / total,
    }


def _combine_tool_usage(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    combined = {
        "used_tools": False,
        "gitnexus_query_count": 0,
        "gitnexus_context_count": 0,
        "retrieval_before_code": False,
    }
    for attempt in attempts:
        usage = attempt.get("tool_usage", {}) or {}
        combined["used_tools"] = combined["used_tools"] or bool(usage.get("used_tools"))
        combined["gitnexus_query_count"] += int(usage.get("gitnexus_query_count", 0))
        combined["gitnexus_context_count"] += int(usage.get("gitnexus_context_count", 0))
        combined["retrieval_before_code"] = combined["retrieval_before_code"] or bool(usage.get("retrieval_before_code"))
    return combined
