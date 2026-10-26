// Host-half test for the AI-HFSS Workstation plugin.
//
// index.js registers /api/ai-hfss/* on the DSH web server. This test mounts
// the real module against a fake Cordis context, captures the registered
// route, and drives it with fake req/res objects — so the guards, the status
// shape, and the spawn/exit path are exercised without a live DSH.
//
// Run: node tests/dsh/ai_hfss_host_test.mjs
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const bundleDir = join(repo, "integrations", "dsh", "bundles", "ai-hfss-workstation");

function staging(spec) {
  const dir = mkdtempSync(join(tmpdir(), "ai-hfss-host-"));
  copyFileSync(join(bundleDir, "index.js"), join(dir, "index.js"));
  writeFileSync(join(dir, "service.json"), JSON.stringify(spec, null, 2) + "\n", "utf8");
  return dir;
}

function baseSpec(overrides = {}) {
  const runtime = mkdtempSync(join(tmpdir(), "ai-hfss-run-"));
  return {
    repoRoot: runtime,
    // A real, absolute binary: the launcher refuses to spawn a missing one.
    // node itself exits immediately on "-m", which exercises the exit path.
    python: process.execPath,
    host: "127.0.0.1",
    port: 8799,
    workbenchPath: "/workbench",
    missionsDb: join(runtime, "missions.db").replaceAll("\\", "/"),
    logFile: join(runtime, "dashboard.log").replaceAll("\\", "/"),
    pidFile: join(runtime, "dashboard.pid").replaceAll("\\", "/"),
    profileConfig: "config/execution_profiles/local_real_aedt.json",
    startupTimeoutMs: 4000,
    ...overrides
  };
}

/** Mount the module and capture its control-API handler. */
async function mount(dir) {
  const moduleUrl = pathToFileURL(join(dir, "index.js")).href + "?v=" + String(Date.now());
  const mod = await import(moduleUrl);
  let handler = null;
  let teardown = null;
  const warnings = [];
  const fakeWebCtx = {
    effect: (fn) => {
      teardown = fn();
      return () => {};
    },
    webServer: {
      register: (route) => {
        handler = route;
        return () => {
          handler = null;
        };
      }
    }
  };
  const ctx = {
    logger: { info: () => {}, warn: (error) => warnings.push(String(error)) },
    inject: (dependencies, callback) => {
      assert.deepEqual(dependencies, ["webServer"], "the control API injects the web server");
      callback(fakeWebCtx);
    }
  };
  mod.apply(ctx);
  assert.ok(handler !== null, "apply must register the control route");
  assert.equal(handler.kind, "prefix");
  assert.equal(handler.path, "/api/ai-hfss");
  return { handler, teardown, warnings, mod };
}

function fakeRequest(method, url, headers = {}, remoteAddress = "127.0.0.1") {
  return { method, url, headers, socket: { remoteAddress } };
}

function fakeResponse() {
  const out = { status: 0, headers: {}, body: "", destroyed: false };
  let settle = () => {};
  const finished = new Promise((resolve) => {
    settle = resolve;
  });
  return {
    out,
    finished,
    headersSent: false,
    writeHead(status, responseHeaders) {
      out.status = status;
      out.headers = responseHeaders ?? {};
      this.headersSent = true;
    },
    end(body) {
      out.body = body ?? "";
      settle();
    },
    destroy() {
      out.destroyed = true;
      settle();
    }
  };
}

async function call(handler, method, url, headers = {}, remoteAddress = "127.0.0.1") {
  const res = fakeResponse();
  // The route handler owns the response lifecycle and does not return its
  // promise, so completion is observed through res.end().
  handler.handler(fakeRequest(method, url, headers, remoteAddress), res);
  await Promise.race([
    res.finished,
    new Promise((_, reject) => setTimeout(() => reject(new Error("control API did not respond")), 15000))
  ]);
  const payload = res.out.body === "" ? null : JSON.parse(res.out.body);
  return { status: res.out.status, payload };
}

