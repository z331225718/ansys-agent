from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from aedt_agent.infrastructure.brd_real_build import RealAedtEnvironment
from aedt_agent.layout.channel_scoring import (
    parse_tdr_csv,
    parse_touchstone,
)


class ArtifactExportError(RuntimeError):
    """Raised when AEDT did not create a required output artifact."""


class ArtifactValidationError(ValueError):
    """Raised when an exported artifact is empty or malformed."""


TDR_EXPRESSION = re.compile(
    r"^(?:"
    r"TDRZt\("
    r"([A-Za-z_][A-Za-z0-9_.:-]*)"
    r"(?:,"
    r"([A-Za-z_][A-Za-z0-9_.:-]*)"
    r")?"
    r"\)"
    r"|"
    r"TDRZ\(([A-Za-z_][A-Za-z0-9_.:-]*)\)"
    r")$"
)
REPORT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
TOUCHSTONE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.s[1-9][0-9]*p$", re.IGNORECASE)
AEDT_DIFF_PAIR_NAME = re.compile(
    r"\bDif=['\"]([A-Za-z_][A-Za-z0-9_.:-]*)['\"]"
)


@dataclass(frozen=True)
class BrdRealSolveRequest:
    project_path: Path
    artifact_dir: Path
    setup_name: str
    sweep_name: str
    solution_name: str
    touchstone_name: str
    tdr_report_name: str
    tdr_expression: str
    expected_port_count: int
    environment: RealAedtEnvironment
    run_analyze: bool = True
    export_tdr: bool = True
    tdr_differential_pairs: bool = False
    tdr_observation_port: str = ""
    tdr_reference_impedance_ohm: float | None = None
    project_copy_mode: str = "checkpoint_copy"


@dataclass(frozen=True)
class BrdRealSolveResult:
    project_checkpoint: str
    solved_project: str
    touchstone_path: str
    tdr_path: str
    solve_manifest_path: str
    summary: dict[str, Any]


