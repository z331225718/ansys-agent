from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus
from aedt_agent.validation.inspector import inspect_aedt_model
from aedt_agent.validation.report import validation_summary
from aedt_agent.validation.rules import validate_model_facts, validation_repair_context
from aedt_agent.workflow.models import Workflow
from aedt_agent.workflow.validator import WorkflowValidationResult, WorkflowValidator


@dataclass(frozen=True)
class WorkflowStepRun:
    step_id: str
    node_id: str
    inputs: dict[str, Any]
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    snapshot_summary: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    elapsed_seconds: float = 0.0

    @classmethod
    def from_execution_result(
        cls,
        step_id: str,
        node_id: str,
        inputs: dict[str, Any],
        result: ExecutionResult,
        snapshot_summary: dict[str, Any] | None = None,
    ) -> "WorkflowStepRun":
        return cls(
            step_id=step_id,
            node_id=node_id,
            inputs=inputs,
            status=result.status.value,
            output=dict(result.output),
            snapshot_summary=snapshot_summary or {},
            error_type=result.error_type,
            error_message=result.error_message,
            elapsed_seconds=result.elapsed_seconds,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "node_id": self.node_id,
            "inputs": _json_safe(self.inputs),
            "status": self.status,
            "output": _json_safe(self.output),
            "snapshot_summary": _json_safe(self.snapshot_summary),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True)
class WorkflowRunResult:
    workflow_id: str
    status: str
    validation: dict[str, Any]
    model_validation: dict[str, Any] = field(default_factory=dict)
    model_facts: dict[str, Any] = field(default_factory=dict)
    steps: list[WorkflowStepRun] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    repair_context: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == ExecutionStatus.SUCCEEDED.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "status": self.status,
            "validation": _json_safe(self.validation),
            "model_validation": _json_safe(self.model_validation),
            "model_facts": _json_safe(self.model_facts),
            "steps": [step.to_dict() for step in self.steps],
            "outputs": _json_safe(self.outputs),
            "repair_context": _json_safe(self.repair_context),
        }

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class WorkflowExecutor:
    def __init__(self, node_executor: NodeExecutor, validator: WorkflowValidator) -> None:
        self.node_executor = node_executor
        self.validator = validator

    def execute(
        self,
        session_id: str,
        workflow: Workflow,
        parameters: dict[str, Any] | None = None,
        artifact_path: Path | None = None,
        start_at_step_id: str | None = None,
        initial_step_outputs: dict[str, dict[str, Any]] | None = None,
    ) -> WorkflowRunResult:
        validation = self.validator.validate(workflow)
        if not validation.passed:
            run = WorkflowRunResult(
                workflow_id=workflow.workflow_id,
                status=ExecutionStatus.REJECTED.value,
                validation=validation.to_dict(),
                repair_context=_validation_repair_context(validation),
            )
            _write_if_requested(run, artifact_path)
            return run

        context = _ExecutionContext(
            parameters=_parameter_values(workflow, parameters or {}),
            step_outputs={key: dict(value) for key, value in (initial_step_outputs or {}).items()},
        )
        steps: list[WorkflowStepRun] = []
        selected_nodes = _nodes_from_start(workflow, start_at_step_id)
        for node in selected_nodes:
            try:
                inputs = _resolve_refs(_apply_edge_inputs(workflow, node.id, node.inputs), context)
            except Exception as exc:
                step = WorkflowStepRun(
                    step_id=node.id,
                    node_id=node.node_id,
                    inputs=dict(node.inputs),
                    status=ExecutionStatus.REJECTED.value,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                steps.append(step)
                run = WorkflowRunResult(
                    workflow_id=workflow.workflow_id,
                    status=ExecutionStatus.REJECTED.value,
                    validation=validation.to_dict(),
                    steps=steps,
                    repair_context=_step_repair_context(step),
                )
                _write_if_requested(run, artifact_path)
                return run

            result = self.node_executor.execute_node(session_id, node.node_id, inputs)
            snapshot_summary = _safe_session_snapshot(self.node_executor, session_id)
            step = WorkflowStepRun.from_execution_result(node.id, node.node_id, inputs, result, snapshot_summary=snapshot_summary)
            steps.append(step)
            _write_if_requested(
                WorkflowRunResult(
                    workflow_id=workflow.workflow_id,
                    status=ExecutionStatus.RUNNING.value,
                    validation=validation.to_dict(),
                    steps=steps,
                ),
                artifact_path,
            )
            if not result.succeeded:
                run = WorkflowRunResult(
                    workflow_id=workflow.workflow_id,
                    status=result.status.value,
                    validation=validation.to_dict(),
                    steps=steps,
                    repair_context=_step_repair_context(step),
                )
                _write_if_requested(run, artifact_path)
                return run
            context.step_outputs[node.id] = dict(result.output)

        model_facts, model_validation, model_repair_context = _validate_model_state(self.node_executor, session_id, workflow)
        if model_validation and not model_validation.get("passed", False):
            run = WorkflowRunResult(
                workflow_id=workflow.workflow_id,
                status=ExecutionStatus.FAILED.value,
                validation=validation.to_dict(),
                model_validation=model_validation,
                model_facts=model_facts,
                steps=steps,
                repair_context=model_repair_context,
            )
            _write_if_requested(run, artifact_path)
            return run

        outputs = _workflow_outputs(workflow, context)
        run = WorkflowRunResult(
            workflow_id=workflow.workflow_id,
            status=ExecutionStatus.SUCCEEDED.value,
            validation=validation.to_dict(),
            model_validation=model_validation,
            model_facts=model_facts,
            steps=steps,
            outputs=outputs,
        )
        _write_if_requested(run, artifact_path)
        return run


@dataclass
class _ExecutionContext:
    parameters: dict[str, Any]
    step_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)


