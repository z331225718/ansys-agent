from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StageBBaseline:
    first_pass_rate: float = 0.80
    pass_rate_3try: float = 1.0
    avg_attempts_to_success: float = 1.20


@dataclass(frozen=True)
class StageBTaskResult:
    task_id: str
    final_pass: bool
    success_on_attempt: int | None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    node_steps: list[dict[str, Any]] = field(default_factory=list)
    unsupported: bool = False
    failure_type: str = ""


def compute_stage_b_metrics(results: list[StageBTaskResult | dict[str, Any]]) -> dict[str, Any]:
    normalized = [_to_mapping(result) for result in results]
    total = len(normalized)
    if total == 0:
        return {
            "task_count": 0,
            "first_pass_rate": 0.0,
            "pass_rate_3try": 0.0,
            "avg_attempts_to_success": 0.0,
            "avg_attempts_all": 0.0,
            "avg_node_count": 0.0,
            "node_coverage_rate": 0.0,
            "unsupported_task_count": 0,
            "free_code_execution_count": 0,
            "failure_categories": {},
        }

    success_attempts = [
        int(result["success_on_attempt"])
        for result in normalized
        if result.get("success_on_attempt") is not None
    ]
    attempt_counts = [len(result.get("attempts", [])) for result in normalized]
    node_counts = [len(result.get("node_steps", [])) for result in normalized]
    unsupported_count = sum(1 for result in normalized if result.get("unsupported"))
    free_code_count = sum(int(result.get("free_code_execution_count", 0)) for result in normalized)
    failure_categories: dict[str, int] = {}
    for result in normalized:
        if result.get("final_pass"):
            continue
        failure_type = str(result.get("failure_type") or "unknown")
        failure_categories[failure_type] = failure_categories.get(failure_type, 0) + 1

    return {
        "task_count": total,
        "first_pass_rate": sum(1 for attempt in success_attempts if attempt == 1) / total,
        "pass_rate_3try": len(success_attempts) / total,
        "avg_attempts_to_success": (sum(success_attempts) / len(success_attempts)) if success_attempts else 0.0,
        "avg_attempts_all": sum(attempt_counts) / total,
        "avg_node_count": sum(node_counts) / total,
        "node_coverage_rate": sum(1 for count in node_counts if count > 0) / total,
        "unsupported_task_count": unsupported_count,
        "free_code_execution_count": free_code_count,
        "failure_categories": failure_categories,
    }


def _to_mapping(result: StageBTaskResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, StageBTaskResult):
        return {
            "task_id": result.task_id,
            "final_pass": result.final_pass,
            "success_on_attempt": result.success_on_attempt,
            "attempts": result.attempts,
            "node_steps": result.node_steps,
            "unsupported": result.unsupported,
            "failure_type": result.failure_type,
        }
    return dict(result)
