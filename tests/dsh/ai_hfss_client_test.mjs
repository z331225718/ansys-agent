// Runtime test for the AI-HFSS Workstation browser half.
//
// The plugin is a lazy-CJS bundle whose only host contract is
// window.__ModuleLoader__.load({id, factory}). This test materializes the
// factory with a stub react plus a tiny hook runtime, then DRIVES the page:
//
//   checking -> stopped -> (press Start service) -> starting -> running -> stop
//
// It asserts the control-API calls the page makes (method, header, path), that
// the iframe appears only once /status reports reachable, and that a start
// which never becomes reachable ends in a timeout state that offers the log.
//
// Run: node tests/dsh/ai_hfss_client_test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const bundle = join(repo, "integrations", "dsh", "bundles", "ai-hfss-workstation");
const BUNDLE_NAME = "@local/dsh-ai-hfss-workstation";
const PANEL_ID = "ai-hfss-workstation";
const WORKBENCH_URL = "http://127.0.0.1:8766/workbench";

const source = readFileSync(join(bundle, "client.js"), "utf8").replaceAll("__WORKBENCH_URL__", WORKBENCH_URL);

// ---- platform stubs -------------------------------------------------------
const registrations = [];
const styleTags = [];
globalThis.window = {
  __ModuleLoader__: { load: (registration) => registrations.push(registration) },
  open: () => {}
};
globalThis.document = {
  getElementById: () => null,
  createElement: (tag) => ({ tag, id: "", textContent: "" }),
  head: { appendChild: (node) => styleTags.push(node) }
};

// Deterministic timers: the page polls, and the test must not wait for real delays.
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn) => {
  queueMicrotask(fn);
  return 0;
};
const tick = () => new Promise((resolve) => realSetTimeout(resolve, 0));

// ---- a minimal hook runtime ----------------------------------------------
let activeRuntime = null;

function createRuntime() {
  const states = [];
  const effects = [];
  const cleanups = [];
  const runtime = {
    cursor: 0,
    pendingRender: false,
    pendingEffects: [],
    react: null
  };
  runtime.react = {
    Fragment: "Fragment",
    createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
    useState(initial) {
      const index = runtime.cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      const set = (next) => {
        states[index] = typeof next === "function" ? next(states[index]) : next;
        runtime.pendingRender = true;
      };
      return [states[index], set];
    },
    useEffect(fn, deps) {
      const index = runtime.cursor++;
      const previous = effects[index];
      const same =
        previous !== undefined &&
        deps !== undefined &&
        previous.deps !== undefined &&
        previous.deps.length === deps.length &&
        previous.deps.every((value, position) => Object.is(value, deps[position]));
      if (same) return;
      effects[index] = { fn, deps };
      runtime.pendingEffects.push(fn);
    }
  };
  return { runtime, cleanups };
}

/** Render, run pending effects, then re-render while state keeps moving. */
async function settle(holder, Component, props) {
  const { runtime, cleanups } = holder;
  activeRuntime = runtime;
  const render = () => {
    runtime.cursor = 0;
    runtime.pendingEffects = [];
    return Component(props);
  };
  const runEffects = () => {
    for (const effect of runtime.pendingEffects.splice(0)) {
      const cleanup = effect();
      if (typeof cleanup === "function") cleanups.push(cleanup);
    }
  };
  let tree = render();
  runEffects();
  // The page talks to the control API through several promise hops, so settle
  // means "no state movement for a few consecutive turns", not "one tick".
  let quiet = 0;
  for (let step = 0; step < 600; step += 1) {
    await tick();
    if (runtime.pendingRender) {
      runtime.pendingRender = false;
      tree = render();
      runEffects();
      quiet = 0;
      continue;
    }
    quiet += 1;
    if (quiet > 12) break;
  }
  assert.ok(quiet > 12, "the page must settle (no render loop)");
  return tree;
}

// The page composes small function components (Toolbar, StateCard); they hold
// no hooks, so the test expands them to reach the real host elements.
function expand(node) {
  let current = node;
  let guard = 0;
  while (
    current !== null &&
    current !== undefined &&
    typeof current === "object" &&
    !Array.isArray(current) &&
    typeof current.type === "function"
  ) {
    current = current.type(current.props);
    guard += 1;
    if (guard > 40) throw new Error("component expansion did not terminate");
  }
  return current;
}

function findAll(node, predicate, out = []) {
  const current = expand(node);
  if (current === null || current === undefined || typeof current !== "object") return out;
  if (Array.isArray(current)) {
    for (const child of current) findAll(child, predicate, out);
    return out;
  }
  if (predicate(current)) out.push(current);
  for (const child of current.children ?? []) findAll(child, predicate, out);
  return out;
}

function textOf(node) {
  const current = expand(node);
  if (current === null || current === undefined) return "";
  if (typeof current === "string" || typeof current === "number") return String(current);
  if (Array.isArray(current)) return current.map(textOf).join("");
  return (current.children ?? []).map(textOf).join("");
}

