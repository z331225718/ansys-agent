import os

import pytest


def test_pyaedt_adapter_import_is_lazy():
    from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter

    assert PyaedtAdapter.__name__ == "PyaedtAdapter"


@pytest.mark.skipif(os.getenv("RUN_REAL_AEDT") != "1", reason="real AEDT smoke is opt-in")
def test_pyaedt_adapter_can_start_real_hfss_session():
    from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter

    adapter = PyaedtAdapter(project_id="stage_b_smoke", design_id="HFSSDesign1")
    try:
        assert adapter.health_check() is True
        assert "objects" in adapter.snapshot_state()
    finally:
        adapter.release()
