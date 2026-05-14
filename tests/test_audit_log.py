import json

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus


def test_audit_logger_writes_jsonl_event(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")

    logger.record(
        event_type="execute_node",
        session_id="s1",
        node_id="create_substrate",
        inputs={"name": "Substrate"},
        result=ExecutionResult(status=ExecutionStatus.SUCCEEDED, transaction_id="txn-1"),
        state_before={"objects": {}},
        state_after={"objects": {"Substrate": {}}},
    )

    event = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert event["event_type"] == "execute_node"
    assert event["result"]["status"] == "succeeded"
