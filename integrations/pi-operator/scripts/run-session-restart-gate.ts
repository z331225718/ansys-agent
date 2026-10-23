import { resolve } from "node:path";

import { createModels, fauxAssistantMessage, fauxProvider, fauxText } from "@earendil-works/pi-ai";

import { BridgePipeClient } from "../src/bridge/pipe-client.js";
import { createObserverHarness } from "../src/observer-harness.js";
import { loadObserverPolicy } from "../src/observer-policy.js";
import { acquireOperatorSessionLease, createOperatorSession, openOperatorSession, operatorBinding, operatorSessionExists, rehydrateOperatorSession } from "../src/session-store.js";

type Arguments = { phase: "init" | "resume"; db: string; schemaRoot: string; sessionDir: string; sessionId: string; graphRunId: string; bridgeCommand: string };

const args = parseArguments(process.argv.slice(2));
const policy = await loadObserverPolicy();
const client = new BridgePipeClient({
  command: args.bridgeCommand,
  args: ["-m", "aedt_agent.operator_bridge", "--db", args.db, "--schema-root", args.schemaRoot],
  schemaRoot: args.schemaRoot,
});
const lease = await acquireOperatorSessionLease(args.sessionDir, args.sessionId);
try {
  const session = args.phase === "init"
    ? await createFreshSession(args, client.schemaBundleDigest, policy.digest)
    : await openResumedSession(args);
  const faux = fauxProvider();
  const models = createModels();
  models.setProvider(faux.provider);
  if (args.phase === "init") faux.setResponses([fauxAssistantMessage(fauxText("Read-only restart checkpoint created."))]);
  const harness = await createObserverHarness({ bridge: client, session, models, model: faux.getModel(), skill: policy.skill });
  if (args.phase === "init") {
    await harness.prompt("Create a read-only session checkpoint.");
  }
  const restored = await rehydrateOperatorSession(client, session);
  if ((restored.graph_status?.data as { status?: unknown } | undefined)?.status !== "running") throw new Error("restart gate did not recover the authoritative running graph");
  process.stdout.write(`${JSON.stringify({ status: "passed", phase: args.phase, session_id: args.sessionId, graph_run_id: args.graphRunId })}\n`);
} finally {
  try {
    await client.close();
  } finally {
    await lease.release();
  }
}

async function createFreshSession(args: Arguments, digest: string, policyDigest: string) {
  if (await operatorSessionExists(args.sessionDir, args.sessionId)) throw new Error("restart gate init refuses to overwrite an existing session");
  return createOperatorSession(args.sessionDir, args.sessionId, {
    graph_run_id: args.graphRunId, schema_bundle_digest: digest, host_policy_digest: policyDigest,
  });
}

async function openResumedSession(args: Arguments) {
  if (!await operatorSessionExists(args.sessionDir, args.sessionId)) throw new Error("restart gate cannot resume a missing session");
  const session = await openOperatorSession(args.sessionDir, args.sessionId);
  const binding = await operatorBinding(session);
  if (binding.graph_run_id !== args.graphRunId) throw new Error("restart gate cannot rebind a session to a different graph run");
  return session;
}

function parseArguments(values: string[]): Arguments {
  const named = new Map<string, string>();
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index]; const value = values[index + 1];
    if (!key?.startsWith("--") || !value || named.has(key)) throw new Error("invalid restart gate arguments");
    named.set(key, value);
  }
  const required = ["--phase", "--db", "--schema-root", "--session-dir", "--session-id", "--graph-run-id"];
  if (required.some((key) => !named.has(key)) || named.size !== required.length) throw new Error("invalid restart gate arguments");
  const phase = named.get("--phase");
  if (phase !== "init" && phase !== "resume") throw new Error("restart gate phase must be init or resume");
  const bridgeCommand = process.env.AEDT_AGENT_PYTHON;
  if (!bridgeCommand) throw new Error("AEDT_AGENT_PYTHON must name the trusted Python runtime");
  return { phase, db: resolve(named.get("--db")!), schemaRoot: resolve(named.get("--schema-root")!), sessionDir: resolve(named.get("--session-dir")!), sessionId: named.get("--session-id")!, graphRunId: named.get("--graph-run-id")!, bridgeCommand };
}
