// Administration › Service logs: the technical log for troubleshooting, with filters and live updates.

import { api } from "../api.js";
import { t, tn } from "../i18n.js";
import { $, debounce, emptyState, errorMessage, html, icon, on, pageHeader, setHTML } from "../ui.js";

const LINE_RE = /^(\S+ \S+) (DEBUG|INFO|WARNING|ERROR|CRITICAL) ([^\s:]+): /;
const LEVEL_RANK = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 3 };
const COMPONENTS = {
  sip: ["sip", "pjsip"],
  scheduler: ["scheduler", "dispatcher", "planner"],
  directory: ["directory"],
  web: ["auth", "main"],
  audit: ["audit"],
};

function parse(line) {
  const m = LINE_RE.exec(line);
  if (!m) return { line, level: "", logger: "" };
  return { line, level: m[2], logger: m[3].split(".")[0].toLowerCase() };
}

export default async function adminLogs(ctx) {
  const { root } = ctx;
  const view = { severity: "all", component: "all", search: "", live: false, follow: true };
  let entries = [];
  let lastKey = "";
  let timer = null;
  let disposed = false;
  let loadedOnce = false;

  function renderShell() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Service logs"),
        description: t("Technical messages from the service, for troubleshooting with your IT support. Passwords are masked."),
        actions: html`<a class="btn btn-outline-secondary" href="/api/system/logs/export" download>${icon("download")} ${t("Download full log")}</a>`,
      })}
      <div class="card">
        <div class="card-body border-bottom">
          <div class="row g-2 align-items-end">
            <div class="col-6 col-md-3 col-lg-2">
              <label class="form-label" for="log-severity">${t("Severity")}</label>
              <select id="log-severity" class="form-select" data-filter="severity">
                <option value="all">${t("All")}</option>
                <option value="error">${t("Errors")}</option>
                <option value="warning">${t("Warnings and errors")}</option>
                <option value="info">${t("Info")}</option>
              </select>
            </div>
            <div class="col-6 col-md-3 col-lg-2">
              <label class="form-label" for="log-component">${t("Component")}</label>
              <select id="log-component" class="form-select" data-filter="component">
                <option value="all">${t("All")}</option>
                <option value="sip">${t("SIP")}</option>
                <option value="scheduler">${t("Scheduler")}</option>
                <option value="directory">${t("Directory")}</option>
                <option value="web">${t("Web")}</option>
                <option value="audit">${t("Audit")}</option>
              </select>
            </div>
            <div class="col-12 col-md-6 col-lg-4">
              <label class="form-label" for="log-search">${t("Search")}</label>
              <div class="input-icon">
                <span class="input-icon-addon">${icon("search")}</span>
                <input id="log-search" class="form-control" type="search" data-filter="search" autocomplete="off" spellcheck="false"
                  placeholder="${t("Text in the message")}">
              </div>
            </div>
            <div class="col-12 col-lg-4 d-flex flex-wrap align-items-center gap-3 justify-content-lg-end pb-1">
              <label class="form-check form-switch mb-0">
                <input class="form-check-input" type="checkbox" data-filter="live">
                <span class="form-check-label">${t("Live")}</span>
              </label>
              <label class="form-check form-switch mb-0">
                <input class="form-check-input" type="checkbox" data-filter="follow" checked>
                <span class="form-check-label">${t("Scroll to newest")}</span>
              </label>
              <button type="button" class="btn btn-icon btn-ghost-secondary" data-action="refresh" aria-label="${t("Refresh")}" title="${t("Refresh")}">${icon("refresh")}</button>
            </div>
          </div>
        </div>
        <div class="card-body">
          <div class="d-flex flex-wrap align-items-center gap-2 mb-2 small text-secondary">
            <span data-region="count" class="tabular" aria-live="polite"></span>
            <span data-region="live-state" class="ms-auto"></span>
          </div>
          <div data-region="log">
            <div aria-busy="true" aria-label="${t("Loading")}">${[88, 70, 94, 62].map((w) => html`<div class="skeleton-line" style="width:${w}%"></div>`)}</div>
          </div>
        </div>
      </div>`);
  }

  async function fetchLines({ quiet = false } = {}) {
    try {
      const r = await api("/api/system/logs", { query: { limit: 1000 }, timeout: 15000 });
      const lines = r.lines || [];
      const key = `${lines.length}|${lines[lines.length - 1] || ""}`;
      loadedOnce = true;
      if (key === lastKey && quiet) return;
      lastKey = key;
      entries = lines.map(parse);
      renderLog();
    } catch (err) {
      if (!loadedOnce) {
        setHTML($("[data-region=log]", root), emptyState({ iconName: "alert-triangle", title: t("This could not be loaded"), text: errorMessage(err),
          action: html`<button type="button" class="btn btn-primary" data-action="refresh">${icon("refresh")} ${t("Try again")}</button>` }));
      } else {
        setHTML($("[data-region=live-state]", root), html`<span class="text-warning">${icon("alert-triangle")} ${errorMessage(err)}</span>`);
      }
    }
  }

  function visible() {
    const q = view.search.toLowerCase();
    const allowed = view.component === "all" ? null : COMPONENTS[view.component];
    return entries.filter((e) => {
      if (view.severity !== "all") {
        const rank = LEVEL_RANK[e.level];
        if (view.severity === "error" && rank !== 3) return false;
        if (view.severity === "warning" && !(rank >= 2)) return false;
        if (view.severity === "info" && e.level !== "INFO") return false;
      }
      if (allowed && !allowed.includes(e.logger)) return false;
      if (q && !e.line.toLowerCase().includes(q)) return false;
      return true;
    });
  }

  function renderLog() {
    const host = $("[data-region=log]", root);
    if (!host) return;
    const list = visible();
    setHTML($("[data-region=count]", root), list.length === entries.length
      ? tn(entries.length, "{n} line", "{n} lines")
      : t("{shown} of {total}", { shown: list.length, total: tn(entries.length, "{n} line", "{n} lines") }));
    setHTML($("[data-region=live-state]", root), view.live
      ? html`<span class="status-dot status-green status-dot-animated me-1"></span>${t("Live, updates every 3 seconds")}` : "");
    if (!entries.length) {
      setHTML(host, emptyState({ iconName: "file-text", title: t("The log is empty"), text: t("Messages appear here as the service runs.") }));
      return;
    }
    if (!list.length) {
      setHTML(host, emptyState({ iconName: "filter-off", title: t("No lines match these filters"), text: t("Choose another severity or component, or change the search text.") }));
      return;
    }
    let pane = $(".log-view", host);
    const atBottom = pane ? pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40 : true;
    const prevTop = pane ? pane.scrollTop : 0;
    // One HTML string for the whole list keeps 2000 lines fast; every line is escaped by html``.
    const body = html`${list.map((e) => (e.level === "ERROR" || e.level === "CRITICAL"
      ? html`<span class="lvl-ERROR">${e.line}</span>\n`
      : e.level === "WARNING" ? html`<span class="lvl-WARNING">${e.line}</span>\n` : html`${e.line}\n`))}`;
    if (!pane) {
      setHTML(host, html`<div class="log-view" tabindex="0" role="log" aria-label="${t("Service log lines")}"></div>`);
      pane = $(".log-view", host);
    }
    setHTML(pane, body);
    if (view.follow && (atBottom || !loadedOnceScroll)) pane.scrollTop = pane.scrollHeight;
    else pane.scrollTop = prevTop;
    loadedOnceScroll = true;
  }
  let loadedOnceScroll = false;

  function schedule() {
    clearTimeout(timer);
    if (disposed || !view.live) return;
    timer = setTimeout(async () => {
      if (!document.hidden) await fetchLines({ quiet: true });
      schedule();
    }, 3000);
  }

  const searchChanged = debounce((value) => { view.search = value.trim(); renderLog(); }, 200);
  on(root, "change", "select[data-filter]", (e, el) => { view[el.dataset.filter] = el.value; renderLog(); });
  on(root, "input", "input[data-filter=search]", (e, el) => searchChanged(el.value));
  on(root, "change", "input[data-filter=live]", (e, el) => {
    view.live = el.checked;
    renderLog();
    if (view.live) fetchLines({ quiet: true }).then(schedule);
    else clearTimeout(timer);
  });
  on(root, "change", "input[data-filter=follow]", (e, el) => {
    view.follow = el.checked;
    const pane = $(".log-view", root);
    if (pane && view.follow) pane.scrollTop = pane.scrollHeight;
  });
  on(root, "click", "[data-action=refresh]", () => fetchLines());

  renderShell();
  await fetchLines();
  return () => { disposed = true; clearTimeout(timer); };
}
