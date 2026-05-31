from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.import_cutout import build_import_cutout_request, run_fake_import_cutout, run_real_import_cutout
from aedt_agent.layout.optimization_actions import build_recorded_optimization_action_plan
from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings


def main() -> None:
    args = _parse_args()
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    params = _read_json_object(args.params)
    recorded_analysis = _read_json_object(args.recorded_analysis)
    params["artifact_dir"] = str(run_dir)
    params["solve_enabled"] = False
    merge_recorded_layout_settings(params, recorded_analysis)

    action_plan = build_recorded_optimization_action_plan(recorded_analysis, solve_enabled=False)
    (run_dir / "stage_c5_action_plan.json").write_text(
        json.dumps(action_plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    request = build_import_cutout_request(params)
    if args.adapter == "fake":
        build_summary = run_fake_import_cutout(request)
        build_summary.setdefault("layout_solve", {"status": "skipped", "reason": "model_build_only"})
    else:
        build_summary = run_real_import_cutout(
            request,
            aedt_version=args.aedt_version,
            cadence_launcher=args.cadence_launcher,
            ansysem_root=args.ansysem_root,
            awp_root=args.awp_root,
            non_graphical=args.non_graphical,
        )
    build_summary["stage_c5_action_plan"] = str(run_dir / "stage_c5_action_plan.json")
    build_summary["stage_c5_mode"] = "recorded_build_only"
    (run_dir / "stage_c5_build_summary.json").write_text(
        json.dumps(build_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Stage C.5 recorded build: {build_summary.get('status')}")
    print(f"Action plan: {run_dir / 'stage_c5_action_plan.json'}")
    print(f"Build summary: {run_dir / 'stage_c5_build_summary.json'}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage C.5 recorded build-only BRD workflow.")
    parser.add_argument("--adapter", choices=["real", "fake"], default="real")
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--recorded-analysis", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--aedt-version", default="2026.1")
    parser.add_argument("--cadence-launcher", default="")
    parser.add_argument("--ansysem-root", default="")
    parser.add_argument("--awp-root", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--non-graphical", dest="non_graphical", action="store_true")
    mode.add_argument("--graphical", dest="non_graphical", action="store_false")
    parser.set_defaults(non_graphical=False)
    return parser.parse_args()


def _read_json_object(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data


if __name__ == "__main__":
    main()
