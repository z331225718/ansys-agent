from __future__ import annotations

import os
from pathlib import Path

from aedt_agent.mcp.tools import create_kernel


def create_server(
    node_catalog_dir: Path = Path("nodes/catalog"),
    adapter: str | None = None,
    audit_path: Path | None = None,
    dev_mode: bool = False,
    include_experimental: bool | None = None,
):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the mcp extra to run the FastMCP server: pip install -e .[mcp]") from exc

    kernel = create_kernel(
        adapter=adapter or os.environ.get("AEDT_AGENT_MCP_ADAPTER", "fake"),
        node_catalog_dir=node_catalog_dir,
        audit_path=audit_path,
        dev_mode=dev_mode,
        include_experimental=(
            include_experimental
            if include_experimental is not None
            else os.environ.get("AEDT_AGENT_INCLUDE_EXPERIMENTAL_NODES", "").lower() in {"1", "true", "yes"}
        ),
    )
    server = FastMCP("aedt-agent")

    @server.tool()
    def create_session(project_id: str, design_id: str) -> dict:
        return kernel.create_session(project_id, design_id)

    @server.tool()
    def release_session(session_id: str) -> dict:
        return kernel.release_session(session_id)

    @server.tool()
    def list_available_nodes() -> list[str]:
        return kernel.list_available_nodes()

    @server.tool()
    def describe_node(node_id: str) -> dict:
        return kernel.describe_node(node_id)

    @server.tool()
    def execute_node(node_id: str, inputs: dict, session_id: str) -> dict:
        result = kernel.execute_node(node_id=node_id, inputs=inputs, session_id=session_id)
        return {
            "status": result.status.value,
            "transaction_id": result.transaction_id,
            "output": result.output,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "traceback": result.traceback,
        }

    @server.tool()
    def get_model_info(session_id: str) -> dict:
        return kernel.get_model_info(session_id)

    return server
