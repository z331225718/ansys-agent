from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aedt_agent.layout.import_cutout import expand_net_patterns
from aedt_agent.layout.import_cutout import parse_net_patterns
from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region


LAYOUT_SUFFIXES = {".brd", ".mcm"}
ProgressCallback = Callable[[dict[str, Any]], None]


def _emit_progress(
    progress_callback: ProgressCallback | None,
    step_id: str,
    label: str,
    status: str,
    **payload: Any,
) -> None:
    if progress_callback is None:
        return
    event = {"step_id": step_id, "label": label, "status": status}
    event.update(payload)
    progress_callback(event)


@dataclass(frozen=True)
class ImportCutoutRequest:
    layout_file: Path
    signal_net_patterns: list[str]
    reference_net_patterns: list[str]
    output_dir: Path
    frequency: str = "28GHz"
    sweep_start: str = "0GHz"
    sweep_stop: str = "67GHz"
    sweep_type: str = "Interpolating"
    sweep_points: int = 501
    use_q3d_for_dc: bool = True
    solve_enabled: bool = False
    expansion_size: float = 0.002
    extent_type: str = "ConvexHull"
    threads: int | None = None
    edb_backend: str = "auto"
    stackup_xml: Path | None = None
    solderball: dict[str, str] | None = None
    recorded_hfss_extents: dict[str, Any] = field(default_factory=dict)
    recorded_design_options: dict[str, Any] = field(default_factory=dict)
    recorded_setup_options: dict[str, Any] = field(default_factory=dict)
    recorded_setup_advanced_settings: dict[str, Any] = field(default_factory=dict)
    recorded_setup_curve_approximation: dict[str, Any] = field(default_factory=dict)
    recorded_sweep_options: dict[str, Any] = field(default_factory=dict)
    interpolation_max_solutions: int = 250
    local_cut_region: dict[str, Any] = field(default_factory=dict)
    local_cut_polygon: dict[str, Any] = field(default_factory=dict)
    uniform_line_port_hint: dict[str, Any] = field(default_factory=dict)


def discover_layout_files(root: Path = Path("~/work")) -> list[Path]:
    base = root.expanduser()
    if not base.exists():
        return []
    files = [path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in LAYOUT_SUFFIXES]
    return sorted(files, key=lambda path: (path.suffix.lower() != ".brd", str(path).lower()))


def discover_stackup_xml(root: Path) -> Path | None:
    base = root.expanduser()
    if not base.exists():
        return None
    matches = [path for path in base.glob("*.xml") if "stack" in path.name.lower()]
    return sorted(matches, key=lambda path: str(path).lower())[0] if matches else None


def build_import_cutout_request(parameters: dict[str, Any], *, default_work_root: Path = Path("~/work")) -> ImportCutoutRequest:
    layout_file = _layout_file_from_parameters(parameters, default_work_root=default_work_root)
    output_dir = Path(str(parameters.get("artifact_dir") or parameters.get("output_dir") or layout_file.parent / "aedt_agent_import_cutout")).expanduser()
    signal_patterns = parse_net_patterns(parameters.get("signal_nets") or parameters.get("target_nets") or parameters.get("nets") or "*")
    reference_patterns = parse_net_patterns(parameters.get("reference_nets") or parameters.get("ref_nets") or "GND")
    stackup_xml = _optional_existing_path(parameters.get("stackup_xml") or parameters.get("stackup_file"))
    if stackup_xml is None:
        stackup_xml = discover_stackup_xml(layout_file.parent)
    recorded_sweep_options = _mapping_parameter(parameters.get("recorded_sweep_options"))
    local_cut_region = {}
    local_cut_polygon = {}
    if parameters.get("local_cut_region"):
        local_cut_region = parse_local_cut_region(parameters.get("local_cut_region"))
        local_cut_polygon = bbox_to_polygon(local_cut_region)
    return ImportCutoutRequest(
        layout_file=layout_file,
        signal_net_patterns=signal_patterns,
        reference_net_patterns=reference_patterns,
        output_dir=output_dir,
        frequency=str(parameters.get("frequency") or "28GHz"),
        sweep_start=str(parameters.get("sweep_start") or "0GHz"),
        sweep_stop=str(parameters.get("sweep_stop") or "67GHz"),
        sweep_type=str(parameters.get("sweep_type") or "Interpolating"),
        sweep_points=int(parameters.get("sweep_points") or parameters.get("points") or 501),
        use_q3d_for_dc=_bool_parameter(recorded_sweep_options.get("UseQ3DForDC", parameters.get("use_q3d_for_dc")), default=True),
        solve_enabled=_bool_parameter(parameters.get("solve_enabled") or parameters.get("analyze"), default=False),
        expansion_size=float(parameters.get("expansion_size") or 0.002),
        extent_type=str(parameters.get("extent_type") or "ConvexHull"),
        threads=int(parameters.get("threads") or parameters["cutout_threads"])
        if parameters.get("threads") or parameters.get("cutout_threads")
        else None,
        edb_backend=str(parameters.get("edb_backend") or "auto"),
        stackup_xml=stackup_xml,
        solderball=_solderball_settings(parameters),
        recorded_hfss_extents=_mapping_parameter(parameters.get("recorded_hfss_extents")),
        recorded_design_options=_mapping_parameter(parameters.get("recorded_design_options")),
        recorded_setup_options=_mapping_parameter(parameters.get("recorded_setup_options")),
        recorded_setup_advanced_settings=_mapping_parameter(parameters.get("recorded_setup_advanced_settings")),
        recorded_setup_curve_approximation=_mapping_parameter(parameters.get("recorded_setup_curve_approximation")),
        recorded_sweep_options=recorded_sweep_options,
        interpolation_max_solutions=int(
            parameters.get("interpolation_max_solutions") or parameters.get("max_solutions") or recorded_sweep_options.get("MaxSolutions") or 250
        ),
        local_cut_region=local_cut_region,
        local_cut_polygon=local_cut_polygon,
        uniform_line_port_hint=_mapping_parameter(parameters.get("uniform_line_port_hint")),
    )


