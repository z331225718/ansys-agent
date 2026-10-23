import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createModels, fauxAssistantMessage, fauxProvider, fauxText, fauxToolCall } from "@earendil-works/pi-ai";

import { BridgePipeClient } from "../src/bridge/pipe-client.js";
import { createObserverHarness } from "../src/observer-harness.js";
import { loadObserverPolicy } from "../src/observer-policy.js";
import { createOperatorSession, openOperatorSession, rehydrateOperatorSession } from "../src/session-store.js";

type GateArguments = {
  db: string;
  schemaRoot: string;
  bridgeCommand: string;
};
type JsonExpectation = { json_path: string; equals: unknown };
type ResultExpectation = { ok: boolean; json_path?: string; equals?: unknown; also?: JsonExpectation[]; error_code?: string };
type GateCall = { tool: string; arguments: Record<string, unknown>; expected: ResultExpectation };
type GateScenario = { name: string; calls: GateCall[] };
type RecordedManifest = {
  version: number; fixture_kind: string; fixture_id: string; inventory_digest: string;
  approval: { approval_id: string; mission_id: string };
  fixture_observations: { node_run_count: number; artifact_count: number; pending_approval_count: number };
  recorded_missions: Array<{ mission_id: string; graph_run_id: string; template_id: string; expected_status: string; execution_profile: string; inputs: Record<string, unknown> }>;
  scenarios: Array<{ name: string; operations: string[]; expectations: ResultExpectation[]; must_include: string[]; must_not_include?: string[] }>;
};

const args = parseArguments(process.argv.slice(2));
const before = await databaseHashes(args.db);
if (Object.keys(before).some((path) => path.endsWith("-wal") || path.endsWith("-shm") || path.endsWith("-journal"))) {
  throw new Error("readonly gate requires a checkpointed static fixture without SQLite sidecars");
}

