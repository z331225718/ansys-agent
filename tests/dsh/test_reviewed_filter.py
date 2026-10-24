from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_reviewed_plugin_denies_hidden_tools_and_filters_catalog():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the DSH preset filter test")
    script = Path(__file__).with_name("reviewed_filter_test.mjs")
    result = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "reviewed guard and catalog filter passed" in result.stdout
