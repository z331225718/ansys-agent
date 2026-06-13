from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from aedt_agent.layout.import_cutout import expand_net_patterns
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
        polygon = bbox_to_polygon(region)
        request.artifact_dir.mkdir(parents=True, exist_ok=True)

        source_dir = Path(tempfile.mkdtemp(prefix="source_", dir=request.artifact_dir))
        source_layout = source_dir / request.layout_file.name
        shutil.copy2(request.layout_file, source_layout)

        cutout_aedb = request.artifact_dir / f"{request.layout_file.stem}_cutout.aedb"
        hfss_aedb = request.artifact_dir / f"{request.layout_file.stem}_cutout_hfss.aedb"
        for path in (cutout_aedb, hfss_aedb):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

        selected_signal_nets: list[str] = []
        selected_reference_nets: list[str] = []
        cutout_extent_points: Any = polygon["points"]
        edb = self._edb_class()(
            edbpath=str(source_layout),
            version=request.environment.version,
            grpc=_grpc_mode(request.environment.edb_backend),
        )
        try:
            available_nets = _available_net_names(edb)
            selected_signal_nets = expand_net_patterns(request.signal_nets, available_nets, fuzzy=True)
            selected_reference_nets = expand_net_patterns(request.reference_nets, available_nets)
            if not selected_signal_nets:
                raise ValueError(f"no signal nets matched: {request.signal_nets}")
            if not selected_reference_nets:
                raise ValueError(f"no reference nets matched: {request.reference_nets}")
            cutout_extent_points = edb.cutout(
                signal_nets=selected_signal_nets,
                reference_nets=selected_reference_nets,
                extent_type="Polygon",
                expansion_size=0.0,
                output_aedb_path=str(cutout_aedb),
                use_pyaedt_cutout=True,
                number_of_threads=1,
                open_cutout_at_end=False,
                custom_extent=polygon["points"],
            )
            save = getattr(edb, "save", None)
            if callable(save):
                save()
        finally:
            _close_edb(edb)

        if cutout_aedb != hfss_aedb:
            if hfss_aedb.exists():
                shutil.rmtree(hfss_aedb)
            if cutout_aedb.is_dir():
                shutil.copytree(cutout_aedb, hfss_aedb)
            elif cutout_aedb.exists():
                shutil.copy2(cutout_aedb, hfss_aedb)

        recorded = _recorded_settings_summary(request.recorded_layout_settings)
        app = self._hfss3dlayout_class()(
            project=str(hfss_aedb),
            version=request.environment.version,
            non_graphical=request.environment.non_graphical,
            new_desktop=True,
            close_on_exit=True,
        )
        try:
            stackup_imported = _import_stackup(app, request.stackup_xml)
            _apply_recorded_hfss_extents(app, recorded["hfss_extents"])
            _apply_recorded_design_options(app, recorded["design_options"])
            layout_setup = _create_build_only_setup(app, recorded)
            app.save_project()
            aedt_project = str(getattr(app, "project_file", hfss_aedb.with_suffix(".aedt")))
        finally:
            release = getattr(app, "release_desktop", None)
            if callable(release):
                release(close_projects=True, close_desktop=True)

        summary = _summary(
            request=request,
            source_layout=source_layout,
            cutout_aedb=cutout_aedb,
            hfss_aedb=hfss_aedb,
            aedt_project=aedt_project,
            signal_nets=selected_signal_nets,
            reference_nets=selected_reference_nets,
            local_cut_region=region,
            local_cut_polygon=polygon,
            stackup_imported=stackup_imported,
            cutout_extent_points=cutout_extent_points,
            layout_setup=layout_setup,
            recorded_settings=recorded,
        )
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


