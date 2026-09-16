// Administration › Audit log: who changed what, when, and whether it worked.

import { api } from "../api.js";
import { fmtDateTime, t } from "../i18n.js";
import { $, debounce, emptyState, errorMessage, html, icon, on, pageHeader, raw, setHTML, toastError } from "../ui.js";

const PAGE_SIZE = 50;

function areas() {
  return [
    { value: "", label: t("All areas") },
    { value: "auth.", label: t("Sign-in") },
    { value: "user.", label: t("Users") },
    { value: "role.", label: t("Roles") },
    { value: "directory.", label: t("Active Directory") },
    { value: "sip.", label: t("Phone system") },
    { value: "destinations.", label: t("Speakers") },
    { value: "adhan.", label: t("Adhan") },
    { value: "bells.", label: t("School bells") },
    { value: "broadcast.", label: t("Broadcasts") },
    { value: "settings.", label: t("Settings") },
    { value: "service.", label: t("Service") },
  ];
}

/** Human labels for the action keys the backend records. Unknown keys fall back to the key itself. */
function actionLabel(key) {
  const labels = {
    "auth.login": t("Signed in"),
    "auth.logout": t("Signed out"),
    "auth.change_password": t("Changed own password"),
    "auth.end_other_sessions": t("Signed out other devices"),
    "user.create": t("Added a user"),
    "user.update": t("Changed a user"),
    "user.delete": t("Deleted a user"),
    "user.reset_password": t("Reset a user's password"),
    "role.create": t("Created a role"),
    "role.update": t("Changed a role"),
    "role.delete": t("Deleted a role"),
    "directory.update": t("Changed Active Directory settings"),
    "directory.test_connection": t("Tested the directory connection"),
    "directory.lookup_user": t("Checked a directory user"),
    "directory.mapping_add": t("Mapped a directory group"),
    "directory.mapping_remove": t("Removed a group mapping"),
    "directory.access_revoked": t("Directory access removed"),
    "sip.update": t("Changed the phone system account"),
    "sip.call_update": t("Changed call behaviour"),
    "sip.codecs_update": t("Changed codecs"),
    "sip.reconnect": t("Reconnected the phone system"),
    "sip.engine_restarted": t("Phone system connection restarted"),
    "destinations.update": t("Changed speakers and zones"),
    "adhan.schedule_update": t("Changed the Adhan schedule"),
    "adhan.location_update": t("Changed the Adhan location"),
    "adhan.zones_update": t("Changed where the Adhan plays"),
    "adhan.sound_upload": t("Uploaded an Adhan sound"),
    "adhan.sound_delete": t("Deleted an Adhan sound"),
    "adhan.sound_assignments_update": t("Changed which Adhan sounds play"),
    "adhan.timetable_import": t("Imported a prayer timetable"),
    "adhan.test": t("Tested the Adhan"),
    "adhan.play_file": t("Played an Adhan sound"),
    "bells.ring_now": t("Rang a bell"),
    "bells.test": t("Tested a bell"),
    "bells.timetable_create": t("Created a timetable"),
    "bells.timetable_update": t("Changed a timetable"),
    "bells.timetable_delete": t("Deleted a timetable"),
    "bells.timetable_duplicate": t("Copied a timetable"),
    "bells.schedule_create": t("Created a weekly schedule"),
    "bells.schedule_update": t("Changed a weekly schedule"),
    "bells.schedule_delete": t("Deleted a weekly schedule"),
    "bells.exception_create": t("Added a holiday or change"),
    "bells.exception_update": t("Changed a holiday or change"),
    "bells.exception_delete": t("Deleted a holiday or change"),
    "bells.sound_upload": t("Uploaded a bell sound"),
    "bells.sound_delete": t("Deleted a bell sound"),
    "broadcast.stop": t("Stopped a broadcast"),
    "broadcast.failed": t("Broadcast failed"),
    "broadcast.partial": t("Broadcast partly played"),
    "broadcast.interrupted": t("Broadcast interrupted"),
    "broadcast.skipped": t("Broadcast skipped"),
    "broadcast.missed": t("Broadcast missed"),
    "settings.timezone_update": t("Changed the timezone"),
    "settings.ntp_update": t("Changed time sync"),
    "settings.ntp_sync": t("Synced the clock"),
    "settings.policy_update": t("Changed broadcast priorities"),
    "service.start": t("Service started"),
    "service.stop": t("Service stopped"),
    "service.restarted_after_outage": t("Service restarted after being offline"),
    "service.missed_during_outage": t("Broadcasts missed while offline"),
    "service.recovered_interrupted": t("Recovered an interrupted broadcast"),
    "logs.export": t("Downloaded service logs"),
  };
  return labels[key] || key;
}

