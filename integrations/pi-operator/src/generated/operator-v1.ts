/* This file is generated from schemas/operator/v1. Do not edit manually. */
import { Type, type TSchema } from "typebox";

export type OperatorEnvelope = { "schema_version": "1.0"; "type": "request"; "request_id": string; "trace_id": string; "operation": "system.handshake" | "template.catalog" | "system.preflight" | "graph.status" | "replay.delta" | "graph.visualize" | "artifact.query_bounded" | "approval.get_pending" | "approval.prepare_decision" | "mission.start" | "graph.advance" | "intervention.apply" | "graph.stop.prepare"; "sent_at": string; "caller"?: { "pi_session_id"?: string; "pi_tool_call_id"?: string; }; "arguments": { [key: string]: unknown; }; } | { "schema_version": "1.0"; "type": "response"; "request_id": string; "trace_id": string; "operation": "system.handshake" | "template.catalog" | "system.preflight" | "graph.status" | "replay.delta" | "graph.visualize" | "artifact.query_bounded" | "approval.get_pending" | "approval.prepare_decision" | "mission.start" | "graph.advance" | "intervention.apply" | "graph.stop.prepare"; "ok": boolean; "completed_at": string; "resource": { "mission_id"?: string; "graph_run_id"?: string; "event_cursor"?: string; "state_digest"?: string; } | null; "data": unknown; "error": { "code": "invalid_argument" | "invalid_cursor" | "not_found" | "unsupported_operation" | "protocol_error" | "outcome_unknown" | "unsafe_profile" | "approval_required" | "stale_cursor" | "idempotency_conflict" | "busy" | "timeout" | "transient" | "terminal_failed" | "internal"; "message": string; "retryable": boolean; "details": { [key: string]: unknown; }; } | null; } | { "schema_version": "1.0"; "type": "event"; "request_id": string; "trace_id": string; "operation": "system.handshake" | "template.catalog" | "system.preflight" | "graph.status" | "replay.delta" | "graph.visualize" | "artifact.query_bounded" | "approval.get_pending" | "approval.prepare_decision" | "mission.start" | "graph.advance" | "intervention.apply" | "graph.stop.prepare"; "event": "progress"; "sequence": number; "occurred_at": string; "data": { [key: string]: unknown; }; };

export type ApprovalGetPendingArguments = { "mission_id"?: string; "graph_run_id"?: string; };

export type ApprovalGetPendingSuccessData = { "approvals": Array<{ "approval_id": string; "mission_id": string; "reason": string; "options": Array<{ "option_id": string; "label": string; }>; "created_at": string; }>; "truncated": boolean; };

export type ApprovalPrepareDecisionArguments = { "approval_id": string; "decision_intent": "approve" | "reject"; };

export type ApprovalPrepareDecisionSuccessData = { "approval": { "approval_id": string; "mission_id": string; "reason": string; "options": Array<{ "option_id": string; "label": string; }>; "created_at": string; }; "decision_intent": "approve" | "reject"; "requires_trusted_controller": true; };

export type ArtifactQueryBoundedArguments = { "mission_id": string; "query": string; "limit": number; };

export type ArtifactQueryBoundedSuccessData = { "artifacts": Array<{ "artifact_id": string; "kind": string; "producer_kind": string; "producer_id": string; "sha256": string; "size_bytes": number; "created_at": string; }>; "truncated": boolean; };

export type GraphAdvanceArguments = { "graph_run_id": string; "write_guard": { "idempotency_key": string; "expected_event_cursor": string; "expected_state_digest": string; "reason": string; }; };

export type GraphAdvanceSuccessData = unknown;

export type GraphStatusArguments = { "graph_run_id": string; };

export type GraphStatusSuccessData = { "status": string; "summary": string; "blocking_node_id": string | null; "reason": string; "recommended_action": string; "truncated_nodes": boolean; "nodes": Array<{ "node_run_id": string; "node_id": string; "node_role": string; "node_kind": string; "sequence": number; "status": string; "edge_decision": string | null; }>; };

export type GraphStopPrepareArguments = { "graph_run_id": string; "reason": string; "write_guard": { "idempotency_key": string; "expected_event_cursor": string; "expected_state_digest": string; "reason": string; }; };

export type GraphStopPrepareSuccessData = unknown;

export type GraphVisualizeArguments = { "graph_run_id": string; "format": "text" | "mermaid"; };

