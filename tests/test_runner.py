from pathlib import Path
import json

from aedt_agent.benchmark.models import BenchmarkTask, load_tasks
from aedt_agent.benchmark.runner import run_offline_benchmark


def test_load_tasks_reads_30_yaml_files():
    tasks = load_tasks(Path("benchmarks/tasks"))

    assert len(tasks) == 30
    levels = {t.level for t in tasks}
    assert levels == {"L1", "L2", "L3", "Trap"}


def test_benchmark_task_exposes_expected_workflow():
    task = BenchmarkTask.from_yaml(Path("benchmarks/tasks/L3_patch_antenna_sparameter.yaml"))

    assert task.level == "L3"
    assert "create_port" in task.expected_workflow


def test_run_offline_benchmark_reports_group_results(tmp_path):
    report = run_offline_benchmark(
        tasks_dir=Path("benchmarks/tasks"),
        generated_dir=Path("benchmarks/generated"),
        node_catalog_dir=Path("nodes/catalog"),
        report_path=tmp_path / "report.json",
    )
    assert "L1_create_substrate" in report["tasks"]
    assert "go_nogo" in report


def test_run_offline_benchmark_can_generate_candidates(tmp_path):
    from aedt_agent.benchmark.generator import CodeGenerator

    class StubGenerator:
        def generate(self, context: str, filename: str | None = None) -> str:
            return "app.modeler.create_box([0,0,0],[1,1,1], name='substrate')\napp.assign_material('substrate', 'FR4_epoxy')"

    generated_dir = tmp_path / "generated"
    report = run_offline_benchmark(
        tasks_dir=Path("benchmarks/tasks"),
        generated_dir=generated_dir,
        node_catalog_dir=Path("nodes/catalog"),
        report_path=tmp_path / "report.json",
        generator=StubGenerator(),
        db_path=Path("knowledge/api_semantics/api_semantics.sqlite"),
        groups=["A"],
    )

    assert (generated_dir / "group_a" / "L1_create_substrate.py").exists()
    assert report["tasks"]["L1_create_substrate"]["A"]["generation_mode"] == "online"


def test_run_offline_benchmark_reuses_existing_generated_candidate(tmp_path):
    single_task_dir = tmp_path / "tasks"
    single_task_dir.mkdir()
    source_task = Path("benchmarks/tasks/L1_create_substrate.yaml")
    (single_task_dir / source_task.name).write_text(source_task.read_text(encoding="utf-8"), encoding="utf-8")

    class FailingGenerator:
        def generate(self, context: str, filename: str | None = None) -> str:
            raise AssertionError("generator should not be called when candidate already exists")

    generated_dir = tmp_path / "generated"
    candidate = generated_dir / "group_a" / "L1_create_substrate.py"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        "app.modeler.create_box([0,0,0],[1,1,1], name='substrate')\n"
        "app.assign_material('substrate', 'FR4_epoxy')\n",
        encoding="utf-8",
    )

    report = run_offline_benchmark(
        tasks_dir=single_task_dir,
        generated_dir=generated_dir,
        node_catalog_dir=Path("nodes/catalog"),
        report_path=tmp_path / "report.json",
        generator=FailingGenerator(),
        db_path=Path("knowledge/api_semantics/api_semantics.sqlite"),
        groups=["A"],
    )

    assert report["tasks"]["L1_create_substrate"]["A"]["generation_mode"] == "online"


def test_run_offline_benchmark_can_force_regenerate_existing_candidate(tmp_path):
    single_task_dir = tmp_path / "tasks"
    single_task_dir.mkdir()
    source_task = Path("benchmarks/tasks/L1_create_substrate.yaml")
    (single_task_dir / source_task.name).write_text(source_task.read_text(encoding="utf-8"), encoding="utf-8")

    class StubGenerator:
        def generate(self, context: str, filename: str | None = None) -> str:
            return "app.modeler.create_box([0,0,0],[2,2,2], name='substrate')"

    generated_dir = tmp_path / "generated"
    candidate = generated_dir / "group_a" / "L1_create_substrate.py"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("old code\n", encoding="utf-8")

    run_offline_benchmark(
        tasks_dir=single_task_dir,
        generated_dir=generated_dir,
        node_catalog_dir=Path("nodes/catalog"),
        report_path=tmp_path / "report.json",
        generator=StubGenerator(),
        db_path=Path("knowledge/api_semantics/api_semantics.sqlite"),
        groups=["A"],
        reuse_existing_candidates=False,
    )

    assert "2,2,2" in candidate.read_text(encoding="utf-8")


def test_run_offline_benchmark_reports_progress(tmp_path):
    events = []

    report = run_offline_benchmark(
        tasks_dir=Path("benchmarks/tasks"),
        generated_dir=Path("benchmarks/generated"),
        node_catalog_dir=Path("nodes/catalog"),
        report_path=tmp_path / "report.json",
        groups=["A"],
        progress_callback=lambda event: events.append(event),
    )

    assert report["tasks"]
    assert events
    assert events[0]["task_id"] == "L1_assign_material"
    assert events[0]["group"] == "A"
    assert "current" in events[0]
    assert "total" in events[0]


def test_cli_run_benchmark_with_config(tmp_path, monkeypatch):
    from aedt_agent import cli

    generated_dir = tmp_path / "generated"
    report_path = tmp_path / "report.json"
    config_path = tmp_path / "benchmark_config.json"
    config_path.write_text(
        json.dumps(
            {
                "generator": {
                    "backend": "file",
                    "file": {"base_dir": "benchmarks/reference_scripts"},
                },
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": str(generated_dir),
                    "nodes": "nodes/catalog",
                    "db": "knowledge/api_semantics/api_semantics.sqlite",
                    "report": str(report_path),
                },
                "groups": ["A"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        ["aedt-agent", "run-benchmark", "--config", str(config_path), "--generate"],
    )

    cli.main()

    assert report_path.exists()
