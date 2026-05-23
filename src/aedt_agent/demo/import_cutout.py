from __future__ import annotations

import fnmatch
import os
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.dom import minidom
import xml.etree.ElementTree as ET


LAYOUT_SUFFIXES = {".brd", ".mcm"}


@dataclass(frozen=True)
class ImportCutoutRequest:
    layout_file: Path
    signal_net_patterns: list[str]
    reference_net_patterns: list[str]
    output_dir: Path
    frequency: str = "28GHz"
    sweep_start: str = "1GHz"
    sweep_stop: str = "56GHz"
    expansion_size: float = 0.002
    extent_type: str = "ConvexHull"


def discover_layout_files(root: Path = Path("~/work")) -> list[Path]:
    base = root.expanduser()
    if not base.exists():
        return []
    files = [path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in LAYOUT_SUFFIXES]
    return sorted(files, key=lambda path: (path.suffix.lower() != ".brd", str(path).lower()))


def parse_net_patterns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_pattern_string(value)
    if isinstance(value, Iterable):
        patterns: list[str] = []
        for item in value:
            patterns.extend(parse_net_patterns(item))
        return patterns
    return _split_pattern_string(str(value))


def expand_net_patterns(patterns: list[str], available_nets: list[str], *, case_sensitive: bool = False) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    available_by_fold = {net.casefold(): net for net in available_nets}
    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        if _is_wildcard(pattern):
            for net in available_nets:
                if _pattern_matches(pattern, net, case_sensitive=case_sensitive) and net not in seen:
                    matched.append(net)
                    seen.add(net)
            continue
        exact = pattern if case_sensitive else available_by_fold.get(pattern.casefold())
        if exact is not None and exact in available_nets and exact not in seen:
            matched.append(exact)
            seen.add(exact)
    return matched


def build_import_cutout_request(parameters: dict[str, Any], *, default_work_root: Path = Path("~/work")) -> ImportCutoutRequest:
    layout_file = _layout_file_from_parameters(parameters, default_work_root=default_work_root)
    output_dir = Path(str(parameters.get("artifact_dir") or parameters.get("output_dir") or layout_file.parent / "aedt_agent_import_cutout")).expanduser()
    signal_patterns = parse_net_patterns(parameters.get("signal_nets") or parameters.get("target_nets") or parameters.get("nets") or "*")
    reference_patterns = parse_net_patterns(parameters.get("reference_nets") or parameters.get("ref_nets") or "GND")
    return ImportCutoutRequest(
        layout_file=layout_file,
        signal_net_patterns=signal_patterns,
        reference_net_patterns=reference_patterns,
        output_dir=output_dir,
        frequency=str(parameters.get("frequency") or "28GHz"),
        sweep_start=str(parameters.get("sweep_start") or "1GHz"),
        sweep_stop=str(parameters.get("sweep_stop") or "56GHz"),
        expansion_size=float(parameters.get("expansion_size") or 0.002),
        extent_type=str(parameters.get("extent_type") or "ConvexHull"),
    )


def run_fake_import_cutout(request: ImportCutoutRequest) -> dict[str, Any]:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    available_nets = [
        "GND",
        "VSS",
        "56G_TX0_P",
        "56G_TX0_N",
        "56G_RX0_P",
        "56G_RX0_N",
        "REFCLK_P",
        "REFCLK_N",
    ]
    signal_nets = expand_net_patterns(request.signal_net_patterns, available_nets)
    reference_nets = expand_net_patterns(request.reference_net_patterns, available_nets)
    if not signal_nets:
        signal_nets = ["56G_TX0_P", "56G_TX0_N"]
    if not reference_nets:
        reference_nets = ["GND"]
    touchstone = request.output_dir / "import_cutout_demo.s2p"
    tdr = request.output_dir / "import_cutout_tdr.csv"
    summary = {
        "status": "succeeded",
        "adapter": "fake",
        "layout_file": str(request.layout_file),
        "signal_nets": signal_nets,
        "reference_nets": reference_nets,
        "edb_path": str(request.output_dir / f"{request.layout_file.stem}.aedb"),
        "aedt_project": str(request.output_dir / f"{request.layout_file.stem}.aedt"),
        "touchstone": str(touchstone),
        "tdr": str(tdr),
        "steps": _step_results("succeeded"),
    }
    _write_demo_touchstone(touchstone)
    _write_demo_tdr(tdr)
    (request.output_dir / "import_cutout_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def run_real_import_cutout(
    request: ImportCutoutRequest,
    *,
    aedt_version: str,
    cadence_launcher: str = "",
    ansysem_root: str = "",
    awp_root: str = "",
) -> dict[str, Any]:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    if cadence_launcher:
        apply_cadence_launcher_environment(Path(cadence_launcher).expanduser())
    apply_aedt_environment(aedt_version, ansysem_root=ansysem_root, awp_root=awp_root)
    return import_brd_with_hfss3dlayout(request, aedt_version=aedt_version)


