
from aedt_agent.benchmark.report_html_stage_b import render_html_report_stage_b, write_html_report_stage_b


def _sample_report():
    return {
        "version": "stage_b_node_v1",
        "max_attempts": 3,
        "groups": {
            "B": {
                "task_count": 2,
                "first_pass_rate": 0.5,
                "pass_rate_3try": 0.5,
                "avg_attempts_to_success": 1.0,
                "avg_attempts_all": 2.0,
                "failure_categories": {"runtime_error": 1},
            },
            "C": {
                "task_count": 2,
                "first_pass_rate": 1.0,
                "pass_rate_3try": 1.0,
                "avg_attempts_to_success": 1.0,
                "avg_attempts_all": 1.0,
                "avg_node_count": 2.5,
                "free_code_execution_count": 0,
                "failure_categories": {},
            },
        },
        "tasks": {
            "L1_create_wave_port": {
                "metadata": {"level": "L1"},
                "B": {
                    "final_pass": False,
                    "success_on_attempt": None,
                    "failure_type": "runtime_error",
                    "attempts": [{"attempt": 1, "failure_type": "runtime_error", "error_summary": "AssignWavePort"}],
                },
                "C": {
                    "final_pass": True,
                    "success_on_attempt": 1,
                    "attempts": [{"attempt": 1, "final_pass": True}],
                    "node_steps": [{"node_id": "create_port"}],
                    "failure_type": "",
                },
            }
        },
    }


def test_render_stage_b_html_report_explains_node_comparison():
    html = render_html_report_stage_b(_sample_report(), model_name="claude")

    assert "Stage B 节点化 AEDT Benchmark 报告" in html
    assert "Group B" in html
    assert "Group C" in html
    assert "受控节点" in html
    assert "自由代码执行次数" in html
    assert "AssignWavePort" in html
    assert "当前限制" in html
    assert "实验设计" in html
    assert "判定依据" in html
    assert "关键发现" in html


def test_write_stage_b_html_report_creates_file(tmp_path):
    output = write_html_report_stage_b(_sample_report(), tmp_path / "stage_b.html", model_name="model")

    assert output.exists()
    assert "model" in output.read_text(encoding="utf-8")
