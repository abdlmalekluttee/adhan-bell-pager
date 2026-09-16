// Application shell: sign-in, navigation, routing, live broadcast bar, clock, preferences.

import { ApiError, api, onAuthProblem } from "./api.js";
import { applyLanguage, applyTheme, fmtDuration, fmtTime, getLang, t } from "./i18n.js";
import { $, $$, alertBox, broadcastTitle, confirmDialog, destState, errorMessage, errorState, html, icon, normalizeTime, on, raw, setHTML, skeleton, toast, toastError, withBusy } from "./ui.js";
import { can, canAny, kickBroadcast, navigate, onBroadcast, pollBroadcast, serverNow, setMe, state } from "./store.js";

const app = document.getElementById("app");

// --------------------------------------------------------------------------
// Routes & navigation
// --------------------------------------------------------------------------
const ROUTES = [
  { path: "/", page: "dashboard", module: "home", title: "Dashboard", icon: "layout-dashboard", nav: "home" },
  { path: "/adhan", page: "adhan-today", module: "adhan", title: "Today", icon: "building-mosque", nav: "adhan", any: ["adhan.view"] },
  { path: "/adhan/schedule", page: "adhan-schedule", module: "adhan", title: "Prayer schedule", icon: "calendar-time", nav: "adhan", any: ["adhan.view"] },
  { path: "/adhan/location", page: "adhan-location", module: "adhan", title: "Location", icon: "map-pin", nav: "adhan", any: ["adhan.view"] },
  { path: "/adhan/sounds", page: "adhan-sounds", module: "adhan", title: "Adhan sounds", icon: "music", nav: "adhan", any: ["adhan.view"] },
  { path: "/adhan/history", page: "history", module: "adhan", title: "History", icon: "history", nav: "adhan", any: ["adhan.history.view"], props: { module: "adhan" } },
  { path: "/bells", page: "bells-today", module: "bells", title: "Today", icon: "bell-ringing", nav: "bells", any: ["bells.view"] },
  { path: "/bells/timetables", page: "bells-timetables", module: "bells", title: "Timetables", icon: "list-details", nav: "bells", any: ["bells.view"] },
  { path: "/bells/timetables/:id", page: "bells-timetable-edit", module: "bells", title: "Timetable", any: ["bells.view"], parent: "/bells/timetables" },
  { path: "/bells/schedules", page: "bells-schedules", module: "bells", title: "Weekly schedules", icon: "calendar-week", nav: "bells", any: ["bells.view"] },
  { path: "/bells/calendar", page: "bells-exceptions", module: "bells", title: "Holidays & changes", icon: "calendar-off", nav: "bells", any: ["bells.view"] },
  { path: "/bells/sounds", page: "bells-sounds", module: "bells", title: "Bell sounds", icon: "music", nav: "bells", any: ["bells.view"] },
  { path: "/bells/history", page: "history", module: "bells", title: "History", icon: "history", nav: "bells", any: ["bells.history.view"], props: { module: "bells" } },
  { path: "/admin/destinations", page: "admin-destinations", module: "system", title: "Speakers & zones", icon: "speakerphone", nav: "system", any: ["system.destinations.manage"] },
  { path: "/admin/sip", page: "admin-sip", module: "system", title: "Phone system", icon: "phone-call", nav: "system", any: ["system.sip.manage"] },
  { path: "/admin/users", page: "admin-users", module: "system", title: "Users", icon: "users", nav: "system", any: ["system.users.manage"] },
  { path: "/admin/roles", page: "admin-roles", module: "system", title: "Roles", icon: "shield-lock", nav: "system", any: ["system.roles.manage", "system.users.manage"] },
  { path: "/admin/directory", page: "admin-directory", module: "system", title: "Active Directory", icon: "sitemap", nav: "system", any: ["system.directory.manage"] },
  { path: "/admin/time", page: "admin-time", module: "system", title: "Time & priorities", icon: "clock-cog", nav: "system", any: ["system.settings.manage"] },
  { path: "/admin/audit", page: "admin-audit", module: "system", title: "Audit log", icon: "list-search", nav: "system", any: ["system.audit.view"] },
  { path: "/admin/logs", page: "admin-logs", module: "system", title: "Service logs", icon: "terminal-2", nav: "system", any: ["system.logs.view"] },
  { path: "/account", page: "account", module: "home", title: "My account", icon: "user-circle" },
];

