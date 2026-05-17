from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.nodes.catalog import NodeCatalog


def main() -> None:
    parser = argparse.ArgumentParser(description="List productized AEDT node catalog metadata.")
    parser.add_argument("--catalog-dir", default="nodes/catalog")
    parser.add_argument("--json", action="store_true", help="Emit full JSON catalog.")
    args = parser.parse_args()

    catalog = NodeCatalog.from_directory(REPO_ROOT / args.catalog_dir)
    if args.json:
        print(catalog.to_json())
        return
    for node in catalog.list_metadata():
        print(f"{node.node_id}\t{node.category}\t{node.stability.value}\t{node.display_name}")


if __name__ == "__main__":
    main()
