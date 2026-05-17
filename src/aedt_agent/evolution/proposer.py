from __future__ import annotations

import hashlib

from aedt_agent.evolution.models import EvolutionAction, NodeEvolutionEvidence, NodeEvolutionProposal, NodeEvolutionReport


def propose_node_evolution(evidence: list[NodeEvolutionEvidence]) -> list[NodeEvolutionProposal]:
    proposals: list[NodeEvolutionProposal] = []
    for item in evidence:
        proposal = _proposal_from_evidence(item)
        if proposal is not None:
            proposals.append(proposal)
    return _deduplicate(proposals)


def build_evolution_report(evidence: list[NodeEvolutionEvidence], source_count: int = 1) -> NodeEvolutionReport:
    return NodeEvolutionReport(source_count=source_count, evidence=evidence, proposals=propose_node_evolution(evidence))


def _proposal_from_evidence(item: NodeEvolutionEvidence) -> NodeEvolutionProposal | None:
    if item.kind == "node_subgraph" and item.count >= 1 and len(item.node_ids) >= 2:
        node_id = "composite_" + "_".join(_short_node_name(node_id) for node_id in item.node_ids[:4])
        return NodeEvolutionProposal(
            proposal_id=_proposal_id(item.kind, item.summary),
            source=item.source,
            problem_pattern=f"Repeated node subgraph: {item.summary}",
            affected_tasks=list(item.tasks),
            recommended_action=EvolutionAction.ADD_NODE,
            candidate_node_metadata={
                "node_id": node_id,
                "category": "workflow",
                "description": f"Composite node candidate for: {item.summary}",
                "stability": "experimental",
            },
            required_tests=["test_node_catalog.py", "test_workflow_validator.py", "real_aedt_smoke_or_manual_gate"],
            risk_level="medium",
            evidence=[item],
            notes=["Composite nodes reduce drag steps for experienced users but require careful schema design."],
        )
    if item.kind in {"failure_pattern", "audit_failure"}:
        return NodeEvolutionProposal(
            proposal_id=_proposal_id(item.kind, item.summary),
            source=item.source,
            problem_pattern=f"Failure pattern: {item.summary}",
            affected_tasks=list(item.tasks),
            recommended_action=EvolutionAction.ADD_POSTCHECK,
            candidate_node_metadata={
                "node_id": "existing_node_upgrade",
                "category": "validation",
                "description": f"Add postcheck or normalization for failure pattern: {item.summary}",
                "stability": "experimental",
            },
            required_tests=["test_node_executor.py", "test_inspector_validation.py", "benchmark_regression"],
            risk_level="high",
            evidence=[item],
            notes=["Failure-driven proposals must include regression evidence before candidate release."],
        )
    if item.kind == "repeated_repair":
        return NodeEvolutionProposal(
            proposal_id=_proposal_id(item.kind, item.summary),
            source=item.source,
            problem_pattern=f"Repeated repair loop: {item.summary}",
            affected_tasks=list(item.tasks),
            recommended_action=EvolutionAction.ADD_NORMALIZATION,
            candidate_node_metadata={
                "node_id": "existing_node_normalization",
                "category": "validation",
                "description": f"Add schema normalization for repeated repair loop: {item.summary}",
                "stability": "experimental",
            },
            required_tests=["test_node_schemas.py", "test_node_executor.py", "benchmark_regression"],
            risk_level="medium",
            evidence=[item],
        )
    return None


def _deduplicate(proposals: list[NodeEvolutionProposal]) -> list[NodeEvolutionProposal]:
    by_id = {}
    for proposal in proposals:
        by_id.setdefault(proposal.proposal_id, proposal)
    return [by_id[key] for key in sorted(by_id)]


def _proposal_id(kind: str, summary: str) -> str:
    digest = hashlib.sha1(f"{kind}:{summary}".encode("utf-8")).hexdigest()[:10]
    return f"proposal-{digest}"


def _short_node_name(node_id: str) -> str:
    return node_id.removeprefix("create_").replace("_or_", "_").replace("_", "-")