const NAV_GROUPS = [
  { key: "adhan", title: "Adhan" },
  { key: "bells", title: "School Bells" },
  { key: "system", title: "Administration" },
];

function matchRoute(hash) {
  const [pathPart, queryPart = ""] = hash.replace(/^#/, "").split("?");
  const path = pathPart || "/";
  for (const r of ROUTES) {
    const names = [];
    const re = new RegExp("^" + r.path.replace(/:(\w+)/g, (_, n) => { names.push(n); return "([^/]+)"; }) + "/?$");
    const m = path.match(re);
    if (m) {
      const params = {};
      names.forEach((n, i) => { params[n] = decodeURIComponent(m[i + 1]); });
      return { route: r, params, query: Object.fromEntries(new URLSearchParams(queryPart)) };
    }
  }
  return null;
}

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------
let cleanup = null;
let clockTimer = null;
let unsubBroadcast = null;

async function boot() {
  let me;
  try {
    me = await api("/api/auth/me", { timeout: 10000 });
  } catch (err) {
    app.setAttribute("aria-busy", "false");
    setHTML(app, html`<div class="container-tight py-6">${errorState(err)}</div>`);
    app.querySelector("[data-action=retry]")?.addEventListener("click", () => location.reload());
    return;
  }
  if (!me.authenticated) return renderLogin(me);
  setMe(me);
  applyPreferences(me.user);
  if (me.user.must_change_password) return renderForcedPassword();
  renderShell();
}

function applyPreferences(user) {
  const stored = (() => { try { return { lang: localStorage.getItem("abp.lang"), theme: localStorage.getItem("abp.theme") }; } catch { return {}; } })();
  const lang = user.language || stored.lang || getLang();
  if (lang !== getLang()) applyLanguage(lang);
  applyTheme(user.theme || stored.theme || "auto");
}

onAuthProblem((err) => {
  if (err.code === "password_change_required") return renderForcedPassword();
  if (state.me) {
    state.me = null;
    toast(err.code === "directory_unavailable" ? t(err.message) : t("Your session has ended. Please sign in again."), "warning");
  }
  renderLogin({ directory_enabled: false });
});

// --------------------------------------------------------------------------
// Sign-in
// --------------------------------------------------------------------------
function prefToggles() {
  const mode = document.documentElement.dataset.themeMode || "auto";
  const themeIcon = { light: "sun", dark: "moon", auto: "device-desktop" }[mode];
  return html`<div class="btn-list">
    <button type="button" class="btn btn-ghost-secondary" data-action="toggle-lang" lang="${getLang() === "ar" ? "en" : "ar"}">
      ${icon("language")} ${getLang() === "ar" ? "English" : "العربية"}</button>
    <div class="dropdown">
      <button type="button" class="btn btn-ghost-secondary" data-bs-toggle="dropdown" aria-expanded="false" aria-label="${t("Theme")}">
        ${icon(themeIcon)} <span class="d-none d-sm-inline">${t("Theme")}</span></button>
      <div class="dropdown-menu dropdown-menu-end">
        ${["light", "dark", "auto"].map((m) => html`<button type="button" class="dropdown-item ${m === mode ? "active" : ""}" data-action="theme" data-theme="${m}">
          ${icon({ light: "sun", dark: "moon", auto: "device-desktop" }[m], "me-2")} ${t({ light: "Light", dark: "Dark", auto: "Same as device" }[m])}</button>`)}
      </div>
    </div>
  </div>`;
}

// Shell-level actions are bound once; views re-render through currentView().
let currentView = () => {};

function bindGlobalActions() {
  // 24-hour time fields (pages and dialogs): tidy "800" into "08:00" when leaving the field.
  document.addEventListener("focusout", (e) => {
    const el = e.target;
    if (el instanceof HTMLInputElement && el.hasAttribute("data-time24")) {
      const v = normalizeTime(el.value);
      if (v !== el.value) {
        el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  });
  on(app, "click", "[data-action=toggle-lang]", () => {
    applyLanguage(getLang() === "ar" ? "en" : "ar");
    if (state.me) api("/api/auth/preferences", { method: "POST", body: { language: getLang() } }).catch(() => {});
    currentView();
  });
  on(app, "click", "[data-action=theme]", (e, el) => {
    applyTheme(el.dataset.theme);
    if (state.me) api("/api/auth/preferences", { method: "POST", body: { theme: el.dataset.theme } }).catch(() => {});
    currentView();
  });
  on(app, "click", "[data-action=reveal]", (e, el) => {
    const input = $("#login-pass");
    if (!input) return;
    input.type = input.type === "password" ? "text" : "password";
    el.innerHTML = icon(input.type === "password" ? "eye" : "eye-off").s;
  });
  on(app, "click", "[data-action=logout]", async (e, el) => {
    if (el.dataset.confirm === "no" || await confirmDialog({ title: t("Sign out?"), message: t("You will need your password to sign in again."), confirmLabel: t("Sign out") })) logout();
  });
  on(app, "click", "[data-action=stop-broadcast]", stopBroadcast);
  on(app, "click", ".navbar-vertical .nav-link", () => {
    const menu = $("#sidebar-menu");
    if (menu && menu.classList.contains("show") && window.tabler?.Collapse) window.tabler.Collapse.getOrCreateInstance(menu).hide();
  });
}

const ARCHES = raw(`<svg class="arches" viewBox="0 0 400 600" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs><pattern id="arch" width="80" height="110" patternUnits="userSpaceOnUse">
    <path d="M10 110V58C10 32 40 18 40 4c0 14 30 28 30 54v52" fill="none" stroke="#f4efe2" stroke-width="2"/></pattern></defs>
  <rect width="400" height="600" fill="url(#arch)"/></svg>`);

function renderLogin(info = {}) {
  teardown();
  app.setAttribute("aria-busy", "false");
  document.title = t("Sign in") + " · " + t("Adhan & Bell Pager");
  const dir = info.directory_enabled;
  setHTML(app, html`<div class="login-page">
    <section class="login-art" aria-hidden="true">${ARCHES}
      <div class="position-relative">
        <img src="/static/img/logo.svg" width="56" height="56" alt="" class="mb-4">
        <h1>${t("The Adhan and the school bell, on every speaker, on time.")}</h1>
        <p>${t("Plan prayer times and class timetables in one place. Broadcasts go out through your phone system automatically.")}</p>
      </div>
    </section>
    <main class="login-panel" id="main">
      <div class="login-card">
        <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-4">
          <div class="d-flex align-items-center gap-2 text-nowrap">
            <img src="/static/img/logo.svg" width="36" height="36" alt="">
            <span class="navbar-brand-name">${t("Adhan & Bell Pager")}</span>
          </div>
          <div class="login-prefs">${prefToggles()}</div>
        </div>
        <h2 class="h2 mb-1">${t("Sign in")}</h2>
        <p class="text-secondary mb-4">${dir ? t("Use your school account (for example SCHOOL\\name) or a local account.") : t("Use the account your administrator gave you.")}</p>
        <form id="login-form" novalidate>
          <div id="login-error" aria-live="assertive"></div>
          <div class="mb-3">
            <label class="form-label" for="login-user">${t("Username")}</label>
            <input id="login-user" class="form-control ltr" name="username" autocomplete="username" autocapitalize="off" spellcheck="false" required autofocus>
          </div>
          <div class="mb-3">
            <label class="form-label" for="login-pass">${t("Password")}</label>
            <div class="input-group input-group-flat">
              <input id="login-pass" class="form-control" type="password" name="password" autocomplete="current-password" required>
              <span class="input-group-text py-0"><button type="button" class="btn btn-link p-0 link-secondary lh-1" data-action="reveal" aria-label="${t("Show password")}">${icon("eye")}</button></span>
            </div>
          </div>
          <label class="form-check mb-4"><input type="checkbox" class="form-check-input" name="remember"><span class="form-check-label">${t("Keep me signed in on this device")}</span></label>
          <button type="submit" class="btn btn-primary w-100">${icon("login-2")} ${t("Sign in")}</button>
        </form>
      </div>
    </main>
  </div>`);
  currentView = () => renderLogin(info);
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const errBox = $("#login-error");
    errBox.innerHTML = "";
    const body = { username: form.username.value.trim(), password: form.password.value, remember: form.remember.checked };
    if (!body.username || !body.password) {
      errBox.innerHTML = alertBox("warning", t("Enter your username and password.")).s;
      return;
    }
    try {
      const me = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/auth/login", { method: "POST", body }));
      setMe(me);
      applyPreferences(me.user);
      if (me.user.must_change_password) return renderForcedPassword();
      if (!location.hash || location.hash === "#/login") location.hash = "#/";
      renderShell();
    } catch (err) {
      let msg = errorMessage(err);
      if (err instanceof ApiError && err.code === "locked") {
        msg = t("Too many attempts. Wait {time} and try again.", { time: fmtDuration(err.detail.retry_after) });
      }
      errBox.innerHTML = alertBox("danger", msg).s;
      form.password.value = "";
      form.password.focus();
    }
  });
}

function renderForcedPassword() {
  teardown();
  app.setAttribute("aria-busy", "false");
  document.title = t("Choose a new password");
  setHTML(app, html`<div class="page page-center"><div class="container container-tight py-4" id="main">
    <div class="text-center mb-4"><img src="/static/img/logo.svg" width="48" height="48" alt=""></div>
    <form class="card card-md" id="pw-form" novalidate>
      <div class="card-body">
        <h2 class="card-title text-center mb-2">${t("Choose a new password")}</h2>
        <p class="text-secondary text-center mb-4">${t("For security, set your own password before you continue.")}</p>
        <div id="pw-error"></div>
        <div class="mb-3"><label class="form-label" for="pw-cur">${t("Current password")}</label>
          <input id="pw-cur" class="form-control" type="password" name="current" autocomplete="current-password" required></div>
        <div class="mb-3"><label class="form-label" for="pw-new">${t("New password")}</label>
          <input id="pw-new" class="form-control" type="password" name="next" autocomplete="new-password" minlength="8" required>
          <div class="form-hint">${t("At least 8 characters. Avoid common words and your username.")}</div></div>
        <div class="mb-4"><label class="form-label" for="pw-rep">${t("Repeat new password")}</label>
          <input id="pw-rep" class="form-control" type="password" name="repeat" autocomplete="new-password" required></div>
        <button class="btn btn-primary w-100" type="submit">${t("Save password and continue")}</button>
        <button class="btn btn-link w-100 mt-2" type="button" data-action="logout" data-confirm="no">${t("Sign out")}</button>
      </div>
    </form></div></div>`);
  currentView = renderForcedPassword;
  $("#pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    const box = $("#pw-error");
    box.innerHTML = "";
    if (f.next.value !== f.repeat.value) { box.innerHTML = alertBox("warning", t("The new passwords do not match.")).s; return; }
    try {
      const me = await withBusy(f.querySelector("button[type=submit]"), () => api("/api/auth/change-password", {
        method: "POST", body: { current_password: f.current.value, new_password: f.next.value } }));
      const full = await api("/api/auth/me");
      setMe(full);
      toast(t("Password changed."));
      renderShell();
    } catch (err) {
      box.innerHTML = alertBox("danger", errorMessage(err)).s;
    }
  });
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST", body: {} }); } catch { /* already signed out */ }
  state.me = null;
  location.hash = "#/";
  renderLogin({ directory_enabled: false });
}

