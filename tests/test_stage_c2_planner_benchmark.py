from pathlib import Path

from aedt_agent.demo.planner_benchmark import run_planner_benchmark


def test_stage_c2_planner_benchmark_writes_html_and_json(tmp_path):
    output_html = tmp_path / "planner_benchmark.html"
    output_json = tmp_path / "planner_benchmark.json"

    report = run_planner_benchmark(Path("."), output_html=output_html, output_json=output_json)

    assert report["summary"]["task_count"] == 5
    assert report["summary"]["valid_workflow_count"] >= 4
    assert output_html.exists()
    assert output_json.exists()
    assert "Stage C.2 Planner Benchmark" in output_html.read_text(encoding="utf-8")
