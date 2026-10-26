from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_client_plugin_matches_the_runtime_contract():
    """Materialize the browser half and assert its slot registrations.

    The bundle is hand-written lazy-CJS, so the contract that a packaging step
    would normally enforce (registration id, apply/inject exports, slot wiring)
    is asserted here instead.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the AI-HFSS client plugin test")
    script = Path(__file__).with_name("ai_hfss_client_test.mjs")
    result = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ai-hfss workstation client smoke test passed" in result.stdout

def test_host_half_control_api_and_launcher():
    """Mount the real host half against a fake Cordis context and drive /api/ai-hfss."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the AI-HFSS host plugin test")
    script = Path(__file__).with_name("ai_hfss_host_test.mjs")
    result = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ai-hfss workstation host smoke test passed" in result.stdout
