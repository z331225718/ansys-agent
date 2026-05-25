from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.import_cutout import build_import_cutout_request, run_fake_import_cutout, run_real_import_cutout


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the BRD/MCM import-cutout model-build demo.")
    parser.add_argument("--adapter", choices=["real", "fake"], default="real")
    parser.add_argument("--params", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--aedt-version", default="2026.1")
    parser.add_argument("--cadence-launcher", default="")
    parser.add_argument("--ansysem-root", default="")
    parser.add_argument("--awp-root", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--non-graphical", dest="non_graphical", action="store_true")
    mode.add_argument("--graphical", dest="non_graphical", action="store_false")
    parser.set_defaults(non_graphical=False)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    parameters = json.loads(Path(args.params).read_text(encoding="utf-8"))
    if not isinstance(parameters, dict):
        raise TypeError("--params must point to a JSON object")
    parameters["artifact_dir"] = str(run_dir)
    request = build_import_cutout_request(parameters)
    if args.adapter == "fake":
        result = run_fake_import_cutout(request)
    else:
        result = run_real_import_cutout(
            request,
            aedt_version=args.aedt_version,
            cadence_launcher=args.cadence_launcher,
            ansysem_root=args.ansysem_root,
            awp_root=args.awp_root,
            non_graphical=args.non_graphical,
        )
    (run_dir / "import_cutout_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
