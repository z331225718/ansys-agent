from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LayoutNetSelection:
    signal_nets: list[str]
    reference_nets: list[str]
    requested_signal_pattern: str = ""
    requested_reference_pattern: str = ""


@dataclass(frozen=True)
class LayoutPortCandidateReport:
    status: str
    signal_nets: list[str]
    reference_nets: list[str]
    recommended_endpoints: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class LayoutModelBuildSummary:
    layout_file: Path
    cutout_path: Path
    aedt_project: Path
    signal_nets: list[str]
    reference_nets: list[str]
    port_names: list[str] = field(default_factory=list)
    setup_name: str = ""
    solve_skipped: bool = True
