// My account: profile, what I am allowed to do, language and theme, password, and signed-in devices.

import { api } from "../api.js";
import { applyLanguage, applyTheme, fmtAgo, fmtDateTime, getLang, t, tn } from "../i18n.js";
import { setMe, state } from "../store.js";
import {
  $, alertBox, confirmDialog, emptyState, errorMessage, errorState, html, icon, on, pageHeader, raw, setHTML, skeleton,
  toast, toastError, withBusy,
} from "../ui.js";

// Labels for the permission catalog (app/rbac.py). Kept here because /api/permissions needs admin rights.
function permLabel(key) {
  return {
    "adhan.view": t("View Adhan"),
    "adhan.schedule.edit": t("Edit Adhan schedule"),
    "adhan.sounds.manage": t("Manage Adhan sounds"),
    "adhan.broadcast": t("Start and test Adhan"),
    "adhan.broadcast.stop": t("Stop Adhan"),
    "adhan.history.view": t("View Adhan history"),
    "bells.view": t("View School Bells"),
    "bells.schedule.edit": t("Edit bell schedules"),
    "bells.sounds.manage": t("Manage bell sounds"),
    "bells.broadcast": t("Ring and test bells"),
    "bells.broadcast.stop": t("Stop bells"),
    "bells.history.view": t("View bell history"),
    "system.destinations.manage": t("Manage destinations and zones"),
    "system.sip.manage": t("Configure SIP"),
    "system.settings.manage": t("System settings"),
    "system.directory.manage": t("Configure Active Directory"),
    "system.users.manage": t("Manage users"),
    "system.roles.manage": t("Manage roles"),
    "system.audit.view": t("View audit log"),
    "system.logs.view": t("View service logs"),
  }[key] || key;
}

const PERM_ORDER = [
  "adhan.view", "adhan.schedule.edit", "adhan.sounds.manage", "adhan.broadcast", "adhan.broadcast.stop", "adhan.history.view",
  "bells.view", "bells.schedule.edit", "bells.sounds.manage", "bells.broadcast", "bells.broadcast.stop", "bells.history.view",
  "system.destinations.manage", "system.sip.manage", "system.settings.manage", "system.directory.manage", "system.users.manage",
  "system.roles.manage", "system.audit.view", "system.logs.view",
];

const BUILTIN_ROLE_NAMES = ["Full Administrator", "Adhan Administrator", "School Bells Administrator", "Operator", "Viewer"];

function roleName(name) {
  // Built-in role names are translated; custom role names are shown as typed.
  if (!BUILTIN_ROLE_NAMES.includes(name)) return name;
  return {
    "Full Administrator": t("Full Administrator"),
    "Adhan Administrator": t("Adhan Administrator"),
    "School Bells Administrator": t("School Bells Administrator"),
    Operator: t("Operator"),
    Viewer: t("Viewer"),
  }[name];
}

function viaText(via) {
  if (via === "direct") return t("assigned to you");
  if (via === "recovery") return t("recovery account");
  if (String(via).startsWith("ad:")) return t("through the AD group {group}", { group: via.slice(3) });
  return via;
}

/** "School Bells Administrator (AD group: X)" → translated. */
function sourceText(label) {
  const m = String(label).match(/^(.*) \((assigned|recovery account|AD group: (.*))\)$/);
  if (!m) return roleName(label);
  if (m[2] === "assigned") return t("{role} (assigned)", { role: roleName(m[1]) });
  if (m[2] === "recovery account") return t("{role} (recovery account)", { role: roleName(m[1]) });
  return t("{role} (AD group: {group})", { role: roleName(m[1]), group: m[3] });
}

function shortAgent(ua) {
  const s = String(ua || "");
  if (!s) return t("Unknown browser");
  const browser = /Edg\//.test(s) ? "Edge" : /OPR\//.test(s) ? "Opera" : /Firefox\//.test(s) ? "Firefox"
    : /Chrome\//.test(s) ? "Chrome" : /Safari\//.test(s) ? "Safari" : s.split(/[\s/]/)[0].slice(0, 30);
  const os = /Windows/.test(s) ? "Windows" : /Android/.test(s) ? "Android" : /iPhone|iPad/.test(s) ? "iOS"
    : /Mac OS X|Macintosh/.test(s) ? "macOS" : /Linux/.test(s) ? "Linux" : "";
  return os ? t("{browser} on {os}", { browser, os }) : browser;
}

