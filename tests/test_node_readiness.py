from aedt_agent.benchmark.node_readiness import compute_node_readiness


def test_compute_node_readiness_returns_candidate():
    report = {
        "tasks": {
            "task1": {
                "metadata": {"allowed_nodes": ["create_setup"]},
                "C": {"semantic_lite_pass": True, "passed": True},
            },
            "task2": {
                "metadata": {"allowed_nodes": ["create_setup"]},
                "C": {"semantic_lite_pass": True, "passed": True},
            },
            "task3": {
                "metadata": {"allowed_nodes": ["create_setup"]},
                "C": {"semantic_lite_pass": True, "passed": True},
            },
        }
    }
    readiness = compute_node_readiness(report)
    assert "create_setup" in readiness["candidate_ready"]
