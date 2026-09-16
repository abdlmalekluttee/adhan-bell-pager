// School Bells › Timetables: reusable day plans (periods, breaks, start and end of day).

import { api } from "../api.js";
import { fmtDate, getLang, t, tn, weekdayName } from "../i18n.js";
import { can, navigate } from "../store.js";
import {
  confirmDialog, emptyState, errorState, field, formDialog, html, icon, on, pageHeader, raw, selectField, setHTML,
  skeleton, toast, toastError, withBusy,
} from "../ui.js";

const WEEK_ORDER = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

function listText(items) {
  try {
    return new Intl.ListFormat(getLang() === "ar" ? "ar" : "en-GB", { style: "short", type: "unit" }).format(items);
  } catch {
    return items.join(", ");
  }
}

function dateRange(from, to) {
  if (!to || to === from) return fmtDate(from);
  return t("{from} to {to}", { from: fmtDate(from), to: fmtDate(to) });
}

/** "Used by" chips for a timetable's usage block. Exported for reuse by the editor. */
export function usageChips(usage, { links = true } = {}) {
  const schedules = usage?.schedules || [];
  const exceptions = usage?.exceptions || [];
  if (!schedules.length && !exceptions.length) return html`<span class="text-secondary">${t("Not used yet")}</span>`;
  const chip = (href, iconName, text, title) => links
    ? html`<a href="${href}" class="badge bg-secondary-lt text-reset text-wrap text-start fw-medium" title="${title}">${icon(iconName)} ${text}</a>`
    : html`<span class="badge bg-secondary-lt text-wrap text-start fw-medium" title="${title}">${icon(iconName)} ${text}</span>`;
  return html`<div class="d-flex flex-wrap gap-1">
    ${schedules.map((s) => {
      const days = WEEK_ORDER.filter((d) => (s.days || []).includes(d)).map((d) => weekdayName(d, "short"));
      return chip("#/bells/schedules", "calendar-week", t("{name} ({days})", { name: s.name, days: listText(days) }), t("Weekly schedule"));
    })}
    ${exceptions.map((e) => chip("#/bells/calendar", "calendar-event",
      e.date_from ? t("{name} ({dates})", { name: e.name, dates: dateRange(e.date_from, e.date_to) }) : e.name, t("Holiday or change")))}
  </div>`;
}

