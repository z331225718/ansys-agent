import { createHash } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, readFile, realpath, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { BridgePipeClient } from "../src/bridge/pipe-client.js";

type GateArguments = { db: string; schemaRoot: string; bridgeCommand: string; evidenceFile: string; topologyManifest: string };
type ReadyRecord = { type: "ready"; namespace: "root/cimv2"; start_subscription: true; stop_subscription: true };
type StartRecord = { type: "start"; pid: string; parent_pid: string; name: string; time_created: string };
type StopRecord = { type: "stop"; pid: string; name: string; time_created: string };
type StoppedRecord = { type: "stopped"; event_count: number };
type ErrorRecord = { type: "error"; code: string; detail?: string };
type WatcherRecord = ReadyRecord | StartRecord | StopRecord | StoppedRecord | ErrorRecord;
type ProcessInstance = { pid: string; name: string; start_time: string; stop_time: string };
type DescendantInstance = { start: StartRecord; instance: ProcessInstance; depth: number };
type ImagePin = { executable_path: string; sha256: string };
type TopologyManifest = { manifest_version: 1; reviewed: true; topology: { launcher: ImagePin; runtime: ImagePin & { argv_digest: string }; console_host: ImagePin } };

const args = parseArguments(process.argv.slice(2));
if (process.platform !== "win32") throw new Error("P3 Windows evidence gate requires Windows and fails closed elsewhere");
const topologyManifest = await loadTopologyManifest(args.topologyManifest);
const topologyManifestDigest = await fileHash(args.topologyManifest);

const watcherScript = fileURLToPath(new URL("./p3-process-watcher.ps1", import.meta.url));
const runnerScript = fileURLToPath(import.meta.url);
const work = await mkdtemp(resolve(tmpdir(), "aedt-p3-windows-evidence-"));
const stopFile = resolve(work, "watcher.stop");
const watcher = startWatcher(watcherScript, stopFile);
if (!watcher.stdout || !watcher.stderr) throw new Error("P3 Windows evidence gate could not open watcher output streams");
const records: WatcherRecord[] = [];
let watcherFailure: Error | undefined;
let stdoutBuffer = "";
let watcherStderr = "";
let activeBridgeClient: BridgePipeClient | undefined;
let evidenceLauncherPid: number | undefined;
let evidenceRuntimePid: number | undefined;
watcher.stdout.on("data", (chunk: Buffer) => {
  stdoutBuffer += chunk.toString("utf8");
  const lines = stdoutBuffer.split("\n");
  stdoutBuffer = lines.pop() ?? "";
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const record = JSON.parse(line) as WatcherRecord;
      validateWatcherRecord(record);
      records.push(record);
      if (record.type === "error") watcherFailure = new Error(`P3 Windows watcher failed: ${record.code}${record.detail ? ` (${record.detail})` : ""}`);
    } catch (error) {
      watcherFailure = error instanceof Error ? error : new Error("P3 Windows watcher emitted invalid NDJSON");
    }
  }
});
watcher.stderr.on("data", (chunk: Buffer) => { watcherStderr += chunk.toString("utf8"); watcherFailure ??= new Error("P3 Windows watcher wrote to stderr"); });
watcher.once("error", (error) => { watcherFailure ??= error; });
watcher.once("exit", (code, signal) => {
  if (code !== 0 && !watcherFailure) watcherFailure = new Error(`P3 Windows watcher exited before completion (${code ?? signal ?? "unknown"}): ${watcherStderr.slice(0, 300)}`);
});

