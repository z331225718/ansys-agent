from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.workflow.templates import WorkflowTemplateCatalog


def main() -> None:
    parser = argparse.ArgumentParser(description="List AEDT workflow templates.")
    parser.add_argument("--templates-dir", default="workflow_templates")
    parser.add_argument("--json", action="store_true", help="Emit UI-safe template JSON.")
    args = parser.parse_args()

    catalog = WorkflowTemplateCatalog.from_directory(REPO_ROOT / args.templates_dir)
    if args.json:
        print(json.dumps(catalog.to_ui_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return
    for template in catalog.list_templates():
        print(f"{template.template_id}\t{template.workflow.workflow_id}\t{len(template.workflow.nodes)} nodes\t{template.name}")


if __name__ == "__main__":
    main()
