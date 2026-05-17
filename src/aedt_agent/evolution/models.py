from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvolutionAction(str, Enum):
    ADD_NODE = "add_node"
    UPGRADE_SCHEMA = "upgrade_node_schema"
    ADD_NORMALIZATION = "add_normalization"
    ADD_POSTCHECK = "add_postcheck"
    UPGRADE_TEMPLATE = "upgrade_template"


class ReviewStatus(str, Enum):
    PROPOSED = "proposed"
    NEEDS_TESTS = "needs_tests"
    MANUAL_GATED = "manual_gated"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    STABLE_APPROVED = "stable_approved"


@dataclass(frozen=True)
class NodeEvolutionEvidence:
    source: str
    kind: str
    summary: str
    count: int = 1
    tasks: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "summary": self.summary,
            "count": self.count,
            "tasks": list(self.tasks),
            "node_ids": list(self.node_ids),
        }


@dataclass(frozen=True)
class NodeEvolutionProposal:
    proposal_id: str
    source: str
    problem_pattern: str
    affected_tasks: list[str]
    recommended_action: EvolutionAction
    candidate_node_metadata: dict[str, Any]
    required_tests: list[str]
    risk_level: str
    review_status: ReviewStatus = ReviewStatus.PROPOSED
    evidence: list[NodeEvolutionEvidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source": self.source,
            "problem_pattern": self.problem_pattern,
            "affected_tasks": list(self.affected_tasks),
            "recommended_action": self.recommended_action.value,
            "candidate_node_metadata": self.candidate_node_metadata,
            "required_tests": list(self.required_tests),
            "risk_level": self.risk_level,
            "review_status": self.review_status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class NodeEvolutionReport:
    source_count: int
    evidence: list[NodeEvolutionEvidence]
    proposals: list[NodeEvolutionProposal]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_count": self.source_count,
            "evidence": [item.to_dict() for item in self.evidence],
            "proposals": [item.to_dict() for item in self.proposals],
        }