export default async function bellsTimetables(ctx) {
  const { root } = ctx;
  const canEdit = can("bells.schedule.edit");
  let timetables = null;

  async function load() {
    try {
      const r = await api("/api/bells/timetables");
      timetables = Array.isArray(r) ? r : (r.timetables || []);
      render();
    } catch (err) {
      setHTML(root, html`${header()}${errorState(err)}`);
    }
  }

  function header() {
    return pageHeader({
      title: t("Timetables"),
      description: t("A timetable is a reusable day plan: periods, breaks, start and end of day. Make one for each kind of day (for example a regular day, a short Thursday or an exam day), then choose which days use it in a weekly schedule."),
      actions: canEdit && timetables?.length ? html`<button type="button" class="btn btn-primary" data-action="new">${icon("plus")} ${t("New timetable")}</button>` : "",
    });
  }

  function render() {
    if (!timetables.length) {
      setHTML(root, html`${header()}<div class="card">${emptyState({
        iconName: "list-details",
        title: t("No timetables yet"),
        text: canEdit
          ? t("Start with your regular school day. You can generate the periods in one step, then adjust any times.")
          : t("Nobody has created a timetable yet. Ask a bells administrator to set one up."),
        action: canEdit ? html`<button type="button" class="btn btn-primary" data-action="new">${icon("plus")} ${t("Create the first timetable")}</button>` : "",
      })}</div>`);
      return;
    }
    const unused = timetables.filter((tt) => !tt.usage?.schedules?.length && !tt.usage?.exceptions?.length).length;
    setHTML(root, html`${header()}
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">${tn(timetables.length, "{n} timetable", "{n} timetables")}</h2>
          ${unused && timetables.length > 1 ? html`<div class="card-actions small text-secondary">${tn(unused, "{n} is not used by any schedule yet", "{n} are not used by any schedule yet")}</div>` : ""}
        </div>
        <div class="list-group list-group-flush">
          ${timetables.map(row)}
        </div>
      </div>
      <p class="form-hint mt-3">${icon("info-circle")} ${t("Changing a timetable changes the bells on every day that uses it. To prepare a different plan, duplicate it first.")}</p>`);
  }

  function row(tt) {
    const href = `#/bells/timetables/${tt.id}`;
    const summary = tt.ring_count
      ? tn(tt.ring_count, "{n} bell from {first} to {last}", "{n} bells from {first} to {last}", {
        first: html`<span class="tabular">${tt.first}</span>`.s, last: html`<span class="tabular">${tt.last}</span>`.s })
      : (tt.event_count ? t("All bells are turned off") : t("No bells yet"));
    return html`<div class="list-group-item">
      <div class="row g-3 align-items-center">
        <div class="col-12 col-md-4">
          <a href="${href}" class="fw-semibold text-reset">${tt.name}</a>
          ${tt.description ? html`<div class="small text-secondary">${tt.description}</div>` : ""}
          <div class="small mt-1">${icon("bell", "text-secondary")} ${raw(summary)}</div>
        </div>
        <div class="col-12 col-md">
          <div class="small text-secondary mb-1">${t("Used by")}</div>
          ${usageChips(tt.usage)}
        </div>
        <div class="col-12 col-md-auto">
          <div class="btn-list flex-nowrap">
            ${canEdit ? html`
              <a href="${href}" class="btn btn-outline-secondary">${icon("pencil")} ${t("Edit")}</a>
              <button type="button" class="btn btn-ghost-secondary" data-action="duplicate" data-id="${tt.id}">${icon("copy")} ${t("Duplicate")}</button>
              <button type="button" class="btn btn-ghost-danger btn-icon" data-action="delete" data-id="${tt.id}" aria-label="${t("Delete {name}", { name: tt.name })}" title="${t("Delete")}">${icon("trash")}</button>`
              : html`<a href="${href}" class="btn btn-outline-secondary">${icon("eye")} ${t("View")}</a>`}
          </div>
        </div>
      </div>
    </div>`;
  }

  async function createDialog() {
    const hasOthers = timetables.length > 0;
    const body = html`
      ${field({ label: t("Name"), name: "name", required: true, attrs: 'maxlength="80" autocomplete="off"', placeholder: t("For example: Regular day") })}
      ${field({ label: t("Description (optional)"), name: "description", attrs: 'maxlength="300" autocomplete="off"', placeholder: t("For example: Sunday to Wednesday") })}
      <div class="mb-3">
        <div class="form-label">${t("Start from")}</div>
        <div class="form-selectgroup form-selectgroup-boxes d-flex flex-column gap-2">
          ${startOption("generate", "wand", t("Generate the periods"), t("Enter the first bell, the period length and how many periods. You can change any row afterwards."), true)}
          ${startOption("empty", "file", t("An empty timetable"), t("Add each period and break yourself."))}
          ${hasOthers ? startOption("copy", "copy", t("A copy of another timetable"), t("Useful for a short day or an exam day that is close to a normal day.")) : ""}
        </div>
      </div>
      ${hasOthers ? html`<div data-copy-source hidden>${selectField({ label: t("Timetable to copy"), name: "source", value: String(timetables[0].id),
        options: timetables.map((tt) => ({ value: tt.id, label: tt.name })) })}</div>` : ""}`;
    const created = await formDialog({
      title: t("New timetable"),
      submitLabel: t("Create timetable"),
      body,
      onMount: (d, form) => {
        const sync = () => {
          const box = form.querySelector("[data-copy-source]");
          if (box) box.hidden = form.querySelector("[name=start]:checked")?.value !== "copy";
        };
        form.addEventListener("change", sync);
        sync();
      },
      onSubmit: async (v) => {
        const name = (v.name || "").trim();
        if (!name) throw new Error(t("Enter a timetable name."));
        let events = [];
        let description = (v.description || "").trim();
        if (v.start === "copy") {
          const src = await api(`/api/bells/timetables/${encodeURIComponent(v.source)}`);
          events = src.events.map(({ id, ...e }) => e);
          if (!description) description = src.description || "";
        }
        const tt = await api("/api/bells/timetables", { method: "POST", body: { name, description, events } });
        return { tt, start: v.start };
      },
    });
    if (!created) return;
    toast(t("Timetable “{name}” created.", { name: created.tt.name }));
    navigate(`/bells/timetables/${created.tt.id}${created.start === "generate" ? "?generate=1" : ""}`);
  }

  function startOption(value, iconName, title, text, checked = false) {
    return html`<label class="form-selectgroup-item flex-fill">
      <input type="radio" name="start" value="${value}" class="form-selectgroup-input" ${checked ? raw("checked") : ""}>
      <div class="form-selectgroup-label d-flex align-items-center gap-3 p-3 text-start">
        <span class="form-selectgroup-check"></span>
        <span>
          <span class="fw-semibold d-block">${icon(iconName, "me-1")} ${title}</span>
          <span class="small text-secondary d-block">${text}</span>
        </span>
      </div>
    </label>`;
  }

  on(root, "click", "[data-action=retry]", () => { setHTML(root, skeleton(5)); load(); });
  on(root, "click", "[data-action=new]", () => createDialog().catch(toastError));
  on(root, "click", "[data-action=duplicate]", async (e, el) => {
    try {
      const copy = await withBusy(el, () => api(`/api/bells/timetables/${el.dataset.id}/duplicate`, { method: "POST", body: {} }));
      toast(t("Copy created as “{name}”. Rename it and adjust the times.", { name: copy.name }));
      navigate(`/bells/timetables/${copy.id}`);
    } catch (err) {
      toastError(err);
    }
  });
  on(root, "click", "[data-action=delete]", async (e, el) => {
    const tt = timetables.find((x) => String(x.id) === el.dataset.id);
    if (!tt) return;
    const used = (tt.usage?.schedules?.length || 0) + (tt.usage?.exceptions?.length || 0);
    const ok = await confirmDialog({
      title: t("Delete “{name}”?", { name: tt.name }),
      message: t("The timetable and all its rows will be deleted. This cannot be undone."),
      details: used ? html`<div class="small text-secondary mb-1">${t("It is still used by:")}</div>${usageChips(tt.usage, { links: false })}` : "",
      confirmLabel: t("Delete timetable"),
      danger: true,
    });
    if (!ok) return;
    try {
      await withBusy(el, () => api(`/api/bells/timetables/${tt.id}`, { method: "DELETE" }));
      toast(t("Timetable “{name}” deleted.", { name: tt.name }));
      await load();
    } catch (err) {
      toastError(err);
    }
  });

  await load();
  if (ctx.query.new === "1" && canEdit) createDialog().catch(toastError);
}