def import_brd_with_hfss3dlayout(request: ImportCutoutRequest, *, aedt_version: str) -> dict[str, Any]:
    from ansys.aedt.core import Hfss3dLayout

    project_name = request.layout_file.stem
    edb_path = request.output_dir / f"{project_name}.aedb"
    project_path = request.output_dir / f"{project_name}.aedt"
    h3d = Hfss3dLayout(
        project=f"{project_name}_import_cutout",
        version=aedt_version,
        non_graphical=False,
        new_desktop=True,
        close_on_exit=False,
    )
    try:
        imported = h3d.import_brd(str(request.layout_file), output_dir=str(edb_path), set_as_active=True, close_active_project=False)
        if imported is False:
            raise RuntimeError(f"Hfss3dLayout.import_brd returned False for {request.layout_file}")
        available_nets = _hfss3dlayout_net_names(h3d)
        signal_nets = expand_net_patterns(request.signal_net_patterns, available_nets)
        reference_nets = expand_net_patterns(request.reference_net_patterns, available_nets)
        if not signal_nets:
            raise ValueError(
                f"no signal nets matched {request.signal_net_patterns}; "
                f"suggestions: {_net_suggestions(request.signal_net_patterns, available_nets)}; "
                f"available examples: {available_nets[:20]}"
            )
        if not reference_nets:
            raise ValueError(
                f"no reference nets matched {request.reference_net_patterns}; "
                f"suggestions: {_net_suggestions(request.reference_net_patterns, available_nets)}; "
                f"available examples: {available_nets[:20]}"
            )
        h3d.save_project(str(project_path))
        summary = {
            "status": "succeeded",
            "adapter": "real",
            "layout_file": str(request.layout_file),
            "signal_nets": signal_nets,
            "reference_nets": reference_nets,
            "available_net_count": len(available_nets),
            "available_net_examples": available_nets[:20],
            "edb_path": str(edb_path),
            "aedt_project": str(project_path),
            "touchstone": "",
            "tdr": "",
            "steps": _step_results("succeeded", stop_after="select_nets"),
            "note": "Real AEDT graphical BRD import and net selection completed. Cutout, stackup, ports, solve, and TDR still require board-specific rules.",
        }
        (request.output_dir / "import_cutout_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        pass


def _hfss3dlayout_net_names(app: Any) -> list[str]:
    names: list[str] = []
    for getter in (
        lambda: list(getattr(app.modeler, "nets", {}).keys()),
        lambda: list(getattr(app.modeler, "signal_nets", {}).keys()),
        lambda: list(getattr(app.modeler, "power_nets", {}).keys()),
        lambda: list(app.modeler.oeditor.GetNetClassNets("<All>")),
    ):
        try:
            for name in getter():
                if isinstance(name, str) and name not in names:
                    names.append(name)
        except Exception:
            continue
    return names


def _net_suggestions(patterns: list[str], available_nets: list[str], *, limit: int = 20) -> list[str]:
    tokens: list[str] = []
    for pattern in patterns:
        for token in re.split(r"[^A-Za-z0-9]+", pattern.replace("*", " ").replace("?", " ")):
            token = token.strip().casefold()
            if len(token) >= 2 and not token.isdigit() and token not in tokens:
                tokens.append(token)
    suggestions = []
    for net in available_nets:
        folded = net.casefold()
        if any(token in folded for token in tokens) and net not in suggestions:
            suggestions.append(net)
        if len(suggestions) >= limit:
            break
    return suggestions


def generate_control_xml(target_nets: list[str], reference_nets: list[str], output_path: Path, project_name: str) -> Path:
    output_path.mkdir(parents=True, exist_ok=True)
    xml_file_path = output_path / f"{project_name}_control.xml"
    ET.register_namespace("c", "http://www.ansys.com/control")
    root = ET.Element("{http://www.ansys.com/control}Control")
    root.set("schemaVersion", "1.0")
    import_options = ET.SubElement(root, "ImportOptions")
    import_options.set("ImportDummyNet", "false")
    import_options.set("ImportCrossHatchShapesAsLines", "true")
    import_options.set("EnableDefaultComponentValues", "true")
    nets = ET.SubElement(root, "Nets")
    for net_name in target_nets + reference_nets:
        net = ET.SubElement(nets, "Net")
        net.set("PinsBecomePorts", "false")
        net.set("Name", net_name)
    xml_str = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="    ")
    xml_file_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(xml_str.split("\n")[1:]), encoding="utf-8")
    return xml_file_path


