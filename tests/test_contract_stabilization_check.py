from __future__ import annotations

from scripts.check_contract_stabilization import collect_contract_status


def test_contract_status_reports_required_sections():
    status = collect_contract_status()

    assert "mcp_adapter_modes" in status
    assert "node_lifecycle" in status
    assert "knowledge_assets" in status
    assert "validation_positioning" in status


def test_contract_status_has_no_layout_nodes_in_default_catalog():
    status = collect_contract_status()

    assert status["node_lifecycle"]["experimental_layout_nodes"]
    assert not status["node_lifecycle"]["default_layout_nodes"]