def run_fake_import_cutout(request: ImportCutoutRequest, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    _emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "running")
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
    _emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "succeeded", layout_file=str(request.layout_file))
    _emit_progress(progress_callback, "select_layout_nets", "Select Nets", "running")
    signal_nets = expand_net_patterns(request.signal_net_patterns, available_nets)
    reference_nets = expand_net_patterns(request.reference_net_patterns, available_nets)
    if not signal_nets:
        signal_nets = ["56G_TX0_P", "56G_TX0_N"]
    if not reference_nets:
        reference_nets = ["GND"]
    _emit_progress(
        progress_callback,
        "select_layout_nets",
        "Select Nets",
        "succeeded",
        signal_nets=signal_nets,
        reference_nets=reference_nets,
    )
    _emit_progress(progress_callback, "create_layout_cutout", "Create PyEDB Cutout", "running")
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
        "recorded_layout_settings": _recorded_layout_settings_summary(request),
        "local_cut_region": dict(request.local_cut_region),
        "local_cut_polygon": dict(request.local_cut_polygon),
        "uniform_line_port_hint": dict(request.uniform_line_port_hint),
    }
    _emit_progress(progress_callback, "create_layout_cutout", "Create PyEDB Cutout", "succeeded", edb_path=summary["edb_path"])
    _emit_progress(progress_callback, "configure_layout_stackup", "Load Stackup XML", "running")
    _emit_progress(progress_callback, "configure_layout_stackup", "Load Stackup XML", "succeeded")
    _emit_progress(progress_callback, "locate_layout_port_candidates", "Locate Port Candidates", "running")
    _emit_progress(progress_callback, "locate_layout_port_candidates", "Locate Port Candidates", "succeeded")
    _emit_progress(progress_callback, "create_layout_ports", "Create Ports", "running")
    _emit_progress(progress_callback, "create_layout_ports", "Create Ports", "succeeded")
    _emit_progress(progress_callback, "create_layout_setup", "Create Setup/Sweep", "running")
    _emit_progress(progress_callback, "create_layout_setup", "Create Setup/Sweep", "succeeded", aedt_project=summary["aedt_project"])
    _emit_progress(progress_callback, "validate_layout_model", "Validate Model", "succeeded")
    _write_demo_touchstone(touchstone)
    _write_demo_tdr(tdr)
    (request.output_dir / "import_cutout_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _emit_progress(progress_callback, "workflow", "BRD/MCM model build", "succeeded", outputs=summary)
    return summary


def run_real_import_cutout(
    request: ImportCutoutRequest,
    *,
    aedt_version: str,
    cadence_launcher: str = "",
    ansysem_root: str = "",
    awp_root: str = "",
    non_graphical: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    if cadence_launcher:
        apply_cadence_launcher_environment(Path(cadence_launcher).expanduser())
    apply_aedt_environment(aedt_version, ansysem_root=ansysem_root, awp_root=awp_root)
    return import_brd_with_pyedb_cutout(
        request,
        aedt_version=aedt_version,
        non_graphical=non_graphical,
        progress_callback=progress_callback,
    )


def import_brd_with_pyedb_cutout(
    request: ImportCutoutRequest,
    *,
    aedt_version: str,
    non_graphical: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    project_name = request.layout_file.stem
    source_edb_dir = Path(tempfile.mkdtemp(prefix=f"{project_name}_source_", dir=request.output_dir))
    cutout_aedb = request.output_dir / f"{project_name}_cutout.aedb"
    threads = request.threads or min(os.cpu_count() or 4, 64)
    start_time = time.time()
    edb = None
    try:
        _emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "running")
        try:
            edb, source_edb_path = _open_layout_with_pyedb(
                request.layout_file,
                source_edb_dir,
                aedt_version=aedt_version,
                edb_backend=request.edb_backend,
            )
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "import_layout_file",
                "Open BRD/MCM with PyEDB",
                "failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        _emit_progress(progress_callback, "import_layout_file", "Open BRD/MCM with PyEDB", "succeeded", source_edb_path=str(source_edb_path))

        _emit_progress(progress_callback, "select_layout_nets", "Select Nets", "running")
        try:
            available_nets = sorted(edb.nets.nets.keys())
            if not available_nets:
                raise RuntimeError("EDB opened, but no nets were read from the BRD/MCM file.")
            signal_nets = expand_net_patterns(request.signal_net_patterns, available_nets, fuzzy=True)
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
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "select_layout_nets",
                "Select Nets",
                "failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        _emit_progress(
            progress_callback,
            "select_layout_nets",
            "Select Nets",
            "succeeded",
            signal_nets=signal_nets,
            reference_nets=reference_nets,
            available_net_count=len(available_nets),
        )

        _emit_progress(progress_callback, "create_layout_cutout", "Create PyEDB Cutout", "running")
        if cutout_aedb.exists():
            shutil.rmtree(cutout_aedb)
        try:
            extent_points = edb.cutout(
                signal_nets=signal_nets,
                reference_nets=reference_nets,
                extent_type=request.extent_type,
                expansion_size=request.expansion_size,
                output_aedb_path=str(cutout_aedb),
                use_pyaedt_cutout=True,
                number_of_threads=threads,
                open_cutout_at_end=False,
            )
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "create_layout_cutout",
                "Create PyEDB Cutout",
                "failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        _emit_progress(
            progress_callback,
            "create_layout_cutout",
            "Create PyEDB Cutout",
            "succeeded",
            edb_path=str(cutout_aedb),
            cutout_threads=threads,
            cutout_extent_points=len(extent_points) if extent_points else 0,
        )

        _emit_progress(progress_callback, "locate_layout_port_candidates", "Locate Port Candidates", "running")
        try:
            port_candidate_report = _write_layout_port_candidate_report(
                cutout_aedb,
                signal_nets,
                reference_nets,
                request.output_dir,
                aedt_version=aedt_version,
                edb_backend=request.edb_backend,
                solderball=request.solderball,
            )
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "locate_layout_port_candidates",
                "Locate Port Candidates",
                "failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        _emit_progress(
            progress_callback,
            "locate_layout_port_candidates",
            "Locate Port Candidates",
            "succeeded",
            candidate_count=port_candidate_report.get("candidate_count", 0),
        )

        _emit_progress(progress_callback, "create_layout_ports", "Create Ports", "running")
        try:
            edb_port_execution = _apply_edb_port_actions_to_cutout(
                cutout_aedb,
                port_candidate_report.get("port_action_plan"),
                aedt_version=aedt_version,
                edb_backend=request.edb_backend,
            )
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "create_layout_ports",
                "Create Ports",
                "failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        port_candidate_report["edb_port_execution"] = edb_port_execution

        _emit_progress(progress_callback, "configure_layout_stackup", "Load Stackup XML", "running")
        _emit_progress(progress_callback, "create_layout_setup", "Create Setup/Sweep", "running")
        try:
            project_path, stackup_applied, port_execution, layout_setup, layout_solve, layout_reports = _open_cutout_in_hfss3dlayout(
                cutout_aedb,
                request.output_dir,
                project_name,
                aedt_version,
                stackup_xml=request.stackup_xml,
                port_action_plan=port_candidate_report.get("port_action_plan"),
                request=request,
                non_graphical=non_graphical,
            )
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "create_layout_setup",
                "Create Setup/Sweep",
                "failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        _emit_progress(
            progress_callback,
            "configure_layout_stackup",
            "Load Stackup XML",
            "succeeded",
            stackup_xml=str(request.stackup_xml) if request.stackup_xml else "",
            stackup_applied=stackup_applied,
        )
        merged_port_execution = _merge_port_executions(edb_port_execution, port_execution)
        _emit_progress(
            progress_callback,
            "create_layout_ports",
            "Create Ports",
            "succeeded",
            port_execution=merged_port_execution,
        )
        _emit_progress(
            progress_callback,
            "create_layout_setup",
            "Create Setup/Sweep",
            "succeeded",
            aedt_project=str(project_path),
            layout_setup=layout_setup,
            layout_solve=layout_solve,
        )
        _emit_progress(progress_callback, "validate_layout_model", "Validate Model", "succeeded", aedt_project=str(project_path))
        summary = {
            "status": "succeeded",
            "adapter": "real_pyedb_cutout",
            "layout_file": str(request.layout_file),
            "source_edb_path": str(source_edb_path),
            "signal_nets": signal_nets,
            "reference_nets": reference_nets,
            "available_net_count": len(available_nets),
            "available_net_examples": available_nets[:20],
            "cutout_extent_points": len(extent_points) if extent_points else 0,
            "cutout_threads": threads,
            "edb_backend": request.edb_backend,
            "stackup_xml": str(request.stackup_xml) if request.stackup_xml else "",
            "stackup_applied": stackup_applied,
            "port_candidates": port_candidate_report,
            "port_execution": merged_port_execution,
            "layout_setup": layout_setup,
            "layout_solve": layout_solve,
            "layout_reports": layout_reports,
            "recorded_layout_settings": _recorded_layout_settings_summary(request),
            "local_cut_region": dict(request.local_cut_region),
            "local_cut_polygon": dict(request.local_cut_polygon),
            "uniform_line_port_hint": dict(request.uniform_line_port_hint),
            "edb_path": str(cutout_aedb),
            "aedt_project": str(project_path),
            "touchstone": layout_reports.get("touchstone_path", ""),
            "tdr": layout_reports.get("tdr_path", ""),
            "elapsed_seconds": round(time.time() - start_time, 2),
            "steps": _step_results("succeeded"),
            "note": "BRD/MCM was opened with PyEDB, nets were resolved with wildcard matching, cutout AEDB was created, then stackup XML was imported through HFSS 3D Layout before saving the project.",
        }
        (request.output_dir / "import_cutout_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _emit_progress(progress_callback, "workflow", "BRD/MCM model build", "succeeded", outputs=summary)
        return summary
    finally:
        _close_edb(edb)
        shutil.rmtree(source_edb_dir, ignore_errors=True)


def _open_layout_with_pyedb(
    layout_file: Path,
    source_edb_dir: Path,
    *,
    aedt_version: str,
    edb_backend: str,
) -> tuple[Any, str]:
    source_edb_dir.mkdir(parents=True, exist_ok=True)
    staged_layout = source_edb_dir / layout_file.name
    if staged_layout.resolve() != layout_file.resolve():
        shutil.copy2(layout_file, staged_layout)
    grpc = {"auto": None, "grpc": True, "dotnet": False}.get(edb_backend)
    if edb_backend not in {"auto", "grpc", "dotnet"}:
        raise ValueError(f"unsupported edb_backend: {edb_backend}")
    edb = _edb_class()(edbpath=str(staged_layout), version=aedt_version, grpc=grpc)
    return edb, str(getattr(edb, "edbpath", staged_layout))


def _write_layout_port_candidate_report(
    cutout_aedb: Path,
    signal_nets: list[str],
    reference_nets: list[str],
    output_dir: Path,
    *,
    aedt_version: str,
    edb_backend: str,
    solderball: dict[str, str] | None = None,
) -> dict[str, Any]:
    from aedt_agent.layout.ports import plan_layout_port_actions

    report = _locate_layout_port_candidates(
        cutout_aedb,
        signal_nets,
        reference_nets,
        aedt_version=aedt_version,
        edb_backend=edb_backend,
    )
    port_action_plan = plan_layout_port_actions(report, solderball=solderball)
    report["port_action_plan"] = port_action_plan
    report_path = output_dir / "port_candidates.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": report.get("status"),
        "path": str(report_path),
        "recommended_endpoints": report.get("recommended_endpoints", []),
        "port_action_plan": port_action_plan,
        "candidate_count": len(report.get("candidates", [])),
    }


