// Administration › Roles: built-in roles (read-only) and custom roles built from the permission catalog.

import { api } from "../api.js";
import { t, tn } from "../i18n.js";
import { can } from "../store.js";
import {
  $, $$, confirmDialog, emptyState, errorMessage, errorState, formDialog, html, icon, on, pageHeader, raw, setHTML, toast,
  toastError, withBusy,
} from "../ui.js";

const BUILTIN_ORDER = ["full_admin", "adhan_admin", "bells_admin", "operator", "viewer"];
const MODULES = ["adhan", "bells", "system"];

function moduleTitle(m) {
  return { adhan: t("Adhan"), bells: t("School Bells"), system: t("Administration") }[m] || m;
}

/** Short words for the one-line permission summary of a role. */
function shortLabel(key) {
  return {
    "adhan.view": t("view"),
    "adhan.schedule.edit": t("edit schedule"),
    "adhan.sounds.manage": t("manage sounds"),
    "adhan.broadcast": t("start and test"),
    "adhan.broadcast.stop": t("stop"),
    "adhan.history.view": t("history"),
    "bells.view": t("view"),
    "bells.schedule.edit": t("edit schedules"),
    "bells.sounds.manage": t("manage sounds"),
    "bells.broadcast": t("ring and test"),
    "bells.broadcast.stop": t("stop"),
    "bells.history.view": t("history"),
    "system.destinations.manage": t("speakers and zones"),
    "system.sip.manage": t("phone system"),
    "system.settings.manage": t("time and priorities"),
    "system.directory.manage": t("Active Directory"),
    "system.users.manage": t("users"),
    "system.roles.manage": t("roles"),
    "system.audit.view": t("audit log"),
    "system.logs.view": t("service logs"),
  }[key] || key;
}

function roleError(err) {
  if (err?.code === "escalation") return new Error(t("You can only use permissions you have yourself. Untick the permissions you do not have."));
  if (err?.code === "duplicate") return new Error(t("A role with that name already exists. Choose another name."));
  if (err?.code === "builtin") return new Error(t("Built-in roles cannot be changed. Create a custom role instead."));
  return new Error(errorMessage(err));
}

