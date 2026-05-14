import time

from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.types import ExecutionStatus, SessionRef


def test_execution_queue_returns_successful_result():
    result = ExecutionQueue(timeout_seconds=1).submit_callable(
        session=SessionRef("s1", "p1", "d1"),
        node_id="create_substrate",
        fn=lambda: {"created": {"objects": ["Substrate"]}},
    )

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.output["session_id"] == "s1"
    assert result.output["node_id"] == "create_substrate"


def test_execution_queue_returns_failure_on_exception():
    def fail():
        raise KeyError("missing")

    result = ExecutionQueue(timeout_seconds=1).submit_callable(SessionRef("s1", "p1", "d1"), fail)

    assert result.status == ExecutionStatus.FAILED
    assert result.error_type == "KeyError"


def test_execution_queue_reports_timeout_after_callable_returns_late():
    result = ExecutionQueue(timeout_seconds=0.001).submit_callable(
        SessionRef("s1", "p1", "d1"),
        lambda: (time.sleep(0.01) or {}),
    )

    assert result.status == ExecutionStatus.TIMEOUT
