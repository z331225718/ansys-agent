from __future__ import annotations


def compute_node_readiness(report: dict) -> dict:
    stats: dict[str, dict[str, int]] = {}
    for task in report.get("tasks", {}).values():
        nodes = task.get("metadata", {}).get("allowed_nodes", [])
        result = task.get("C", {})
        for node in nodes:
            item = stats.setdefault(node, {"coverage": 0, "passed": 0, "semantic": 0})
            item["coverage"] += 1
            item["passed"] += 1 if result.get("passed") else 0
            item["semantic"] += 1 if result.get("semantic_lite_pass") else 0

    candidate_ready: list[str] = []
    metrics: dict[str, dict[str, float]] = {}
    for node, item in stats.items():
        coverage = item["coverage"]
        pass_rate = item["passed"] / coverage
        semantic_rate = item["semantic"] / coverage
        metrics[node] = {
            "coverage": coverage,
            "pass_rate": pass_rate,
            "semantic_rate": semantic_rate,
        }
        if coverage >= 3 and pass_rate >= 0.85 and semantic_rate >= 0.70:
            candidate_ready.append(node)
    return {"nodes": metrics, "candidate_ready": sorted(candidate_ready)}