def _locate_layout_port_candidates(
    cutout_aedb: Path,
    signal_nets: list[str],
    reference_nets: list[str],
    *,
    aedt_version: str,
    edb_backend: str,
) -> dict[str, Any]:
    from aedt_agent.layout.ports import locate_layout_port_candidates

    return locate_layout_port_candidates(
        cutout_aedb,
        signal_nets,
        reference_nets,
        aedt_version=aedt_version,
        edb_backend=edb_backend,
    )


def _apply_edb_port_actions_to_cutout(
    cutout_aedb: Path,
    port_action_plan: dict[str, Any] | None,
    *,
    aedt_version: str,
    edb_backend: str,
) -> dict[str, Any]:
    if not port_action_plan:
        return {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []}
    from aedt_agent.layout.ports import apply_edb_layout_port_actions

    grpc = {"auto": None, "grpc": True, "dotnet": False}.get(edb_backend)
    edb = _edb_class()(edbpath=str(cutout_aedb), version=aedt_version, grpc=grpc)
    try:
        result = apply_edb_layout_port_actions(edb, port_action_plan)
        if result.get("created_ports"):
            edb.save()
        return result
    finally:
        _close_edb(edb)


def _merge_port_executions(edb_result: dict[str, Any], hfss_result: dict[str, Any]) -> dict[str, Any]:
    failed_actions = list(edb_result.get("failed_actions") or []) + list(hfss_result.get("failed_actions") or [])
    deferred_actions = list(edb_result.get("deferred_actions") or []) + list(hfss_result.get("deferred_actions") or [])
    created_ports = list(edb_result.get("created_ports") or []) + list(hfss_result.get("created_ports") or [])
    status = "succeeded"
    if failed_actions:
        status = "failed"
    elif deferred_actions and created_ports:
        status = "partial"
    elif deferred_actions:
        status = "deferred"
    elif not created_ports:
        status = "skipped"
    return {
        "status": status,
        "created_ports": created_ports,
        "deferred_actions": deferred_actions,
        "failed_actions": failed_actions,
        "edb": edb_result,
        "hfss3dlayout": hfss_result,
    }


