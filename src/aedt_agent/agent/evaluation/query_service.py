from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.evaluation.artifact_query import (
    query_sparameter_artifact,
    query_tdr_artifact,
)
from aedt_agent.agent.mission import EventType


class ArtifactQueryService:
    def __init__(self, store) -> None:
        self.store = store

    def query_sparameter(
        self,
        mission_id: str,
        artifact_ref: str,
        frequency_start_ghz: float,
        frequency_stop_ghz: float,
        *,
        max_points: int = 64,
        rl_target_db: float = -20.0,
    ) -> dict[str, Any]:
        artifact = self._registered_artifact(
            mission_id,
            artifact_ref,
        )
        result = query_sparameter_artifact(
            artifact.path,
            frequency_start_ghz,
            frequency_stop_ghz,
            max_points=max_points,
            rl_target_db=rl_target_db,
        )
        self._record(
            mission_id,
            artifact.artifact_id,
            "sparameter",
            {
                "frequency_start_ghz": frequency_start_ghz,
                "frequency_stop_ghz": frequency_stop_ghz,
            },
            result,
        )
        return result

    def query_tdr(
        self,
        mission_id: str,
        artifact_ref: str,
        time_start_ps: float,
        time_stop_ps: float,
        *,
        max_points: int = 64,
        target_ohm: float = 100.0,
    ) -> dict[str, Any]:
        artifact = self._registered_artifact(
            mission_id,
            artifact_ref,
        )
        result = query_tdr_artifact(
            artifact.path,
            time_start_ps,
            time_stop_ps,
            max_points=max_points,
            target_ohm=target_ohm,
        )
        self._record(
            mission_id,
            artifact.artifact_id,
            "tdr",
            {
                "time_start_ps": time_start_ps,
                "time_stop_ps": time_stop_ps,
            },
            result,
        )
        return result

    def _registered_artifact(
        self,
        mission_id: str,
        artifact_ref: str,
    ):
        requested = Path(artifact_ref).resolve()
        for artifact in self.store.list_artifact_manifests(
            mission_id
        ):
            if Path(artifact.path).resolve() != requested:
                continue
            digest = hashlib.sha256(
                requested.read_bytes()
            ).hexdigest()
            if digest != artifact.sha256:
                raise ValueError("registered artifact digest changed")
            return artifact
        raise ValueError("artifact is not registered for mission")

    def _record(
        self,
        mission_id: str,
        artifact_id: str,
        query_kind: str,
        query_range: dict[str, float],
        result: dict[str, Any],
    ) -> None:
        summary = dict(result["window_summary"])
        summary_digest = hashlib.sha256(
            json.dumps(
                summary,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.store.append_event(
            mission_id,
            EventType.ARTIFACT_QUERY_COMPLETED,
            {
                "artifact_id": artifact_id,
                "query_kind": query_kind,
                "query_range": query_range,
                "point_count": result["point_count"],
                "summary_digest": summary_digest,
            },
        )
