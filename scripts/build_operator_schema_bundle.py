from __future__ import annotations

import argparse
import json
from pathlib import Path

from aedt_agent.operator_api.contracts import OperatorSchemaCatalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Operator Protocol v1 schema manifest.")
    parser.add_argument("--schema-root", type=Path, default=Path("schemas/operator/v1"))
    args = parser.parse_args()

    catalog = OperatorSchemaCatalog.load(args.schema_root)
    manifest_path = catalog.schema_root / "bundle-manifest.json"
    manifest_path.write_text(
        json.dumps(catalog.manifest(), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