// --------------------------------------------------------------------------
// Shell
// --------------------------------------------------------------------------
function navItems() {
  const visible = ROUTES.filter((r) => r.nav && canAny(r.any));
  const home = visible.find((r) => r.nav === "home");
  return html`
    <ul class="navbar-nav pt-lg-2">
      ${home ? navLink(home) : ""}
      ${NAV_GROUPS.map((g) => {
        const items = visible.filter((r) => r.nav === g.key);
        if (!items.length) return "";
        return html`<li class="nav-group-title ${g.key}" role="presentation"><span class="dot"></span>${t(g.title)}</li>${items.map(navLink)}`;
      })}
    </ul>`;
}

function navLink(r) {
  return html`<li class="nav-item ${r.nav}"><a class="nav-link" href="#${r.path}" data-path="${r.path}">
    <span class="nav-link-icon">${icon(r.icon)}</span><span class="nav-link-title">${t(r.title)}</span></a></li>`;
}

function renderShell() {
  teardown();
  app.setAttribute("aria-busy", "false");
  const me = state.me;
  const u = me.user;
  const roleNames = [...new Set(me.roles.map((r) => t(r.name)))].join(", ");
  setHTML(app, html`
    <div class="page">
      <aside class="navbar navbar-vertical navbar-expand-lg" data-bs-theme="${document.documentElement.getAttribute("data-bs-theme")}">
        <div class="container-fluid">
          <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#sidebar-menu" aria-controls="sidebar-menu" aria-expanded="false" aria-label="${t("Menu")}">
            <span class="navbar-toggler-icon"></span></button>
          <a href="#/" class="navbar-brand navbar-brand-autodark d-flex align-items-center gap-2 py-3">
            <img src="/static/img/logo.svg" width="34" height="34" alt="">
            <span class="navbar-brand-name text-start">${t("Adhan & Bell Pager")}<small>${me.server?.version ? "v" + me.server.version : ""}</small></span>
          </a>
          <div class="navbar-nav flex-row d-lg-none">${userMenu(u, roleNames)}</div>
          <div class="collapse navbar-collapse" id="sidebar-menu">${navItems()}</div>
        </div>
      </aside>
      <div class="page-wrapper">
        <header class="navbar navbar-expand-md d-print-none">
          <div class="container-xl">
            <div class="topbar-clock" aria-live="off">
              <div class="clock tabular" id="clock">--:--</div>
              <small id="clock-date">${me.server?.timezone || ""}</small>
            </div>
            <div class="navbar-nav flex-row order-md-last ms-auto align-items-center gap-1">
              <div id="sip-indicator" class="d-none d-sm-flex"></div>
              <div class="d-none d-md-flex">${prefToggles()}</div>
              <div class="d-none d-lg-flex">${userMenu(u, roleNames)}</div>
            </div>
          </div>
        </header>
        ${me.server?.simulated ? html`<div class="sim-banner">${icon("flask")} ${t("Simulation mode: no real calls are made. Set up PJSIP to page speakers.")}</div>` : ""}
        <div id="live-bar"></div>
        <main class="page-body" id="main" tabindex="-1">
          <div class="container-xl" id="page-root"></div>
        </main>
      </div>
    </div>`);
  currentView = renderShell;
  startClock();
  window.addEventListener("hashchange", renderRoute);
  unsubBroadcast?.();
  unsubBroadcast = onBroadcast(renderLiveBar);
  pollBroadcast();
  refreshSipIndicator();
  renderRoute();
}

