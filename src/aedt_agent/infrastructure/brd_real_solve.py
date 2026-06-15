from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
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
    r"^TDRZt\("
    r"([A-Za-z_][A-Za-z0-9_.:-]*),"
    r"([A-Za-z_][A-Za-z0-9_.:-]*)"
    r"\)$"
)
REPORT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
TOUCHSTONE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.s[1-9][0-9]*p$", re.IGNORECASE)


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

        checkpoint_dir = artifact_dir / "input_checkpoint"
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        project_checkpoint = checkpoint_dir / project_path.name
        shutil.copy2(project_path, project_checkpoint)
        _copy_results_directory(project_path, project_checkpoint)

        solved_project = (
            artifact_dir / f"{project_path.stem}.solved.aedt"
        )
        shutil.copy2(project_checkpoint, solved_project)
        _copy_results_directory(project_checkpoint, solved_project)

        touchstone_path = artifact_dir / request.touchstone_name
        tdr_path = artifact_dir / f"{request.tdr_report_name}.csv"
        raw_tdr_dir = artifact_dir / "_aedt_report_tmp"

        app = self._hfss3dlayout_class()(
            project=str(solved_project),
            version=request.environment.version,
            non_graphical=request.environment.non_graphical,
            new_desktop=True,
            close_on_exit=request.environment.non_graphical,
            remove_lock=False,
        )
        try:
            _validate_project_contract(app, request)
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

            report = app.post.create_report(
                expressions=request.tdr_expression,
                setup_sweep_name=request.solution_name,
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
                    "differential_pairs": False,
                },
            )
            if not report:
                raise ArtifactExportError(
                    "AEDT TDR report creation failed"
                )
            raw_tdr_dir.mkdir(parents=False, exist_ok=False)
            try:
                exported_tdr = app.post.export_report_to_file(
                    str(raw_tdr_dir),
                    request.tdr_report_name,
                    ".csv",
                )
                raw_tdr_path = _validated_export_path(
                    exported_tdr,
                    raw_tdr_dir,
                )
                _normalize_tdr_report_csv(
                    raw_tdr_path,
                    tdr_path,
                    request.tdr_expression,
                )
            finally:
                shutil.rmtree(raw_tdr_dir, ignore_errors=True)
            tdr_samples = _validated_tdr(tdr_path)
        finally:
            app.release_desktop(
                close_projects=request.environment.non_graphical,
                close_desktop=request.environment.non_graphical,
            )

        if _sha256(project_path) != source_digest:
            raise ArtifactValidationError(
                "source AEDT project changed during solve"
            )

        summary = {
            "status": "succeeded",
            "adapter": "real_hfss3dlayout_solve",
            "setup_name": request.setup_name,
            "sweep_name": request.sweep_name,
            "solution_name": request.solution_name,
            "expected_port_count": request.expected_port_count,
            "touchstone_sample_count": len(touchstone_samples),
            "tdr_sample_count": len(tdr_samples),
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
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
                "tdr": _artifact_record(tdr_path),
            },
            "summary": summary,
        }
        _atomic_write_json(manifest_path, manifest)
        return BrdRealSolveResult(
            project_checkpoint=str(project_checkpoint),
            solved_project=str(solved_project),
            touchstone_path=str(touchstone_path),
            tdr_path=str(tdr_path),
            solve_manifest_path=str(manifest_path),
            summary=summary,
        )

    def _hfss3dlayout_class(self) -> Callable[..., Any]:
        if self._hfss3dlayout_factory is not None:
            return self._hfss3dlayout_factory
        from ansys.aedt.core import Hfss3dLayout

        return Hfss3dLayout


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


def _validate_project_contract(
    app: Any,
    request: BrdRealSolveRequest,
) -> None:
    if request.setup_name not in set(app.setup_names):
        raise ValueError(
            f"setup not found: {request.setup_name}"
        )
    if request.solution_name not in set(app.setup_sweeps_names):
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
    missing_ports = [
        port
        for port in expression.groups()
        if port not in ports
    ]
    if missing_ports:
        raise ValueError(
            f"TDR expression port not found: {missing_ports[0]}"
        )


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
                if name.casefold()
                in {"time", "time [ps]", "time_ps"}
            ),
            None,
        )
        value_column = next(
            (
                name
                for name in fieldnames
                if name == expression
                or "tdrzt" in name.casefold()
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
                    "time_ps": float(row[time_column]),
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
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


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
