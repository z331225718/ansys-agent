// AI-HFSS Workstation — host half.
//
// Two jobs:
//   1. Mount the loader row so the runtime client-modules service finds this
//      package's `dsh.client` declaration and serves the browser half.
//   2. Serve a small local control API under /api/ai-hfss so the panel can
//      START / STOP the workbench dashboard itself instead of asking the user
//      to run a command first.
//
// The control API is deliberately narrow and local:
//   GET  /api/ai-hfss/status   -> service state + workbench URL + log path
//   GET  /api/ai-hfss/log      -> bounded tail of the dashboard log
//   POST /api/ai-hfss/start    -> spawn the dashboard (idempotent)
//   POST /api/ai-hfss/stop     -> stop the dashboard this machine started
//
// Guards: loopback-only sockets, POSTs must carry the x-ai-hfss header (a
// cross-site form post cannot), and stop only kills a process whose command
// line still looks like this launcher's target.
//
// Everything the launcher needs comes from the adjacent service.json, which
// the installer renders for this machine; nothing is hard-coded here.
import { spawn, spawnSync } from "node:child_process";
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { request as httpRequest } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const name = "ai-hfss-workstation";
export const inject = [];

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
const API_PREFIX = "/api/ai-hfss";
const SPAWN_MARKER = "aedt_agent.agent";
const SPAWN_MARKER_ARGS = ["mission", "web"];

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

/** Route one control request; the handler owns the whole response. */
async function handle(req, res, logger) {
  if (!isLoopback(req)) {
    sendJson(res, 403, { ok: false, error: "ai-hfss control API is loopback-only" });
    return;
  }
  const url = new URL(req.url ?? "/", "http://localhost");
  const action = url.pathname.slice(API_PREFIX.length).replace(/\/$/, "") || "/status";
  let spec;
  try {
    spec = loadSpec();
  } catch (error) {
    sendJson(res, 500, { ok: false, error: error instanceof Error ? error.message : String(error) });
    return;
  }
  const method = req.method ?? "GET";

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
          handle(req, res, ctx.logger).catch((error) => {
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