def _grpc_mode(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if normalized in {"", "auto"}:
        return None
    if normalized in {"grpc", "true", "1", "yes", "on"}:
        return True
    if normalized in {"legacy", "dotnet", "false", "0", "no", "off"}:
        return False
    raise ValueError(f"unsupported edb_backend: {value}")


def _import_stackup(app: Any, stackup_xml: Path | None) -> bool:
    if stackup_xml is None:
        return False
    editor = getattr(getattr(app, "modeler", None), "oeditor", None)
    if editor is None or not hasattr(editor, "ImportStackupXML"):
        return False
    editor.ImportStackupXML(str(stackup_xml))
    return True


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


def _create_build_only_setup(app: Any, recorded: dict[str, Any]) -> dict[str, Any]:
    setup_name = "Setup1"
    sweep_name = "Sweep1"
    unit = "GHz"
    start = 0.0
    stop = 67.0
    count = 1341
    sweep_type = "Interpolating"
    sweep_options = dict(recorded.get("sweep_options") or {})
    props = dict(recorded.get("setup_options") or {})
    advanced_settings = dict(recorded.get("setup_advanced_settings") or {})
    curve_approximation = dict(recorded.get("setup_curve_approximation") or {})
    if advanced_settings:
        props["AdvancedSettings"] = advanced_settings
    if curve_approximation:
        props["CurveApproximation"] = curve_approximation

    setup = app.create_setup(name=setup_name, props=props)
    setup_name = str(getattr(setup, "name", setup_name))
    use_q3d_for_dc = bool(sweep_options.get("UseQ3DForDC", False))
    interpolation_max_solutions = sweep_options.get("MaxSolutions", 2500)
    sweep = app.create_linear_count_sweep(
        setup_name,
        unit,
        start,
        stop,
        count,
        name=sweep_name,
        sweep_type=sweep_type,
        use_q3d_for_dc=use_q3d_for_dc,
        interpolation_max_solutions=interpolation_max_solutions,
        save_fields=False,
    )
    props_dict = getattr(sweep, "props", None)
    if isinstance(props_dict, dict):
        props_dict.update(sweep_options)
        update = getattr(sweep, "update", None)
        if callable(update):
            update()
    sweep_name = str(getattr(sweep, "name", sweep_name))
    return {
        "status": "created",
        "setup_name": setup_name,
        "sweep_name": sweep_name,
        "unit": unit,
        "start": start,
        "stop": stop,
        "count": count,
        "sweep_type": sweep_type,
        "use_q3d_for_dc": use_q3d_for_dc,
        "interpolation_max_solutions": interpolation_max_solutions,
    }


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
        output.extend([f"{key}:=", _aedt_option_value(item)])
    return output


def _close_edb(edb: Any) -> None:
    close = getattr(edb, "close", None)
    if callable(close):
        close()


def _summary(
    *,
    request: BrdRealBuildRequest,
    source_layout: Path,
    cutout_aedb: Path,
    hfss_aedb: Path,
    aedt_project: str,
    signal_nets: list[str],
    reference_nets: list[str],
    local_cut_region: dict[str, Any],
    local_cut_polygon: dict[str, Any],
    stackup_imported: bool,
    cutout_extent_points: Any,
    layout_setup: dict[str, Any],
    recorded_settings: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "adapter": "real_pyedb_hfss3dlayout_build_only",
        "layout_file": str(request.layout_file),
        "source_edb_path": str(source_layout),
        "edb_path": str(cutout_aedb),
        "hfss_aedb_path": str(hfss_aedb),
        "aedt_project": aedt_project,
        "signal_nets": list(signal_nets),
        "reference_nets": list(reference_nets),
        "local_cut_region": local_cut_region,
        "local_cut_polygon": local_cut_polygon,
        "stackup": {
            "path": str(request.stackup_xml) if request.stackup_xml is not None else "",
            "imported": stackup_imported,
        },
        "cutout_extent_points": cutout_extent_points,
        "port_candidates": {"status": "not_evaluated", "candidate_count": 0},
        "port_execution": {"status": "skipped", "created_ports": [], "deferred_actions": [], "failed_actions": []},
        "layout_setup": layout_setup,
        "layout_solve": {"status": "skipped", "reason": "model_review_only"},
        "layout_reports": {},
        "recorded_layout_settings": recorded_settings,
        "target_metrics": list(request.target_metrics),
        "steps": [
            "stage_layout",
            "pyedb_cutout",
            "copy_cutout_for_hfss",
            "hfss3dlayout_build",
            "save_project",
        ],
    }


def _available_net_names(edb: Any) -> list[str]:
    nets = getattr(getattr(edb, "nets", None), "nets", {})
    if isinstance(nets, dict):
        return list(nets.keys())
    return list(nets or [])
