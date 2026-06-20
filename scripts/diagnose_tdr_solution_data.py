from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ansys.aedt.core import Hfss3dLayout  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe AEDT/PyAEDT TDR report solution-data variants."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--version", default="2026.1")
    parser.add_argument("--setup", default="Setup1")
    parser.add_argument("--sweep", default="Sweep1")
    parser.add_argument("--port", default="Diff1")
    parser.add_argument("--ansysem-root", default=r"C:\Program Files\ANSYS Inc\v261\AnsysEM")
    args = parser.parse_args()

    os.environ.setdefault("ANSYSLMD_LICENSE_FILE", "1055@localhost")
    if args.ansysem_root:
        os.environ.setdefault("ANSYSEM_ROOT261", args.ansysem_root)

    project = Path(args.project)
    output = Path(args.output_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    app = Hfss3dLayout(
        project=str(project),
        version=args.version,
        non_graphical=True,
        new_desktop=True,
        close_on_exit=True,
        remove_lock=False,
    )
    try:
        solution = f"{args.setup} : {args.sweep}"
        tests: list[tuple[str, str, dict[str, Any]]] = []
        for expression in (f"TDRZ({args.port})", f"TDRZt({args.port})"):
            tests.extend(
                [
                    (expression, "minimal", {}),
                    (
                        expression,
                        "setup_sweep_requested",
                        {"setup_sweep_name": solution},
                    ),
                    (expression, "setup_only", {"setup_sweep_name": args.setup}),
                    (
                        expression,
                        "context",
                        {
                            "setup_sweep_name": solution,
                            "variations": {"Time": ["All"]},
                            "context": {
                                "pulse_rise_time": 1.49253731343284e-11,
                                "step_time": 2.98507462686567e-12,
                                "time_windowing": 4,
                                "maximum_time": 2.98507462686567e-10,
                                "use_pulse_in_tdr": True,
                                "differential_pairs": True,
                            },
                        },
                    ),
                ]
            )
        for index, (expression, mode, kwargs) in enumerate(tests, start=1):
            report_name = f"AgentTdrDiag_{index}"
            record: dict[str, Any] = {
                "expression": expression,
                "mode": mode,
                "report_name": report_name,
            }
            try:
                report = app.post.create_report(
                    expressions=expression,
                    domain="Time",
                    primary_sweep_variable="Time",
                    plot_name=report_name,
                    **kwargs,
                )
                record.update(_solution_data_record(report))
            except Exception as exc:
                record["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            finally:
                try:
                    app.post.delete_report(report_name)
                except Exception:
                    pass
            results.append(record)
    finally:
        app.release_desktop(close_projects=True, close_desktop=True)

    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(output)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


def _solution_data_record(report: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "report_type": type(report).__name__,
        "report_bool": bool(report),
        "has_get_solution_data": callable(getattr(report, "get_solution_data", None)),
    }
    get_solution_data = getattr(report, "get_solution_data", None)
    if not callable(get_solution_data):
        return record
    data = get_solution_data()
    record["solution_data_type"] = type(data).__name__ if data is not None else None
    if data is None:
        return record
    raw_times = getattr(data, "primary_sweep_values", None)
    times = list(raw_times) if raw_times is not None else []
    record["time_count"] = len(times)
    record["time_preview"] = times[:5]
    record["units_sweeps"] = getattr(data, "units_sweeps", {}) or {}
    record["expressions"] = getattr(data, "expressions", None)
    data_real = getattr(data, "data_real", None)
    record["has_data_real"] = callable(data_real)
    if callable(data_real):
        raw_values = data_real()
        values = list(raw_values) if raw_values is not None else []
        record["data_count"] = len(values)
        record["data_preview"] = values[:5]
    return record


if __name__ == "__main__":
    main()
