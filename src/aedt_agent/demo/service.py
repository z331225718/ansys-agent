from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.demo.config import PlannerConfig
from aedt_agent.demo.planner import PlannerRunner, WorkflowProposalClient
from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.nodes.registry import NodeRegistry
from aedt_agent.workflow.executor import WorkflowExecutor
from aedt_agent.workflow.models import Workflow
from aedt_agent.workflow.templates import WorkflowTemplateCatalog
from aedt_agent.workflow.validator import WorkflowValidator


class DemoService:
    def __init__(
        self,
        repo_root: Path,
        *,
        run_dir: Path | None = None,
        catalog_dir: Path | None = None,
        templates_dir: Path | None = None,
        default_adapter: str = "fake",
        planner_config: PlannerConfig | None = None,
        llm_client: WorkflowProposalClient | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.run_dir = run_dir or self.repo_root / "benchmarks/runs/stage_c1_demo_latest"
        self.catalog_dir = catalog_dir or self.repo_root / "nodes/catalog"
        self.templates_dir = templates_dir or self.repo_root / "workflow_templates"
        self.default_adapter = default_adapter
        self.planner_config = planner_config or PlannerConfig()
        self.llm_client = llm_client

    def status(self) -> dict[str, Any]:
        return {
            "stage": "Stage C.1",
            "default_adapter": self.default_adapter,
            "real_aedt_from_browser": False,
            "capabilities": [
                "nodes",
                "templates",
                "deterministic_planning",
                "llm_workflow_planning",
                "validation_repair_loop",
                "workflow_validation",
                "fake_adapter_run",
                "report_links",
            ],
            "reports": self.reports()["reports"],
        }

    def nodes(self) -> dict[str, Any]:
        return self._node_catalog().to_dict()

    def templates(self) -> dict[str, Any]:
        return self._template_catalog().to_ui_dict()

    def template(self, template_id: str) -> dict[str, Any]:
        return self._template_catalog().get(template_id).to_dict()

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        runner = PlannerRunner(
            config=self.planner_config,
            node_catalog=self._node_catalog(),
            workflow_templates=self._template_catalog(),
            llm_client=self.llm_client,
        )
        result = runner.plan(
            str(payload.get("user_request", "")),
            requested_mode=payload.get("planner_mode") if isinstance(payload.get("planner_mode"), str) else None,
            retrieved_context=[str(item) for item in payload.get("retrieved_context", []) if isinstance(item, str)],
        )
        return result.to_dict()

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        workflow = _workflow_from_payload(payload)
        return WorkflowValidator(self._node_catalog()).validate(workflow).to_dict()

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        workflow = self._workflow_for_run(payload)
        run_dir = self.run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        session_manager = SessionManager(lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
        session = session_manager.create_session(f"stage-c1-{workflow.workflow_id}", "HFSSDesign1")
        try:
            executor = WorkflowExecutor(
                node_executor=NodeExecutor(
                    registry=NodeRegistry.from_directory(self.catalog_dir),
                    session_manager=session_manager,
                    queue=ExecutionQueue(timeout_seconds=30),
                    audit_logger=AuditLogger(run_dir / "audit.jsonl"),
                ),
                validator=WorkflowValidator(self._node_catalog()),
            )
            result = executor.execute(session.ref.session_id, workflow, artifact_path=run_dir / "workflow_run.json")
        finally:
            session_manager.release_session(session.ref.session_id)
        return {
            "workflow_id": result.workflow_id,
            "status": result.status,
            "succeeded": result.succeeded,
            "step_count": len(result.steps),
            "validation": result.validation,
            "model_validation": result.model_validation,
            "artifacts": {
                "workflow_run": str(run_dir / "workflow_run.json"),
                "validation": str(run_dir / "validation.json"),
                "audit": str(run_dir / "audit.jsonl"),
                "report": str(run_dir / "report.html"),
            },
        }

    def reports(self) -> dict[str, Any]:
        return {
            "reports": {
                "stage_c_report": "benchmarks/reports/aedt_agent_stage_c_progress_report.html",
                "real_smoke_dashboard": "benchmarks/reports/stage_c_real_smoke_dashboard.html",
                "demo_index": "benchmarks/reports/stage_c_demo_index.html",
                "node_evolution_review": "benchmarks/reports/stage_c_node_evolution_review.html",
                "planner_benchmark": "benchmarks/reports/stage_c2_planner_benchmark.html",
            }
        }

    def _node_catalog(self) -> NodeCatalog:
        return NodeCatalog.from_directory(self.catalog_dir)

    def _template_catalog(self) -> WorkflowTemplateCatalog:
        return WorkflowTemplateCatalog.from_directory(self.templates_dir)

    def _workflow_for_run(self, payload: dict[str, Any]) -> Workflow:
        template_id = payload.get("template_id")
        if isinstance(template_id, str) and template_id:
            parameters = payload.get("parameters", {})
            if not isinstance(parameters, dict):
                raise TypeError("parameters must be a JSON object")
            return self._template_catalog().get(template_id).instantiate(parameters)
        return _workflow_from_payload(payload)


def _workflow_from_payload(payload: dict[str, Any]) -> Workflow:
    workflow_data = payload.get("workflow", payload)
    if isinstance(workflow_data, str):
        return Workflow.from_json(workflow_data)
    if not isinstance(workflow_data, dict):
        raise TypeError("workflow payload must be a JSON object")
    return Workflow.from_dict(json.loads(json.dumps(workflow_data)))
