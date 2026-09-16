// Administration › Users: local and directory accounts, their roles and effective permissions.

import { api } from "../api.js";
import { fmtAgo, fmtDateTime, t, tn } from "../i18n.js";
import { can, state } from "../store.js";
import {
  $, $$, alertBox, confirmDialog, emptyState, errorMessage, errorState, field, formDialog, html, icon, on, pageHeader, raw,
  setHTML, switchField, toast, toastError, withBusy,
} from "../ui.js";

const BUILTIN_ORDER = ["bells_admin", "adhan_admin", "operator", "viewer", "full_admin"];

function builtinLead(key) {
  return {
    bells_admin: t("School Bells only"),
    adhan_admin: t("Adhan only"),
    operator: t("Both modules, day to day"),
    viewer: t("Look, but not change"),
    full_admin: t("Everything"),
  }[key] || "";
}

function moduleTitle(m) {
  return { adhan: t("Adhan"), bells: t("School Bells"), system: t("Administration") }[m] || m;
}

/** Clear messages for the server's refusal codes. */
function userError(err) {
  const known = {
    escalation: t("You can only give permissions you have yourself, and only change people who do not have more access than you."),
    last_admin: t("This would leave nobody with Full Administrator. Give another active person Full Administrator first."),
    recovery_account: t("The recovery administrator always keeps Full Administrator and cannot be disabled or deleted."),
  };
  if (err && known[err.code]) return new Error(known[err.code]);
  return new Error(errorMessage(err));
}

const hasAll = (perms) => (perms || []).every((p) => can(p));

