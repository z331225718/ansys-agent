from aedt_agent.benchmark.repair import RepairRecord, run_with_repair


def test_repair_record_tracks_rounds():
    record = RepairRecord(task_id="test", group="C")
    record.add_round(round_num=1, code="bad code", passed=False, error="SyntaxError")
    record.add_round(round_num=2, code="fixed code", passed=True, error="")
    assert record.total_rounds == 2
    assert record.success is True
    assert record.repair_count == 1


def test_run_with_repair_attempts_second_round_on_failure():
    class StubGenerator:
        def __init__(self):
            self.calls = 0

        def generate(self, context: str, filename: str | None = None) -> str:
            self.calls += 1
            return "bad code" if self.calls == 1 else "fixed code"

    def evaluator(code: str):
        return code == "fixed code", "SyntaxError" if code != "fixed code" else ""

    record = run_with_repair("task", "C", "ctx", StubGenerator(), evaluator)
    assert record.total_rounds == 2
    assert record.success is True
