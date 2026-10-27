// Browser-half focus-mode test for the AI-HFSS Workstation plugin.
//
// The AEDT script opens DSH at /?aihfss=focus&session=<id>. In that mode the
// client plugin must:
//   * collapse the sidebar once (data-sidebar-collapsed is the only state read),
//   * mount a thin AEDT status strip that polls /api/ai-hfss/aedt/status,
//   * register NEITHER "main" NOR "sidebar.panellist" (the conversation owns
//     the main area),
//   * keep the normal panel registrations when the URL is not a focus URL.
//
// Only the platform seed module (react) is stubbed; the real client.js is
// materialized exactly as the host serves it.
//
// Run: node tests/dsh/ai_hfss_focus_test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const bundle = join(repo, "integrations", "dsh", "bundles", "ai-hfss-workstation");
const source = readFileSync(join(bundle, "client.js"), "utf8").replaceAll(
  "__WORKBENCH_URL__",
  "http://127.0.0.1:8766/workbench"
);
const BUNDLE_NAME = "@local/dsh-ai-hfss-workstation";
const realSetTimeout = globalThis.setTimeout;
const tick = () => new Promise((resolve) => realSetTimeout(resolve, 0));

const DICT = {
  "focus.checking": "正在读取 AEDT 状态…",
  "focus.connected": "AEDT 已连接",
  "focus.partial": "AEDT 状态不完整",
  "focus.pidDown": "AEDT 未连接",
  "focus.noContext": "尚未上报 AEDT 工程",
  "focus.unprobed": "AEDT 工程已上报（未探测进程/端口）",
  "focus.statusError": "无法读取 AEDT 状态",
  "focus.project": "工程",
  "focus.design": "设计"
};

// ---- a tiny DOM ----------------------------------------------------------
class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.id = "";
    this.className = "";
    this.textContent = "";
    this.title = "";
    this.attributes = {};
    this.children = [];
    this.parentNode = null;
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  remove() {
    if (this.parentNode === null) return;
    const parent = this.parentNode;
    const index = parent.children.indexOf(this);
    if (index >= 0) parent.children.splice(index, 1);
    if (parent.firstChild === this) parent.firstChild = parent.children[0] ?? null;
    this.parentNode = null;
  }
}

class FakeBody extends FakeNode {
  constructor() {
    super("body");
    this.firstChild = null;
  }
  insertBefore(node, reference) {
    node.parentNode = this;
    const index = reference === null || reference === undefined ? -1 : this.children.indexOf(reference);
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
    this.firstChild = this.children[0] ?? null;
    return node;
  }
}

function installDom(options = {}) {
  const styleTags = [];
  const doc = {
    collapsed: Boolean(options.collapsed),
    styleTags,
    body: new FakeBody(),
    head: { appendChild: (node) => styleTags.push(node) },
    getElementById: () => null,
    createElement: (tag) => new FakeNode(tag),
    querySelector: (selector) =>
      selector === "[data-sidebar-collapsed]" && doc.collapsed ? new FakeNode("div") : null
  };
  globalThis.document = doc;
  return doc;
}

function setLocation(search, hash) {
  globalThis.location = { search, hash };
}

// ---- deterministic timers ------------------------------------------------
let intervals = [];
let cleared = [];
globalThis.setInterval = (fn, ms) => {
  intervals.push({ fn, ms });
  return "timer-" + String(intervals.length);
};
globalThis.clearInterval = (id) => {
  cleared.push(id);
};
function resetTimers() {
  intervals = [];
  cleared = [];
}

// ---- materialize the lazy-CJS factory -----------------------------------
function materialize() {
  const registrations = [];
  globalThis.window = { __ModuleLoader__: { load: (registration) => registrations.push(registration) } };
  new Function(source)();
  assert.equal(registrations.length, 1, "client.js must register exactly one module");
  const registration = registrations[0];
  assert.equal(registration.id, BUNDLE_NAME);
  return registration.factory((specifier) => {
    if (specifier !== "react") throw new Error('unexpected require("' + specifier + '")');
    return {
      Fragment: "Fragment",
      createElement: (type, props, ...children) => ({ type, props: props ?? {}, children })
    };
  });
}

