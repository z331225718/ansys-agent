from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aedt_agent.demo.config import AedtConfig, PlannerConfig
from aedt_agent.demo.planner import PlannerRunner, WorkflowProposalClient
from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.nodes.registry import NodeRegistry
from aedt_agent.workflow.executor import WorkflowExecutor
from aedt_agent.workflow.models import Workflow, WorkflowParameter
from aedt_agent.workflow.templates import WorkflowTemplateCatalog
from aedt_agent.workflow.validator import WorkflowValidator


@dataclass
class DemoRunJob:
    job_id: str
    template_id: str
    adapter: str
    run_dir: Path
    workflow_path: Path | None = None
    graphical: bool = True
    stream_to_terminal: bool = True
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
            "graphical": self.graphical,
            "stream_to_terminal": self.stream_to_terminal,
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
        outputs = data.get("outputs", {})
        if isinstance(outputs, dict) and outputs.get("touchstone"):
            artifacts["touchstone"] = str(outputs["touchstone"])
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
        aedt_config: AedtConfig | None = None,
        llm_client: WorkflowProposalClient | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.run_dir = run_dir or self.repo_root / "benchmarks/runs/stage_c1_demo_latest"
        self.real_run_root = self.run_dir.parent if run_dir is not None else self.repo_root / "benchmarks/runs"
        self.catalog_dir = catalog_dir or self.repo_root / "nodes/catalog"
        self.templates_dir = templates_dir or self.repo_root / "workflow_templates"
        self.default_adapter = default_adapter
        self.planner_config = planner_config or PlannerConfig()
        self.aedt_config = aedt_config or AedtConfig()
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
        artifacts = {
            "workflow_run": str(run_dir / "workflow_run.json"),
            "validation": str(run_dir / "validation.json"),
            "audit": str(run_dir / "audit.jsonl"),
            "report": str(run_dir / "report.html"),
        }
        if result.outputs.get("touchstone"):
            artifacts["touchstone"] = str(result.outputs["touchstone"])
        sparameters = _read_demo_sparameters(result.outputs.get("touchstone"), _target_frequency_hz(payload.get("parameters", {})))
        return {
            "workflow_id": result.workflow_id,
            "status": result.status,
            "succeeded": result.succeeded,
            "step_count": len(result.steps),
            "steps": [step.to_dict() for step in result.steps],
            "validation": result.validation,
            "model_validation": result.model_validation,
            "outputs": result.outputs,
            "artifacts": artifacts,
            "sparameters": sparameters,
        }

    def start_real_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        workflow_payload = payload.get("workflow")
        template_id = str(payload.get("template_id") or "microstrip_sparameter")
        if isinstance(workflow_payload, dict):
            template_id = str(workflow_payload.get("workflow_id") or template_id)
        adapter = str(payload.get("adapter") or "real")
        if adapter not in {"real", "fake"}:
            raise ValueError("adapter must be real or fake")
        graphical = bool(payload.get("graphical", True))
        stream_to_terminal = bool(payload.get("stream_to_terminal", True))
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
        workflow_path = None
        if isinstance(workflow_payload, dict):
            workflow = _workflow_with_artifact_dir(Workflow.from_dict(json.loads(json.dumps(workflow_payload))), str(run_dir.resolve()))
            workflow_path = run_dir / "workflow_input.json"
            workflow_path.write_text(json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        job = DemoRunJob(
            job_id=job_id,
            template_id=template_id,
            adapter=adapter,
            run_dir=run_dir,
            workflow_path=workflow_path,
            graphical=graphical,
            stream_to_terminal=stream_to_terminal,
        )
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
            parameters = dict(parameters)
            parameters.setdefault("artifact_dir", str(self.run_dir))
            return self._template_catalog().get(template_id).instantiate(parameters)
        return _workflow_with_artifact_dir(_workflow_from_payload(payload), str(self.run_dir.resolve()))

    def _run_real_job(self, job: DemoRunJob, params_path: Path) -> None:
        command = [
            sys.executable,
            str(self.repo_root / "scripts/run_stage_c_real_workflow_smoke.py"),
            "--adapter",
            job.adapter,
            "--params",
            str(params_path),
            "--run-dir",
            str(job.run_dir),
        ]
        if job.workflow_path is not None:
            command.extend(["--workflow", str(job.workflow_path), "--template", job.template_id])
        else:
            command.extend(["--template", job.template_id])
        if job.adapter == "real":
            command.append("--graphical" if job.graphical else "--non-graphical")
            command.extend(
                [
                    "--aedt-version",
                    self.aedt_config.version,
                    "--ansysem-root",
                    self.aedt_config.ansysem_root,
                    "--awp-root",
                    self.aedt_config.awp_root,
                    "--timeout-seconds",
                    str(self.aedt_config.timeout),
                ]
            )
        job.status = "running"
        header = (
            f"Starting AEDT workflow smoke in {'graphical' if job.graphical else 'non-graphical'} mode.\n"
            f"Command: {' '.join(command)}\n"
        )
        (job.run_dir / "stdout.log").write_text(header, encoding="utf-8")
        if job.stream_to_terminal:
            print(f"[demo:{job.job_id}] {header.rstrip()}", flush=True)
        try:
            process = subprocess.Popen(
                command,
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            job.returncode = _stream_process_logs(
                process,
                stdout_path=job.run_dir / "stdout.log",
                stderr_path=job.run_dir / "stderr.log",
                terminal_prefix=f"[demo:{job.job_id}]",
                stream_to_terminal=job.stream_to_terminal,
            )
        except Exception as exc:
            job.returncode = -1
            job.error = f"{type(exc).__name__}: {exc}"
            if job.stream_to_terminal:
                print(f"[demo:{job.job_id}] {job.error}", flush=True)
        job.finished_at = time.time()
        job.status = "succeeded" if job.returncode == 0 else "failed"
        if job.stream_to_terminal:
            print(f"[demo:{job.job_id}] finished status={job.status} returncode={job.returncode}", flush=True)


def _workflow_from_payload(payload: dict[str, Any]) -> Workflow:
    workflow_data = payload.get("workflow", payload)
    if isinstance(workflow_data, str):
        return Workflow.from_json(workflow_data)
    if not isinstance(workflow_data, dict):
        raise TypeError("workflow payload must be a JSON object")
    return Workflow.from_dict(json.loads(json.dumps(workflow_data)))


def _workflow_with_artifact_dir(workflow: Workflow, artifact_dir: str) -> Workflow:
    parameters: list[WorkflowParameter] = []
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
        data["outputs"] = workflow.get("outputs", {})
    params = _read_json(run_dir / "params.json")
    outputs = data.get("outputs", {})
    if isinstance(outputs, dict) and outputs.get("touchstone"):
        data["sparameters"] = _read_demo_sparameters(outputs.get("touchstone"), _target_frequency_hz(params))
    data["stdout_tail"] = _tail(run_dir / "stdout.log")
    data["stderr_tail"] = _tail(run_dir / "stderr.log")
    return data


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _tail(path: Path, *, max_lines: int = 60) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def _read_demo_sparameters(touchstone_path: Any, target_frequency_hz: float | None = None) -> dict[str, Any]:
    if not isinstance(touchstone_path, str) or not touchstone_path:
        return {}
    path = Path(touchstone_path)
    if not path.exists():
        return {}
    option = {"unit": "GHz", "format": "MA"}
    samples: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("!"):
            continue
        if line.startswith("#"):
            parts = line[1:].split()
            if parts:
                option["unit"] = parts[0]
            if len(parts) >= 3:
                option["format"] = parts[2]
            continue
        values = _floats_from_touchstone_line(line)
        if len(values) < 9:
            continue
        sample = _touchstone_2port_sample(values, option)
        if sample:
            samples.append(sample)
    if not samples:
        return {}
    selected = _nearest_sample(samples, target_frequency_hz)
    return {
        "source": str(path),
        "point_count": len(samples),
        "frequency_unit": option["unit"],
        "data_format": option["format"],
        "selected": selected,
        "samples": samples,
    }


def _floats_from_touchstone_line(line: str) -> list[float]:
    values = []
    for token in line.split("!")[0].split():
        try:
            values.append(float(token))
        except ValueError:
            return values
    return values


def _touchstone_2port_sample(values: list[float], option: dict[str, str]) -> dict[str, Any]:
    unit = option.get("unit", "GHz")
    data_format = option.get("format", "MA").upper()
    frequency = values[0]
    frequency_hz = _frequency_to_hz(frequency, unit)
    s11 = _pair_to_magnitude(values[1], values[2], data_format)
    s21 = _pair_to_magnitude(values[3], values[4], data_format)
    return {
        "frequency": frequency,
        "frequency_hz": frequency_hz,
        "s11_mag": s11,
        "s21_mag": s21,
        "s11_db": _magnitude_to_db(s11),
        "s21_db": _magnitude_to_db(s21),
    }


def _pair_to_magnitude(first: float, second: float, data_format: str) -> float:
    if data_format == "RI":
        return (first**2 + second**2) ** 0.5
    if data_format == "DB":
        return 10 ** (first / 20)
    return abs(first)


def _magnitude_to_db(value: float) -> float | None:
    if value <= 0:
        return None
    import math

    return 20 * math.log10(value)


def _nearest_sample(samples: list[dict[str, Any]], target_frequency_hz: float | None) -> dict[str, Any]:
    if target_frequency_hz is None:
        return samples[0]
    return min(samples, key=lambda item: abs(float(item["frequency_hz"]) - target_frequency_hz))


def _target_frequency_hz(parameters: Any) -> float | None:
    if not isinstance(parameters, dict):
        return None
    return _parse_frequency_hz(parameters.get("frequency"))


def _parse_frequency_hz(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    import re

    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([GMK]?Hz)\s*", value, re.IGNORECASE)
    if not match:
        return None
    return _frequency_to_hz(float(match.group(1)), match.group(2))


def _frequency_to_hz(value: float, unit: str) -> float:
    scale = {
        "hz": 1,
        "khz": 1e3,
        "mhz": 1e6,
        "ghz": 1e9,
    }
    return value * scale.get(unit.lower(), 1e9)


def _stream_process_logs(
    process: subprocess.Popen[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
    terminal_prefix: str,
    stream_to_terminal: bool = True,
) -> int:
    log_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    threads = [
        threading.Thread(target=_enqueue_pipe_lines, args=(process.stdout, "stdout", log_queue), daemon=True),
        threading.Thread(target=_enqueue_pipe_lines, args=(process.stderr, "stderr", log_queue), daemon=True),
    ]
    for thread in threads:
        thread.start()
    open_mode = "a"
    with stdout_path.open(open_mode, encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        active = len(threads)
        while active:
            stream_name, line = log_queue.get()
            if line is None:
                active -= 1
                continue
            target = stderr_file if stream_name == "stderr" else stdout_file
            target.write(line)
            target.flush()
            if stream_to_terminal:
                sys.stderr.write(f"{terminal_prefix} {stream_name}: {line}") if stream_name == "stderr" else sys.stdout.write(f"{terminal_prefix} {line}")
                sys.stderr.flush() if stream_name == "stderr" else sys.stdout.flush()
    return process.wait()


def _enqueue_pipe_lines(pipe: Any, stream_name: str, log_queue: queue.Queue[tuple[str, str | None]]) -> None:
    try:
        if pipe is not None:
            for line in pipe:
                log_queue.put((stream_name, line))
    finally:
        log_queue.put((stream_name, None))
