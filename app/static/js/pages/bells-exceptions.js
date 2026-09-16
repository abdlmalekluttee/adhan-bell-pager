// School Bells › Holidays & changes: calendar exceptions with a six-week overview.

import { api } from "../api.js";
import { fmtDate, getLang, getTimeZone, t, tn, weekdayName } from "../i18n.js";
import { can, serverNow } from "../store.js";
import {
  confirmDialog, emptyState, errorState, field, formDialog, html, icon, on, pageHeader, raw, selectField, setHTML, skeleton,
  toast, toastError, withBusy,
} from "../ui.js";

const WEEK_ORDER = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

const KINDS = {
  holiday: { label: () => t("Holiday"), icon: "beach", cls: "bg-purple-lt", explain: () => t("School is closed. No bells ring.") },
  no_bells: { label: () => t("No bells"), icon: "bell-off", cls: "bg-secondary-lt", explain: () => t("School is open but the bells stay silent, for example exams without bells or maintenance work.") },
  override: { label: () => t("Different timetable"), icon: "replace", cls: "bg-azure-lt", explain: () => t("Use another timetable on these dates, for example a half day or an exam day.") },
};

const DAY = {
  school_day: { label: () => t("School day"), dot: "status-green", cell: "" },
  override: { label: () => t("Different timetable"), dot: "status-azure", cell: "bg-azure-lt" },
  holiday: { label: () => t("Holiday — no bells"), dot: "status-purple", cell: "bg-purple-lt" },
  no_bells: { label: () => t("No bells"), dot: "status-secondary", cell: "bg-secondary-lt" },
  not_school_day: { label: () => t("Not a school day"), dot: "", cell: "text-secondary" },
  no_schedule: { label: () => t("No active schedule"), dot: "status-yellow", cell: "" },
};

