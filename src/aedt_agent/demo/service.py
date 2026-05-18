from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
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


@dataclass
class DemoRunJob:
    job_id: str
    template_id: str
    adapter: str
    run_dir: Path
    status: str = "queued"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    returncode: int | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        artifacts = {
            "workflow_run": str(self.run_dir / "workflow_run.json"),
            "validation": str(self.run_dir / "validation.json"),
            "audit": str(self.run_dir / "audit.jsonl"),
            "report": str(self.run_dir / "report.html"),
            "summary": str(self.run_dir / "smoke_summary.json"),
            "stdout": str(self.run_dir / "stdout.log"),
            "stderr": str(self.run_dir / "stderr.log"),
        }
        data: dict[str, Any] = {
            "job_id": self.job_id,
            "template_id": self.template_id,
            "adapter": self.adapter,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": (self.finished_at or time.time()) - self.started_at,
            "returncode": self.returncode,
            "error": self.error,
            "run_dir": str(self.run_dir),
            "artifacts": artifacts,
        }
        data.update(_read_real_run_artifacts(self.run_dir))
        return data


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
        self.real_run_root = self.run_dir.parent if run_dir is not None else self.repo_root / "benchmarks/runs"
        self.catalog_dir = catalog_dir or self.repo_root / "nodes/catalog"
        self.templates_dir = templates_dir or self.repo_root / "workflow_templates"
        self.default_adapter = default_adapter
        self.planner_config = planner_config or PlannerConfig()
        self.llm_client = llm_client
        self._jobs: dict[str, DemoRunJob] = {}
        self._jobs_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "stage": "Stage C.1",
            "default_adapter": self.default_adapter,
            "real_aedt_from_browser": True,
            "capabilities": [
                "nodes",
                "templates",
                "deterministic_planning",
                "llm_workflow_planning",
                "validation_repair_loop",
                "workflow_validation",
                "real_adapter_run",
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
            "steps": [step.to_dict() for step in result.steps],
            "validation": result.validation,
            "model_validation": result.model_validation,
            "artifacts": {
                "workflow_run": str(run_dir / "workflow_run.json"),
                "validation": str(run_dir / "validation.json"),
                "audit": str(run_dir / "audit.jsonl"),
                "report": str(run_dir / "report.html"),
            },
        }

    def start_real_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        template_id = str(payload.get("template_id") or "microstrip_sparameter")
        adapter = str(payload.get("adapter") or "real")
        if adapter not in {"real", "fake"}:
            raise ValueError("adapter must be real or fake")
        parameters = payload.get("parameters", {})
        if not isinstance(parameters, dict):
            raise TypeError("parameters must be a JSON object")
        job_id = uuid.uuid4().hex[:12]
        run_dir = self.real_run_root / f"stage_c_real_demo_{job_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "params.json").write_text(
            json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        job = DemoRunJob(job_id=job_id, template_id=template_id, adapter=adapter, run_dir=run_dir)
        with self._jobs_lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run_real_job, args=(job, run_dir / "params.json"), daemon=True)
        thread.start()
        return job.to_dict()

    def real_run_status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown real run job: {job_id}")
        return job.to_dict()

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

    def _run_real_job(self, job: DemoRunJob, params_path: Path) -> None:
        command = [
            sys.executable,
            str(self.repo_root / "scripts/run_stage_c_real_workflow_smoke.py"),
            "--adapter",
            job.adapter,
            "--template",
            job.template_id,
            "--params",
            str(params_path),
            "--run-dir",
            str(job.run_dir),
        ]
        job.status = "running"
        (job.run_dir / "stdout.log").write_text(
            "Starting AEDT workflow smoke in non-graphical mode.\n"
            f"Command: {' '.join(command)}\n",
            encoding="utf-8",
        )
        try:
            with (job.run_dir / "stdout.log").open("a", encoding="utf-8") as stdout_file, (
                job.run_dir / "stderr.log"
            ).open("w", encoding="utf-8") as stderr_file:
                process = subprocess.Popen(
                    command,
                    cwd=self.repo_root,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                )
                job.returncode = process.wait()
        except Exception as exc:
            job.returncode = -1
            job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = time.time()
        job.status = "succeeded" if job.returncode == 0 else "failed"


def _workflow_from_payload(payload: dict[str, Any]) -> Workflow:
    workflow_data = payload.get("workflow", payload)
    if isinstance(workflow_data, str):
        return Workflow.from_json(workflow_data)
    if not isinstance(workflow_data, dict):
        raise TypeError("workflow payload must be a JSON object")
    return Workflow.from_dict(json.loads(json.dumps(workflow_data)))


def _read_real_run_artifacts(run_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    summary_path = run_dir / "smoke_summary.json"
    if summary_path.exists():
        data["summary"] = _read_json(summary_path)
    workflow_path = run_dir / "workflow_run.json"
    if workflow_path.exists():
        workflow = _read_json(workflow_path)
        data["workflow_id"] = workflow.get("workflow_id", "")
        data["model_validation"] = workflow.get("model_validation", {})
        data["validation"] = workflow.get("validation", {})
        data["steps"] = workflow.get("steps", [])
    data["stdout_tail"] = _tail(run_dir / "stdout.log")
    data["stderr_tail"] = _tail(run_dir / "stderr.log")
    return data


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _tail(path: Path, *, max_lines: int = 60) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])