def _parameter_values(workflow: Workflow, overrides: dict[str, Any]) -> dict[str, Any]:
    values = {parameter.name: parameter.default for parameter in workflow.parameters}
    values.update(overrides)
    return values


def _apply_edge_inputs(workflow: Workflow, node_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_copy(inputs)
    prefix = f"{node_id}.inputs."
    for edge in workflow.edges:
        if edge.target.startswith(prefix):
            target_path = edge.target[len(prefix) :].split(".")
            _set_path_if_missing(merged, target_path, {"$ref": edge.source})
    return merged


def _resolve_refs(value: Any, context: _ExecutionContext) -> Any:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and len(value) == 1:
            return _lookup_ref(ref, context)
        return {key: _resolve_refs(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(item, context) for item in value]
    return value


def _lookup_ref(ref: str, context: _ExecutionContext) -> Any:
    parts = ref.split(".")
    if len(parts) >= 2 and parts[0] == "parameters":
        return _lookup_path(context.parameters, parts[1:])
    if len(parts) >= 3 and parts[1] == "output":
        step_id = parts[0]
        if step_id not in context.step_outputs:
            raise KeyError(f"unknown step output reference: {ref}")
        return _lookup_path(context.step_outputs[step_id], parts[2:])
    raise ValueError(f"unsupported workflow reference: {ref}")


def _lookup_path(value: Any, path: list[str]) -> Any:
    current = value
    for part in path:
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(".".join(path))
    return _deep_copy(current)


def _set_path_if_missing(data: dict[str, Any], path: list[str], value: Any) -> None:
    current: Any = data
    for part in path[:-1]:
        if isinstance(current, list):
            index = int(part)
            while len(current) <= index:
                current.append({})
            if not isinstance(current[index], (dict, list)):
                current[index] = {}
            current = current[index]
            continue
        current = current.setdefault(part, {})
    leaf = path[-1]
    if isinstance(current, list):
        index = int(leaf)
        while len(current) <= index:
            current.append(None)
        if current[index] is None:
            current[index] = value
    else:
        current.setdefault(leaf, value)


def _workflow_outputs(workflow: Workflow, context: _ExecutionContext) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for output in workflow.outputs:
        outputs[output.name] = _lookup_ref(output.source, context)
    return outputs


def _validate_model_state(node_executor: NodeExecutor, session_id: str, workflow: Workflow) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not workflow.validation:
        return {}, {}, {}
    snapshot = node_executor.session_manager.snapshot(session_id)
    facts = inspect_aedt_model(snapshot)
    result = validate_model_facts(facts, workflow.validation)
    data = result.to_dict()
    data["summary"] = validation_summary(result)
    return facts.to_dict(), data, validation_repair_context(result) if not result.passed else {}


def _nodes_from_start(workflow: Workflow, start_at_step_id: str | None) -> list[Any]:
    if start_at_step_id is None:
        return list(workflow.nodes)
    for index, node in enumerate(workflow.nodes):
        if node.id == start_at_step_id:
            return list(workflow.nodes[index:])
    raise KeyError(f"unknown start_at_step_id: {start_at_step_id}")


def _validation_repair_context(validation: WorkflowValidationResult) -> dict[str, Any]:
    return {
        "reason": "workflow_validation_failed",
        "errors": [issue.to_dict() for issue in validation.errors],
        "warnings": [issue.to_dict() for issue in validation.warnings],
    }


def _step_repair_context(step: WorkflowStepRun) -> dict[str, Any]:
    return {
        "reason": "workflow_step_failed",
        "failed_step_id": step.step_id,
        "node_id": step.node_id,
        "status": step.status,
        "error_type": step.error_type,
        "error_message": step.error_message,
        "inputs": _json_safe(step.inputs),
        "output": _json_safe(step.output),
    }


def _write_if_requested(run: WorkflowRunResult, artifact_path: Path | None) -> None:
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        run.write_json(artifact_path)
        (artifact_path.parent / "validation.json").write_text(
            json.dumps(
                {"workflow": run.validation, "model": run.model_validation, "model_facts": run.model_facts},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (artifact_path.parent / "report.html").write_text(_workflow_report_html(run), encoding="utf-8")


def _safe_session_snapshot(node_executor: NodeExecutor, session_id: str) -> dict[str, Any]:
    try:
        snapshot = node_executor.session_manager.snapshot(session_id)
    except Exception as exc:
        return {"snapshot_error": f"{type(exc).__name__}: {exc}"}
    return {
        "object_count": len(snapshot.get("objects", {})),
        "port_count": len(snapshot.get("ports", {})),
        "boundary_count": len(snapshot.get("boundaries", {})),
        "setup_count": len(snapshot.get("setups", {})),
        "sweep_count": len(snapshot.get("sweeps", {})),
    }


def _workflow_report_html(run: WorkflowRunResult) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{_escape(step.step_id)}</td>"
        f"<td>{_escape(step.node_id)}</td>"
        f"<td>{_escape(step.status)}</td>"
        f"<td>{_escape(step.error_message)}</td>"
        "</tr>"
        for step in run.steps
    )
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>Workflow Run Report</title>"
        "<style>body{font-family:Arial,sans-serif;margin:32px;color:#111827}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #d1d5db;padding:8px;text-align:left}"
        ".status{font-weight:700}</style></head><body>"
        f"<h1>{_escape(run.workflow_id)}</h1>"
        f"<p class=\"status\">Status: {_escape(run.status)}</p>"
        f"<p>Model validation: {_escape(run.model_validation.get('summary', 'not requested'))}</p>"
        "<table><thead><tr><th>Step</th><th>Node</th><th>Status</th><th>Error</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</body></html>\n"
    )


def _escape(value: Any) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
