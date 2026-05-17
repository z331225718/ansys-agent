from pathlib import Path

from aedt_agent.evolution.evaluator import evaluate_proposal
from aedt_agent.evolution.miner import mine_audit_jsonl, mine_evolution_evidence, mine_stage_b_report
from aedt_agent.evolution.models import EvolutionAction, NodeEvolutionEvidence, NodeEvolutionProposal, ReviewStatus
from aedt_agent.evolution.policy import can_transition
from aedt_agent.evolution.proposer import build_evolution_report, propose_node_evolution


def test_miner_extracts_failures_and_subgraphs_from_stage_b_report():
    evidence = mine_stage_b_report(Path("benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json"))

    kinds = {item.kind for item in evidence}
    assert "node_subgraph" in kinds
    assert any("select_face" in item.node_ids for item in evidence if item.kind == "node_subgraph")


def test_miner_extracts_node_usage_from_audit_jsonl():
    evidence = mine_audit_jsonl(Path("benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_node_audit.jsonl"))

    assert any(item.kind == "node_usage" and item.summary == "create_substrate" for item in evidence)
    assert any(item.kind == "node_subgraph" for item in evidence)


def test_proposer_generates_composite_node_proposal_from_repeated_subgraph():
    evidence = [
        NodeEvolutionEvidence(
            source="unit",
            kind="node_subgraph",
            summary="create_conductor_or_geometry_group -> select_face -> create_port",
            count=3,
            tasks=["L1_create_wave_port"],
            node_ids=["create_conductor_or_geometry_group", "select_face", "create_port"],
        )
    ]

    proposals = propose_node_evolution(evidence)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.recommended_action == EvolutionAction.ADD_NODE
    assert proposal.review_status == ReviewStatus.PROPOSED
    assert proposal.candidate_node_metadata["stability"] == "experimental"
    assert proposal.evidence[0].tasks == ["L1_create_wave_port"]


def test_build_evolution_report_keeps_evidence_and_proposals():
    evidence = mine_evolution_evidence([Path("benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json")])

    report = build_evolution_report(evidence, source_count=1)

    assert report.source_count == 1
    assert report.evidence
    assert report.proposals
    assert report.to_dict()["proposals"][0]["review_status"] == "proposed"


def test_evaluator_blocks_candidate_without_tests_or_gates():
    proposal = NodeEvolutionProposal(
        proposal_id="proposal-test",
        source="unit",
        problem_pattern="Failure pattern",
        affected_tasks=["task"],
        recommended_action=EvolutionAction.ADD_POSTCHECK,
        candidate_node_metadata={"node_id": "candidate", "description": "Candidate"},
        required_tests=["test_node_executor.py", "real_aedt_smoke_or_manual_gate", "benchmark_regression"],
        risk_level="high",
    )

    result = evaluate_proposal(
        proposal,
        available_tests=set(),
        workflow_validator_passed=False,
        real_aedt_smoke_passed=False,
        benchmark_regression_passed=False,
    )

    assert result.passed is False
    assert result.accepted_status == ReviewStatus.NEEDS_TESTS
    assert any("missing required tests" in blocker for blocker in result.blockers)
    assert "workflow validator did not pass" in result.blockers
    assert "real AEDT smoke or manual gate is required" in result.blockers
    assert "benchmark regression evidence is required" in result.blockers


def test_evaluator_allows_manual_gated_candidate_but_not_stable():
    proposal = NodeEvolutionProposal(
        proposal_id="proposal-test",
        source="unit",
        problem_pattern="Repeated subgraph",
        affected_tasks=["task"],
        recommended_action=EvolutionAction.ADD_NODE,
        candidate_node_metadata={"node_id": "candidate", "description": "Candidate"},
        required_tests=["test_node_catalog.py", "real_aedt_smoke_or_manual_gate"],
        risk_level="medium",
    )

    result = evaluate_proposal(
        proposal,
        available_tests={"test_node_catalog.py"},
        workflow_validator_passed=True,
        manual_gated=True,
    )

    assert result.passed is True
    assert result.accepted_status == ReviewStatus.MANUAL_GATED
    assert result.warnings == ["candidate requires manual AEDT validation before release"]
    assert can_transition(ReviewStatus.CANDIDATE, ReviewStatus.STABLE_APPROVED, human_approved=False) is False
    assert can_transition(ReviewStatus.CANDIDATE, ReviewStatus.STABLE_APPROVED, human_approved=True) is True