def apply_cadence_launcher_environment(launcher: Path) -> None:
    if not launcher.exists():
        raise FileNotFoundError(f"Cadence launcher not found: {launcher}")
    text = launcher.read_text(encoding="utf-8")
    assignments = _launcher_assignments(text)
    cdsroot = Path(assignments.get("CDSROOT", "/home/zzmjay/Cadence/SPB221")).expanduser()
    tools = Path(assignments.get("TOOLS", str(cdsroot / "tools.lnx86"))).expanduser()
    os.environ["CDSROOT"] = str(cdsroot)
    os.environ["CDS_AUTO_64BIT"] = "ALL"
    os.environ["CDS_LIC_FILE"] = str(cdsroot / "share/license/license.dat")
    os.environ.setdefault("LM_LICENSE_FILE", os.environ["CDS_LIC_FILE"])
    path_entries = [cdsroot / "tools/bin", cdsroot / "tools/pcb/bin", tools / "bin"]
    os.environ["PATH"] = os.pathsep.join(str(item) for item in path_entries) + os.pathsep + os.environ.get("PATH", "")
    ld_entries = [
        cdsroot / "tools/lib/64bit/SuSE/SLES12",
        tools / "mainwin560/mw/lib-amd64_linux/X11",
        tools / "lib/64bit",
        tools / "lib",
        tools / "mainwin560/mw/lib-amd64_linux_optimized",
        tools / "Qt/v5/64bit/lib",
        tools / "TPtools/boost/lib/64bit",
        tools / "spatial/HOOPS_3DF_2400/bin/linux_x86_64",
        tools / "spatial/IoP_2022/linux_a64/code/bin",
        tools / "jre64/lib/amd64/server",
    ]
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(str(item) for item in ld_entries) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    os.environ.setdefault("MWHOME", str(tools / "mainwin560/mw"))
    os.environ.setdefault("MWLOOK", "motif")
    os.environ.setdefault("MWRT_MODE", "classic")
    os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


def apply_aedt_environment(version: str, *, ansysem_root: str = "", awp_root: str = "") -> None:
    suffix = _version_suffix(version)
    resolved_awp_root = Path(awp_root).expanduser() if awp_root else Path("~/ansys_inc").expanduser() / f"v{suffix}"
    resolved_ansysem_root = Path(ansysem_root).expanduser() if ansysem_root else resolved_awp_root / "AnsysEM"
    if not resolved_awp_root.exists():
        raise FileNotFoundError(f"AWP root not found: {resolved_awp_root}")
    if not resolved_ansysem_root.exists():
        raise FileNotFoundError(f"ANSYSEM root not found: {resolved_ansysem_root}")
    os.environ[f"AWP_ROOT{suffix}"] = str(resolved_awp_root)
    os.environ[f"ANSYSEM_ROOT{suffix}"] = str(resolved_ansysem_root)
    os.environ["PATH"] = str(resolved_ansysem_root) + os.pathsep + os.environ.get("PATH", "")


