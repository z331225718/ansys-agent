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

The exported file name follows the design's real port count (`.s4p` for the
reviewed 4-port differential layout, `.s1p` only for a genuine single port), and
the read-back parser honours the Touchstone spec: one frequency point spans
`1 + 2*N*N` numbers, so a 4-port point is nine numbers plus eight more rows.

Nothing here starts or advances a mission graph; that stays with the task service.
"""
from __future__ import annotations

import json
import re
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
MAX_TOUCHSTONE_POINTS = 4096
_TOUCHSTONE_SUFFIX = re.compile(r"\.s([1-9][0-9]*)p$", re.IGNORECASE)
_PORT_DECLARATION = re.compile(r"^\s*!\s*port\[[^\]]*\]\s*=", re.IGNORECASE)
_NUMBER_OF_PORTS = re.compile(r"\[\s*number of ports\s*\]\s*([0-9]+)", re.IGNORECASE)
_FREQUENCY_UNITS = frozenset({"HZ", "KHZ", "MHZ", "GHZ"})


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
    requested_touchstone_name = str(payload.get("touchstone_name") or "").strip()
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

        # The layout decides the export width: a 4-port differential cut writes
        # `.s4p`, and only a genuine single-port design writes `.s1p`. An explicit
        # payload name always wins and is never renamed; the port count is then
        # reported from the file.
        #
        # When the layout cannot name its own width (no usable `port_list`), the
        # file is exported under a provisional name that claims no port count and
        # renamed once the file says how wide it is. Downstream reads the width
        # from the `.sNp` suffix, so leaving a wrong suffix on disk is worse than
        # an honest placeholder.
        design_port_count = _design_port_count(layout)
        if requested_touchstone_name:
            touchstone = artifact_dir / requested_touchstone_name
        elif design_port_count:
            touchstone = artifact_dir / f"channel_{frequency_ghz:g}ghz.s{design_port_count}p"
        else:
            touchstone = artifact_dir / f"channel_{frequency_ghz:g}ghz.touchstone"
        exported = layout.export_touchstone(
            setup=setup_name,
            sweep=sweep_name,
            output_file=str(touchstone),
        )
        # AEDT may append its own `.sNp` extension to a name it does not
        # recognise, so adopt the file it actually wrote rather than reporting a
        # path that does not exist.
        touchstone = _resolve_exported_file(touchstone)
        # `get_touchstone_data` returns one TouchstoneData per variation, so its
        # `len` is a variation count. Ask the object for its own frequency axis.
        data: Any = None
        points = 0
        try:
            data = layout.get_touchstone_data(setup=setup_name, sweep=sweep_name)
            points = _touchstone_points_from_data(data)
        except Exception:  # noqa: BLE001 - the file itself is the artifact
            points = 0
        parsed = _parse_touchstone(touchstone)
        if points <= 0:
            points = parsed["point_count"]
        port_count = int(
            parsed["port_count"]
            or design_port_count
            or _touchstone_nports_from_data(data)
            or 0
        )
        touchstone_kind = f"s{port_count}p" if port_count else "unknown"
        warnings = list(parsed["warnings"])
        if not touchstone.is_file():
            warnings.append(
                f"export reported success but no file was found at {touchstone}; "
                f"the name was not adjusted"
            )
        renamed_from = ""
        if not requested_touchstone_name and touchstone.is_file():
            wanted_suffix = f".s{port_count}p" if port_count else ""
            if wanted_suffix and touchstone.suffix.lower() != wanted_suffix:
                renamed = touchstone.with_name(
                    f"channel_{frequency_ghz:g}ghz{wanted_suffix}"
                )
                try:
                    if renamed.exists():
                        renamed.unlink()  # a stale file from an earlier run
                    touchstone.rename(renamed)
                except OSError as exc:  # noqa: BLE001 - keep the file, say why
                    warnings.append(
                        f"could not rename {touchstone.name} to {renamed.name}: {exc}"
                    )
                else:
                    # The on-disk name before the rename, whichever name AEDT
                    # settled on, so the trail never claims a file that never was.
                    renamed_from = touchstone.name
                    touchstone = renamed
            elif not wanted_suffix:
                warnings.append(
                    f"exported file width is unknown; kept the provisional name "
                    f"{touchstone.name}"
                )
        if requested_touchstone_name and port_count:
            requested_kind = _touchstone_suffix_kind(Path(requested_touchstone_name))
            if requested_kind and requested_kind != touchstone_kind:
                warnings.append(
                    f"touchstone_name suffix {requested_kind} does not match the exported "
                    f"{touchstone_kind} data"
                )

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
            "touchstone_port_count": port_count,
            "touchstone_kind": touchstone_kind,
            "touchstone_renamed_from": renamed_from,
            "touchstone_frequencies_ghz": parsed["frequencies_ghz"],
            "touchstone_data_format": parsed["data_format"],
            "touchstone_frequency_unit": parsed["frequency_unit"],
            "touchstone_parse_warnings": warnings,
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


def _touchstone_suffix_kind(path: Path) -> str:
    match = _TOUCHSTONE_SUFFIX.search(str(path))
    return f"s{int(match.group(1))}p" if match else ""


def _parse_touchstone(path: Path) -> dict[str, Any]:
    """Read a Touchstone file the way the spec defines it.

    One frequency point is `1 + 2*N*N` numbers (N = port count): the frequency,
    then N*N complex S values. A writer may break a point across rows, so the
    numbers are streamed and sliced — never one frequency per line, which would
    read S values as frequencies on a 4-port file.

    Every failure mode is bounded: a missing, truncated, comment-only, or
    port-count-mismatched file yields zero points and a warning instead of an
    exception. Only the frequency axis and counts are returned; the S values
    stay in the artifact.
    """
    parsed: dict[str, Any] = {
        "point_count": 0,
        "port_count": 0,
        "frequencies_ghz": [],
        "data_format": "MA",
        "frequency_unit": "GHZ",
        "warnings": [],
    }
    warnings: list[str] = parsed["warnings"]
    if not path.is_file():
        warnings.append("touchstone file is missing")
        return parsed

    suffix_port_count = 0
    declared = _touchstone_suffix_kind(path)
    if declared:
        suffix_port_count = int(declared[1:-1])
    port_count = suffix_port_count
    frequency_unit = "GHZ"
    data_format = "MA"
    declared_ports = 0
    numbers: list[float] = []
    frequencies: list[float] = []
    truncated = False
    bad_rows = 0

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:  # noqa: BLE001 - bounded read failure
        warnings.append(f"touchstone file is unreadable: {exc}")
        return parsed

    for raw_line in lines:
        line = raw_line.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            # Option line, e.g. `# GHz S MA R 50`. The unit is optional, so only
            # a recognised unit token may set it — otherwise `# S MA R 50` would
            # report "S" as the frequency unit.
            parts = line[1:].split()
            upper = [part.upper() for part in parts]
            if parts and upper[0] in _FREQUENCY_UNITS:
                frequency_unit = upper[0]
            if "RI" in upper:
                data_format = "RI"
            elif "DB" in upper:
                data_format = "DB"
            elif "MA" in upper:
                data_format = "MA"
            continue
        if line.startswith("["):
            match = _NUMBER_OF_PORTS.match(line)
            if match:
                declared_ports = int(match.group(1))
            continue
        for item in line.replace(",", " ").split():
            try:
                numbers.append(float(item))
            except ValueError:
                bad_rows += 1

    # The `! Port[n] = ...` comment lines are stripped above, so count them here.
    comment_ports = sum(1 for raw in lines if _PORT_DECLARATION.match(raw))
    if not port_count:
        port_count = declared_ports or comment_ports
    else:
        # The suffix names the data width; the file's own declarations are the
        # evidence. A disagreement means the two cannot both describe one
        # network, so report it and read nothing rather than guess a stride.
        mismatched = next(
            (
                (source, value)
                for source, value in (
                    ("[Number of Ports]", declared_ports),
                    ("! Port[n] declarations", comment_ports),
                )
                if value and value != port_count
            ),
            None,
        )
        if mismatched is not None:
            source, value = mismatched
            warnings.append(
                f"{source} report {value} ports but the {declared} suffix says "
                f"{port_count}; refusing to parse a file whose width is ambiguous"
            )
            parsed.update(
                {
                    "point_count": 0,
                    "port_count": value,
                    "frequencies_ghz": [],
                    "data_format": data_format,
                    "frequency_unit": frequency_unit,
                }
            )
            return parsed

    if not numbers:
        warnings.append("touchstone file has no data rows")
        parsed.update(
            {
                "point_count": 0,
                "port_count": port_count,
                "frequencies_ghz": [],
                "data_format": data_format,
                "frequency_unit": frequency_unit,
            }
        )
        return parsed

    if not port_count:
        warnings.append("port count is unknown: no .sNp suffix and no port declarations")
        parsed.update(
            {
                "point_count": 0,
                "port_count": 0,
                "frequencies_ghz": [],
                "data_format": data_format,
                "frequency_unit": frequency_unit,
            }
        )
        return parsed

    expected = 1 + 2 * port_count * port_count
    point_count = len(numbers) // expected
    if len(numbers) % expected:
        truncated = True
    for index in range(point_count):
        start = index * expected
        try:
            frequency = float(numbers[start])
        except (TypeError, ValueError):
            truncated = True
            break
        frequencies.append(round(frequency * _frequency_unit_scale(frequency_unit), 6))
        if len(frequencies) >= MAX_TOUCHSTONE_POINTS:
            break
    if truncated:
        warnings.append(
            f"touchstone data is truncated: {len(numbers)} numbers is not a whole "
            f"number of {expected}-number points for {port_count} ports"
        )
    if bad_rows:
        warnings.append(f"{bad_rows} touchstone rows held non-numeric values and were skipped")

    parsed.update(
        {
            "point_count": len(frequencies),
            "port_count": port_count,
            "frequencies_ghz": frequencies,
            "data_format": data_format,
            "frequency_unit": frequency_unit,
        }
    )
    return parsed


def _frequency_unit_scale(unit: str) -> float:
    lowered = unit.strip().lower()
    if lowered == "mhz":
        return 1e-3
    if lowered == "khz":
        return 1e-6
    if lowered == "hz":
        return 1e-9
    return 1.0


def _touchstone_point_count(path: Path) -> int:
    """Frequency points in a Touchstone file (0 when it cannot be read)."""
    return int(_parse_touchstone(path)["point_count"])


def _touchstone_frequencies(path: Path) -> list[float]:
    """Frequency axis in GHz (empty when the file cannot be read)."""
    return list(_parse_touchstone(path)["frequencies_ghz"])


def _resolve_exported_file(path: Path) -> Path:
    """The file AEDT actually wrote for `path`.

    AEDT honours a `.sNp` name as given, but may append its own extension when
    the name carries none (our provisional name). Prefer the exact path, then a
    single `.sNp` sibling, and otherwise keep the requested path so the caller
    reports a missing artifact rather than inventing one.
    """
    if path.is_file():
        return path
    siblings = sorted(path.parent.glob(f"{path.stem}.s*p"))
    return siblings[0] if len(siblings) == 1 else path


def _design_port_count(layout: Any) -> int:
    """Port count of the solved design, or 0 when AEDT cannot report it."""
    try:
        ports = layout.port_list
    except Exception:  # noqa: BLE001 - the exported file is the fallback
        return 0
    try:
        return len(ports)
    except TypeError:
        return 0


def _touchstone_nports_from_data(data: Any) -> int:
    """Port count on a `get_touchstone_data` result, or 0 when it cannot say.

    Used only when the exported file does not declare its own width, so that the
    file can still be named after the network it holds.
    """
    if data is None:
        return 0
    entries = data if isinstance(data, (list, tuple)) else [data]
    for entry in entries:
        try:
            return int(entry.nports)
        except Exception:  # noqa: BLE001 - unknown object shape
            continue
    return 0


def _touchstone_points_from_data(data: Any) -> int:
    """Frequency points on a `get_touchstone_data` result.

    pyaedt returns `list[TouchstoneData]` — one entry per variation — so the
    list length is a variation count. Each TouchstoneData is a scikit-rf
    Network, whose `f` array is the frequency axis; that length is the point
    count. Anything unexpected yields 0 so the caller falls back to the file.
    """
    if data is None:
        return 0
    entries = data if isinstance(data, (list, tuple)) else [data]
    points = 0
    for entry in entries:
        try:
            points = max(points, len(entry.f))
        except Exception:  # noqa: BLE001 - unknown object shape
            continue
    return points
