from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PYAEDT_MIGRATION = {
    "ImportExtracta": {"preferred": "Hfss3dLayout.import_brd or PyEDB import/cutout path", "fallback": "raw ImportExtracta"},
    "CutOutSubDesign": {"preferred": "PyEDB edb.cutout", "fallback": "raw CutOutSubDesign"},
    "CreatePortsOnComponentsByNet": {
        "preferred": "Hfss3dLayout.create_ports_on_component_by_nets",
        "fallback": "raw CreatePortsOnComponentsByNet",
    },
    "CreateEdgePort": {"preferred": "Hfss3dLayout.create_edge_port", "fallback": "raw CreateEdgePort"},
    "SolveSetups.Add": {"preferred": "Hfss3dLayout.create_setup", "fallback": "raw SolveSetups.Add"},
    "AddSweep": {"preferred": "Hfss3dLayout.create_linear_step_sweep", "fallback": "raw AddSweep"},
    "Analyze": {"preferred": "Hfss3dLayout.analyze", "fallback": "raw Analyze"},
    "SaveAs": {"preferred": "Hfss3dLayout.save_project", "fallback": "raw SaveAs"},
    "CreateReport": {"preferred": "PyAEDT post/report API when stable", "fallback": "raw ReportSetup.CreateReport"},
    "CreateCircleVoid": {"preferred": "", "fallback": "raw CreateCircleVoid behind void action schema"},
    "CreateRectangleVoid": {"preferred": "", "fallback": "raw CreateRectangleVoid behind void action schema"},
    "SetDiffPairs": {"preferred": "", "fallback": "raw SetDiffPairs behind differential-pair action schema"},
}


def analyze_recorded_workflow(path: Path) -> dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    paths = _extract_paths(text)
    signal_nets, reference_nets = _extract_nets(text)
    return {
        "source": str(path),
        "line_count": len(text.splitlines()),
        "aedt_version": _first_match(text, r"Ansys Electronics Desktop Version ([0-9.]+)") or "",
        "paths": paths,
        "nets": {"signal": signal_nets, "reference": reference_nets},
        "component": _extract_component(text),
        "hfss_extents": _extract_hfss_extents(text),
        "design_options": _extract_design_options(text),
        "setup": _extract_setup(text),
        "sweep": _extract_sweep(text),
        "reports": _extract_reports(text),
        "optimization_variables": _extract_variables(text),
        "voids": _extract_voids(text),
        "steps": _extract_steps(text),
        "pyaedt_migration": PYAEDT_MIGRATION,
    }


def _extract_paths(text: str) -> dict[str, str]:
    brd = aedb = ""
    match = re.search(r'ImportExtracta\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\)', text)
    if match:
        brd, aedb = match.group(1), match.group(2)
    project = _first_match(text, r'SaveAs\("([^"]+\.aedt)"')
    return {"brd": brd, "aedb": aedb, "aedt_project": project or ""}


def _extract_nets(text: str) -> tuple[list[str], list[str]]:
    nets = []
    for raw in re.findall(r'"(?:[^":]+:)?([A-Za-z0-9_]+)"\s*,\s*True', text):
        if raw not in nets:
            nets.append(raw)
    if not nets:
        for raw in re.findall(r'"(SRDS_[A-Za-z0-9_]+|GND)"', text):
            if raw not in nets:
                nets.append(raw)
    reference = [net for net in nets if net.upper() in {"GND", "VSS"} or net.upper().startswith("GND")]
    signal = [net for net in nets if net not in reference and ("_P" in net or "_N" in net or "SRDS" in net)]
    return signal, reference


def _extract_component(text: str) -> str:
    match = re.search(r'CreatePortsOnComponentsByNet\(\s*\[\s*"NAME:Components",\s*"([^"]+)"', text, re.S)
    return match.group(1) if match else ""


