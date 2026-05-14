import os
from pathlib import Path

import pytest

from aedt_agent.benchmark.stage_b_validation import run_stage_b_validation


@pytest.mark.skipif(os.getenv("RUN_REAL_AEDT") != "1", reason="real AEDT smoke is opt-in")
def test_real_aedt_create_substrate_node_passes_validation(tmp_path):
    from aedt_agent.mcp.ast_guard import AstGuard
    from aedt_agent.mcp.audit_log import AuditLogger
    from aedt_agent.mcp.execution_queue import ExecutionQueue
    from aedt_agent.mcp.node_executor import NodeExecutor
    from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter
    from aedt_agent.mcp.session_manager import SessionManager
    from aedt_agent.mcp.tools import McpToolKernel
    from aedt_agent.nodes.registry import NodeRegistry

    registry = NodeRegistry.from_directory(Path("nodes/catalog"))
    manager = SessionManager(
        lambda project_id, design_id: PyaedtAdapter(
            project_id=project_id,
            design_id=design_id,
            version="2026.1",
            non_graphical=True,
        )
    )
    queue = ExecutionQueue(timeout_seconds=120)
    executor = NodeExecutor(registry, manager, queue, AuditLogger(tmp_path / "audit.jsonl"))
    kernel = McpToolKernel(registry, manager, executor, queue, AstGuard())
    session = kernel.create_session("stage_b_real_node_smoke", "HFSSDesign1")

    try:
        result = kernel.execute_node(
            "create_substrate",
            {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy"},
            session["session_id"],
        )
        validation = run_stage_b_validation(
            validation_script=Path("benchmarks/validation_scripts/validate_session.py"),
            session_id=session["session_id"],
            project_id=session["project_id"],
            design_id=session["design_id"],
            model_info=kernel.get_model_info(session["session_id"]),
            expected_outputs=["substrate"],
        )
    finally:
        kernel.release_session(session["session_id"])

    assert result.succeeded
    assert validation["passed"] is True
