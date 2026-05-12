from pathlib import Path

from aedt_agent.nodes.models import NodeDefinition
from aedt_agent.nodes.registry import NodeRegistry


def test_node_definition_from_yaml_reads_whitelist():
    node = NodeDefinition.from_yaml(Path("nodes/catalog/create_port.yaml"))

    assert node.node_id == "create_port"
    assert "Hfss.wave_port" in node.allowed_apis


def test_registry_loads_all_stage_a_nodes():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    assert len(registry.list_nodes()) == 8
    assert registry.get("create_substrate").summary


def test_registry_api_whitelist_merges_in_order():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    whitelist = registry.api_whitelist(["create_substrate", "create_port"])

    assert whitelist[:2] == ["Hfss.modeler.create_box", "Hfss.assign_material"]
    assert "Hfss.wave_port" in whitelist
