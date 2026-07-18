from __future__ import annotations

import hashlib
from importlib import metadata
import json
from typing import Any

from aedt_agent.exploration.contracts import ExplorationError, OperationPlan
from aedt_agent.exploration.policy import ExplorationPolicy


_TARGET_EVIDENCE_PACKAGE = {
    "desktop": "pyaedt",
    "hfss": "pyaedt",
    "hfss3dlayout": "pyaedt",
}


class OperationValidator:
    def __init__(
        self,
        *,
        policy: ExplorationPolicy | None = None,
        package_versions: dict[str, str] | None = None,
        evidence_verifier: Any | None = None,
    ) -> None:
        self.policy = policy or ExplorationPolicy()
        self.package_versions = package_versions or {
            "pyaedt": _version("pyaedt"),
            "pyedb": _version("pyedb"),
        }
        self.evidence_verifier = evidence_verifier

    def validate(self, value: dict[str, Any] | OperationPlan) -> dict[str, Any]:
        plan = value if isinstance(value, OperationPlan) else OperationPlan.from_dict(value)
        ids = [step.id for step in plan.steps]
        if len(ids) != len(set(ids)):
            raise ExplorationError("invalid_plan", "operation step ids must be unique")
        readback_ids = [check.id for check in plan.readback]
        if len(readback_ids) != len(set(readback_ids)):
            raise ExplorationError("invalid_plan", "readback check ids must be unique")
        for evidence in plan.evidence:
            expected = self.package_versions.get(evidence.package)
            if expected is None or evidence.package_version != expected:
                raise ExplorationError(
                    "evidence_stale",
                    f"{evidence.package} evidence version {evidence.package_version} does not match installed {expected}",
                )
        evidence_verification = {"status": "version_only"}
        if self.evidence_verifier is not None:
            verify = getattr(self.evidence_verifier, "verify", self.evidence_verifier)
            try:
                evidence_verification = verify([item.to_dict() for item in plan.evidence])
            except ExplorationError:
                raise
            except Exception as exc:
                raise ExplorationError(
                    "evidence_unavailable",
                    f"source evidence verifier failed: {type(exc).__name__}: {exc}",
                ) from exc
            if not isinstance(evidence_verification, dict) or evidence_verification.get("status") != "verified":
                raise ExplorationError("evidence_unverified", "source evidence verifier did not verify the plan")
        mutations = []
        step_bindings: dict[str, list[dict[str, str]]] = {}
        required_evidence_package = _TARGET_EVIDENCE_PACKAGE[plan.target.product]
        for step in plan.steps:
            self.policy.validate_path(step.path)
            bindings = _require_symbol_evidence(
                step.path,
                plan,
                self.policy,
                required_package=required_evidence_package,
            )
            step_bindings[step.id] = bindings
            classification = self.policy.classify(step, evidence_bindings=bindings)
            if classification == "reversible_edit":
                mutations.append(step)
        if plan.risk == "read_only" and mutations:
            raise ExplorationError("risk_mismatch", "read_only plan cannot contain mutation steps")
        if plan.risk == "reversible_edit" and not mutations:
            raise ExplorationError("risk_mismatch", "reversible_edit plan must contain a mutation")
        mutation_ids = {step.id for step in mutations}
        if mutations:
            if not plan.readback:
                raise ExplorationError("readback_required", "reversible edits require readback checks")
            readback_paths = {item.path for item in plan.readback}
            missing_readback = [step.path for step in mutations if step.path not in readback_paths]
            if missing_readback:
                raise ExplorationError("readback_required", f"mutation has no exact readback: {missing_readback[0]}")
            if set(plan.rollback) != mutation_ids:
                raise ExplorationError(
                    "rollback_required",
                    "rollback must list every mutation step id; values are captured by the server",
                )
        elif plan.rollback:
            raise ExplorationError("invalid_plan", "read-only plan cannot declare rollback steps")
        readback_bindings: dict[str, list[dict[str, str]]] = {}
        for check in plan.readback:
            self.policy.validate_path(check.path)
            readback_bindings[check.id] = _require_symbol_evidence(
                check.path,
                plan,
                self.policy,
                required_package=required_evidence_package,
            )
        normalized = plan.to_dict()
        plan_digest = hashlib.sha256(
            json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "status": "validated",
            "plan": normalized,
            "plan_digest": plan_digest,
            "policy_version": self.policy.version,
            "policy_digest": self.policy.digest,
            "risk": plan.risk,
            "mutation_count": len(mutations),
            "rollback_strategy": "server_snapshot" if mutations else "none",
            "evidence_verification": evidence_verification,
            "evidence_bindings": {
                "steps": step_bindings,
                "readback": readback_bindings,
            },
        }


def _require_symbol_evidence(
    path: str,
    plan: OperationPlan,
    policy: ExplorationPolicy,
    *,
    required_package: str,
) -> list[dict[str, str]]:
    member_bindings = policy.evidence_bindings(path, plan.evidence)
    bindings = [item for item in member_bindings if item["package"] == required_package]
    if bindings:
        return bindings
    if member_bindings:
        packages = sorted({item["package"] for item in member_bindings})
        raise ExplorationError(
            "evidence_package_mismatch",
            f"live target {plan.target.product} requires {required_package} evidence, got {packages}",
        )
    else:
        member = path.rsplit(".", 1)[-1].lower()
        raise ExplorationError(
            "evidence_required",
            f"no qualified class/member API evidence matches operation member: {member}",
        )


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "missing"
