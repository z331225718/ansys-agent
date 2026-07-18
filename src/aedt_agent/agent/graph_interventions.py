from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GraphInterventionRequest:
    graph_run_id: str
    action: str
    node_id: str
    expected_event_cursor: int
    idempotency_key: str
    reason: str


class GraphInterventionService:
    def __init__(self, store: Any):
        self.store = store

    def apply(self, request: GraphInterventionRequest) -> dict[str, Any]:
        try:
            return self.store.intervene_graph(
                graph_run_id=request.graph_run_id,
                action=request.action,
                node_id=request.node_id,
                expected_event_cursor=request.expected_event_cursor,
                idempotency_key=request.idempotency_key,
                reason=request.reason,
            )
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).casefold() and "busy" not in str(exc).casefold():
                raise
            return {
                "ok": False,
                "idempotent_replay": False,
                "error": {
                    "code": "intervention_busy",
                    "message": "graph state is busy; intervention was not applied",
                    "details": {"graph_run_id": request.graph_run_id},
                },
            }


def intervene_graph(
    runtime_or_store: Any,
    *,
    graph_run_id: str,
    action: str,
    node_id: str,
    expected_event_cursor: int,
    idempotency_key: str,
    reason: str,
) -> dict[str, Any]:
    store = getattr(runtime_or_store, "store", runtime_or_store)
    return GraphInterventionService(store).apply(
        GraphInterventionRequest(
            graph_run_id=graph_run_id,
            action=action,
            node_id=node_id,
            expected_event_cursor=expected_event_cursor,
            idempotency_key=idempotency_key,
            reason=reason,
        )
    )
