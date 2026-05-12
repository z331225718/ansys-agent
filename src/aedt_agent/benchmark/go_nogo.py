from __future__ import annotations


def compute_go_nogo(report: dict) -> dict:
    tasks = report.get("tasks", {})
    totals = {
        "group_c": 0,
        "api_c": 0,
        "semantic_b": 0,
        "semantic_c": 0,
        "trap_total": 0,
        "trap_captured": 0,
    }

    for task_id, groups in tasks.items():
        if "C" in groups:
            totals["group_c"] += 1
            totals["api_c"] += 1 if groups["C"].get("api_pass") else 0
            totals["semantic_c"] += 1 if groups["C"].get("semantic_lite_pass") else 0
        if "B" in groups:
            totals["semantic_b"] += 1 if groups["B"].get("semantic_lite_pass") else 0
        if task_id.startswith("Trap_"):
            totals["trap_total"] += 1
            totals["trap_captured"] += 1 if groups.get("C", {}).get("semantic_lite_pass") else 0

    group_c = max(totals["group_c"], 1)
    metrics = {
        "api_pass_rate_c": totals["api_c"] / group_c,
        "semantic_pass_rate_b": totals["semantic_b"] / group_c,
        "semantic_pass_rate_c": totals["semantic_c"] / group_c,
        "semantic_lift": (totals["semantic_c"] - totals["semantic_b"]) / group_c,
        "trap_capture_rate": totals["trap_captured"] / max(totals["trap_total"], 1),
    }
    return {
        "metrics": metrics,
        "go": metrics["api_pass_rate_c"] >= 0.85 and metrics["semantic_pass_rate_c"] >= 0.70,
    }
