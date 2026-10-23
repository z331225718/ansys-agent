import { constants } from "node:fs";
import { access, appendFile, mkdir, open, readFile, realpath, rm, writeFile, type FileHandle } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { dirname, resolve, sep } from "node:path";

import {
  err,
  FileError,
  JsonlSessionStorage,
  ok,
  Session,
  type FileSystem,
  type Result,
  type SessionMetadata,
} from "@earendil-works/pi-agent-core";

export type OperatorBinding = {
  mission_id?: string;
  graph_run_id?: string;
  event_cursor?: string;
  schema_bundle_digest: string;
  host_policy_digest?: string;
  provider_profile_digest?: string;
};

export type OperatorSessionLease = { release(): Promise<void> };
export type OperatorBridgeCaller = { pi_session_id: string };
export type OperatorBridge = {
  call(operation: string, argumentsValue: Record<string, unknown>, caller?: OperatorBridgeCaller): Promise<Record<string, unknown>>;
};

export async function acquireOperatorSessionLease(directory: string, sessionId: string): Promise<OperatorSessionLease> {
  await mkdir(directory, { recursive: true });
  const root = await realpath(directory);
  const safeSessionId = validateSessionId(sessionId);
  await assertNoRecoveryInProgress(root, safeSessionId);
  const path = resolve(root, `${safeSessionId}.lock`);
  const token = randomUUID();
  const handle = await claimLease(path, token, sessionId);
  try {
    await assertNoRecoveryInProgress(root, safeSessionId);
  } catch (error) {
    await handle.close();
    const contents = await readFile(path, "utf8").catch(() => "");
    if (parseLeaseRecord(contents)?.token === token) await rm(path, { force: true });
    throw error;
  }
  let released = false;
  return { async release() {
    if (released) return;
    await handle.close();
    const contents = await readFile(path, "utf8").catch(() => "");
    const record = parseLeaseRecord(contents);
    if (record?.token === token) await rm(path, { force: true });
    released = true;
  } };
}

/**
 * Explicit trusted recovery only. It refuses a live or changed lease and
 * coordinates with new acquisition using a maintenance marker.
 */
export async function clearTrustedStaleOperatorSessionLease(directory: string, sessionId: string, expectedToken: string): Promise<void> {
  if (!isUuidV4(expectedToken)) throw new Error("trusted stale-lease recovery requires an exact lease token");
  const root = await realpath(directory);
  const safeSessionId = validateSessionId(sessionId);
  const recovery = await acquireRecoveryMarker(root, safeSessionId);
  try {
    const path = resolve(root, `${safeSessionId}.lock`);
    const before = await readFile(path, "utf8");
    const record = parseLeaseRecord(before);
    if (!record || record.token !== expectedToken || record.session_id !== sessionId || !isProcessId(record.pid) || !isIsoTimestamp(record.created_at)) {
      throw new Error("trusted stale-lease recovery refused an invalid or mismatched lease");
    }
    await assertProcessDead(record.pid);
    const current = await readFile(path, "utf8");
    if (current !== before) throw new Error("trusted stale-lease recovery refused a changed lease");
    await rm(path, { force: false });
  } finally {
    await recovery.release();
  }
}

/** Higher-privilege recovery for a crashed maintenance marker; never automatic. */
export async function clearTrustedStaleRecoveryMarker(directory: string, sessionId: string, expectedToken: string): Promise<void> {
  if (!isUuidV4(expectedToken)) throw new Error("trusted recovery-marker cleanup requires an exact marker token");
  const root = await realpath(directory);
  const safeSessionId = validateSessionId(sessionId);
  const path = resolve(root, `${safeSessionId}.recovery.lock`);
  const before = await readFile(path, "utf8");
  const marker = parseRecoveryMarker(before);
  if (!marker || marker.token !== expectedToken || marker.session_id !== sessionId || !isProcessId(marker.pid) || !isIsoTimestamp(marker.created_at)) {
    throw new Error("trusted recovery-marker cleanup refused an invalid or mismatched marker");
  }
  await assertProcessDead(marker.pid);
  if (await readFile(path, "utf8") !== before) throw new Error("trusted recovery-marker cleanup refused a changed marker");
  await rm(path, { force: false });
}

