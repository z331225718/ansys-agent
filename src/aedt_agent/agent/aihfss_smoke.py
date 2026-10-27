"""Bounded AEDT operation nodes for the AI-HFSS workbench graphs.

The workbench owns the strict workflow; the first real operation it can drive is
one **single-point HFSS 3D Layout solve**, which proves the whole command path
(`task -> graph node -> AEDT -> artifact -> task snapshot`) without a wideband
sweep on a machine that cannot afford one.

The node is deliberately narrow:

* it always works on a COPY of the source project inside the task directory
  (`<task>/runs/working/...`), never on the reviewed source;
* it creates (or reuses) a single-point sweep at the requested frequency and
  solves exactly that setup once;
* it exports a touchstone into the task's artifact directory and reports the
  point count read back, so the caller can assert data flowed.

Nothing here starts or advances a mission graph; that stays with the task service.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from aedt_agent.agent.graph_executors import (
    GraphNodeExecutionContext,
    GraphNodeExecutionResult,
)
from aedt_agent.agent.mission import NodeRunStatus

HANDLER_SOLVE_POINT = "aihfss.smoke.layout_solve_point"
PROJECT_SUFFIX = ".aedt"
RESULT_FILE = "solve_point_result.json"


def register_aihfss_handlers(registry: Any) -> None:
    """Register the AEDT smoke handlers on a graph executor registry."""
    registry.register(HANDLER_SOLVE_POINT, _solve_layout_point)


def _solve_layout_point(context: GraphNodeExecutionContext) -> dict[str, Any]:
    payload = dict(context.input_payload or {})
    started = time.perf_counter()
    artifact_dir = Path(str(payload.get("artifact_dir") or ".")).expanduser()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    source_value = str(payload.get("source_project_path") or "").strip()
    if not source_value:
        return _failed("source_project_path is required")
    source = Path(source_value).expanduser()
    if not source.is_file():
        return _failed(f"source project not found: {source}")

    working_value = str(payload.get("working_project_path") or "").strip()
    working = (
        Path(working_value).expanduser()
        if working_value
        else Path(str(payload.get("run_root") or artifact_dir)) / "working" / source.name
    )
    design_name = str(payload.get("design_name") or "").strip()
    setup_name = str(payload.get("setup_name") or "Setup1").strip()
    frequency_ghz = float(payload.get("frequency_ghz") or 1.0)
    sweep_name = str(payload.get("smoke_sweep_name") or f"AIHFSS_{frequency_ghz:g}GHz").strip()
    touchstone_name = str(payload.get("touchstone_name") or f"channel_{frequency_ghz:g}ghz.s1p").strip()
    reset = bool(payload.get("reset_working_project"))

    prepared = _prepare_working_copy(source, working, reset=reset)
    desktop = None
    try:
        from ansys.aedt.core import Desktop, Hfss3dLayout  # type: ignore

        desktop = Desktop(new_desktop=False, non_graphical=True)
        desktop.load_project(str(working))
        layout = Hfss3dLayout(
            project=str(working),
            design=design_name or None,
            new_desktop=False,
            non_graphical=True,
        )
        resolved_design = getattr(layout, "design_name", design_name)
        setup = layout.get_setup(setup_name)
        if setup is None:
            return _failed(f"setup {setup_name!r} was not found in {resolved_design!r}")
        existing = [item.name for item in setup.sweeps]
        if sweep_name not in existing:
            created = layout.create_single_point_sweep(
                setup=setup_name,
                unit="GHz",
                freq=frequency_ghz,
                name=sweep_name,
            )
            if not created:
                return _failed(f"could not create single-point sweep {sweep_name!r} at {frequency_ghz} GHz")

        solve_started = time.perf_counter()
        solved = layout.analyze_setup(setup_name)
        solve_seconds = round(time.perf_counter() - solve_started, 1)
        if not solved:
            return _failed(f"analyze_setup({setup_name!r}) did not complete")

        touchstone = artifact_dir / touchstone_name
        exported = layout.export_touchstone(
            setup=setup_name,
            sweep=sweep_name,
            output_file=str(touchstone),
        )
        points = 0
        try:
            data = layout.get_touchstone_data(setup=setup_name, sweep=sweep_name)
            points = len(data) if isinstance(data, list) else 0
        except Exception:  # noqa: BLE001 - the file itself is the artifact
            points = _touchstone_point_count(touchstone)
        frequencies = _touchstone_frequencies(touchstone)

        result_payload = {
            "status": "solved",
            "design": resolved_design,
            "setup": setup_name,
            "sweep": sweep_name,
            "frequency_ghz": frequency_ghz,
            "solve_seconds": solve_seconds,
            "touchstone_path": str(touchstone),
            "touchstone_exists": touchstone.is_file(),
            "touchstone_bytes": touchstone.stat().st_size if touchstone.is_file() else 0,
            "touchstone_points": points,
            "touchstone_frequencies_ghz": frequencies,
            "export_returned": str(exported),
            "working_project_path": str(working),
            "source_project_path": str(source),
            "source_copied": prepared,
            "total_seconds": round(time.perf_counter() - started, 1),
        }
        (artifact_dir / RESULT_FILE).write_text(
            json.dumps(result_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {
            "status": NodeRunStatus.SUCCEEDED.value,
            "outcome": "layout_solve_point",
            "output_payload": result_payload,
            "artifact_refs": [str(touchstone), str(artifact_dir / RESULT_FILE)],
        }
    except Exception as exc:  # noqa: BLE001 - reported as a bounded failure
        return _failed(f"{type(exc).__name__}: {exc}", elapsed=round(time.perf_counter() - started, 1))
    finally:
        if desktop is not None:
            try:
                # Keep other AEDT sessions alive: never close projects or the desktop.
                desktop.release_desktop(close_projects=False, close_on_exit=False)
            except Exception:  # noqa: BLE001
                pass


def _prepare_working_copy(source: Path, working: Path, *, reset: bool) -> bool:
    """Copy the source project (and its .aedb) into the task working directory."""
    if working.is_file() and not reset:
        return False
    working.parent.mkdir(parents=True, exist_ok=True)
    if reset and working.is_file():
        working.unlink()
    shutil.copy2(source, working)
    source_stem = source.with_suffix("")
    working_stem = working.with_suffix("")
    for suffix in (".aedb",):
        source_dir = Path(str(source_stem) + suffix)
        target_dir = Path(str(working_stem) + suffix)
        if not source_dir.is_dir():
            continue
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    for lock in working.parent.glob("*.lock"):
        try:
            lock.unlink()
        except OSError:
            pass
    return True


def _failed(message: str, *, elapsed: float | None = None) -> dict[str, Any]:
    return {
        "status": NodeRunStatus.FAILED.value,
        "outcome": "layout_solve_point_failed",
        "output_payload": {"error": message, "elapsed_seconds": elapsed},
        "artifact_refs": [],
        "error": {"code": "aihfss/solve-point", "message": message},
    }


def _touchstone_point_count(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("!", "#")):
            continue
        count += 1
    return count


def _touchstone_frequencies(path: Path) -> list[float]:
    """Bounded frequency read-out (the first numeric column of each data row)."""
    frequencies: list[float] = []
    if not path.is_file():
        return frequencies
    unit_scale = 1.0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            lowered = stripped.lower()
            if "ghz" in lowered:
                unit_scale = 1.0
            elif "mhz" in lowered:
                unit_scale = 1e-3
            elif "khz" in lowered:
                unit_scale = 1e-6
            elif "hz" in lowered:
                unit_scale = 1e-9
            continue
        if stripped.startswith("!"):
            continue
        parts = stripped.replace(",", " ").split()
        if not parts:
            continue
        try:
            frequencies.append(round(float(parts[0]) * unit_scale, 6))
        except ValueError:
            continue
        if len(frequencies) >= 4096:
            break
    return frequencies
