// AI-HFSS Workstation — host half.
//
// Three jobs:
//   1. Mount the loader row so the runtime client-modules service finds this
//      package's `dsh.client` declaration and serves the browser half.
//   2. Serve a small local control API under /api/ai-hfss so the panel can
//      START / STOP the workbench dashboard itself instead of asking the user
//      to run a command first.
//   3. Serve the AEDT hand-off API: the AEDT script button reports the open
//      project, the focus page reads that context, and /open creates a DSH
//      session with the `ansys-embedded` preset plus its first message.
//
// The control API is deliberately narrow and local:
//   GET  /api/ai-hfss/status         -> service state + workbench URL + log path
//   GET  /api/ai-hfss/log            -> bounded tail of the dashboard log
//   POST /api/ai-hfss/start          -> spawn the dashboard (idempotent)
//   POST /api/ai-hfss/stop           -> stop the dashboard this machine started
//   POST /api/ai-hfss/aedt/context   -> remember the AEDT project/design/pid/port
//   GET  /api/ai-hfss/aedt/status    -> remembered context + pid/port liveness
//   POST /api/ai-hfss/aedt/open      -> create the embedded session + first prompt
//
// Guards: loopback-only sockets, POSTs must carry the x-ai-hfss header (a
// cross-site form post cannot), and stop only kills a process whose command
// line still looks like this launcher's target. The AEDT script sends the
// x-ai-hfss header too; that is what lets a local script (which cannot forge
// an Origin) use the mutating routes.
//
// Everything the launcher needs comes from the adjacent service.json, which
// the installer renders for this machine; nothing is hard-coded here.
import { randomUUID } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync
} from "node:fs";
import { request as httpRequest } from "node:http";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const name = "ai-hfss-workstation";
export const inject = [];

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
const API_PREFIX = "/api/ai-hfss";
const SPAWN_MARKER = "aedt_agent.agent";
const SPAWN_MARKER_ARGS = ["mission", "web"];

// AEDT hand-off (see docs/ai-hfss-workbench-task-mode-plan.md §7).
const AEDT_PRESET = "ansys-embedded";
const AEDT_CONTEXT_FILE_NAME = "aedt-context.json";
const AEDT_MAX_BODY_BYTES = 64 * 1024;
const AEDT_MAX_PROJECT_PATH = 4096;
// How long a request waits for the (async) sessionController injection before
// failing with an explicit error instead of hanging. Overridable for tests.
const AEDT_SESSION_READY_MS = readPositiveInteger(process.env.AIHFSS_SESSION_READY_TIMEOUT_MS, 2500);
const AEDT_DIR = join(homedir(), ".dsh", "ai-hfss-workstation");

// sessionController is mounted by interactive profiles only, and Cordis
// resolves it asynchronously. Keep a nullable cache and inject it on first use
// (NOT in apply: the control API must not depend on an optional service at
// mount time, and a headless profile simply never caches it).
let sessionController = null;
let sessionLookup = null;

function readPositiveInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function aedtContextPath() {
  // Test/override hook: keeps the real ~/.dsh file out of automated runs.
  const override = process.env.AIHFSS_AEDT_CONTEXT_FILE;
  if (typeof override === "string" && override.trim() !== "") return override.trim();
  return join(AEDT_DIR, AEDT_CONTEXT_FILE_NAME);
}

function aedtError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function isValidPid(value) {
  return Number.isInteger(value) && value > 0 && value <= 2147483647;
}

function isValidPort(value) {
  return Number.isInteger(value) && value >= 1 && value <= 65535;
}

/**
 * Is a loopback TCP port accepting connections?
 *
 * Uses node:http (as the AEDT contract asks) but answers on the TCP connect,
 * not on an HTTP response: AEDT's gRPC endpoint speaks HTTP/2 and would never
 * answer an HTTP/1.1 GET, while a refused connection still fails fast.
 */