def _extract_setup(text: str) -> dict[str, Any]:
    name = _first_match(text, r'oModule\.Add\(\s*\[\s*"NAME:([^"]+)"') or ""
    frequency = _first_match(text, r'"Frequency:="\s*,\s*"([^"]+)"') or ""
    block = _between(text, "oModule.Add(", "oModule.AddSweep")
    return {
        "name": name,
        "frequency": frequency,
        "options": _extract_known_options(
            block,
            [
                "SliderType",
                "ElementType",
                "SolveSetupType",
                "PercentRefinementPerPass",
                "MinNumberOfPasses",
                "MinNumberOfConvergedPasses",
                "MeshSizeFactor",
                "HfssMesh",
                "Style25DVia",
                "ViaMeshPlating",
                "UseDefeature",
                "ViaNumSides",
            ],
        ),
        "advanced_settings": _extract_known_options(
            _named_section(block, "AdvancedSettings"),
            [
                "OrderBasis",
                "SolveInsideMetalBasis",
                "MaxDeltaZo",
                "CausalMaterials",
                "CircuitSparamDefinition",
                "CircuitIntegrationType",
                "MeshingMethod",
                "UseAlternativeMeshMethodsAsFallBack",
                "BroadbandFreqOption",
                "BroadbandMaxNumFreq",
                "PhiMesherDeltaZRatio",
            ],
        ),
        "curve_approximation": _extract_known_options(
            _named_section(block, "CurveApproximation"),
            [
                "ArcAngle",
                "StartAzimuth",
                "UseError",
                "Error",
                "MaxPoints",
                "UnionPolys",
                "Replace3DTriangles",
            ],
        ),
    }


def _extract_sweep(text: str) -> dict[str, Any]:
    name = _first_match(text, r'AddSweep\("([^"]+)",\s*\[\s*"NAME:([^"]+)"')
    sweep_name = ""
    setup_name = ""
    match = re.search(r'AddSweep\("([^"]+)",\s*\[\s*"NAME:([^"]+)"', text)
    if match:
        setup_name, sweep_name = match.group(1), match.group(2)
    data = _first_match(text, r'"Data:="\s*,\s*"([^"]+)"') or ""
    stop = _stop_ghz_from_sweep_data(data)
    block = _between(text, "AddSweep(", "oModule.SetDiffPairs")
    return {
        "name": sweep_name or name or "",
        "setup": setup_name,
        "data": data,
        "stop_ghz": stop,
        "options": _extract_known_options(
            block,
            [
                "GenerateSurfaceCurrent",
                "SaveRadFieldsOnly",
                "SAbsError",
                "ZoPercentError",
                "EnforcePassivity",
                "PassivityTolerance",
                "UseQ3DForDC",
                "UseComputeDC",
                "MaxSolutions",
                "InterpUseSMatrix",
                "InterpUsePortImpedance",
                "InterpUsePropConst",
                "InterpUseFullBasis",
                "AdvDCExtrapolation",
                "MinSolvedFreq",
                "AutoSMatOnlySolve",
                "MinFreqSMatrixOnlySolve",
            ],
        ),
    }


def _extract_hfss_extents(text: str) -> dict[str, Any]:
    block = _between(text, "oDesign.EditHfssExtents(", "oDesign.DesignOptions")
    options = _extract_known_options(
        block,
        [
            "ExtentType",
            "DielExtentType",
            "HonorUserDiel",
            "Include3D",
            "TruncAtGnd",
            "SyncZExt",
            "OpenRegionType",
            "UseRadBound",
            "OperFreq",
            "UseStackupForZExtFact",
            "Smooth",
        ],
    )
    options.update(_extract_compound_options(block, ["DielExt", "AirHorExt", "AirPosZExt", "AirNegZExt"]))
    return options


def _extract_design_options(text: str) -> dict[str, Any]:
    return _extract_known_options(
        _between(text, "oDesign.DesignOptions(", "oModule = oDesign.GetModule"),
        [
            "CausalMaterials",
            "MeshingMethod",
            "EnableDesignIntersectionCheck",
            "UseAlternativeMeshMethodsAsFallBack",
            "CircuitSparamDefinition",
            "CircuitIntegrationType",
            "ExportAfterSolve",
            "BroadbandFreqOption",
            "BroadbandMaxNumFreq",
            "SaveADP",
            "UseAdvancedDCExtrap",
            "ModeOption",
            "PhiMesherDeltaZRatio",
        ],
    )


