from __future__ import annotations

from aedt_agent.v0.evolution.models import ReviewStatus


RELEASE_ORDER = [
    ReviewStatus.PROPOSED,
    ReviewStatus.NEEDS_TESTS,
    ReviewStatus.MANUAL_GATED,
    ReviewStatus.CANDIDATE,
    ReviewStatus.STABLE_APPROVED,
]


def can_transition(current: ReviewStatus, target: ReviewStatus, *, human_approved: bool = False) -> bool:
    if target == ReviewStatus.REJECTED:
        return True
    if target == ReviewStatus.STABLE_APPROVED:
        return current == ReviewStatus.CANDIDATE and human_approved
    try:
        return RELEASE_ORDER.index(target) == RELEASE_ORDER.index(current) + 1
    except ValueError:
        return False