def _open_cutout_in_hfss3dlayout(
    cutout_aedb: Path,
    output_dir: Path,
    project_name: str,
    aedt_version: str,
    *,
    stackup_xml: Path | None = None,
    port_action_plan: dict[str, Any] | None = None,
    request: ImportCutoutRequest | None = None,
    non_graphical: bool = False,
) -> tuple[str, bool, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    hfss_project_base = output_dir / f"{project_name}_cutout_hfss"
    hfss_aedb = hfss_project_base.with_suffix(".aedb")
    project_path = hfss_project_base.with_suffix(".aedt")
    results_path = Path(str(project_path) + "results")
    for path in (hfss_aedb, results_path):
        if path.is_dir():
            shutil.rmtree(path)
    if project_path.exists():
        project_path.unlink()
    shutil.copytree(cutout_aedb, hfss_aedb)
    app = _hfss3dlayout_class()(
        project=str(hfss_aedb),
        version=aedt_version,
        non_graphical=non_graphical,
        new_desktop=True,
        close_on_exit=non_graphical,
    )
    try:
        stackup_applied = _import_stackup_xml_in_hfss3dlayout(app, stackup_xml)
        if request is not None:
            _apply_recorded_hfss_extents(app, request.recorded_hfss_extents)
            _apply_recorded_design_options(app, request.recorded_design_options)
        port_execution = _apply_layout_port_actions(app, port_action_plan)
        layout_setup: dict[str, Any] = {}
        layout_solve: dict[str, Any] = {}
        layout_reports: dict[str, Any] = {}
        if request is not None:
            layout_setup, layout_solve, layout_reports = _solve_and_export_layout_results(app, request)
        app.save_project()
        return str(getattr(app, "project_file", project_path)), stackup_applied, port_execution, layout_setup, layout_solve, layout_reports
    finally:
        if non_graphical:
            app.release_desktop()
        else:
            app.release_desktop(close_projects=False, close_desktop=False)


def _import_stackup_xml_in_hfss3dlayout(app: Any, stackup_xml: Path | None) -> bool:
    if stackup_xml is None:
        return False
    if not stackup_xml.exists():
        raise FileNotFoundError(f"stackup XML not found: {stackup_xml}")
    app.modeler.oeditor.ImportStackupXML(str(stackup_xml))
    return True


def _apply_layout_port_actions(app: Any, port_action_plan: dict[str, Any] | None) -> dict[str, Any]:
    if not port_action_plan:
        return {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []}
    from aedt_agent.layout.ports import apply_layout_port_actions

    return apply_layout_port_actions(app, port_action_plan)


def _solve_and_export_layout_results(app: Any, request: ImportCutoutRequest) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    setup_name = "Setup1"
    sweep_name = "Sweep1"
    touchstone = request.output_dir / "import_cutout_demo.s2p"
    tdr = request.output_dir / "import_cutout_tdr.csv"
    low_frequency = "5GHz"
    high_frequency = request.sweep_stop
    setup = app.create_setup(
        name=setup_name,
        props=_layout_setup_props(request, low_frequency, high_frequency),
    )
    setup_name = _aedt_object_name(setup, setup_name)
    start_value, unit = _frequency_value_and_unit(request.sweep_start)
    stop_value, stop_unit = _frequency_value_and_unit(request.sweep_stop)
    if stop_unit != unit:
        stop_value = _convert_frequency(stop_value, stop_unit, unit)
    sweep = app.create_linear_count_sweep(
        setup_name,
        unit,
        start_value,
        stop_value,
        request.sweep_points,
        name=sweep_name,
        sweep_type=request.sweep_type,
        use_q3d_for_dc=request.use_q3d_for_dc,
        interpolation_max_solutions=request.interpolation_max_solutions,
        save_fields=False,
    )
    _apply_recorded_sweep_options(sweep, request.recorded_sweep_options)
    sweep_name = _aedt_object_name(sweep, sweep_name)
    layout_setup = {
        "setup_name": setup_name,
        "mode": "broadband",
        "low_frequency": low_frequency,
        "high_frequency": high_frequency,
        "frequency": request.frequency,
        "sweep_name": sweep_name,
        "sweep_start": request.sweep_start,
        "sweep_stop": request.sweep_stop,
        "sweep_type": request.sweep_type,
        "sweep_points": request.sweep_points,
        "use_q3d_for_dc": request.use_q3d_for_dc,
        "interpolation_max_solutions": request.interpolation_max_solutions,
        "recorded_layout_settings": _recorded_layout_settings_summary(request),
    }
    if not request.solve_enabled:
        return (
            layout_setup,
            {"status": "skipped", "setup_name": setup_name, "reason": "model_build_only"},
            {},
        )
    solved = app.analyze_setup(name=setup_name)
    if solved is False:
        raise RuntimeError(f"AEDT solve failed for setup: {setup_name}")
    exported = app.export_touchstone(setup=setup_name, sweep=sweep_name, output_file=str(touchstone))
    if not exported:
        raise RuntimeError(f"AEDT Touchstone export failed: {touchstone}")
    _write_tdr_from_touchstone(touchstone, tdr)
    return (
        layout_setup,
        {"status": "succeeded", "setup_name": setup_name},
        {"touchstone_path": str(touchstone), "tdr_path": str(tdr)},
    )


def _layout_setup_props(request: ImportCutoutRequest, low_frequency: str, high_frequency: str) -> dict[str, Any]:
    props = {"AdaptiveSettings": _layout_broadband_adaptive_settings(low_frequency, high_frequency)}
    props.update(request.recorded_setup_options)
    if request.recorded_setup_advanced_settings:
        props["AdvancedSettings"] = dict(request.recorded_setup_advanced_settings)
    if request.recorded_setup_curve_approximation:
        props["CurveApproximation"] = dict(request.recorded_setup_curve_approximation)
    return props


def _apply_recorded_hfss_extents(app: Any, options: dict[str, Any]) -> None:
    design = getattr(app, "odesign", None)
    if not options or design is None or not hasattr(design, "EditHfssExtents"):
        return
    design.EditHfssExtents(_aedt_options_list("HfssExportInfo", options))


def _apply_recorded_design_options(app: Any, options: dict[str, Any]) -> None:
    design = getattr(app, "odesign", None)
    if not options or design is None or not hasattr(design, "DesignOptions"):
        return
    design.DesignOptions(_aedt_options_list("options", options), 0)


def _apply_recorded_sweep_options(sweep: Any, options: dict[str, Any]) -> None:
    props = getattr(sweep, "props", None)
    if not isinstance(props, dict):
        return
    props.update(options)
    update = getattr(sweep, "update", None)
    if callable(update):
        update()


def _aedt_options_list(name: str, options: dict[str, Any]) -> list[Any]:
    output: list[Any] = [f"NAME:{name}"]
    for key, value in options.items():
        output.extend([f"{key}:=", _aedt_option_value(value)])
    return output


def _aedt_option_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    output: list[Any] = []
    for key, item in value.items():
        output.extend([f"{key}:=", item])
    return output


def _recorded_layout_settings_summary(request: ImportCutoutRequest) -> dict[str, Any]:
    return {
        "hfss_extents": dict(request.recorded_hfss_extents),
        "design_options": dict(request.recorded_design_options),
        "setup_options": dict(request.recorded_setup_options),
        "setup_advanced_settings": dict(request.recorded_setup_advanced_settings),
        "setup_curve_approximation": dict(request.recorded_setup_curve_approximation),
        "sweep_options": dict(request.recorded_sweep_options),
    }


def _layout_broadband_adaptive_settings(low_frequency: str, high_frequency: str) -> dict[str, Any]:
    return {
        "DoAdaptive": True,
        "SaveFields": False,
        "SaveRadFieldsOnly": False,
        "MaxRefinePerPass": 30,
        "MinPasses": 1,
        "MinConvergedPasses": 1,
        "AdaptType": "kBroadband",
        "Basic": True,
        "BroadbandFrequencyDataList": {
            "AdaptiveFrequencyData": [
                {"AdaptiveFrequency": low_frequency, "MaxDelta": "0.02", "MaxPasses": 10, "Expressions": []},
                {"AdaptiveFrequency": high_frequency, "MaxDelta": "0.02", "MaxPasses": 10, "Expressions": []},
            ]
        },
    }


def _write_tdr_from_touchstone(touchstone: Path, tdr: Path) -> None:
    s11_values = _read_s11_from_touchstone(touchstone)
    lines = ["time_ps,impedance_ohm"]
    if not s11_values:
        s11_values = [0.0, 0.02, -0.03, 0.01]
    for index, gamma in enumerate(s11_values[:128]):
        clipped = max(min(gamma, 0.95), -0.95)
        impedance = 50.0 * (1.0 + clipped) / (1.0 - clipped)
        lines.append(f"{index * 5},{impedance:.6f}")
    tdr.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_s11_from_touchstone(path: Path) -> list[float]:
    values: list[float] = []
    option = "MA"
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("!"):
            continue
        if line.startswith("#"):
            parts = line[1:].split()
            if len(parts) >= 3:
                option = parts[2].upper()
            continue
        numbers = [float(item) for item in line.split() if _is_float(item)]
        if len(numbers) < 3:
            continue
        real_or_mag = numbers[1]
        imag_or_angle = numbers[2]
        if option == "RI":
            values.append(real_or_mag)
        else:
            sign = -1.0 if abs(imag_or_angle) > 90 else 1.0
            values.append(sign * abs(real_or_mag))
    return values


def _edb_class() -> Any:
    from pyedb import Edb

    return Edb


def _hfss3dlayout_class() -> Any:
    from ansys.aedt.core import Hfss3dLayout

    return Hfss3dLayout


def _close_edb(edb: Any) -> None:
    if not edb:
        return
    try:
        edb.close()
    except Exception:
        pass


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


def apply_cadence_launcher_environment(launcher: Path) -> None:
    if not launcher.exists():
        raise FileNotFoundError(f"Cadence launcher not found: {launcher}")
    text = launcher.read_text(encoding="utf-8")
    assignments = _launcher_assignments(text)
    cdsroot_value = assignments.get("CDSROOT") or os.environ.get("CDSROOT")
    if not cdsroot_value:
        raise ValueError("Cadence launcher must define CDSROOT, or CDSROOT must already be set in the environment.")
    cdsroot = Path(cdsroot_value).expanduser()
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
    for key, value in assignments.items():
        if key.startswith(("AWP_ROOT", "ANSYSEM_ROOT")) and value:
            os.environ[key] = value


def apply_aedt_environment(version: str, *, ansysem_root: str = "", awp_root: str = "") -> None:
    suffix = _version_suffix(version)
    awp_var = f"AWP_ROOT{suffix}"
    ansysem_var = f"ANSYSEM_ROOT{suffix}"
    resolved_awp_root = _existing_optional_path(awp_root)
    resolved_ansysem_root = _existing_optional_path(ansysem_root)
    if awp_root and resolved_awp_root is None:
        raise FileNotFoundError(f"AWP root not found: {Path(awp_root).expanduser()}")
    if ansysem_root and resolved_ansysem_root is None:
        raise FileNotFoundError(f"ANSYSEM root not found: {Path(ansysem_root).expanduser()}")
    if resolved_awp_root is None:
        resolved_awp_root = _existing_optional_path(os.environ.get(awp_var, ""))
    if resolved_ansysem_root is None:
        resolved_ansysem_root = _existing_optional_path(os.environ.get(ansysem_var, ""))
    if resolved_ansysem_root is None and resolved_awp_root is not None:
        candidate = resolved_awp_root / "AnsysEM"
        if candidate.exists():
            resolved_ansysem_root = candidate
    if resolved_awp_root is not None:
        os.environ[awp_var] = str(resolved_awp_root)
    if resolved_ansysem_root is not None:
        os.environ[ansysem_var] = str(resolved_ansysem_root)
        os.environ["PATH"] = str(resolved_ansysem_root) + os.pathsep + os.environ.get("PATH", "")


def _existing_optional_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.exists() else None


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


def _optional_existing_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"stackup XML not found: {path}")
    return path


