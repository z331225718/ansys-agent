from pathlib import Path

from aedt_agent.benchmark.report_html_stage_b import render_html_report_stage_b
from aedt_agent.benchmark.stage_b_presentation import build_stage_b_presentation_report


def test_stage_b_presentation_combines_groups_and_scrubs_artifacts():
    repo_root = Path("/workspace/ansys-agent")
    group_b = {
        "max_attempts": 3,
        "groups": {"B": {"task_count": 1, "first_pass_rate": 0.0, "pass_rate_3try": 0.0}},
        "tasks": {
            "L2_dipole_antenna": {
                "metadata": {"level": "L2"},
                "B": {
                    "final_pass": False,
                    "failure_type": "runtime_error",
                    "attempts": [
                        {
                            "attempt": 1,
                            "failure_type": "runtime_error",
                            "error_summary": 'File "/workspace/ansys-agent/benchmarks/runs/x/attempt_1_code.py"',
                            "code_path": "/workspace/ansys-agent/benchmarks/runs/x/attempt_1_code.py",
                            "prompt_path": "/workspace/ansys-agent/benchmarks/runs/x/attempt_1_prompt.txt",
                        }
                    ],
                },
            }
        },
    }
    group_c = {
        "max_attempts": 3,
        "groups": {
            "C": {
                "task_count": 1,
                "first_pass_rate": 1.0,
                "pass_rate_3try": 1.0,
                "free_code_execution_count": 0,
            }
        },
        "tasks": {
            "L2_dipole_antenna": {
                "metadata": {"level": "L2"},
                "C": {
                    "final_pass": True,
                    "success_on_attempt": 1,
                    "attempts": [{"attempt": 1, "final_pass": True}],
                    "node_steps": [{"node_id": "create_sweep_or_export"}],
                },
            }
        },
    }

    report = build_stage_b_presentation_report(
        group_b,
        group_c,
        repo_root=repo_root,
        group_b_source="/workspace/ansys-agent/benchmarks/runs/b/stage_b_report.json",
        group_c_source="/workspace/ansys-agent/benchmarks/runs/c/stage_b_report.json",
    )

    attempt = report["tasks"]["L2_dipole_antenna"]["B"]["attempts"][0]
    assert report["groups"]["B"]["task_count"] == 1
    assert report["groups"]["C"]["task_count"] == 1
    assert "code_path" not in attempt
    assert "prompt_path" not in attempt
    assert "<repo>/benchmarks/runs/x/attempt_1_code.py" in attempt["error_summary"]
    assert report["run_sources"]["group_b"] == "benchmarks/runs/b/stage_b_report.json"
    assert "/workspace/ansys-agent" not in str(report)


def test_stage_b_presentation_html_contains_chinese_report_sections():
    report = {
        "version": "stage_b_node_v1_presentation_10task",
        "max_attempts": 3,
        "groups": {
            "B": {"task_count": 1, "first_pass_rate": 0.0, "pass_rate_3try": 0.0},
            "C": {"task_count": 1, "first_pass_rate": 1.0, "pass_rate_3try": 1.0, "free_code_execution_count": 0},
        },
        "tasks": {},
    }

    html = render_html_report_stage_b(report, model_name="deepseek-v4-flash")

    assert "实验设计" in html
    assert "判定依据" in html
    assert "关键发现" in html
    assert "deepseek-v4-flash" in html
