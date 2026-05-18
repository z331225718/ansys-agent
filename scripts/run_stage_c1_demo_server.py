from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.config import load_demo_config
from aedt_agent.demo.web import run_demo_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local Stage C.1 AEDT Agent demo server.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--run-dir")
    parser.add_argument("--config", default="config/demo_config.example.json")
    parser.add_argument("--local-config", default="config/demo_config.local.json")
    args = parser.parse_args()

    config = load_demo_config(example_path=Path(args.config), local_path=Path(args.local_config))
    host = args.host or config.server.host
    port = args.port or config.server.port
    run_dir = Path(args.run_dir or config.execution.run_dir)
    print(f"Stage C.1 demo server: http://{host}:{port}")
    run_demo_server(host, port, REPO_ROOT, run_dir)


if __name__ == "__main__":
    main()
