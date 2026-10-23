import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";

import { ObserverTelemetry } from "../src/telemetry.js";

test("observer telemetry is ordered and never persists prompt or tool payload content", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-telemetry-"));
  const file = resolve(directory, "observer.jsonl");
  try {
    const telemetry = new ObserverTelemetry(file);
    await Promise.all([
      telemetry.record({ event: "host_started", session_id: "session-1", graph_run_id: "graph-1", policy_digest: "sha256:" + "a".repeat(64) }),
      telemetry.record({ event: "tool_call", session_id: "session-1", graph_run_id: "graph-1", operation: "graph.status", policy_digest: "sha256:" + "a".repeat(64) }),
    ]);
    await telemetry.close();
    const entries = (await readFile(file, "utf8")).trim().split("\n").map((line) => JSON.parse(line) as { event: string; operation?: string; policy_digest?: string });
    assert.deepEqual(entries.map((entry) => entry.event), ["host_started", "tool_call"]);
    assert.equal(entries[1].operation, "graph.status");
    assert.equal(entries.every((entry) => entry.policy_digest === "sha256:" + "a".repeat(64)), true);
    assert.equal(JSON.stringify(entries).includes("prompt"), false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