function resultBadge(result) {
  const map = {
    ok: html`<span class="badge bg-green-lt">${icon("check")} ${t("Success")}</span>`,
    denied: html`<span class="badge bg-red-lt">${icon("lock")} ${t("Denied")}</span>`,
    error: html`<span class="badge bg-red-lt">${icon("x")} ${t("Error")}</span>`,
    warning: html`<span class="badge bg-yellow-lt">${icon("alert-triangle")} ${t("Warning")}</span>`,
  };
  return map[result] || html`<span class="badge bg-secondary-lt">${result}</span>`;
}

function loadingRows() {
  return html`<div class="card-body" aria-busy="true" aria-label="${t("Loading")}">
    ${[90, 72, 84, 60, 78, 66].map((w) => html`<div class="skeleton-line" style="width:${w}%"></div>`)}</div>`;
}

export default async function adminAudit(ctx) {
  const { root } = ctx;
  const filters = { action: "", actor: "", result: "" };
  let offset = 0;
  let page = null;
  let seq = 0;

  function renderShell() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Audit log"),
        description: t("A permanent record of sign-ins, changes and broadcast problems: who did what, when, and whether it worked."),
      })}
      <div class="card">
        <div class="card-body border-bottom">
          <form class="row g-2 align-items-end" data-form="filters" role="search">
            <div class="col-sm-6 col-lg-4">
              <label class="form-label" for="audit-area">${t("Area")}</label>
              <select id="audit-area" class="form-select" name="action">
                ${areas().map((a) => html`<option value="${a.value}">${a.label}</option>`)}
              </select>
            </div>
            <div class="col-sm-6 col-lg-4">
              <label class="form-label" for="audit-actor">${t("Who")}</label>
              <input id="audit-actor" class="form-control" type="search" name="actor" maxlength="64" autocomplete="off"
                placeholder="${t("Username, or “system”")}">
            </div>
            <div class="col-sm-6 col-lg-3">
              <label class="form-label" for="audit-result">${t("Result")}</label>
              <select id="audit-result" class="form-select" name="result">
                <option value="">${t("Any result")}</option>
                <option value="ok">${t("Success")}</option>
                <option value="denied">${t("Denied")}</option>
                <option value="error">${t("Error")}</option>
                <option value="warning">${t("Warning")}</option>
              </select>
            </div>
            <div class="col-sm-6 col-lg-1">
              <button type="button" class="btn btn-icon btn-ghost-secondary w-100" data-action="refresh" aria-label="${t("Refresh")}" title="${t("Refresh")}">${icon("refresh")}</button>
            </div>
          </form>
        </div>
        <div data-region="list" aria-live="polite">${loadingRows()}</div>
      </div>`);
  }

  async function load() {
    const my = ++seq;
    const list = $("[data-region=list]", root);
    list.setAttribute("aria-busy", "true");
    try {
      const r = await api("/api/audit", { query: { limit: PAGE_SIZE, offset, action: filters.action, actor: filters.actor, result: filters.result } });
      if (my !== seq) return;
      page = r;
      renderList();
    } catch (err) {
      if (my !== seq) return;
      setHTML(list, emptyState({ iconName: "alert-triangle", title: t("This could not be loaded"), text: errorMessage(err),
        action: html`<button type="button" class="btn btn-primary" data-action="retry">${icon("refresh")} ${t("Try again")}</button>` }));
    } finally {
      if (my === seq) list.removeAttribute("aria-busy");
    }
  }

  function renderList() {
    const list = $("[data-region=list]", root);
    const items = page.items || [];
    const filtered = filters.action || filters.actor || filters.result;
    if (!items.length) {
      setHTML(list, filtered
        ? emptyState({ iconName: "filter-off", title: t("Nothing matches these filters"), text: t("Choose another area or result, or clear the name."),
          action: html`<button type="button" class="btn btn-outline-secondary" data-action="clear">${t("Clear filters")}</button>` })
        : emptyState({ iconName: "list-search", title: t("No entries yet"), text: t("Sign-ins, changes and broadcast problems appear here as they happen.") }));
      return;
    }
    const from = offset + 1;
    const to = offset + items.length;
    setHTML(list, html`
      <div class="table-responsive">
        <table class="table card-table table-vcenter">
          <thead><tr>
            <th>${t("When")}</th>
            <th>${t("Who")}</th>
            <th>${t("What")}</th>
            <th class="d-none d-md-table-cell">${t("Target")}</th>
            <th>${t("Result")}</th>
            <th class="w-1"><span class="visually-hidden">${t("Details")}</span></th>
          </tr></thead>
          <tbody>${items.map(row)}</tbody>
        </table>
      </div>
      <div class="card-footer d-flex flex-wrap align-items-center gap-2">
        <span class="text-secondary small me-auto tabular">${t("Showing {from}–{to} of {total}", { from, to, total: page.total })}</span>
        <div class="btn-group">
          <button type="button" class="btn" data-action="page" data-dir="-1" ${offset <= 0 ? raw("disabled") : ""}>${icon("chevron-left", "flip-rtl")} ${t("Newer")}</button>
          <button type="button" class="btn" data-action="page" data-dir="1" ${to >= page.total ? raw("disabled") : ""}>${t("Older")} ${icon("chevron-right", "flip-rtl")}</button>
        </div>
      </div>`);
  }

  function row(e) {
    const hasDetails = e.details && typeof e.details === "object" && Object.keys(e.details).length > 0;
    const label = actionLabel(e.action);
    const detailId = `audit-detail-${e.id}`;
    return html`<tr>
      <td class="text-nowrap tabular">${fmtDateTime(e.ts)}</td>
      <td>
        <div>${e.actor === "system" ? html`<span class="text-secondary">${icon("server")} ${t("System")}</span>` : html`<span class="ltr">${e.actor}</span>`}</div>
        ${e.ip ? html`<div class="small text-secondary ltr">${e.ip}</div>` : ""}
      </td>
      <td>
        <div>${label}</div>
        ${label !== e.action ? html`<div class="small text-secondary ltr">${e.action}</div>` : ""}
        ${e.target ? html`<div class="small d-md-none text-break"><bdi>${e.target}</bdi></div>` : ""}
      </td>
      <td class="d-none d-md-table-cell text-break" style="max-width:18rem"><bdi>${e.target || "—"}</bdi></td>
      <td>${resultBadge(e.result)}</td>
      <td>${hasDetails ? html`<button type="button" class="btn btn-sm btn-ghost-secondary" data-action="toggle" aria-expanded="false" aria-controls="${detailId}">
        ${icon("chevron-down")} <span class="d-none d-sm-inline">${t("Details")}</span></button>` : ""}</td>
    </tr>
    ${hasDetails ? html`<tr id="${detailId}" hidden><td colspan="6" class="bg-body-tertiary">
      <dl class="row mb-2 small">${Object.entries(e.details).filter(([, v]) => v === null || typeof v !== "object").map(([k, v]) => html`
        <dt class="col-sm-3 fw-normal text-secondary ltr">${k}</dt><dd class="col-sm-9 mb-1 text-break"><bdi>${v === null ? "—" : String(v)}</bdi></dd>`)}</dl>
      <pre class="ltr mb-0 small" style="white-space:pre-wrap;word-break:break-word">${JSON.stringify(e.details, null, 2)}</pre>
    </td></tr>` : ""}`;
  }

  // ---- events --------------------------------------------------------------
  function readFilters() {
    const form = $("form[data-form=filters]", root);
    filters.action = form.elements.action.value;
    filters.actor = form.elements.actor.value.trim();
    filters.result = form.elements.result.value;
    offset = 0;
  }

  const actorChanged = debounce(() => { readFilters(); load(); }, 400);
  on(root, "change", "form[data-form=filters] select", () => { readFilters(); load(); });
  on(root, "input", "form[data-form=filters] [name=actor]", actorChanged);
  on(root, "submit", "form[data-form=filters]", (e) => { e.preventDefault(); readFilters(); load(); });
  on(root, "click", "[data-action=refresh]", () => load());
  on(root, "click", "[data-action=retry]", () => load());
  on(root, "click", "[data-action=clear]", () => {
    const form = $("form[data-form=filters]", root);
    form.reset();
    readFilters();
    load();
  });
  on(root, "click", "[data-action=page]", (e, el) => {
    offset = Math.max(0, offset + Number(el.dataset.dir) * PAGE_SIZE);
    load().then(() => $("[data-region=list]", root)?.scrollIntoView({ block: "start", behavior: "smooth" })).catch(toastError);
  });
  on(root, "click", "[data-action=toggle]", (e, el) => {
    const target = document.getElementById(el.getAttribute("aria-controls"));
    if (!target) return;
    const open = target.hidden;
    target.hidden = !open;
    el.setAttribute("aria-expanded", String(open));
    el.querySelector(".ti")?.classList.replace(open ? "ti-chevron-down" : "ti-chevron-up", open ? "ti-chevron-up" : "ti-chevron-down");
  });

  renderShell();
  await load();
}