export type GraphVisualizeSuccessData = { "format": "text" | "mermaid"; "content": string; };

export type InterventionApplyArguments = { "graph_run_id": string; "intervention": { [key: string]: unknown; }; "write_guard": { "idempotency_key": string; "expected_event_cursor": string; "expected_state_digest": string; "reason": string; }; };

export type InterventionApplySuccessData = unknown;

export type MissionStartArguments = { "preflight_snapshot_id": string; "initial_payload": { [key: string]: unknown; }; "write_guard": { "idempotency_key": string; "expected_event_cursor": string; "expected_state_digest": string; "reason": string; }; };

export type MissionStartSuccessData = unknown;

export type ReplayDeltaArguments = { "graph_run_id": string; "after_cursor"?: string | null; "limit"?: number; };

export type ReplayDeltaSuccessData = { "events": Array<{ "sequence": number; "event_type": string; "scope": string; "created_at": string; }>; "next_cursor": string; "truncated": boolean; };

export type SystemHandshakeArguments = { "client": { "name": string; "version": string; "schema_version": "1.0"; "schema_bundle_digest"?: string; }; "requested_capabilities"?: Array<string>; };

export type SystemHandshakeSuccessData = { "schema_version": "1.0"; "schema_bundle_digest": string; "runtime_pid": number; "runtime_argv_digest": string; "capabilities": Array<string>; "limits": { "max_protocol_frame_bytes": number; "max_tool_result_bytes": number; }; };

export type SystemPreflightArguments = { "template_id": string; "execution_profile": string; "inputs": { [key: string]: unknown; }; };

export type SystemPreflightSuccessData = { "preflight_snapshot_id": string; "safe_to_start": false; "requires_trusted_controller": true; "snapshot": { "template_id": string; "execution_profile": string; "inputs": { [key: string]: unknown; }; }; };

export type TemplateCatalogArguments = { "capability_filter"?: string; "limit"?: number; };

export type TemplateCatalogSuccessData = { "templates": Array<{ "template_id": string; "version": number; "description": string; "capabilities": Array<string>; "digest": string; }>; "truncated": boolean; };

export interface OperationArgumentsByName {
  "approval.get_pending": ApprovalGetPendingArguments;
  "approval.prepare_decision": ApprovalPrepareDecisionArguments;
  "artifact.query_bounded": ArtifactQueryBoundedArguments;
  "graph.advance": GraphAdvanceArguments;
  "graph.status": GraphStatusArguments;
  "graph.stop.prepare": GraphStopPrepareArguments;
  "graph.visualize": GraphVisualizeArguments;
  "intervention.apply": InterventionApplyArguments;
  "mission.start": MissionStartArguments;
  "replay.delta": ReplayDeltaArguments;
  "system.handshake": SystemHandshakeArguments;
  "system.preflight": SystemPreflightArguments;
  "template.catalog": TemplateCatalogArguments;
}

export type OperatorOperation = keyof OperationArgumentsByName;
export type ArgumentsFor<T extends OperatorOperation> = OperationArgumentsByName[T];

export interface OperationSuccessDataByName {
  "approval.get_pending": ApprovalGetPendingSuccessData;
  "approval.prepare_decision": ApprovalPrepareDecisionSuccessData;
  "artifact.query_bounded": ArtifactQueryBoundedSuccessData;
  "graph.advance": GraphAdvanceSuccessData;
  "graph.status": GraphStatusSuccessData;
  "graph.stop.prepare": GraphStopPrepareSuccessData;
  "graph.visualize": GraphVisualizeSuccessData;
  "intervention.apply": InterventionApplySuccessData;
  "mission.start": MissionStartSuccessData;
  "replay.delta": ReplayDeltaSuccessData;
  "system.handshake": SystemHandshakeSuccessData;
  "system.preflight": SystemPreflightSuccessData;
  "template.catalog": TemplateCatalogSuccessData;
}

export type SuccessDataFor<T extends OperatorOperation> = OperationSuccessDataByName[T];

