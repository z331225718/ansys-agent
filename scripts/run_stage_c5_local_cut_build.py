from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.import_cutout import build_import_cutout_request, run_fake_import_cutout, run_real_import_cutout
from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region
from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings


def main() -> None:
    args = _parse_args()
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    params = _read_json_object(args.params)
    recorded_analysis = _read_json_object(args.recorded_analysis)
    region = parse_local_cut_region(params.get("local_cut_region"))
    params["local_cut_region"] = region
    params["artifact_dir"] = str(run_dir)
    params["solve_enabled"] = False
    merge_recorded_layout_settings(params, recorded_analysis)
    request = build_import_cutout_request(params)
    if args.adapter == "fake":
        summary = run_fake_import_cutout(request)
        summary.setdefault("layout_solve", {"status": "skipped", "reason": "model_build_only"})
    else:
        summary = run_real_import_cutout(
            request,
            aedt_version=args.aedt_version,
            cadence_launcher=args.cadence_launcher,
            ansysem_root=args.ansysem_root,
            awp_root=args.awp_root,
            non_graphical=args.non_graphical,
        )
    summary["stage_c5_mode"] = "local_cut_build_only"
    summary["local_cut_region"] = region
    summary["local_cut_polygon"] = bbox_to_polygon(region)
    (run_dir / "stage_c5_local_cut_params.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "stage_c5_local_cut_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Stage C.5 local cut build: {summary.get('status')}")
    print(f"Summary: {run_dir / 'stage_c5_local_cut_summary.json'}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage C.5 local cut build-only workflow.")
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