const iso = (d) => d.toISOString().slice(0, 10);
const shift = (s, days) => { const d = new Date(`${s}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + days); return iso(d); };
const daysBetween = (a, b) => Math.round((new Date(`${b}T12:00:00Z`) - new Date(`${a}T12:00:00Z`)) / 86400000);

function serverToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: getTimeZone() }).format(serverNow());
}

function monthShort(s) {
  return new Intl.DateTimeFormat(getLang() === "ar" ? "ar-u-nu-latn" : "en-GB", { month: "short", timeZone: "UTC" })
    .format(new Date(`${s}T12:00:00Z`));
}

function dateRange(from, to) {
  if (!to || to === from) return fmtDate(from, { weekday: true });
  return t("{from} to {to}", { from: fmtDate(from), to: fmtDate(to) });
}

export default async function bellsExceptions(ctx) {
  const { root } = ctx;
  const canEdit = can("bells.schedule.edit");
  let exceptions = null;
  let calendar = [];
  let timetables = [];
  let schedules = [];
  let today = serverToday();

  async function load() {
    today = serverToday();
    const start = shift(today, -new Date(`${today}T12:00:00Z`).getUTCDay());      // Sunday of this week
    try {
      const [ex, cal, tts, sch] = await Promise.all([
        api("/api/bells/exceptions"),
        api("/api/bells/calendar", { query: { start, days: 42 } }),
        api("/api/bells/timetables"),
        api("/api/bells/schedules"),
      ]);
      exceptions = Array.isArray(ex) ? ex : (ex.exceptions || []);
      calendar = cal.days || [];
      timetables = Array.isArray(tts) ? tts : (tts.timetables || []);
      schedules = Array.isArray(sch) ? sch : (sch.schedules || []);
      render();
    } catch (err) {
      setHTML(root, html`${header()}${errorState(err)}`);
    }
  }

  function header() {
    return pageHeader({
      title: t("Holidays & changes"),
      description: t("Mark holidays, days without bells, and days that need a different timetable. Holidays and no-bells days always win over a different timetable."),
      actions: canEdit ? html`<button type="button" class="btn btn-primary" data-action="new">${icon("calendar-plus")} ${t("Add a holiday or change")}</button>` : "",
    });
  }

  function render() {
    setHTML(root, html`${header()}
      <div class="row row-cards">
        <div class="col-xl-7">${calendarCard()}</div>
        <div class="col-xl-5">${listCard()}</div>
      </div>`);
  }

  // ------------------------------------------------------------------ six-week overview
  function calendarCard() {
    if (!calendar.length) return "";
    const first = calendar[0].date;
    const last = calendar[calendar.length - 1].date;
    return html`<div class="card">
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Next six weeks")}</h2>
          <div class="small text-secondary mt-1">${t("{from} to {to}", { from: fmtDate(first), to: fmtDate(last) })}</div>
        </div>
      </div>
      <div class="card-body">
        <div class="d-grid gap-1 mb-1" style="grid-template-columns:repeat(7,minmax(0,1fr))" aria-hidden="true">
          ${WEEK_ORDER.map((d) => html`<div class="small text-secondary text-center text-truncate">${weekdayName(d, "short")}</div>`)}
        </div>
        <div class="d-grid gap-1" style="grid-template-columns:repeat(7,minmax(0,1fr))" role="list" aria-label="${t("Next six weeks")}">
          ${calendar.map(dayCell)}
        </div>
        <ul class="list-inline small text-secondary mt-3 mb-0">
          ${["school_day", "override", "holiday", "no_bells", "no_schedule"].map((k) => html`<li class="list-inline-item me-3">
            <span class="status-dot ${DAY[k].dot} me-1"></span>${DAY[k].label()}</li>`)}
        </ul>
        ${canEdit ? html`<p class="form-hint mt-2 mb-0">${t("Tap a day to add a holiday or change on that date, or to edit the one already there.")}</p>`
          : html`<p class="form-hint mt-2 mb-0">${t("Tap a day to see its bells.")}</p>`}
      </div>
    </div>`;
  }

  function dayCell(d) {
    const st = DAY[d.status] || DAY.no_schedule;
    const dayNum = Number(d.date.slice(8));
    const isToday = d.date === today;
    const past = d.date < today;
    const label = [fmtDate(d.date, { weekday: true }), st.label(), d.exception?.name, d.bell_count ? tn(d.bell_count, "{n} bell", "{n} bells") : ""]
      .filter(Boolean).join(" · ");
    const inner = html`
      <span class="d-flex align-items-center gap-1 w-100">
        <span class="${isToday ? "fw-bold text-primary" : "fw-semibold"} tabular">${dayNum}${dayNum === 1 || d === calendar[0] ? html`<span class="d-none d-md-inline fw-normal"> ${monthShort(d.date)}</span>` : ""}</span>
        ${st.dot ? html`<span class="status-dot ${st.dot} ms-auto"></span>` : ""}
      </span>
      <span class="small w-100 d-none d-md-block cal-cell-text">${d.exception ? d.exception.name : (d.bell_count ? tn(d.bell_count, "{n} bell", "{n} bells") : "")}</span>`;
    const bg = st.cell.startsWith("bg-") ? st.cell : "bg-transparent";
    const text = d.status === "not_school_day" ? "text-secondary" : "text-reset";
    const cls = `d-flex flex-column align-items-start text-start rounded p-1 p-md-2 w-100 h-100 border ${isToday ? "border-primary border-2" : ""} ${bg} ${text} ${past ? "opacity-50" : ""}`;
    const style = "min-height:3.25rem";
    if (canEdit) {
      return html`<div role="listitem"><button type="button" class="${cls}" style="${style}"
        data-action="day" data-date="${d.date}" ${d.exception ? raw(`data-exception="${Number(d.exception.id)}"`) : ""}
        aria-label="${label}" title="${label}" ${isToday ? raw('aria-current="date"') : ""}>${inner}</button></div>`;
    }
    return html`<div role="listitem"><a href="#/bells?date=${d.date}" class="${cls} text-decoration-none" style="${style}"
      aria-label="${label}" title="${label}" ${isToday ? raw('aria-current="date"') : ""}>${inner}</a></div>`;
  }

  // ------------------------------------------------------------------ list
  function listCard() {
    const current = exceptions.filter((e) => e.date_to >= today).sort((a, b) => a.date_from.localeCompare(b.date_from));
    const past = exceptions.filter((e) => e.date_to < today).sort((a, b) => b.date_from.localeCompare(a.date_from));
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Upcoming and current")}</h2></div>
      ${current.length ? html`<div class="list-group list-group-flush">${current.map(item)}</div>`
        : emptyState({
          iconName: "calendar-check",
          title: t("No holidays or changes coming up"),
          text: canEdit
            ? t("Add public holidays, exam days and half days here so the bells follow them automatically.")
            : t("The normal weekly schedule applies on every day."),
          action: canEdit ? html`<button type="button" class="btn btn-outline-secondary" data-action="new">${icon("calendar-plus")} ${t("Add a holiday or change")}</button>` : "",
        })}
      ${past.length ? html`<details class="border-top">
        <summary class="card-body py-3 text-secondary" style="cursor:pointer">${t("Past ({n})", { n: past.length })}</summary>
        <div class="list-group list-group-flush">${past.map(item)}</div>
      </details>` : ""}
    </div>`;
  }

  function item(e) {
    const k = KINDS[e.kind] || KINDS.holiday;
    const tt = timetables.find((x) => x.id === e.timetable_id);
    const sched = schedules.find((x) => x.id === e.schedule_id);
    const length = daysBetween(e.date_from, e.date_to) + 1;
    const now = e.date_from <= today && e.date_to >= today;
    return html`<div class="list-group-item">
      <div class="d-flex gap-3 align-items-start">
        <span class="avatar avatar-sm ${k.cls}" aria-hidden="true">${icon(k.icon)}</span>
        <div class="flex-fill" style="min-width:0">
          <div class="fw-semibold d-flex flex-wrap align-items-center gap-2">${e.name}
            ${now ? html`<span class="badge bg-green-lt">${t("In effect now")}</span>` : ""}</div>
          <div class="small text-secondary">${dateRange(e.date_from, e.date_to)}${length > 1 ? html` · ${tn(length, "{n} day", "{n} days")}` : ""}</div>
          <div class="small mt-1">
            ${e.kind === "override"
              ? html`${t("Different timetable")}: ${tt ? html`<a href="#/bells/timetables/${tt.id}">${tt.name}</a>` : html`<span class="text-danger">${t("Missing timetable")}</span>`}`
              : k.label()}
            <span class="text-secondary"> · ${e.schedule_id ? t("Only “{name}”", { name: sched ? sched.name : t("a deleted schedule") }) : t("All schedules")}</span>
          </div>
        </div>
        ${canEdit ? html`<div class="btn-list flex-nowrap">
          <button type="button" class="btn btn-ghost-secondary btn-icon" data-action="edit" data-id="${e.id}" aria-label="${t("Edit {name}", { name: e.name })}" title="${t("Edit")}">${icon("pencil")}</button>
          <button type="button" class="btn btn-ghost-danger btn-icon" data-action="delete" data-id="${e.id}" aria-label="${t("Delete {name}", { name: e.name })}" title="${t("Delete")}">${icon("trash")}</button>
        </div>` : ""}
      </div>
    </div>`;
  }

  // ------------------------------------------------------------------ add / edit
  async function editDialog(e = null, preset = {}) {
    const v = e || { name: "", kind: "holiday", date_from: preset.date || today, date_to: preset.date || today, timetable_id: null, schedule_id: null };
    const body = html`
      ${field({ label: t("Name"), name: "name", value: v.name, required: true, attrs: 'maxlength="80" autocomplete="off"', placeholder: t("For example: National holiday") })}
      <div class="mb-3">
        <div class="form-label">${t("What happens on these dates")}</div>
        <div class="form-selectgroup form-selectgroup-boxes d-flex flex-column gap-2">
          ${Object.entries(KINDS).map(([key, k]) => html`<label class="form-selectgroup-item flex-fill">
            <input type="radio" name="kind" value="${key}" class="form-selectgroup-input" ${v.kind === key ? raw("checked") : ""}>
            <div class="form-selectgroup-label d-flex align-items-center gap-3 p-3 text-start">
              <span class="form-selectgroup-check"></span>
              <span>
                <span class="fw-semibold d-block">${icon(k.icon, "me-1")} ${k.label()}</span>
                <span class="small text-secondary d-block">${k.explain()}</span>
              </span>
            </div>
          </label>`)}
        </div>
      </div>
      <div data-timetable>
        ${timetables.length
          ? selectField({ label: t("Timetable to use"), name: "timetable_id", value: v.timetable_id ?? "", required: true, dataType: "int",
            options: [{ value: "", label: t("Choose a timetable"), disabled: true }, ...timetables.map((x) => ({ value: x.id, label: x.name }))] })
          : html`<div class="alert alert-warning">${t("There are no timetables yet. Create one under Timetables first.")}</div>`}
      </div>
      <div class="row">
        ${field({ label: t("From"), name: "date_from", type: "date", value: v.date_from, required: true, cls: "col-sm-6" })}
        ${field({ label: t("To"), name: "date_to", type: "date", value: v.date_to || v.date_from, required: true, cls: "col-sm-6", hint: t("For a single day, use the same date.") })}
      </div>
      <p class="form-hint" data-length aria-live="polite"></p>
      ${selectField({ label: t("Applies to"), name: "schedule_id", value: v.schedule_id ?? "", dataType: "int",
        options: [{ value: "", label: t("All schedules") }, ...schedules.map((x) => ({ value: x.id, label: x.name }))],
        hint: t("Most schools use “All schedules”. Pick one schedule only if you run several at the same time.") })}
      <p class="form-hint mb-0">${icon("info-circle")} ${t("If a holiday or no-bells day overlaps a different timetable, no bells ring.")}</p>`;
    const saved = await formDialog({
      title: e ? t("Edit holiday or change") : t("Add a holiday or change"),
      submitLabel: e ? t("Save") : t("Add"),
      body,
      onMount: (d, form) => {
        const sync = () => {
          const kind = form.querySelector("[name=kind]:checked")?.value;
          const box = form.querySelector("[data-timetable]");
          box.hidden = kind !== "override";
          if (form.timetable_id) form.timetable_id.disabled = kind !== "override";
          form.date_to.min = form.date_from.value || "";
          const lenEl = form.querySelector("[data-length]");
          const a = form.date_from.value;
          const b = form.date_to.value;
          lenEl.textContent = a && b && b >= a ? tn(daysBetween(a, b) + 1, "{n} day", "{n} days") : "";
        };
        form.date_from.addEventListener("change", () => {
          if (!form.date_to.value || form.date_to.value < form.date_from.value) form.date_to.value = form.date_from.value;
        });
        form.addEventListener("change", sync);
        form.addEventListener("input", sync);
        sync();
      },
      onSubmit: (val) => {
        const payload = {
          name: (val.name || "").trim(),
          kind: val.kind,
          date_from: val.date_from,
          date_to: val.date_to || val.date_from,
          timetable_id: val.kind === "override" ? (val.timetable_id ?? null) : null,
          schedule_id: val.schedule_id ?? null,
        };
        if (payload.date_to < payload.date_from) throw new Error(t("The end date must be on or after the start date."));
        return e
          ? api(`/api/bells/exceptions/${e.id}`, { method: "PUT", body: payload })
          : api("/api/bells/exceptions", { method: "POST", body: payload });
      },
    });
    if (!saved) return;
    toast(e ? t("“{name}” saved.", { name: saved.name }) : t("“{name}” added.", { name: saved.name }));
    await load();
  }

  const byId = (id) => exceptions.find((x) => String(x.id) === String(id));

  on(root, "click", "[data-action=retry]", () => { setHTML(root, skeleton(6)); load(); });
  on(root, "click", "[data-action=new]", () => editDialog().catch(toastError));
  on(root, "click", "[data-action=day]", (ev, el) => {
    const ex = el.dataset.exception ? byId(el.dataset.exception) : null;
    editDialog(ex || null, { date: el.dataset.date }).catch(toastError);
  });
  on(root, "click", "[data-action=edit]", (ev, el) => { const ex = byId(el.dataset.id); if (ex) editDialog(ex).catch(toastError); });
  on(root, "click", "[data-action=delete]", async (ev, el) => {
    const ex = byId(el.dataset.id);
    if (!ex) return;
    const ok = await confirmDialog({
      title: t("Delete “{name}”?", { name: ex.name }),
      message: ex.kind === "override"
        ? t("The normal timetable will be used again on {dates}.", { dates: dateRange(ex.date_from, ex.date_to) })
        : t("Bells will ring as normal again on {dates}.", { dates: dateRange(ex.date_from, ex.date_to) }),
      confirmLabel: t("Delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await withBusy(el, () => api(`/api/bells/exceptions/${ex.id}`, { method: "DELETE" }));
      toast(t("“{name}” deleted.", { name: ex.name }));
      await load();
    } catch (err) {
      toastError(err);
    }
  });

  await load();
}
