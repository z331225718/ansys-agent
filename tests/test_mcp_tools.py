from pathlib import Path

from aedt_agent.mcp.tools import create_fake_kernel
from aedt_agent.mcp.types import ExecutionStatus


def test_tool_kernel_lists_and_describes_nodes():
    kernel = create_fake_kernel(Path("nodes/catalog"))

    assert "create_substrate" in kernel.list_available_nodes()
    assert kernel.describe_node("create_substrate")["required"] == ["material", "origin", "size"]


def test_tool_kernel_execute_node_updates_model_info(tmp_path):
    kernel = create_fake_kernel(Path("nodes/catalog"), audit_path=tmp_path / "audit.jsonl")
    session = kernel.create_session("p1", "d1")

    result = kernel.execute_node(
        "create_substrate",
        {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy"},
        session["session_id"],
    )

    info = kernel.get_model_info(session["session_id"])
    assert result.status == ExecutionStatus.SUCCEEDED
    assert "Substrate" in info["objects"]
    assert (tmp_path / "audit.jsonl").exists()


def test_tool_kernel_restricted_script_is_dev_only():
    kernel = create_fake_kernel(Path("nodes/catalog"))
    session = kernel.create_session("p1", "d1")

    result = kernel.execute_script_restricted("app.modeler.create_box([0,0,0], [1,1,1], name='Box')", session["session_id"])

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "DevModeDisabled"


def test_tool_kernel_dev_restricted_script_uses_ast_guard():
    kernel = create_fake_kernel(Path("nodes/catalog"), dev_mode=True)
    session = kernel.create_session("p1", "d1")

    result = kernel.execute_script_restricted("import os\nos.remove('x')", session["session_id"])

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "AstGuardViolation"


def test_server_module_exposes_factory():
    from aedt_agent.mcp.server import create_server

    assert callable(create_server)
