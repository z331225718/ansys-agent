from pathlib import Path

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