const manifest = JSON.parse(await readFile(fileURLToPath(new URL("../resources/p3-recorded-manifest.json", import.meta.url)), "utf8")) as RecordedManifest;
assertRecordedInventory(manifest);
const graphRunIds = manifest.recorded_missions.map((item) => item.graph_run_id);
const firstMission = manifest.recorded_missions[0]!;
const scenarios: GateScenario[] = [
  { name: "handshake", calls: [{ tool: "operator_handshake", arguments: {}, expected: { ok: true, json_path: "data.capabilities", equals: "array" } }] },
  { name: "template-catalog", calls: [{ tool: "operator_template_catalog", arguments: { limit: 5 }, expected: { ok: true, json_path: "data.templates", equals: "array" } }] },
  { name: "preflight", calls: [{ tool: "operator_preflight", arguments: { template_id: "brd_local_cut_build", execution_profile: "safe-recorded", inputs: { signal_net_count: 2, has_layout_reference: true } }, expected: { ok: true, json_path: "data.preflight_snapshot_id", equals: "string" } }] },
  { name: "running-truncated-nodes", calls: [{ tool: "operator_graph_status", arguments: { graph_run_id: graphRunIds[0] }, expected: { ok: true, json_path: "data.status", equals: "running", also: [{ json_path: "data.truncated_nodes", equals: true }] } }] },
  { name: "succeeded", calls: [{ tool: "operator_graph_status", arguments: { graph_run_id: graphRunIds[1] }, expected: { ok: true, json_path: "data.status", equals: "succeeded" } }] },
  { name: "failed", calls: [{ tool: "operator_graph_status", arguments: { graph_run_id: graphRunIds[2] }, expected: { ok: true, json_path: "data.status", equals: "failed" } }] },
  { name: "waiting-approval", calls: [{ tool: "operator_graph_status", arguments: { graph_run_id: graphRunIds[3] }, expected: { ok: true, json_path: "data.status", equals: "waiting_approval" } }] },
  { name: "replay-truncated", calls: [{ tool: "operator_replay_delta", arguments: { graph_run_id: graphRunIds[4], after_cursor: "0", limit: 1 }, expected: { ok: true, json_path: "data.truncated", equals: true } }] },
  { name: "visualize", calls: [{ tool: "operator_graph_visualize", arguments: { graph_run_id: graphRunIds[5], format: "mermaid" }, expected: { ok: true, json_path: "data.format", equals: "mermaid" } }] },
  { name: "artifact-approval-and-missing-resource", calls: [
    { tool: "operator_artifact_query", arguments: { mission_id: firstMission.mission_id, query: "model", limit: 10 }, expected: { ok: true, json_path: "data.artifacts", equals: "array", also: [{ json_path: "data.artifacts.length", equals: 10 }, { json_path: "data.truncated", equals: true }] } },
    { tool: "operator_pending_approvals", arguments: { mission_id: firstMission.mission_id }, expected: { ok: true, json_path: "data.approvals", equals: "array" } },
    { tool: "operator_prepare_approval", arguments: { approval_id: manifest.approval.approval_id, decision_intent: "approve" }, expected: { ok: true, json_path: "data.approval.approval_id", equals: manifest.approval.approval_id } },
    { tool: "operator_graph_status", arguments: { graph_run_id: "missing-recorded-graph" }, expected: { ok: false, error_code: "not_found" } },
  ] },
];
assertManifestMatchesScenarios(manifest, scenarios);
const faux = fauxProvider();
faux.setResponses(scenarios.flatMap((scenario) => [
  fauxAssistantMessage(scenario.calls.map((call) => fauxToolCall(call.tool, call.arguments))),
  (context) => fauxAssistantMessage(fauxText(explainAuthoritativeToolResults(context))),
]));
const client = new BridgePipeClient({
  command: args.bridgeCommand,
  args: ["-m", "aedt_agent.operator_bridge", "--db", args.db, "--schema-root", args.schemaRoot],
  schemaRoot: args.schemaRoot,
  timeoutMs: 15_000,
});
const models = createModels();
models.setProvider(faux.provider);
const sessionDirectory = await mkdtemp(resolve(tmpdir(), "aedt-p3-observer-session-"));
const policy = await loadObserverPolicy();
const session = await createOperatorSession(sessionDirectory, "readonly-gate", {
  graph_run_id: graphRunIds[0], schema_bundle_digest: client.schemaBundleDigest, host_policy_digest: policy.digest,
});
const observedTools = new Set<string>();
const observedToolResults: Array<{ toolName: string; input: Record<string, unknown>; isError: boolean; text: string }> = [];
const observedExplanations: string[] = [];
const agent = await createObserverHarness({
  bridge: client,
  session,
  models,
  model: faux.getModel(),
  skill: policy.skill,
  onToolCall: (toolName) => {
    observedTools.add(toolName);
  },
  onToolResult: (toolName, input, isError, text) => {
    observedToolResults.push({ toolName, input, isError, text });
  },
});
agent.subscribe((event) => {
  if (event.type !== "message_end" || event.message.role !== "assistant") return;
  const content = typeof event.message.content === "string"
    ? event.message.content
    : event.message.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");
  if (content) observedExplanations.push(content);
});

try {
  for (const scenario of scenarios) {
    await agent.prompt(`Run the ${scenario.name} read-only observation and report its bounded result.`);
  }
  const reopenedSession = await openOperatorSession(sessionDirectory, "readonly-gate");
  const restored = await rehydrateOperatorSession(client, reopenedSession);
  if (restored.binding.graph_run_id !== graphRunIds[0] || (restored.graph_status?.data as { status?: unknown } | undefined)?.status !== "running") {
    throw new Error("readonly gate failed: reopened Session did not rehydrate its authoritative graph binding");
  }
  const resumeProvider = fauxProvider();
  const resumeModels = createModels();
  resumeModels.setProvider(resumeProvider.provider);
  await createObserverHarness({ bridge: client, session: reopenedSession, models: resumeModels, model: resumeProvider.getModel(), skill: policy.skill });
} finally {
  await client.close();
  await rm(sessionDirectory, { recursive: true, force: true });
}

const after = await databaseHashes(args.db);
if (JSON.stringify(after) !== JSON.stringify(before)) {
  throw new Error("readonly gate failed: SQLite fixture changed");
}
if (faux.state.callCount !== scenarios.length * 2) {
  throw new Error("readonly gate failed: unexpected Pi provider turn count");
}
const expectedTools = new Set(scenarios.flatMap((scenario) => scenario.calls.map((call) => call.tool)));
if (observedTools.size !== expectedTools.size || [...expectedTools].some((tool) => !observedTools.has(tool))) {
  throw new Error(`readonly gate failed: actual Harness tool coverage did not match the recorded scenario manifest (actual: ${[...observedTools].join(",")})`);
}
assertScenarioFacts(manifest, scenarios, observedToolResults);
assertExplanations(manifest, observedExplanations);
process.stdout.write(`${JSON.stringify({
  status: "passed",
  scenario_count: scenarios.length,
  observed_operations: [...observedTools].sort(),
  tool_result_count: observedToolResults.length,
  database_hashes: before,
  pi_turns: faux.state.callCount,
})}\n`);

