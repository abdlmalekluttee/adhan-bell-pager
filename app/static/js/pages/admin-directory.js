// Administration › Active Directory: directory sign-in settings, connection test, user check and group mappings.

import { api } from "../api.js";
import { fmtDateTime, t, tn } from "../i18n.js";
import { can } from "../store.js";
import {
  $, alertBox, confirmDialog, debounce, dirtyTracker, emptyState, errorMessage, errorState, field, formDialog, formValues, html,
  icon, on, pageHeader, saveBar, selectField, setHTML, switchField, toast, toastError, withBusy,
} from "../ui.js";

function connectionTip(code) {
  return {
    unavailable: t("Check the server addresses, that this server can reach port 636 (LDAPS) or 389 (StartTLS), and that the names resolve in DNS."),
    tls_error: t("The secure connection was not trusted. Check that the address matches the domain controller's certificate name, or paste your CA certificate."),
    invalid_credentials: t("Check the bind account and its password. The account must not be locked or expired."),
    not_configured: t("Add at least one domain controller address."),
    search_failed: t("Signed in, but searching failed. Check the base DN and that the bind account may read the directory."),
  }[code] || "";
}

export default async function adminDirectory(ctx) {
  const { root } = ctx;
  let cfg = null;
  let mappings = [];
  let roles = [];
  let tracker = null;

  async function load() {
    try {
      const [d, r] = await Promise.all([api("/api/directory"), api("/api/roles")]);
      cfg = d.config;
      mappings = d.mappings;
      roles = r.roles;
      renderPage();
    } catch (err) {
      setHTML(root, errorState(err));
    }
  }

  function renderPage() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Active Directory"),
        description: t("Let staff sign in with their school Windows account, and decide what each directory group may do."),
      })}
      <div class="row row-cards">
        <div class="col-lg-4 order-lg-2">${howItWorksCard()}</div>
        <div class="col-lg-8 order-lg-1">
          <div class="row row-cards">
            <div class="col-12">
              <form class="card" data-form="settings" novalidate autocomplete="off">
                <div class="card-header">
                  <div>
                    <h2 class="card-title">${t("Connection")}</h2>
                    <p class="card-subtitle mb-0" data-region="state">${stateLine()}</p>
                  </div>
                </div>
                <div class="card-body" data-region="settings">${settingsFields()}</div>
                <div class="card-footer d-flex flex-wrap align-items-center gap-2">
                  <button type="button" class="btn btn-outline-secondary" data-action="test">${icon("plug-connected")} ${t("Test connection")}</button>
                  <span class="small text-secondary">${t("Uses the values above, even before you save them.")}</span>
                </div>
                <div data-region="test-result" aria-live="polite"></div>
              </form>
            </div>
            <div class="col-12" data-region="mappings">${mappingsCard()}</div>
            <div class="col-12">${lookupCard()}</div>
          </div>
        </div>
      </div>
      ${saveBar()}`);
    tracker = dirtyTracker($("form[data-form=settings] [data-region=settings]", root), $(".save-bar", root), {
      onSave: save,
      onDiscard: () => { setHTML($("[data-region=settings]", root), settingsFields()); setHTML($("[data-region=test-result]", root), ""); },
    });
  }

  function stateLine() {
    return cfg.enabled
      ? html`<span class="status-dot status-green me-1"></span>${t("Directory sign-in is on.")}`
      : html`<span class="status-dot status-secondary me-1"></span>${t("Directory sign-in is off. Only local accounts can sign in.")}`;
  }

  function howItWorksCard() {
    const rules = [
      { i: "user-shield", text: t("Local accounts, including the recovery administrator, always work — even when the directory is down.") },
      { i: "users-group", text: t("A directory user can only sign in if they belong to at least one group that is mapped to a role below.") },
      { i: "stack-2", text: t("Their permissions are everything their mapped groups allow, plus any roles given to them directly on the Users page.") },
      { i: "refresh", text: t("Group membership is checked at every sign-in and again every {n} minutes while they use the app. Removing someone from a group takes effect within that time.", { n: cfg.refresh_minutes }) },
      { i: "cloud-off", text: t("If the directory cannot be reached, people already signed in keep their access for {n} minutes. New directory sign-ins fail until it is back.", { n: cfg.outage_grace_minutes }) },
      { i: "lock", text: t("Disabled or deleted directory accounts cannot sign in, and their sessions end at the next check.") },
      { i: "key-off", text: t("Domain passwords are never stored. Each sign-in is checked directly with Active Directory.") },
    ];
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${icon("info-circle", "me-2")}${t("How directory sign-in works")}</h2></div>
      <ul class="list-group list-group-flush">
        ${rules.map((r) => html`<li class="list-group-item d-flex gap-2">${icon(r.i, "text-secondary mt-1")}<span>${r.text}</span></li>`)}
      </ul>
    </div>`;
  }

  // ---- settings ------------------------------------------------------------
  function settingsFields() {
    const c = cfg;
    return html`
      ${switchField({ label: t("Allow sign-in with school (Active Directory) accounts"), name: "enabled", checked: c.enabled })}
      <div class="mb-3">
        <label class="form-label" for="ad-servers">${t("Domain controllers")}</label>
        <textarea id="ad-servers" class="form-control ltr" name="server_urls" rows="3" spellcheck="false" autocapitalize="off"
          placeholder="ldaps://dc1.school.local&#10;ldaps://dc2.school.local">${(c.server_urls || []).join("\n")}</textarea>
        <div class="form-hint">${t("One address per line. The first that answers is used; add a second domain controller as a backup.")}</div>
      </div>
      <div class="row">
        ${selectField({ label: t("Connection security"), name: "security", value: c.security, cls: "col-md-6",
          options: [{ value: "ldaps", label: t("LDAPS, port 636 (recommended)") }, { value: "starttls", label: t("StartTLS, port 389") }],
          hint: t("Addresses must start with ldaps:// for LDAPS, or ldap:// for StartTLS. The connection is always encrypted.") })}
        <div class="col-md-6">
          ${switchField({ label: t("Check the domain controller's certificate"), name: "validate_cert", checked: c.validate_cert,
            hint: t("Keep this on. It proves you are talking to your real domain controller.") })}
        </div>
      </div>
      <div data-region="cert-warning">${certWarning(c.validate_cert)}</div>
      <div class="mb-3">
        <label class="form-label" for="ad-ca">${t("CA certificate (optional)")}</label>
        <textarea id="ad-ca" class="form-control ltr font-monospace small" name="ca_cert_pem" rows="3" spellcheck="false"
          placeholder="-----BEGIN CERTIFICATE-----">${c.ca_cert_pem}</textarea>
        <div class="form-hint">${t("Paste your school's certificate authority (PEM text) if the domain controllers use certificates from your own PKI.")}</div>
      </div>
      <div class="row">
        ${field({ label: t("Bind account"), name: "bind_dn", value: c.bind_dn, ltr: true, cls: "col-md-6",
          placeholder: "CN=svc-pager,OU=Service Accounts,DC=school,DC=local",
          hint: t("A read-only service account used to look people up, as a distinguished name (DN)."), attrs: 'spellcheck="false" autocapitalize="off"' })}
        <div class="col-md-6 mb-3">
          <label class="form-label" for="ad-bind-pw">${t("Bind account password")}
            ${c.has_bind_password ? html`<span class="badge bg-green-lt ms-2">${icon("lock")} ${t("Saved")}</span>` : ""}</label>
          <input id="ad-bind-pw" class="form-control" type="password" name="bind_password" value="" autocomplete="new-password"
            placeholder="${c.has_bind_password ? t("Unchanged") : ""}">
          <div class="form-hint">${c.has_bind_password ? t("Leave blank to keep the current password.") : t("No password is saved yet.")}</div>
        </div>
      </div>
      ${field({ label: t("Base DN"), name: "base_dn", value: c.base_dn, ltr: true, placeholder: "DC=school,DC=local",
        hint: t("Where to search for people and groups, usually your domain."), attrs: 'spellcheck="false" autocapitalize="off"' })}
      <details>
        <summary class="fw-semibold" style="cursor:pointer">${t("Advanced")}</summary>
        <div class="pt-3">
          <div class="row">
            ${field({ label: t("User search base"), name: "user_search_base", value: c.user_search_base, ltr: true, cls: "col-md-6",
              hint: t("Leave blank to use the base DN."), attrs: 'spellcheck="false" autocapitalize="off"' })}
            ${field({ label: t("Group search base"), name: "group_search_base", value: c.group_search_base, ltr: true, cls: "col-md-6",
              hint: t("Leave blank to use the base DN."), attrs: 'spellcheck="false" autocapitalize="off"' })}
          </div>
          <div class="row">
            ${field({ label: t("User filter"), name: "user_filter", value: c.user_filter, ltr: true, cls: "col-md-8",
              hint: t("LDAP filter that selects people. The default fits Active Directory."), attrs: 'spellcheck="false" autocapitalize="off"' })}
            ${field({ label: t("NetBIOS domain name"), name: "domain_netbios", value: c.domain_netbios, ltr: true, cls: "col-md-4",
              placeholder: "SCHOOL", hint: t("Lets people sign in as SCHOOL\\name."), attrs: 'spellcheck="false" autocapitalize="off"' })}
          </div>
          <div class="row">
            ${field({ label: t("Timeout (seconds)"), name: "timeout_seconds", type: "number", value: c.timeout_seconds, dataType: "int", cls: "col-md-4",
              required: true, attrs: 'min="2" max="30"' })}
            ${field({ label: t("Re-check membership every (minutes)"), name: "refresh_minutes", type: "number", value: c.refresh_minutes, dataType: "int",
              cls: "col-md-4", required: true, attrs: 'min="5" max="240"', hint: t("5–240") })}
            ${field({ label: t("Keep access during an outage for (minutes)"), name: "outage_grace_minutes", type: "number", value: c.outage_grace_minutes,
              dataType: "int", cls: "col-md-4", required: true, attrs: 'min="0" max="1440"', hint: t("0–1440. 0 ends access as soon as a check fails.") })}
          </div>
          ${switchField({ label: t("Include nested groups"), name: "nested_groups", checked: c.nested_groups,
            hint: t("Counts membership through groups inside groups.") })}
        </div>
      </details>`;
  }

  function certWarning(validate) {
    return validate ? "" : alertBox("danger",
      t("Certificate checking is off. Someone on the network could pretend to be your domain controller and collect staff passwords. Only use this briefly while testing, then paste your CA certificate and turn checking back on."),
      { title: t("Not safe for everyday use") });
  }

  on(root, "change", "[data-form=settings] [name=validate_cert]", (e, el) => {
    setHTML($("[data-region=cert-warning]", root), certWarning(el.checked));
  });

  function readSettings({ forTest = false } = {}) {
    const form = $("form[data-form=settings]", root);
    const v = formValues(form);
    const urls = String(v.server_urls || "").split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    const problems = [];
    const prefix = v.security === "starttls" ? "ldap://" : "ldaps://";
    urls.forEach((u) => {
      if (!u.toLowerCase().startsWith(prefix)) problems.push(t("“{url}” must start with {prefix} for the chosen connection security.", { url: u, prefix }));
    });
    if (v.ca_cert_pem && !v.ca_cert_pem.includes("BEGIN CERTIFICATE")) problems.push(t("The CA certificate must be PEM text starting with -----BEGIN CERTIFICATE-----."));
    if (!forTest && v.enabled) {
      if (!urls.length) problems.push(t("Add at least one domain controller to turn directory sign-in on."));
      if (!v.bind_dn.trim()) problems.push(t("Enter the bind account to turn directory sign-in on."));
      if (!v.base_dn.trim()) problems.push(t("Enter the base DN to turn directory sign-in on."));
      if (!cfg.has_bind_password && !v.bind_password) problems.push(t("Enter the bind account password to turn directory sign-in on."));
    }
    if (!form.checkValidity()) {
      form.classList.add("was-validated");
      const bad = form.querySelector(":invalid");
      const details = bad?.closest("details");
      if (details) details.open = true;
      bad?.focus();
      problems.push(t("Some fields need attention."));
    }
    const body = {
      enabled: v.enabled, server_urls: urls, security: v.security, validate_cert: v.validate_cert, ca_cert_pem: v.ca_cert_pem.trim(),
      bind_dn: v.bind_dn.trim(), base_dn: v.base_dn.trim(), user_search_base: v.user_search_base.trim(),
      group_search_base: v.group_search_base.trim(), user_filter: v.user_filter.trim(), domain_netbios: v.domain_netbios.trim(),
      timeout_seconds: v.timeout_seconds, refresh_minutes: v.refresh_minutes, outage_grace_minutes: v.outage_grace_minutes,
      nested_groups: v.nested_groups,
    };
    if (v.bind_password) body.bind_password = v.bind_password;
    return { body, problems };
  }

  async function save() {
    const { body, problems } = readSettings();
    const result = $("[data-region=test-result]", root);
    if (problems.length) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("danger", html`<ul class="mb-0 ps-3">${problems.map((p) => html`<li>${p}</li>`)}</ul>`, { title: t("Settings not saved") })}</div>`);
      throw new Error(problems[0]);
    }
    try {
      const r = await api("/api/directory", { method: "PUT", body });
      cfg = r.config;
      setHTML($("[data-region=settings]", root), settingsFields());
      setHTML($("[data-region=state]", root), stateLine());
      setHTML(result, "");
      toast(cfg.enabled ? t("Directory settings saved. Directory sign-in is on.") : t("Directory settings saved."));
    } catch (err) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("danger", errorMessage(err), { title: t("Settings not saved") })}</div>`);
      throw err;
    }
  }

  on(root, "click", "[data-action=test]", async (e, el) => {
    const result = $("[data-region=test-result]", root);
    const { body, problems } = readSettings({ forTest: true });
    if (problems.length) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("warning", problems.join(" "))}</div>`);
      return;
    }
    delete body.enabled;
    setHTML(result, "");
    try {
      const r = await withBusy(el, () => api("/api/directory/test", { method: "POST", body, timeout: 60000 }));
      if (r.ok) {
        setHTML(result, html`<div class="card-body border-top">
          ${alertBox("success", html`${t(r.message)}${r.server ? html` <span class="ltr small text-secondary">${r.server}</span>` : ""}`, { title: t("Connection works") })}
          ${r.base_dn_found === false ? alertBox("warning", t("Signed in, but the base DN was not found. Check the spelling of the base DN.")) : ""}
        </div>`);
      } else {
        const tip = connectionTip(r.code);
        setHTML(result, html`<div class="card-body border-top">${alertBox("danger",
          html`<div>${t(r.message)}</div>${tip ? html`<div class="mt-1">${tip}</div>` : ""}`, { title: t("Connection failed") })}</div>`);
      }
    } catch (err) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("danger", errorMessage(err), { title: t("Connection failed") })}</div>`);
    }
  });

  // ---- lookup --------------------------------------------------------------
  function lookupCard() {
    return html`<form class="card" data-form="lookup" novalidate autocomplete="off">
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Check a user")}</h2>
          <p class="card-subtitle mb-0">${t("See what would happen if someone signed in: their groups, the roles they would get, and whether they may sign in. Uses the saved settings.")}</p>
        </div>
      </div>
      <div class="card-body">
        <div class="row g-2 align-items-end">
          <div class="col-md-5">
            <label class="form-label" for="lookup-user">${t("Username")}</label>
            <input id="lookup-user" class="form-control ltr" name="username" required maxlength="200" placeholder="${t("name, SCHOOL\\name or name@school.local")}"
              autocapitalize="off" spellcheck="false" autocomplete="off">
          </div>
          <div class="col-md-4">
            <label class="form-label" for="lookup-pw">${t("Password (optional)")}</label>
            <input id="lookup-pw" class="form-control" type="password" name="password" autocomplete="new-password">
          </div>
          <div class="col-md-3">
            <button type="submit" class="btn btn-outline-secondary w-100">${icon("user-search")} ${t("Check")}</button>
          </div>
        </div>
        <div class="form-hint mt-1">${t("Enter the password only to also test it. It is not saved.")}</div>
      </div>
      <div data-region="lookup-result" aria-live="polite"></div>
    </form>`;
  }

  on(root, "submit", "form[data-form=lookup]", async (e, form) => {
    e.preventDefault();
    const out = $("[data-region=lookup-result]", form);
    const username = form.username.value.trim();
    if (!username) { form.username.focus(); return; }
    setHTML(out, "");
    try {
      const r = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/directory/lookup-user", {
        method: "POST", body: { username, password: form.password.value }, timeout: 60000 }));
      form.password.value = "";
      setHTML(out, html`<div class="card-body border-top">${lookupResult(r)}</div>`);
    } catch (err) {
      setHTML(out, html`<div class="card-body border-top">${alertBox("danger", errorMessage(err))}</div>`);
    }
  });

  function lookupResult(r) {
    if (!r.ok) {
      const tip = connectionTip(r.code);
      return alertBox(r.code === "not_found" ? "warning" : "danger",
        html`<div>${t(r.message)}</div>${tip ? html`<div class="mt-1">${tip}</div>` : ""}`,
        { title: r.code === "not_found" ? t("User not found") : t("The directory could not be checked") });
    }
    const name = r.display_name || r.username;
    let reason = "";
    if (r.disabled) reason = t("The account is disabled in Active Directory.");
    else if (!r.mapped_roles.length) reason = t("They are not in any group that is mapped to a role. Add a group mapping above.");
    const verdict = r.would_sign_in
      ? alertBox("success", t("They would get: {roles}.", { roles: r.mapped_roles.map((n) => t(n)).join(", ") }), { title: t("{name} can sign in", { name }), iconName: "circle-check" })
      : alertBox("danger", reason, { title: t("{name} cannot sign in", { name }), iconName: "circle-x" });
    return html`${verdict}
      <dl class="row mb-0">
        <dt class="col-sm-4">${t("Distinguished name")}</dt><dd class="col-sm-8"><span class="ltr text-break">${r.dn}</span></dd>
        <dt class="col-sm-4">${t("Username")}</dt><dd class="col-sm-8"><span class="ltr">${r.username}</span></dd>
        ${r.email ? html`<dt class="col-sm-4">${t("Email")}</dt><dd class="col-sm-8"><span class="ltr">${r.email}</span></dd>` : ""}
        <dt class="col-sm-4">${t("Account")}</dt><dd class="col-sm-8">${r.disabled
          ? html`<span class="badge bg-red-lt">${t("Disabled")}</span>` : html`<span class="badge bg-green-lt">${t("Enabled")}</span>`}</dd>
        ${r.password_ok !== undefined ? html`<dt class="col-sm-4">${t("Password")}</dt><dd class="col-sm-8">${r.password_ok
          ? html`<span class="text-green">${icon("check")} ${t("Correct")}</span>`
          : html`<span class="text-danger">${icon("x")} ${t("Not accepted: {reason}", { reason: (r.password_message ? t(r.password_message) : t("invalid credentials")) })}</span>`}</dd>` : ""}
        <dt class="col-sm-4">${t("Roles from groups")}</dt><dd class="col-sm-8">${r.mapped_roles.length
          ? html`<div class="d-flex flex-wrap gap-1">${r.mapped_roles.map((n) => html`<span class="badge bg-primary-lt">${t(n)}</span>`)}</div>`
          : html`<span class="text-secondary">${t("None")}</span>`}</dd>
        <dt class="col-sm-4">${tn(r.groups.length, "{n} group", "{n} groups")}</dt>
        <dd class="col-sm-8">${r.groups.length ? html`<ul class="list-unstyled mb-0">${r.groups.map((g) => html`<li class="mb-1">
          ${g.name}${mappings.some((m) => m.group_dn.toLowerCase() === g.dn.toLowerCase()) ? html` <span class="badge bg-green-lt">${t("Mapped")}</span>` : ""}
          <div class="small text-secondary ltr text-break">${g.dn}</div></li>`)}</ul>` : html`<span class="text-secondary">${t("Not a member of any group")}</span>`}</dd>
      </dl>`;
  }

  // ---- mappings ------------------------------------------------------------
  function mappingsCard() {
    return html`<div class="card">
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Group mappings")}</h2>
          <p class="card-subtitle mb-0">${t("Members of a directory group get the role it is mapped to.")}</p>
        </div>
        <div class="card-actions">
          <button type="button" class="btn btn-primary" data-action="add-mapping">${icon("plus")} ${t("Add mapping")}</button>
        </div>
      </div>
      ${mappings.length ? html`<div class="table-responsive"><table class="table card-table table-vcenter">
        <thead><tr><th>${t("Directory group")}</th><th>${t("Role")}</th><th class="w-1"><span class="visually-hidden">${t("Actions")}</span></th></tr></thead>
        <tbody>${mappings.map((m) => html`<tr data-mapping="${m.id}">
          <td style="min-width:12rem">
            <div class="fw-semibold">${m.group_name || m.group_dn}</div>
            <div class="small text-secondary ltr text-break">${m.group_dn}</div>
          </td>
          <td><span class="badge bg-primary-lt">${icon("arrow-narrow-right", "flip-rtl")} ${t(m.role_name)}</span>
            <div class="small text-secondary mt-1">${t("Added {date}", { date: fmtDateTime(m.created_at) })}</div></td>
          <td><button type="button" class="btn btn-icon btn-ghost-danger" data-action="delete-mapping"
            aria-label="${t("Remove mapping for {group}", { group: m.group_name || m.group_dn })}" title="${t("Remove")}">${icon("trash")}</button></td>
        </tr>`)}</tbody>
      </table></div>` : emptyState({
        iconName: "sitemap",
        title: t("No groups mapped yet"),
        text: t("Nobody can sign in with a directory account until at least one group is mapped to a role."),
        action: html`<button type="button" class="btn btn-primary" data-action="add-mapping">${icon("plus")} ${t("Add mapping")}</button>`,
      })}
    </div>`;
  }

  async function reloadMappings() {
    try {
      const d = await api("/api/directory");
      mappings = d.mappings;
      setHTML($("[data-region=mappings]", root), mappingsCard());
    } catch (err) {
      toastError(err);
    }
  }

  async function addMapping() {
    const grantable = (r) => (r.permissions || []).every((p) => can(p));
    const created = await formDialog({
      title: t("Map a directory group to a role"),
      submitLabel: t("Add mapping"),
      body: html`
        <p class="text-secondary">${t("Example: map “Pager School Bells” to School Bells Administrator, so everyone in that group can manage the bells.")}</p>
        <div class="mb-3">
          <label class="form-label" for="group-search">${t("Find a group")}</label>
          <div class="input-icon">
            <span class="input-icon-addon">${icon("search")}</span>
            <input id="group-search" class="form-control" type="search" data-group-search autocomplete="off" placeholder="${t("Type at least 2 letters of the group name")}">
          </div>
          <div class="mt-2" data-group-results aria-live="polite"></div>
        </div>
        <div class="mb-3">
          <label class="form-label form-required" for="group-dn">${t("Group distinguished name (DN)")}</label>
          <input id="group-dn" class="form-control ltr" name="group_dn" required maxlength="500" spellcheck="false" autocapitalize="off"
            placeholder="CN=Pager School Bells,OU=Groups,DC=school,DC=local">
          <div class="form-hint">${t("Filled in when you pick a group above, or type it yourself.")}</div>
          <div class="invalid-feedback">${t("Enter the group's DN, for example CN=Bell Admins,OU=Groups,DC=school,DC=local.")}</div>
        </div>
        ${field({ label: t("Group name (optional)"), name: "group_name", attrs: 'maxlength="120" autocomplete="off"',
          hint: t("A friendly name shown in this list. Taken from the DN if left blank.") })}
        ${selectField({ label: t("Role"), name: "role_id", required: true, dataType: "int", value: "",
          options: [{ value: "", label: t("Choose a role…") }, ...roles.map((r) => ({ value: r.id, label: grantable(r) ? t(r.name) : t("{role} (you do not have all its permissions)", { role: t(r.name) }), disabled: !grantable(r) }))] })}`,
      onMount: (d, form) => {
        const input = $("[data-group-search]", form);
        const list = $("[data-group-results]", form);
        let seq = 0;
        let found = [];
        const search = debounce(async () => {
          const q = input.value.trim();
          const my = ++seq;
          if (q.length < 2) { setHTML(list, ""); return; }
          setHTML(list, html`<div class="small text-secondary"><span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>${t("Searching…")}</div>`);
          try {
            const r = await api("/api/directory/groups", { query: { q }, timeout: 60000 });
            if (my !== seq) return;
            found = r.groups || [];
            if (!r.ok) {
              setHTML(list, alertBox("warning", html`${(r.message ? t(r.message) : t("The directory could not be searched."))} ${t("You can still type the group's DN below.")}`));
            } else if (!found.length) {
              setHTML(list, html`<div class="small text-secondary">${t("No groups match “{q}”.", { q })}</div>`);
            } else {
              setHTML(list, html`<div class="list-group" role="listbox" aria-label="${t("Matching groups")}">${found.map((g, i) => html`
                <button type="button" class="list-group-item list-group-item-action text-start" data-pick="${i}">
                  <div class="fw-semibold">${g.name}</div>
                  ${g.description ? html`<div class="small text-secondary">${g.description}</div>` : ""}
                  <div class="small text-secondary ltr text-break">${g.dn}</div>
                </button>`)}</div>`);
            }
          } catch (err) {
            if (my === seq) setHTML(list, alertBox("warning", errorMessage(err)));
          }
        }, 350);
        input.addEventListener("input", search);
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
        list.addEventListener("click", (e) => {
          const btn = e.target.closest("[data-pick]");
          if (!btn) return;
          const g = found[Number(btn.dataset.pick)];
          if (!g) return;
          form.group_dn.value = g.dn;
          form.group_name.value = g.name || "";
          list.querySelectorAll("[data-pick]").forEach((b) => b.classList.toggle("active", b === btn));
          form.role_id.focus();
        });
      },
      onSubmit: async (v) => {
        const dn = v.group_dn.trim();
        if (!dn.includes("=")) throw new Error(t("Enter the group's DN, for example CN=Bell Admins,OU=Groups,DC=school,DC=local."));
        try {
          return await api("/api/directory/mappings", { method: "POST", body: { group_dn: dn, group_name: v.group_name.trim(), role_id: v.role_id } });
        } catch (err) {
          if (err.code === "escalation") throw new Error(t("You can only map roles whose permissions you have yourself."));
          throw err;
        }
      },
    });
    if (created) {
      toast(t("Mapping added. Members of the group get the role at their next sign-in or check."));
      await reloadMappings();
    }
  }

  on(root, "click", "[data-action=add-mapping]", () => addMapping().catch(toastError));

  on(root, "click", "[data-action=delete-mapping]", async (e, el) => {
    const m = mappings.find((x) => String(x.id) === el.closest("[data-mapping]")?.dataset.mapping);
    if (!m) return;
    const ok = await confirmDialog({
      title: t("Remove this mapping?"),
      message: t("Members of “{group}” lose the {role} role straight away, unless another mapping or a direct role gives it to them. People with no roles left cannot sign in.", { group: m.group_name || m.group_dn, role: t(m.role_name) }),
      confirmLabel: t("Remove mapping"), danger: true,
    });
    if (!ok) return;
    try {
      await withBusy(el, () => api(`/api/directory/mappings/${m.id}`, { method: "DELETE" }));
      toast(t("Mapping removed."));
      await reloadMappings();
    } catch (err) {
      toastError(err);
    }
  });

  on(root, "click", "[data-action=retry]", load);

  await load();
  return () => { tracker = null; };
}
