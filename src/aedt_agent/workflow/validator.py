from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aedt_agent.mcp.node_schemas import NODE_SCHEMAS
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.models import Workflow


@dataclass(frozen=True)
class WorkflowValidationIssue:
    code: str
    message: str
    node_id: str | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
            "field": self.field,
        }


@dataclass(frozen=True)
class WorkflowValidationResult:
    passed: bool
    errors: list[WorkflowValidationIssue] = field(default_factory=list)
    warnings: list[WorkflowValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


class WorkflowValidator:
    def __init__(self, catalog: NodeCatalog) -> None:
        self.catalog = catalog

    def validate(self, workflow: Workflow) -> WorkflowValidationResult:
        errors: list[WorkflowValidationIssue] = []
        warnings: list[WorkflowValidationIssue] = []
        node_ids = [node.id for node in workflow.nodes]
        node_id_set = set(node_ids)
        parameter_names = {parameter.name for parameter in workflow.parameters}

        duplicates = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
        for duplicate in duplicates:
            errors.append(WorkflowValidationIssue("duplicate_node_id", f"duplicate workflow node id: {duplicate}", node_id=duplicate))

        for node in workflow.nodes:
            if node.node_id not in self.catalog.metadata:
                errors.append(WorkflowValidationIssue("unknown_node", f"unknown node_id: {node.node_id}", node_id=node.id))
                continue
            errors.extend(_validate_node_inputs(workflow, node.id, node.node_id, node.inputs))

        for edge in workflow.edges:
            errors.extend(_validate_edge(edge.source, edge.target, node_id_set, parameter_names, workflow))

        errors.extend(_validate_dag_order(workflow))
        errors.extend(_validate_prerequisites(workflow))
        warnings.extend(_validate_parameter_ranges(workflow))

        return WorkflowValidationResult(passed=not errors, errors=errors, warnings=warnings)


def validate_workflow(workflow: Workflow, catalog: NodeCatalog) -> WorkflowValidationResult:
    return WorkflowValidator(catalog).validate(workflow)


def _validate_node_inputs(workflow: Workflow, workflow_node_id: str, executable_node_id: str, inputs: dict[str, Any]) -> list[WorkflowValidationIssue]:
    schema = NODE_SCHEMAS.get(executable_node_id)
    if schema is None:
        return [WorkflowValidationIssue("missing_node_schema", f"missing input schema for node_id: {executable_node_id}", node_id=workflow_node_id)]
    errors: list[WorkflowValidationIssue] = []
    normalized = dict(schema.defaults)
    normalized.update(inputs)
    for key in schema.required:
        if key not in normalized and not _edge_targets_input(workflow, workflow_node_id, key):
            errors.append(WorkflowValidationIssue("missing_input", f"missing required input: {key}", node_id=workflow_node_id, field=key))
    for key in inputs:
        if key not in schema.allowed_keys:
            errors.append(WorkflowValidationIssue("unknown_input", f"unknown input: {key}", node_id=workflow_node_id, field=key))
    for key, expected_type in {**schema.required, **schema.optional}.items():
        if key in normalized and not _input_matches_type_or_ref(normalized[key], expected_type):
            errors.append(WorkflowValidationIssue("wrong_input_type", f"wrong type for {key}", node_id=workflow_node_id, field=key))
    return errors


def _edge_targets_input(workflow: Workflow, node_id: str, input_name: str) -> bool:
    prefix = f"{node_id}.inputs.{input_name}"
    return any(edge.target == prefix or edge.target.startswith(f"{prefix}.") for edge in workflow.edges)


def _input_matches_type_or_ref(value: Any, expected_type: type | tuple[type, ...]) -> bool:
    if _contains_ref(value):
        return True
    return isinstance(value, expected_type)


def _contains_ref(value: Any) -> bool:
    if isinstance(value, dict):
        if isinstance(value.get("$ref"), str):
            return True
        return any(_contains_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ref(item) for item in value)
    return False


def _validate_edge(
    source: str,
    target: str,
    node_ids: set[str],
    parameter_names: set[str],
    workflow: Workflow,
) -> list[WorkflowValidationIssue]:
    errors: list[WorkflowValidationIssue] = []
    source_parts = source.split(".")
    target_parts = target.split(".")
    if len(source_parts) < 2:
        errors.append(WorkflowValidationIssue("invalid_edge_source", f"invalid edge source: {source}", field=source))
    elif source_parts[0] == "parameters":
        if len(source_parts) != 2 or source_parts[1] not in parameter_names:
            errors.append(WorkflowValidationIssue("unknown_parameter_ref", f"unknown parameter reference: {source}", field=source))
    elif source_parts[0] not in node_ids:
        errors.append(WorkflowValidationIssue("unknown_source_node", f"unknown source node: {source_parts[0]}", node_id=source_parts[0], field=source))

    if len(target_parts) < 3 or target_parts[1] != "inputs":
        errors.append(WorkflowValidationIssue("invalid_edge_target", f"invalid edge target: {target}", field=target))
    elif target_parts[0] not in node_ids:
        errors.append(WorkflowValidationIssue("unknown_target_node", f"unknown target node: {target_parts[0]}", node_id=target_parts[0], field=target))
    else:
        target_node = workflow.node_by_id(target_parts[0])
        schema = NODE_SCHEMAS.get(target_node.node_id)
        if schema is not None and target_parts[2] not in schema.allowed_keys:
            errors.append(WorkflowValidationIssue("unknown_target_input", f"unknown target input: {target}", node_id=target_node.id, field=target_parts[2]))
    return errors


def _validate_dag_order(workflow: Workflow) -> list[WorkflowValidationIssue]:
    order = {node.id: index for index, node in enumerate(workflow.nodes)}
    errors: list[WorkflowValidationIssue] = []
    for edge in workflow.edges:
        source_node = edge.source.split(".", 1)[0]
        target_node = edge.target.split(".", 1)[0]
        if source_node == "parameters":
            continue
        if source_node in order and target_node in order and order[source_node] >= order[target_node]:
            errors.append(WorkflowValidationIssue("dependency_order", f"source node must run before target node: {edge.source} -> {edge.target}", node_id=target_node))
    return errors


def _validate_prerequisites(workflow: Workflow) -> list[WorkflowValidationIssue]:
    errors: list[WorkflowValidationIssue] = []
    completed_node_ids: set[str] = set()
    for node in workflow.nodes:
        if node.node_id == "create_port" and not _has_input_or_edge(workflow, node.id, "assignment"):
            errors.append(WorkflowValidationIssue("missing_port_assignment", "create_port requires an assignment from geometry or selected face", node_id=node.id, field="assignment"))
        if node.node_id == "assign_boundary" and not _has_input_or_edge(workflow, node.id, "assignment"):
            errors.append(WorkflowValidationIssue("missing_boundary_assignment", "assign_boundary requires an object, face, or region assignment", node_id=node.id, field="assignment"))
        if node.node_id == "create_sweep_or_export" and not _has_setup_dependency(workflow, node.id, completed_node_ids):
            errors.append(WorkflowValidationIssue("missing_setup_dependency", "create_sweep_or_export requires a setup dependency", node_id=node.id, field="setup"))
        if node.node_id == "solve_setup" and not _has_setup_dependency(workflow, node.id, completed_node_ids):
            errors.append(WorkflowValidationIssue("missing_setup_dependency", "solve_setup requires a setup dependency", node_id=node.id, field="setup"))
        if node.node_id == "create_sparameter_report" and not _has_setup_dependency(workflow, node.id, completed_node_ids):
            errors.append(WorkflowValidationIssue("missing_setup_dependency", "create_sparameter_report requires a setup dependency", node_id=node.id, field="setup"))
        completed_node_ids.add(node.id)
    return errors


def _has_input_or_edge(workflow: Workflow, node_id: str, input_name: str) -> bool:
    node = workflow.node_by_id(node_id)
    if input_name in node.inputs:
        return True
    prefix = f"{node_id}.inputs.{input_name}"
    return any(edge.target == prefix or edge.target.startswith(f"{prefix}.") for edge in workflow.edges)


def _has_setup_dependency(workflow: Workflow, node_id: str, completed_node_ids: set[str]) -> bool:
    node = workflow.node_by_id(node_id)
    setup_value = node.inputs.get("setup")
    if isinstance(setup_value, str) and setup_value:
        return True
    incoming = [edge for edge in workflow.edges if edge.target == f"{node_id}.inputs.setup"]
    for edge in incoming:
        source_node = edge.source.split(".", 1)[0]
        if source_node in completed_node_ids:
            return True
    return False


def _validate_parameter_ranges(workflow: Workflow) -> list[WorkflowValidationIssue]:
    warnings: list[WorkflowValidationIssue] = []
    for parameter in workflow.parameters:
        if isinstance(parameter.default, (int, float)):
            if parameter.minimum is not None and parameter.default < parameter.minimum:
                warnings.append(WorkflowValidationIssue("parameter_default_below_min", f"default below minimum for parameter: {parameter.name}", field=parameter.name))
            if parameter.maximum is not None and parameter.default > parameter.maximum:
                warnings.append(WorkflowValidationIssue("parameter_default_above_max", f"default above maximum for parameter: {parameter.name}", field=parameter.name))
    return warnings
