import json
from pathlib import Path

from aedt_agent.nodes.catalog import NodeCatalog, load_node_catalog


def test_node_catalog_exports_all_executable_nodes():
    catalog = load_node_catalog(Path("nodes/catalog"))

    node_ids = {metadata.node_id for metadata in catalog.list_metadata()}

    assert node_ids == {
        "assign_boundary",
        "create_airbox",
        "create_conductor_or_geometry_group",
        "create_port",
        "create_setup",
        "create_substrate",
        "create_sweep_or_export",
        "select_face",
    }


def test_catalog_metadata_has_product_fields():
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"))
    port = catalog.get("create_port")

    assert port.display_name == "Create Port"
    assert port.category == "port"
    assert port.version == "0.1.0"
    assert port.stability.value == "candidate"
    assert "Hfss.wave_port" in port.required_capabilities
    assert port.input_schema["required"] == ["assignment", "port_type"]
    assert port.output_schema["properties"]["port_name"]["type"] == "string"
    assert port.ui_hints["draggable"] is True
    assert "port_created" in port.postchecks


def test_catalog_json_is_serializable_and_frontend_safe():
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"))

    payload = catalog.to_dict()
    encoded = json.dumps(payload)

    assert payload["version"] == "0.1.0"
    assert len(payload["nodes"]) == 8
    assert "function" not in encoded
    assert "callable" not in encoded
    assert "/home/" not in encoded
    assert "nodes/catalog" not in encoded


def test_every_node_has_schema_version_and_postchecks():
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"))

    for metadata in catalog.list_metadata():
        serialized = metadata.to_dict()
        assert serialized["input_schema"]["type"] == "object"
        assert serialized["output_schema"]["type"] == "object"
        assert serialized["version"]
        assert serialized["stability"] in {"experimental", "candidate", "stable", "deprecated"}
        assert isinstance(serialized["postchecks"], list)
        assert serialized["ui_hints"]["icon"]