function userMenu(u, roleNames) {
  const initials = (u.display_name || u.username).split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
  return html`<div class="nav-item dropdown">
    <a href="#" class="nav-link d-flex lh-1 text-reset p-0 px-2" data-bs-toggle="dropdown" aria-label="${t("Account menu")}">
      <span class="avatar avatar-sm bg-primary-lt">${initials}</span>
      <div class="d-none d-xl-block ps-2 text-start">
        <div>${u.display_name || u.username}</div>
        <div class="mt-1 small text-secondary text-truncate" style="max-width:14rem">${roleNames}</div>
      </div>
    </a>
    <div class="dropdown-menu dropdown-menu-end dropdown-menu-arrow">
      <a href="#/account" class="dropdown-item">${icon("user-circle", "me-2")} ${t("My account")}</a>
      <button type="button" class="dropdown-item d-md-none" data-action="toggle-lang">${icon("language", "me-2")} ${getLang() === "ar" ? "English" : "العربية"}</button>
      <div class="dropdown-divider d-md-none"></div>
      ${["light", "dark", "auto"].map((m) => html`<button type="button" class="dropdown-item d-md-none" data-action="theme" data-theme="${m}">
        ${icon({ light: "sun", dark: "moon", auto: "device-desktop" }[m], "me-2")} ${t({ light: "Light", dark: "Dark", auto: "Same as device" }[m])}</button>`)}
      <div class="dropdown-divider"></div>
      <button type="button" class="dropdown-item" data-action="logout">${icon("logout", "me-2")} ${t("Sign out")}</button>
    </div>
  </div>`;
}