function makeCtx(options = {}) {
  const record = {
    injected: [],
    registered: [],
    effects: [],
    toggles: 0,
    layoutDeps: [],
    locale: null
  };
  const translate = (key) => DICT[key] ?? key;
  const layout = {
    toggleSidebar: () => {
      record.toggles += 1;
    }
  };
  const ctx = {
    effect: (fn, label) => {
      record.effects.push({ label, cleanup: fn() });
      return () => {};
    },
    locale: {
      register: (namespace, dictionaries) => {
        record.locale = namespace;
        record.dictionaries = dictionaries;
        return () => {};
      },
      bind: () => translate
    },
    slots: {
      inject: (name, factory) => {
        record.injected.push(name);
        factory();
        return () => {};
      },
      register: (registration) => {
        record.registered.push(registration);
        return () => {};
      }
    }
  };
  if (options.layout === true) ctx.layout = layout;
  if (options.injectLayout === true) {
    ctx.inject = (dependencies, callback) => {
      record.layoutDeps.push(dependencies);
      callback({ layout });
    };
  }
  return { ctx, record };
}

function stubFetch(payloadOrError) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    if (payloadOrError instanceof Error) throw payloadOrError;
    return { ok: true, status: 200, json: async () => payloadOrError };
  };
  return calls;
}

function barOf(doc) {
  return doc.body.children.find((node) => String(node.className).includes("aihfss-focusbar"));
}

const AEDT_STATUS = {
  ok: true,
  context: {
    project_path: "C:\\proj\\demo.aedt",
    design_name: "HFSSDesign1",
    pid: 4242,
    grpc_port: 50051
  },
  pid_alive: true,
  port_reachable: true
};

