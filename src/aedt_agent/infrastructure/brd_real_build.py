from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region


@dataclass(frozen=True)
class RealAedtEnvironment:
    version: str = "2026.1"
    non_graphical: bool = False
    edb_backend: str = "auto"
    cadence_launcher: str = ""
    ansysem_root: str = ""
    awp_root: str = ""


@dataclass(frozen=True)
class BrdRealBuildRequest:
    layout_file: Path
    artifact_dir: Path
    signal_nets: list[str]
    reference_nets: list[str]
    local_cut_region: dict[str, Any]
    recorded_layout_settings: dict[str, Any] = field(default_factory=dict)
    environment: RealAedtEnvironment = field(default_factory=RealAedtEnvironment)
    stackup_xml: Path | None = None
    uniform_line_port_hint: dict[str, Any] = field(default_factory=dict)
    target_metrics: list[dict[str, Any]] = field(default_factory=list)
    approved_port_selection: dict[str, Any] = field(default_factory=dict)
    solve_enabled: bool = False


@dataclass(frozen=True)
class BrdRealBuildResult:
    summary: dict[str, Any]


class BrdRealBuildAdapter:
    def __init__(
        self,
        *,
        edb_factory: Callable[..., Any] | None = None,
        hfss3dlayout_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._edb_factory = edb_factory
        self._hfss3dlayout_factory = hfss3dlayout_factory

    def run(self, request: BrdRealBuildRequest) -> BrdRealBuildResult:
        if request.solve_enabled:
            raise ValueError("solve_enabled is not supported by real_build; run a solve mission after model approval")
        if not request.layout_file.exists():
            raise FileNotFoundError(f"layout_file not found: {request.layout_file}")
        if request.stackup_xml is not None and not request.stackup_xml.exists():
            raise FileNotFoundError(f"stackup_xml not found: {request.stackup_xml}")
        region = parse_local_cut_region(request.local_cut_region)
        request.artifact_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "status": "succeeded",
            "adapter": "real_pyedb_hfss3dlayout_build_only",
            "layout_file": str(request.layout_file),
            "source_edb_path": "",
            "edb_path": str(request.artifact_dir / f"{request.layout_file.stem}_cutout.aedb"),
            "aedt_project": str(request.artifact_dir / f"{request.layout_file.stem}_cutout_hfss.aedt"),
            "signal_nets": list(request.signal_nets),
            "reference_nets": list(request.reference_nets),
            "local_cut_region": region,
            "local_cut_polygon": bbox_to_polygon(region),
            "port_candidates": {"status": "not_evaluated", "candidate_count": 0},
            "port_execution": {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []},
            "layout_setup": {},
            "layout_solve": {"status": "skipped", "reason": "model_review_only"},
            "layout_reports": {},
            "recorded_layout_settings": _recorded_settings_summary(request.recorded_layout_settings),
            "target_metrics": list(request.target_metrics),
            "steps": [],
        }
        return BrdRealBuildResult(summary=summary)

    def _edb_class(self) -> Callable[..., Any]:
        if self._edb_factory is not None:
            return self._edb_factory
        from pyedb import Edb

        return Edb

    def _hfss3dlayout_class(self) -> Callable[..., Any]:
        if self._hfss3dlayout_factory is not None:
            return self._hfss3dlayout_factory
        from ansys.aedt.core import Hfss3dLayout

        return Hfss3dLayout


def _recorded_settings_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "hfss_extents": dict(value.get("hfss_extents") or {}),
        "design_options": dict(value.get("design_options") or {}),
        "setup_options": dict(value.get("setup_options") or {}),
        "setup_advanced_settings": dict(value.get("setup_advanced_settings") or {}),
        "setup_curve_approximation": dict(value.get("setup_curve_approximation") or {}),
        "sweep_options": dict(value.get("sweep_options") or {}),
    }
