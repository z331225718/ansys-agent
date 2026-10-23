import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";

import { createModels, fauxAssistantMessage, fauxProvider, fauxText } from "@earendil-works/pi-ai";

import { createObserverHarness, observerPolicyDigest, observerSystemPrompt } from "../src/observer-harness.js";
import { createOperatorSession } from "../src/session-store.js";

test("Observer Harness commits settled turns and does not expose mutable harness controls", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-observer-harness-"));
  try {
    const digest = "sha256:" + "4".repeat(64);
    const skill = "Use only read-only tools.";
    const session = await createOperatorSession(directory, "observer-harness", { schema_bundle_digest: digest, host_policy_digest: observerPolicyDigest(skill) });
    const faux = fauxProvider();
    const models = createModels();
    models.setProvider(faux.provider);
    faux.setResponses([fauxAssistantMessage(fauxText("Read-only observation complete."))]);
    const bridge = {
      schemaBundleDigest: digest,
      async call(operation: string) {
        assert.equal(operation, "system.handshake");
        return { data: { schema_bundle_digest: digest, capabilities: ["system.handshake"] } };
      },
    };
    const agent = await createObserverHarness({
      bridge: bridge as never,
      session,
      models,
      model: faux.getModel(),
      skill,
    });

    assert.equal("compact" in agent, false);
    assert.equal("setTools" in agent, false);
    await assert.rejects(agent.prompt("api_token=sk-very-secret-value"), /sensitive data/);
    assert.equal(faux.state.callCount, 0);
    await agent.prompt("Check whether the bridge is ready.");
    const branch = await session.getBranch();
    assert.equal(branch.filter((entry) => entry.type === "custom" && entry.customType === "observer_turn_commit_v1").length, 2);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("exposes only the Session-bound graph ID as observer navigation context", () => {
  const prompt = observerSystemPrompt("Use read-only tools.", "graph-bound-001");
  assert.ok(prompt.includes("graph_run_id=graph-bound-001"));
  assert.ok(prompt.includes("never substitute or invent"));
});
