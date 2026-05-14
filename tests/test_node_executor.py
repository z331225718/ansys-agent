import json
from pathlib import Path

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionStatus
from aedt_agent.nodes.registry import NodeRegistry


def _executor(tmp_path):
    manager = SessionManager(lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    executor = NodeExecutor(
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
        session_manager=manager,
        queue=ExecutionQueue(timeout_seconds=1),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
    )
    return manager, executor


def test_node_executor_runs_create_substrate_and_audits(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_substrate",
        {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy", "name": "Substrate"},
    )

    state = manager.snapshot(session.ref.session_id)
    audit_event = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["objects"]["Substrate"]["material"] == "FR4_epoxy"
    assert audit_event["node_id"] == "create_substrate"


def test_node_executor_rejects_unknown_node(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(session.ref.session_id, "not_a_node", {})

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "UnknownNode"


def test_node_executor_selects_face_from_object(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    executor.execute_node(
        session.ref.session_id,
        "create_substrate",
        {"origin": [0, 0, 0], "size": [1, 1, 1], "material": "FR4_epoxy"},
    )

    result = executor.execute_node(
        session.ref.session_id,
        "select_face",
        {"object_name": "Substrate", "axis": "x", "side": "max"},
    )

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.output["selected_face_id"] > 0


def test_node_executor_accepts_common_geometry_aliases(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {
            "geometry": [
                {
                    "type": "box",
                    "position": [0, 0, 0],
                    "dimensions": [10, 10, 1],
                    "name": "metal",
                    "matname": "copper",
                }
            ]
        },
    )

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["objects"]["metal"]["material"] == "copper"


def test_node_executor_rejects_bad_schema(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(session.ref.session_id, "create_setup", {})

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "schema_error"


def test_node_executor_creates_lumped_port_with_modal_solution(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_port",
        {"port_type": "lumped", "assignment": "PortSheet", "name": "P1", "integration_line": [[0, 0, 0], [0, 1, 0]]},
    )

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["ports"]["P1"]["type"] == "lumped"
