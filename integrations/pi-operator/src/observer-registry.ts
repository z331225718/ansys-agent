import type { AgentTool } from "@earendil-works/pi-agent-core";

import { BridgePipeClient } from "./bridge/pipe-client.js";
import { createReadonlyOperatorTools } from "./readonly-tools.js";

const HOST_IDENTITY = { name: "pi-agent-host", version: "0.1.0", schema_version: "1.0" } as const;

/**
 * Bootstrap the observer surface from an authoritative handshake. The bridge may
 * advertise more capabilities, but only the locally compiled R0/R1 tool mapping
 * can become model-visible.
 */
export async function createObserverToolRegistry(client: BridgePipeClient, sessionId?: string): Promise<AgentTool[]> {
  const response = await client.call("system.handshake", { client: HOST_IDENTITY }, sessionId ? { pi_session_id: sessionId } : undefined);
  const data = response.data as { schema_bundle_digest?: unknown; capabilities?: unknown } | undefined;
  if (data?.schema_bundle_digest !== client.schemaBundleDigest) {
    throw new Error("observer bridge schema bundle digest does not match the Host contract");
  }
  if (!Array.isArray(data.capabilities) || !data.capabilities.every((item) => typeof item === "string")) {
    throw new Error("observer bridge handshake has invalid capabilities");
  }
  const serverCapabilities = new Set(data.capabilities);
  const localTools = createReadonlyOperatorTools(client, undefined, sessionId);
  const allowed = new Set<string>();
  for (const tool of localTools) {
    const operation = toolOperation(tool.name);
    if (serverCapabilities.has(operation)) allowed.add(operation);
  }
  if (!allowed.has("system.handshake")) {
    throw new Error("observer bridge did not confirm system.handshake capability");
  }
  return createReadonlyOperatorTools(client, allowed, sessionId);
}

function toolOperation(name: string): string {
  const mapping: Record<string, string> = {
    operator_handshake: "system.handshake", operator_template_catalog: "template.catalog", operator_preflight: "system.preflight",
    operator_graph_status: "graph.status", operator_replay_delta: "replay.delta", operator_graph_visualize: "graph.visualize",
    operator_artifact_query: "artifact.query_bounded", operator_pending_approvals: "approval.get_pending",
    operator_prepare_approval: "approval.prepare_decision",
  };
  const operation = mapping[name];
  if (!operation) throw new Error(`unknown local observer tool: ${name}`);
  return operation;
}