function probePort(port, timeoutMs = 800) {
  if (!isValidPort(port)) return Promise.resolve(false);
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    let req;
    try {
      req = httpRequest({ host: "127.0.0.1", port, path: "/", method: "GET", timeout: timeoutMs }, (res) => {
        res.resume();
        done(true);
      });
    } catch {
      done(false);
      return;
    }
    req.on("socket", (socket) => {
      if (!socket.connecting) {
        done(true);
        req.destroy();
        return;
      }
      socket.once("connect", () => {
        done(true);
        req.destroy();
      });
    });
    req.on("timeout", () => {
      req.destroy();
      done(false);
    });
    req.on("error", () => done(false));
    req.end();
  });
}

function readAedtContext() {
  const file = aedtContextPath();
  if (!existsSync(file)) return null;
  try {
    const value = JSON.parse(readFileSync(file, "utf8"));
    if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
    return value;
  } catch {
    // A half-written or hand-edited file means "no context", never a crash.
    return null;
  }
}

function writeAedtContext(context) {
  const file = aedtContextPath();
  ensureDir(file);
  const temporary = file + ".tmp";
  writeFileSync(temporary, JSON.stringify(context, null, 2) + "\n", "utf8");
  renameSync(temporary, file);
  return context;
}

function optionalInteger(value, code, label, isValid) {
  if (value === undefined || value === null || value === "") return undefined;
  const parsed = typeof value === "number" ? value : Number(String(value).trim());
  if (!Number.isInteger(parsed) || !isValid(parsed)) {
    throw aedtError(code, label + " is invalid: " + String(value));
  }
  return parsed;
}

/**
 * Merge an AEDT report into the stored context.
 *
 * project_path is mandatory for /aedt/context; /aedt/open may omit it and keep
 * the previously reported project (the script sometimes knows only the design).
 */
function mergeAedtContext(previous, body, options) {
  const prior = previous !== null && typeof previous === "object" ? previous : {};
  const source = body !== null && typeof body === "object" ? body : {};
  const reportedProject = typeof source.project_path === "string" ? source.project_path.trim() : "";
  const projectPath = reportedProject !== "" ? reportedProject : options.requireProject ? "" : String(prior.project_path ?? "").trim();
  if (projectPath === "") {
    throw aedtError("aedt/invalid-project-path", "project_path is required");
  }
  if (projectPath.length > AEDT_MAX_PROJECT_PATH) {
    throw aedtError("aedt/invalid-project-path", "project_path is longer than " + String(AEDT_MAX_PROJECT_PATH) + " characters");
  }
  const designName =
    typeof source.design_name === "string" && source.design_name.trim() !== ""
      ? source.design_name.trim()
      : typeof prior.design_name === "string"
        ? prior.design_name
        : "";
  const pid =
    optionalInteger(source.pid, "aedt/invalid-pid", "pid", isValidPid) ??
    (isValidPid(prior.pid) ? prior.pid : null);
  const grpcPort =
    optionalInteger(source.grpc_port, "aedt/invalid-grpc-port", "grpc_port", isValidPort) ??
    (isValidPort(prior.grpc_port) ? prior.grpc_port : null);
  return {
    project_path: projectPath,
    design_name: designName,
    pid,
    grpc_port: grpcPort,
    updated_at: new Date().toISOString()
  };
}

