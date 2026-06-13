from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.models import Workflow, WorkflowNode, WorkflowParameter
from aedt_agent.workflow.templates import WorkflowTemplateCatalog
from aedt_agent.workflow.validator import WorkflowValidator


@dataclass(frozen=True)
class ChatPlannerInput:
    user_request: str
    node_catalog: NodeCatalog
    workflow_templates: WorkflowTemplateCatalog
    retrieved_context: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChatPlannerOutput:
    selected_template: str | None = None
    generated_workflow: Workflow | None = None
    missing_information: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    validation_errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_template": self.selected_template,
            "generated_workflow": self.generated_workflow.to_dict() if self.generated_workflow else None,
            "missing_information": list(self.missing_information),
            "assumptions": list(self.assumptions),
            "confidence": self.confidence,
            "validation_errors": list(self.validation_errors),
        }


class ChatWorkflowPlanner:
    def plan(self, planner_input: ChatPlannerInput) -> ChatPlannerOutput:
        request = planner_input.user_request.strip()
        if not request:
            return ChatPlannerOutput(missing_information=["user_request"], confidence=0.0)

        template_id, blocked_reason = _select_template(request, planner_input.workflow_templates, planner_input.node_catalog)
        if blocked_reason is not None:
            return ChatPlannerOutput(
                missing_information=[blocked_reason],
                assumptions=["Matched an experimental workflow, but experimental nodes are not enabled in the supplied node catalog."],
                confidence=0.3,
            )
        if template_id:
            template = planner_input.workflow_templates.get(template_id)
            workflow = template.instantiate(_parameter_overrides(request))
            return _validated_output(
                workflow=workflow,
                selected_template=template_id,
                validator=WorkflowValidator(planner_input.node_catalog),
                assumptions=[f"Selected template {template_id} from request keywords."],
                confidence=0.82,
            )

        generated = _generate_simple_workflow(request)
        if generated is None:
            return ChatPlannerOutput(
                missing_information=["simulation_type"],
                assumptions=["No matching workflow template or safe node-only pattern was found."],
                confidence=0.2,
            )
        return _validated_output(
            workflow=generated,
            selected_template=None,
            validator=WorkflowValidator(planner_input.node_catalog),
            assumptions=["Generated a simple workflow from node catalog keywords."],
            confidence=0.62,
        )


def _select_template(request: str, templates: WorkflowTemplateCatalog, node_catalog: NodeCatalog) -> tuple[str | None, str | None]:
    lowered = request.lower()
    candidates = {
        "import_brd_cutout_sparam_tdr": ["brd", "mcm", "cutout", "cadence", "allegro", "切割", "导入"],
        "dipole_antenna_s11_farfield": ["dipole", "偶极子", "far field", "farfield", "gain pattern", "方向图", "增益"],
        "microstrip_sparameter": ["microstrip", "s-parameter", "s parameter", "transmission line"],
        "wave_port_setup": ["wave port", "waveport", "port face"],
        "radiation_airbox_setup": ["radiation", "airbox", "open region", "antenna"],
    }
    for template_id, keywords in candidates.items():
        if template_id in templates.templates and any(keyword in lowered for keyword in keywords):
            if _template_nodes_available(templates.get(template_id), node_catalog):
                return template_id, None
            return None, "experimental_workflow_not_enabled"
    return None, None


def _template_nodes_available(template: Any, node_catalog: NodeCatalog) -> bool:
    available = set(node_catalog.metadata)
    return all(node.node_id in available for node in template.workflow.nodes)


def _parameter_overrides(request: str) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    frequency = _first_frequency(request)
    if frequency:
        overrides["frequency"] = frequency
    target = _target_resonance_frequency(request)
    if target:
        overrides["target_resonance_frequency"] = target
    stop = _sweep_stop(request)
    if stop:
        overrides["sweep_stop"] = stop
    padding = _first_number_after(request, "padding")
    if padding is not None:
        overrides["airbox_padding"] = padding
    return overrides


def _generate_simple_workflow(request: str) -> Workflow | None:
    lowered = request.lower()
    if "setup" in lowered:
        frequency = _first_frequency(request)
        if frequency is None:
            return Workflow(
                workflow_id="generated_setup_missing_frequency",
                name="Generated Setup",
                parameters=[WorkflowParameter(name="frequency", type="string")],
                nodes=[WorkflowNode(id="setup", node_id="create_setup", inputs={})],
            )
        return Workflow(
            workflow_id="generated_setup",
            name="Generated Setup",
            parameters=[WorkflowParameter(name="frequency", type="string", default=frequency)],
            nodes=[WorkflowNode(id="setup", node_id="create_setup", inputs={"frequency": {"$ref": "parameters.frequency"}})],
        )
    return None


def _validated_output(
    workflow: Workflow,
    selected_template: str | None,
    validator: WorkflowValidator,
    assumptions: list[str],
    confidence: float,
) -> ChatPlannerOutput:
    validation = validator.validate(workflow)
    return ChatPlannerOutput(
        selected_template=selected_template,
        generated_workflow=workflow,
        missing_information=[] if validation.passed else _missing_from_validation(validation.to_dict()),
        assumptions=assumptions,
        confidence=confidence if validation.passed else min(confidence, 0.4),
        validation_errors=validation.to_dict()["errors"],
    )


def _missing_from_validation(validation: dict[str, Any]) -> list[str]:
    missing = []
    for error in validation.get("errors", []):
        if error.get("code") == "missing_input" and error.get("field"):
            missing.append(str(error["field"]))
    return missing


def _first_frequency(text: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ghz|mhz|khz|hz|g|m|k)(?![A-Za-z])", text, flags=re.IGNORECASE)
    if not match:
        return None
    return f"{match.group(1)}{_frequency_unit(match.group(2))}"


def _target_resonance_frequency(text: str) -> str | None:
    match = re.search(
        r"(?:优化|調整|调整|目标|目標|谐振|諧振|resonance|target|optimi[sz]e)[^\d]{0,24}(?:到|至|为|為|at|to)?\s*(\d+(?:\.\d+)?)\s*(ghz|mhz|khz|hz|g|m|k)(?![A-Za-z])",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return f"{match.group(1)}{_frequency_unit(match.group(2))}"


def _sweep_stop(text: str) -> str | None:
    match = re.search(r"(?:to|stop(?:s)?(?: at)?|扫频到|扫到|截止到|到)\s*(\d+(?:\.\d+)?)\s*(ghz|mhz|khz|hz|g|m|k)(?![A-Za-z])", text, flags=re.IGNORECASE)
    if not match:
        return None
    return f"{match.group(1)}{_frequency_unit(match.group(2))}"


def _first_number_after(text: str, keyword: str) -> float | None:
    match = re.search(rf"{re.escape(keyword)}\D+(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))


def _frequency_unit(unit: str) -> str:
    return {
        "ghz": "GHz",
        "mhz": "MHz",
        "khz": "KHz",
        "hz": "Hz",
        "g": "GHz",
        "m": "MHz",
        "k": "KHz",
    }[unit.lower()]
