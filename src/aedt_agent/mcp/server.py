from __future__ import annotations

from pathlib import Path

from aedt_agent.mcp.tools import create_fake_kernel


def create_server(node_catalog_dir: Path = Path("nodes/catalog")):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the mcp extra to run the FastMCP server: pip install -e .[mcp]") from exc

    kernel = create_fake_kernel(node_catalog_dir)
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
