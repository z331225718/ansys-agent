// Verify the AI-HFSS Workstation control API end to end, without DSH.
//
// Mounts the INSTALLED host half against a fake Cordis context and drives the
// same routes the panel's buttons use: status -> start -> poll -> workbench ->
// stop. Use this when the panel misbehaves, to separate "plugin wiring" from
// "dashboard process" problems.
//
// Usage: node scripts/dsh/verify_ai_hfss_service.mjs [--profile <dir>]
import { existsSync, readFileSync } from "node:fs";
import { request as httpRequest } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const args = process.argv.slice(2);
const profileIndex = args.indexOf("--profile");
const profileDir =
  profileIndex !== -1 && args[profileIndex + 1] !== undefined
    ? args[profileIndex + 1]
    : join(homedir(), ".dsh", "profiles", "desktop");

const bundleDir = join(profileDir, "ai-hfss-workstation-bundle");
if (!existsSync(join(bundleDir, "service.json"))) {
  console.error("not installed: " + bundleDir + " has no service.json");
  console.error("run: .\\scripts\\dsh\\ai_hfss_workstation.ps1 apply");
  process.exit(2);
}

const spec = JSON.parse(readFileSync(join(bundleDir, "service.json"), "utf8"));
const workbenchUrl = "http://" + spec.host + ":" + String(spec.port) + spec.workbenchPath;
console.log("profile:", profileDir);
console.log("workbench:", workbenchUrl);
console.log("missionsDb:", spec.missionsDb);
console.log("logFile:", spec.logFile, "\n");

let handler = null;
const module = await import(pathToFileURL(join(bundleDir, "index.js")).href + "?verify=" + String(Date.now()));
module.apply({
  logger: { info: (m) => console.log("[host]", String(m)), warn: (m) => console.log("[host warn]", String(m)) },
  inject: (dependencies, callback) =>
    callback({
      effect: (fn) => {
        fn();
        return () => {};
      },
      webServer: {
        register: (route) => {
          handler = route;
          return () => {};
        }
      }
    })
});
if (handler === null) {
  console.error("the host half registered no control route");
  process.exit(1);
}

function fakeResponse() {
  let settle = () => {};
  const finished = new Promise((resolve) => {
    settle = resolve;
  });
  const out = { status: 0, body: "" };
  return {
    out,
    finished,
    headersSent: false,
    writeHead(status) {
      out.status = status;
      this.headersSent = true;
    },
    end(body) {
      out.body = body ?? "";
      settle();
    },
    destroy() {
      settle();
    }
  };
}

async function call(method, path, headers = {}) {
  const res = fakeResponse();
  handler.handler({ method, url: path, headers, socket: { remoteAddress: "127.0.0.1" } }, res);
  await res.finished;
  return { status: res.out.status, body: JSON.parse(res.out.body === "" ? "{}" : res.out.body) };
}

const trust = { "x-ai-hfss": "1", host: spec.host + ":" + String(spec.port) };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const probe = (url) =>
  new Promise((resolve) => {
    const req = httpRequest(url, { method: "GET", timeout: 2000 }, (res) => {
      res.resume();
      resolve(res.statusCode ?? 0);
    });
    req.on("error", () => resolve(0));
    req.on("timeout", () => {
      req.destroy();
      resolve(0);
    });
    req.end();
  });

let status = await call("GET", "/api/ai-hfss/status");
console.log("[1] status:", status.status, "| running:", status.body.running);
if (status.body.running) {
  console.log("already running (owned:", status.body.owned, ", pid:", status.body.pid, ") - stopping it first");
  await call("POST", "/api/ai-hfss/stop", trust);
  await sleep(1500);
}

console.log("[2] POST /start");
const started = await call("POST", "/api/ai-hfss/start", trust);
console.log("    ok:", started.body.ok, "| pid:", started.body.pid, "| error:", started.body.error ?? "-");
if (!started.body.ok) process.exit(1);

console.log("[3] polling /status");
const deadline = Date.now() + (spec.startupTimeoutMs ?? 45000);
let reachable = false;
while (Date.now() < deadline) {
  await sleep(1200);
  status = await call("GET", "/api/ai-hfss/status");
  if (status.body.lastExit) {
    console.log("    child exited:", JSON.stringify(status.body.lastExit));
    break;
  }
  if (status.body.reachable) {
    reachable = true;
    break;
  }
  process.stdout.write(".");
}
console.log("\n    reachable:", reachable, "| owned:", status.body.owned, "| pid:", status.body.pid);
console.log("    workbench HTTP:", await probe(workbenchUrl));

if (!reachable) {
  const log = await call("GET", "/api/ai-hfss/log?tail=40");
  console.log("[4] dashboard log tail:\n" + (log.body.tail || "(empty)"));
  await call("POST", "/api/ai-hfss/stop", trust);
  process.exit(1);
}

console.log("[4] POST /stop");
const stopped = await call("POST", "/api/ai-hfss/stop", trust);
console.log("    ok:", stopped.body.ok, "| killed:", JSON.stringify(stopped.body.killed));
for (let attempt = 0; attempt < 20; attempt += 1) {
  await sleep(500);
  status = await call("GET", "/api/ai-hfss/status");
  if (!status.body.reachable && !status.body.owned) break;
}
console.log("    after stop -> reachable:", status.body.reachable, "| owned:", status.body.owned);
console.log("    pid file removed:", !existsSync(spec.pidFile));
console.log("    workbench HTTP:", await probe(workbenchUrl));
console.log("\nOK: the panel's Start/Stop path works on this machine.");