export default async function adminUsers(ctx) {
  const { root } = ctx;
  let users = [];
  let roles = [];
  let catalog = [];
  let query = "";

  async function load() {
    try {
      const [u, r, p] = await Promise.all([api("/api/users"), api("/api/roles"), api("/api/permissions")]);
      users = u.users;
      roles = r.roles;
      catalog = p.permissions;
      render();
    } catch (err) {
      setHTML(root, errorState(err));
    }
  }

  async function reloadUsers() {
    try {
      const [u, r] = await Promise.all([api("/api/users"), api("/api/roles")]);
      users = u.users;
      roles = r.roles;
      renderTable();
    } catch (err) {
      toastError(err);
    }
  }

  const roleById = (id) => roles.find((r) => r.id === id);

  function render() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Users"),
        description: t("Who can sign in and what each person may do. People with a school (Active Directory) account appear here after their first sign-in."),
        actions: html`<button type="button" class="btn btn-primary" data-action="add">${icon("user-plus")} ${t("Add user")}</button>`,
      })}
      <div class="card">
        <div class="card-header flex-wrap gap-2">
          <div class="input-icon flex-fill" style="max-width:24rem">
            <span class="input-icon-addon">${icon("search")}</span>
            <input type="search" class="form-control" data-action="search" value="${query}" placeholder="${t("Search by name, username, email or role")}"
              aria-label="${t("Search users")}" autocomplete="off">
          </div>
          <div class="ms-auto text-secondary small" data-region="count" aria-live="polite"></div>
        </div>
        <div data-region="table"></div>
      </div>`);
    renderTable();
  }

  function matches(u) {
    if (!query) return true;
    const q = query.toLowerCase();
    return [u.username, u.display_name, u.email, ...(u.roles || []).map((r) => r.name), ...(u.roles || []).map((r) => t(r.name))]
      .some((s) => String(s || "").toLowerCase().includes(q));
  }

  function renderTable() {
    const list = users.filter(matches);
    setHTML($("[data-region=count]", root), query
      ? t("{shown} of {total}", { shown: list.length, total: tn(users.length, "{n} user", "{n} users") })
      : tn(users.length, "{n} user", "{n} users"));
    const host = $("[data-region=table]", root);
    if (!list.length) {
      setHTML(host, query
        ? emptyState({ iconName: "search", title: t("No users match “{q}”", { q: query }), text: t("Try part of the name or username.") })
        : emptyState({ iconName: "users", title: t("No users yet"), text: t("Add a user for each person who needs to sign in.") }));
      return;
    }
    setHTML(host, html`<div class="table-responsive">
      <table class="table card-table table-vcenter">
        <thead><tr>
          <th>${t("Person")}</th>
          <th class="d-none d-md-table-cell">${t("Account type")}</th>
          <th>${t("Roles")}</th>
          <th>${t("Status")}</th>
          <th class="d-none d-lg-table-cell">${t("Last sign-in")}</th>
          <th class="w-1"><span class="visually-hidden">${t("Actions")}</span></th>
        </tr></thead>
        <tbody>${list.map(userRow)}</tbody>
      </table></div>`);
  }

  function sourceBadge(u) {
    return u.source === "ad"
      ? html`<span class="badge bg-azure-lt">${icon("sitemap")} ${t("Active Directory")}</span>`
      : html`<span class="badge bg-secondary-lt">${icon("user")} ${t("Local")}</span>`;
  }

  function roleChips(u) {
    if (!u.roles?.length) return html`<span class="text-secondary small">${t("No roles — cannot use anything")}</span>`;
    return html`<div class="d-flex flex-wrap gap-1">${u.roles.map((r) => {
      const via = r.via || "direct";
      if (via.startsWith("ad:")) {
        return html`<span class="badge bg-azure-lt text-wrap text-start" title="${t("From directory group: {group}", { group: via.slice(3) })}">${t(r.name)}
          <span class="fw-normal">· ${t("via {group}", { group: via.slice(3) })}</span></span>`;
      }
      return html`<span class="badge bg-primary-lt text-wrap text-start">${t(r.name)}</span>`;
    })}</div>`;
  }

  function statusBadges(u) {
    const out = [];
    if (u.disabled) out.push(html`<span class="badge bg-red-lt">${icon("ban")} ${t("Disabled")}</span>`);
    else if (u.must_change_password) out.push(html`<span class="badge bg-yellow-lt">${icon("key")} ${t("Must change password")}</span>`);
    else out.push(html`<span class="badge bg-green-lt">${icon("check")} ${t("Active")}</span>`);
    if (u.is_recovery) out.push(html`<span class="badge bg-purple-lt">${icon("lifebuoy")} ${t("Recovery administrator")}</span>`);
    if (u.source === "ad" && u.ad_sync_error) out.push(html`<span class="badge bg-orange-lt" title="${t(u.ad_sync_error)}">${icon("alert-triangle")} ${t("Directory check failed")}</span>`);
    return html`<div class="d-flex flex-wrap gap-1">${out}</div>`;
  }

  function userRow(u) {
    const self = u.id === state.me.user.id;
    const manageable = hasAll(u.permissions);
    const name = u.display_name || u.username;
    return html`<tr data-user="${u.id}">
      <td>
        <div class="d-flex align-items-center gap-2">
          <span class="avatar avatar-sm ${u.disabled ? "" : "bg-primary-lt"}">${initials(name)}</span>
          <div class="text-truncate">
            <div class="fw-semibold">${name}${self ? html` <span class="text-secondary fw-normal small">(${t("you")})</span>` : ""}</div>
            <div class="small text-secondary"><span class="ltr">${u.username}</span>${u.email ? html` · <span class="ltr">${u.email}</span>` : ""}</div>
            <div class="d-md-none mt-1">${sourceBadge(u)}</div>
          </div>
        </div>
      </td>
      <td class="d-none d-md-table-cell">${sourceBadge(u)}</td>
      <td style="min-width:10rem">${roleChips(u)}</td>
      <td style="min-width:8rem">${statusBadges(u)}</td>
      <td class="d-none d-lg-table-cell text-secondary" title="${u.last_login ? fmtDateTime(u.last_login) : ""}">${u.last_login ? fmtAgo(u.last_login) : t("Never signed in")}</td>
      <td>
        ${manageable ? html`<div class="btn-list flex-nowrap justify-content-end">
          <button type="button" class="btn btn-sm btn-outline-secondary" data-action="edit" aria-label="${t("Edit {name}", { name })}">${icon("pencil")} ${t("Edit")}</button>
          ${u.source === "local" ? html`<button type="button" class="btn btn-sm btn-icon btn-ghost-secondary" data-action="reset"
            aria-label="${t("Reset password for {name}", { name })}" title="${t("Reset password")}">${icon("key")}</button>` : ""}
          ${!u.is_recovery && !self ? html`<button type="button" class="btn btn-sm btn-icon btn-ghost-danger" data-action="delete"
            aria-label="${t("Delete {name}", { name })}" title="${t("Delete")}">${icon("trash")}</button>` : ""}
        </div>` : html`<span class="badge bg-secondary-lt" title="${t("This person has permissions you do not have, so you cannot change them.")}">${icon("lock")} ${t("More access than you")}</span>`}
      </td>
    </tr>`;
  }

  function initials(name) {
    return String(name).split(/\s+/).map((p) => p[0] || "").join("").slice(0, 2).toUpperCase();
  }

  // ---- role picker ---------------------------------------------------------
  function rolePicker({ selected = new Set(), locked = new Set(), intro = true }) {
    const builtin = BUILTIN_ORDER.map((k) => roles.find((r) => r.builtin && r.key === k)).filter(Boolean)
      .concat(roles.filter((r) => r.builtin && !BUILTIN_ORDER.includes(r.key)));
    const custom = roles.filter((r) => !r.builtin);
    const anyBlocked = roles.some((r) => !hasAll(r.permissions));
    const box = (r) => {
      const grantable = hasAll(r.permissions);
      const isLocked = locked.has(r.id);
      const checked = selected.has(r.id) || isLocked;
      return html`<div class="col-md-6"><div class="form-selectgroup form-selectgroup-boxes d-flex h-100 m-0">
        <label class="form-selectgroup-item flex-fill m-0">
          <input type="checkbox" name="role_ids" value="${r.id}" class="form-selectgroup-input" data-role
            ${checked ? raw("checked") : ""} ${isLocked || !grantable ? raw("disabled") : ""}>
          <div class="form-selectgroup-label d-flex align-items-start p-3 h-100 text-start">
            <div class="me-3 pt-1"><span class="form-selectgroup-check"></span></div>
            <div class="form-selectgroup-label-content">
              ${builtinLead(r.key) ? html`<div class="fw-bold">${builtinLead(r.key)}</div>` : ""}
              <div class="${builtinLead(r.key) ? "small" : "fw-bold"}">${t(r.name)}</div>
              <div class="small text-secondary mt-1">${t(r.description)}</div>
              ${isLocked ? html`<div class="small text-purple mt-1">${icon("lock")} ${t("Always kept by the recovery administrator")}</div>` : ""}
              ${!grantable && !isLocked ? html`<div class="small text-secondary mt-1">${icon("lock")} ${t("Includes permissions you do not have")}</div>` : ""}
            </div>
          </div>
        </label>
      </div></div>`;
    };
    return html`<fieldset class="mb-3">
      <legend class="form-label">${t("What can this person do?")}</legend>
      ${intro ? html`<p class="text-secondary small mb-2">${t("Pick what this person needs: School Bells only, Adhan only, both, or everything. You can tick more than one; the person gets everything the ticked roles allow.")}</p>` : ""}
      <div class="row g-2">${builtin.map(box)}</div>
      ${custom.length ? html`<div class="form-label mt-3">${t("Custom roles")}</div>
        <div class="row g-2">${custom.map(box)}</div>` : ""}
      ${anyBlocked ? html`<div class="form-hint mt-2">${icon("info-circle")} ${t("You can only give roles whose permissions you have yourself.")}</div>` : ""}
    </fieldset>`;
  }

  const checkedRoleIds = (form) => $$("input[data-role]:checked", form).map((el) => Number(el.value));

  // ---- effective permissions panel ----------------------------------------
  function permissionPanel(perms, sources, { preview = false } = {}) {
    const held = new Set(perms);
    return html`<div class="perm-matrix">
      <div class="d-flex align-items-center gap-2 mb-2">
        <h3 class="h4 mb-0 me-auto">${t("Effective permissions")}</h3>
        ${preview ? html`<span class="badge bg-yellow-lt">${icon("eye")} ${t("Preview — not saved yet")}</span>` : ""}
      </div>
      ${["adhan", "bells", "system"].map((m) => {
        const items = catalog.filter((c) => c.module === m);
        const mine = items.filter((c) => held.has(c.key));
        return html`<div class="module-block">
          <div class="d-flex align-items-center gap-2">
            <div class="fw-semibold me-auto">${moduleTitle(m)}</div>
            <span class="small text-secondary tabular">${t("{n} of {total}", { n: mine.length, total: items.length })}</span>
          </div>
          ${mine.length ? html`<ul class="list-unstyled mb-0 mt-2">${mine.map((c) => html`<li class="mb-1">
            <div class="small">${icon("check", "text-green")} ${t(c.label)}</div>
            <div class="perm-source ms-3">${(sources[c.key] || []).join(" · ")}</div>
          </li>`)}</ul>` : html`<div class="small text-secondary mt-1">${t("No access")}</div>`}
        </div>`;
      })}
    </div>`;
  }

  function previewFor(u, selectedIds) {
    const perms = new Set();
    const sources = {};
    const add = (role, label) => {
      (role?.permissions || []).forEach((p) => { perms.add(p); (sources[p] = sources[p] || []).push(label); });
    };
    selectedIds.forEach((id) => { const r = roleById(id); if (r) add(r, t("{role} (assigned)", { role: t(r.name) })); });
    const fullAdmin = roles.find((r) => r.key === "full_admin");
    (u.roles || []).forEach((ur) => {
      const via = ur.via || "direct";
      if (via === "direct") return;
      const r = roleById(ur.id);
      if (via.startsWith("ad:")) add(r, t("{role} (directory group: {group})", { role: t(ur.name), group: via.slice(3) }));
      else if (via === "recovery" && !(fullAdmin && selectedIds.includes(fullAdmin.id))) add(r, t("{role} (recovery account)", { role: t(ur.name) }));
    });
    return { perms: catalog.map((c) => c.key).filter((k) => perms.has(k)), sources };
  }

  // ---- dialogs -------------------------------------------------------------
  async function addUser() {
    const created = await formDialog({
      title: t("Add a user"),
      size: "lg",
      submitLabel: raw(`${icon("user-plus").s} ${t("Add user")}`),
      body: html`
        <div class="row">
          ${field({ label: t("Username"), name: "username", required: true, ltr: true, cls: "col-md-6",
            hint: t("What they type to sign in. Letters, numbers, dots, dashes and underscores."),
            attrs: 'autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="64"' })}
          ${field({ label: t("Full name"), name: "display_name", cls: "col-md-6", attrs: 'maxlength="120" autocomplete="off"' })}
        </div>
        <div class="row">
          ${field({ label: t("Email (optional)"), name: "email", type: "email", ltr: true, cls: "col-md-6", attrs: 'maxlength="200" autocomplete="off"' })}
          <div class="col-md-6 mb-3">
            <label class="form-label form-required" for="new-user-password">${t("Temporary password")}</label>
            <input id="new-user-password" class="form-control" type="password" name="password" required minlength="8" autocomplete="new-password">
            <div class="form-hint">${t("At least 8 characters. Give it to the person privately.")}</div>
            <div class="invalid-feedback">${t("Use at least 8 characters.")}</div>
          </div>
        </div>
        ${switchField({ label: t("Ask them to choose their own password at first sign-in"), name: "must_change_password", checked: true })}
        <hr class="my-3">
        ${rolePicker({})}
        <p class="form-hint mb-0">${t("People who sign in with a school (Active Directory) account do not need to be added here. Give their directory group a role on the Active Directory page instead.")}</p>`,
      onSubmit: async (v, form) => {
        const roleIds = checkedRoleIds(form);
        if (!roleIds.length) {
          const ok = await confirmDialog({
            title: t("Add without a role?"),
            message: t("This person will be able to sign in but will not see or change anything until you give them a role."),
            confirmLabel: t("Add anyway"),
          });
          if (!ok) return false;
        }
        try {
          return await api("/api/users", { method: "POST", body: {
            username: v.username.trim(), display_name: v.display_name.trim(), email: v.email.trim(), password: v.password,
            must_change_password: v.must_change_password, role_ids: roleIds } });
        } catch (err) {
          throw userError(err);
        }
      },
    });
    if (created) {
      toast(t("{name} was added.", { name: created.display_name || created.username }));
      await reloadUsers();
    }
  }

  async function editUser(u) {
    const self = u.id === state.me.user.id;
    const fullAdmin = roles.find((r) => r.key === "full_admin");
    const locked = new Set(u.is_recovery && fullAdmin ? [fullAdmin.id] : []);
    const adRoles = (u.roles || []).filter((r) => (r.via || "").startsWith("ad:"));
    const initialSources = Object.fromEntries(Object.entries(u.permission_sources || {}).map(([k, v]) => [k, v.map((s) => t(s))]));
    const disableNote = u.is_recovery ? t("The recovery administrator cannot be disabled.")
      : self ? t("You cannot disable your own account.") : "";
    const saved = await formDialog({
      title: t("Edit {name}", { name: u.display_name || u.username }),
      size: "lg",
      body: html`
        <div class="row g-4">
          <div class="col-lg-7">
            <div class="d-flex flex-wrap gap-2 mb-3">
              ${sourceBadge(u)} <span class="badge bg-secondary-lt ltr">${u.username}</span>
              ${u.is_recovery ? html`<span class="badge bg-purple-lt">${icon("lifebuoy")} ${t("Recovery administrator")}</span>` : ""}
            </div>
            <div class="row">
              ${field({ label: t("Full name"), name: "display_name", value: u.display_name, cls: "col-md-6", attrs: 'maxlength="120" autocomplete="off"' })}
              ${field({ label: t("Email"), name: "email", type: "email", value: u.email, ltr: true, cls: "col-md-6", attrs: 'maxlength="200" autocomplete="off"' })}
            </div>
            ${u.source === "ad" ? html`<div class="form-hint mt-n2 mb-3">${t("For directory accounts, the name and email are refreshed from Active Directory at each sign-in.")}</div>` : ""}
            ${disableNote ? html`<p class="text-secondary small">${icon("lock")} ${disableNote}</p>`
              : switchField({ label: t("Disable this account"), name: "disabled", checked: u.disabled,
                hint: t("A disabled person cannot sign in and is signed out everywhere. Their history stays.") })}
            <hr class="my-3">
            ${rolePicker({ selected: new Set(u.role_ids), locked, intro: false })}
            ${u.source === "ad" ? html`<div class="mb-3">
              <div class="form-label">${t("From Active Directory")}</div>
              ${adRoles.length ? html`<ul class="list-unstyled mb-2">${adRoles.map((r) => html`<li class="small">${icon("sitemap", "text-azure")} ${t("{role} (directory group: {group})", { role: t(r.name), group: r.via.slice(3) })}</li>`)}</ul>`
                : html`<p class="small text-secondary mb-2">${t("No roles come from directory groups right now.")}</p>`}
              <p class="form-hint">${t("These roles come from group mappings and cannot be removed here. Change the mappings on the Active Directory page, or the person's groups in Active Directory.")}
                ${can("system.directory.manage") ? html` <a href="#/admin/directory" data-dismiss>${t("Open Active Directory settings")}</a>` : ""}</p>
              <details>
                <summary class="small" style="cursor:pointer">${tn((u.ad_groups || []).length, "{n} directory group", "{n} directory groups")}</summary>
                ${(u.ad_groups || []).length ? html`<ul class="small mt-2 mb-0 ps-3">${u.ad_groups.map((g) => html`<li class="ltr text-break">${g}</li>`)}</ul>` : ""}
              </details>
              <div class="small text-secondary mt-2">${u.ad_synced_at ? t("Last checked {ago}", { ago: fmtAgo(u.ad_synced_at) }) : t("Not checked yet")}</div>
              ${u.ad_sync_error ? alertBox("warning", t(u.ad_sync_error), { title: t("Last directory check failed") }) : ""}
            </div>` : ""}
          </div>
          <div class="col-lg-5" data-perm-panel aria-live="polite">${permissionPanel(u.permissions, initialSources)}</div>
        </div>`,
      onMount: (_d, form) => {
        const panel = $("[data-perm-panel]", form);
        const original = JSON.stringify([...u.role_ids].sort());
        form.addEventListener("change", (e) => {
          if (!e.target.matches("input[data-role]")) return;
          const ids = checkedRoleIds(form);
          if (JSON.stringify([...ids].sort()) === original) setHTML(panel, permissionPanel(u.permissions, initialSources));
          else {
            const p = previewFor(u, ids);
            setHTML(panel, permissionPanel(p.perms, p.sources, { preview: true }));
          }
        });
      },
      onSubmit: async (v, form) => {
        const body = { display_name: v.display_name.trim(), email: v.email.trim(), role_ids: checkedRoleIds(form) };
        if (!disableNote) body.disabled = !!v.disabled;
        if (self && fullAdmin && u.role_ids.includes(fullAdmin.id) && !body.role_ids.includes(fullAdmin.id)
          && !adRoles.some((r) => r.id === fullAdmin.id)) {
          const ok = await confirmDialog({
            title: t("Remove your own Full Administrator role?"),
            message: t("You may lose access to this page and other administration pages as soon as you save."),
            confirmLabel: t("Remove it"), danger: true,
          });
          if (!ok) return false;
        }
        try {
          return await api(`/api/users/${u.id}`, { method: "PUT", body });
        } catch (err) {
          throw userError(err);
        }
      },
    });
    if (saved) {
      toast(t("Changes to {name} saved.", { name: saved.display_name || saved.username }));
      await reloadUsers();
    }
  }

  async function resetPassword(u) {
    const name = u.display_name || u.username;
    const done = await formDialog({
      title: t("Reset password for {name}", { name }),
      submitLabel: t("Set new password"),
      body: html`
        <p class="text-secondary">${t("They are signed out everywhere and must use the new password.")}</p>
        <div class="mb-3">
          <label class="form-label form-required" for="reset-pw">${t("New password")}</label>
          <input id="reset-pw" class="form-control" type="password" name="password" required minlength="8" autocomplete="new-password">
          <div class="form-hint">${t("At least 8 characters. Avoid common words and the username.")}</div>
          <div class="invalid-feedback">${t("Use at least 8 characters.")}</div>
        </div>
        <div class="mb-3">
          <label class="form-label form-required" for="reset-pw2">${t("Repeat new password")}</label>
          <input id="reset-pw2" class="form-control" type="password" name="repeat" required autocomplete="new-password">
        </div>
        ${switchField({ label: t("Ask them to choose their own password at next sign-in"), name: "must_change_password", checked: true })}`,
      onSubmit: async (v) => {
        if (v.password !== v.repeat) throw new Error(t("The passwords do not match."));
        try {
          return await api(`/api/users/${u.id}/reset-password`, { method: "POST", body: { password: v.password, must_change_password: v.must_change_password } });
        } catch (err) {
          throw userError(err);
        }
      },
    });
    if (done) {
      toast(t("The password for {name} was reset.", { name }));
      await reloadUsers();
    }
  }

  async function deleteUser(u, btn) {
    const name = u.display_name || u.username;
    const ok = await confirmDialog({
      title: t("Delete {name}?", { name }),
      message: u.source === "ad"
        ? t("They lose access immediately. If they are still in a mapped directory group, they can sign in again and the account is recreated. The audit log keeps their past actions.")
        : t("They lose access immediately and the account cannot be recovered. The audit log keeps their past actions."),
      confirmLabel: t("Delete user"), danger: true,
    });
    if (!ok) return;
    try {
      await withBusy(btn, () => api(`/api/users/${u.id}`, { method: "DELETE" }));
      toast(t("{name} was deleted.", { name }));
      await reloadUsers();
    } catch (err) {
      toastError(userError(err));
    }
  }

  // ---- events --------------------------------------------------------------
  const userOf = (el) => users.find((u) => String(u.id) === el.closest("[data-user]")?.dataset.user);
  on(root, "input", "[data-action=search]", (e, el) => { query = el.value.trim(); renderTable(); });
  on(root, "click", "[data-action=add]", () => addUser().catch(toastError));
  on(root, "click", "[data-action=edit]", (e, el) => { const u = userOf(el); if (u) editUser(u).catch(toastError); });
  on(root, "click", "[data-action=reset]", (e, el) => { const u = userOf(el); if (u) resetPassword(u).catch(toastError); });
  on(root, "click", "[data-action=delete]", (e, el) => { const u = userOf(el); if (u) deleteUser(u, el); });
  on(root, "click", "[data-action=retry]", load);

  await load();
}