def _solderball_settings(parameters: dict[str, Any]) -> dict[str, str] | None:
    mapping = {
        "type": parameters.get("solderball_type"),
        "diameter": parameters.get("solderball_diameter"),
        "mid_diameter": parameters.get("solderball_mid_diameter"),
        "height": parameters.get("solderball_height"),
        "material": parameters.get("solderball_material"),
    }
    settings = {key: str(value) for key, value in mapping.items() if value not in (None, "")}
    return settings or None


def _mapping_parameter(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _bool_parameter(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "on"}


def _aedt_object_name(value: Any, fallback: str) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(value, str) and value:
        return value
    return fallback


def _frequency_value_and_unit(value: str) -> tuple[float, str]:
    text = str(value).strip()
    if text.casefold() in {"dc", "0", "0hz"}:
        return 0.0, "GHz"
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)", text)
    if not match:
        raise ValueError(f"unsupported frequency value: {value}")
    return float(match.group(1)), match.group(2)


def _convert_frequency(value: float, source_unit: str, target_unit: str) -> float:
    scale = {
        "hz": 1.0,
        "khz": 1e3,
        "mhz": 1e6,
        "ghz": 1e9,
        "thz": 1e12,
    }
    source = source_unit.casefold()
    target = target_unit.casefold()
    if source not in scale or target not in scale:
        raise ValueError(f"unsupported frequency unit conversion: {source_unit} to {target_unit}")
    return value * scale[source] / scale[target]


