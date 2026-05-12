import json
from pathlib import Path

from aedt_agent.benchmark.report_html import render_html_report, write_html_report


def _sample_report():
    return {
        "tasks": {
            "L1_create_substrate": {
                "metadata": {"allowed_nodes": ["create_substrate"], "level": "L1"},
                "A": {"passed": True, "syntax_pass": True, "api_pass": True, "semantic_lite_pass": True},
                "B": {"passed": False, "syntax_pass": True, "api_pass": False, "semantic_lite_pass": False},
                "C": {"passed": True, "syntax_pass": True, "api_pass": True, "semantic_lite_pass": True},
            }
        },
        "go_nogo": {
            "go": True,
            "metrics": {
                "api_pass_rate_c": 1.0,
                "semantic_pass_rate_b": 0.0,
                "semantic_pass_rate_c": 1.0,
                "trap_capture_rate": 1.0,
            },
        },
        "node_readiness": {
            "nodes": {"create_substrate": {"coverage": 3, "pass_rate": 1.0, "semantic_rate": 1.0}},
            "candidate_ready": ["create_substrate"],
        },
    }


def test_render_html_report_contains_key_sections():
    html = render_html_report(_sample_report(), model_name="deepseek-v4-flash")

    assert "Stage A Benchmark Report" in html
    assert "deepseek-v4-flash" in html
    assert "Task Matrix" in html
    assert "Node Readiness" in html
    assert "How To Read This Report" in html
    assert "Task PASS / FAIL" in html
    assert "Go / No-Go Rule" in html
    assert "Group A" in html
    assert "api_pass_rate_c" in html
    assert "L1_create_substrate" in html


def test_write_html_report_creates_file(tmp_path):
    output = tmp_path / "report.html"
    write_html_report(_sample_report(), output, model_name="demo-model")

    assert output.exists()
    assert "demo-model" in output.read_text(encoding="utf-8")
