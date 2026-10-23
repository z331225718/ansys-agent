import { AgentHarness, type AgentHarnessEvent, type AgentHarnessTool, type AgentMessage, type Session } from "@earendil-works/pi-agent-core";
import type { Model, Models } from "@earendil-works/pi-ai";

import type { BridgePipeClient } from "./bridge/pipe-client.js";
import { READ_ONLY_OPERATOR_SYSTEM_PROMPT } from "./readonly-tools.js";
import { observerPolicyDigest } from "./observer-policy.js";
import { createObserverToolRegistry } from "./observer-registry.js";
import { operatorBinding, rehydrateOperatorSession } from "./session-store.js";

const SETTLED_COMMIT_TYPE = "observer_turn_commit_v1";

export { observerPolicyDigest } from "./observer-policy.js";

/** Deliberately exposes only turn execution and observation, never mutable harness controls. */
export type ObserverHarness = {
  prompt(input: string): Promise<void>;
  abort(): Promise<void>;
  subscribe(listener: (event: AgentHarnessEvent) => Promise<void> | void): () => void;
};

export async function createObserverHarness(options: {
  bridge: BridgePipeClient; session: Session; models: Models; model: Model<any>; skill: string;
  onToolCall?: (toolName: string, input: Record<string, unknown>) => void | Promise<void>;
  onToolResult?: (toolName: string, input: Record<string, unknown>, isError: boolean, content: string) => void | Promise<void>;
}): Promise<ObserverHarness> {
  const metadata = await options.session.getMetadata();
  const sessionId = metadata.id;
  await recoverObserverSession(options.session);
  const binding = await operatorBinding(options.session);
  if (binding.host_policy_digest !== observerPolicyDigest(options.skill)) {
    throw new Error("observer session is bound to a different System/Skill policy");
  }
  await rehydrateOperatorSession(options.bridge, options.session);
  const tools = await createObserverToolRegistry(options.bridge, sessionId);
  const harnessTools = tools.map((tool) => ({
    ...tool,
    execute: (id, params, signal, onUpdate) => tool.execute(id, params, signal, onUpdate),
  })) as AgentHarnessTool<undefined>[];
  const harness = new AgentHarness({
    session: options.session, models: options.models, model: options.model, tools: harnessTools,
    thinkingLevel: "off", systemPrompt: observerSystemPrompt(options.skill, binding.graph_run_id),
    streamOptions: { timeoutMs: 120_000, maxRetries: 1, maxRetryDelayMs: 1_000 },
  });
  harness.on("context", async (event) => ({ messages: event.messages.map(redactHistoricalUserMessage) }));
  harness.on("session_before_compact", async () => ({ cancel: true }));
  harness.on("tool_call", async (event) => {
    await options.onToolCall?.(event.toolName, event.input);
    return undefined;
  });
  harness.on("tool_result", async (event) => {
    const content = event.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");
    await options.onToolResult?.(event.toolName, event.input, event.isError, content);
    return undefined;
  });
  let commitFailure: Error | undefined;
  let promptInFlight = false;
  harness.subscribe(async (event) => {
    if (event.type !== "settled") return;
    try {
      await options.session.appendCustomEntry(SETTLED_COMMIT_TYPE, { version: 1, settled_at: new Date().toISOString() });
    } catch (error) {
      commitFailure = error instanceof Error ? error : new Error(String(error));
      throw commitFailure;
    }
  });
  return {
    async prompt(input: string): Promise<void> {
      if (commitFailure) throw new Error(`observer session commit failed: ${commitFailure.message}`);
      rejectUnsafePrompt(input);
      if (promptInFlight) throw new Error("observer is already processing a prompt");
      promptInFlight = true;
      try {
        await harness.prompt(input);
      } finally {
        promptInFlight = false;
      }
    },
    async abort(): Promise<void> { await harness.abort(); },
    subscribe: (listener) => harness.subscribe(listener),
  };
}

/** The bound graph is non-secret navigation context, never a model-selected target. */
export function observerSystemPrompt(skill: string, graphRunId?: string): string {
  const binding = graphRunId ? `\n\nThis observer Session is bound to graph_run_id=${graphRunId}. Use that exact ID when the user asks about the bound graph; never substitute or invent another ID.` : "";
  return `${READ_ONLY_OPERATOR_SYSTEM_PROMPT}\n\n${skill}${binding}`;
}

export async function recoverObserverSession(session: Session): Promise<void> {
  const branch = await session.getBranch();
  const commits = branch.filter(isSettledCommit);
  const lastCommit = commits.at(-1);
  if (!lastCommit) {
    if (branch.some((entry) => entry.type === "message")) {
      throw new Error("observer session has uncommitted message history; refusing unsafe recovery");
    }
    await session.appendCustomEntry(SETTLED_COMMIT_TYPE, { version: 1, kind: "initial" });
    return;
  }
  const leafId = await session.getLeafId();
  if (leafId !== lastCommit.id) await session.moveTo(lastCommit.id);
}

function isSettledCommit(entry: Awaited<ReturnType<Session["getBranch"]>>[number]): boolean {
  if (entry.type !== "custom" || entry.customType !== SETTLED_COMMIT_TYPE) return false;
  const data = entry.data;
  return Boolean(data && typeof data === "object" && !Array.isArray(data) && (data as { version?: unknown }).version === 1);
}

function rejectUnsafePrompt(value: string): void {
  if (/\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)/i.test(value) || /(?:secret|token|password|api[_-]?key)\s*[:=]/i.test(value) || /(?:[A-Za-z]:[\\/]|\\\\|\/)[^\s]+/.test(value)) {
    throw new Error("observer prompt contains sensitive data or an absolute path; use the trusted controller");
  }
}

function redactHistoricalUserMessage(message: AgentMessage): AgentMessage {
  if (message.role !== "user") return message;
  const text = typeof message.content === "string" ? message.content : message.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");
  if (!/(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+|secret|token|password|api[_-]?key|[A-Za-z]:[\\/]|\\\\|(?:^|\s)\/\S+)/i.test(text)) return message;
  return { ...message, content: "[Sensitive historical user content was withheld from the model.]" };
}