function deviceIcon(ua) {
  return /Mobile|Android|iPhone/.test(ua || "") ? "device-mobile" : /iPad|Tablet/.test(ua || "") ? "device-tablet" : "device-desktop";
}

export default async function account(ctx) {
  const { root } = ctx;
  let sessions = null;
  let sessionsError = null;

  setHTML(root, html`
    ${pageHeader({ title: t("My account"), description: t("Your profile, what you are allowed to do, and how the app looks for you.") })}
    <div data-host>${skeleton(6)}</div>`);
  const host = $("[data-host]", root);

  async function load() {
    try {
      const me = await api("/api/auth/me");
      // Keep the server clock and permissions from the current session payload.
      setMe({ ...state.me, ...me });
      render();
      loadSessions();
    } catch (err) {
      setHTML(host, errorState(err));
    }
  }

  async function loadSessions() {
    try {
      sessions = (await api("/api/auth/sessions")).sessions;
      sessionsError = null;
    } catch (err) {
      sessionsError = err;
    }
    const el = $("[data-sessions]", host);
    if (el) setHTML(el, sessionsCard());
  }

  function render() {
    const u = state.me.user;
    setHTML(host, html`<div class="row row-cards">
      <div class="col-lg-5 d-flex flex-column gap-3">
        ${profileCard()}
        ${preferencesCard()}
        ${u.source === "local" ? passwordCard() : ""}
      </div>
      <div class="col-lg-7 d-flex flex-column gap-3">
        ${permissionsCard()}
        <div data-sessions>${sessionsCard()}</div>
      </div>
    </div>`);
  }

  function profileCard() {
    const me = state.me;
    const u = me.user;
    const initials = (u.display_name || u.username).split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
    return html`<div class="card">
      <div class="card-body">
        <div class="d-flex align-items-center gap-3 mb-3">
          <span class="avatar avatar-lg bg-primary-lt">${initials}</span>
          <div style="min-width:0">
            <div class="h3 mb-0">${u.display_name || u.username}</div>
            <div class="text-secondary ltr text-truncate">${u.username}</div>
          </div>
        </div>
        <div class="datagrid">
          <div class="datagrid-item">
            <div class="datagrid-title">${t("Account type")}</div>
            <div class="datagrid-content">${u.source === "ad" ? t("Active Directory") : t("Local account")}</div>
          </div>
          <div class="datagrid-item">
            <div class="datagrid-title">${t("Last sign-in")}</div>
            <div class="datagrid-content tabular">${u.last_login ? fmtDateTime(u.last_login) : "—"}</div>
          </div>
        </div>
        <div class="mt-3">
          <div class="datagrid-title mb-1">${t("Roles")}</div>
          ${me.roles.length ? html`<ul class="list-unstyled mb-0">
            ${me.roles.map((r) => html`<li class="d-flex flex-wrap gap-2 align-items-center py-1">
              ${icon("shield-check", "text-secondary")}<span class="fw-semibold">${roleName(r.name)}</span>
              <span class="small text-secondary">${viaText(r.via)}</span></li>`)}
          </ul>` : html`<p class="text-secondary mb-0">${t("No roles.")}</p>`}
        </div>
        ${u.is_recovery ? html`<div class="mt-3">${alertBox("warning", t("This is the recovery account. It always has full access, so keep its password safe and use personal accounts day to day."))}</div>` : ""}
        ${u.source === "ad" ? html`<p class="small text-secondary mt-3 mb-0">${t("Your password is managed by Active Directory. Change it in Windows.")}</p>` : ""}
      </div>
    </div>`;
  }

  function permissionsCard() {
    const me = state.me;
    const have = new Set(me.permissions || []);
    const groups = [
      { key: "adhan", title: t("Adhan") },
      { key: "bells", title: t("School Bells") },
      { key: "system", title: t("Administration") },
    ];
    const blocks = groups.map((g) => {
      const perms = PERM_ORDER.filter((p) => p.startsWith(g.key + ".") && have.has(p));
      if (!perms.length) return "";
      return html`<div class="module-block">
        <div class="fw-semibold mb-2">${g.title}</div>
        <ul class="list-unstyled mb-0">
          ${perms.map((p) => html`<li class="d-flex gap-2 py-1">
            ${icon("check", "text-green mt-1")}
            <div>
              <div>${permLabel(p)}</div>
              <div class="perm-source">${(me.permission_sources?.[p] || []).map(sourceText).join(" · ")}</div>
            </div>
          </li>`)}
        </ul>
      </div>`;
    });
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("What you can do")}</h2></div>
      <div class="card-body perm-matrix">
        ${have.size ? blocks : html`<p class="text-secondary mb-0">${t("You have no permissions yet. Ask an administrator.")}</p>`}
        <p class="small text-secondary mt-3 mb-0">${t("Permissions come from your roles. Ask an administrator if you need something else.")}</p>
      </div>
    </div>`;
  }

  function preferencesCard() {
    const lang = getLang();
    const mode = document.documentElement.dataset.themeMode || state.me.user.theme || "auto";
    const radio = (name, value, current, label, iconName, attrs = "") => html`<label class="form-selectgroup-item">
      <input type="radio" name="${name}" value="${value}" class="form-selectgroup-input" ${value === current ? raw("checked") : ""} ${raw(attrs)}>
      <span class="form-selectgroup-label">${iconName ? html`${icon(iconName)} ` : ""}${label}</span>
    </label>`;
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Preferences")}</h2></div>
      <div class="card-body">
        <div class="mb-3">
          <div class="form-label">${t("Language")}</div>
          <div class="form-selectgroup">
            ${radio("pref-lang", "en", lang, "English", "", 'lang="en"')}
            ${radio("pref-lang", "ar", lang, "العربية", "", 'lang="ar"')}
          </div>
          <div class="form-hint mt-1">${t("The page reloads to switch language.")}</div>
        </div>
        <div>
          <div class="form-label">${t("Theme")}</div>
          <div class="form-selectgroup">
            ${radio("pref-theme", "light", mode, t("Light"), "sun")}
            ${radio("pref-theme", "dark", mode, t("Dark"), "moon")}
            ${radio("pref-theme", "auto", mode, t("Same as device"), "device-desktop")}
          </div>
        </div>
      </div>
    </div>`;
  }

  function passwordCard() {
    return html`<form class="card" data-pw-form novalidate>
      <div class="card-header"><h2 class="card-title">${t("Change password")}</h2></div>
      <div class="card-body">
        <div data-pw-error aria-live="assertive"></div>
        <div class="mb-3">
          <label class="form-label" for="acc-pw-cur">${t("Current password")}</label>
          <input id="acc-pw-cur" class="form-control" type="password" name="current" autocomplete="current-password" required>
        </div>
        <div class="mb-3">
          <label class="form-label" for="acc-pw-new">${t("New password")}</label>
          <input id="acc-pw-new" class="form-control" type="password" name="next" autocomplete="new-password" minlength="8" required>
          <div class="form-hint">${t("At least 8 characters. Avoid common words and your username.")}</div>
        </div>
        <div class="mb-0">
          <label class="form-label" for="acc-pw-rep">${t("Repeat new password")}</label>
          <input id="acc-pw-rep" class="form-control" type="password" name="repeat" autocomplete="new-password" required>
        </div>
      </div>
      <div class="card-footer d-flex flex-wrap align-items-center gap-2">
        <span class="small text-secondary me-auto">${t("Your other devices will be signed out.")}</span>
        <button type="submit" class="btn btn-primary">${icon("key")} ${t("Change password")}</button>
      </div>
    </form>`;
  }

  function sessionsCard() {
    let body;
    if (sessionsError) {
      body = emptyState({
        iconName: "alert-triangle", title: t("This could not be loaded"), text: errorMessage(sessionsError),
        action: html`<button type="button" class="btn btn-outline-secondary" data-action="retry-sessions">${icon("refresh")} ${t("Try again")}</button>`,
      });
    } else if (!sessions) {
      body = html`<div class="card-body" aria-busy="true"><div class="skeleton-line" style="width:80%"></div><div class="skeleton-line" style="width:60%"></div></div>`;
    } else {
      body = html`<div class="list-group list-group-flush">
        ${sessions.map((s) => html`<div class="list-group-item">
          <div class="d-flex gap-3 align-items-start">
            ${icon(deviceIcon(s.user_agent), "fs-2 text-secondary")}
            <div class="flex-fill" style="min-width:0">
              <div class="d-flex flex-wrap gap-2 align-items-center">
                <span class="fw-semibold">${shortAgent(s.user_agent)}</span>
                ${s.current ? html`<span class="badge bg-green-lt">${t("This device")}</span>` : ""}
                ${s.remember ? html`<span class="badge bg-secondary-lt">${t("Kept signed in")}</span>` : ""}
              </div>
              <div class="small text-secondary">
                ${t("Signed in {when}", { when: fmtDateTime(s.created_at) })} · ${t("Last active {ago}", { ago: fmtAgo(s.last_seen) })}
                ${s.ip ? html` · <span class="ltr">${s.ip}</span>` : ""}
              </div>
            </div>
          </div>
        </div>`)}
      </div>`;
    }
    const others = sessions ? sessions.filter((s) => !s.current).length : 0;
    return html`<div class="card">
      <div class="card-header">
        <h2 class="card-title">${t("Where you are signed in")}</h2>
        ${sessions ? html`<div class="card-actions small text-secondary">${tn(sessions.length, "{n} device", "{n} devices")}</div>` : ""}
      </div>
      ${body}
      ${others ? html`<div class="card-footer d-flex flex-wrap align-items-center gap-2">
        <span class="small text-secondary me-auto">${t("Signed in somewhere you do not recognise? Sign out everywhere else, then change your password.")}</span>
        <button type="button" class="btn btn-outline-danger" data-action="end-others">${icon("logout")} ${t("Sign out other devices")}</button>
      </div>` : ""}
    </div>`;
  }

  // ------------------------------------------------------------------------
  on(root, "click", "[data-action=retry]", () => { setHTML(host, skeleton(6)); load(); });
  on(root, "click", "[data-action=retry-sessions]", () => { sessionsError = null; sessions = null; setHTML($("[data-sessions]", host), sessionsCard()); loadSessions(); });

  on(root, "change", "[name=pref-lang]", async (e, el) => {
    const next = el.value;
    if (next === getLang()) return;
    try {
      await api("/api/auth/preferences", { method: "POST", body: { language: next } });
      applyLanguage(next);
      location.reload();
    } catch (err) {
      toastError(err);
      const cur = $(`[name=pref-lang][value="${getLang()}"]`, host);
      if (cur) cur.checked = true;
    }
  });

  on(root, "change", "[name=pref-theme]", async (e, el) => {
    const theme = el.value;
    applyTheme(theme);
    // The sidebar copies the theme when the shell renders; keep it in step without a reload.
    const aside = document.querySelector("aside.navbar-vertical");
    if (aside) aside.setAttribute("data-bs-theme", document.documentElement.getAttribute("data-bs-theme"));
    try {
      const profile = await api("/api/auth/preferences", { method: "POST", body: { theme } });
      if (state.me) state.me.user = { ...state.me.user, theme: profile.user.theme };
      toast(t("Theme saved."));
    } catch (err) {
      toastError(err);
    }
  });

  on(root, "submit", "[data-pw-form]", async (e, form) => {
    e.preventDefault();
    const box = $("[data-pw-error]", form);
    box.innerHTML = "";
    const cur = form.elements.namedItem("current");
    const next = form.elements.namedItem("next");
    const rep = form.elements.namedItem("repeat");
    if (!cur.value || !next.value || !rep.value) {
      setHTML(box, alertBox("warning", t("Fill in all three password fields.")));
      (!cur.value ? cur : !next.value ? next : rep).focus();
      return;
    }
    if (next.value.length < 8) {
      setHTML(box, alertBox("warning", t("The new password must be at least 8 characters.")));
      next.focus();
      return;
    }
    if (next.value !== rep.value) {
      setHTML(box, alertBox("warning", t("The new passwords do not match.")));
      rep.focus();
      return;
    }
    try {
      await withBusy(form.querySelector("button[type=submit]"), () => api("/api/auth/change-password", {
        method: "POST", body: { current_password: cur.value, new_password: next.value },
      }));
      form.reset();
      toast(t("Password changed. Your other devices were signed out."));
      loadSessions();
    } catch (err) {
      setHTML(box, alertBox("danger", errorMessage(err)));
      if (err.code === "wrong_password") { cur.value = ""; cur.focus(); } else next.focus();
    }
  });

  on(root, "click", "[data-action=end-others]", async (e, el) => {
    const ok = await confirmDialog({
      title: t("Sign out other devices?"),
      message: t("Every other browser and device signed in to your account will be signed out. This device stays signed in."),
      confirmLabel: t("Sign out other devices"), danger: true,
    });
    if (!ok) return;
    try {
      const r = await withBusy(el, () => api("/api/auth/sessions/end-others", { method: "POST", body: {} }));
      toast(tn(r.ended, "{n} other device was signed out.", "{n} other devices were signed out."));
      loadSessions();
    } catch (err) {
      toastError(err);
    }
  });

  await load();
}