const buttonByLabel = (tree, label) =>
  findAll(tree, (node) => node.type === "button").find((node) => textOf(node).includes(label));
const iframeOf = (tree) => findAll(tree, (node) => node.type === "iframe")[0];

// ---- fake control API ----------------------------------------------------
function makeApi(handlers) {
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const method = options.method ?? "GET";
    calls.push({ path, method, headers: options.headers ?? {} });
    const key = method + " " + path.split("?")[0];
    const handler = handlers[key];
    const payload = handler === undefined ? { status: 404, body: { ok: false, error: "unexpected " + key } } : handler(path);
    const status = payload.status ?? 200;
    return { ok: status >= 200 && status < 300, status, json: async () => payload.body };
  };
  return calls;
}

// ---- materialize the factory ---------------------------------------------
new Function(source)();

assert.equal(registrations.length, 1, "client.js must register exactly one module");
const registration = registrations[0];
assert.equal(registration.id, BUNDLE_NAME, "registration id must equal the package name");
assert.equal(typeof registration.factory, "function", "factory must be a function");

const mod = registration.factory((specifier) => {
  if (specifier !== "react") {
    throw new Error('unexpected require("' + specifier + '") — only platform seed modules are allowed');
  }
  return {
    Fragment: "Fragment",
    createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
    useState: (initial) => activeRuntime.react.useState(initial),
    useEffect: (fn, deps) => activeRuntime.react.useEffect(fn, deps)
  };
});

// ---- apply against a stub client context --------------------------------
const registered = new Map();
const injected = [];
const localeNamespaces = [];
const ctx = {
  effect: (fn) => {
    fn();
    return () => {};
  },
  locale: {
    register: (ns, dictionaries) => {
      localeNamespaces.push(ns);
      assert.ok(dictionaries.zh && dictionaries.en, "locale dictionaries need zh and en");
      return () => {};
    },
    bind: (ns) => (key) => ns + ":" + key
  },
  slots: {
    inject: (name, factory) => {
      injected.push(name);
      factory();
      return () => {};
    },
    register: (options, component) => {
      registered.set(options.name, { options, component });
      return () => {};
    }
  }
};

assert.equal(typeof mod.apply, "function", "module must export apply");
assert.deepEqual(mod.inject, ["slots", "locale"], "module must inject the slots and locale services");
mod.apply(ctx);

assert.deepEqual([...injected].sort(), ["main", "sidebar.panellist"], "apply must inject both slots");
assert.deepEqual(localeNamespaces, ["ai-hfss-workstation"], "apply must register one dictionary namespace");

// ---- sidebar panel entry -------------------------------------------------
const panellist = registered.get("sidebar.panellist");
assert.ok(panellist, "sidebar.panellist must be registered");
assert.equal(panellist.options.id, PANEL_ID, "panel id is the layout panel id");
assert.equal(panellist.options.order, 30, "panel order must be explicit");
assert.equal(panellist.options.label(), "ai-hfss-workstation:panel", "label resolves through locale");
const icon = panellist.component({ size: 16, active: false });
assert.equal(icon.type, "svg", "the panel glyph is an inline svg");
assert.equal(icon.props.width, 16, "the glyph honours the requested size");
assert.equal(icon.props["aria-hidden"], "true", "the glyph is decorative");

// ---- main page -----------------------------------------------------------
const main = registered.get("main");
assert.ok(main, "main must be registered");
assert.equal(main.options.key, PANEL_ID, "main key must match the panel id or the page never renders");
const injectedProps = main.options.inject();
assert.equal(typeof injectedProps.t, "function", "main page must receive a bound translator");

// In the real host the page receives ctx.locale.bind(NS), so keys are bare.
const LABELS = {
  "page.scope": "只读视图",
  "state.checking": "正在检查工作台服务…",
  "state.stoppedTitle": "工作台服务未运行",
  "state.stoppedBody": "点击「启动服务」拉起 dashboard",
  "btn.start": "启动服务",
  "btn.starting": "启动中…",
  "btn.stop": "停止服务",
  "btn.reload": "重新加载",
  "btn.retry": "重试",
  "btn.log": "查看日志",
  "btn.hideLog": "隐藏日志",
  "btn.open": "在浏览器打开"
};
const translate = (key) => LABELS[key] ?? key;

async function renderPage(handlers) {
  const calls = makeApi(handlers);
  const holder = createRuntime();
  const tree = await settle(holder, main.component, { t: translate });
  return { tree, calls, holder };
}

// 1) status says "not running" -> Start offered, nothing embedded
{
  const { tree } = await renderPage({
    "GET /api/ai-hfss/status": () => ({
      body: { ok: true, running: false, reachable: false, workbenchUrl: WORKBENCH_URL, service: { port: 8766 } }
    })
  });
  assert.equal(iframeOf(tree), undefined, "no iframe before the service runs");
  assert.ok(buttonByLabel(tree, "启动服务"), "the page offers Start service");
}

