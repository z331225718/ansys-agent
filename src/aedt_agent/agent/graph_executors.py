from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_template import GraphNode, GraphTemplate
from aedt_agent.agent.handoff import HandoffValidationError, validate_handoff
from aedt_agent.agent.mission import (
    ApprovalDecision,
    EvidencePackage,
    GraphRunRecord,
    JobStatus,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.scorecard import score_mission


@dataclass(frozen=True)
class GraphNodeExecutionContext:
    runtime: Any
    graph_run: GraphRunRecord
    node_run: NodeRunRecord
    node: GraphNode
    template: GraphTemplate
    input_payload: dict[str, Any]
    run_index: int
    worker_id: str


@dataclass(frozen=True)
class GraphNodeExecutionResult:
    status: NodeRunStatus
    outcome: str
    output_payload: dict[str, Any]
    artifact_refs: list[str]
    evidence_package_id: str | None = None
    error: dict[str, Any] | None = None


GraphHandler = Callable[[GraphNodeExecutionContext], GraphNodeExecutionResult | dict[str, Any]]


class GraphNodeExecutorRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, GraphHandler] = {}

    def register(self, handler_id: str, handler: GraphHandler) -> None:
        if handler_id in self._handlers:
            raise ValueError(f"graph handler already registered: {handler_id}")
        self._handlers[handler_id] = handler

    def execute(self, handler_id: str, context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
        handler = self._handlers.get(handler_id)
        if handler is None:
            raise KeyError(f"graph handler not found: {handler_id}")
        return _normalize_result(handler(context))


def execute_graph_node(
    context: GraphNodeExecutionContext,
    *,
    registry: GraphNodeExecutorRegistry | None = None,
) -> GraphNodeExecutionResult:
    try:
        if context.node.handler:
            if registry is None:
                raise KeyError(f"graph handler not found: {context.node.handler}")
            result = registry.execute(context.node.handler, context)
        elif context.node.kind == "worker":
            result = _execute_worker(context)
        elif context.node.role == "planner":
            result = _execute_planner(context)
        elif context.node.role == "validator":
            result = _execute_validator(context)
        elif context.node.role == "scorecard":
            result = _execute_scorecard(context)
        elif context.node.role == "approval_gate":
            result = _execute_approval_gate(context)
        else:
            raise ValueError(f"unsupported graph node executor: {context.node.node_id}")
        if result.status == NodeRunStatus.SUCCEEDED:
            _validate_output(context, result.output_payload)
        return result
    except HandoffValidationError as exc:
        return _failed_result("handoff_validation", str(exc))
    except Exception as exc:
        return _failed_result(
            "graph_node_execution",
            str(exc),
            details={"error_type": type(exc).__name__},
        )


def _execute_planner(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    output = dict(context.input_payload)
    output["planning_source"] = "graph_initial_payload"
    return GraphNodeExecutionResult(NodeRunStatus.SUCCEEDED, "succeeded", output, [])


def _execute_validator(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    _validate_input(context)
    output = dict(context.input_payload)
    return GraphNodeExecutionResult(NodeRunStatus.SUCCEEDED, "succeeded", output, [])


def _execute_worker(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    _validate_input(context)
    runtime = context.runtime
    store = runtime.store
    bound_job_id = store.get_graph_node_job(
        context.graph_run.graph_run_id,
        context.node.node_id,
        context.run_index,
    )
    if bound_job_id is None:
        already_bound = set(store.list_graph_bound_job_ids(context.graph_run.graph_run_id))
        matching = [
            job
            for job in runtime.list_jobs(context.graph_run.mission_id)
            if job.capability == context.node.capability
            and job.status == JobStatus.QUEUED
            and job.job_id not in already_bound
        ]
        if matching:
            job = matching[0]
        else:
            job = runtime.create_job(
                context.graph_run.mission_id,
                context.node.capability,
                f"graph:{context.graph_run.graph_run_id}:{context.node.node_id}:{context.run_index}",
                _worker_input(context.input_payload),
            )
        bound_job_id = store.bind_graph_node_job(
            context.graph_run.graph_run_id,
            context.node.node_id,
            context.run_index,
            job.job_id,
        )
    result = runtime.execute_job(bound_job_id, context.worker_id)
    if result.status == JobStatus.FAILED:
        return GraphNodeExecutionResult(
            NodeRunStatus.FAILED,
            "failed",
            {},
            [],
            error=None if result.error is None else result.error.to_json_dict(),
        )
    output = dict(result.output_payload)
    output["artifact_refs"] = list(result.artifact_refs)
    explicit_outcome = output.pop("edge_outcome", None)
    if explicit_outcome:
        outcome = str(explicit_outcome)
    elif isinstance(output.get("approval_required"), dict):
        outcome = "approval_required"
    else:
        outcome = "succeeded"
    return GraphNodeExecutionResult(
        NodeRunStatus.SUCCEEDED,
        outcome,
        output,
        list(result.artifact_refs),
    )


def _execute_scorecard(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    _validate_input(context)
    report = score_mission(
        context.runtime,
        context.graph_run.mission_id,
        template_id=context.graph_run.template_id,
    )
    artifact_refs = [
        artifact_ref
        for job in context.runtime.list_jobs(context.graph_run.mission_id)
        if job.status == JobStatus.SUCCEEDED
        for artifact_ref in job.artifact_refs
    ]
    evidence = context.runtime.store.create_evidence_package(
        EvidencePackage.create(
            evidence_package_id=str(uuid4()),
            mission_id=context.graph_run.mission_id,
            producer_kind="node",
            producer_id=context.node_run.node_run_id,
            summary={"scorecard": report},
            artifact_refs=artifact_refs,
            token_budget={"raw_trace_policy": "artifact_only"},
        )
    )
    status = NodeRunStatus.SUCCEEDED if report["status"] == "passed" else NodeRunStatus.FAILED
    return GraphNodeExecutionResult(
        status,
        report["status"],
        report,
        artifact_refs,
        evidence_package_id=evidence.evidence_package_id,
        error=None if status == NodeRunStatus.SUCCEEDED else {"scorecard_status": report["status"]},
    )


def _execute_approval_gate(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    approval_id = str(
        context.node_run.output_payload.get("approval_id")
        or context.input_payload.get("approval_id")
        or ""
    )
    approval = None
    if approval_id:
        approval = context.runtime.store.get_approval(approval_id)
    else:
        pending = context.runtime.store.list_approvals(
            context.graph_run.mission_id,
            decision=ApprovalDecision.PENDING,
        )
        if pending:
            approval = pending[-1]
        else:
            approval = ApprovalService(context.runtime.store).request_approval(
                context.graph_run.mission_id,
                f"graph_gate:{context.graph_run.graph_run_id}:{context.node.node_id}:{context.run_index}",
                [
                    {"id": "approve", "label": "Approve"},
                    {"id": "reject", "label": "Reject"},
                ],
            )
    output = _approval_output(context.input_payload, approval)
    if approval.decision == ApprovalDecision.PENDING:
        return GraphNodeExecutionResult(
            NodeRunStatus.WAITING_APPROVAL,
            "waiting_approval",
            output,
            [],
        )
    if approval.decision == ApprovalDecision.APPROVED:
        return GraphNodeExecutionResult(NodeRunStatus.SUCCEEDED, "approved", output, [])
    return GraphNodeExecutionResult(
        NodeRunStatus.FAILED,
        "rejected",
        output,
        [],
        error={"error_class": "approval_rejected", "approval_id": approval.approval_id},
    )


def _approval_output(input_payload: dict[str, Any], approval) -> dict[str, Any]:
    output = {
        "approval_id": approval.approval_id,
        "decision": approval.decision.value,
    }
    for key in ("action_id", "digest"):
        if key in input_payload:
            output[key] = input_payload[key]
    for option in approval.options:
        if not isinstance(option, dict):
            continue
        for key in ("action_id", "action_digest"):
            if key in option and key not in output:
                output["digest" if key == "action_digest" else key] = option[key]
    return output


def _validate_input(context: GraphNodeExecutionContext) -> None:
    if not context.node.input_schema:
        return
    schema = context.template.handoffs[context.node.input_schema]
    validate_handoff(schema, context.input_payload)


def _validate_output(context: GraphNodeExecutionContext, payload: dict[str, Any]) -> None:
    if not context.node.output_schema:
        return
    schema = context.template.handoffs[context.node.output_schema]
    validate_handoff(schema, payload)


def _worker_input(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "_handoffs"}


def _failed_result(error_class: str, message: str, *, details: dict[str, Any] | None = None):
    return GraphNodeExecutionResult(
        NodeRunStatus.FAILED,
        "failed",
        {},
        [],
        error={
            "error_class": error_class,
            "message": message,
            "details": details or {},
        },
    )


def _normalize_result(value: GraphNodeExecutionResult | dict[str, Any]) -> GraphNodeExecutionResult:
    if isinstance(value, GraphNodeExecutionResult):
        return value
    return GraphNodeExecutionResult(
        status=NodeRunStatus(value["status"]),
        outcome=str(value["outcome"]),
        output_payload=dict(value.get("output_payload") or {}),
        artifact_refs=list(value.get("artifact_refs") or []),
        evidence_package_id=value.get("evidence_package_id"),
        error=value.get("error"),
    )
