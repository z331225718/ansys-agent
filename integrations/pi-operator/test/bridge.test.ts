import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { test } from "node:test";

import { BridgePipeClient } from "../src/bridge/pipe-client.js";
import { BridgeClientError } from "../src/bridge/errors.js";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const schemaRoot = resolve(packageRoot, "..", "..", "schemas", "operator", "v1");
const repositoryRoot = resolve(packageRoot, "..", "..");
const fixture = resolve(packageRoot, "test", "fixtures", "fake-bridge.mjs");
const corruptFixture = resolve(packageRoot, "test", "fixtures", "fake-corrupt-bridge.mjs");
const slowFixture = resolve(packageRoot, "test", "fixtures", "slow-bridge.mjs");
const execFileAsync = promisify(execFile);

test("uses fd 3 for strict protocol frames while draining stdout diagnostics", async () => {
  const diagnostics: string[] = [];
  const client = new BridgePipeClient({
    command: process.execPath,
    args: [fixture],
    schemaRoot,
    onDiagnostic: (stream, text) => diagnostics.push(`${stream}:${text}`),
  });
  try {
    const response = await client.call("system.handshake", {
      client: { name: "contract-test", version: "0", schema_version: "1.0" },
    });
    assert.equal((response.data as { schema_version: string }).schema_version, "1.0");
    assert.ok(diagnostics.some((item) => item.includes("native diagnostic noise")));
  } finally {
    await client.close();
  }
});

test("communicates with the Python bridge over fd 3 without parsing stdout", async () => {
  const workspace = await mkdtemp(resolve(tmpdir(), "aedt-pi-bridge-"));
  const diagnostics: string[] = [];
  const python = process.env.AEDT_AGENT_PYTHON ?? resolve(repositoryRoot, ".venv", "Scripts", "python.exe");
  const database = resolve(workspace, "missions.db");
  await execFileAsync(
    python,
    [
      "-c",
      `from pathlib import Path\nfrom aedt_agent.agent.runtime_factory import create_runtime_without_workers\ncreate_runtime_without_workers(Path(${JSON.stringify(database)}))`,
    ],
    { cwd: repositoryRoot },
  );
  const client = new BridgePipeClient({
    command: python,
    args: ["-m", "aedt_agent.operator_bridge", "--db", database, "--schema-root", schemaRoot],
    schemaRoot,
    timeoutMs: 15_000,
    onDiagnostic: (stream, text) => diagnostics.push(`${stream}:${text}`),
  });
  try {
    const response = await client.call("system.handshake", {
      client: { name: "node-integration", version: "0", schema_version: "1.0" },
    });
    const data = response.data as { capabilities: string[] };
    assert.ok(data.capabilities.includes("graph.status"));
    assert.ok(!data.capabilities.includes("mission.start"));
    assert.equal(diagnostics.filter((item) => item.startsWith("stdout:")).length, 0);
  } finally {
    await client.close();
    await rm(workspace, { recursive: true, force: true });
  }
});

test("fails closed when the dedicated response pipe contains non-JSON data", async () => {
  const client = new BridgePipeClient({
    command: process.execPath,
    args: [corruptFixture],
    schemaRoot,
  });
  try {
    await assert.rejects(
      client.call("system.handshake", {
        client: { name: "corruption-test", version: "0", schema_version: "1.0" },
      }),
      (error: unknown) => error instanceof BridgeClientError && error.code === "protocol_error",
    );
  } finally {
    await client.close();
  }
});

test("a delayed read-only response is discarded without poisoning the next bridge call", async () => {
  const client = new BridgePipeClient({ command: process.execPath, args: [slowFixture], schemaRoot, timeoutMs: 10 });
  try {
    await assert.rejects(client.call("system.handshake", { client: { name: "test", version: "0", schema_version: "1.0" } }),
      (error: unknown) => error instanceof BridgeClientError && error.code === "timeout");
    await new Promise((resolve) => setTimeout(resolve, 80));
    const response = await client.call("system.handshake", { client: { name: "test", version: "0", schema_version: "1.0" } });
    assert.equal((response.data as { schema_version: string }).schema_version, "1.0");
  } finally {
    await client.close();
  }
});
