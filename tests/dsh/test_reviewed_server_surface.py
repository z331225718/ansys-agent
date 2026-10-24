from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastmcp")


def test_real_fastmcp_reviewed_surface_has_only_read_only_tools(monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_MCP_PROFILE", "reviewed")
    from aedt_agent.interactive.server import create_server

    server = create_server()
    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "list_ansys_capabilities_v2",
        "list_ansys_workflows",
        "inspect_ansys_workflow",
        "get_ansys_workflow_status",
    }