/** Read one JSON object body with a hard size cap; an empty body is {}. */
function readJsonBody(req, limit = AEDT_MAX_BODY_BYTES) {
  return new Promise((resolve, reject) => {
    if (req === null || req === undefined || typeof req.on !== "function") {
      resolve({});
      return;
    }
    const chunks = [];
    let size = 0;
    let settled = false;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };
    req.on("data", (chunk) => {
      if (settled) return;
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      size += buffer.length;
      if (size > limit) {
        fail(aedtError("aedt/body-too-large", "request body exceeds " + String(limit) + " bytes"));
        return;
      }
      chunks.push(buffer);
    });
    req.on("end", () => {
      if (settled) return;
      settled = true;
      const text = Buffer.concat(chunks).toString("utf8").trim();
      if (text === "") {
        resolve({});
        return;
      }
      let value;
      try {
        value = JSON.parse(text);
      } catch (error) {
        reject(aedtError("aedt/invalid-body", "request body is not valid JSON: " + error.message));
        return;
      }
      if (value === null || typeof value !== "object" || Array.isArray(value)) {
        reject(aedtError("aedt/invalid-body", "request body must be a JSON object"));
        return;
      }
      resolve(value);
    });
    req.on("error", (error) => fail(error));
    req.on("aborted", () => fail(aedtError("aedt/body-aborted", "request body was aborted")));
  });
}

/** The session working directory: the repo when service.json exists, else the runtime dir. */
function aedtSessionCwd() {
  try {
    const spec = loadSpec();
    if (typeof spec.repoRoot === "string" && spec.repoRoot !== "" && existsSync(spec.repoRoot)) {
      return spec.repoRoot;
    }
  } catch {
    // service.json is optional for the AEDT routes (tests, first boot).
  }
  try {
    mkdirSync(AEDT_DIR, { recursive: true });
  } catch {
    /* best effort: the caller surfaces an unreachable cwd as a session error */
  }
  return AEDT_DIR;
}

/**
 * Resolve the sessionController service on demand.
 *
 * Deliberately not injected from apply(): mounting the control API must not
 * depend on an optional service, and a profile without it should fail /open
 * with an explicit code instead of refusing to load the whole plugin.
 */
function waitForSessionController(ctx) {
  if (sessionController !== null) return Promise.resolve(sessionController);
  if (sessionLookup !== null) return sessionLookup;
  const inject = ctx !== null && ctx !== undefined && typeof ctx.inject === "function" ? ctx.inject : null;
  if (inject === null) return Promise.resolve(null);
  sessionLookup = new Promise((resolve) => {
    let done = false;
    let disposable = null;
    let timer = null;
    const finish = (value) => {
      if (done) return;
      done = true;
      if (timer !== null) clearTimeout(timer);
      resolve(value);
    };
    const adopt = (candidate) => {
      if (candidate !== null && typeof candidate === "object" && typeof candidate.create === "function" && typeof candidate.prompt === "function") {
        sessionController = candidate;
      }
      return sessionController;
    };
    try {
      disposable = inject.call(ctx, ["sessionController"], (sessionCtx) => {
        const service = adopt(sessionCtx?.sessionController ?? sessionCtx ?? null);
        // A callback without a usable service must not pin the cache to null:
        // the next /open retries the injection.
        if (service === null) sessionLookup = null;
        finish(service);
      });
    } catch {
      finish(null);
    }
    timer = setTimeout(() => {
      if (!done) {
        // Let the next /open retry: the service may mount later.
        sessionLookup = null;
        try {
          disposable?.dispose?.();
        } catch {
          /* ignore */
        }
      }
      finish(sessionController);
    }, AEDT_SESSION_READY_MS);
  });
  return sessionLookup;
}

function defaultAedtPrompt(context) {
  const project =
    context !== null && typeof context.project_path === "string" && context.project_path !== ""
      ? context.project_path
      : "(未上报)";
  const design =
    context !== null && typeof context.design_name === "string" && context.design_name !== ""
      ? context.design_name
      : "(未上报)";
  return [
    "当前 AEDT 工程：" + project,
    "当前设计：" + design,
    "",
    "请按顺序开始，不要跳步：",
    "1. 先调用 mcp__aedt__check_aedt_status 查看本机 AEDT 会话状态。",
    "2. 再用 mcp__aedt__connect_to_aedt 连接上面的工程；已有会话就复用，不要新建工程。",
    "3. 连接后报告：工程路径、设计名、求解类型/Setup/Sweep 等求解设置，以及是否已有求解结果。",
    "4. 报告完成后等待我的提问；不要启动求解，也不要修改模型。",
    "",
    "报告遵守有界输出：只给指标（最差 SDD11/SDD21、TDR 峰值偏差等）与 artifact 路径，不要贴原始 S/SDD 或 TDR 曲线；需要修改模型时先给 preview 方案等我确认。"
  ].join("\n");
}