const modelToolSchemas: Record<OperatorOperation, Record<string, unknown>> = {
  "approval.get_pending": {"type":"object","additionalProperties":false,"properties":{"mission_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"graph_run_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"}},"anyOf":[{"properties":{"mission_id":{}},"required":["mission_id"]},{"properties":{"graph_run_id":{}},"required":["graph_run_id"]}]},
  "approval.prepare_decision": {"type":"object","additionalProperties":false,"required":["approval_id","decision_intent"],"properties":{"approval_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"decision_intent":{"enum":["approve","reject"]}}},
  "artifact.query_bounded": {"type":"object","additionalProperties":false,"required":["mission_id","query","limit"],"properties":{"mission_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":100}}},
  "graph.advance": {"type":"object","additionalProperties":false,"required":["graph_run_id","write_guard"],"properties":{"graph_run_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"write_guard":{"type":"object","additionalProperties":false,"required":["idempotency_key","expected_event_cursor","expected_state_digest","reason"],"properties":{"idempotency_key":{"type":"string","minLength":16,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"expected_event_cursor":{"type":"string","minLength":1,"maxLength":200},"expected_state_digest":{"type":"string","pattern":"^sha256:[0-9a-f]{64}$"},"reason":{"type":"string","minLength":1,"maxLength":500}}}}},
  "graph.status": {"type":"object","additionalProperties":false,"required":["graph_run_id"],"properties":{"graph_run_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"}}},
  "graph.stop.prepare": {"type":"object","additionalProperties":false,"required":["graph_run_id","reason","write_guard"],"properties":{"graph_run_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"reason":{"type":"string","minLength":1,"maxLength":500},"write_guard":{"type":"object","additionalProperties":false,"required":["idempotency_key","expected_event_cursor","expected_state_digest","reason"],"properties":{"idempotency_key":{"type":"string","minLength":16,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"expected_event_cursor":{"type":"string","minLength":1,"maxLength":200},"expected_state_digest":{"type":"string","pattern":"^sha256:[0-9a-f]{64}$"},"reason":{"type":"string","minLength":1,"maxLength":500}}}}},
  "graph.visualize": {"type":"object","additionalProperties":false,"required":["graph_run_id","format"],"properties":{"graph_run_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"format":{"enum":["text","mermaid"]}}},
  "intervention.apply": {"type":"object","additionalProperties":false,"required":["graph_run_id","intervention","write_guard"],"properties":{"graph_run_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"intervention":{"type":"object","minProperties":1,"maxProperties":30},"write_guard":{"type":"object","additionalProperties":false,"required":["idempotency_key","expected_event_cursor","expected_state_digest","reason"],"properties":{"idempotency_key":{"type":"string","minLength":16,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"expected_event_cursor":{"type":"string","minLength":1,"maxLength":200},"expected_state_digest":{"type":"string","pattern":"^sha256:[0-9a-f]{64}$"},"reason":{"type":"string","minLength":1,"maxLength":500}}}}},
  "mission.start": {"type":"object","additionalProperties":false,"required":["preflight_snapshot_id","initial_payload","write_guard"],"properties":{"preflight_snapshot_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"initial_payload":{"type":"object","maxProperties":100},"write_guard":{"type":"object","additionalProperties":false,"required":["idempotency_key","expected_event_cursor","expected_state_digest","reason"],"properties":{"idempotency_key":{"type":"string","minLength":16,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"expected_event_cursor":{"type":"string","minLength":1,"maxLength":200},"expected_state_digest":{"type":"string","pattern":"^sha256:[0-9a-f]{64}$"},"reason":{"type":"string","minLength":1,"maxLength":500}}}}},
  "replay.delta": {"type":"object","additionalProperties":false,"required":["graph_run_id"],"properties":{"graph_run_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"after_cursor":{"anyOf":[{"type":"string","pattern":"^[0-9]+$","maxLength":200},{"type":"null"}]},"limit":{"type":"integer","minimum":1,"maximum":100}}},
  "system.handshake": {"type":"object","additionalProperties":false,"properties":{}},
  "system.preflight": {"type":"object","additionalProperties":false,"required":["template_id","execution_profile","inputs"],"properties":{"template_id":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"execution_profile":{"type":"string","minLength":1,"maxLength":200,"pattern":"^[A-Za-z0-9._:-]+$"},"inputs":{"type":"object","maxProperties":100}}},
  "template.catalog": {"type":"object","additionalProperties":false,"properties":{"capability_filter":{"type":"string","minLength":1,"maxLength":100},"limit":{"type":"integer","minimum":1,"maximum":100}}},
};

export function modelToolSchemaFor<T extends OperatorOperation>(operation: T): TSchema {
  return Type.Unsafe<ArgumentsFor<T>>(structuredClone(modelToolSchemas[operation]));
}