const TRUST = { "x-ai-hfss": "1", host: "127.0.0.1:3080" };
const cleanups = [];
try {
  // ---- 1. status shape ---------------------------------------------------
  {
    const dir = staging(baseSpec());
    cleanups.push(dir);
    const { handler } = await mount(dir);
    const status = await call(handler, "GET", "/api/ai-hfss/status");
    assert.equal(status.status, 200);
    assert.equal(status.payload.ok, true);
    assert.equal(status.payload.running, false, "nothing is listening on the test port");
    assert.equal(status.payload.workbenchUrl, "http://127.0.0.1:8799/workbench");
    assert.equal(status.payload.service.port, 8799);
    assert.equal(typeof status.payload.service.logFile, "string");
  }

  // ---- 2. guards ---------------------------------------------------------
  {
    const dir = staging(baseSpec());
    cleanups.push(dir);
    const { handler } = await mount(dir);

    const noHeader = await call(handler, "POST", "/api/ai-hfss/start");
    assert.equal(noHeader.status, 403, "start without the header is refused");
    assert.match(noHeader.payload.error, /x-ai-hfss/);

    const crossSite = await call(handler, "POST", "/api/ai-hfss/start", {
      ...TRUST,
      "sec-fetch-site": "cross-site"
    });
    assert.equal(crossSite.status, 403, "cross-site POSTs are refused");

    const wrongOrigin = await call(handler, "POST", "/api/ai-hfss/start", {
      ...TRUST,
      origin: "http://evil.example"
    });
    assert.equal(wrongOrigin.status, 403, "a foreign Origin is refused");

    const getStart = await call(handler, "GET", "/api/ai-hfss/start");
    assert.equal(getStart.status, 405, "start is POST-only");

    const unknown = await call(handler, "GET", "/api/ai-hfss/nope");
    assert.equal(unknown.status, 404);

    const remote = await call(handler, "GET", "/api/ai-hfss/status", {}, "10.1.2.3");
    assert.equal(remote.status, 403, "the API is loopback-only");
  }

  // ---- 3. stop refuses a process that is not ours ------------------------
  {
    const spec = baseSpec();
    const dir = staging(spec);
    cleanups.push(dir);
    mkdirSync(dirname(spec.pidFile), { recursive: true });
    writeFileSync(spec.pidFile, String(process.pid), "utf8");
    const { handler } = await mount(dir);

    const stopped = await call(handler, "POST", "/api/ai-hfss/stop", TRUST);
    assert.equal(stopped.status, 409, "stop reports it could not stop anything");
    assert.equal(stopped.payload.ok, false);
    assert.equal(stopped.payload.killed.length, 0, "the test process must not be killed");
    assert.ok(
      stopped.payload.refused.some((line) => line.includes("not an AI-HFSS dashboard")),
      "stop explains the refusal: " + JSON.stringify(stopped.payload.refused)
    );
  }

  // ---- 4. spawn a real child and detect its exit -------------------------
  {
    const spec = baseSpec();
    const dir = staging(spec);
    cleanups.push(dir);
    const { handler } = await mount(dir);
    const started = await call(handler, "POST", "/api/ai-hfss/start", TRUST);
    assert.equal(started.status, 200, "start accepted: " + JSON.stringify(started.payload));
    assert.equal(started.payload.ok, true);

    // The staging repoRoot has no aedt_agent package, so the child exits at once.
    let status = null;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      status = await call(handler, "GET", "/api/ai-hfss/status");
      if (status.payload.lastExit !== null) break;
    }
    assert.ok(status.payload.lastExit !== null, "an immediate child exit is reported");
    assert.equal(typeof status.payload.lastExit.code, "number");
    assert.equal(status.payload.running, false, "a dead child does not read as running");
  }

  // ---- 5. missing service.json surfaces as a clear 500 -------------------
  {
    const dir = mkdtempSync(join(tmpdir(), "ai-hfss-host-broken-"));
    cleanups.push(dir);
    copyFileSync(join(bundleDir, "index.js"), join(dir, "index.js"));
    const { handler } = await mount(dir);
    const status = await call(handler, "GET", "/api/ai-hfss/status");
    assert.equal(status.status, 500);
    assert.match(status.payload.error, /service\.json/);
  }

  console.log("ai-hfss workstation host smoke test passed");
} finally {
  for (const dir of cleanups) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      /* best effort */
    }
  }
  void spawnSync;
}
