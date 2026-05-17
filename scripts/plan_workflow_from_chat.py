from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.chat.workflow_planner import ChatPlannerInput, ChatWorkflowPlanner
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.templates import WorkflowTemplateCatalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan a controlled AEDT workflow from a natural-language request.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--catalog-dir", default="nodes/catalog")
    parser.add_argument("--templates-dir", default="workflow_templates")
    parser.add_argument("--output")
    args = parser.parse_args()

    planner_input = ChatPlannerInput(
        user_request=args.request,
        node_catalog=NodeCatalog.from_directory(REPO_ROOT / args.catalog_dir),
        workflow_templates=WorkflowTemplateCatalog.from_directory(REPO_ROOT / args.templates_dir),
    )
    result = ChatWorkflowPlanner().plan(planner_input)
    payload = result.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