try {
  await waitFor(() => !!watcherFailure || records.some((record): record is ReadyRecord => record.type === "ready" && record.namespace === "root/cimv2"), "watcher ready");
  assertHealthy(watcherFailure);
  const leadCanary = await runCanary();
  await waitFor(() => canaryIsComplete(records, leadCanary), "lead canary events");
  assertHealthy(watcherFailure);

  const client = activeBridgeClient = new BridgePipeClient({
    command: args.bridgeCommand,
    args: ["-m", "aedt_agent.operator_bridge", "--db", args.db, "--schema-root", args.schemaRoot],
    schemaRoot: args.schemaRoot,
  });
  try {
    await client.start();
    const launcherPid = client.processId;
    if (!launcherPid) throw new Error("P3 Windows evidence gate could not obtain Bridge launcher PID");
    evidenceLauncherPid = launcherPid;
    await waitFor(() => !!watcherFailure || records.some((record): record is StartRecord => record.type === "start" && record.pid === String(launcherPid)), "Bridge launcher start event");
    assertHealthy(watcherFailure);
    const bridgeStart = uniqueBridgeStart(records, launcherPid);
    const handshake = await client.call("system.handshake", {
      client: { name: "p3-windows-evidence", version: "0.1.0", schema_version: "1.0" },
    });
    const handshakeData = handshake.data as { runtime_pid?: unknown; runtime_argv_digest?: unknown } | undefined;
    const runtimePidValue = handshakeData?.runtime_pid;
    if (typeof runtimePidValue !== "number" || !Number.isSafeInteger(runtimePidValue) || runtimePidValue < 1) throw new Error("P3 Windows evidence gate could not bind the Bridge runtime PID");
    const runtimePid = runtimePidValue;
    evidenceRuntimePid = runtimePid;
    const expectedArgvDigest = canonicalArgvDigest([args.bridgeCommand, "-m", "aedt_agent.operator_bridge", "--db", args.db, "--schema-root", args.schemaRoot]);
    if (handshakeData?.runtime_argv_digest !== expectedArgvDigest || expectedArgvDigest !== topologyManifest.topology.runtime.argv_digest) {
      throw new Error("P3 Windows evidence gate runtime argv digest does not match the reviewed topology manifest");
    }
    await waitFor(() => !!watcherFailure || records.some((record): record is StartRecord => record.type === "start" && record.pid === String(runtimePid)), "Bridge runtime start event");
    assertHealthy(watcherFailure);
    const runtimeStart = uniqueBridgeStart(records, runtimePid, launcherPid);
    const directChildren = records.filter((record): record is StartRecord => record.type === "start" && record.parent_pid === String(launcherPid) && greaterThan(record.time_created, bridgeStart.time_created));
    const runtimeChildren = directChildren.filter((record) => record.pid === String(runtimePid));
    const consoleChildren = directChildren.filter((record) => record.pid !== String(runtimePid));
    if (runtimeChildren.length !== 1 || consoleChildren.length > 1) throw new Error("P3 Windows evidence gate observed an unreviewed Bridge launch topology");
    const identities = await inspectLiveProcessIdentities([launcherPid, runtimePid, ...consoleChildren.map((record) => Number(record.pid))]);
    if (identities.get(runtimePid)?.parent_pid !== launcherPid || consoleChildren.some((record) => identities.get(Number(record.pid))?.parent_pid !== launcherPid)) {
      throw new Error("P3 Windows evidence gate live process parents do not match the reviewed topology");
    }
    const imageEvidence = new Map<number, ImageEvidence>();
    imageEvidence.set(launcherPid, await assertImagePin(identities.get(launcherPid), topologyManifest.topology.launcher, "launcher"));
    imageEvidence.set(runtimePid, await assertImagePin(identities.get(runtimePid), topologyManifest.topology.runtime, "runtime"));
    if (consoleChildren.length === 1) imageEvidence.set(Number(consoleChildren[0]!.pid), await assertImagePin(identities.get(Number(consoleChildren[0]!.pid)), topologyManifest.topology.console_host, "console host"));
    await client.close();
    await waitFor(() => records.some((record): record is StopRecord => record.type === "stop" && record.pid === String(launcherPid) && greaterThan(record.time_created, bridgeStart.time_created)), "Bridge launcher stop event");
    await waitFor(() => records.some((record): record is StopRecord => record.type === "stop" && record.pid === String(runtimePid) && greaterThan(record.time_created, runtimeStart.time_created)), "Bridge runtime stop event");
    assertHealthy(watcherFailure);
    const bridge = processInstance(records, bridgeStart);
    await assertProcessAbsent(bridge);

    const tailCanary = await runCanary();
    await waitFor(() => canaryIsComplete(records, tailCanary), "tail canary events");
    assertHealthy(watcherFailure);
    await writeFile(stopFile, "stop\n", { encoding: "ascii", mode: 0o600 });
    await waitFor(() => records.some((record): record is StoppedRecord => record.type === "stopped"), "watcher stop record");
    await waitForExit(watcher, "watcher exit");
    if (stdoutBuffer.trim()) throw new Error("P3 Windows watcher ended with a partial NDJSON frame");
    const descendants = descendantStarts(records, bridge);
    const reviewedDescendants = reviewedDescendantTree(descendants, runtimePid);
    for (const descendant of reviewedDescendants) await assertProcessAbsent(descendant.instance);
    const nodes = [
      nodeEvidence("launcher", bridgeStart, bridge, identities.get(launcherPid), imageEvidence.get(launcherPid)!, undefined),
      nodeEvidence("runtime", runtimeStart, processInstance(records, runtimeStart), identities.get(runtimePid), imageEvidence.get(runtimePid)!, expectedArgvDigest),
      ...reviewedDescendants.filter((item) => item.start.pid !== String(runtimePid)).map((item) => nodeEvidence("console_host", item.start, item.instance, identities.get(Number(item.start.pid)), imageEvidence.get(Number(item.start.pid))!, undefined)),
    ];
    const evidence = {
      status: "passed", topology_manifest_sha256: topologyManifestDigest, watcher_script_sha256: await fileHash(watcherScript), runner_script_sha256: await fileHash(runnerScript),
      powershell_version: await powershellVersion(), watcher_ready: true, watcher_namespace: "root/cimv2",
      canaries: [canaryEvidence(records, leadCanary), canaryEvidence(records, tailCanary)],
      bridge: { launcher_pid: bridge.pid, runtime_pid: runtimePid, reviewed_descendant_count: reviewedDescendants.length, nodes },
      lifecycle_record_count: records.length, lifecycle_records_sha256: lifecycleRecordsHash(records), record_set_complete: true,
      console_host_authenticode: "not_evidenced",
      watcher_event_count: records.filter((record) => record.type === "start" || record.type === "stop").length,
    };
    await writeFile(args.evidenceFile, `${JSON.stringify(evidence)}\n`, { encoding: "utf8", mode: 0o600 });
    process.stdout.write(`${JSON.stringify(evidence)}\n`);
  } finally {
    await client.close();
  }
} catch (error) {
  await activeBridgeClient?.close();
  let recordSetComplete = false;
  try {
    await writeFile(stopFile, "stop\n", { encoding: "ascii", mode: 0o600 });
    await waitFor(() => records.some((record) => record.type === "stopped") || watcher.exitCode !== null, "failure watcher stop record");
    if (watcher.exitCode === null) await waitForExit(watcher, "failure watcher exit");
    recordSetComplete = watcher.exitCode === 0 && !stdoutBuffer.trim() && !watcherFailure && records.some((record) => record.type === "stopped");
  } catch { /* failure evidence records incomplete drain below */ }
  const anchorPids = new Set([String(process.pid), ...(evidenceLauncherPid ? [String(evidenceLauncherPid)] : []), ...(evidenceRuntimePid ? [String(evidenceRuntimePid)] : [])]);
  const relevantStarts = records.filter((record): record is StartRecord => record.type === "start" && (anchorPids.has(record.pid) || anchorPids.has(record.parent_pid)));
  const relevantPids = new Set([...anchorPids, ...relevantStarts.map((record) => record.pid)]);
  const boundedRecords = records.filter((record) => (record.type === "start" && (relevantPids.has(record.pid) || relevantPids.has(record.parent_pid))) || (record.type === "stop" && relevantPids.has(record.pid))).slice(0, 64);
  await writeFile(args.evidenceFile, `${JSON.stringify({
    status: "failed", gate_passed: false, failure_code: "p3_windows_gate_failure", topology_manifest_sha256: topologyManifestDigest,
    watcher_script_sha256: await fileHash(watcherScript), runner_script_sha256: await fileHash(runnerScript),
    lifecycle_record_count: records.length, lifecycle_records_sha256: lifecycleRecordsHash(records), record_set_complete: recordSetComplete,
    bridge: { launcher_pid: evidenceLauncherPid ?? null, runtime_pid: evidenceRuntimePid ?? null },
    bounded_lifecycle_records: boundedRecords, bounded_lifecycle_records_truncated: boundedRecords.length === 64,
  })}\n`, { encoding: "utf8", mode: 0o600 });
  throw error;
} finally {
  try { await writeFile(stopFile, "stop\n", { encoding: "ascii", mode: 0o600 }); } catch { /* watcher startup failed */ }
  if (watcher.exitCode === null) watcher.kill();
  await rm(work, { recursive: true, force: true });
}

