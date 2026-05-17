from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.nodes.registry import NodeRegistry
from aedt_agent.workflow.executor import WorkflowExecutor
from aedt_agent.workflow.templates import WorkflowTemplateCatalog
from aedt_agent.workflow.validator import WorkflowValidator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a workflow template through the controlled node executor.")
    parser.add_argument("--template", required=True)
    parser.add_argument("--params", help="JSON file containing parameter overrides.")
    parser.add_argument("--templates-dir", default="workflow_templates")
    parser.add_argument("--catalog-dir", default="nodes/catalog")
    parser.add_argument("--run-dir", default="benchmarks/runs/stage_c_demo_workflow")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    parameters = _load_params(args.params)
    template_catalog = WorkflowTemplateCatalog.from_directory(REPO_ROOT / args.templates_dir)
    workflow = template_catalog.get(args.template).instantiate(parameters)

    session_manager = SessionManager(lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    session = session_manager.create_session(f"stage-c-{args.template}", "HFSSDesign1")
    node_executor = NodeExecutor(
        registry=NodeRegistry.from_directory(REPO_ROOT / args.catalog_dir),
        session_manager=session_manager,
        queue=ExecutionQueue(timeout_seconds=30),
        audit_logger=AuditLogger(run_dir / "audit.jsonl"),
    )
    executor = WorkflowExecutor(
        node_executor=node_executor,
        validator=WorkflowValidator(NodeCatalog.from_directory(REPO_ROOT / args.catalog_dir)),
    )
    result = executor.execute(session.ref.session_id, workflow, artifact_path=run_dir / "workflow_run.json")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


def _load_params(path: str | None) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("--params must point to a JSON object")
    return data


if __name__ == "__main__":
    main()
