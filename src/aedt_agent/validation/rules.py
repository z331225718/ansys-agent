from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aedt_agent.validation.inspector import AedtModelFacts


@dataclass(frozen=True)
class ValidationCheckResult:
    rule: str
    target: str
    passed: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "target": self.target,
            "passed": self.passed,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class ModelValidationResult:
    passed: bool
    checks: list[ValidationCheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "failed_checks": [check.to_dict() for check in self.checks if not check.passed],
        }


def validate_model_facts(facts: AedtModelFacts, checks: list[dict[str, Any]]) -> ModelValidationResult:
    results = [_run_check(facts, check) for check in checks]
    return ModelValidationResult(passed=all(check.passed for check in results), checks=results)


def validation_repair_context(result: ModelValidationResult) -> dict[str, Any]:
    return {
        "reason": "model_validation_failed",
        "failed_checks": [check.to_dict() for check in result.checks if not check.passed],
    }


def _run_check(facts: AedtModelFacts, check: dict[str, Any]) -> ValidationCheckResult:
    rule = str(check.get("rule", ""))
    target = str(check.get("target", ""))
    expected = check.get("expected")
    if rule == "object_exists":
        return _exists(rule, target, target in facts.objects, "object")
    if rule == "material_assigned":
        passed = target in facts.materials and (expected is None or facts.materials[target] == expected)
        return ValidationCheckResult(rule, target, passed, _message(passed, f"material assigned to {target}"), {"material": facts.materials.get(target), "expected": expected})
    if rule == "port_exists":
        return _exists(rule, target, target in facts.ports, "port")
    if rule == "port_assignment_valid":
        return _assignment_valid(rule, target, facts.ports.get(target), facts)
    if rule == "boundary_exists":
        return _exists(rule, target, target in facts.boundaries, "boundary")
    if rule == "setup_exists":
        return _exists(rule, target, target in facts.setups, "setup")
    if rule == "sweep_exists":
        return _exists(rule, target, target in facts.sweeps, "sweep")
    if rule == "sweep_attached_to_setup":
        setup = str(check.get("setup", expected or ""))
        sweep = facts.sweeps.get(target, {})
        passed = bool(sweep) and (not setup or str(sweep.get("setup", "")) == setup)
        return ValidationCheckResult(rule, target, passed, _message(passed, f"sweep {target} attached to setup {setup}"), {"setup": sweep.get("setup"), "expected": setup})
    if rule == "airbox_radiation_relation_valid":
        boundary = facts.boundaries.get(target, {})
        assignment = boundary.get("assignment")
        passed = bool(boundary) and _assignment_references(assignment, facts.objects)
        return ValidationCheckResult(rule, target, passed, _message(passed, f"radiation boundary {target} references model object"), {"assignment": assignment})
    return ValidationCheckResult(rule, target, False, f"unsupported validation rule: {rule}")


def _exists(rule: str, target: str, passed: bool, kind: str) -> ValidationCheckResult:
    return ValidationCheckResult(rule, target, passed, _message(passed, f"{kind} exists: {target}"))


def _assignment_valid(rule: str, target: str, port: dict[str, Any] | None, facts: AedtModelFacts) -> ValidationCheckResult:
    assignment = port.get("assignment") if port else None
    passed = bool(port) and (_assignment_references(assignment, facts.objects) or _assignment_references_face(assignment, facts.faces))
    return ValidationCheckResult(rule, target, passed, _message(passed, f"port {target} assignment is valid"), {"assignment": assignment})


def _assignment_references(assignment: Any, objects: dict[str, Any]) -> bool:
    if isinstance(assignment, str):
        return assignment in objects
    if isinstance(assignment, list):
        return any(_assignment_references(item, objects) for item in assignment)
    return False


def _assignment_references_face(assignment: Any, faces: dict[str, list[Any]]) -> bool:
    face_ids = {_coerce_int(face_id) for face_list in faces.values() for face_id in face_list}
    face_ids.discard(None)
    if isinstance(assignment, str):
        assignment = _coerce_int(assignment)
    if isinstance(assignment, int):
        return assignment in face_ids
    if isinstance(assignment, list):
        return any(_assignment_references_face(item, faces) for item in assignment)
    return False


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _message(passed: bool, text: str) -> str:
    return text if passed else f"failed: {text}"