function parseArguments(values: string[]): GateArguments {
  const valuesByName = new Map<string, string>();
  for (let index = 0; index < values.length; index += 2) {
    const name = values[index]; const value = values[index + 1];
    if (!name?.startsWith("--") || !value || valuesByName.has(name)) throw new Error("usage: --db --schema-root --bridge-command --evidence-file --topology-manifest");
    valuesByName.set(name, value);
  }
  const required = ["--db", "--schema-root", "--bridge-command", "--evidence-file", "--topology-manifest"];
  if (valuesByName.size !== required.length || required.some((name) => !valuesByName.has(name))) throw new Error("usage: --db --schema-root --bridge-command --evidence-file --topology-manifest");
  return { db: resolve(valuesByName.get("--db")!), schemaRoot: resolve(valuesByName.get("--schema-root")!), bridgeCommand: valuesByName.get("--bridge-command")!, evidenceFile: resolve(valuesByName.get("--evidence-file")!), topologyManifest: resolve(valuesByName.get("--topology-manifest")!) };
}

async function loadTopologyManifest(path: string): Promise<TopologyManifest> {
  const value: unknown = JSON.parse(await readFile(path, "utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("P3 Windows topology manifest must be an object");
  const manifest = value as Partial<TopologyManifest>;
  if (!hasExactKeys(manifest, ["manifest_version", "reviewed", "topology"])) throw new Error("P3 Windows topology manifest has unexpected properties");
  if (manifest.manifest_version !== 1 || manifest.reviewed !== true || !manifest.topology || typeof manifest.topology !== "object") {
    throw new Error("P3 Windows topology manifest is not reviewed version 1");
  }
  const topology = manifest.topology as TopologyManifest["topology"];
  if (!hasExactKeys(topology, ["launcher", "runtime", "console_host"])) throw new Error("P3 Windows topology manifest has unexpected topology roles");
  for (const role of ["launcher", "runtime", "console_host"] as const) {
    const pin = topology[role];
    const keys = role === "runtime" ? ["executable_path", "sha256", "argv_digest"] : ["executable_path", "sha256"];
    if (!pin || !hasExactKeys(pin, keys) || typeof pin.executable_path !== "string" || !/^sha256:[0-9a-f]{64}$/.test(pin.sha256)) {
      throw new Error(`P3 Windows topology manifest has an invalid ${role} image pin`);
    }
  }
  if (!/^sha256:[0-9a-f]{64}$/.test(topology.runtime.argv_digest)) throw new Error("P3 Windows topology manifest has an invalid runtime argv digest");
  return { manifest_version: 1, reviewed: true, topology };
}

function hasExactKeys(value: object, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function startWatcher(script: string, stopFile: string): ChildProcess {
  return spawn("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-StopFile", stopFile], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
}

async function runCanary(): Promise<number> {
  const child = spawn("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "$p = Start-Process -FilePath cmd.exe -ArgumentList '/c','exit 0' -PassThru; $p.WaitForExit()"], { stdio: "ignore", windowsHide: true });
  if (!child.pid) throw new Error("P3 Windows evidence gate could not start canary");
  await waitForExit(child, "canary exit");
  return child.pid;
}

function canaryIsComplete(records: WatcherRecord[], canaryPid: number): boolean {
  const root = records.find((record): record is StartRecord => record.type === "start" && record.pid === String(canaryPid) && record.parent_pid === String(process.pid));
  if (!root) return false;
  try {
    const rootInstance = processInstance(records, root);
    const child = records.find((record): record is StartRecord => record.type === "start" && record.parent_pid === root.pid && isWithin(record.time_created, rootInstance));
    return !!child && !!processInstance(records, child);
  } catch {
    return false;
  }
}

function uniqueBridgeStart(records: WatcherRecord[], bridgePid: number, parentPid = process.pid): StartRecord {
  const found = records.filter((record): record is StartRecord => record.type === "start" && record.pid === String(bridgePid) && record.parent_pid === String(parentPid));
  if (found.length !== 1) throw new Error(`P3 Windows evidence gate expected one Bridge start event, found ${found.length}`);
  return found[0]!;
}

function processInstance(records: WatcherRecord[], start: StartRecord): ProcessInstance {
  if (records.some((record): record is StartRecord => record.type === "start" && record !== start && record.pid === start.pid && compareTime(record.time_created, start.time_created) === 0)) {
    throw new Error(`P3 Windows evidence gate observed an ambiguous process lifetime for PID ${start.pid}`);
  }
  const laterStarts = records.filter((record): record is StartRecord => record.type === "start" && record.pid === start.pid && greaterThan(record.time_created, start.time_created));
  const nextStart = laterStarts.sort((left, right) => compareTime(left.time_created, right.time_created))[0];
  const stops = records.filter((record): record is StopRecord => record.type === "stop" && record.pid === start.pid
    && greaterThan(record.time_created, start.time_created) && (!nextStart || greaterThan(nextStart.time_created, record.time_created)));
  if (stops.length !== 1 || stops[0]!.name.localeCompare(start.name, undefined, { sensitivity: "accent" }) !== 0) {
    throw new Error(`P3 Windows evidence gate observed an ambiguous process lifetime for PID ${start.pid}`);
  }
  const stop = stops[0]!;
  return { pid: start.pid, name: start.name, start_time: start.time_created, stop_time: stop.time_created };
}

function descendantStarts(records: WatcherRecord[], bridge: ProcessInstance): DescendantInstance[] {
  const starts = records.filter((record): record is StartRecord => record.type === "start").sort((left, right) => compareTime(left.time_created, right.time_created));
  const active = new Map<string, { instance: ProcessInstance; depth: number }>([[bridge.pid, { instance: bridge, depth: 0 }]]);
  const descendants: DescendantInstance[] = [];
  for (const start of starts) {
    const parent = active.get(start.parent_pid);
    if (!parent || !isWithin(start.time_created, parent.instance)) continue;
    const instance = processInstance(records, start);
    const depth = parent.depth + 1;
    active.set(start.pid, { instance, depth });
    descendants.push({ start, instance, depth });
  }
  return descendants;
}

function reviewedDescendantTree(descendants: DescendantInstance[], runtimePid: number): DescendantInstance[] {
  const runtime = descendants.filter((item) => item.start.pid === String(runtimePid));
  const consoleHosts = descendants.filter((item) => item.start.pid !== String(runtimePid));
  if (runtime.length !== 1 || runtime[0]!.depth !== 1 || consoleHosts.length > 1 || consoleHosts.some((item) => item.depth !== 1)) {
    throw new Error("P3 Windows evidence gate observed an unreviewed Bridge process tree");
  }
  return descendants;
}

type LiveProcessIdentity = { pid: number; parent_pid: number; executable_path: string; creation_date: string };
type ImageEvidence = { executable_path_sha256: string; image_sha256: string; pin_match: true };

async function inspectLiveProcessIdentities(pids: number[]): Promise<Map<number, LiveProcessIdentity>> {
  const unique = [...new Set(pids)];
  const command = `$result = @(); ${unique.map((pid) => `$p = Get-CimInstance Win32_Process -Filter 'ProcessId = ${pid}'; if ($null -eq $p -or [string]::IsNullOrWhiteSpace($p.ExecutablePath) -or [string]::IsNullOrWhiteSpace($p.CreationDate)) { exit 2 }; $result += [PSCustomObject]@{ pid = [int]$p.ProcessId; parent_pid = [int]$p.ParentProcessId; executable_path = [string]$p.ExecutablePath; creation_date = [string]$p.CreationDate }`).join("; ")}; $result | ConvertTo-Json -Compress`;
  const output = await runPowerShell(command);
  const value: unknown = JSON.parse(output);
  const entries = Array.isArray(value) ? value : [value];
  if (entries.length !== unique.length) throw new Error("P3 Windows evidence gate could not query every live process identity");
  const identities = new Map<number, LiveProcessIdentity>();
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") throw new Error("P3 Windows evidence gate received an invalid live process identity");
    const identity = entry as Partial<LiveProcessIdentity>;
    if (typeof identity.pid !== "number" || !Number.isSafeInteger(identity.pid) || typeof identity.parent_pid !== "number" || !Number.isSafeInteger(identity.parent_pid) || typeof identity.executable_path !== "string" || !identity.executable_path || typeof identity.creation_date !== "string" || !identity.creation_date) {
      throw new Error("P3 Windows evidence gate received an incomplete live process identity");
    }
    identities.set(identity.pid, identity as LiveProcessIdentity);
  }
  return identities;
}

async function assertImagePin(identity: LiveProcessIdentity | undefined, pin: ImagePin, role: string): Promise<ImageEvidence> {
  if (!identity) throw new Error(`P3 Windows evidence gate did not obtain ${role} identity`);
  const [actualPath, expectedPath] = await Promise.all([realpath(identity.executable_path), realpath(pin.executable_path)]);
  if (actualPath.toLowerCase() !== expectedPath.toLowerCase()) throw new Error(`P3 Windows evidence gate ${role} executable path does not match the reviewed manifest`);
  const imageSha256 = await stableFileHash(actualPath);
  if (imageSha256 !== pin.sha256) throw new Error(`P3 Windows evidence gate ${role} executable hash does not match the reviewed manifest`);
  return { executable_path_sha256: `sha256:${createHash("sha256").update(actualPath.toLowerCase(), "utf8").digest("hex")}`, image_sha256: imageSha256, pin_match: true };
}

function nodeEvidence(role: "launcher" | "runtime" | "console_host", start: StartRecord, instance: ProcessInstance, identity: LiveProcessIdentity | undefined, image: ImageEvidence, runtimeArgvDigest: string | undefined): Record<string, unknown> {
  if (!identity) throw new Error(`P3 Windows evidence gate did not retain ${role} live identity`);
  return {
    role, pid: start.pid, parent_pid: start.parent_pid, name: start.name, start_time: instance.start_time, stop_time: instance.stop_time,
    live_creation_date: identity.creation_date, ...image,
    ...(runtimeArgvDigest ? { runtime_argv_digest: runtimeArgvDigest } : {}),
    final_exact_instance_absence: true,
  };
}

async function stableFileHash(path: string): Promise<string> {
  const before = await stat(path);
  const digest = await fileHash(path);
  const after = await stat(path);
  if (before.size !== after.size || before.mtimeMs !== after.mtimeMs) throw new Error("P3 Windows evidence gate executable changed while being hashed");
  return digest;
}

function canonicalArgvDigest(argv: string[]): string {
  if (argv.some((value) => /[^\x00-\x7f]/.test(value))) throw new Error("P3 Windows evidence gate requires an ASCII Bridge invocation");
  return `sha256:${createHash("sha256").update(JSON.stringify(argv), "utf8").digest("hex")}`;
}

async function assertProcessAbsent(bridge: ProcessInstance): Promise<void> {
  const command = `Get-CimInstance Win32_Process -Filter \"ProcessId = ${bridge.pid}\" | Select-Object -ExpandProperty CreationDate`;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const output = await runPowerShell(command);
    if (!output.trim()) return;
    await delay(200);
  }
  throw new Error("P3 Windows evidence gate final CIM check still finds the Bridge PID; PID reuse cannot be excluded");
}

function canaryEvidence(records: WatcherRecord[], pid: number): { pid: number; complete: true } {
  if (!canaryIsComplete(records, pid)) throw new Error("P3 Windows evidence gate lost a canary lifecycle");
  return { pid, complete: true };
}

function validateWatcherRecord(record: WatcherRecord): void {
  if (!record || typeof record !== "object" || !["ready", "start", "stop", "stopped", "error"].includes(record.type)) throw new Error("P3 Windows watcher emitted an invalid record");
  if ((record.type === "start" || record.type === "stop") && (!/^\d+$/.test(record.pid) || !/^\d+$/.test(record.time_created))) throw new Error("P3 Windows watcher emitted an invalid process lifetime");
}

function isWithin(time: string, process: ProcessInstance): boolean {
  return greaterThan(time, process.start_time) && (!process.stop_time || greaterThan(process.stop_time, time));
}

function compareTime(left: string, right: string): number { return BigInt(left) < BigInt(right) ? -1 : BigInt(left) > BigInt(right) ? 1 : 0; }
function greaterThan(left: string, right: string): boolean { return BigInt(left) > BigInt(right); }
function lifecycleRecordsHash(records: WatcherRecord[]): string {
  return `sha256:${createHash("sha256").update(JSON.stringify(records)).digest("hex")}`;
}
function assertHealthy(error: Error | undefined): asserts error is undefined { if (error) throw error; }
function delay(milliseconds: number): Promise<void> { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }
function waitFor(condition: () => boolean, description: string): Promise<void> { return poll(condition, description, 10_000); }
async function poll(condition: () => boolean, description: string, timeout: number): Promise<void> { const deadline = Date.now() + timeout; while (!condition()) { if (Date.now() >= deadline) throw new Error(`P3 Windows evidence gate timed out waiting for ${description}`); await delay(50); } }
function waitForExit(child: ChildProcess, description: string): Promise<void> {
  if (child.exitCode !== null) {
    if (child.exitCode === 0) return Promise.resolve();
    return Promise.reject(new Error(`P3 Windows evidence gate ${description} failed (${child.exitCode})`));
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`P3 Windows evidence gate timed out waiting for ${description}`)), 10_000);
    child.once("error", reject);
    child.once("exit", (code) => { clearTimeout(timer); if (code === 0) resolve(); else reject(new Error(`P3 Windows evidence gate ${description} failed (${code ?? "signal"})`)); });
  });
}
async function fileHash(path: string): Promise<string> { return `sha256:${createHash("sha256").update(await readFile(path)).digest("hex")}`; }
async function powershellVersion(): Promise<string> { return (await runPowerShell("$PSVersionTable.PSVersion.ToString()")).trim(); }
function runPowerShell(command: string): Promise<string> { return new Promise((resolvePromise, reject) => { const child = spawn("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true }); let output = ""; let errors = ""; child.stdout.on("data", (chunk: Buffer) => { output += chunk.toString("utf8"); }); child.stderr.on("data", (chunk: Buffer) => { errors += chunk.toString("utf8"); }); child.once("error", reject); child.once("exit", (code) => code === 0 ? resolvePromise(output) : reject(new Error(`PowerShell command failed: ${errors.slice(0, 200)}`))); }); }
