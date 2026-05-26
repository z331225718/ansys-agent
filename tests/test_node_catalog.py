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
        "create_antenna_report",
        "create_farfield_setup",
        "create_layout_cutout",
        "locate_layout_port_candidates",
        "create_layout_ports",
        "create_layout_setup",
        "create_layout_sparam_tdr_report",
        "create_sparameter_report",
        "create_setup",
        "create_substrate",
        "create_sweep_or_export",
        "create_wave_port",
        "configure_layout_stackup",
        "import_layout_file",
        "select_face",
        "select_layout_nets",
        "solve_layout",
        "solve_setup",
    }


def test_default_node_catalog_excludes_experimental_layout_nodes():
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"))
    node_ids = {metadata.node_id for metadata in catalog.list_metadata()}

    assert "create_substrate" in node_ids
    assert "create_layout_cutout" not in node_ids


def test_node_catalog_can_include_experimental_layout_nodes():
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"), include_experimental=True)
    node_ids = {metadata.node_id for metadata in catalog.list_metadata()}

    assert "create_layout_cutout" in node_ids


def test_layout_nodes_are_documented_as_experimental_with_limits():
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"), include_experimental=True)
    layout_nodes = [
        catalog.get("import_layout_file"),
        catalog.get("select_layout_nets"),
        catalog.get("create_layout_cutout"),
        catalog.get("configure_layout_stackup"),
        catalog.get("locate_layout_port_candidates"),
        catalog.get("create_layout_ports"),
        catalog.get("create_layout_setup"),
    ]

    for metadata in layout_nodes:
        serialized = metadata.to_dict()
        assert serialized["status"] == "experimental"
        assert serialized["track"] == "layout-brd"
        assert (
            "experimental" in serialized["description"].lower()
            or "experimental" in serialized["ui_hints"].get("badge", "").lower()
        )


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
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"), include_experimental=True)

    payload = catalog.to_dict()
    encoded = json.dumps(payload)

    assert payload["version"] == "0.1.0"
    assert len(payload["nodes"]) == 22
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