/** The focus URL host comes from the request, never from a hard-coded port. */
function requestHost(req) {
  const host = typeof req.headers?.host === "string" ? req.headers.host.trim() : "";
  if (host === "" || !/^[A-Za-z0-9.\-:\[\]]+$/.test(host)) {
    throw aedtError("aedt/invalid-host", "the request Host header is required to build the focus URL");
  }
  return host;
}

function focusUrl(host, sessionId) {
  return "http://" + host + "/?aihfss=focus&session=" + encodeURIComponent(sessionId);
}

async function aedtStatusPayload() {
  const context = readAedtContext();
  const pid = context !== null && isValidPid(context.pid) ? context.pid : null;
  const port = context !== null && isValidPort(context.grpc_port) ? context.grpc_port : null;
  return {
    ok: true,
    context,
    pid_alive: pid !== null && pidAlive(pid),
    // Probe only when AEDT reported a gRPC port; a random port must not make
    // the page wait for a timeout on every poll.
    port_reachable: port !== null ? await probePort(port) : false
  };
}

/** Read and shape-check the rendered service specification. */
function loadSpec() {
  const raw = JSON.parse(readFileSync(join(MODULE_DIR, "service.json"), "utf8"));
  if (typeof raw.python !== "string" || typeof raw.repoRoot !== "string") {
    throw new Error("ai-hfss-workstation: service.json must carry python and repoRoot strings");
  }
  const port = Number(raw.port);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error("ai-hfss-workstation: service.json port must be a valid TCP port");
  }
  const host = typeof raw.host === "string" && raw.host.length > 0 ? raw.host : "127.0.0.1";
  const workbenchPath = typeof raw.workbenchPath === "string" && raw.workbenchPath.startsWith("/")
    ? raw.workbenchPath
    : "/workbench";
  return {
    repoRoot: raw.repoRoot,
    python: raw.python,
    port,
    host,
    workbenchPath,
    workbenchUrl: "http://" + host + ":" + String(port) + workbenchPath,
    missionsDb: typeof raw.missionsDb === "string" ? raw.missionsDb : "",
    profileConfig: typeof raw.profileConfig === "string" ? raw.profileConfig : "",
    logFile: typeof raw.logFile === "string" ? raw.logFile : "",
    startupTimeoutMs: Number.isFinite(Number(raw.startupTimeoutMs)) ? Number(raw.startupTimeoutMs) : 45000,
    pidFile: typeof raw.pidFile === "string" ? raw.pidFile : ""
  };
}

/** One bounded loopback probe: is this URL answering? */
function probe(url, timeoutMs = 1500) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const req = httpRequest(url, { method: "GET", timeout: timeoutMs }, (res) => {
      res.resume();
      done(true);
    });
    req.on("timeout", () => {
      req.destroy();
      done(false);
    });
    req.on("error", () => done(false));
    req.end();
  });
}

function ensureDir(path) {
  if (path === "") return;
  mkdirSync(dirname(path), { recursive: true });
}

function isLoopback(req) {
  const address = req.socket?.remoteAddress ?? "";
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1" || /^127\./.test(address);
}

/** Restrict mutating calls to same-origin callers that send our header. */
function isTrustedMutation(req) {
  if (req.headers["x-ai-hfss"] !== "1") return false;
  if (req.headers["sec-fetch-site"] === "cross-site") return false;
  const origin = req.headers.origin;
  if (origin === undefined) return true;
  try {
    return new URL(origin).host === req.headers.host;
  } catch {
    return false;
  }
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "content-length": Buffer.byteLength(body)
  });
  res.end(body);
}