function teardown() {
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  clearInterval(clockTimer);
  window.removeEventListener("hashchange", renderRoute);
}

function startClock() {
  const tick = () => {
    const el = $("#clock");
    if (!el) return;
    const now = serverNow();
    el.textContent = fmtTime(now, { seconds: true });
    const d = $("#clock-date");
    if (d) d.textContent = new Intl.DateTimeFormat(getLang() === "ar" ? "ar-u-nu-latn" : "en-GB", {
      weekday: "short", day: "numeric", month: "short", timeZone: state.me?.server?.timezone }).format(now) + " · " + (state.me?.server?.timezone || "");
  };
  tick();
  clockTimer = setInterval(tick, 1000);
}

async function refreshSipIndicator() {
  const el = $("#sip-indicator");
  if (!el || !canAny(["adhan.view", "bells.view", "system.sip.manage"])) return;
  try {
    const s = await api("/api/system/sip/status", { timeout: 8000 });
    const ok = s.registered;
    const label = s.simulated ? t("Simulated") : ok ? t("Phone system connected") : t("Phone system not connected");
    const link = can("system.sip.manage") ? "#/admin/sip" : "#/";
    el.innerHTML = html`<a href="${link}" class="btn btn-ghost-secondary" title="${t(s.message)}">
      <span class="status-dot ${ok ? "status-green" : "status-red"} ${ok ? "" : "status-dot-animated"} me-2"></span>
      <span class="d-none d-xl-inline">${label}</span></a>`.s;
  } catch { /* the dashboard shows details */ }
  setTimeout(refreshSipIndicator, 30000);
}

