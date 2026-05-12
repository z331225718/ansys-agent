from aedt_agent.benchmark.go_nogo import compute_go_nogo


def test_compute_go_nogo_passes_when_metrics_met():
    report = {
        "tasks": {
            "L1_create_substrate": {
                "A": {"syntax_pass": True, "api_pass": True, "semantic_lite_pass": False},
                "B": {"syntax_pass": True, "api_pass": True, "semantic_lite_pass": True},
                "C": {"syntax_pass": True, "api_pass": True, "semantic_lite_pass": True},
            },
        }
    }
    result = compute_go_nogo(report)
    assert result["metrics"]["semantic_pass_rate_c"] >= 0.70
