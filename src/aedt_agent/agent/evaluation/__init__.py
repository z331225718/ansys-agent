"""Deterministic evidence evaluation interfaces."""

from aedt_agent.agent.evaluation.artifact_query import (
    query_sparameter_artifact,
    query_tdr_artifact,
)
from aedt_agent.agent.evaluation.query_service import (
    ArtifactQueryService,
)
from aedt_agent.agent.evaluation.spectral import build_sparameter_evidence, query_sparameter_window

__all__ = [
    "ArtifactQueryService",
    "build_sparameter_evidence",
    "query_sparameter_artifact",
    "query_sparameter_window",
    "query_tdr_artifact",
]