function parseArguments(values: string[]): GateArguments {
  let db = "";
  let schemaRoot = resolve(process.cwd(), "..", "..", "schemas", "operator", "v1");
  let bridgeCommand = process.env.AEDT_AGENT_PYTHON || "python";
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    const next = values[index + 1];
    if (value === "--db" && next) {
      db = resolve(next);
      index += 1;
    } else if (value === "--schema-root" && next) {
      schemaRoot = resolve(next);
      index += 1;
    } else if (value === "--bridge-command" && next) {
      bridgeCommand = next;
      index += 1;
    } else {
      throw new Error(`unknown or incomplete argument: ${value}`);
    }
  }
  if (!db) {
    throw new Error("usage: gate:readonly -- --db <static.db> [--schema-root <path>] [--bridge-command <python>]");
  }
  return { db, schemaRoot, bridgeCommand };
}

function assertRecordedInventory(manifest: RecordedManifest): void {
  if (manifest.fixture_id !== "p3-readonly-recorded-v1" || !/^sha256:[a-f0-9]{64}$/.test(manifest.inventory_digest) || manifest.recorded_missions.length !== 10) {
    throw new Error("readonly gate recorded fixture manifest is invalid");
  }
  const graphIds = new Set(manifest.recorded_missions.map((item) => item.graph_run_id));
  const missionIds = new Set(manifest.recorded_missions.map((item) => item.mission_id));
  const templates = new Set(manifest.recorded_missions.map((item) => item.template_id));
  if (graphIds.size !== 10 || missionIds.size !== 10 || templates.size !== 5 || !manifest.recorded_missions.every((item) => item.execution_profile === "safe-recorded")) {
    throw new Error("readonly gate recorded fixture inventory is not heterogeneous");
  }
  if (manifest.approval.mission_id !== manifest.recorded_missions[0]!.mission_id) throw new Error("readonly gate approval is not bound to its recorded mission");
  if (manifest.fixture_observations.node_run_count !== 101 || manifest.fixture_observations.artifact_count !== 11 || manifest.fixture_observations.pending_approval_count !== 1) {
    throw new Error("readonly gate recorded fixture observations are invalid");
  }
}

function assertManifestMatchesScenarios(manifest: RecordedManifest, scenarios: GateScenario[]): void {
  if (manifest.version !== 1 || manifest.fixture_kind !== "immutable_recorded_sqlite" || manifest.scenarios.length !== scenarios.length) {
    throw new Error("readonly gate manifest is invalid");
  }
  for (const [index, scenario] of scenarios.entries()) {
    const declared = manifest.scenarios[index];
    const operations = scenario.calls.map((call) => call.tool);
    const expectations = scenario.calls.map((call) => call.expected);
    if (declared?.name !== scenario.name || JSON.stringify(declared.operations) !== JSON.stringify(operations) || JSON.stringify(declared.expectations) !== JSON.stringify(expectations)) {
      throw new Error(`readonly gate manifest does not match scenario: ${scenario.name}`);
    }
  }
}

function assertScenarioFacts(
  manifest: RecordedManifest,
  scenarios: GateScenario[],
  results: Array<{ toolName: string; input: Record<string, unknown>; isError: boolean; text: string }>,
): void {
  for (const [index, scenario] of scenarios.entries()) {
    const scenarioResults = scenario.calls.map((call) => results.find((result) => result.toolName === call.tool && JSON.stringify(result.input) === JSON.stringify(call.arguments)));
    if (scenarioResults.some((result) => result === undefined)) throw new Error(`readonly gate failed: missing actual result for ${scenario.name}`);
    const declared = manifest.scenarios[index]!;
    for (const [callIndex, result] of scenarioResults.entries()) {
      const expectation = declared.expectations[callIndex]!;
      assertToolResult(scenario.name, scenario.calls[callIndex]!, result!, expectation);
    }
  }
}

