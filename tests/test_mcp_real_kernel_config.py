from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aedt_agent.mcp import server as server_module
from aedt_agent.mcp.tools import create_fake_kernel


def test_create_kernel_defaults_to_fake():
    from aedt_agent.mcp.tools import create_kernel

    kernel = create_kernel(adapter="fake", node_catalog_dir=Path("nodes/catalog"))

    session = kernel.create_session("Project", "Design")

    assert session["project_id"] == "Project"
    assert session["design_id"] == "Design"
    assert "create_substrate" in kernel.list_available_nodes()
    assert "create_layout_cutout" not in kernel.list_available_nodes()


def test_create_kernel_can_include_experimental_nodes():
    from aedt_agent.mcp.tools import create_kernel

    kernel = create_kernel(adapter="fake", node_catalog_dir=Path("nodes/catalog"), include_experimental=True)

    assert "create_layout_cutout" in kernel.list_available_nodes()


def test_create_kernel_rejects_unknown_adapter():
    from aedt_agent.mcp.tools import create_kernel

    with pytest.raises(ValueError, match="adapter must be fake or real"):
        create_kernel(adapter="bogus", node_catalog_dir=Path("nodes/catalog"))


def test_create_server_passes_adapter_to_kernel(monkeypatch):
    captured = {}

    class FakeFastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(fn):
                return fn

            return decorator

    class FakeFastMCPModule:
        FastMCP = FakeFastMCP

    def fake_create_kernel(*, adapter, node_catalog_dir, **kwargs):
        captured["adapter"] = adapter
        captured["node_catalog_dir"] = node_catalog_dir
        return create_fake_kernel(node_catalog_dir)

    monkeypatch.setitem(sys.modules, "fastmcp", FakeFastMCPModule)
    monkeypatch.setattr(server_module, "create_kernel", fake_create_kernel)

    server_module.create_server(adapter="real", node_catalog_dir=Path("nodes/catalog"))

    assert captured["adapter"] == "real"
    assert captured["node_catalog_dir"] == Path("nodes/catalog")