def read_tdr_csv(path: Any) -> dict[str, Any]:
    if not isinstance(path, str) or not path:
        return {}
    csv_path = Path(path)
    if not csv_path.exists():
        return {}
    samples: list[dict[str, float]] = []
    for line in csv_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lower().startswith("time"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            samples.append({"time_ps": float(parts[0]), "impedance_ohm": float(parts[1])})
        except ValueError:
            continue
    return {"source": str(csv_path), "point_count": len(samples), "samples": samples} if samples else {}


def _split_pattern_string(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("'\"") for item in text.split(",") if item.strip().strip("'\"")]


def _is_wildcard(pattern: str) -> bool:
    return bool(re.search(r"[*?\[\]]", pattern))


def _pattern_matches(pattern: str, net: str, *, case_sensitive: bool) -> bool:
    if case_sensitive:
        return fnmatch.fnmatchcase(net, pattern)
    return fnmatch.fnmatchcase(net.casefold(), pattern.casefold())


def _layout_file_from_parameters(parameters: dict[str, Any], *, default_work_root: Path) -> Path:
    explicit = parameters.get("layout_file") or parameters.get("brd_file") or parameters.get("mcm_file")
    if explicit:
        path = Path(str(explicit)).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f"layout file not found: {path}")
    discovered = discover_layout_files(default_work_root)
    if not discovered:
        raise FileNotFoundError(f"no .brd or .mcm files found under {default_work_root.expanduser()}")
    return discovered[0]


def _write_demo_touchstone(path: Path) -> None:
    lines = ["! AEDT Agent import/cutout demo", "# GHz S MA R 50"]
    for frequency, s11_mag, s21_mag in [
        (1.0, 0.22, 0.45),
        (8.0, 0.18, 0.70),
        (16.0, 0.12, 0.82),
        (28.0, 0.08, 0.76),
        (40.0, 0.14, 0.62),
        (56.0, 0.20, 0.48),
    ]:
        lines.append(f"{frequency:.3f} {s11_mag:.6f} 0 {s21_mag:.6f} 0 {s21_mag:.6f} 0 {s11_mag:.6f} 0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_demo_tdr(path: Path) -> None:
    lines = ["time_ps,impedance_ohm"]
    for time_ps, impedance in [(0, 50.0), (25, 49.4), (50, 47.8), (75, 52.1), (100, 50.6), (125, 49.9)]:
        lines.append(f"{time_ps},{impedance}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _import_layout_file(edb: Any, layout_file: Path, working_dir: Path, control_xml: Path) -> None:
    try:
        edb.import_layout_file(input_file=str(layout_file), working_dir=str(working_dir), control_file=str(control_xml))
        return
    except TypeError:
        pass
    try:
        edb.import_layout_file(input_file=str(layout_file), dest_dir=str(working_dir), control_file=str(control_xml))
        return
    except TypeError:
        pass
    edb.import_layout_file(input_file=str(layout_file), working_dir=str(working_dir))


def _launcher_assignments(text: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r'^\s*([A-Z_]+)="?([^"\n]+)"?\s*$', line)
        if match:
            value = match.group(2)
            for key, replacement in assignments.items():
                value = value.replace(f"${key}", replacement)
            assignments[match.group(1)] = shlex.split(value)[0] if value else ""
    return assignments


def _version_suffix(version: str) -> str:
    parts = version.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]) % 100}{int(parts[1])}"
    digits = "".join(char for char in version if char.isdigit())
    return digits[-3:] if len(digits) >= 3 else digits


def _step_results(status: str, *, stop_after: str | None = None) -> list[dict[str, Any]]:
    steps = [
        {"step_id": "discover_file", "status": status},
        {"step_id": "import_layout", "status": status},
        {"step_id": "select_nets", "status": status},
        {"step_id": "cutout", "status": status},
        {"step_id": "stackup", "status": status},
        {"step_id": "ports", "status": status},
        {"step_id": "setup", "status": status},
        {"step_id": "solve", "status": status},
        {"step_id": "postprocess", "status": status},
    ]
    if stop_after is None:
        return steps
    output = []
    reached = False
    for step in steps:
        output.append(step if not reached else {**step, "status": "pending"})
        if step["step_id"] == stop_after:
            reached = True
    return output
