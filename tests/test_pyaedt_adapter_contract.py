import os

import pytest


def test_pyaedt_adapter_import_is_lazy():
    from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter

    assert PyaedtAdapter.__name__ == "PyaedtAdapter"


def test_pyaedt_adapter_boundary_snapshot_keeps_assignment_props():
    from aedt_agent.mcp.pyaedt_adapter import _safe_boundary_names

    class Boundary:
        name = "Port1"
        type = "Wave Port"
        props = {"Faces": [12], "RenormalizeAllTerminals": True}

    class App:
        boundaries = [Boundary()]

    ports = _safe_boundary_names(App(), ("Wave Port",))

    assert ports["Port1"]["assignment"] == [12]
    assert ports["Port1"]["props"]["Faces"] == [12]


@pytest.mark.skipif(os.getenv("RUN_REAL_AEDT") != "1", reason="real AEDT smoke is opt-in")
def test_pyaedt_adapter_can_start_real_hfss_session():
    from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter

    adapter = PyaedtAdapter(project_id="stage_b_smoke", design_id="HFSSDesign1")
    try:
        assert adapter.health_check() is True
        assert "objects" in adapter.snapshot_state()
    finally:
        adapter.release()
