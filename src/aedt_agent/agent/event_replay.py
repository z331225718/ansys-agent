from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


_SCOPES = (
    "graph",
    "node",
    "handoff",
    "job",
    "attempt",
    "artifact",
    "evidence",
    "approval",
)

_SINGLE_ID_FIELDS = {
    "graph_run_id": "graph",
    "node_run_id": "node",
    "handoff_id": "handoff",
    "job_id": "job",
    "attempt_id": "attempt",
    "artifact_id": "artifact",
    "evidence_package_id": "evidence",
    "approval_id": "approval",
}

_PLURAL_ID_FIELDS = {
    "graph_run_ids": "graph",
    "node_run_ids": "node",
    "handoff_ids": "handoff",
    "job_ids": "job",
    "attempt_ids": "attempt",
    "artifact_ids": "artifact",
    "artifact_refs": "artifact",
    "evidence_package_ids": "evidence",
    "approval_ids": "approval",
}

_SUMMARY_FIELDS = (
    "graph_run_id",
    "node_run_id",
    "node_id",
    "handoff_id",
    "job_id",
    "attempt_id",
    "artifact_id",
    "evidence_package_id",
    "approval_id",
    "status",
    "decision",
    "code",
    "error_class",
)


@dataclass
class _References:
    values: dict[str, set[str]] = field(
        default_factory=lambda: {scope: set() for scope in _SCOPES}
    )

    def add(self, scope: str, value: Any) -> None:
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return
        text = str(value).strip()
        if text:
            self.values[scope].add(text)

    def merge(self, other: "_References") -> bool:
        changed = False
        for scope in _SCOPES:
            before = len(self.values[scope])
            self.values[scope].update(other.values[scope])
            changed = changed or len(self.values[scope]) != before
        return changed

    def intersects(self, other: "_References") -> bool:
        return any(self.values[scope] & other.values[scope] for scope in _SCOPES)


class EventReplayService:
    """Rebuild a graph-run-local event stream from persisted relationships."""

    def __init__(self, store: Any):
        self.store = store

    def replay(self, graph_run_id: str) -> dict[str, Any]:
        graph_run = self.store.get_graph_run(graph_run_id)
        if graph_run is None:
            raise KeyError(f"graph run not found: {graph_run_id}")

        node_runs = self.store.list_node_runs(graph_run_id)
        handoffs = self.store.list_graph_handoffs(graph_run_id)
        job_ids = set(self.store.list_graph_bound_job_ids(graph_run_id))
        jobs = [self.store.get_job(job_id) for job_id in sorted(job_ids)]
        attempts = [
            attempt
            for job_id in sorted(job_ids)
            for attempt in self.store.list_job_attempts(job_id)
        ]

        references = _References()
        references.add("graph", graph_run_id)
        for node_run in node_runs:
            references.add("node", node_run.node_run_id)
        for handoff in handoffs:
            references.add("handoff", handoff.handoff_id)
        for job_id in job_ids:
            references.add("job", job_id)
        for attempt in attempts:
            references.add("attempt", attempt.attempt_id)

        association_documents: list[Any] = [
            graph_run.initial_payload,
            graph_run.error,
            *(node_run.to_json_dict() for node_run in node_runs),
            *(handoff.to_json_dict() for handoff in handoffs),
            *(job.to_json_dict() for job in jobs),
            *(attempt.to_json_dict() for attempt in attempts),
        ]
        for document in association_documents:
            references.merge(_collect_references(document))

        producer_ids = {
            graph_run_id,
            *(node_run.node_run_id for node_run in node_runs),
            *(handoff.handoff_id for handoff in handoffs),
            *job_ids,
            *(attempt.attempt_id for attempt in attempts),
        }
        artifacts = {
            artifact.artifact_id: artifact
            for artifact in self.store.list_artifact_manifests(graph_run.mission_id)
        }
        evidence_packages = {
            evidence.evidence_package_id: evidence
            for evidence in self.store.list_evidence_packages(graph_run.mission_id)
        }
        approvals = {
            approval.approval_id: approval
            for approval in self.store.list_approvals(graph_run.mission_id)
        }
        related_artifact_ids: set[str] = set()
        related_evidence_ids: set[str] = set()
        related_approval_ids: set[str] = set()

        changed = True
        while changed:
            changed = False
            for evidence_id, evidence in evidence_packages.items():
                if evidence_id in related_evidence_ids:
                    continue
                candidate = _collect_references(evidence.to_json_dict())
                if (
                    evidence_id in references.values["evidence"]
                    or evidence.producer_id in producer_ids
                    or candidate.intersects(references)
                ):
                    related_evidence_ids.add(evidence_id)
                    producer_ids.add(evidence_id)
                    references.merge(candidate)
                    changed = True

            for artifact_id, artifact in artifacts.items():
                if artifact_id in related_artifact_ids:
                    continue
                candidate = _collect_references(artifact.to_json_dict())
                if (
                    artifact_id in references.values["artifact"]
                    or artifact.producer_id in producer_ids
                    or candidate.intersects(references)
                ):
                    related_artifact_ids.add(artifact_id)
                    producer_ids.add(artifact_id)
                    references.merge(candidate)
                    changed = True

            for approval_id, approval in approvals.items():
                if approval_id in related_approval_ids:
                    continue
                candidate = _collect_references(approval.to_json_dict())
                if (
                    approval_id in references.values["approval"]
                    or candidate.intersects(references)
                ):
                    related_approval_ids.add(approval_id)
                    references.merge(candidate)
                    changed = True

        references.values["artifact"].update(related_artifact_ids)
        references.values["evidence"].update(related_evidence_ids)
        references.values["approval"].update(related_approval_ids)

        replayed_events: list[dict[str, Any]] = []
        for event in self.store.list_events(graph_run.mission_id):
            scope = _event_scope(event.event_type.value, event.payload, references, graph_run_id)
            if scope is None:
                continue
            item = event.to_json_dict()
            item["scope"] = scope
            replayed_events.append(item)

        replayed_events.sort(key=lambda item: int(item["sequence"]))
        entity_counts = {
            "graph": 1,
            "node": len(node_runs),
            "handoff": len(handoffs),
            "job": len(job_ids),
            "attempt": len(attempts),
            "artifact": len(related_artifact_ids),
            "evidence": len(related_evidence_ids),
            "approval": len(related_approval_ids),
            "events": len(replayed_events),
        }
        return {
            "graph_run_id": graph_run_id,
            "mission_id": graph_run.mission_id,
            "status": graph_run.status.value,
            "event_cursor": (
                int(replayed_events[-1]["sequence"]) if replayed_events else 0
            ),
            "entity_counts": entity_counts,
            "events": replayed_events,
        }