def _extract_reports(text: str) -> list[dict[str, str]]:
    reports = []
    for name in re.findall(r'CreateReport\("([^"]+)"', text):
        kind = "tdr" if "TDR" in name.upper() else "sparameter"
        reports.append({"name": name, "kind": kind})
    return reports


def _extract_variables(text: str) -> list[dict[str, str]]:
    variables = []
    for name in sorted(set(re.findall(r'"NAME:(r_cut_[A-Za-z0-9_]+)"', text))):
        value = _first_match(text, rf'"NAME:{re.escape(name)}".*?"Value:="\s*,\s*"([^"]+)"', flags=re.S) or ""
        variables.append({"name": name, "value": value})
    return variables


def _extract_voids(text: str) -> list[dict[str, str]]:
    voids = []
    for kind, call in [("circle", "CreateCircleVoid"), ("rectangle", "CreateRectangleVoid")]:
        for block in re.findall(rf"{call}\((.*?)\)\s*", text, flags=re.S):
            layer = _first_match(block, r'"LayerName:="\s*,\s*"([^"]+)"') or ""
            item = {"layer": layer, "kind": kind}
            if item not in voids:
                voids.append(item)
    return voids


def _extract_steps(text: str) -> list[str]:
    candidates = [
        ("ImportExtracta", "import_layout_file"),
        ("CutOutSubDesign", "create_layout_cutout"),
        ("ChangeLayers", "configure_layout_stackup"),
        ("CreatePortsOnComponentsByNet", "create_layout_component_ports"),
        ("CreateEdgePort", "create_layout_edge_port"),
        ("SetDiffPairs", "create_layout_differential_pairs"),
        ("oModule.Add(", "create_layout_setup"),
        ("AddSweep", "create_layout_sweep"),
        ("CreateReport", "create_layout_reports"),
        ("CreateCircleVoid", "apply_layout_void_adjustment"),
        ("CreateRectangleVoid", "apply_layout_void_adjustment"),
        ("Analyze", "solve_layout_channel"),
    ]
    steps = []
    for needle, step in candidates:
        if needle in text and step not in steps:
            steps.append(step)
    return steps


def _stop_ghz_from_sweep_data(data: str) -> float:
    match = re.search(r"LIN\s+([0-9.]+)GHz\s+([0-9.]+)GHz", data, re.I)
    return float(match.group(2)) if match else 0.0


def _extract_known_options(text: str, keys: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in keys:
        value = _extract_aedt_value(text, key)
        if value is not None:
            options[key] = value
    return options


def _extract_compound_options(text: str, keys: list[str]) -> dict[str, dict[str, Any]]:
    options: dict[str, dict[str, Any]] = {}
    for key in keys:
        match = re.search(rf'"{re.escape(key)}:="\s*,\s*\[(.*?)\]', text, flags=re.S)
        if not match:
            continue
        item = _extract_known_options(match.group(1), ["Ext", "Dim"])
        if item:
            options[key] = item
    return options


def _extract_aedt_value(text: str, key: str) -> Any:
    match = re.search(rf'"{re.escape(key)}:="\s*,\s*("[^"]*"|True|False|-?[0-9]+(?:\.[0-9]+)?(?:E-?[0-9]+)?)', text)
    if not match:
        return None
    raw = match.group(1)
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw == "True":
        return True
    if raw == "False":
        return False
    if "." in raw or "E" in raw:
        return float(raw)
    return int(raw)


def _between(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    end_index = text.find(end, start_index + len(start))
    if end_index < 0:
        return text[start_index:]
    return text[start_index:end_index]


def _named_section(text: str, name: str) -> str:
    start = text.find(f'"NAME:{name}"')
    if start < 0:
        return ""
    next_section = text.find('["NAME:', start + len(name) + 7)
    if next_section < 0:
        return text[start:]
    return text[start:next_section]


def _first_match(text: str, pattern: str, *, flags: int = 0) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1) if match else None
