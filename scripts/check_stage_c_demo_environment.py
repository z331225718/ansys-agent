from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.config import load_demo_config
from aedt_agent.demo.preflight import run_stage_c_preflight


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether the Stage C demo environment is ready.")
    parser.add_argument("--config", default="config/demo_config.example.json")
    parser.add_argument("--local-config", default="config/demo_config.local.json")
    parser.add_argument("--params", default="")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as a failed preflight.")
    args = parser.parse_args()

    config = load_demo_config(example_path=Path(args.config), local_path=Path(args.local_config))
    parameters = _read_params(Path(args.params)) if args.params else {}
    result = run_stage_c_preflight(config, parameters=parameters, strict=args.strict)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_text_report(result.to_dict())
    raise SystemExit(0 if result.ok else 1)


def _read_params(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("--params must point to a JSON object")
    return data


def _print_text_report(payload: dict[str, object]) -> None:
    status = "OK" if payload["ok"] else "FAILED"
    print(f"Stage C demo preflight: {status}")
    for check in payload["checks"]:
        print(f"[{check['status']}] {check['id']}: {check['message']}")


if __name__ == "__main__":
    main()
