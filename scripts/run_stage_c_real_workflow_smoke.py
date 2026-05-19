from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.benchmark.config import load_benchmark_config
from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.nodes.registry import NodeRegistry
from aedt_agent.workflow.executor import WorkflowExecutor
from aedt_agent.workflow.models import Workflow, WorkflowParameter
from aedt_agent.workflow.templates import WorkflowTemplateCatalog
from aedt_agent.workflow.validator import WorkflowValidator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Stage C workflow smoke through the controlled executor.")
    parser.add_argument("--template", default="microstrip_sparameter")
    parser.add_argument("--workflow", help="JSON file containing a generated workflow to execute.")
    parser.add_argument("--params", help="JSON file containing parameter overrides.")
    parser.add_argument("--templates-dir", default="workflow_templates")
    parser.add_argument("--catalog-dir", default="nodes/catalog")
    parser.add_argument("--run-dir", default="benchmarks/runs/stage_c_real_microstrip_smoke")
    parser.add_argument("--adapter", choices=["real", "fake"], default="real")
    parser.add_argument("--config", default="config/benchmark_config.json")
    parser.add_argument("--aedt-version")
    parser.add_argument("--ansysem-root")
    parser.add_argument("--awp-root")
    parser.add_argument("--timeout-seconds", type=float)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--non-graphical", dest="non_graphical", action="store_true", default=True)
    mode.add_argument("--graphical", dest="non_graphical", action="store_false")
    args = parser.parse_args()
    benchmark_config = load_benchmark_config(REPO_ROOT / args.config)
    aedt_config = benchmark_config.aedt
    args.aedt_version = args.aedt_version or aedt_config.version
    args.ansysem_root = args.ansysem_root if args.ansysem_root is not None else aedt_config.ansysem_root
    args.awp_root = args.awp_root if args.awp_root is not None else aedt_config.awp_root
    args.timeout_seconds = float(args.timeout_seconds if args.timeout_seconds is not None else aedt_config.timeout)

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    parameters = _load_params(args.params)
    parameters.setdefault("artifact_dir", str(run_dir.resolve()))
    if args.workflow:
        workflow = _workflow_with_artifact_dir(Workflow.from_file(Path(args.workflow)), str(run_dir.resolve()))
    else:
        workflow = WorkflowTemplateCatalog.from_directory(REPO_ROOT / args.templates_dir).get(args.template).instantiate(parameters)
    session_manager = SessionManager(_adapter_factory(args))
    session = session_manager.create_session(f"stage-c-{args.template}-smoke", "HFSSDesign1")
    try:
        executor = WorkflowExecutor(
            node_executor=NodeExecutor(
                registry=NodeRegistry.from_directory(REPO_ROOT / args.catalog_dir),
                session_manager=session_manager,
                queue=ExecutionQueue(timeout_seconds=args.timeout_seconds),
                audit_logger=AuditLogger(run_dir / "audit.jsonl"),
            ),
            validator=WorkflowValidator(NodeCatalog.from_directory(REPO_ROOT / args.catalog_dir)),
        )
        result = executor.execute(session.ref.session_id, workflow, artifact_path=run_dir / "workflow_run.json")
    finally:
        session_manager.release_session(session.ref.session_id)

    (run_dir / "smoke_summary.json").write_text(
        json.dumps(
            {
                "adapter": args.adapter,
                "non_graphical": args.non_graphical if args.adapter == "real" else None,
                "aedt": {
                    "version": args.aedt_version,
                    "ansysem_root": args.ansysem_root,
                    "awp_root": args.awp_root,
                    "timeout": args.timeout_seconds,
                },
                "template": args.template,
                "workflow_id": workflow.workflow_id,
                "status": result.status,
                "step_count": len(result.steps),
                "model_validation": result.model_validation,
                "artifacts": ["workflow_run.json", "validation.json", "audit.jsonl", "report.html"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if not result.succeeded:
        raise SystemExit(1)


def _adapter_factory(args: argparse.Namespace):
    if args.adapter == "fake":
        return lambda project_id, design_id: FakeAedtAdapter(project_id, design_id)
    return lambda project_id, design_id: PyaedtAdapter(
        project_id=project_id,
        design_id=design_id,
        version=args.aedt_version,
        non_graphical=args.non_graphical,
        ansysem_root=args.ansysem_root,
        awp_root=args.awp_root,
    )


def _load_params(path: str | None) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("--params must point to a JSON object")
    return data


def _workflow_with_artifact_dir(workflow: Workflow, artifact_dir: str) -> Workflow:
    parameters = []
    found = False
    for parameter in workflow.parameters:
        if parameter.name == "artifact_dir":
            parameters.append(
                WorkflowParameter(
                    name=parameter.name,
                    type=parameter.type,
                    default=artifact_dir,
                    unit=parameter.unit,
                    minimum=parameter.minimum,
                    maximum=parameter.maximum,
                    label=parameter.label,
                    description=parameter.description,
                )
            )
            found = True
        else:
            parameters.append(parameter)
    if not found:
        parameters.append(WorkflowParameter(name="artifact_dir", type="string", default=artifact_dir, label="Artifact directory"))
    return Workflow(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        version=workflow.version,
        description=workflow.description,
        parameters=parameters,
        nodes=workflow.nodes,
        edges=workflow.edges,
        validation=workflow.validation,
        outputs=workflow.outputs,
        metadata=workflow.metadata,
    )


if __name__ == "__main__":
    main()