def replay_graph_run(runtime_or_store: Any, graph_run_id: str) -> dict[str, Any]:
    store = getattr(runtime_or_store, "store", runtime_or_store)
    return EventReplayService(store).replay(graph_run_id)


def render_replay_text(replay: Mapping[str, Any]) -> str:
    counts = replay.get("entity_counts", {})
    count_text = " ".join(
        f"{scope}={counts.get(scope, 0)}" for scope in (*_SCOPES, "events")
    )
    lines = [
        (
            f"graph_run_id={replay.get('graph_run_id', '')} "
            f"mission_id={replay.get('mission_id', '')} "
            f"status={replay.get('status', '')} "
            f"event_cursor={replay.get('event_cursor', 0)}"
        ),
        f"entities {count_text}",
    ]
    events = replay.get("events", [])
    if not events:
        lines.append("events (none)")
        return "\n".join(lines)

    for event in events:
        payload = event.get("payload", {})
        details = _render_event_details(payload)
        suffix = f" {details}" if details else ""
        lines.append(
            f"{int(event['sequence']):06d} {event.get('created_at', '')} "
            f"scope={event.get('scope', '')} {event.get('event_type', '')}{suffix}"
        )
    return "\n".join(lines)


def diagnose_graph_supervision(
    graph_run: Any,
    node_runs: list[Any],
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    status = _enum_value(graph_run.status)
    blocking_node_id: str | None = None
    reason = f"graph_{status}"
    recommended_action = "advance_or_wait"
    summary = f"graph is {status}"

    if status == "waiting_approval":
        waiting = [run for run in node_runs if _enum_value(run.status) == "waiting_approval"]
        waiting_run = waiting[-1] if waiting else None
        blocking_node_id = (
            waiting_run.node_id if waiting_run is not None else graph_run.current_node_id
        )
        approval_ids = _References()
        if waiting_run is not None:
            approval_ids.merge(_collect_references(waiting_run.input_payload))
            approval_ids.merge(_collect_references(waiting_run.output_payload))
            approval_ids.merge(_collect_references(waiting_run.error))
        if not approval_ids.values["approval"]:
            for event in replay.get("events", []):
                if event.get("scope") == "approval":
                    approval_ids.merge(_collect_references(event.get("payload", {})))
        approval_id = _last_sorted(approval_ids.values["approval"])
        reason = (
            f"waiting_approval:{approval_id}" if approval_id else "waiting_approval"
        )
        recommended_action = "resolve_approval"
        summary = "waiting for approval"
        if approval_id:
            summary += f" {approval_id}"
        if blocking_node_id:
            summary += f" at node {blocking_node_id}"
    elif status == "failed":
        failed = [run for run in node_runs if _enum_value(run.status) == "failed"]
        failed_run = failed[-1] if failed else None
        blocking_node_id = (
            failed_run.node_id if failed_run is not None else graph_run.current_node_id
        )
        error_code = _error_code(graph_run.error)
        if error_code is None and failed_run is not None:
            error_code = _error_code(failed_run.error)
        reason = error_code or "graph_failed"
        recommended_action = "inspect_or_takeover"
        summary = "graph failed"
        if blocking_node_id:
            summary += f" at node {blocking_node_id}"
        summary += f": {reason}"
    elif status == "succeeded":
        reason = "graph_succeeded"
        recommended_action = "none"
        summary = "graph succeeded"
    elif status == "canceled":
        reason = _error_code(graph_run.error) or "graph_canceled"
        recommended_action = "none"
        summary = f"graph canceled: {reason}"

    return {
        "summary": summary,
        "blocking_node_id": blocking_node_id,
        "reason": reason,
        "recommended_action": recommended_action,
        "event_cursor": int(replay.get("event_cursor", 0)),
        "counts": dict(replay.get("entity_counts", {})),
    }


def _collect_references(value: Any) -> _References:
    references = _References()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if key in _SINGLE_ID_FIELDS:
                    references.add(_SINGLE_ID_FIELDS[key], child)
                elif key in _PLURAL_ID_FIELDS:
                    scope = _PLURAL_ID_FIELDS[key]
                    if isinstance(child, (list, tuple, set)):
                        for member in child:
                            references.add(scope, member)
                    else:
                        references.add(scope, child)
                visit(child)
        elif isinstance(item, (list, tuple, set)):
            for child in item:
                visit(child)

    visit(value)
    return references


def _event_scope(
    event_type: str,
    payload: Mapping[str, Any],
    related: _References,
    graph_run_id: str,
) -> str | None:
    event_refs = _collect_references(payload)
    graph_refs = event_refs.values["graph"]
    if graph_refs and graph_run_id not in graph_refs:
        return None
    if graph_run_id not in graph_refs and not event_refs.intersects(related):
        return None

    if event_type.startswith("job_attempt_"):
        return "attempt"
    if event_type.startswith("graph_handoff_"):
        return "handoff"
    if event_type == "graph_node_job_bound" or event_type.startswith("job_"):
        return "job"
    if event_type.startswith("artifact_"):
        return "artifact"
    if event_type.startswith("evidence_"):
        return "evidence"
    if event_type.startswith("approval_"):
        return "approval"
    if event_type.startswith("node_run_"):
        return "node"
    if event_type.startswith("graph_run_") or event_type == "graph_step_advanced":
        return "graph"

    for scope in ("attempt", "handoff", "job", "artifact", "evidence", "approval", "node", "graph"):
        if event_refs.values[scope] & related.values[scope]:
            return scope
    return "graph" if graph_run_id in graph_refs else None


def _render_event_details(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    parts: list[str] = []
    for field_name in _SUMMARY_FIELDS:
        values = sorted(_values_for_key(payload, field_name))
        if not values:
            continue
        value = values[0]
        if len(values) > 1:
            value += f"(+{len(values) - 1})"
        parts.append(f"{field_name}={value}")
    return " ".join(parts)


def _values_for_key(value: Any, target: str) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == target and not isinstance(child, (dict, list, tuple, set)):
                text = str(child).strip()
                if text:
                    values.add(text)
            values.update(_values_for_key(child, target))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            values.update(_values_for_key(child, target))
    return values


def _error_code(error: Any) -> str | None:
    if not isinstance(error, Mapping):
        return None
    for key in ("code", "error_class", "status"):
        value = error.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _last_sorted(values: set[str]) -> str | None:
    return sorted(values)[-1] if values else None
