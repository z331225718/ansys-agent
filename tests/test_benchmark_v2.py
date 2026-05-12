from pathlib import Path

from aedt_agent.benchmark.models import load_tasks
from aedt_agent.benchmark.task_sets import STAGE_A_V2_TASK_IDS, load_stage_a_v2_tasks
from aedt_agent.benchmark.v2_models import AttemptResult, compute_group_metrics


def test_stage_a_v2_selects_10_representative_tasks():
    tasks = load_stage_a_v2_tasks(Path("benchmarks/tasks"))

    assert len(tasks) == 10
    assert [task.task_id for task in tasks] == STAGE_A_V2_TASK_IDS
    levels = {task.level for task in tasks}
    assert levels == {"L1", "L2", "Trap"}


def test_stage_a_v2_task_ids_exist_in_task_directory():
    task_ids = {task.task_id for task in load_tasks(Path("benchmarks/tasks"))}

    assert set(STAGE_A_V2_TASK_IDS) <= task_ids


def test_compute_group_metrics_tracks_first_and_three_try_success():
    results = [
        {"final_pass": True, "success_on_attempt": 1, "attempts": [{}, {}][:1], "failure_type": ""},
        {"final_pass": True, "success_on_attempt": 3, "attempts": [{}, {}, {}], "failure_type": ""},
        {"final_pass": False, "success_on_attempt": None, "attempts": [{}, {}, {}], "failure_type": "runtime_error"},
    ]

    metrics = compute_group_metrics(results)

    assert metrics["task_count"] == 3
    assert metrics["first_pass_rate"] == 1 / 3
    assert metrics["pass_rate_3try"] == 2 / 3
    assert metrics["avg_attempts_to_success"] == 2.0
    assert metrics["failure_categories"] == {"runtime_error": 1}


def test_attempt_result_final_pass_requires_execution_and_validation():
    attempt = AttemptResult(
        attempt=1,
        code_path="attempt_1_code.py",
        prompt_path="attempt_1_prompt.txt",
        exec_log_path="attempt_1_exec.log",
        validation_log_path="attempt_1_validation.log",
        generation_ok=True,
        execution_ok=True,
        validation_ok=True,
    )

    assert attempt.final_pass is True
