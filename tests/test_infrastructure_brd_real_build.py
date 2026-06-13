from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.infrastructure.brd_real_build import (
    BrdRealBuildAdapter,
    BrdRealBuildRequest,
    RealAedtEnvironment,
)


def _request(tmp_path: Path, **overrides) -> BrdRealBuildRequest:
    layout = tmp_path / "case.brd"
    layout.write_text("brd", encoding="utf-8")
    values = {
        "layout_file": layout,
        "artifact_dir": tmp_path / "artifacts",
        "signal_nets": ["56G_TX0_P", "56G_TX0_N"],
        "reference_nets": ["GND"],
        "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        "recorded_layout_settings": {},
        "environment": RealAedtEnvironment(version="2026.1"),
    }
    values.update(overrides)
    return BrdRealBuildRequest(**values)


def test_real_build_request_requires_existing_layout(tmp_path):
    missing = tmp_path / "missing.brd"

    with pytest.raises(FileNotFoundError, match="layout_file not found"):
        BrdRealBuildAdapter().run(_request(tmp_path, layout_file=missing))


def test_real_build_rejects_solve_enabled_in_build_only_phase(tmp_path):
    with pytest.raises(ValueError, match="solve_enabled is not supported"):
        BrdRealBuildAdapter().run(_request(tmp_path, solve_enabled=True))


def test_real_build_request_accepts_graphical_environment(tmp_path):
    request = _request(tmp_path, environment=RealAedtEnvironment(version="2026.1", non_graphical=False))

    assert request.environment.version == "2026.1"
    assert request.environment.non_graphical is False
