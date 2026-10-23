import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";

import { createModels } from "@earendil-works/pi-ai";
import { deepseekProvider } from "@earendil-works/pi-ai/providers/deepseek";

import { BridgePipeClient } from "./bridge/pipe-client.js";
import { acquireOperatorSessionLease, createOperatorSession, openOperatorSession, operatorBinding, operatorSessionExists } from "./session-store.js";
import { createObserverHarness, type ObserverHarness } from "./observer-harness.js";
import { shouldExitAfterInputFailure, type ObserverInputState } from "./input-lifecycle.js";
import { loadObserverPolicy } from "./observer-policy.js";
import { loadProviderProfile, type ProviderProfile } from "./provider-profile.js";
import { installProviderFetchGuard } from "./provider-transport.js";
import { ObserverTelemetry } from "./telemetry.js";

type ObserverConfig = { db: string; schemaRoot: string; sessionDir: string; sessionId: string; graphRunId: string; telemetryFile: string; model: string };

async function main(): Promise<void> {
  const config = parseArguments(process.argv.slice(2));
  let input: ReturnType<typeof createInterface> | undefined;
  let telemetry: ObserverTelemetry | undefined;
  let agent: ObserverHarness | undefined;
  let stopping = false;
  let stopReason: "stdin_eof" | "user_exit" | "signal" | "error" = "error";
  let inputState: ObserverInputState = "open";
  let onInputEnd: (() => void) | undefined;
  let onInputError: (() => void) | undefined;
  let abortPromise = Promise.resolve();
  const onSignal = () => {
    if (stopping) return;
    stopping = true;
    stopReason = "signal";
    input?.close();
    abortPromise = agent?.abort().catch(() => undefined) ?? Promise.resolve();
  };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  const python = process.env.AEDT_AGENT_PYTHON;
  if (!python) {
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
    throw new Error("AEDT_AGENT_PYTHON must name the trusted Python runtime");
  }
  let client: BridgePipeClient;
  try {
    client = new BridgePipeClient({
      command: python,
      args: ["-m", "aedt_agent.operator_bridge", "--db", config.db, "--schema-root", config.schemaRoot],
      schemaRoot: config.schemaRoot,
    });
  } catch (error) {
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
    throw error;
  }
  let lease;
  try {
    lease = await acquireOperatorSessionLease(config.sessionDir, config.sessionId);
  } catch (error) {
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
    throw error;
  }
  let policyDigest = "";
  let providerProfile: ProviderProfile | undefined;
  let restoreProviderFetch: (() => void) | undefined;
  try {
    if (stopping) return;
    const configuredModels = await createConfiguredModels(config.model);
    const { models, model } = configuredModels;
    providerProfile = configuredModels.profile;
    restoreProviderFetch = installProviderFetchGuard(new URL(providerProfile.baseUrl).origin);
    const policy = await loadObserverPolicy();
    policyDigest = policy.digest;
    const { session } = await openOrCreateSession(config, client.schemaBundleDigest, policy.digest, providerProfile.digest);
    telemetry = new ObserverTelemetry(config.telemetryFile);
    const telemetryProfile = { provider_profile_digest: providerProfile.digest, provider_adapter: providerProfile.adapter, provider_model: providerProfile.model, provider_origin_digest: providerProfile.originDigest };
    await telemetry.record({ event: "host_started", session_id: config.sessionId, graph_run_id: config.graphRunId, policy_digest: policy.digest, ...telemetryProfile });
    agent = await createObserverHarness({
      bridge: client, session, models, model, skill: policy.skill,
      onToolCall: (operation) => telemetry!.record({ event: "tool_call", session_id: config.sessionId, graph_run_id: config.graphRunId, operation, policy_digest: policy.digest, ...telemetryProfile }),
      onToolResult: (operation, _input, isError) => telemetry!.record({ event: "tool_result", session_id: config.sessionId, graph_run_id: config.graphRunId, operation, is_error: isError, policy_digest: policy.digest, ...telemetryProfile }),
    });
    agent.subscribe(async (event) => {
      if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") stdout.write(event.assistantMessageEvent.delta);
      if (event.type === "settled") await telemetry!.record({ event: "turn_settled", session_id: config.sessionId, graph_run_id: config.graphRunId, policy_digest: policy.digest, ...telemetryProfile });
    });
    input = createInterface({ input: stdin, output: stdout });
    onInputEnd = () => { inputState = "eof"; };
    onInputError = () => { inputState = "errored"; };
    stdin.once("end", onInputEnd);
    stdin.once("error", onInputError);
    while (!stopping) {
      let prompt: string;
      try {
        prompt = await input.question("\nobserver> ");
      } catch (error) {
        if (shouldExitAfterInputFailure(stopping, inputState, stdin.readableEnded)) {
          if (!stopping) stopReason = "stdin_eof";
          return;
        }
        throw error;
      }
      if (prompt.trim() === "/exit") {
        stopReason = "user_exit";
        return;
      }
      try {
        await agent.prompt(prompt);
      } catch (error) {
        if (stopping) return;
        throw error;
      }
      stdout.write("\n");
    }
  } finally {
    try {
      await abortPromise;
      input?.close();
      restoreProviderFetch?.();
      if (onInputEnd) stdin.off("end", onInputEnd);
      if (onInputError) stdin.off("error", onInputError);
      await client.close();
    } finally {
      try {
        if (telemetry) {
          await telemetry.record({ event: "host_stopped", session_id: config.sessionId, graph_run_id: config.graphRunId, policy_digest: policyDigest, stop_reason: stopReason, ...(providerProfile ? { provider_profile_digest: providerProfile.digest, provider_adapter: providerProfile.adapter, provider_model: providerProfile.model, provider_origin_digest: providerProfile.originDigest } : {}) });
          await telemetry.close();
        }
      } finally {
        process.off("SIGINT", onSignal);
        process.off("SIGTERM", onSignal);
        await lease.release();
      }
    }
  }
}

