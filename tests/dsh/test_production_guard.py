from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_production_guard_denies_mission_lifecycle_only():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the DSH production guard test")
    script = Path(__file__).with_name("production_guard_test.mjs")
    result = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "production guard passed" in result.stdout