// --------------------------------------------------------------------------
// Live broadcast bar
// --------------------------------------------------------------------------
function renderLiveBar(b) {
  const host = $("#live-bar");
  if (!host) return;
  if (!b || (!b.active && !b.busy_other_module)) { host.innerHTML = ""; return; }
  if (!b.active) {
    host.innerHTML = html`<div class="live-bar"><div class="container-xl inner">
      <span class="live-icon" aria-hidden="true">${icon("volume")}</span><span class="meta">${t("The paging line is busy.")}</span></div></div>`.s;
    return;
  }
  const a = b.active;
  const line = a.line || {};
  const pct = line.duration && line.elapsed_s !== undefined ? Math.min(100, (line.elapsed_s / line.duration) * 100) : null;
  const dests = (line.destinations || []).map((d) => `${d.uri}: ${t(destState(d.state))}`).join(" · ");
  const canStop = can(`${a.module}.broadcast.stop`);
  host.innerHTML = html`<div class="live-bar ${a.module}" role="status">
    <div class="container-xl inner">
      <span class="live-icon" aria-hidden="true">${icon("volume")}</span>
      <div class="me-auto">
        <div class="title">${t("Now playing")}: ${broadcastTitle(a.title)}</div>
        <div class="meta">${dests || t("Calling…")}${a.actor && a.manual ? " · " + t("started by {name}", { name: a.actor }) : ""}</div>
      </div>
      ${pct !== null ? html`<div class="d-flex align-items-center gap-2"><div class="progress" role="progressbar" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100" aria-label="${t("Playback progress")}"><div class="progress-bar" style="--p:${(pct / 100).toFixed(3)}"></div></div>
        <span class="meta tabular">${fmtDuration(line.remaining_s)}</span></div>` : ""}
      ${canStop ? html`<button type="button" class="btn btn-danger" data-action="stop-broadcast">${icon("player-stop-filled")} ${t("Stop")}</button>` : ""}
    </div></div>`.s;
}

