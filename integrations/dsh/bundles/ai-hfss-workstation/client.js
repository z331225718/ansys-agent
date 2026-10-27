// AI-HFSS Workstation — browser half (lazy-CJS client plugin).
//
// Registers, at parity with the built-in 插件 (plugin-manager) and 自动化任务
// (schedule) surfaces:
//   - a sidebar panel entry   -> ctx.slots "sidebar.panellist" (id: ai-hfss-workstation)
//   - a main-area page        -> ctx.slots "main" (key: ai-hfss-workstation)
//
// The page drives the workbench dashboard through the host control API
// (/api/ai-hfss/*): it shows the service state, starts and stops the dashboard
// with a button, polls until the workbench answers, and only then embeds it.
// No command has to be typed by the user.
//
// Only the platform seed modules are required (react), never a graph row.
window.__ModuleLoader__.load({
  id: "@local/dsh-ai-hfss-workstation",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const react = require("react");

    const NS = "ai-hfss-workstation";
    const PANEL_ID = "ai-hfss-workstation";
    const STYLE_ID = "ai-hfss-workstation-styles";
    const API = "/api/ai-hfss";
    // Shown only when the control API itself cannot be reached; the live URL
    // always comes from /api/ai-hfss/status.
    const FALLBACK_WORKBENCH_URL = "__WORKBENCH_URL__";
    const POLL_INTERVAL_MS = 1200;
    const DEFAULT_STARTUP_TIMEOUT_MS = 45000;
    // Focus mode (?aihfss=focus or #aihfss=focus): the AEDT script button opens
    // this page with the sidebar collapsed and only a thin AEDT status bar on
    // top, so the conversation is the whole surface.
    const FOCUS_PARAM = "aihfss";
    const FOCUS_VALUE = "focus";
    const FOCUS_STATUS_INTERVAL_MS = 3000;

    const zh = {
      panel: "AI-HFSS 工作站",
      "page.title": "AI-HFSS 工作站",
      "page.subtitle": "高速 SI/PI 通道优化 · 一键启停 Mission 工作台",
      "page.scope":
        "工作台只读：不启动求解、不推进 graph、不批准 human_gate、不写 AEDT。生产操作仍走受控 CLI/dashboard。",
      "state.checking": "正在检查工作台服务…",
      "state.stoppedTitle": "工作台服务未运行",
      "state.stoppedBody": "点击「启动服务」在当前机器拉起 dashboard，启动完成后页面会自动嵌入工作台。",
      "state.startingTitle": "正在启动工作台服务…",
      "state.startingBody": "首次启动通常需要 10-30 秒，页面会自动检测就绪。",
      "state.stopping": "正在停止服务…",
      "state.errorTitle": "服务操作失败",
      "state.apiUnavailable": "控制接口不可用（插件 host 半边未加载或 DSH 需要重启）。",
      "btn.start": "启动服务",
      "btn.starting": "启动中…",
      "btn.stop": "停止服务",
      "btn.stopping": "停止中…",
      "btn.open": "在浏览器打开",
      "btn.reload": "重新加载",
      "btn.retry": "重试",
      "btn.log": "查看日志",
      "btn.hideLog": "隐藏日志",
      "meta.db": "Mission 库",
      "meta.log": "日志",
      "meta.pid": "进程",
      "meta.port": "端口",
      "meta.external": "该服务不是由本面板启动的",
      "focus.checking": "正在读取 AEDT 状态…",
      "focus.connected": "AEDT 已连接",
      "focus.partial": "AEDT 状态不完整（PID 与端口只探测到一个）",
      "focus.pidDown": "AEDT 未连接",
      "focus.noContext": "尚未上报 AEDT 工程",
      "focus.unprobed": "AEDT 工程已上报（未探测进程/端口）",
      "focus.statusError": "无法读取 AEDT 状态",
      "focus.project": "工程",
      "focus.design": "设计"
    };
    const en = {
      panel: "AI-HFSS Workstation",
      "page.title": "AI-HFSS Workstation",
      "page.subtitle": "High-speed SI/PI channel optimization · start/stop the Mission workbench",
      "page.scope":
        "The workbench is read-only: no solve, no graph advance, no human_gate approval, no AEDT writes. Production actions stay in the controlled CLI/dashboard.",
      "state.checking": "Checking the workbench service…",
      "state.stoppedTitle": "Workbench service is not running",
      "state.stoppedBody": "Press Start service to launch the dashboard on this machine; the page embeds it automatically once it answers.",
      "state.startingTitle": "Starting the workbench service…",
      "state.startingBody": "The first start usually takes 10-30 seconds; this page polls until it is ready.",
      "state.stopping": "Stopping the service…",
      "state.errorTitle": "Service action failed",
      "state.apiUnavailable": "Control API unavailable (the plugin host half is not loaded, or DSH needs a restart).",
      "btn.start": "Start service",
      "btn.starting": "Starting…",
      "btn.stop": "Stop service",
      "btn.stopping": "Stopping…",
      "btn.open": "Open in browser",
      "btn.reload": "Reload",
      "btn.retry": "Retry",
      "btn.log": "Show log",
      "btn.hideLog": "Hide log",
      "meta.db": "Mission DB",
      "meta.log": "Log",
      "meta.pid": "Process",
      "meta.port": "Port",
      "meta.external": "This service was not started by this panel",
      "focus.checking": "Reading AEDT status…",
      "focus.connected": "AEDT connected",
      "focus.partial": "AEDT status is partial (only one of pid / port answered)",
      "focus.pidDown": "AEDT is not connected",
      "focus.noContext": "No AEDT project has been reported yet",
      "focus.unprobed": "AEDT project reported (no pid/port probe available)",
      "focus.statusError": "Cannot read the AEDT status",
      "focus.project": "Project",
      "focus.design": "Design"
    };

    function injectStyles() {
      if (document.getElementById(STYLE_ID) !== null) return;
      const tag = document.createElement("style");
      tag.id = STYLE_ID;
      // Colours come from the platform design tokens (the same pair the built-in
      // Button uses), so light and dark themes both stay legible: a "brand"
      // fill with a hard-coded white label is invisible on dark themes, where
      // brand-primary resolves to a near-white neutral.
      tag.textContent = [
        ".aihfss-root{display:flex;flex-direction:column;height:100%;min-height:0;color:var(--dsw-alias-label-primary,#1f2328);background:var(--dsw-alias-bg-base,transparent)}",
        ".aihfss-bar{display:flex;align-items:flex-start;gap:12px;padding:12px 20px;border-bottom:.5px solid var(--dsw-alias-border-l2,rgba(128,128,128,.28));flex:0 0 auto}",
        ".aihfss-head{display:flex;flex-direction:column;gap:3px;min-width:0}",
        ".aihfss-title{font-size:15px;font-weight:600;line-height:1.35;color:var(--dsw-alias-label-primary,inherit)}",
        ".aihfss-sub{font-size:12px;line-height:1.35;color:var(--dsw-alias-label-tertiary,inherit)}",
        ".aihfss-spacer{flex:1 1 auto}",
        ".aihfss-actions{display:flex;gap:8px;flex:0 0 auto;align-items:center;flex-wrap:wrap}",
        ".aihfss-btn{font:inherit;font-size:12px;line-height:1.2;padding:6px 12px;border-radius:var(--dsw-radius-sm,6px);cursor:pointer;border:.5px solid var(--dsw-alias-border-l3,rgba(128,128,128,.45));background:transparent;color:var(--dsw-alias-label-primary,inherit)}",
        ".aihfss-btn:hover:not([disabled]){background:var(--dsw-alias-interactive-bg-hover,rgba(128,128,128,.12))}",
        ".aihfss-btn:active:not([disabled]){background:var(--dsw-alias-interactive-bg-active,rgba(128,128,128,.18))}",
        ".aihfss-btn:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-state-business-primary,#2d6eeb));outline-offset:1px}",
        ".aihfss-btn[disabled]{opacity:.5;cursor:default}",
        ".aihfss-btn.primary{border-color:transparent;background:var(--dsw-alias-button-primary-fill,#1f2328);color:var(--dsw-alias-label-primary-foreground,#fff)}",
        ".aihfss-btn.primary:hover:not([disabled]){background:var(--dsw-alias-button-primary-hover,#3a3f45)}",
        ".aihfss-scope{font-size:11.5px;line-height:1.5;color:var(--dsw-alias-label-tertiary,inherit);padding:6px 20px;border-bottom:.5px solid var(--dsw-alias-border-l2,rgba(128,128,128,.18));flex:0 0 auto}",
        ".aihfss-body{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}",
        ".aihfss-frame{flex:1 1 auto;width:100%;border:0;background:var(--dsw-alias-bg-layer-1,#fff)}",
        ".aihfss-state{margin:auto;padding:28px;max-width:66ch;text-align:left}",
        ".aihfss-state h2{font-size:14px;font-weight:600;margin:0 0 8px;color:var(--dsw-alias-label-primary,inherit)}",
        ".aihfss-state p{font-size:13px;line-height:1.65;margin:0 0 12px;color:var(--dsw-alias-label-secondary,inherit)}",
        ".aihfss-state-actions{display:flex;gap:8px;margin:0 0 14px;flex-wrap:wrap;align-items:center}",
        ".aihfss-meta{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--dsw-alias-label-tertiary,inherit);word-break:break-all;line-height:1.7}",
        ".aihfss-spin{display:inline-block;width:12px;height:12px;border:1.6px solid var(--dsw-alias-border-l3,rgba(128,128,128,.35));border-top-color:currentColor;border-radius:50%;margin-right:8px;animation:aihfss-spin .9s linear infinite;vertical-align:-2px}",
        "@keyframes aihfss-spin{to{transform:rotate(360deg)}}",
        // Focus mode: a thin, informational AEDT strip pinned above the shell.
        // pointer-events:none keeps it from stealing clicks from the title bar
        // it overlaps; colours stay on the platform semantic tokens.
        ".aihfss-focusbar{position:fixed;top:0;left:0;right:0;z-index:2147483000;display:flex;align-items:center;gap:8px;height:26px;padding:0 12px;font:inherit;font-size:12px;line-height:1;background:var(--dsw-alias-bg-layer-1,#fff);color:var(--dsw-alias-label-secondary,inherit);border-bottom:.5px solid var(--dsw-alias-border-l2,rgba(128,128,128,.28));pointer-events:none}",
        ".aihfss-focusbar-dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto;background:var(--dsw-alias-label-tertiary,#8a8f98)}",
        ".aihfss-focusbar.ok .aihfss-focusbar-dot{background:var(--dsw-alias-state-business-primary,#2d6eeb)}",
        ".aihfss-focusbar.warn .aihfss-focusbar-dot{background:var(--dsw-alias-label-tertiary,#b7791f)}",
        ".aihfss-focusbar.error .aihfss-focusbar-dot{background:var(--dsw-alias-label-secondary,#c0392b)}",
        ".aihfss-focusbar-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
        ".aihfss-log{margin:10px 0 0;padding:10px 12px;border-radius:var(--dsw-radius-sm,6px);border:.5px solid var(--dsw-alias-border-l2,rgba(128,128,128,.35));background:var(--dsw-alias-bg-layer-2,rgba(128,128,128,.08));color:var(--dsw-alias-label-secondary,inherit);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;line-height:1.55;white-space:pre-wrap;max-height:260px;overflow:auto}"
      ].join("\n");
      document.head.appendChild(tag);
    }

    function el(tag, props, ...children) {
      return react.createElement(tag, props, ...children);
    }

    function sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    /** One control-API call; the header marks it as same-origin intent. */
    async function api(path, init) {
      const options = Object.assign({ cache: "no-store", headers: { "x-ai-hfss": "1" } }, init ?? {});
      const response = await fetch(API + path, options);
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok) {
        const message = payload && typeof payload.error === "string" ? payload.error : "HTTP " + String(response.status);
        const error = new Error(message);
        error.payload = payload;
        throw error;
      }
      return payload ?? {};
    }

    function metaRow(label, value) {
      if (value === undefined || value === null || value === "") return null;
      return el("div", { className: "aihfss-meta" }, label + ": " + String(value));
    }

    function Toolbar(props) {
      return el(
        "div",
        { className: "aihfss-bar" },
        el(
          "div",
          { className: "aihfss-head" },
          el("div", { className: "aihfss-title" }, props.t("page.title")),
          el("div", { className: "aihfss-sub" }, props.t("page.subtitle"))
        ),
        el("div", { className: "aihfss-spacer" }),
        el("div", { className: "aihfss-actions" }, props.actions)
      );
    }

    function StateCard(props) {
      return el(
        "div",
        { className: "aihfss-state" },
        el("h2", null, props.title),
        props.title && props.busy ? null : null,
        el("p", null, props.body),
        props.actions ? el("div", { className: "aihfss-state-actions" }, props.actions) : null,
        props.meta ?? null,
        props.log ? el("pre", { className: "aihfss-log" }, props.log) : null
      );
    }

    function WorkbenchPage(props) {
      const t = props.t;
      const [view, setView] = react.useState({ phase: "checking", data: null });
      const [busy, setBusy] = react.useState(false);
      const [showLog, setShowLog] = react.useState(false);
      const [log, setLog] = react.useState("");
      const [attempt, setAttempt] = react.useState(0);

      react.useEffect(() => {
        let cancelled = false;
        api("/status")
          .then((data) => {
            if (cancelled) return;
            setView({ phase: data.running ? "running" : "stopped", data });
          })
          .catch((error) => {
            if (cancelled) return;
            setView({ phase: "api-error", error: String(error.message ?? error) });
          });
        return () => {
          cancelled = true;
        };
      }, [attempt]);

      const refreshLog = async () => {
        try {
          const payload = await api("/log?tail=160");
          setLog(typeof payload.tail === "string" ? payload.tail : "");
        } catch (error) {
          setLog(String(error.message ?? error));
        }
      };

      const start = async () => {
        setBusy(true);
        setView((previous) => ({ phase: "starting", data: previous.data }));
        try {
          const begun = await api("/start", { method: "POST" });
          const timeoutMs =
            (begun && begun.service && Number(begun.service.startupTimeoutMs)) || DEFAULT_STARTUP_TIMEOUT_MS;
          const deadline = Date.now() + timeoutMs;
          let status = await api("/status");
          while (!status.reachable && Date.now() < deadline) {
            // start() clears lastExit on spawn, so any exit seen here is this run's.
            if (status.lastExit && status.lastExit.intentional !== true) {
              throw new Error("服务启动后立即退出（code=" + String(status.lastExit.code) + "）");
            }
            await sleep(POLL_INTERVAL_MS);
            status = await api("/status");
          }
          if (!status.reachable) {
            await refreshLog();
            setView({ phase: "timeout", data: status });
            return;
          }
          setView({ phase: "running", data: status });
        } catch (error) {
          await refreshLog();
          setView((previous) => ({ phase: "error", data: previous.data, error: String(error.message ?? error) }));
        } finally {
          setBusy(false);
        }
      };

      const stop = async () => {
        setBusy(true);
        try {
          const outcome = await api("/stop", { method: "POST" });
          setView({ phase: "stopped", data: outcome });
        } catch (error) {
          const payload = error.payload;
          if (payload && typeof payload.running === "boolean" && payload.running) {
            setView({ phase: "running", data: payload });
          } else {
            setView((previous) => ({ phase: "error", data: previous.data, error: String(error.message ?? error) }));
          }
        } finally {
          setBusy(false);
        }
      };

      const reloadFrame = () => setAttempt((value) => value + 1);
      const data = view.data ?? {};
      const service = data.service ?? {};
      const workbenchUrl = data.workbenchUrl ?? FALLBACK_WORKBENCH_URL;

      const button = (label, onClick, options) =>
        el(
          "button",
          {
            type: "button",
            className: options && options.primary ? "aihfss-btn primary" : "aihfss-btn",
            onClick,
            disabled: Boolean(options && options.disabled)
          },
          label
        );

      const openButton = button(t("btn.open"), () => {
        window.open(workbenchUrl, "_blank", "noopener");
      });

      const reloadButton = button(t("btn.reload"), reloadFrame);
      const logButton = button(showLog ? t("btn.hideLog") : t("btn.log"), () => setShowLog((value) => !value));

      if (view.phase === "checking") {
        return el(
          "div",
          { className: "aihfss-root" },
          el(Toolbar, { t, actions: null }),
          el("div", { className: "aihfss-scope" }, t("page.scope")),
          el("div", { className: "aihfss-body" }, el(StateCard, { title: "", body: t("state.checking"), busy: true }))
        );
      }

      if (view.phase === "running") {
        return el(
          "div",
          { className: "aihfss-root" },
          el(Toolbar, {
            t,
            actions: [reloadButton, openButton, button(t("btn.stop"), stop, { disabled: busy })]
          }),
          el("div", { className: "aihfss-scope" }, t("page.scope")),
          el(
            "div",
            { className: "aihfss-body" },
            el("iframe", {
              className: "aihfss-frame",
              src: workbenchUrl,
              title: t("page.title"),
              sandbox: "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals"
            })
          )
        );
      }

      const starting = view.phase === "starting";
      const timeout = view.phase === "timeout";
      const apiError = view.phase === "api-error";
      const title = apiError
        ? t("state.errorTitle")
        : starting
          ? t("state.startingTitle")
          : timeout
            ? t("state.errorTitle")
            : view.phase === "error"
              ? t("state.errorTitle")
              : t("state.stoppedTitle");
      const body = apiError
        ? t("state.apiUnavailable") + " " + (view.error ?? "")
        : starting
          ? t("state.startingBody")
          : timeout
            ? t("state.startingBody")
            : view.phase === "error"
              ? String(view.error ?? "")
              : t("state.stoppedBody");

      const meta = el(
        "div",
        null,
        metaRow(t("meta.port"), service.port),
        metaRow(t("meta.db"), service.missionsDb),
        metaRow(t("meta.log"), service.logFile),
        data.pid ? metaRow(t("meta.pid"), data.pid) : null,
        metaRow(t("btn.open"), workbenchUrl),
        data.running && data.owned === false ? el("div", { className: "aihfss-meta" }, t("meta.external")) : null
      );

      const actions = apiError
        ? [button(t("btn.retry"), reloadFrame)]
        : starting
          ? [
              el("span", { className: "aihfss-meta" }, el("span", { className: "aihfss-spin" }), t("btn.starting"))
            ]
          : [
              button(t("btn.start"), start, { primary: true, disabled: busy }),
              button(t("btn.retry"), reloadFrame, { disabled: busy }),
              logButton
            ];

      return el(
        "div",
        { className: "aihfss-root" },
        el(Toolbar, { t, actions: data.running ? [openButton, button(t("btn.stop"), stop, { disabled: busy })] : null }),
        el("div", { className: "aihfss-scope" }, t("page.scope")),
        el(
          "div",
          { className: "aihfss-body" },
          el(StateCard, {
            title,
            body,
            actions,
            meta,
            log: showLog ? log : ""
          })
        )
      );
    }

    /** Sidebar glyph: a channel/trace glyph drawn inline, no icon dependency. */
    function WorkstationIcon(props) {
      const size = props && typeof props.size === "number" ? props.size : 18;
      return el(
        "svg",
        {
          width: size,
          height: size,
          viewBox: "0 0 24 24",
          fill: "none",
          stroke: "currentColor",
          strokeWidth: 1.7,
          strokeLinecap: "round",
          strokeLinejoin: "round",
          "aria-hidden": "true",
          focusable: "false"
        },
        el("path", { d: "M2.5 15.5 H7 L9.4 7.5 L12.6 19.5 L15 11.5 H21.5" }),
        el("circle", { cx: 21.5, cy: 11.5, r: 1.5, fill: "currentColor", stroke: "none" })
      );
    }

    // ---- focus mode (?aihfss=focus) -------------------------------------
    // The AEDT script button opens /?aihfss=focus&session=<id>. In focus mode
    // the conversation is the whole page: no panel glyph, no main-area page
    // (registering "main" would take the main area over), the sidebar is
    // collapsed once, and a thin strip reports the AEDT project/design.

    /** True when the page was opened by the AEDT hand-off (query or hash). */
    function focusRequested() {
      const location = globalThis.location;
      if (location === undefined || location === null) return false;
      const search = typeof location.search === "string" ? location.search : "";
      const hash = typeof location.hash === "string" ? location.hash : "";
      try {
        if (new URLSearchParams(search).get(FOCUS_PARAM) === FOCUS_VALUE) return true;
        const rawHash = hash.startsWith("#") ? hash.slice(1) : hash;
        if (rawHash === "") return false;
        if (!rawHash.includes("=")) return decodeURIComponent(rawHash) === FOCUS_VALUE;
        return new URLSearchParams(rawHash).get(FOCUS_PARAM) === FOCUS_VALUE;
      } catch {
        return false;
      }
    }

    /** The renderer's data-sidebar-collapsed attribute is the only state read. */
    function sidebarCollapsed() {
      try {
        return document.querySelector("[data-sidebar-collapsed]") !== null;
      } catch (error) {
        console.warn("[ai-hfss] focus mode: cannot inspect the sidebar state", error);
        return false;
      }
    }

    function layoutService(ctx) {
      let layout = ctx.layout;
      if (layout === undefined && typeof ctx.get === "function") {
        try {
          layout = ctx.get("layout");
        } catch {
          /* not mounted in this profile */
        }
      }
      return layout;
    }

    function toggleSidebar(layout) {
      if (layout === undefined || layout === null || typeof layout.toggleSidebar !== "function") return false;
      layout.toggleSidebar();
      return true;
    }

    /**
     * Collapse the sidebar once, at focus mount. There is no read API for the
     * layout state, so the rendered data-sidebar-collapsed attribute decides
     * whether a toggle is still needed (see docs/spikes/ai-hfss-p0.md, Q2).
     */
    function collapseSidebarOnce(ctx) {
      if (sidebarCollapsed()) return false;
      try {
        if (toggleSidebar(layoutService(ctx))) return true;
      } catch (error) {
        console.warn("[ai-hfss] focus mode: collapsing the sidebar failed", error);
        return false;
      }
      // Cordis can hide a service this plugin did not declare in exports.inject;
      // request it explicitly (still once, still non-blocking).
      if (typeof ctx.inject === "function") {
        try {
          ctx.inject(["layout"], (layoutCtx) => {
            try {
              if (!sidebarCollapsed()) toggleSidebar(layoutCtx?.layout ?? layoutCtx);
            } catch (error) {
              console.warn("[ai-hfss] focus mode: collapsing the sidebar failed", error);
            }
          });
          return true;
        } catch (error) {
          console.warn("[ai-hfss] focus mode: the layout service is unavailable", error);
        }
      } else {
        console.warn("[ai-hfss] focus mode: the layout service is unavailable; the sidebar stays as it is");
      }
      return false;
    }

    function createFocusStatusBar(t) {
      const body = document.body;
      if (body === undefined || body === null || typeof body.insertBefore !== "function") {
        console.warn("[ai-hfss] focus mode: document.body is unavailable; the AEDT status bar is skipped");
        return null;
      }
      const bar = document.createElement("div");
      bar.className = "aihfss-focusbar";
      bar.setAttribute("role", "status");
      bar.setAttribute("aria-live", "polite");
      const dot = document.createElement("span");
      dot.className = "aihfss-focusbar-dot";
      const text = document.createElement("span");
      text.className = "aihfss-focusbar-text";
      text.textContent = t("focus.checking");
      bar.appendChild(dot);
      bar.appendChild(text);
      body.insertBefore(bar, body.firstChild ?? null);
      return { bar, text };
    }

    function renderFocusStatus(statusBar, t, payload, failed) {
      if (statusBar === null || statusBar === undefined) return;
      const context =
        payload !== null && typeof payload === "object" && payload.context !== null && typeof payload.context === "object"
          ? payload.context
          : null;
      const project = context !== null && typeof context.project_path === "string" ? context.project_path : "";
      const design = context !== null && typeof context.design_name === "string" ? context.design_name : "";
      const alive = Boolean(payload !== null && typeof payload === "object" && payload.pid_alive);
      const reachable = Boolean(payload !== null && typeof payload === "object" && payload.port_reachable);
      // The AEDT script reports only project/design; pid and gRPC port are
      // optional probes. Without them the honest state is "reported, unprobed"
      // rather than a false "AEDT is not connected".
      const probed = context !== null && (typeof context.pid === "number" || typeof context.grpc_port === "number");
      const parts = [];
      let state = "warn";
      if (failed) {
        state = "error";
        parts.push(t("focus.statusError"));
      } else if (project === "") {
        state = "warn";
        parts.push(t("focus.noContext"));
      } else if (!probed) {
        state = "warn";
        parts.push(t("focus.unprobed"));
        parts.push(t("focus.project") + ": " + project);
        if (design !== "") parts.push(t("focus.design") + ": " + design);
      } else {
        const both = alive && reachable;
        state = both ? "ok" : alive || reachable ? "warn" : "error";
        parts.push(t(both ? "focus.connected" : alive || reachable ? "focus.partial" : "focus.pidDown"));
        parts.push(t("focus.project") + ": " + project);
        if (design !== "") parts.push(t("focus.design") + ": " + design);
      }
      statusBar.bar.className = "aihfss-focusbar " + state;
      statusBar.bar.title = project;
      statusBar.text.textContent = parts.join("  ·  ");
    }

    function removeFocusStatusBar(statusBar) {
      if (statusBar === null || statusBar === undefined) return;
      try {
        if (typeof statusBar.bar.remove === "function") statusBar.bar.remove();
        else if (statusBar.bar.parentNode) statusBar.bar.parentNode.removeChild(statusBar.bar);
      } catch {
        /* the shell is going away anyway */
      }
    }

    /** The host-created session id carried by the focus URL (?session= / #session=). */
    function focusSessionId() {
      const location = globalThis.location;
      if (location === undefined || location === null) return "";
      try {
        const search = typeof location.search === "string" ? location.search : "";
        const fromSearch = new URLSearchParams(search).get("session");
        if (fromSearch) return fromSearch;
        const rawHash = String(location.hash || "").replace(/^#/, "");
        if (rawHash.includes("=")) {
          const fromHash = new URLSearchParams(rawHash).get("session");
          if (fromHash) return fromHash;
        }
      } catch (error) {
        console.warn("[ai-hfss] focus mode: cannot read the session parameter", error);
      }
      return "";
    }

    /**
     * Select the session the AEDT hand-off created.
     *
     * The shell itself reads no `session` query parameter (the P0 follow-up
     * grep over the client output found no URL-search handling), so the focus
     * page opens the session through the workspace service instead. Without
     * this the page would land on whatever conversation was last open.
     */
    function openFocusSession(ctx) {
      const sessionId = focusSessionId();
      if (sessionId === "") return false;
      const select = (workspace) => {
        if (workspace === undefined || workspace === null || typeof workspace.openSession !== "function") return false;
        try {
          workspace.openSession(sessionId);
          return true;
        } catch (error) {
          console.warn("[ai-hfss] focus mode: opening the handed-off session failed", error);
          return false;
        }
      };
      let workspace;
      try {
        workspace = ctx.uiWorkspace;
        if (workspace === undefined && typeof ctx.get === "function") workspace = ctx.get("uiWorkspace");
      } catch {
        workspace = undefined;
      }
      if (select(workspace)) return true;
      if (typeof ctx.inject === "function") {
        try {
          ctx.inject(["uiWorkspace"], (workspaceCtx) => {
            select(workspaceCtx?.uiWorkspace ?? workspaceCtx);
          });
          return true;
        } catch (error) {
          console.warn("[ai-hfss] focus mode: the workspace service is unavailable", error);
        }
      }
      console.warn("[ai-hfss] focus mode: cannot select session", sessionId);
      return false;
    }

    function mountFocus(ctx, t) {
      ctx.effect(() => {
        collapseSidebarOnce(ctx);
        openFocusSession(ctx);
        const statusBar = createFocusStatusBar(t);
        if (statusBar === null) return () => {};
        let disposed = false;
        const refresh = () => {
          api("/aedt/status")
            .then((payload) => {
              if (!disposed) renderFocusStatus(statusBar, t, payload, false);
            })
            .catch(() => {
              if (!disposed) renderFocusStatus(statusBar, t, null, true);
            });
        };
        refresh();
        const timer = setInterval(refresh, FOCUS_STATUS_INTERVAL_MS);
        return () => {
          disposed = true;
          clearInterval(timer);
          removeFocusStatusBar(statusBar);
        };
      }, "ai-hfss-workstation: focus AEDT status bar");
    }

    function apply(ctx) {
      injectStyles();
      ctx.effect(() => ctx.locale.register(NS, { zh: zh, en: en }), "ai-hfss-workstation: dictionaries");
      const t = ctx.locale.bind(NS);

      if (focusRequested()) {
        // Focus leaves the main area to the conversation: neither the sidebar
        // entry nor the workbench page is registered in this mode.
        mountFocus(ctx, t);
        return;
      }

      ctx.slots.inject("main", () =>
        ctx.slots.register(
          { name: "main", key: PANEL_ID, locale: NS, inject: () => ({ t: t }) },
          WorkbenchPage
        )
      );

      ctx.slots.inject("sidebar.panellist", () =>
        ctx.slots.register(
          { name: "sidebar.panellist", id: PANEL_ID, order: 30, locale: NS, label: () => t("panel") },
          WorkstationIcon
        )
      );
    }

    exports.apply = apply;
    exports.inject = ["slots", "locale"];
    return module.exports;
  }
});
