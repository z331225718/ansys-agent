from pathlib import Path

from aedt_agent.benchmark.models import BenchmarkTask
from aedt_agent.benchmark.runner_stage_b import _build_group_c_prompt, _expand_allowed_node_ids, _resolve_refs, run_stage_b_node_benchmark
from aedt_agent.mcp.tools import create_fake_kernel


class StaticPlanGenerator:
    def generate(self, context, filename=None):
        return '{"plan": [{"node_id": "create_substrate", "inputs": {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy"}}]}'


class WrongPlanGenerator:
    def generate(self, context, filename=None):
        return '{"plan": [{"node_id": "create_setup", "inputs": {"frequency": "5GHz"}}]}'


class RefPlanGenerator:
    def generate(self, context, filename=None):
        return (
            '{"plan": ['
            '{"id": "box", "node_id": "create_substrate", "inputs": {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy"}},'
            '{"id": "face", "node_id": "select_face", "inputs": {"object_name": "Substrate", "axis": "x", "side": "max"}},'
            '{"node_id": "create_port", "inputs": {"port_type": "wave", "assignment": {"$ref": "face.output.selected_face_id"}}}'
            ']}'
        )


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


def test_stage_b_runner_requires_validation_after_node_execution(tmp_path):
    report = run_stage_b_node_benchmark(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        group_b_generator=WrongPlanGenerator(),
        group_c_generator=WrongPlanGenerator(),
        group_b_executor=UnusedExecutor(),
        kernel=create_fake_kernel(Path("nodes/catalog"), audit_path=tmp_path / "audit.jsonl"),
        groups=["C"],
        task_ids=["L1_create_substrate"],
        max_attempts=1,
    )

    task_result = report["tasks"]["L1_create_substrate"]["C"]
    assert task_result["final_pass"] is False
    assert task_result["failure_type"] == "validation_fail"
    assert task_result["attempts"][0]["validation_ok"] is False


def test_stage_b_runner_resolves_node_output_refs(tmp_path):
    report = run_stage_b_node_benchmark(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        group_b_generator=RefPlanGenerator(),
        group_c_generator=RefPlanGenerator(),
        group_b_executor=UnusedExecutor(),
        kernel=create_fake_kernel(Path("nodes/catalog"), audit_path=tmp_path / "audit.jsonl"),
        groups=["C"],
        task_ids=["L1_create_wave_port"],
        max_attempts=1,
    )

    task_result = report["tasks"]["L1_create_wave_port"]["C"]
    assert task_result["final_pass"] is True
    assert task_result["node_steps"][-1]["inputs"]["assignment"] > 0


def test_group_c_prompt_expands_prerequisite_nodes():
    kernel = create_fake_kernel(Path("nodes/catalog"))
    task = BenchmarkTask(
        task_id="L1_create_wave_port",
        level="L1",
        domain="hfss",
        requirement="Create a wave port from the intended face.",
        allowed_nodes=["select_face", "create_port"],
        expected_workflow=["select_face", "create_port"],
        expected_outputs=["wave_port"],
    )

    allowed = _expand_allowed_node_ids(task.allowed_nodes, kernel)
    prompt = _build_group_c_prompt(task, kernel, previous_log="")

    assert allowed == ["create_conductor_or_geometry_group", "select_face", "create_port"]
    assert "The AEDT design starts empty" in prompt
    assert "create_conductor_or_geometry_group" in prompt


def test_resolve_refs_accepts_common_output_aliases():
    step_outputs = {
        "geom": {"output": {"object_name": "conductor1"}},
        "air": {"output": {"object_name": "AirBox"}},
    }

    assert _resolve_refs({"$ref": "geom.output.conductor_id"}, step_outputs) == "conductor1"
    assert _resolve_refs({"$ref": "geom.output.name"}, step_outputs) == "conductor1"
    assert _resolve_refs({"$ref": "air.output.airbox_id"}, step_outputs) == "AirBox"
    assert _resolve_refs({"$ref": "air"}, step_outputs)["object_name"] == "AirBox"
