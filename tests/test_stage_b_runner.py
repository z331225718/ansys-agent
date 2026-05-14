from pathlib import Path

from aedt_agent.benchmark.runner_stage_b import run_stage_b_node_benchmark
from aedt_agent.mcp.tools import create_fake_kernel


class StaticPlanGenerator:
    def generate(self, context, filename=None):
        return '{"plan": [{"node_id": "create_substrate", "inputs": {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy"}}]}'


class UnusedExecutor:
    def execute(self, code_path, validation_script, work_dir):
        raise AssertionError("Group B executor should not be used when running only C")


def test_stage_b_runner_executes_group_c_node_plan(tmp_path):
    report = run_stage_b_node_benchmark(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        group_b_generator=StaticPlanGenerator(),
        group_c_generator=StaticPlanGenerator(),
        group_b_executor=UnusedExecutor(),
        kernel=create_fake_kernel(Path("nodes/catalog"), audit_path=tmp_path / "audit.jsonl"),
        groups=["C"],
        task_ids=["L1_create_substrate"],
        max_attempts=3,
    )

    assert report["tasks"]["L1_create_substrate"]["C"]["final_pass"] is True
    assert report["groups"]["C"]["pass_rate_3try"] == 1.0
    assert report["groups"]["C"]["free_code_execution_count"] == 0
    assert (tmp_path / "audit.jsonl").exists()