// 2) press Start -> POST with the same-origin header, poll, then embed
{
  let polls = 0;
  const { tree, calls, holder } = await renderPage({
    "GET /api/ai-hfss/status": () => {
      polls += 1;
      const ready = polls > 2;
      return {
        body: {
          ok: true,
          running: ready,
          reachable: ready,
          workbenchUrl: WORKBENCH_URL,
          pid: 4242,
          service: { port: 8766, missionsDb: "/tmp/missions.db", logFile: "/tmp/dashboard.log" }
        }
      };
    },
    "POST /api/ai-hfss/start": () => ({ body: { ok: true, pid: 4242, service: { startupTimeoutMs: 5000 } } })
  });

  const startButton = buttonByLabel(tree, "启动服务");
  assert.ok(startButton, "Start button present before starting");
  startButton.props.onClick();
  const afterStart = await settle(holder, main.component, { t: translate });

  const startCall = calls.find((call) => call.method === "POST" && call.path.startsWith("/api/ai-hfss/start"));
  assert.ok(startCall, "pressing Start must POST /api/ai-hfss/start");
  assert.equal(startCall.headers["x-ai-hfss"], "1", "control calls carry the same-origin header");

  const frame = iframeOf(afterStart);
  assert.ok(frame, "the workbench is embedded once /status reports reachable");
  assert.equal(frame.props.src, WORKBENCH_URL, "the iframe points at the reported workbench URL");
  assert.equal(frame.props.sandbox.includes("allow-downloads"), true, "artifacts must stay downloadable");
  assert.ok(buttonByLabel(afterStart, "停止服务"), "a running service offers Stop");
}

// 3) Stop returns the panel to the stopped state
{
  let running = true;
  const { tree, holder, calls } = await renderPage({
    "GET /api/ai-hfss/status": () => ({
      body: { ok: true, running, reachable: running, workbenchUrl: WORKBENCH_URL, service: { port: 8766 } }
    }),
    "POST /api/ai-hfss/stop": () => {
      running = false;
      return { body: { ok: true, killed: [4242], running: false, reachable: false } };
    }
  });
  assert.ok(iframeOf(tree), "starts in the running state");
  buttonByLabel(tree, "停止服务").props.onClick();
  const afterStop = await settle(holder, main.component, { t: translate });
  assert.ok(calls.some((call) => call.method === "POST" && call.path.startsWith("/api/ai-hfss/stop")));
  assert.equal(iframeOf(afterStop), undefined, "the iframe is gone after Stop");
  assert.ok(buttonByLabel(afterStop, "启动服务"), "Start is offered again");
}

// 4) a start that never becomes reachable -> timeout state, log available
{
  const { tree, holder } = await renderPage({
    "GET /api/ai-hfss/status": () => ({
      body: { ok: true, running: false, reachable: false, workbenchUrl: WORKBENCH_URL, service: { port: 8766 } }
    }),
    "POST /api/ai-hfss/start": () => ({ body: { ok: true, service: { startupTimeoutMs: 1 } } }),
    "GET /api/ai-hfss/log": () => ({ body: { ok: true, tail: "Traceback: boom" } })
  });
  buttonByLabel(tree, "启动服务").props.onClick();
  const after = await settle(holder, main.component, { t: translate });
  assert.equal(iframeOf(after), undefined, "a failed start never embeds an empty frame");
  assert.ok(buttonByLabel(after, "查看日志"), "a failed start offers the log");
}

// 5) control API unavailable -> explicit error, never a fake embed
{
  const { tree } = await renderPage({
    "GET /api/ai-hfss/status": () => ({ status: 500, body: { ok: false, error: "host half not loaded" } })
  });
  assert.equal(iframeOf(tree), undefined);
  assert.ok(buttonByLabel(tree, "重试"), "an unavailable API offers Retry");
}

assert.ok(styleTags.length >= 1, "styles are injected once");

// ---- theme-token regression --------------------------------------------
// The primary action must use the platform's own Button pair. Using a brand
// fill with a hard-coded white label makes the button invisible on dark
// themes, where brand-primary resolves to a near-white neutral.
{
  const css = styleTags.map((tag) => tag.textContent).join("\n");
  assert.match(
    css,
    /\.aihfss-btn\.primary\{[^}]*background:var\(--dsw-alias-button-primary-fill/,
    "the primary fill must come from --dsw-alias-button-primary-fill"
  );
  assert.match(
    css,
    /\.aihfss-btn\.primary\{[^}]*color:var\(--dsw-alias-label-primary-foreground/,
    "the primary label must come from --dsw-alias-label-primary-foreground"
  );
  assert.equal(
    css.includes("--dsw-alias-brand-primary"),
    false,
    "brand-primary flips to near-white on dark themes and must not fill a button"
  );
  assert.equal(/color:\s*#fff\b/.test(css), false, "no hard-coded white label");
  assert.match(
    css,
    /\.aihfss-btn\{[^}]*color:var\(--dsw-alias-label-primary/,
    "neutral buttons take the themed label colour"
  );
  assert.match(css, /--dsw-alias-label-primary/, "body text uses the themed label token");
}

console.log("ai-hfss workstation client smoke test passed");