async function createConfiguredModels(modelName: string) {
  const profile = loadProviderProfile(process.env, modelName);
  const models = createModels();
  models.setProvider(deepseekProvider());
  const model = models.getModel(profile.providerId, modelName);
  if (!model) throw new Error(`unsupported observer model: ${modelName}`);
  if (!await models.checkAuth(profile.providerId)) throw new Error(`provider authentication is not configured; set ${profile.apiKeyEnv} in the Host environment`);
  return { models, model, profile };
}

async function openOrCreateSession(config: ObserverConfig, digest: string, policyDigest: string, providerProfileDigest: string) {
  if (await operatorSessionExists(config.sessionDir, config.sessionId)) {
    const session = await openOperatorSession(config.sessionDir, config.sessionId);
    const binding = await operatorBinding(session);
    if (binding.graph_run_id !== config.graphRunId) throw new Error("existing observer session is bound to a different graph run");
    if (binding.host_policy_digest !== policyDigest) throw new Error("existing observer session is bound to a different System/Skill policy");
    if (binding.provider_profile_digest !== providerProfileDigest) throw new Error("existing observer session is bound to a different provider profile");
    return { session, created: false };
  }
  return { session: await createOperatorSession(config.sessionDir, config.sessionId, { graph_run_id: config.graphRunId, schema_bundle_digest: digest, host_policy_digest: policyDigest, provider_profile_digest: providerProfileDigest }), created: true };
}

function parseArguments(values: string[]): ObserverConfig {
  const named = new Map<string, string>();
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index]; const value = values[index + 1];
    if (!key?.startsWith("--") || !value || named.has(key)) throw new Error("usage: --db --schema-root --session-dir --session-id --graph-run-id --telemetry-file --model");
    named.set(key, value);
  }
  const required = ["--db", "--schema-root", "--session-dir", "--session-id", "--graph-run-id", "--telemetry-file", "--model"];
  if (named.size !== required.length || required.some((key) => !named.has(key))) throw new Error("usage: --db --schema-root --session-dir --session-id --graph-run-id --telemetry-file --model");
  return { db: named.get("--db")!, schemaRoot: named.get("--schema-root")!, sessionDir: named.get("--session-dir")!, sessionId: named.get("--session-id")!, graphRunId: named.get("--graph-run-id")!, telemetryFile: named.get("--telemetry-file")!, model: named.get("--model")! };
}

void main().catch((error: unknown) => { process.stderr.write(`observer Host failed: ${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
