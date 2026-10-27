// Host-half test for the AEDT hand-off API of the AI-HFSS Workstation plugin.
//
// index.js serves /api/ai-hfss/aedt/context|status|open next to the dashboard
// launcher. This test mounts the REAL module against a fake Cordis context
// (including a fake sessionController), captures the registered route, and
// drives it with fake req/res pairs:
//
//   context -> status (pid alive + port reachable, then dead) -> open (missing
//   service, fake service, rejected prompt, failed create) -> guards -> errors.
//
// Run: node tests/dsh/ai_hfss_aedt_api_test.mjs
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const indexPath = join(repo, "integrations", "dsh", "bundles", "ai-hfss-workstation", "index.js");

// The module writes ~/.dsh/ai-hfss-workstation/aedt-context.json by default;
// point it at a scratch file so the test never touches the real context, and
// keep the session-controller wait short for the "service missing" case.
const work = mkdtempSync(join(tmpdir(), "ai-hfss-aedt-api-"));
const contextFile = join(work, "aedt-context.json");
process.env.AIHFSS_AEDT_CONTEXT_FILE = contextFile;
process.env.AIHFSS_SESSION_READY_TIMEOUT_MS = "40";

const TRUST = { "x-ai-hfss": "1", host: "127.0.0.1:62311" };
const cleanups = [];

/** Mount a fresh module instance and capture its control-API route. */
async function mount(options = {}) {
  const moduleUrl = pathToFileURL(indexPath).href + "?v=" + String(mount.counter + 1);
  mount.counter += 1;
  const mod = await import(moduleUrl);
  let handler = null;
  let teardown = null;
  const warnings = [];
  const sessionCallbacks = [];
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
      if (dependencies.length === 1 && dependencies[0] === "webServer") {
        callback(fakeWebCtx);
        return;
      }
      if (dependencies.length === 1 && dependencies[0] === "sessionController") {
        sessionCallbacks.push(callback);
        // options.session === undefined simulates a profile without the
        // service: the callback never fires and /open must fail explicitly.
        if (options.session !== undefined) callback({ sessionController: options.session });
        return;
      }
      throw new Error("unexpected injection " + JSON.stringify(dependencies));
    }
  };
  mod.apply(ctx);
  assert.ok(handler !== null, "apply must register the control route");
  assert.equal(handler.kind, "prefix");
  assert.equal(handler.path, "/api/ai-hfss");
  return { handler, teardown, warnings, sessionCallbacks };
}
mount.counter = 0;

function fakeRequest(method, url, headers, remoteAddress, body) {
  const chunks = body === undefined ? [] : [Buffer.from(body, "utf8")];
  const stream = Readable.from(chunks);
  stream.method = method;
  stream.url = url;
  stream.headers = headers ?? {};
  stream.socket = { remoteAddress: remoteAddress ?? "127.0.0.1" };
  return stream;
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

async function call(handler, method, url, headers = {}, remoteAddress = "127.0.0.1", body = undefined) {
  const res = fakeResponse();
  handler.handler(fakeRequest(method, url, headers, remoteAddress, body), res);
  await Promise.race([
    res.finished,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("route did not respond: " + method + " " + url)), 15000)
    )
  ]);
  const payload = res.out.body === "" ? null : JSON.parse(res.out.body);
  return { status: res.out.status, payload };
}

/** A port with nothing listening on it. */
async function closedPort() {
  const server = createServer(() => {});
  const port = await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
  await new Promise((resolve) => server.close(resolve));
  return port;
}

/** A live loopback HTTP server (the "gRPC port" probe target). */
async function liveServer() {
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok");
  });
  const port = await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
  cleanups.push(() => new Promise((resolve) => server.close(resolve)));
  return port;
}

async function postContext(handler, body) {
  return call(handler, "POST", "/api/ai-hfss/aedt/context", TRUST, "127.0.0.1", JSON.stringify(body));
}