try {
  // ---- 1. ?aihfss=focus: collapse once, status bar, no slot pages -------
  {
    resetTimers();
    const doc = installDom({ collapsed: false });
    setLocation("?aihfss=focus&session=sess-1", "");
    const fetchCalls = stubFetch(AEDT_STATUS);
    const mod = materialize();
    const { ctx, record } = makeCtx({ layout: true });

    mod.apply(ctx);
    assert.deepEqual(record.injected, [], "focus registers neither main nor sidebar.panellist");
    assert.deepEqual(record.registered, [], "focus registers no slot components");
    assert.equal(record.toggles, 1, "the sidebar is collapsed exactly once");
    assert.equal(record.locale, "ai-hfss-workstation");

    await tick();
    await tick();
    const bar = barOf(doc);
    assert.ok(bar, "a focus status bar is mounted on document.body");
    assert.equal(bar.attributes.role, "status");
    assert.equal(bar.attributes["aria-live"], "polite");
    const text = bar.children[1].textContent;
    assert.ok(text.includes("AEDT 已连接"), text);
    assert.ok(text.includes("工程: C:\\proj\\demo.aedt"), text);
    assert.ok(text.includes("设计: HFSSDesign1"), text);
    assert.equal(bar.className, "aihfss-focusbar ok");

    assert.equal(fetchCalls.length, 1, "status is read once at mount");
    assert.equal(fetchCalls[0].url, "/api/ai-hfss/aedt/status");
    assert.equal(fetchCalls[0].options.headers["x-ai-hfss"], "1", "control calls carry the same-origin header");
    assert.equal(intervals.length, 1, "focus polls on an interval");
    assert.equal(intervals[0].ms, 3000, "the status bar polls every 3s");

    // Unmount clears the timer and removes the bar.
    const focusEffect = record.effects[record.effects.length - 1];
    focusEffect.cleanup();
    assert.equal(cleared.length, 1, "the poll timer is cleared on unmount");
    assert.equal(doc.body.children.includes(bar), false, "the status bar is removed on unmount");
    assert.equal(bar.parentNode, null);

    const css = doc.styleTags.map((tag) => tag.textContent).join("\n");
    assert.match(css, /\.aihfss-focusbar\{/, "focus styles are injected");
    assert.match(css, /\.aihfss-focusbar\{[^}]*background:var\(--dsw-alias-bg-layer-1/, "the bar uses a themed token");
    assert.match(css, /\.aihfss-focusbar\{[^}]*color:var\(--dsw-alias-label-secondary/, "the bar text uses a themed token");
  }

  // ---- 2. hash focus, already collapsed: no redundant toggle -----------
  {
    resetTimers();
    const doc = installDom({ collapsed: true });
    setLocation("", "#aihfss=focus");
    stubFetch(AEDT_STATUS);
    const mod = materialize();
    const { ctx, record } = makeCtx({ layout: true });
    mod.apply(ctx);
    await tick();
    await tick();
    assert.equal(record.toggles, 0, "an already-collapsed sidebar is left alone");
    assert.ok(barOf(doc), "hash focus still mounts the status bar");
  }

  // ---- 3. bare #focus is accepted too ----------------------------------
  {
    resetTimers();
    installDom({ collapsed: false });
    setLocation("", "#focus");
    stubFetch(AEDT_STATUS);
    const mod = materialize();
    const { ctx, record } = makeCtx({ layout: true });
    mod.apply(ctx);
    assert.equal(record.toggles, 1, "the #focus hash toggles the sidebar");
  }

  // ---- 4. non-focus keeps the existing panel registrations -------------
  {
    resetTimers();
    const doc = installDom({ collapsed: false });
    setLocation("", "");
    const fetchCalls = stubFetch(AEDT_STATUS);
    const mod = materialize();
    const { ctx, record } = makeCtx({ layout: true });
    mod.apply(ctx);
    assert.deepEqual([...record.injected].sort(), ["main", "sidebar.panellist"], "apply still injects both slots");
    assert.ok(record.registered.some((item) => item.name === "main"));
    assert.ok(record.registered.some((item) => item.name === "sidebar.panellist"));
    assert.equal(record.toggles, 0, "non-focus never touches the sidebar");
    assert.equal(barOf(doc), undefined, "no status bar outside focus mode");
    assert.equal(fetchCalls.length, 0, "no AEDT polling outside focus mode");
    assert.equal(intervals.length, 0);
  }

  // ---- 5. layout via ctx.inject when ctx.layout is hidden --------------
  {
    resetTimers();
    installDom({ collapsed: false });
    setLocation("?aihfss=focus", "");
    stubFetch(AEDT_STATUS);
    const mod = materialize();
    const { ctx, record } = makeCtx({ injectLayout: true });
    mod.apply(ctx);
    assert.deepEqual(record.layoutDeps, [["layout"]], "the plugin asks for the layout service explicitly");
    assert.equal(record.toggles, 1, "the injected layout service collapses the sidebar");
  }

  // ---- 6. no layout service at all: warn, keep rendering ---------------
  {
    resetTimers();
    const doc = installDom({ collapsed: false });
    setLocation("?aihfss=focus", "");
    stubFetch(AEDT_STATUS);
    const warnings = [];
    const originalWarn = console.warn;
    console.warn = (...args) => warnings.push(args.map(String).join(" "));
    try {
      const mod = materialize();
      const { ctx, record } = makeCtx();
      mod.apply(ctx);
      await tick();
      assert.equal(record.toggles, 0);
      assert.ok(warnings.some((line) => line.includes("layout service")), JSON.stringify(warnings));
      assert.ok(barOf(doc), "the status bar still mounts without a layout service");
    } finally {
      console.warn = originalWarn;
    }
  }

  // ---- 7. status API failure shows an explicit error state -------------
  {
    resetTimers();
    const doc = installDom({ collapsed: false });
    setLocation("?aihfss=focus", "");
    stubFetch(new Error("host half not loaded"));
    const mod = materialize();
    const { ctx } = makeCtx({ layout: true });
    mod.apply(ctx);
    await tick();
    await tick();
    const bar = barOf(doc);
    assert.ok(bar);
    assert.equal(bar.className, "aihfss-focusbar error");
    assert.ok(bar.children[1].textContent.includes("无法读取 AEDT 状态"), bar.children[1].textContent);
  }

  // ---- 8. project reported without pid/port: honest "unprobed" state -----
  {
    resetTimers();
    const doc = installDom({ collapsed: false });
    setLocation("?aihfss=focus", "");
    stubFetch({
      ok: true,
      context: { project_path: "C:\\proj\\demo.aedt", design_name: "HFSSDesign1", pid: null, grpc_port: null },
      pid_alive: false,
      port_reachable: false
    });
    const mod = materialize();
    const { ctx } = makeCtx({ layout: true });
    mod.apply(ctx);
    await tick();
    await tick();
    const bar = barOf(doc);
    assert.ok(bar);
    assert.equal(bar.className, "aihfss-focusbar warn");
    assert.ok(bar.children[1].textContent.includes("AEDT 工程已上报"), bar.children[1].textContent);
    assert.ok(bar.children[1].textContent.includes("C:\\proj\\demo.aedt"), bar.children[1].textContent);
  }

  console.log("ai-hfss focus test passed");
} finally {
  // Nothing global to restore: the test owns document/location/fetch/timers.
}
