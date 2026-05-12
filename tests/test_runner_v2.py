from pathlib import Path
from types import SimpleNamespace

from aedt_agent.benchmark.runner_v2 import run_aedt_benchmark_v2


class SequencedGenerator:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, context: str, filename: str | None = None) -> str:
        self.prompts.append(context)
        return self.outputs.pop(0)


class SequencedHarnessGenerator:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_attempt(
        self,
        context,
        task_id,
        group,
        attempt,
        artifact_dir,
        filename=None,
        previous_code="",
        previous_log="",
    ):
        self.calls.append(
            {
                "context": context,
                "task_id": task_id,
                "group": group,
                "attempt": attempt,
                "previous_code": previous_code,
                "previous_log": previous_log,
            }
        )

        return SimpleNamespace(
            code=self.outputs.pop(0),
            stdout_path=str(artifact_dir / f"attempt_{attempt}_harness_stdout.txt"),
            stderr_path=str(artifact_dir / f"attempt_{attempt}_harness_stderr.txt"),
            transcript_path=str(artifact_dir / f"attempt_{attempt}_transcript.txt"),
            tool_usage_path=str(artifact_dir / f"attempt_{attempt}_tool_usage.json"),
            tool_usage={
                "used_tools": group == "B",
                "gitnexus_query_count": 1 if group == "B" else 0,
                "retrieval_before_code": group == "B",
            },
        )


class FakeExecutor:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def execute(self, code_path: Path, validation_script: Path, work_dir: Path):
        return self.outcomes.pop(0)


def test_runner_v2_retries_until_validation_pass(tmp_path):
    generator = SequencedGenerator(["bad code", "fixed code"])
    executor = FakeExecutor(
        [
            {"execution_ok": False, "validation_ok": False, "failure_type": "runtime_error", "log": "NameError: app"},
            {"execution_ok": True, "validation_ok": True, "failure_type": "", "log": "ok"},
        ]
    )

    report = run_aedt_benchmark_v2(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        generator=generator,
        executor=executor,
        groups=["A"],
        task_ids=["L1_create_substrate"],
        max_attempts=3,
    )

    group = report["tasks"]["L1_create_substrate"]["A"]
    assert group["final_pass"] is True
    assert group["success_on_attempt"] == 2
    assert len(group["attempts"]) == 2
    assert "NameError: app" in generator.prompts[1]


def test_runner_v2_group_b_prompt_requires_harness_tools(tmp_path):
    generator = SequencedHarnessGenerator(["code"])
    executor = FakeExecutor(
        [{"execution_ok": True, "validation_ok": True, "failure_type": "", "log": "ok"}]
    )

    report = run_aedt_benchmark_v2(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        generator=generator,
        executor=executor,
        groups=["B"],
        task_ids=["L1_create_substrate"],
        max_attempts=3,
    )

    assert generator.calls[0]["group"] == "B"
    assert "GitNexus/PyAEDT tools" in generator.calls[0]["context"]
    assert report["groups"]["B"]["pass_rate_3try"] == 1.0
    assert report["groups"]["B"]["tool_usage_rate"] == 1.0


def test_runner_v2_harness_receives_error_log_on_repair(tmp_path):
    generator = SequencedHarnessGenerator(["bad code", "fixed code"])
    executor = FakeExecutor(
        [
            {"execution_ok": False, "validation_ok": False, "failure_type": "runtime_error", "log": "TypeError: bad port"},
            {"execution_ok": True, "validation_ok": True, "failure_type": "", "log": "ok"},
        ]
    )
    run_aedt_benchmark_v2(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        generator=generator,
        executor=executor,
        groups=["B"],
        task_ids=["L1_create_substrate"],
        max_attempts=3,
    )

    assert generator.calls[0]["previous_log"] == ""
    assert generator.calls[1]["previous_log"] == "TypeError: bad port"


def test_runner_v2_reports_attempt_heartbeat(tmp_path):
    events = []
    generator = SequencedGenerator(["code"])
    executor = FakeExecutor(
        [{"execution_ok": True, "validation_ok": True, "failure_type": "", "log": "ok"}]
    )

    run_aedt_benchmark_v2(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        generator=generator,
        executor=executor,
        groups=["A"],
        task_ids=["L1_create_substrate"],
        max_attempts=3,
        progress_callback=lambda event: events.append(event),
    )

    assert any(event.get("phase") == "attempt_start" and event.get("attempt") == 1 for event in events)
    assert any(event.get("phase") == "attempt_end" and event.get("final_pass") is True for event in events)


def test_runner_v2_prompt_tells_model_to_use_existing_app(tmp_path):
    generator = SequencedGenerator(["code"])
    executor = FakeExecutor(
        [{"execution_ok": True, "validation_ok": True, "failure_type": "", "log": "ok"}]
    )

    run_aedt_benchmark_v2(
        tasks_dir=Path("benchmarks/tasks"),
        run_dir=tmp_path / "runs",
        generator=generator,
        executor=executor,
        groups=["A"],
        task_ids=["L1_create_substrate"],
        max_attempts=3,
    )

    assert "Use the existing `app` object" in generator.prompts[0]
    assert "Do not import pyaedt" in generator.prompts[0]
