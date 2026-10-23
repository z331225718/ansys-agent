import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { test } from "node:test";

import type { AgentOptions } from "@earendil-works/pi-agent-core";
import { createFauxCore, fauxAssistantMessage, fauxToolCall, fauxText } from "@earendil-works/pi-ai";

import { BridgePipeClient } from "../src/bridge/pipe-client.js";
import { createPiAgentHost } from "../src/agent-host.js";
import { createReadonlyOperatorAgent } from "../src/readonly-agent.js";
import { createReadonlyOperatorTools, READ_ONLY_OPERATOR_SYSTEM_PROMPT } from "../src/readonly-tools.js";
import { createObserverToolRegistry } from "../src/observer-registry.js";
import { modelToolSchemaFor } from "../src/generated/operator-v1.js";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const schemaRoot = resolve(packageRoot, "..", "..", "schemas", "operator", "v1");
const fixture = resolve(packageRoot, "test", "fixtures", "fake-bridge.mjs");

test("Pi tool surface is explicitly read-only and excludes all write operations", () => {
  const client = new BridgePipeClient({ command: process.execPath, args: [], schemaRoot });
  const tools = createReadonlyOperatorTools(client);

  assert.deepEqual(
    tools.map((tool) => tool.name),
    [
      "operator_handshake",
      "operator_template_catalog",
      "operator_preflight",
      "operator_graph_status",
      "operator_replay_delta",
      "operator_graph_visualize",
      "operator_artifact_query",
      "operator_pending_approvals",
      "operator_prepare_approval",
    ],
  );
  assert.ok(tools.every((tool) => tool.executionMode === "parallel"));
  assert.ok(!tools.some((tool) => /start|advance|intervention|stop|approve/.test(tool.name)));
});

test("model-visible tool schemas are generated from the canonical operator contract", () => {
  const handshake = modelToolSchemaFor("system.handshake") as { properties?: Record<string, unknown> };
  const replay = modelToolSchemaFor("replay.delta") as { properties?: Record<string, { anyOf?: Array<{ pattern?: string }> }> };
  assert.deepEqual(handshake.properties, {});
  assert.equal(replay.properties?.after_cursor?.anyOf?.[0]?.pattern, "^[0-9]+$");
});

test("observer registry intersects local R0/R1 tools with the authoritative handshake", async () => {
  const client = {
    schemaBundleDigest: "sha256:" + "0".repeat(64),
    async call() {
      return { data: { schema_bundle_digest: "sha256:" + "0".repeat(64), capabilities: ["system.handshake", "graph.status", "mission.start"] } };
    },
  } as unknown as BridgePipeClient;
  const tools = await createObserverToolRegistry(client);
  assert.deepEqual(tools.map((tool) => tool.name), ["operator_handshake", "operator_graph_status"]);
});

test("constructs a Pi Agent with the read-only system prompt and tools", () => {
  const client = new BridgePipeClient({ command: process.execPath, args: [], schemaRoot });
  const options = {
    streamFn: (() => {
      throw new Error("stream should not run in construction test");
    }) as AgentOptions["streamFn"],
    initialState: { model: {} as never },
  } satisfies AgentOptions;

  const agent = createReadonlyOperatorAgent(client, options);

  assert.equal(agent.state.systemPrompt, READ_ONLY_OPERATOR_SYSTEM_PROMPT);
  assert.equal(agent.state.tools.length, 9);
});

test("handshake tool injects fixed host identity instead of model-provided arguments", async () => {
  const calls: Array<{ operation: string; args: Record<string, unknown> }> = [];
  const client = {
    call: async (operation: string, args: Record<string, unknown>) => {
      calls.push({ operation, args });
      return { data: {} };
    },
  } as unknown as BridgePipeClient;
  const handshake = createReadonlyOperatorTools(client).find((tool) => tool.name === "operator_handshake");
  assert.ok(handshake);
  await handshake.execute("tool-identity", {}, new AbortController().signal, undefined);
  assert.deepEqual(calls, [{
    operation: "system.handshake",
    args: { client: { name: "pi-agent-host", version: "0.1.0", schema_version: "1.0" } },
  }]);
});

test("Agent Host profile is fixed by process configuration and fails closed before write profiles exist", () => {
  const client = new BridgePipeClient({ command: process.execPath, args: [], schemaRoot });
  const options = {
    streamFn: (() => {
      throw new Error("stream should not run in construction test");
    }) as AgentOptions["streamFn"],
    initialState: { model: {} as never },
  } satisfies AgentOptions;

  const agent = createPiAgentHost(client, options, { profile: "observer" });
  assert.equal(agent.state.tools.length, 9);
  assert.throws(
    () => createPiAgentHost(client, options, { profile: "controlled-recorded" }),
    /not implemented/,
  );
});

