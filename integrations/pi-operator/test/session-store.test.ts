import assert from "node:assert/strict";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";

import {
  createOperatorSession,
  openOperatorSession,
  operatorBinding,
  acquireOperatorSessionLease,
  clearTrustedStaleOperatorSessionLease,
  clearTrustedStaleRecoveryMarker,
  operatorSessionExists,
  rehydrateOperatorSession,
} from "../src/session-store.js";
import { recoverObserverSession } from "../src/observer-harness.js";

test("persists Pi session binding metadata separately from the authoritative mission database", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    const created = await createOperatorSession(directory, "session-001", {
      mission_id: "mission-001",
      graph_run_id: "graph-001",
      event_cursor: "7",
      schema_bundle_digest: "sha256:" + "0".repeat(64),
    });
    await created.appendCustomEntry("operator-observation", { status: "running" });

    const reopened = await openOperatorSession(directory, "session-001");
    const binding = await operatorBinding(reopened);
    const observations = await reopened.getStorage().findEntries("custom");

    assert.equal(binding.graph_run_id, "graph-001");
    assert.equal(observations.length, 1);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("rehydration always handshakes then rereads authoritative graph state", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  const digest = "sha256:" + "1".repeat(64);
  try {
    const session = await createOperatorSession(directory, "session-002", {
      graph_run_id: "graph-002",
      schema_bundle_digest: digest,
    });
    const calls: Array<{ operation: string; caller?: { pi_session_id: string } }> = [];
    const restored = await rehydrateOperatorSession(
      {
        async call(operation, _argumentsValue, caller) {
          calls.push({ operation, caller });
          if (operation === "system.handshake") return { data: { schema_bundle_digest: digest } };
          return { data: { status: "running" } };
        },
      },
      session,
    );

    assert.deepEqual(calls, [
      { operation: "system.handshake", caller: { pi_session_id: "session-002" } },
      { operation: "graph.status", caller: { pi_session_id: "session-002" } },
    ]);
    assert.equal((restored.graph_status?.data as { status: string }).status, "running");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("recovery restores the last settled commit and rejects an uncommitted transcript", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    const session = await createOperatorSession(directory, "session-recover", {
      schema_bundle_digest: "sha256:" + "2".repeat(64),
    });
    await recoverObserverSession(session);
    const initialCommit = await session.getLeafId();
    assert.ok(initialCommit);
    await session.appendCustomEntry("partial_observer_runtime_state", { unexpected: true });
    await recoverObserverSession(session);
    assert.equal(await session.getLeafId(), initialCommit);

    const unsafe = await createOperatorSession(directory, "session-unsafe", {
      schema_bundle_digest: "sha256:" + "3".repeat(64),
    });
    await unsafe.appendMessage({ role: "user", content: [{ type: "text", text: "uncommitted" }], timestamp: Date.now() });
    await assert.rejects(recoverObserverSession(unsafe), /uncommitted message history/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("corrupt Pi session JSONL fails without creating or changing any mission database", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    await writeFile(resolve(directory, "corrupt.jsonl"), "not-json\n", "utf8");

    await assert.rejects(openOperatorSession(directory, "corrupt"));
    assert.equal((await readdir(directory)).some((name) => name.endsWith(".db")), false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("rejects session identifiers that could escape or poison the session directory", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    for (const sessionId of ["../escape", "..", "C:\\escape", "aux", "session/name"]) {
      await assert.rejects(openOperatorSession(directory, sessionId), /invalid Pi session id/);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("distinguishes a missing session from a corrupt existing session", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    assert.equal(await operatorSessionExists(directory, "missing"), false);
    const path = resolve(directory, "corrupt.jsonl");
    await writeFile(path, "not-json\n", "utf8");
    assert.equal(await operatorSessionExists(directory, "corrupt"), true);
    await assert.rejects(openOperatorSession(directory, "corrupt"));
    assert.equal(await (await import("node:fs/promises")).readFile(path, "utf8"), "not-json\n");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("allows exactly one observer process to hold a session lease", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    const first = await acquireOperatorSessionLease(directory, "session-lock");
    await assert.rejects(acquireOperatorSessionLease(directory, "session-lock"), /already held/);
    await first.release();
    const second = await acquireOperatorSessionLease(directory, "session-lock");
    await second.release();
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("fails closed when a stale lease needs trusted recovery", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    await writeFile(resolve(directory, "session-stale.lock"), JSON.stringify({ token: "old", pid: 999_999_999 }) + "\n", "utf8");
    await assert.rejects(acquireOperatorSessionLease(directory, "session-stale"), /trusted recovery/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("all concurrent claimants fail closed while a stale lease exists", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    await writeFile(resolve(directory, "session-race.lock"), JSON.stringify({ token: "old", pid: 999_999_999 }) + "\n", "utf8");
    const attempts = await Promise.allSettled([
      acquireOperatorSessionLease(directory, "session-race"),
      acquireOperatorSessionLease(directory, "session-race"),
    ]);
    assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 0);
    assert.equal(attempts.filter((attempt) => attempt.status === "rejected").length, 2);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("trusted stale-lease recovery requires an exact dead-owner token and leaves mismatches untouched", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  const token = "11111111-1111-4111-8111-111111111111";
  const path = resolve(directory, "session-stale.lock");
  try {
    const contents = JSON.stringify({ token, pid: 999_999_999, session_id: "session-stale", created_at: new Date().toISOString() }) + "\n";
    await writeFile(path, contents, "utf8");
    await assert.rejects(clearTrustedStaleOperatorSessionLease(directory, "session-stale", "22222222-2222-4222-8222-222222222222"), /mismatched/);
    assert.equal(await (await import("node:fs/promises")).readFile(path, "utf8"), contents);
    await clearTrustedStaleOperatorSessionLease(directory, "session-stale", token);
    assert.equal(await (await import("node:fs/promises")).access(path).then(() => true, () => false), false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("trusted stale-lease recovery rejects a lease still owned by this process", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  const token = "33333333-3333-4333-8333-333333333333";
  const path = resolve(directory, "session-live.lock");
  try {
    await writeFile(path, JSON.stringify({ token, pid: process.pid, session_id: "session-live", created_at: new Date().toISOString() }) + "\n", "utf8");
    await assert.rejects(clearTrustedStaleOperatorSessionLease(directory, "session-live", token), /live lease owner/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("trusted stale-lease recovery rejects malformed tokens and acquisition refuses an active recovery marker", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  try {
    await assert.rejects(clearTrustedStaleOperatorSessionLease(directory, "session-token", "11111111----------------------------"), /exact lease token/);
    await writeFile(resolve(directory, "session-recovery.recovery.lock"), "maintenance\n", "utf8");
    await assert.rejects(acquireOperatorSessionLease(directory, "session-recovery"), /recovery is in progress/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("trusted stale-lease recovery leaves malformed lock metadata untouched", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  const token = "44444444-4444-4444-8444-444444444444";
  const path = resolve(directory, "session-malformed.lock");
  const contents = JSON.stringify({ token, pid: 999_999_999, session_id: "session-malformed" }) + "\n";
  try {
    await writeFile(path, contents, "utf8");
    await assert.rejects(clearTrustedStaleOperatorSessionLease(directory, "session-malformed", token), /invalid or mismatched/);
    assert.equal(await (await import("node:fs/promises")).readFile(path, "utf8"), contents);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("trusted recovery-marker cleanup refuses live owners and clears a dead matching marker", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "aedt-pi-session-"));
  const token = "55555555-5555-4555-8555-555555555555";
  const path = resolve(directory, "session-marker.recovery.lock");
  try {
    await writeFile(path, JSON.stringify({ token, pid: process.pid, session_id: "session-marker", created_at: new Date().toISOString() }) + "\n", "utf8");
    await assert.rejects(clearTrustedStaleRecoveryMarker(directory, "session-marker", token), /live lease owner/);
    await writeFile(path, JSON.stringify({ token, pid: 999_999_999, session_id: "session-marker", created_at: new Date().toISOString() }) + "\n", "utf8");
    await clearTrustedStaleRecoveryMarker(directory, "session-marker", token);
    assert.equal(await (await import("node:fs/promises")).access(path).then(() => true, () => false), false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
