from aedt_agent.benchmark.stage_b_models import StageBBaseline, StageBTaskResult, compute_stage_b_metrics


def test_stage_b_baseline_records_stage_a_final_b_metrics():
    baseline = StageBBaseline()

    assert baseline.first_pass_rate == 0.80
    assert baseline.pass_rate_3try == 1.0
    assert baseline.avg_attempts_to_success == 1.20


def test_compute_stage_b_metrics_tracks_node_plan_quality():
    metrics = compute_stage_b_metrics(
        [
            StageBTaskResult(
                task_id="T1",
                final_pass=True,
                success_on_attempt=1,
                attempts=[{"attempt": 1}],
                node_steps=[{"node_id": "create_substrate"}],
            ),
            {
                "task_id": "T2",
                "final_pass": False,
                "success_on_attempt": None,
                "attempts": [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
                "node_steps": [],
                "unsupported": True,
                "failure_type": "unsupported_node_coverage",
                "free_code_execution_count": 0,
            },
        ]
    )

    assert metrics["task_count"] == 2
    assert metrics["first_pass_rate"] == 0.5
    assert metrics["pass_rate_3try"] == 0.5
    assert metrics["node_coverage_rate"] == 0.5
    assert metrics["unsupported_task_count"] == 1
    assert metrics["free_code_execution_count"] == 0
    assert metrics["failure_categories"] == {"unsupported_node_coverage": 1}
