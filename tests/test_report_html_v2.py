from pathlib import Path

from aedt_agent.benchmark.report_html_v2 import render_html_report_v2, write_html_report_v2


def _sample_report():
    return {
        "version": "stage_a_v2",
        "max_attempts": 3,
        "groups": {
            "A": {
                "task_count": 1,
                "first_pass_rate": 0.0,
                "pass_rate_3try": 1.0,
                "avg_attempts_to_success": 2.0,
                "avg_attempts_all": 2.0,
                "failure_categories": {},
            },
            "B": {
                "task_count": 1,
                "first_pass_rate": 1.0,
                "pass_rate_3try": 1.0,
                "avg_attempts_to_success": 1.0,
                "avg_attempts_all": 1.0,
                "failure_categories": {},
            },
        },
        "tasks": {
            "L1_create_substrate": {
                "metadata": {"level": "L1", "validation_script": "validate.py"},
                "A": {
                    "final_pass": True,
                    "success_on_attempt": 2,
                    "failure_type": "",
                    "attempts": [{"attempt": 1, "final_pass": False}, {"attempt": 2, "final_pass": True}],
                },
                "B": {
                    "final_pass": True,
                    "success_on_attempt": 1,
                    "failure_type": "",
                    "attempts": [{"attempt": 1, "final_pass": True}],
                },
            }
        },
    }


def test_render_html_report_v2_explains_a_b_and_attempt_metrics():
    html = render_html_report_v2(_sample_report(), model_name="model-under-test")

    assert "AEDT Execution Benchmark" in html
    assert "Group A" in html
    assert "Group B" in html
    assert "Success within 3 attempts" in html
    assert "Average attempts to success" in html
    assert "L1_create_substrate" in html
    assert "Node Readiness" not in html


def test_write_html_report_v2_creates_file(tmp_path):
    output = write_html_report_v2(_sample_report(), tmp_path / "report.html", model_name="model")

    assert output.exists()
    assert "model" in output.read_text(encoding="utf-8")