test("faux Pi provider performs a read-only handshake ReAct turn", async () => {
  const faux = createFauxCore({});
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("operator_handshake", {}, { id: "tool-001" })),
    fauxAssistantMessage(fauxText("The bridge is available in read-only mode.")),
  ]);
  const client = new BridgePipeClient({ command: process.execPath, args: [fixture], schemaRoot });
  const agent = createReadonlyOperatorAgent(client, {
    streamFn: faux.streamSimple,
    initialState: { model: faux.getModel() },
  });
  try {
    await agent.prompt("Inspect the operator bridge.");
    await agent.waitForIdle();
    assert.equal(faux.state.callCount, 2);
    assert.ok(agent.state.messages.some((message) => message.role === "toolResult"));
    assert.ok(agent.state.messages.some((message) => message.role === "assistant"));
  } finally {
    await client.close();
  }
});

test("faux Pi provider can issue independent read-only calls in one parallel tool batch", async () => {
  const faux = createFauxCore({});
  faux.setResponses([
    fauxAssistantMessage([
      fauxToolCall("operator_handshake", {}, { id: "tool-parallel-001" }),
      fauxToolCall("operator_template_catalog", {}, { id: "tool-parallel-002" }),
    ]),
    fauxAssistantMessage(fauxText("Both observations completed.")),
  ]);
  const client = new BridgePipeClient({ command: process.execPath, args: [fixture], schemaRoot });
  const agent = createReadonlyOperatorAgent(client, {
    streamFn: faux.streamSimple,
    initialState: { model: faux.getModel() },
  });
  try {
    await agent.prompt("Inspect the bridge and templates.");
    await agent.waitForIdle();
    assert.equal(faux.state.callCount, 2);
    assert.equal(agent.state.messages.filter((message) => message.role === "toolResult").length, 2);
  } finally {
    await client.close();
  }
});

test("faux Pi provider cannot invoke shell or write tools that are absent from the Agent", async () => {
  const faux = createFauxCore({});
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("bash", { command: "echo unsafe" })),
    fauxAssistantMessage(fauxToolCall("mission.start", { goal: "unsafe" })),
    fauxAssistantMessage(fauxText("Those capabilities are unavailable in read-only mode.")),
  ]);
  const client = new BridgePipeClient({ command: "does-not-run", args: [], schemaRoot });
  const agent = createReadonlyOperatorAgent(client, {
    streamFn: faux.streamSimple,
    initialState: { model: faux.getModel() },
  });

  await agent.prompt("Run a shell command and start a mission.");
  await agent.waitForIdle();

  assert.equal(faux.state.callCount, 3);
  assert.ok(agent.state.messages.filter((message) => message.role === "toolResult").length >= 2);
});

test("readonly tool boundary blocks raw arrays, secrets, and absolute paths before bridge dispatch", async () => {
  const faux = createFauxCore({});
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("operator_preflight", {
      template_id: "brd_local_cut_build",
      execution_profile: "safe-recorded",
      inputs: { raw_s_parameters: [1, 2], api_token: "sk-very-secret-value" },
    })),
    fauxAssistantMessage(fauxToolCall("operator_preflight", {
      template_id: "brd_local_cut_build",
      execution_profile: "safe-recorded",
      inputs: { layout_file: "C:/sensitive/case.brd" },
    })),
    fauxAssistantMessage(fauxText("Sensitive input was not sent to the bridge.")),
  ]);
  const client = new BridgePipeClient({ command: "does-not-run", args: [], schemaRoot });
  const agent = createReadonlyOperatorAgent(client, {
    streamFn: faux.streamSimple,
    initialState: { model: faux.getModel() },
  });

  await agent.prompt("Use these sensitive inputs.");
  await agent.waitForIdle();

  assert.equal(faux.state.callCount, 3);
  assert.equal(agent.state.messages.filter((message) => message.role === "toolResult").length, 2);
});

test("provider context redacts secret-bearing user content before the Pi model is called", async () => {
  const faux = createFauxCore({});
  let providerContext = "";
  faux.setResponses([
    (context) => {
      providerContext = JSON.stringify(context);
      return fauxAssistantMessage(fauxText("Please use the trusted controller for sensitive data."));
    },
  ]);
  const client = new BridgePipeClient({ command: "does-not-run", args: [], schemaRoot });
  const agent = createReadonlyOperatorAgent(client, {
    streamFn: faux.streamSimple,
    initialState: { model: faux.getModel() },
  });

  await agent.prompt("My api_token=sk-very-secret-value and file is C:/sensitive/case.brd");
  await agent.waitForIdle();

  assert.ok(providerContext.includes("Sensitive user content was withheld"));
  assert.ok(!providerContext.includes("sk-very-secret-value"));
  assert.ok(!providerContext.includes("C:/sensitive/case.brd"));
});
