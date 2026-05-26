from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.mcp.tools import create_kernel


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a minimal MCP-kernel AEDT smoke through controlled nodes.")
    parser.add_argument("--adapter", choices=["fake", "real"], default="fake")
    parser.add_argument("--project", default=str(REPO_ROOT / "benchmarks/runs/mcp_real_smoke/mcp_real_smoke.aedt"))
    parser.add_argument("--design", default="McpSmoke")
    parser.add_argument("--aedt-version", default="2026.1")
    parser.add_argument("--include-experimental", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--non-graphical", dest="non_graphical", action="store_true")
    mode.add_argument("--graphical", dest="non_graphical", action="store_false")
    parser.set_defaults(non_graphical=True)
    args = parser.parse_args()

    kernel = create_kernel(
        adapter=args.adapter,
        node_catalog_dir=REPO_ROOT / "nodes/catalog",
        version=args.aedt_version,
        non_graphical=args.non_graphical,
        include_experimental=args.include_experimental,
        timeout_seconds=120.0,
    )
    session = kernel.create_session(args.project, args.design)
    try:
        substrate = kernel.execute_node(
            "create_substrate",
            {"origin": [0, 0, 0], "size": [10, 10, 0.8], "material": "FR4_epoxy", "name": "Substrate"},
            session["session_id"],
        )
        setup = kernel.execute_node(
            "create_setup",
            {"frequency": "2.4GHz", "name": "Setup1", "max_passes": 1},
            session["session_id"],
        )
        summary = {
            "adapter": args.adapter,
            "session": session,
            "substrate": asdict(substrate),
            "setup": asdict(setup),
            "available_nodes": kernel.list_available_nodes(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    finally:
        kernel.release_session(session["session_id"])


if __name__ == "__main__":
    main()