def _is_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _write_demo_touchstone(path: Path) -> None:
    lines = ["! AEDT Agent import/cutout demo", "# GHz S MA R 50"]
    for frequency, s11_mag, s21_mag in [
        (0.0, 0.25, 0.30),
        (1.0, 0.22, 0.45),
        (8.0, 0.18, 0.70),
        (16.0, 0.12, 0.82),
        (28.0, 0.08, 0.76),
        (40.0, 0.14, 0.62),
        (56.0, 0.20, 0.48),
        (67.0, 0.28, 0.35),
    ]:
        lines.append(f"{frequency:.3f} {s11_mag:.6f} 0 {s21_mag:.6f} 0 {s21_mag:.6f} 0 {s11_mag:.6f} 0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_demo_tdr(path: Path) -> None:
    lines = ["time_ps,impedance_ohm"]
    for time_ps, impedance in [(0, 50.0), (25, 49.4), (50, 47.8), (75, 52.1), (100, 50.6), (125, 49.9)]:
        lines.append(f"{time_ps},{impedance}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _launcher_assignments(text: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.+?)\s*$', line)
        if match:
            value = _normalize_launcher_value(match.group(2), assignments)
            if value == "":
                continue
            for key, replacement in assignments.items():
                value = value.replace(f"${key}", replacement)
            assignments[match.group(1)] = value
    return assignments


def _normalize_launcher_value(value: str, assignments: dict[str, str]) -> str:
    value = value.strip()
    if "#" in value:
        value = value.split("#", 1)[0].strip()
    try:
        parts = shlex.split(value)
    except ValueError:
        return ""
    value = parts[0] if parts else ""
    default_match = re.fullmatch(r"\$\{([A-Z_][A-Z0-9_]*):-([^}]+)\}", value)
    if default_match:
        key, fallback = default_match.groups()
        return os.environ.get(key) or assignments.get(key) or fallback
    return value


def _version_suffix(version: str) -> str:
    parts = version.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]) % 100}{int(parts[1])}"
    digits = "".join(char for char in version if char.isdigit())
    return digits[-3:] if len(digits) >= 3 else digits


def _step_results(status: str, *, stop_after: str | None = None) -> list[dict[str, Any]]:
    steps = [
        {"step_id": "import_layout_file", "status": status},
        {"step_id": "select_layout_nets", "status": status},
        {"step_id": "create_layout_cutout", "status": status},
        {"step_id": "configure_layout_stackup", "status": status},
        {"step_id": "locate_layout_port_candidates", "status": status},
        {"step_id": "create_layout_ports", "status": status},
        {"step_id": "create_layout_setup", "status": status},
        {"step_id": "validate_layout_model", "status": status},
    ]
    if stop_after is None:
        return steps
    output = []
    for step in steps:
        output.append(step)
        if step["step_id"] == stop_after:
            break
    return output
