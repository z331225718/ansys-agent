import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ContractValidator } from "../src/contracts.js";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const schemaRoot = resolve(packageRoot, "..", "..", "schemas", "operator", "v1");
const validator = new ContractValidator(schemaRoot);

test("accepts protocol-owned valid fixtures", () => {
  for (const name of ["system.handshake.request.json", "graph.status.request.json", "mission.start.request.json"]) {
    const result = validator.validateRequest(readJson(resolve(schemaRoot, "fixtures", "valid", name)));
    assert.deepEqual(result, { ok: true });
  }
});

test("rejects protocol-owned invalid fixtures", () => {
  for (const name of [
    "unknown-operation.request.json",
    "mission.start-missing-guard.request.json",
    "graph.status-unexpected-field.request.json",
  ]) {
    const result = validator.validateRequest(readJson(resolve(schemaRoot, "fixtures", "invalid", name)));
    assert.equal(result.ok, false);
  }
});

test("uses the same schema bundle digest as the Python manifest", () => {
  const manifest = readJson(resolve(schemaRoot, "bundle-manifest.json"));
  assert.equal(validator.schemaBundleDigest, String(manifest.bundle_digest));
});

test("rejects schema-valid envelopes whose successful data does not match the operation result contract", () => {
  const result = validator.validateTerminalResponse({
    schema_version: "1.0", type: "response",
    request_id: "00000000-0000-4000-8000-000000000001",
    trace_id: "00000000-0000-4000-8000-000000000002",
    operation: "system.handshake", ok: true, completed_at: "2026-07-26T08:00:00Z",
    resource: null, data: { schema_version: "1.0" }, error: null,
  });
  assert.equal(result.ok, false);
});

function readJson(path: string): Record<string, unknown> {
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}