async function claimLease(path: string, token: string, sessionId: string): Promise<FileHandle> {
  let handle: FileHandle;
  try {
    handle = await open(path, "wx", 0o600);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") throw new Error("observer session is already held; trusted recovery must clear a stale lease");
    throw error;
  }
  try {
    await handle.writeFile(JSON.stringify({ token, pid: process.pid, session_id: sessionId, created_at: new Date().toISOString() }) + "\n", "utf8");
    return handle;
  } catch (error) {
    await handle.close().catch(() => undefined);
    await rm(path, { force: true }).catch(() => undefined);
    throw error;
  }
}

export async function createOperatorSession(
  directory: string,
  sessionId: string,
  binding: OperatorBinding,
): Promise<Session> {
  await mkdir(directory, { recursive: true });
  const storage = await JsonlSessionStorage.create(jsonlFileSystem, sessionFilePath(directory, sessionId), {
    cwd: directory,
    sessionId,
    metadata: { operator_binding: binding },
  });
  return new Session(storage);
}

export async function openOperatorSession(directory: string, sessionId: string): Promise<Session> {
  const storage = await JsonlSessionStorage.open(jsonlFileSystem, sessionFilePath(directory, sessionId));
  return new Session(storage);
}

export async function operatorSessionExists(directory: string, sessionId: string): Promise<boolean> {
  try {
    await access(sessionFilePath(directory, sessionId), constants.F_OK);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}

export async function operatorBinding(session: Session): Promise<OperatorBinding> {
  const metadata = await session.getMetadata() as SessionMetadata & { metadata?: { operator_binding?: unknown } };
  const candidate = metadata.metadata?.operator_binding;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    throw new Error("Pi session does not contain operator binding metadata");
  }
  const binding = candidate as Partial<OperatorBinding>;
  if (typeof binding.schema_bundle_digest !== "string") {
    throw new Error("Pi session binding is missing schema_bundle_digest");
  }
  if (binding.host_policy_digest !== undefined && typeof binding.host_policy_digest !== "string") {
    throw new Error("Pi session binding has an invalid host_policy_digest");
  }
  if (binding.provider_profile_digest !== undefined && typeof binding.provider_profile_digest !== "string") {
    throw new Error("Pi session binding has an invalid provider_profile_digest");
  }
  return binding as OperatorBinding;
}

export async function rehydrateOperatorSession(
  bridge: OperatorBridge,
  session: Session,
): Promise<{ binding: OperatorBinding; handshake: Record<string, unknown>; graph_status?: Record<string, unknown> }> {
  const binding = await operatorBinding(session);
  const metadata = await session.getMetadata();
  const caller = { pi_session_id: metadata.id };
  const handshake = await bridge.call("system.handshake", {
    client: { name: "pi-operator", version: "0.1.0", schema_version: "1.0" },
  }, caller);
  const data = handshake.data as { schema_bundle_digest?: unknown } | undefined;
  if (data?.schema_bundle_digest !== binding.schema_bundle_digest) {
    throw new Error("Pi session schema bundle does not match the authoritative bridge");
  }
  const graph_status = binding.graph_run_id
    ? await bridge.call("graph.status", { graph_run_id: binding.graph_run_id }, caller)
    : undefined;
  return { binding, handshake, graph_status };
}

function parseLeaseRecord(contents: string): { token?: unknown; pid?: unknown; session_id?: unknown; created_at?: unknown } | undefined {
  try {
    const parsed: unknown = JSON.parse(contents);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as { token?: unknown; pid?: unknown; session_id?: unknown; created_at?: unknown } : undefined;
  } catch {
    return undefined;
  }
}

