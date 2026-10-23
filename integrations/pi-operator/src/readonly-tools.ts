import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { TSchema } from "typebox";

import { BridgePipeClient } from "./bridge/pipe-client.js";
import { BridgeClientError } from "./bridge/errors.js";
import { modelToolSchemaFor } from "./generated/operator-v1.js";

const MAX_TOOL_TEXT_BYTES = 64 * 1024;
const HOST_IDENTITY = { name: "pi-agent-host", version: "0.1.0", schema_version: "1.0" } as const;

export const READ_ONLY_OPERATOR_SYSTEM_PROMPT = [
  "You are an AEDT engineering observer operating in read-only shadow mode.",
  "Use only the supplied operator tools to inspect authoritative state.",
  "Do not claim that you started, advanced, edited, stopped, approved, or solved anything.",
  "Treat tool output as bounded metadata: never request or reconstruct raw simulation arrays, file contents, secrets, or approval tokens.",
  "When a graph is waiting for approval, explain the pending decision and direct the user to the trusted controller.",
].join(" ");

export function createReadonlyOperatorTools(client: BridgePipeClient, allowedOperations?: ReadonlySet<string>, sessionId?: string): AgentTool[] {
  const tools = [
    tool("operator_handshake", "Read the bridge protocol capabilities and schema identity.", modelToolSchemaFor("system.handshake"), client, "system.handshake", () => ({ client: HOST_IDENTITY }), sessionId),
    tool("operator_template_catalog", "List allowlisted graph templates and their bounded metadata.", modelToolSchemaFor("template.catalog"), client, "template.catalog", undefined, sessionId),
    tool("operator_preflight", "Create a read-only preflight snapshot. This never starts a mission.", modelToolSchemaFor("system.preflight"), client, "system.preflight", undefined, sessionId),
    tool("operator_graph_status", "Read bounded authoritative status for one graph run.", modelToolSchemaFor("graph.status"), client, "graph.status", undefined, sessionId),
    tool("operator_replay_delta", "Read a bounded event delta after an optional cursor.", modelToolSchemaFor("replay.delta"), client, "replay.delta", undefined, sessionId),
    tool("operator_graph_visualize", "Read a bounded text or Mermaid graph visualization.", modelToolSchemaFor("graph.visualize"), client, "graph.visualize", undefined, sessionId),
    tool("operator_artifact_query", "Find artifact metadata only; it never returns paths or file contents.", modelToolSchemaFor("artifact.query_bounded"), client, "artifact.query_bounded", undefined, sessionId),
    tool("operator_pending_approvals", "Read pending approval summaries without submitting any decision.", modelToolSchemaFor("approval.get_pending"), client, "approval.get_pending", undefined, sessionId),
    tool("operator_prepare_approval", "Read a decision preview. It never submits an approval decision.", modelToolSchemaFor("approval.prepare_decision"), client, "approval.prepare_decision", undefined, sessionId),
  ];
  return allowedOperations ? tools.filter((candidate) => allowedOperations.has(operationForTool(candidate.name))) : tools;
}

const TOOL_OPERATIONS: Record<string, string> = {
  operator_handshake: "system.handshake", operator_template_catalog: "template.catalog", operator_preflight: "system.preflight",
  operator_graph_status: "graph.status", operator_replay_delta: "replay.delta", operator_graph_visualize: "graph.visualize",
  operator_artifact_query: "artifact.query_bounded", operator_pending_approvals: "approval.get_pending",
  operator_prepare_approval: "approval.prepare_decision",
};

function operationForTool(name: string): string {
  const operation = TOOL_OPERATIONS[name];
  if (!operation) throw new Error(`operator tool has no canonical operation mapping: ${name}`);
  return operation;
}

function tool(
  name: string,
  description: string,
  parameters: TSchema,
  client: BridgePipeClient,
  operation: string,
  argumentMapper: (params: Record<string, unknown>) => Record<string, unknown> = (params) => params,
  sessionId?: string,
): AgentTool<TSchema> {
  return {
    name,
    label: name,
    description,
    parameters,
    executionMode: "parallel",
    async execute(_toolCallId, params, signal) {
      if (signal?.aborted) throw new Error("operator observation aborted before dispatch");
      assertModelSafeArguments(params);
      let response: Record<string, unknown>;
      try {
        response = await client.call(operation, argumentMapper(params as Record<string, unknown>), {
          ...(sessionId ? { pi_session_id: sessionId } : {}), pi_tool_call_id: _toolCallId,
        });
      } catch (error) {
        if (error instanceof BridgeClientError) throw new Error(JSON.stringify({ code: error.code, message: error.message }));
        throw error;
      }
      if (signal?.aborted) throw new Error("operator observation aborted");
      return {
        content: [{ type: "text", text: boundedText(response) }],
        details: boundedDetails(response),
      };
    },
  };
}

function assertModelSafeArguments(value: unknown, path = "arguments"): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertModelSafeArguments(item, `${path}[${index}]`));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      if (/(secret|token|password|api[_-]?key|raw[_-]?(s|tdr)|s[_-]?parameters)/i.test(key)) {
        throw new Error(`blocked sensitive or raw argument field: ${path}.${key}`);
      }
      assertModelSafeArguments(item, `${path}.${key}`);
    }
    return;
  }
  if (typeof value !== "string") return;
  if (/\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)/i.test(value)) {
    throw new Error(`blocked secret-like argument value at ${path}`);
  }
  if (/^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(value)) {
    throw new Error(`blocked absolute path argument at ${path}`);
  }
}

function boundedText(value: unknown): string {
  const text = JSON.stringify(value);
  if (Buffer.byteLength(text, "utf8") <= MAX_TOOL_TEXT_BYTES) return text;
  return `${Buffer.from(text, "utf8").subarray(0, MAX_TOOL_TEXT_BYTES - 64).toString("utf8")}...<truncated>`;
}

function boundedDetails(value: Record<string, unknown>): Record<string, unknown> {
  const text = JSON.stringify(value);
  if (Buffer.byteLength(text, "utf8") <= MAX_TOOL_TEXT_BYTES) return value;
  return {
    operation: value.operation,
    request_id: value.request_id,
    truncated: true,
    summary: "Validated bridge response exceeded the Pi tool-result byte limit.",
  };
}