export default async function adminRoles(ctx) {
  const { root } = ctx;
  let roles = [];
  let catalog = [];
  const canManage = can("system.roles.manage");

  async function load() {
    try {
      const [r, p] = await Promise.all([api("/api/roles"), api("/api/permissions")]);
      roles = r.roles;
      catalog = p.permissions;
      render();
    } catch (err) {
      setHTML(root, errorState(err));
    }
  }

  const mineAll = (perms) => (perms || []).every((p) => can(p));

  function render() {
    const builtin = roles.filter((r) => r.builtin)
      .sort((a, b) => (BUILTIN_ORDER.indexOf(a.key) + 1 || 99) - (BUILTIN_ORDER.indexOf(b.key) + 1 || 99));
    const custom = roles.filter((r) => !r.builtin);
    setHTML(root, html`
      ${pageHeader({
        title: t("Roles"),
        description: t("A role is a set of permissions. Give roles to people on the Users page, or to directory groups on the Active Directory page. A person gets everything their roles allow."),
        actions: canManage ? html`<button type="button" class="btn btn-primary" data-action="create">${icon("plus")} ${t("New role")}</button>` : "",
      })}
      <div class="row row-cards">
        <div class="col-12">
          <div class="card">
            <div class="card-header">
              <div>
                <h2 class="card-title">${t("Custom roles")}</h2>
                <p class="card-subtitle mb-0">${t("Your own roles for needs the built-in ones do not cover.")}</p>
              </div>
            </div>
            ${custom.length ? html`<div class="list-group list-group-flush">${custom.map((r) => roleItem(r))}</div>`
              : emptyState({
                iconName: "shield-plus",
                title: t("No custom roles yet"),
                text: t("Create one when a built-in role gives too much or too little, for example a “Bell ringer” who may only ring and stop bells."),
                action: canManage ? html`<button type="button" class="btn btn-primary" data-action="create">${icon("plus")} ${t("New role")}</button>` : "",
              })}
          </div>
        </div>
        <div class="col-12">
          <div class="card">
            <div class="card-header">
              <div>
                <h2 class="card-title">${t("Built-in roles")}</h2>
                <p class="card-subtitle mb-0">${t("Ready-made roles that cannot be changed. They stay up to date when the application adds new features.")}</p>
              </div>
            </div>
            <div class="list-group list-group-flush">${builtin.map((r) => roleItem(r))}</div>
          </div>
        </div>
      </div>`);
  }

  function summary(perms) {
    const held = new Set(perms);
    return MODULES.map((m) => {
      const keys = catalog.filter((c) => c.module === m && held.has(c.key)).map((c) => c.key);
      if (!keys.length) return "";
      return html`<div class="small"><span class="fw-semibold">${moduleTitle(m)}:</span> <span class="text-secondary">${keys.map(shortLabel).join(", ")}</span></div>`;
    });
  }

  function roleItem(r) {
    const editable = canManage && !r.builtin && mineAll(r.permissions);
    return html`<div class="list-group-item py-3" data-role="${r.id}">
      <div class="d-flex flex-wrap gap-3 align-items-start">
        <span class="avatar ${r.builtin ? "bg-secondary-lt" : "bg-primary-lt"}">${icon(r.builtin ? "lock" : "shield")}</span>
        <div class="flex-fill" style="min-width:14rem">
          <div class="d-flex flex-wrap align-items-center gap-2">
            <h3 class="h4 mb-0">${t(r.name)}</h3>
            ${r.builtin ? html`<span class="badge bg-secondary-lt">${icon("lock")} ${t("Built-in")}</span>` : ""}
          </div>
          ${r.description ? html`<div class="text-secondary mt-1">${t(r.description)}</div>` : ""}
          <div class="mt-2">${summary(r.permissions)}</div>
          <div class="small text-secondary mt-2">
            ${icon("users")} ${tn(r.user_count, "{n} user", "{n} users")}
            <span class="mx-1">·</span>${icon("sitemap")} ${tn(r.mapping_count, "{n} directory group", "{n} directory groups")}
          </div>
        </div>
        ${editable ? html`<div class="btn-list flex-nowrap">
          <button type="button" class="btn btn-outline-secondary" data-action="edit">${icon("pencil")} ${t("Edit")}</button>
          <button type="button" class="btn btn-icon btn-ghost-danger" data-action="delete" aria-label="${t("Delete role {name}", { name: r.name })}" title="${t("Delete")}">${icon("trash")}</button>
        </div>` : !r.builtin && canManage ? html`<span class="badge bg-secondary-lt" title="${t("This role includes permissions you do not have, so you cannot change it.")}">${icon("lock")} ${t("More access than you")}</span>` : ""}
      </div>
    </div>`;
  }

  // ---- editor --------------------------------------------------------------
  function matrix(selected) {
    const held = new Set(selected);
    const missingAny = catalog.some((c) => !can(c.key));
    const check = (c, { disabled = false } = {}) => {
      const mine = can(c.key);
      return html`<label class="form-check mb-2">
        <input class="form-check-input" type="checkbox" name="perm" value="${c.key}" data-perm
          ${held.has(c.key) ? raw("checked") : ""} ${disabled || !mine ? raw("disabled") : ""}>
        <span class="form-check-label">${t(c.label)}${mine ? "" : html` <span class="badge bg-secondary-lt ms-1">${icon("lock")} ${t("You do not have this")}</span>`}</span>
        <span class="form-check-description">${t(c.description)}</span>
      </label>`;
    };
    return html`<div class="perm-matrix">
      ${missingAny ? html`<p class="form-hint mb-2">${icon("info-circle")} ${t("You can only grant permissions you have.")}</p>` : ""}
      ${MODULES.map((m) => {
        const items = catalog.filter((c) => c.module === m);
        if (m === "system") {
          return html`<fieldset class="module-block" data-module-block="${m}">
            <legend class="fw-semibold fs-4 mb-2 float-none w-auto">${moduleTitle(m)}</legend>
            <div class="row"><div class="col-md-6">${items.slice(0, Math.ceil(items.length / 2)).map((c) => check(c))}</div>
              <div class="col-md-6">${items.slice(Math.ceil(items.length / 2)).map((c) => check(c))}</div></div>
          </fieldset>`;
        }
        const viewKey = `${m}.view`;
        const view = items.find((c) => c.key === viewKey);
        const actions = items.filter((c) => c.key !== viewKey);
        const on_ = held.has(viewKey);
        return html`<fieldset class="module-block" data-module-block="${m}">
          <legend class="visually-hidden">${moduleTitle(m)}</legend>
          <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
            <div class="fw-semibold fs-4 me-auto">${moduleTitle(m)}</div>
            <label class="form-check form-switch mb-0">
              <input class="form-check-input" type="checkbox" name="perm" value="${viewKey}" data-perm data-access="${m}"
                ${on_ ? raw("checked") : ""} ${can(viewKey) ? "" : raw("disabled")}>
              <span class="form-check-label">${t("Access to this module")}</span>
            </label>
          </div>
          ${view ? html`<div class="small text-secondary mb-2">${t(view.description)}</div>` : ""}
          <div class="row ps-md-2" data-actions="${m}">
            ${actions.map((c) => html`<div class="col-md-6">${check(c, { disabled: !on_ })}</div>`)}
          </div>
        </fieldset>`;
      })}
    </div>`;
  }

  async function editRole(role = null) {
    const isNew = !role;
    const result = await formDialog({
      title: isNew ? t("New role") : t("Edit role “{name}”", { name: t(role.name) }),
      size: "lg",
      submitLabel: isNew ? t("Create role") : t("Save role"),
      body: html`
        <div class="row">
          <div class="col-md-5 mb-3">
            <label class="form-label form-required" for="role-name">${t("Name")}</label>
            <input id="role-name" class="form-control" name="name" required maxlength="60" value="${role?.name || ""}" autocomplete="off"
              placeholder="${t("For example: Bell ringer")}">
            <div class="invalid-feedback">${t("Enter a name.")}</div>
          </div>
          <div class="col-md-7 mb-3">
            <label class="form-label" for="role-desc">${t("Description")}</label>
            <input id="role-desc" class="form-control" name="description" maxlength="300" value="${role?.description || ""}" autocomplete="off"
              placeholder="${t("Who is this role for?")}">
          </div>
        </div>
        <div class="form-label">${t("Permissions")}</div>
        ${matrix(role?.permissions || [])}
        <div class="text-danger small mt-2" data-perm-error hidden>${t("Choose at least one permission.")}</div>`,
      onMount: (d, form) => {
        form.addEventListener("change", (e) => {
          const el = e.target;
          if (el.matches("[data-access]")) {
            const m = el.dataset.access;
            $$(`[data-actions="${m}"] input[data-perm]`, form).forEach((cb) => {
              if (!el.checked) cb.checked = false;
              cb.disabled = !el.checked || !can(cb.value);
            });
          }
          if (el.matches("[data-perm]")) $("[data-perm-error]", form).hidden = true;
        });
      },
      onSubmit: async (v, form) => {
        const permissions = $$("input[data-perm]:checked", form).map((el) => el.value);
        if (!permissions.length) {
          $("[data-perm-error]", form).hidden = false;
          throw new Error(t("Choose at least one permission."));
        }
        const body = { name: v.name.trim(), description: v.description.trim(), permissions };
        try {
          return isNew
            ? await api("/api/roles", { method: "POST", body })
            : await api(`/api/roles/${role.id}`, { method: "PUT", body });
        } catch (err) {
          throw roleError(err);
        }
      },
    });
    if (result) {
      toast(isNew ? t("Role “{name}” created.", { name: result.name }) : t("Role “{name}” saved. People with this role get the change straight away.", { name: result.name }));
      await load();
    }
  }

  async function deleteRole(role, btn) {
    const people = tn(role.user_count, "{n} user", "{n} users");
    const groups = tn(role.mapping_count, "{n} directory group", "{n} directory groups");
    const ok = await confirmDialog({
      title: t("Delete role “{name}”?", { name: t(role.name) }),
      message: role.user_count || role.mapping_count
        ? t("{people} and {groups} lose the permissions this role gives, straight away. Other roles they hold are not affected.", { people, groups })
        : t("Nobody has this role, so nobody loses access."),
      confirmLabel: t("Delete role"), danger: true,
    });
    if (!ok) return;
    try {
      await withBusy(btn, () => api(`/api/roles/${role.id}`, { method: "DELETE" }));
      toast(t("Role “{name}” deleted.", { name: role.name }));
      await load();
    } catch (err) {
      toastError(roleError(err));
    }
  }

  const roleOf = (el) => roles.find((r) => String(r.id) === el.closest("[data-role]")?.dataset.role);
  on(root, "click", "[data-action=create]", () => editRole().catch(toastError));
  on(root, "click", "[data-action=edit]", (e, el) => { const r = roleOf(el); if (r) editRole(r).catch(toastError); });
  on(root, "click", "[data-action=delete]", (e, el) => { const r = roleOf(el); if (r) deleteRole(r, el); });
  on(root, "click", "[data-action=retry]", load);

  await load();
}
