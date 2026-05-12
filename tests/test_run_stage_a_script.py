import json
from pathlib import Path
from io import StringIO
import sys


class PassingExecutor:
    def execute(self, code_path, validation_script, work_dir):
        return {"execution_ok": True, "validation_ok": True, "failure_type": "", "log": "ok"}


def test_run_stage_a_script_writes_html_report_and_progress(tmp_path, monkeypatch):
    import scripts.run_stage_a_benchmark as run_script

    report_path = tmp_path / "report.json"
    html_path = tmp_path / "report.html"
    config_path = tmp_path / "benchmark_config.json"
    config_path.write_text(
        json.dumps(
            {
                "generator": {
                    "backend": "file",
                    "file": {"base_dir": "benchmarks/reference_scripts"},
                    "openai": {"base_url": "", "api_key": "", "model": "", "timeout": 60, "temperature": 0.0},
                },
                "official_retrieval": {"backend": "legacy"},
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": str(tmp_path / "generated"),
                    "nodes": "nodes/catalog",
                    "db": str(tmp_path / "api.sqlite"),
                    "report": str(report_path),
                    "html_report": str(html_path),
                    "run_dir": str(tmp_path / "run"),
                },
                "groups": ["A", "B"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(run_script, "REPO_ROOT", Path.cwd())
    monkeypatch.setattr(run_script, "load_benchmark_config", lambda _: __import__("aedt_agent.benchmark.config", fromlist=["load_benchmark_config"]).load_benchmark_config(config_path))
    monkeypatch.setattr(run_script, "build_executor", lambda config=None: PassingExecutor())
    stdout = StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    run_script.main()

    assert report_path.exists()
    assert html_path.exists()
    assert "AEDT Execution Benchmark" in html_path.read_text(encoding="utf-8")
    assert "[1/" in stdout.getvalue()


def test_run_stage_a_script_can_clear_generated_with_fresh_flag(tmp_path, monkeypatch):
    import scripts.run_stage_a_benchmark as run_script

    report_path = tmp_path / "report.json"
    html_path = tmp_path / "report.html"
    generated_dir = tmp_path / "generated"
    stale = generated_dir / "group_a" / "stale.py"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("old\n", encoding="utf-8")
    config_path = tmp_path / "benchmark_config.json"
    config_path.write_text(
        json.dumps(
            {
                "generator": {
                    "backend": "file",
                    "file": {"base_dir": "benchmarks/reference_scripts"},
                    "openai": {"base_url": "", "api_key": "", "model": "", "timeout": 60, "temperature": 0.0},
                },
                "official_retrieval": {"backend": "legacy"},
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": str(generated_dir),
                    "nodes": "nodes/catalog",
                    "db": str(tmp_path / "api.sqlite"),
                    "report": str(report_path),
                    "html_report": str(html_path),
                    "run_dir": str(generated_dir),
                },
                "groups": ["A"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(run_script, "REPO_ROOT", Path.cwd())
    monkeypatch.setattr(run_script, "load_benchmark_config", lambda _: __import__("aedt_agent.benchmark.config", fromlist=["load_benchmark_config"]).load_benchmark_config(config_path))
    monkeypatch.setattr(run_script, "build_executor", lambda config=None: PassingExecutor())
    monkeypatch.setattr(sys, "argv", ["run_stage_a_benchmark.py", "--fresh"])

    run_script.main()

    assert not stale.exists()
