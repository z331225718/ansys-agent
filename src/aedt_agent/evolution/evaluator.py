from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aedt_agent.evolution.models import NodeEvolutionProposal, ReviewStatus


@dataclass(frozen=True)
class ProposalEvaluationResult:
    proposal_id: str
    accepted_status: ReviewStatus
    passed: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "accepted_status": self.accepted_status.value,
            "passed": self.passed,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def evaluate_proposal(
    proposal: NodeEvolutionProposal,
    *,
    available_tests: set[str],
    workflow_validator_passed: bool,
    real_aedt_smoke_passed: bool = False,
    manual_gated: bool = False,
    benchmark_regression_passed: bool = False,
    human_approved: bool = False,
) -> ProposalEvaluationResult:
    blockers: list[str] = []
    warnings: list[str] = []
    metadata = proposal.candidate_node_metadata
    if not metadata.get("node_id"):
        blockers.append("candidate_node_metadata.node_id is required")
    if not metadata.get("description"):
        blockers.append("candidate_node_metadata.description is required")
    missing_tests = sorted(set(proposal.required_tests) - available_tests - {"real_aedt_smoke_or_manual_gate", "benchmark_regression"})
    if missing_tests:
        blockers.append(f"missing required tests: {', '.join(missing_tests)}")
    if not workflow_validator_passed:
        blockers.append("workflow validator did not pass")
    requires_aedt = "real_aedt_smoke_or_manual_gate" in proposal.required_tests
    if requires_aedt and not (real_aedt_smoke_passed or manual_gated):
        blockers.append("real AEDT smoke or manual gate is required")
    if "benchmark_regression" in proposal.required_tests and not benchmark_regression_passed:
        blockers.append("benchmark regression evidence is required")
    if proposal.review_status == ReviewStatus.STABLE_APPROVED and not human_approved:
        blockers.append("stable approval requires human review")

    if blockers:
        status = ReviewStatus.NEEDS_TESTS
    elif manual_gated and not real_aedt_smoke_passed:
        status = ReviewStatus.MANUAL_GATED
        warnings.append("candidate requires manual AEDT validation before release")
    else:
        status = ReviewStatus.CANDIDATE
    return ProposalEvaluationResult(
        proposal_id=proposal.proposal_id,
        accepted_status=status,
        passed=not blockers,
        blockers=blockers,
        warnings=warnings,
    )
