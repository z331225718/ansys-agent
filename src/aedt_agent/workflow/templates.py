from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aedt_agent.workflow.models import Workflow, WorkflowParameter


@dataclass(frozen=True)
class WorkflowTemplate:
    template_id: str
    name: str
    description: str
    scenario: str
    workflow: Workflow
    validation_checks: list[str] = field(default_factory=list)
    known_limits: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowTemplate":
        workflow_data = data.get("workflow")
        if not isinstance(workflow_data, dict):
            raise TypeError("workflow template requires a workflow mapping")
        return cls(
            template_id=str(data["template_id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            scenario=str(data.get("scenario", "")),
            workflow=Workflow.from_dict(workflow_data),
            validation_checks=_list_of_strings(data, "validation_checks"),
            known_limits=_list_of_strings(data, "known_limits"),
            tags=_list_of_strings(data, "tags"),
        )

    @classmethod
    def from_file(cls, path: Path) -> "WorkflowTemplate":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"{path} must contain a JSON object")
        return cls.from_dict(data)

    def instantiate(self, parameters: dict[str, Any] | None = None) -> Workflow:
        overrides = _derived_parameter_overrides(self.template_id, parameters or {})
        workflow_data = self.workflow.to_dict()
        workflow_data["parameters"] = [_parameter_with_override(parameter, overrides).to_dict() for parameter in self.workflow.parameters]
        workflow_data["metadata"] = {
            **workflow_data.get("metadata", {}),
            "template_id": self.template_id,
            "template_name": self.name,
        }
        return Workflow.from_dict(workflow_data)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "scenario": self.scenario,
            "workflow_id": self.workflow.workflow_id,
            "node_count": len(self.workflow.nodes),
            "parameters": [parameter.to_dict() for parameter in self.workflow.parameters],
            "validation_checks": list(self.validation_checks),
            "known_limits": list(self.known_limits),
            "tags": list(self.tags),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_summary_dict(),
            "workflow": self.workflow.to_dict(),
        }


@dataclass(frozen=True)
class WorkflowTemplateCatalog:
    templates: dict[str, WorkflowTemplate]

    @classmethod
    def from_directory(cls, directory: Path) -> "WorkflowTemplateCatalog":
        templates = {}
        for path in sorted(directory.glob("*.json")):
            template = WorkflowTemplate.from_file(path)
            templates[template.template_id] = template
        return cls(templates)

    def get(self, template_id: str) -> WorkflowTemplate:
        return self.templates[template_id]

    def list_templates(self) -> list[WorkflowTemplate]:
        return [self.templates[template_id] for template_id in sorted(self.templates)]

    def to_ui_dict(self) -> dict[str, Any]:
        return {
            "version": "0.1.0",
            "templates": [template.to_summary_dict() for template in self.list_templates()],
        }


def load_workflow_templates(directory: Path = Path("workflow_templates")) -> WorkflowTemplateCatalog:
    return WorkflowTemplateCatalog.from_directory(directory)


def _parameter_with_override(parameter: WorkflowParameter, overrides: dict[str, Any]) -> WorkflowParameter:
    if parameter.name not in overrides:
        return parameter
    return WorkflowParameter(
        name=parameter.name,
        type=parameter.type,
        default=overrides[parameter.name],
        unit=parameter.unit,
        minimum=parameter.minimum,
        maximum=parameter.maximum,
        label=parameter.label,
        description=parameter.description,
    )


def _derived_parameter_overrides(template_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
    values = dict(overrides)
    if template_id == "dipole_antenna_s11_farfield":
        values.update(_dipole_geometry_overrides(values))
    return values


def _dipole_geometry_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    frequency = _parse_frequency_hz(overrides.get("frequency", "2.4GHz"))
    if frequency is None:
        return {}
    feed_gap_mm = float(overrides.get("feed_gap_mm", 1.0))
    arm_radius_mm = float(overrides.get("arm_radius_mm", 0.5))
    velocity_factor = float(overrides.get("velocity_factor", 0.95))
    arm_length_mm = round(299_792_458.0 / (4.0 * frequency) * 1000.0 * velocity_factor, 3)
    half_gap = feed_gap_mm / 2.0
    return {
        "velocity_factor": velocity_factor,
        "feed_gap_mm": feed_gap_mm,
        "arm_radius_mm": arm_radius_mm,
        "dipole_arm_length_mm": arm_length_mm,
        "left_arm_origin": [round(-half_gap - arm_length_mm, 3), 0, 0],
        "right_arm_origin": [round(half_gap, 3), 0, 0],
        "feed_sheet_origin": [round(-half_gap, 3), 0, round(-arm_radius_mm, 3)],
        "feed_sheet_size": [round(feed_gap_mm, 3), round(2.0 * arm_radius_mm, 3)],
        "feed_line_start": [round(-half_gap, 3), 0, 0],
        "feed_line_end": [round(half_gap, 3), 0, 0],
    }


def _parse_frequency_hz(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([GMK]?Hz)\s*", value, flags=re.IGNORECASE)
    if not match:
        return None
    scale = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
    return float(match.group(1)) * scale[match.group(2).lower()]


def _list_of_strings(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return [str(item) for item in value]