function parseRecoveryMarker(contents: string): { token?: unknown; pid?: unknown; session_id?: unknown; created_at?: unknown } | undefined {
  return parseLeaseRecord(contents);
}

function isProcessId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isUuidV4(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function isIsoTimestamp(value: unknown): boolean {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

async function assertNoRecoveryInProgress(root: string, sessionId: string): Promise<void> {
  try {
    await access(resolve(root, `${sessionId}.recovery.lock`), constants.F_OK);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  throw new Error("observer session recovery is in progress; acquisition is refused");
}

async function acquireRecoveryMarker(root: string, sessionId: string): Promise<{ release(): Promise<void> }> {
  const path = resolve(root, `${sessionId}.recovery.lock`);
  let handle: FileHandle;
  try {
    handle = await open(path, "wx", 0o600);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") throw new Error("trusted stale-lease recovery is already in progress");
    throw error;
  }
  const token = randomUUID();
  try {
    await handle.writeFile(JSON.stringify({ token, pid: process.pid, session_id: sessionId, created_at: new Date().toISOString() }) + "\n", "utf8");
  } catch (error) {
    await handle.close().catch(() => undefined);
    await rm(path, { force: true }).catch(() => undefined);
    throw error;
  }
  return { async release() {
    await handle.close();
    const current = await readFile(path, "utf8").catch(() => "");
    if (parseRecoveryMarker(current)?.token === token) await rm(path, { force: true });
  } };
}

async function assertProcessDead(pid: number): Promise<void> {
  try {
    process.kill(pid, 0);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return;
    throw new Error("trusted stale-lease recovery could not prove the lease owner is dead");
  }
  throw new Error("trusted stale-lease recovery refuses a live lease owner");
}

const jsonlFileSystem: Pick<FileSystem, "readTextFile" | "readTextLines" | "writeFile" | "appendFile"> = {
  async readTextFile(path: string, _abortSignal?: AbortSignal): Promise<Result<string, FileError>> {
    try {
      return ok(await readFile(path, "utf8"));
    } catch (cause) {
      return err(new FileError("not_found", `cannot read session file: ${path}`, path, toError(cause)));
    }
  },
  async readTextLines(path: string, options?: { maxLines?: number; abortSignal?: AbortSignal }): Promise<Result<string[], FileError>> {
    try {
      const lines = (await readFile(path, "utf8")).split(/\r?\n/).filter(Boolean);
      return ok(options?.maxLines === undefined ? lines : lines.slice(0, options.maxLines));
    } catch (cause) {
      return err(new FileError("not_found", `cannot read session file: ${path}`, path, toError(cause)));
    }
  },
  async writeFile(path: string, content: string | Uint8Array, _abortSignal?: AbortSignal): Promise<Result<void, FileError>> {
    try {
      await mkdir(dirname(path), { recursive: true });
      await writeFile(path, content);
      return ok(undefined);
    } catch (cause) {
      return err(new FileError("unknown", `cannot write session file: ${path}`, path, toError(cause)));
    }
  },
  async appendFile(path: string, content: string | Uint8Array): Promise<Result<void, FileError>> {
    try {
      await mkdir(dirname(path), { recursive: true });
      await appendFile(path, content);
      return ok(undefined);
    } catch (cause) {
      return err(new FileError("unknown", `cannot append session file: ${path}`, path, toError(cause)));
    }
  },
};

function toError(cause: unknown): Error {
  return cause instanceof Error ? cause : new Error(String(cause));
}

function sessionFilePath(directory: string, sessionId: string): string {
  validateSessionId(sessionId);
  const root = resolve(directory);
  const candidate = resolve(root, `${sessionId}.jsonl`);
  if (!candidate.startsWith(`${root}${sep}`)) {
    throw new Error("Pi session path escapes the session directory");
  }
  return candidate;
}

function validateSessionId(sessionId: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(sessionId) || sessionId === "." || sessionId === ".." || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(sessionId)) {
    throw new Error("invalid Pi session id");
  }
  return sessionId;
}
