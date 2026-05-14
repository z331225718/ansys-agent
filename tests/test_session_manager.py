import pytest

from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionStatus, SessionRef


def test_session_ref_and_execution_status_are_stable():
    ref = SessionRef("s1", "p1", "d1")

    assert ref.session_id == "s1"
    assert ExecutionStatus.SUCCEEDED.value == "succeeded"
    assert ExecutionStatus.REJECTED.value == "rejected"


def test_session_manager_creates_snapshots_and_releases_session():
    manager = SessionManager(lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    session = manager.create_session("p1", "d1")

    snapshot = manager.snapshot(session.ref.session_id)
    manager.release_session(session.ref.session_id)

    assert snapshot["project_id"] == "p1"
    assert snapshot["design_id"] == "d1"
    with pytest.raises(KeyError):
        manager.get_session(session.ref.session_id)


def test_fake_adapter_executes_node_callable():
    adapter = FakeAedtAdapter("p1", "d1")

    result = adapter.execute_node_callable(
        lambda app: {"name": app.modeler.create_box([0, 0, 0], [1, 1, 1], name="Box", material="copper").name}
    )

    assert result == {"name": "Box"}
    assert adapter.snapshot_state()["objects"]["Box"]["material"] == "copper"
