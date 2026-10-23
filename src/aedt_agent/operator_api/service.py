from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from aedt_agent.agent.event_replay import replay_graph_run
from aedt_agent.agent.graph_runner import graph_status
from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path
from aedt_agent.agent.graph_visualizer import render_graph_live, render_graph_mermaid
from aedt_agent.agent.mission import ApprovalDecision
from aedt_agent.operator_api.contracts import OperatorSchemaCatalog
from aedt_agent.operator_api.contracts import OperatorContractError

READ_ONLY_OPERATIONS = (
    "system.handshake", "template.catalog", "system.preflight", "graph.status", "replay.delta",
    "graph.visualize", "artifact.query_bounded", "approval.get_pending", "approval.prepare_decision",
)
_TEMPLATES = (
    "brd_local_cut_build", "brd_channel_optimize", "brd_before_after_compare",
    "brd_real_solve_evidence", "brd_multi_channel_demo",
)


class OperatorService:
    def __init__(self, runtime: Any, *, schema_catalog: OperatorSchemaCatalog | None = None):
        self.runtime = runtime
        self.schema_catalog = schema_catalog or OperatorSchemaCatalog.load()

    def handshake(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "schema_bundle_digest": self.schema_catalog.bundle_digest, "runtime_pid": os.getpid(),
            "runtime_argv_digest": _digest(list(sys.orig_argv)),
            "capabilities": list(READ_ONLY_OPERATIONS),
            "limits": {"max_protocol_frame_bytes": 1_048_576, "max_tool_result_bytes": 65_536},
        }

    def template_catalog(self, capability_filter: str | None = None, limit: int | None = None) -> dict[str, Any]:
        templates: list[dict[str, Any]] = []
        for template_id in _TEMPLATES:
            template = load_graph_template(resolve_template_path(template_id))
            capabilities = sorted({node.capability for node in template.nodes if node.capability})
            if capability_filter and capability_filter not in capabilities:
                continue
            templates.append({
                "template_id": template.template_id, "version": template.version,
                "description": f"Allowlisted {template.template_id} graph template.",
                "capabilities": capabilities, "digest": _digest(template.to_json_dict()),
            })
        capped = templates[:limit or 100]
        return {"templates": capped, "truncated": len(capped) < len(templates)}

    def system_preflight(self, template_id: str, execution_profile: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if template_id not in _TEMPLATES:
            raise KeyError(f"template not allowlisted: {template_id}")
        shape = _input_shape(inputs)
        snapshot = {"template_id": template_id, "execution_profile": execution_profile, "inputs": shape}
        return {"data": {
            "preflight_snapshot_id": _digest(snapshot), "safe_to_start": False,
            "requires_trusted_controller": True, "snapshot": snapshot,
        }}

    def graph_status(self, graph_run_id: str) -> dict[str, Any]:
        report = graph_status(self.runtime, graph_run_id)
        graph = self._require_graph(graph_run_id)
        nodes = [
            {"node_run_id": item["node_run_id"], "node_id": item["node_id"], "node_role": item["node_role"],
             "node_kind": item["node_kind"], "sequence": item["sequence"], "status": item["status"],
             "edge_decision": item.get("edge_decision")}
            for item in report["node_runs"][:100]
        ]
        supervision = report["supervision"]
        resource = self._graph_resource(graph_run_id, report["mission_id"], str(supervision["event_cursor"]), graph)
        return {"resource": resource, "data": {
            "status": report["status"], "summary": supervision["summary"],
            "blocking_node_id": supervision["blocking_node_id"], "reason": supervision["reason"],
            "recommended_action": supervision["recommended_action"], "nodes": nodes,
            "truncated_nodes": len(nodes) < len(report["node_runs"]),
        }}

    def replay_delta(self, graph_run_id: str, after_cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        replay = replay_graph_run(self.runtime, graph_run_id)
        try:
            cursor = int(after_cursor or 0)
        except (TypeError, ValueError) as error:
            raise OperatorContractError("invalid_cursor", "after_cursor must be a decimal event cursor") from error
        high_water_mark = int(replay["event_cursor"])
        if cursor < 0 or cursor > high_water_mark:
            raise OperatorContractError("invalid_cursor", "after_cursor is outside the graph replay range")
        events = [event for event in replay["events"] if int(event["sequence"]) > cursor]
        capped = events[:limit or 100]
        event_data = [{"sequence": int(event["sequence"]), "event_type": str(event["event_type"]),
                       "scope": str(event.get("scope", "graph")), "created_at": str(event["created_at"])} for event in capped]
        graph = self._require_graph(graph_run_id)
        return {"resource": self._graph_resource(graph_run_id, graph.mission_id, str(replay["event_cursor"]), graph), "data": {
            "events": event_data, "next_cursor": str(event_data[-1]["sequence"] if event_data else cursor),
            "truncated": len(capped) < len(events),
        }}

    def graph_visualize(self, graph_run_id: str, format: str) -> dict[str, Any]:
        graph = self._require_graph(graph_run_id)
        if format == "mermaid":
            content = render_graph_mermaid(graph.template_snapshot, [run.to_json_dict() for run in self.runtime.store.list_node_runs(graph_run_id)],
                                           [handoff.to_json_dict() for handoff in self.runtime.store.list_graph_handoffs(graph_run_id)])
        else:
            content = render_graph_live(graph.template_snapshot, [run.to_json_dict() for run in self.runtime.store.list_node_runs(graph_run_id)],
                                        [handoff.to_json_dict() for handoff in self.runtime.store.list_graph_handoffs(graph_run_id)], width=80)
        return {"resource": self._graph_resource(graph_run_id, graph.mission_id, "0", graph), "data": {"format": format, "content": content[:60_000]}}

    def artifact_query_bounded(self, mission_id: str, query: str, limit: int) -> dict[str, Any]:
        matches = []
        needle = query.lower()
        for artifact in self.runtime.store.list_artifact_manifests(mission_id):
            if needle not in " ".join((artifact.artifact_id, artifact.kind, artifact.producer_kind)).lower():
                continue
            matches.append({"artifact_id": artifact.artifact_id, "kind": artifact.kind, "producer_kind": artifact.producer_kind,
                            "producer_id": artifact.producer_id, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes,
                            "created_at": artifact.created_at})
        capped = matches[:limit]
        return {"data": {"artifacts": capped, "truncated": len(capped) < len(matches)}}

    def approval_get_pending(self, mission_id: str | None = None, graph_run_id: str | None = None) -> dict[str, Any]:
        if graph_run_id is not None:
            graph_mission_id = self._require_graph(graph_run_id).mission_id
            if mission_id is not None and mission_id != graph_mission_id:
                raise OperatorContractError("invalid_argument", "mission_id does not own graph_run_id")
            mission_id = graph_mission_id
        if mission_id is None:
            raise ValueError("mission_id or graph_run_id is required")
        approvals = self.runtime.store.list_approvals(mission_id, decision=ApprovalDecision.PENDING)
        return {"data": {"approvals": [self._approval_summary(item) for item in approvals[:100]], "truncated": len(approvals) > 100}}

    def approval_prepare_decision(self, approval_id: str, decision_intent: str) -> dict[str, Any]:
        approval = self.runtime.store.get_approval(approval_id)
        return {"data": {"approval": self._approval_summary(approval), "decision_intent": decision_intent, "requires_trusted_controller": True}}

    def _require_graph(self, graph_run_id: str) -> Any:
        graph = self.runtime.store.get_graph_run(graph_run_id)
        if graph is None:
            raise KeyError(f"graph run not found: {graph_run_id}")
        return graph

    def _graph_resource(self, graph_run_id: str, mission_id: str, event_cursor: str, graph: Any) -> dict[str, str]:
        observable = {"status": graph.status.value, "current_node_id": graph.current_node_id, "step_count": graph.step_count, "event_cursor": event_cursor}
        return {"mission_id": mission_id, "graph_run_id": graph_run_id, "event_cursor": event_cursor, "state_digest": _digest(observable)}

    @staticmethod
    def _approval_summary(approval: Any) -> dict[str, Any]:
        return {"approval_id": approval.approval_id, "mission_id": approval.mission_id, "reason": approval.reason[:500],
                "options": [{"option_id": str(option.get("option_id", option.get("id", "option")))[:200],
                             "label": str(option.get("label", option.get("name", "option")))[:500]} for option in approval.options[:30]],
                "created_at": approval.created_at}


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _input_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key)[:100]: _input_shape(child) for key, child in list(value.items())[:100]}
    if isinstance(value, list):
        return {"type": "array", "items": _input_shape(value[0]) if value else "unknown", "length": len(value)}
    return type(value).__name__