try {
  const livePort = await liveServer();
  // A pid that existed but is already gone: its status is definitively dead.
  const deadPid = spawnSync(process.execPath, ["-e", "0"]).pid;
  const deadPort = await closedPort();
  const projectPath = "C:\\proj\\demo.aedt";

  // ---- 1. /aedt/context writes the file --------------------------------
  {
    const { handler } = await mount();
    const saved = await postContext(handler, {
      project_path: projectPath,
      design_name: "HFSSDesign1",
      pid: process.pid,
      grpc_port: livePort
    });
    assert.equal(saved.status, 200, JSON.stringify(saved.payload));
    assert.equal(saved.payload.ok, true);
    assert.equal(saved.payload.context.project_path, projectPath);
    assert.equal(saved.payload.context.design_name, "HFSSDesign1");
    assert.equal(saved.payload.context.pid, process.pid);
    assert.equal(saved.payload.context.grpc_port, livePort);
    assert.ok(!Number.isNaN(Date.parse(saved.payload.context.updated_at)), "updated_at is an ISO timestamp");
    assert.ok(existsSync(contextFile), "the context is written to the runtime file");
    const onDisk = JSON.parse(readFileSync(contextFile, "utf8"));
    assert.deepEqual(onDisk, saved.payload.context, "the response is exactly what landed on disk");

    // Only the project is new: the previous pid/port/design are preserved.
    const merged = await postContext(handler, { project_path: "C:\\proj\\other.aedt" });
    assert.equal(merged.status, 200);
    assert.equal(merged.payload.context.project_path, "C:\\proj\\other.aedt");
    assert.equal(merged.payload.context.design_name, "HFSSDesign1");
    assert.equal(merged.payload.context.pid, process.pid);
    assert.equal(merged.payload.context.grpc_port, livePort);
  }

  // ---- 2. /aedt/status reflects pid and port liveness ------------------
  {
    const { handler } = await mount();
    await postContext(handler, {
      project_path: projectPath,
      design_name: "HFSSDesign1",
      pid: process.pid,
      grpc_port: livePort
    });
    const alive = await call(handler, "GET", "/api/ai-hfss/aedt/status");
    assert.equal(alive.status, 200);
    assert.equal(alive.payload.ok, true);
    assert.equal(alive.payload.pid_alive, true, "the test process pid is alive");
    assert.equal(alive.payload.port_reachable, true, "the live server port answers");
    assert.equal(alive.payload.context.project_path, projectPath);

    await postContext(handler, {
      project_path: projectPath,
      pid: deadPid,
      grpc_port: deadPort
    });
    const dead = await call(handler, "GET", "/api/ai-hfss/aedt/status");
    assert.equal(dead.status, 200);
    assert.equal(dead.payload.pid_alive, false, "an exited child pid is not alive");
    assert.equal(dead.payload.port_reachable, false, "a closed port is not reachable");

    // No context file at all: a well-shaped "unknown" answer, not an error.
    rmSync(contextFile, { force: true });
    const none = await call(handler, "GET", "/api/ai-hfss/aedt/status");
    assert.equal(none.status, 200);
    assert.equal(none.payload.context, null);
    assert.equal(none.payload.pid_alive, false);
    assert.equal(none.payload.port_reachable, false);

    // A corrupt file is treated as "no context" and never 500s the status bar.
    writeFileSync(contextFile, "{not json", "utf8");
    const corrupt = await call(handler, "GET", "/api/ai-hfss/aedt/status");
    assert.equal(corrupt.status, 200);
    assert.equal(corrupt.payload.context, null);
    rmSync(contextFile, { force: true });
  }

  // ---- 3. guards -------------------------------------------------------
  {
    const { handler } = await mount();
    const noHeader = await call(handler, "POST", "/api/ai-hfss/aedt/context", {}, "127.0.0.1",
      JSON.stringify({ project_path: projectPath }));
    assert.equal(noHeader.status, 403, "context without the x-ai-hfss header is refused");
    assert.match(noHeader.payload.error, /x-ai-hfss/);

    const crossSite = await call(handler, "POST", "/api/ai-hfss/aedt/context",
      { ...TRUST, "sec-fetch-site": "cross-site" }, "127.0.0.1",
      JSON.stringify({ project_path: projectPath }));
    assert.equal(crossSite.status, 403, "cross-site POSTs are refused");

    const foreignOrigin = await call(handler, "POST", "/api/ai-hfss/aedt/open",
      { ...TRUST, origin: "http://evil.example" }, "127.0.0.1", "{}");
    assert.equal(foreignOrigin.status, 403, "a foreign Origin is refused");

    const remote = await call(handler, "GET", "/api/ai-hfss/aedt/status", {}, "10.1.2.3");
    assert.equal(remote.status, 403, "the AEDT API is loopback-only");

    const getOpen = await call(handler, "GET", "/api/ai-hfss/aedt/open");
    assert.equal(getOpen.status, 405, "open is POST-only");

    const unknown = await call(handler, "GET", "/api/ai-hfss/aedt/nope");
    assert.equal(unknown.status, 404);
  }

  // ---- 4. /aedt/open without sessionController fails loudly ------------
  {
    const { handler, sessionCallbacks } = await mount();
    const opened = await call(handler, "POST", "/api/ai-hfss/aedt/open", TRUST, "127.0.0.1",
      JSON.stringify({ project_path: projectPath }));
    assert.equal(opened.status, 500, "missing sessionController is a hard failure");
    assert.equal(opened.payload.ok, false);
    assert.equal(opened.payload.code, "aedt/session-unavailable");
    assert.equal(sessionCallbacks.length, 1, "the service was looked up once");
    // The context is still reported even though the session failed.
    assert.equal(JSON.parse(readFileSync(contextFile, "utf8")).project_path, projectPath);
  }

  // ---- 5. /aedt/open with a fake sessionController ---------------------
  {
    const creates = [];
    const prompts = [];
    const session = {
      create: async (request) => {
        creates.push(request);
        return { sessionId: "sess-aedt-1" };
      },
      prompt: async (request, source) => {
        prompts.push({ request, source });
        return { accepted: true };
      }
    };
    const { handler } = await mount({ session });
    const opened = await call(handler, "POST", "/api/ai-hfss/aedt/open", TRUST, "127.0.0.1",
      JSON.stringify({ project_path: projectPath, design_name: "Layout1" }));

    assert.equal(opened.status, 200, JSON.stringify(opened.payload));
    assert.equal(opened.payload.ok, true);
    assert.equal(opened.payload.session_id, "sess-aedt-1");
    assert.equal(
      opened.payload.focus_url,
      "http://127.0.0.1:62311/?aihfss=focus&session=sess-aedt-1",
      "focus_url uses the request host and the created session"
    );

    assert.equal(creates.length, 1);
    assert.equal(creates[0].agentPreset, "ansys-embedded");
    assert.equal(typeof creates[0].cwd, "string");
    assert.ok(existsSync(creates[0].cwd), "the session cwd exists");

    assert.equal(prompts.length, 1);
    const request = prompts[0].request;
    const source = prompts[0].source;
    assert.equal(request.sessionId, "sess-aedt-1");
    assert.ok(typeof request.requestId === "string" && request.requestId.length > 0);
    assert.equal(request.content.length, 1);
    assert.equal(request.content[0].type, "text");
    assert.ok(request.content[0].text.includes(projectPath), "the first prompt carries the project path");
    assert.ok(request.content[0].text.includes("Layout1"), "the first prompt carries the design name");
    assert.ok(request.content[0].text.includes("mcp__aedt__check_aedt_status"));
    assert.ok(request.content[0].text.includes("mcp__aedt__connect_to_aedt"));
    assert.equal(source.kind, "user");
    assert.equal(source.rpcId, request.requestId);

    // /open persists the reported project before creating the session.
    assert.equal(JSON.parse(readFileSync(contextFile, "utf8")).project_path, projectPath);

    // A custom prompt is submitted verbatim instead of the default script.
    const custom = await call(handler, "POST", "/api/ai-hfss/aedt/open", TRUST, "127.0.0.1",
      JSON.stringify({ prompt: "只看 SDD21 的最差值" }));
    assert.equal(custom.status, 200);
    assert.equal(prompts.length, 2);
    assert.equal(prompts[1].request.content[0].text, "只看 SDD21 的最差值");
  }

  // ---- 6. /aedt/open surfaces prompt / create failures -----------------
  {
    const rejected = await mount({
      session: {
        create: async () => ({ sessionId: "sess-rejected" }),
        prompt: async () => ({ accepted: false })
      }
    });
    const opened = await call(rejected.handler, "POST", "/api/ai-hfss/aedt/open", TRUST, "127.0.0.1",
      JSON.stringify({ project_path: projectPath }));
    assert.equal(opened.status, 500);
    assert.equal(opened.payload.code, "aedt/prompt-rejected");

    const broken = await mount({
      session: {
        create: async () => {
          throw new Error("no workspace");
        },
        prompt: async () => ({ accepted: true })
      }
    });
    const failed = await call(broken.handler, "POST", "/api/ai-hfss/aedt/open", TRUST, "127.0.0.1",
      JSON.stringify({ project_path: projectPath }));
    assert.equal(failed.status, 500);
    assert.equal(failed.payload.code, "aedt/session-create-failed");
  }

  // ---- 7. request validation -------------------------------------------
  {
    const { handler } = await mount();
    const missingProject = await postContext(handler, { design_name: "HFSSDesign1" });
    assert.equal(missingProject.status, 400);
    assert.equal(missingProject.payload.code, "aedt/invalid-project-path");

    const badPid = await postContext(handler, { project_path: projectPath, pid: -3 });
    assert.equal(badPid.status, 400);
    assert.equal(badPid.payload.code, "aedt/invalid-pid");

    const badPort = await postContext(handler, { project_path: projectPath, grpc_port: 70000 });
    assert.equal(badPort.status, 400);
    assert.equal(badPort.payload.code, "aedt/invalid-grpc-port");

    const badJson = await call(handler, "POST", "/api/ai-hfss/aedt/context", TRUST, "127.0.0.1", "{");
    assert.equal(badJson.status, 400);
    assert.equal(badJson.payload.code, "aedt/invalid-body");

    // /open needs the Host header to build the focus URL.
    const noHost = await call(handler, "POST", "/api/ai-hfss/aedt/open",
      { "x-ai-hfss": "1" }, "127.0.0.1", JSON.stringify({ project_path: projectPath }));
    assert.equal(noHost.status, 400);
    assert.equal(noHost.payload.code, "aedt/invalid-host");
  }

  console.log("ai-hfss aedt api test passed");
} finally {
  for (const cleanup of cleanups.reverse()) {
    try {
      await cleanup();
    } catch {
      /* best effort */
    }
  }
  try {
    rmSync(work, { recursive: true, force: true });
  } catch {
    /* best effort */
  }
}