function assertToolResult(name: string, call: GateCall, result: { toolName: string; input: Record<string, unknown>; isError: boolean; text: string }, expectation: ResultExpectation): void {
  if (expectation.ok === false) {
    const error = parseToolError(result.text);
    if (!result.isError || !expectation.error_code || error?.code !== expectation.error_code) {
      throw new Error(`readonly gate failed: ${name}/${call.tool} did not return the expected ${expectation.error_code ?? "error"}`);
    }
    return;
  }
  if (result.isError) throw new Error(`readonly gate failed: unexpected tool error from ${call.tool} in ${name}: ${result.text.slice(0, 300)}`);
  let response: unknown;
  try {
    response = JSON.parse(result.text);
  } catch {
    throw new Error(`readonly gate failed: ${name}/${call.tool} returned a non-JSON success payload`);
  }
  if (!response || typeof response !== "object" || (response as { ok?: unknown }).ok !== true) {
    throw new Error(`readonly gate failed: ${name}/${call.tool} did not return an ok bridge envelope`);
  }
  if (expectation.json_path) assertJsonExpectation(name, call.tool, response, { json_path: expectation.json_path, equals: expectation.equals });
  for (const additional of expectation.also ?? []) assertJsonExpectation(name, call.tool, response, additional);
}

function parseToolError(text: string): { code?: unknown } | undefined {
  try {
    const value: unknown = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? value as { code?: unknown } : undefined;
  } catch {
    return undefined;
  }
}

function assertJsonExpectation(name: string, tool: string, response: unknown, expectation: JsonExpectation): void {
  const actual = readJsonPath(response, expectation.json_path);
  const matches = expectation.equals === "array" ? Array.isArray(actual)
    : expectation.equals === "string" ? typeof actual === "string" && actual.length > 0
      : Object.is(actual, expectation.equals);
  if (!matches) throw new Error(`readonly gate failed: ${name}/${tool} did not satisfy ${expectation.json_path}`);
}

function readJsonPath(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, part) => Array.isArray(current)
    ? part === "length" ? current.length : undefined
    : current && typeof current === "object" ? (current as Record<string, unknown>)[part] : undefined, value);
}

function explainAuthoritativeToolResults(context: { messages: Array<{ role: string; content: unknown }> }): string {
  const latestUser = context.messages.map((message) => message.role).lastIndexOf("user");
  const resultText = JSON.stringify(context.messages.slice(latestUser + 1));
  if (resultText.includes('"status":"waiting_approval"')) return "The graph is waiting for a trusted approval decision.";
  if (resultText.includes('"status":"failed"')) return "The authoritative graph failed; no retry was attempted.";
  if (resultText.includes('"status":"succeeded"')) return "The authoritative graph succeeded.";
  if (resultText.includes('"status":"running"') && resultText.includes('"truncated_nodes":true')) return "The authoritative graph is running and the node view is truncated.";
  if (resultText.includes('"events"') && resultText.includes('"truncated":true')) return "The replay is bounded and truncated.";
  if (resultText.includes('"format":"mermaid"')) return "The graph visualization was observed read-only.";
  if (resultText.includes('"artifacts"')) return "Artifact and approval summaries were observed; the missing graph was reported as an error.";
  if (resultText.includes('"preflight_snapshot_id"')) return "The preflight is read-only and did not start a mission.";
  if (resultText.includes('"templates"')) return "Only allowlisted templates were observed.";
  if (resultText.includes('"capabilities"')) return "Read-only bridge capabilities were observed.";
  return "No authoritative observation was returned.";
}

function assertExplanations(manifest: RecordedManifest, explanations: string[]): void {
  if (explanations.length !== manifest.scenarios.length) throw new Error("readonly gate failed: missing Pi explanation");
  for (const [index, scenario] of manifest.scenarios.entries()) {
    const explanation = explanations[index]!.toLowerCase();
    if (scenario.must_include.some((term) => !explanation.includes(term.toLowerCase())) || scenario.must_not_include?.some((term) => explanation.includes(term.toLowerCase()))) {
      throw new Error(`readonly gate failed: semantic explanation mismatch for ${scenario.name}`);
    }
  }
}

async function databaseHashes(database: string): Promise<Record<string, string>> {
  const hashes: Record<string, string> = {};
  for (const path of [database, `${database}-wal`, `${database}-shm`, `${database}-journal`]) {
    try {
      hashes[path] = createHash("sha256").update(await readFile(path)).digest("hex");
    } catch (error: unknown) {
      if (!(error instanceof Error) || !("code" in error) || error.code !== "ENOENT") throw error;
    }
  }
  return hashes;
}
