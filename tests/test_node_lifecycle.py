from __future__ import annotations

from pathlib import Path

from aedt_agent.nodes.registry import NodeRegistry


def test_catalog_nodes_have_status_and_track():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    assert registry.nodes
    for node in registry.list_nodes():
        assert node.status in {"experimental", "candidate", "stable", "deprecated"}
        assert node.track in {"hfss-core", "hfss-demo", "layout-brd", "postprocess"}


def test_default_catalog_excludes_experimental_layout_nodes():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    default_nodes = registry.list_nodes(include_experimental=False)
    default_ids = {node.node_id for node in default_nodes}

    assert "create_substrate" in default_ids
    assert "create_layout_cutout" not in default_ids
    assert "import_layout_file" not in default_ids


def test_experimental_catalog_can_be_requested():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    all_ids = {node.node_id for node in registry.list_nodes(include_experimental=True)}

    assert "create_layout_cutout" in all_ids
    assert "create_layout_ports" in all_ids