async function stopBroadcast(e, el) {
  const title = state.broadcast?.active ? broadcastTitle(state.broadcast.active.title) : "";
  if (!await confirmDialog({ title: t("Stop the broadcast?"), message: t("“{title}” will stop immediately on every speaker.", { title }), confirmLabel: t("Stop now"), danger: true })) return;
  try {
    const r = await withBusy(el, () => api("/api/broadcast/stop", { method: "POST", body: {} }));
    toast(r.stopped ? t("Stopped.") : t("Nothing was playing."), r.stopped ? "success" : "info");
  } catch (err) {
    toastError(err);
  }
  kickBroadcast();
}

// --------------------------------------------------------------------------
// Router
// --------------------------------------------------------------------------
async function renderRoute() {
  if (!state.me) return;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  $$("dialog[open]").forEach((d) => d.close());      // dialogs belong to the page being left
  const old = $("#page-root");
  if (!old) return;
  const root = old.cloneNode(false);        // drops listeners bound by the previous page
  old.replaceWith(root);
  const wrapper = $(".page-wrapper");
  const found = matchRoute(location.hash || "#/");
  $$(".navbar-vertical .nav-link").forEach((a) => {
    const path = a.dataset.path;
    const active = found && (found.route.path === path || found.route.parent === path);
    a.classList.toggle("active", !!active);
    if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (!found) {
    wrapper.dataset.module = "home";
    setHTML(root, html`<div class="empty"><div class="empty-header">404</div><p class="empty-title">${t("Page not found")}</p>
      <div class="empty-action"><a href="#/" class="btn btn-primary">${t("Go to the dashboard")}</a></div></div>`);
    return;
  }
  const { route, params, query } = found;
  wrapper.dataset.module = route.module;
  document.title = `${t(route.title)} · ${t("Adhan & Bell Pager")}`;
  if (!canAny(route.any)) {
    setHTML(root, html`<div class="empty"><div class="empty-icon">${icon("lock")}</div>
      <p class="empty-title">${t("You do not have access to this page")}</p>
      <p class="empty-subtitle text-secondary">${t("Ask an administrator if you need it.")}</p>
      <div class="empty-action"><a href="#/" class="btn btn-primary">${t("Go to the dashboard")}</a></div></div>`);
    return;
  }
  setHTML(root, skeleton(5));
  const token = Symbol("render");
  renderRoute.current = token;
  try {
    const mod = await import(`./pages/${route.page}.js`);
    if (renderRoute.current !== token) return;
    root.innerHTML = "";
    const ctx = { root, params, query, route, props: route.props || {}, reload: renderRoute };
    const result = await mod.default(ctx);
    if (renderRoute.current === token) cleanup = typeof result === "function" ? result : null;
    else if (typeof result === "function") result();
    if (document.activeElement === document.body) $("#main")?.focus({ preventScroll: true });
  } catch (err) {
    console.error(err);
    if (renderRoute.current !== token) return;
    setHTML(root, errorState(err));
    root.querySelector("[data-action=retry]")?.addEventListener("click", () => renderRoute());
  }
}

bindGlobalActions();
boot();