class BrdRealSolveAdapter:
    def __init__(
        self,
        *,
        hfss3dlayout_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._hfss3dlayout_factory = hfss3dlayout_factory

    def run(
        self,
        request: BrdRealSolveRequest,
    ) -> BrdRealSolveResult:
        _validate_request(request)
        project_path = request.project_path.resolve()
        artifact_dir = request.artifact_dir.resolve()
        source_digest = _sha256(project_path)

        if request.project_copy_mode == "working_project":
            project_checkpoint = project_path
            project_checkpoint_edb = project_path.with_suffix(".aedb")
            solved_project = project_path
            solved_edb = project_path.with_suffix(".aedb")
            check_source_unchanged = False
        else:
            checkpoint_dir = artifact_dir / "input_checkpoint"
            checkpoint_dir.mkdir(parents=True, exist_ok=False)
            project_checkpoint = checkpoint_dir / project_path.name
            shutil.copy2(project_path, project_checkpoint)
            project_checkpoint_edb = _copy_sidecar_edb(
                project_path,
                project_checkpoint,
            )
            _copy_results_directory(project_path, project_checkpoint)

            solved_project = (
                artifact_dir / f"{project_path.stem}.solved.aedt"
            )
            shutil.copy2(project_checkpoint, solved_project)
            solved_edb = _copy_sidecar_edb(project_checkpoint, solved_project)
            _copy_results_directory(project_checkpoint, solved_project)
            check_source_unchanged = True

        touchstone_path = artifact_dir / request.touchstone_name
        tdr_path = artifact_dir / f"{request.tdr_report_name}.csv"
        raw_tdr_dir = artifact_dir / "_aedt_report_tmp"

        app = self._hfss3dlayout_class()(
            project=str(solved_project),
            version=request.environment.version,
            non_graphical=request.environment.non_graphical,
            new_desktop=True,
            close_on_exit=False,
            remove_lock=False,
        )
        try:
            resolved_solution_name = _validate_project_contract(
                app,
                request,
            )
            if request.run_analyze:
                if app.analyze_setup(
                    name=request.setup_name,
                    blocking=True,
                ) is not True:
                    raise ArtifactExportError(
                        f"AEDT solve failed: {request.setup_name}"
                    )
            if app.save_project(
                file_name=str(solved_project)
            ) is False:
                raise ArtifactExportError(
                    "AEDT solved project save failed"
                )
            exported = app.export_touchstone(
                setup=request.setup_name,
                sweep=request.sweep_name,
                output_file=str(touchstone_path),
            )
            if not exported:
                raise ArtifactExportError(
                    "AEDT Touchstone export failed"
                )
            touchstone_samples = _validated_touchstone(
                touchstone_path
            )

            tdr_samples: list[dict[str, float]] = []
            tdr_export_method = ""
            if request.export_tdr:
                _write_tdr_from_touchstone(
                    touchstone_path,
                    tdr_path,
                    request,
                )
                tdr_export_method = "skrf_touchstone_step_response"
                tdr_samples = _validated_tdr(tdr_path)
            else:
                tdr_samples = []
                if raw_tdr_dir.exists():
                    shutil.rmtree(raw_tdr_dir, ignore_errors=True)
        except Exception:
            _release_desktop_best_effort(app, request)
            raise

        if check_source_unchanged and _sha256(project_path) != source_digest:
            _release_desktop_best_effort(app, request)
            raise ArtifactValidationError(
                "source AEDT project changed during solve"
            )

        execution_attestation = {
            "kind": "real_aedt",
            "adapter": "BrdRealSolveAdapter",
            "backend": "ansys.aedt.core.Hfss3dLayout",
            "aedt_version": request.environment.version,
            "non_graphical": request.environment.non_graphical,
            "analyze_executed": request.run_analyze,
        }
        summary = {
            "status": "succeeded",
            "adapter": "real_hfss3dlayout_solve",
            "execution_attestation": execution_attestation,
            "setup_name": request.setup_name,
            "sweep_name": request.sweep_name,
            "solution_name": resolved_solution_name,
            "requested_solution_name": request.solution_name,
            "analyze_executed": request.run_analyze,
            "tdr_exported": request.export_tdr,
            "project_copy_mode": request.project_copy_mode,
            "expected_port_count": request.expected_port_count,
            "tdr_differential_pairs": request.tdr_differential_pairs,
            "tdr_observation_port": request.tdr_observation_port,
            "tdr_reference_impedance_ohm": _tdr_reference_impedance(request),
            "tdr_export_method": tdr_export_method,
            "touchstone_sample_count": len(touchstone_samples),
            "tdr_sample_count": len(tdr_samples),
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only"
            if request.export_tdr
            else "deferred_manual_export",
            "sidecar_edb_copied": solved_edb is not None,
        }
        manifest_path = artifact_dir / "solve_manifest.json"
        manifest = {
            "version": 1,
            "input": {
                "source_project": _artifact_record(project_path),
                "project_checkpoint": _artifact_record(
                    project_checkpoint
                ),
            },
            "outputs": {
                "solved_project": _artifact_record(solved_project),
                "touchstone": _artifact_record(touchstone_path),
            },
            "summary": summary,
        }
        if request.export_tdr:
            manifest["outputs"]["tdr"] = _artifact_record(tdr_path)
        if project_checkpoint_edb is not None:
            if project_checkpoint_edb.exists():
                manifest["input"]["project_checkpoint_edb"] = _artifact_record(
                    project_checkpoint_edb
                )
        if solved_edb is not None:
            if solved_edb.exists():
                manifest["outputs"]["solved_edb"] = _artifact_record(solved_edb)
        _atomic_write_json(manifest_path, manifest)
        release_warning = _release_desktop_best_effort(app, request)
        if release_warning:
            summary["aedt_exit_warning"] = release_warning
            manifest["summary"] = summary
            _atomic_write_json(manifest_path, manifest)
        return BrdRealSolveResult(
            project_checkpoint=str(project_checkpoint),
            solved_project=str(solved_project),
            touchstone_path=str(touchstone_path),
            tdr_path=str(tdr_path) if request.export_tdr else "",
            solve_manifest_path=str(manifest_path),
            summary=summary,
        )

    def _hfss3dlayout_class(self) -> Callable[..., Any]:
        if self._hfss3dlayout_factory is not None:
            return self._hfss3dlayout_factory
        from ansys.aedt.core import Hfss3dLayout

        return Hfss3dLayout


def _release_desktop_best_effort(
    app: Any,
    request: BrdRealSolveRequest,
) -> dict[str, Any] | None:
    edb_session_count = _edb_session_count(app)
    if edb_session_count:
        return {
            "status": "skipped_after_artifacts",
            "reason": "pyaedt_edb_sessions_present",
            "edb_session_count": edb_session_count,
        }
    release = getattr(app, "release_desktop", None)
    if not callable(release):
        return {
            "status": "skipped",
            "reason": "release_desktop is not callable",
        }
    outcome: dict[str, Any] = {}

    def _release() -> None:
        try:
            release(
                close_projects=request.environment.non_graphical,
                close_desktop=request.environment.non_graphical,
            )
        except Exception as exc:
            outcome.update(
                {
                    "status": "failed_after_artifacts",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    timeout_seconds = _release_desktop_timeout_seconds()
    thread = threading.Thread(
        target=_release,
        name="aedt-release-desktop",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        return {
            "status": "timed_out_after_artifacts",
            "timeout_seconds": timeout_seconds,
        }
    return outcome or None


def _release_desktop_timeout_seconds() -> float:
    raw = os.environ.get("AEDT_AGENT_RELEASE_DESKTOP_TIMEOUT_SECONDS", "30")
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return max(value, 0.1)


def _edb_session_count(app: Any) -> int:
    sessions = getattr(app, "_edb_sessions", None)
    if sessions is None:
        return 0
    try:
        return len(sessions)
    except TypeError:
        return 1


def _validate_request(request: BrdRealSolveRequest) -> None:
    project_path = Path(request.project_path)
    if project_path.suffix.casefold() != ".aedt":
        raise ValueError("project_path must end with .aedt")
    if not project_path.is_file():
        raise FileNotFoundError(
            f"project_path not found: {project_path}"
        )
    artifact_dir = Path(request.artifact_dir)
    if not artifact_dir.is_dir():
        raise ValueError(
            f"artifact_dir must be an existing directory: {artifact_dir}"
        )
    for field_name in (
        "setup_name",
        "sweep_name",
        "solution_name",
    ):
        value = str(getattr(request, field_name))
        if not value.strip():
            raise ValueError(f"{field_name} is required")
    if not TOUCHSTONE_NAME.fullmatch(request.touchstone_name):
        raise ValueError(
            "touchstone_name must be a safe Touchstone filename"
        )
    if not REPORT_NAME.fullmatch(request.tdr_report_name):
        raise ValueError("tdr_report_name must be a safe report name")
    if not TDR_EXPRESSION.fullmatch(request.tdr_expression):
        raise ValueError("tdr_expression is not approved")
    if (
        not isinstance(request.expected_port_count, int)
        or isinstance(request.expected_port_count, bool)
        or request.expected_port_count < 2
    ):
        raise ValueError(
            "expected_port_count must be an integer of at least 2"
        )
    if not request.environment.version.strip():
        raise ValueError("AEDT version is required")
    if request.project_copy_mode not in {"checkpoint_copy", "working_project"}:
        raise ValueError(
            "project_copy_mode must be checkpoint_copy or working_project"
        )


def _validate_project_contract(
    app: Any,
    request: BrdRealSolveRequest,
) -> str:
    if request.setup_name not in set(app.setup_names):
        raise ValueError(
            f"setup not found: {request.setup_name}"
        )
    setup_sweeps = {str(name) for name in app.setup_sweeps_names}
    if request.solution_name in setup_sweeps:
        resolved_solution_name = request.solution_name
    elif request.setup_name in setup_sweeps:
        resolved_solution_name = request.setup_name
    else:
        raise ValueError(
            f"setup sweep not found: {request.solution_name}"
        )
    ports = {str(port) for port in app.port_list}
    port_count = len(ports)
    if port_count != request.expected_port_count:
        raise ValueError(
            f"expected {request.expected_port_count} ports, "
            f"found {port_count}"
        )
    expression = TDR_EXPRESSION.fullmatch(
        request.tdr_expression
    )
    assert expression is not None
    report_ports = (
        ports
        | _report_port_names(app)
        | _project_diff_pair_names(request.project_path)
    )
    expression_ports = [port for port in expression.groups() if port]
    missing_ports = [port for port in expression_ports if port not in report_ports]
    if missing_ports:
        raise ValueError(
            f"TDR expression port not found: {missing_ports[0]}"
        )
    return resolved_solution_name


def _report_port_names(app: Any) -> set[str]:
    names: set[str] = set()
    for attr_name in (
        "excitations",
        "excitation_names",
        "differential_pairs",
        "differential_pair_names",
    ):
        value = getattr(app, attr_name, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        names.update(_collect_names(value))
    return names


def _project_diff_pair_names(project_path: Path) -> set[str]:
    try:
        project_text = project_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return set()
    return set(AEDT_DIFF_PAIR_NAME.findall(project_text))


def _write_tdr_from_touchstone(
    touchstone_path: Path,
    output_path: Path,
    request: BrdRealSolveRequest,
) -> None:
    try:
        import numpy as np
        import skrf as rf
    except ModuleNotFoundError as exc:
        raise ArtifactExportError(
            "scikit-rf is required to derive TDR from Touchstone"
        ) from exc

    try:
        network = rf.Network(str(touchstone_path))
    except Exception as exc:
        raise ArtifactValidationError(
            f"failed to parse Touchstone with scikit-rf: {touchstone_path}"
        ) from exc
    if network.nports < 1 or len(network.f) < 2:
        raise ArtifactValidationError(
            "Touchstone must contain at least one port and two frequency points"
        )

    response_network = network.copy()
    trace_index = _touchstone_tdr_trace_index(request, response_network.nports)
    reference_impedance = _tdr_reference_impedance(request)
    if _touchstone_tdr_uses_mixed_mode(request, response_network.nports):
        pair_count = response_network.nports // 2
        if reference_impedance > 0:
            _renormalize_network(response_network, reference_impedance / 2.0)
        response_network.se2gmm(p=pair_count)
        trace_index = min(trace_index, pair_count - 1)
    elif reference_impedance > 0:
        _renormalize_network(response_network, reference_impedance)

    if response_network.f[0] > 0:
        try:
            response_network = response_network.extrapolate_to_dc(
                kind="linear"
            )
        except Exception:
            pass

    try:
        times_seconds, reflection = response_network.step_response(
            squeeze=False
        )
    except Exception as exc:
        raise ArtifactExportError(
            "failed to compute TDR step response from Touchstone"
        ) from exc

    time_values = np.asarray(times_seconds, dtype=float)
    reflection_values = np.real(
        np.asarray(reflection)[:, trace_index, trace_index]
    )
    if time_values.size == 0 or reflection_values.size == 0:
        raise ArtifactValidationError(
            "Touchstone TDR step response produced no samples"
        )
    non_negative = time_values >= 0
    if np.any(non_negative):
        time_values = time_values[non_negative]
        reflection_values = reflection_values[non_negative]

    z0 = _touchstone_tdr_reference_impedance(response_network, trace_index)
    denominator = 1.0 - reflection_values
    impedance = np.array(
        [
            math.nan if abs(float(value)) < 1e-12 else z0 * (1.0 + rho) / value
            for rho, value in zip(reflection_values, denominator, strict=False)
        ],
        dtype=float,
    )
    rows = [
        (float(time_value) * 1e12, float(impedance_value))
        for time_value, impedance_value in zip(
            time_values,
            impedance,
            strict=False,
        )
        if math.isfinite(float(impedance_value))
    ]
    if not rows:
        raise ArtifactValidationError(
            "Touchstone TDR impedance conversion produced no finite samples"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["time_ps", "impedance_ohm"])
        for time_ps, impedance_ohm in rows:
            writer.writerow([f"{time_ps:.12g}", f"{impedance_ohm:.12g}"])


def _touchstone_tdr_uses_mixed_mode(
    request: BrdRealSolveRequest,
    port_count: int,
) -> bool:
    if port_count < 2 or port_count % 2:
        return False
    port_name = request.tdr_observation_port.strip().casefold()
    if request.tdr_differential_pairs or port_name.startswith("diff"):
        return True
    return any(
        str(port).casefold().startswith("diff")
        for port in TDR_EXPRESSION.fullmatch(request.tdr_expression).groups()
        if port
    )


def _touchstone_tdr_trace_index(
    request: BrdRealSolveRequest,
    port_count: int,
) -> int:
    expression = TDR_EXPRESSION.fullmatch(request.tdr_expression)
    expression_ports = [port for port in expression.groups() if port] if expression else []
    port_name = request.tdr_observation_port.strip() or (
        expression_ports[0] if expression_ports else ""
    )
    match = re.search(r"([0-9]+)$", port_name)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < port_count:
            return index
    return 0


def _touchstone_tdr_reference_impedance(
    network: Any,
    trace_index: int,
) -> float:
    try:
        z0 = network.z0[:, trace_index]
    except Exception:
        return 50.0
    real_values = [float(value.real) for value in z0 if float(value.real) > 0]
    if not real_values:
        return 50.0
    return sum(real_values) / len(real_values)


def _tdr_reference_impedance(request: BrdRealSolveRequest) -> float:
    if request.tdr_reference_impedance_ohm is not None:
        value = float(request.tdr_reference_impedance_ohm)
        if value > 0:
            return value
    return 0.0


def _renormalize_network(network: Any, reference_impedance_ohm: float) -> None:
    if reference_impedance_ohm <= 0:
        return
    try:
        network.renormalize(reference_impedance_ohm)
    except Exception as exc:
        raise ArtifactExportError(
            f"failed to renormalize Touchstone to {reference_impedance_ohm:g} ohm"
        ) from exc


def _create_tdr_report(
    app: Any,
    request: BrdRealSolveRequest,
    solution_name: str,
) -> bool:
    if _create_native_tdr_report(app, request, solution_name):
        return True
    return bool(
        app.post.create_report(
            expressions=request.tdr_expression,
            setup_sweep_name=solution_name,
            domain="Time",
            variations={"Time": ["All"]},
            primary_sweep_variable="Time",
            plot_name=request.tdr_report_name,
            context={
                "pulse_rise_time": "10ps",
                "step_time": "1ps",
                "time_windowing": 4,
                "maximum_time": "10ns",
                "use_pulse_in_tdr": True,
                "differential_pairs": request.tdr_differential_pairs,
            },
        )
    )


def _export_tdr_report_or_solution_data(
    app: Any,
    request: BrdRealSolveRequest,
    solution_name: str,
    raw_tdr_dir: Path,
    output_path: Path,
) -> str:
    if _create_tdr_report(app, request, solution_name):
        raw_tdr_dir.mkdir(parents=False, exist_ok=False)
        try:
            raw_tdr_path = _validated_export_path(
                _export_tdr_report(
                    app,
                    request.tdr_report_name,
                    raw_tdr_dir,
                ),
                raw_tdr_dir,
            )
            _normalize_tdr_report_csv(
                raw_tdr_path,
                output_path,
                request.tdr_expression,
            )
        finally:
            shutil.rmtree(raw_tdr_dir, ignore_errors=True)
        return "report_csv"
    if _export_tdr_solution_data(
        app,
        request,
        solution_name,
        output_path,
    ):
        return "solution_data"
    return ""


def _export_tdr_solution_data(
    app: Any,
    request: BrdRealSolveRequest,
    solution_name: str,
    output_path: Path,
) -> bool:
    create_report = getattr(getattr(app, "post", None), "create_report", None)
    if not callable(create_report):
        return False
    report_name = f"{request.tdr_report_name}_SolutionData"
    try:
        report = create_report(
            expressions=request.tdr_expression,
            setup_sweep_name=solution_name,
            domain="Time",
            variations={"Time": ["All"]},
            primary_sweep_variable="Time",
            plot_name=report_name,
            context={
                "pulse_rise_time": 1.49253731343284e-11,
                "step_time": 2.98507462686567e-12,
                "time_windowing": 4,
                "maximum_time": 2.98507462686567e-10,
                "use_pulse_in_tdr": True,
                "differential_pairs": request.tdr_differential_pairs,
            },
        )
        get_solution_data = getattr(report, "get_solution_data", None)
        if not callable(get_solution_data):
            return False
        solution_data = get_solution_data()
        if solution_data is None:
            return False
        times, impedances, time_unit = _tdr_trace_from_solution_data(
            solution_data,
            request.tdr_expression,
        )
        if not times or not impedances or len(times) != len(impedances):
            return False
        _write_tdr_solution_data_csv(
            output_path,
            times,
            impedances,
            time_unit=time_unit,
        )
        return True
    except Exception:
        return False
    finally:
        delete_report = getattr(getattr(app, "post", None), "delete_report", None)
        if callable(delete_report):
            try:
                delete_report(report_name)
            except Exception:
                pass


def _tdr_trace_from_solution_data(
    solution_data: Any,
    expression: str,
) -> tuple[list[Any], list[Any], str]:
    units = getattr(solution_data, "units_sweeps", {}) or {}
    time_unit = str(units.get("Time") or units.get("time") or "ns")
    times = _sequence_list(
        getattr(solution_data, "primary_sweep_values", None)
    )

    data_real = getattr(solution_data, "data_real", None)
    if callable(data_real):
        for args in ((), (expression,)):
            try:
                impedances = _sequence_list(data_real(*args))
            except Exception:
                continue
            if _has_matching_samples(times, impedances):
                return times, impedances, time_unit

    get_expression_data = getattr(solution_data, "get_expression_data", None)
    if callable(get_expression_data):
        call_shapes = (
            ((expression,), {"formula": "real"}),
            ((expression,), {}),
            ((), {"formula": "real"}),
            ((), {}),
        )
        for args, kwargs in call_shapes:
            try:
                expression_data = get_expression_data(*args, **kwargs)
            except Exception:
                continue
            expression_times, impedances = _expression_data_trace(
                expression_data
            )
            if _has_matching_samples(expression_times, impedances):
                return expression_times, impedances, time_unit
            if _has_matching_samples(times, impedances):
                return times, impedances, time_unit

    return [], [], time_unit


def _expression_data_trace(data: Any) -> tuple[list[Any], list[Any]]:
    if isinstance(data, dict):
        time_values = _first_mapping_value(data, "time", "Time", "x", "X")
        impedance_values = _first_mapping_value(
            data,
            "impedance",
            "Impedance",
            "y",
            "Y",
            "values",
        )
        return _sequence_list(time_values), _sequence_list(impedance_values)
    if isinstance(data, (list, tuple)) and len(data) >= 2:
        return _sequence_list(data[0]), _sequence_list(data[1])
    return [], []


def _first_mapping_value(data: dict[Any, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _sequence_list(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _has_matching_samples(times: list[Any], values: list[Any]) -> bool:
    return bool(times) and bool(values) and len(times) == len(values)


def _write_tdr_solution_data_csv(
    output_path: Path,
    times: list[Any],
    impedances: list[Any],
    *,
    time_unit: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    factor = _time_unit_to_ps_factor(time_unit)
    with output_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=["time_ps", "impedance_ohm"],
        )
        writer.writeheader()
        for time_value, impedance in zip(times, impedances, strict=True):
            writer.writerow(
                {
                    "time_ps": float(time_value) * factor,
                    "impedance_ohm": float(impedance),
                }
            )


def _create_native_tdr_report(
    app: Any,
    request: BrdRealSolveRequest,
    solution_name: str,
) -> bool:
    design = getattr(app, "odesign", None)
    get_module = getattr(design, "GetModule", None)
    if not callable(get_module):
        return False
    module = get_module("ReportSetup")
    create_report = getattr(module, "CreateReport", None)
    if not callable(create_report):
        return False
    _delete_existing_report(module, request.tdr_report_name)
    candidates = [
        request.solution_name,
        solution_name,
    ]
    last_error: Exception | None = None
    for candidate in dict.fromkeys(item for item in candidates if item):
        try:
            create_report(
                request.tdr_report_name,
                "Standard",
                "Rectangular Plot",
                candidate,
                [
                    "NAME:Context",
                    "Domain:=",
                    "Time",
                    "HoldTime:=",
                    1,
                    "RiseTime:=",
                    1.49253731343284e-11,
                    "StepTime:=",
                    2.98507462686567e-12,
                    "Step:=",
                    True,
                    "WindowWidth:=",
                    1,
                    "WindowType:=",
                    4,
                    "KaiserParameter:=",
                    1,
                    "MaximumTime:=",
                    2.98507462686567e-10,
                ],
                ["Time:=", ["All"]],
                [
                    "X Component:=",
                    "Time",
                    "Y Component:=",
                    [request.tdr_expression],
                ],
            )
            return True
        except Exception as exc:  # pragma: no cover - exercised by AEDT.
            last_error = exc
    if last_error is not None:
        return False
    return True


def _delete_existing_report(module: Any, report_name: str) -> None:
    delete_reports = getattr(module, "DeleteReports", None)
    if not callable(delete_reports):
        return
    try:
        delete_reports([report_name])
    except Exception:
        return


def _export_tdr_report(
    app: Any,
    report_name: str,
    output_dir: Path,
) -> Any:
    native_output = _export_native_report(app, report_name, output_dir)
    if native_output:
        return native_output
    return app.post.export_report_to_file(
        str(output_dir),
        report_name,
        ".csv",
    )


def _export_native_report(
    app: Any,
    report_name: str,
    output_dir: Path,
) -> str:
    design = getattr(app, "odesign", None)
    get_module = getattr(design, "GetModule", None)
    if not callable(get_module):
        return ""
    module = get_module("ReportSetup")
    export_to_file = getattr(module, "ExportToFile", None)
    if not callable(export_to_file):
        return ""
    output_path = output_dir / f"{report_name}.csv"
    try:
        result = export_to_file(report_name, str(output_path), False)
    except TypeError:
        result = export_to_file(report_name, str(output_path))
    return str(output_path) if output_path.is_file() or result is None else str(result)


def _collect_names(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        names = {str(key) for key in value if str(key).strip()}
        for item in value.values():
            names.update(_collect_names(item))
        return names
    if isinstance(value, (list, tuple, set)):
        names: set[str] = set()
        for item in value:
            names.update(_collect_names(item))
        return names
    return {str(value)} if str(value).strip() else set()


def _copy_results_directory(
    source_project: Path,
    target_project: Path,
) -> None:
    source_results = Path(f"{source_project}results")
    if source_results.is_dir():
        shutil.copytree(
            source_results,
            Path(f"{target_project}results"),
            copy_function=shutil.copy2,
        )


def _copy_sidecar_edb(
    source_project: Path,
    target_project: Path,
) -> Path | None:
    source_edb = source_project.with_suffix(".aedb")
    if not source_edb.is_dir():
        return None
    target_edb = target_project.with_suffix(".aedb")
    if target_edb.exists():
        shutil.rmtree(target_edb)
    shutil.copytree(source_edb, target_edb, copy_function=shutil.copy2)
    return target_edb


def _validated_touchstone(
    path: Path,
) -> list[dict[str, float]]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ArtifactExportError(
            f"Touchstone artifact is missing or empty: {path}"
        )
    try:
        samples = parse_touchstone(path)
    except (OSError, TypeError, ValueError) as exc:
        raise ArtifactValidationError(
            f"Touchstone artifact is invalid: {path}"
        ) from exc
    if not samples:
        raise ArtifactValidationError(
            f"Touchstone artifact contains no samples: {path}"
        )
    return samples


def _validated_export_path(
    value: Any,
    output_dir: Path,
) -> Path:
    if not value:
        raise ArtifactExportError(
            "AEDT TDR report export failed"
        )
    path = Path(str(value))
    if not path.is_absolute():
        path = output_dir / path
    resolved = path.resolve()
    if not resolved.is_relative_to(output_dir.resolve()):
        raise ArtifactExportError(
            "AEDT TDR report escaped the controlled output directory"
        )
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ArtifactExportError(
            f"AEDT TDR report is missing or empty: {resolved}"
        )
    return resolved


def _normalize_tdr_report_csv(
    exported_path: Path,
    normalized_path: Path,
    expression: str,
) -> None:
    with exported_path.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as source:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        time_column = next(
            (
                name
                for name in fieldnames
                if _is_time_column(name)
            ),
            None,
        )
        value_column = next(
            (
                name
                for name in fieldnames
                if name == expression
                or "tdrzt" in name.casefold()
                or "tdrz" in name.casefold()
                or "impedance" in name.casefold()
            ),
            None,
        )
        if time_column is None or value_column is None:
            raise ArtifactValidationError(
                "AEDT TDR report does not contain time and "
                "impedance columns"
            )
        try:
            rows = [
                {
                    "time_ps": _time_value_to_ps(
                        float(row[time_column]),
                        time_column,
                    ),
                    "impedance_ohm": float(row[value_column]),
                }
                for row in reader
                if row.get(time_column) not in {None, ""}
                and row.get(value_column) not in {None, ""}
            ]
        except (TypeError, ValueError) as exc:
            raise ArtifactValidationError(
                "AEDT TDR report contains non-numeric samples"
            ) from exc
    if not rows:
        raise ArtifactValidationError(
            "AEDT TDR report contains no samples"
        )
    with normalized_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as target:
        writer = csv.DictWriter(
            target,
            fieldnames=["time_ps", "impedance_ohm"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _is_time_column(name: str) -> bool:
    normalized = name.strip().casefold()
    return normalized == "time" or normalized == "time_ps" or normalized.startswith("time [")


def _time_value_to_ps(value: float, column_name: str) -> float:
    normalized = column_name.strip().casefold()
    if "[s]" in normalized:
        return value * 1e12
    if "[ns]" in normalized:
        return value * 1e3
    if "[us]" in normalized:
        return value * 1e6
    if "[ms]" in normalized:
        return value * 1e9
    return value


def _time_unit_to_ps_factor(unit: str) -> float:
    normalized = unit.strip().casefold()
    if normalized in {"s", "sec", "second", "seconds"}:
        return 1e12
    if normalized in {"ms", "millisecond", "milliseconds"}:
        return 1e9
    if normalized in {"us", "µs", "microsecond", "microseconds"}:
        return 1e6
    if normalized in {"ns", "nanosecond", "nanoseconds"}:
        return 1e3
    if normalized in {"ps", "picosecond", "picoseconds"}:
        return 1.0
    return 1.0


def _validated_tdr(path: Path) -> list[dict[str, float]]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ArtifactExportError(
            f"TDR artifact is missing or empty: {path}"
        )
    try:
        samples = parse_tdr_csv(path)
    except (OSError, TypeError, ValueError) as exc:
        raise ArtifactValidationError(
            f"TDR artifact is invalid: {path}"
        ) from exc
    if not samples:
        raise ArtifactValidationError(
            f"TDR artifact contains no samples: {path}"
        )
    return samples


def _artifact_record(path: Path) -> dict[str, Any]:
    if path.is_dir():
        digest, size, count = _hash_directory(path)
        return {
            "path": str(path),
            "sha256": digest,
            "size_bytes": size,
            "file_count": count,
        }
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _hash_directory(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total_size = 0
    file_count = 0
    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        data = file_path.read_bytes()
        digest.update(data)
        total_size += len(data)
        file_count += 1
    return digest.hexdigest(), total_size, file_count


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
