from __future__ import annotations

from dataclasses import dataclass, field

from aedt_agent.benchmark.models import BenchmarkTask
from aedt_agent.knowledge.models import ApiSemantic, CommonTrap


STEP_API_MAP: dict[str, list[str]] = {
    "create_substrate": ["create_box", "assign_material"],
    "create_conductor_or_geometry_group": ["create_box", "create_rectangle", "insert_3d_component", "unite", "subtract"],
    "select_face": ["get_object_faces", "get_face_center", "get_faceid_from_position"],
    "create_port": ["wave_port", "lumped_port", "create_probe_port"],
    "create_airbox": ["create_region", "create_open_region", "airbox", "region"],
    "assign_boundary": ["assign_radiation_boundary", "assign_radiation_boundary_to_objects", "assign_perfecte"],
    "create_setup": ["create_setup"],
    "create_sweep_or_export": ["create_linear_count_sweep", "create_frequency_sweep", "export_touchstone"],
}

DEFAULT_DEPENDENCIES: dict[str, list[str]] = {
    "wave_port": ["get_object_faces"],
    "lumped_port": ["get_object_faces"],
}


@dataclass(frozen=True)
class SemanticLiteResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def check_semantic_lite(
    code: str,
    task: BenchmarkTask | None,
    api_semantics: list[ApiSemantic],
    traps: list[CommonTrap],
) -> SemanticLiteResult:
    violations: list[str] = []

    if task is not None:
        for step in task.expected_workflow:
            step_apis = STEP_API_MAP.get(step, [])
            if step_apis and not _step_present(step, code, step_apis):
                violations.append(f"missing_step: {step} (expected APIs: {step_apis})")

    for target_api, required_apis in DEFAULT_DEPENDENCIES.items():
        if target_api in code:
            for req in required_apis:
                if req not in code:
                    violations.append(f"missing_dependency: {req} required before {target_api}")

    return SemanticLiteResult(passed=len(violations) == 0, violations=violations)


def _step_present(step: str, code: str, step_apis: list[str]) -> bool:
    if step == "create_airbox":
        return (
            "create_region" in code
            or "create_open_region" in code
            or "airbox" in code.lower()
            or "region" in code.lower()
        )
    return any(api in code for api in step_apis)