function readTail(file, lines) {
  if (file === "" || !existsSync(file)) return "";
  try {
    const parts = readFileSync(file, "utf8").split(/\r?\n/);
    return parts.slice(Math.max(0, parts.length - lines)).join("\n").trimEnd();
  } catch (error) {
    return "(cannot read log: " + (error instanceof Error ? error.message : String(error)) + ")";
  }
}

/** Windows: the command line of a pid, or "" when it cannot be read. */
function commandLineOf(pid) {
  const script = "(Get-CimInstance Win32_Process -Filter 'ProcessId=" + String(pid) + "').CommandLine";
  const result = spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 8000
  });
  if (result.status !== 0) return "";
  return (result.stdout ?? "").trim();
}

function killTree(pid) {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, timeout: 15000 });
  } else {
    spawnSync("kill", ["-TERM", String(pid)], { timeout: 8000 });
  }
}

function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** Launcher state for this host process. */
const state = { childPid: null, startedAt: null, lastError: null, lastExit: null, stopping: false };

function readPidFile(spec) {
  if (spec.pidFile === "" || !existsSync(spec.pidFile)) return null;
  try {
    const value = Number(readFileSync(spec.pidFile, "utf8").trim());
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function writePidFile(spec, pid) {
  ensureDir(spec.pidFile);
  try {
    writeFileSync(spec.pidFile, String(pid), "utf8");
  } catch {
    /* best effort: the pid file only helps across DSH restarts */
  }
}

function clearPidFile(spec) {
  if (spec.pidFile === "" || !existsSync(spec.pidFile)) return;
  try {
    unlinkSync(spec.pidFile);
  } catch {
    /* ignore */
  }
}

/** Build the exact dashboard command this machine was installed for. */
function buildArgs(spec) {
  const args = ["-m", "aedt_agent.agent", "mission", "web", "--host", spec.host, "--port", String(spec.port)];
  if (spec.missionsDb !== "") {
    ensureDir(spec.missionsDb);
    args.push("--db", spec.missionsDb);
  }
  if (spec.profileConfig !== "") {
    const absolute = spec.profileConfig.startsWith("/") || /^[A-Za-z]:/.test(spec.profileConfig)
      ? spec.profileConfig
      : join(spec.repoRoot, spec.profileConfig);
    // The reviewed-loop profile is machine-local and often absent; the
    // dashboard runs fine without it (it only changes worker execution).
    if (existsSync(absolute)) args.push("--profile", spec.profileConfig);
  }
  return args;
}

function spawnDashboard(spec, logger) {
  if (!existsSync(spec.python)) return { ok: false, error: "python not found: " + spec.python };
  if (!existsSync(spec.repoRoot)) return { ok: false, error: "repository not found: " + spec.repoRoot };
  ensureDir(spec.logFile);
  let fd = -1;
  try {
    fd = openSync(spec.logFile, "w");
  } catch (error) {
    return { ok: false, error: "cannot open log: " + (error instanceof Error ? error.message : String(error)) };
  }
  let child;
  try {
    child = spawn(spec.python, buildArgs(spec), {
      cwd: spec.repoRoot,
      windowsHide: true,
      stdio: ["ignore", fd, fd]
    });
  } catch (error) {
    closeSync(fd);
    return { ok: false, error: "cannot spawn: " + (error instanceof Error ? error.message : String(error)) };
  }
  closeSync(fd);
  state.childPid = child.pid ?? null;
  state.startedAt = state.childPid === null ? null : new Date().toISOString();
  state.lastError = null;
  state.lastExit = null;
  if (state.childPid !== null) writePidFile(spec, state.childPid);
  child.on("exit", (code, signal) => {
    const intentional = state.stopping;
    state.stopping = false;
    state.lastExit = { code, signal, at: new Date().toISOString(), intentional };
    if (state.childPid === child.pid) {
      state.childPid = null;
      state.startedAt = null;
    }
    clearPidFile(spec);
    const message = "[ai-hfss-workstation] dashboard exited code=" + String(code) + " signal=" + String(signal);
    // A stop we asked for is not a warning.
    if (!intentional && logger?.warn) logger.warn(message);
  });
  child.on("error", (error) => {
    state.lastError = error instanceof Error ? error.message : String(error);
    state.childPid = null;
    state.startedAt = null;
    clearPidFile(spec);
    if (logger?.warn) logger.warn(error);
  });
  return { ok: true, pid: state.childPid };
}

function stopDashboard(spec, logger) {
  const candidates = new Set();
  if (state.childPid !== null) candidates.add(state.childPid);
  const fromFile = readPidFile(spec);
  if (fromFile !== null) candidates.add(fromFile);
  const killed = [];
  const refused = [];
  for (const pid of candidates) {
    if (!pidAlive(pid)) {
      refused.push("pid " + String(pid) + " is not running");
      continue;
    }
    const commandLine = commandLineOf(pid);
    const looksOurs =
      commandLine !== "" &&
      commandLine.includes(SPAWN_MARKER) &&
      SPAWN_MARKER_ARGS.every((token) => commandLine.includes(token));
    if (!looksOurs) {
      refused.push("pid " + String(pid) + " is not an AI-HFSS dashboard process");
      continue;
    }
    state.stopping = true;
    killTree(pid);
    killed.push(pid);
  }
  if (state.childPid !== null && killed.includes(state.childPid)) {
    state.childPid = null;
    state.startedAt = null;
  }
  clearPidFile(spec);
  if (killed.length > 0 && logger?.info) {
    logger.info("[ai-hfss-workstation] dashboard stopped (pid " + killed.join(", ") + ")");
  }
  return { ok: killed.length > 0, killed, refused };
}

async function statusPayload(spec) {
  const reachable = await probe(spec.workbenchUrl);
  const childAlive = state.childPid !== null && pidAlive(state.childPid);
  const pidFromFile = readPidFile(spec);
  return {
    ok: true,
    reachable,
    running: reachable || childAlive,
    owned: childAlive,
    pid: childAlive ? state.childPid : pidFromFile,
    startedAt: state.startedAt,
    lastExit: state.lastExit,
    workbenchUrl: spec.workbenchUrl,
    service: {
      host: spec.host,
      port: spec.port,
      missionsDb: spec.missionsDb,
      repoRoot: spec.repoRoot,
      python: spec.python,
      logFile: spec.logFile,
      startupTimeoutMs: spec.startupTimeoutMs
    },
    error: state.lastError
  };
}

function aedtFailure(error, fallbackCode = "aedt/request-failed") {
  const code =
    error !== null &&
    typeof error === "object" &&
    typeof error.code === "string" &&
    error.code.startsWith("aedt/")
      ? error.code
      : fallbackCode;
  return { ok: false, code, error: error instanceof Error ? error.message : String(error) };
}

/** Route one AEDT hand-off request; the handler owns the whole response. */
async function handleAedt(req, res, action, method, ctx) {
  if (action === "/aedt/context") {
    if (method !== "POST") {
      sendJson(res, 405, { ok: false, code: "aedt/method-not-allowed", error: "aedt/context is POST" });
      return;
    }
    if (!isTrustedMutation(req)) {
      sendJson(res, 403, {
        ok: false,
        code: "aedt/forbidden",
        error: "aedt/context requires the x-ai-hfss header from the same origin"
      });
      return;
    }
    let body;
    try {
      body = await readJsonBody(req);
    } catch (error) {
      sendJson(res, error?.code === "aedt/body-too-large" ? 413 : 400, aedtFailure(error));
      return;
    }
    let context;
    try {
      context = mergeAedtContext(readAedtContext(), body, { requireProject: true });
    } catch (error) {
      sendJson(res, 400, aedtFailure(error));
      return;
    }
    try {
      writeAedtContext(context);
    } catch (error) {
      sendJson(res, 500, aedtFailure(error, "aedt/context-write-failed"));
      return;
    }
    sendJson(res, 200, { ok: true, context });
    return;
  }

  if (action === "/aedt/status") {
    if (method !== "GET" && method !== "HEAD") {
      sendJson(res, 405, { ok: false, code: "aedt/method-not-allowed", error: "aedt/status is GET" });
      return;
    }
    let payload;
    try {
      payload = await aedtStatusPayload();
    } catch (error) {
      // The status bar must always get an answer, even on a weird context file.
      payload = { ok: true, context: null, pid_alive: false, port_reachable: false, error: String(error) };
    }
    sendJson(res, 200, payload);
    return;
  }

  if (action === "/aedt/open") {
    if (method !== "POST") {
      sendJson(res, 405, { ok: false, code: "aedt/method-not-allowed", error: "aedt/open is POST" });
      return;
    }
    if (!isTrustedMutation(req)) {
      sendJson(res, 403, {
        ok: false,
        code: "aedt/forbidden",
        error: "aedt/open requires the x-ai-hfss header from the same origin"
      });
      return;
    }
    let body;
    try {
      body = await readJsonBody(req);
    } catch (error) {
      sendJson(res, error?.code === "aedt/body-too-large" ? 413 : 400, aedtFailure(error));
      return;
    }
    let host;
    try {
      host = requestHost(req);
    } catch (error) {
      sendJson(res, 400, aedtFailure(error));
      return;
    }
    let context = readAedtContext();
    if (typeof body.project_path === "string" && body.project_path.trim() !== "") {
      try {
        context = mergeAedtContext(context, body, { requireProject: true });
      } catch (error) {
        sendJson(res, 400, aedtFailure(error));
        return;
      }
      try {
        writeAedtContext(context);
      } catch (error) {
        sendJson(res, 500, aedtFailure(error, "aedt/context-write-failed"));
        return;
      }
    }

    const controller = await waitForSessionController(ctx);
    if (controller === null) {
      sendJson(res, 500, {
        ok: false,
        code: "aedt/session-unavailable",
        error:
          "sessionController is not available; the DSH web profile must mount " +
          "@deepseek-ai/dsh-api-session-controller for /api/ai-hfss/aedt/open"
      });
      return;
    }

    let sessionId = "";
    try {
      const created = await controller.create({ cwd: aedtSessionCwd(), agentPreset: AEDT_PRESET });
      sessionId =
        created !== null && typeof created === "object" && typeof created.sessionId === "string"
          ? created.sessionId.trim()
          : "";
      if (sessionId === "") {
        throw aedtError("aedt/session-create-failed", "sessionController.create returned no sessionId");
      }
    } catch (error) {
      sendJson(res, 500, aedtFailure(error, "aedt/session-create-failed"));
      return;
    }

    const requestId = randomUUID();
    const customPrompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
    const text = customPrompt !== "" ? customPrompt : defaultAedtPrompt(context);
    try {
      const ack = await controller.prompt(
        { sessionId, requestId, content: [{ type: "text", text }] },
        { kind: "user", rpcId: requestId }
      );
      if (ack !== null && typeof ack === "object" && ack.accepted === false) {
        throw aedtError("aedt/prompt-rejected", "sessionController.prompt did not accept the first message");
      }
    } catch (error) {
      sendJson(res, 500, aedtFailure(error, "aedt/prompt-rejected"));
      return;
    }

    sendJson(res, 200, { ok: true, session_id: sessionId, focus_url: focusUrl(host, sessionId) });
    return;
  }

  sendJson(res, 404, { ok: false, error: "unknown ai-hfss aedt action " + JSON.stringify(action) });
}

/** Route one control request; the handler owns the whole response. */
async function handle(req, res, logger, ctx) {
  if (!isLoopback(req)) {
    sendJson(res, 403, { ok: false, error: "ai-hfss control API is loopback-only" });
    return;
  }
  const url = new URL(req.url ?? "/", "http://localhost");
  const action = url.pathname.slice(API_PREFIX.length).replace(/\/$/, "") || "/status";
  const method = req.method ?? "GET";
  // AEDT hand-off routes do not need service.json (they may run before the
  // dashboard is installed); they answer even when the launcher spec is absent.
  if (action === "/aedt" || action.startsWith("/aedt/")) {
    await handleAedt(req, res, action, method, ctx);
    return;
  }
  let spec;
  try {
    spec = loadSpec();
  } catch (error) {
    sendJson(res, 500, { ok: false, error: error instanceof Error ? error.message : String(error) });
    return;
  }

  if (action === "/status") {
    if (method !== "GET" && method !== "HEAD") {
      sendJson(res, 405, { ok: false, error: "status is GET" });
      return;
    }
    sendJson(res, 200, await statusPayload(spec));
    return;
  }

  if (action === "/log") {
    if (method !== "GET") {
      sendJson(res, 405, { ok: false, error: "log is GET" });
      return;
    }
    const tail = Math.min(Math.max(Number(url.searchParams.get("tail") ?? 120) || 120, 1), 2000);
    sendJson(res, 200, { ok: true, logFile: spec.logFile, tail: readTail(spec.logFile, tail) });
    return;
  }

  if (action === "/start") {
    if (method !== "POST") {
      sendJson(res, 405, { ok: false, error: "start is POST" });
      return;
    }
    if (!isTrustedMutation(req)) {
      sendJson(res, 403, { ok: false, error: "start requires the x-ai-hfss header from the same origin" });
      return;
    }
    const already = await statusPayload(spec);
    if (already.running) {
      sendJson(res, 200, { ...already, ok: true, alreadyRunning: true });
      return;
    }
    const started = spawnDashboard(spec, logger);
    if (!started.ok) {
      sendJson(res, 500, { ok: false, error: started.error });
      return;
    }
    sendJson(res, 200, { ok: true, pid: started.pid, workbenchUrl: spec.workbenchUrl });
    return;
  }

  if (action === "/stop") {
    if (method !== "POST") {
      sendJson(res, 405, { ok: false, error: "stop is POST" });
      return;
    }
    if (!isTrustedMutation(req)) {
      sendJson(res, 403, { ok: false, error: "stop requires the x-ai-hfss header from the same origin" });
      return;
    }
    const outcome = stopDashboard(spec, logger);
    const after = await statusPayload(spec);
    sendJson(res, outcome.ok ? 200 : 409, { ...outcome, ...after, ok: outcome.ok });
    return;
  }

  sendJson(res, 404, { ok: false, error: "unknown ai-hfss action " + JSON.stringify(action) });
}

export function apply(ctx) {
  // The control API only exists where a web server does; headless profiles
  // mount this plugin and simply never see the routes.
  ctx.inject(["webServer"], (webCtx) => {
    webCtx.effect(() => {
      const dispose = webCtx.webServer.register({
        kind: "prefix",
        path: API_PREFIX,
        handler: (req, res) => {
          handle(req, res, ctx.logger, ctx).catch((error) => {
            if (ctx.logger?.warn) ctx.logger.warn(error);
            if (!res.headersSent) sendJson(res, 500, { ok: false, error: String(error) });
            else res.destroy();
          });
        }
      });
      return () => {
        dispose();
        // Stop a dashboard this host owns when the plugin unloads.
        try {
          if (state.childPid !== null) stopDashboard(loadSpec(), ctx.logger);
        } catch {
          /* nothing to clean up */
        }
      };
    }, "ai-hfss-workstation: control API");
  });
}
