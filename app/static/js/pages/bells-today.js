// School Bells › Today: the day's bells with live status, ring now, and a two-week outlook.

import { api } from "../api.js";
import { fmtCountdown, fmtDate, fmtDuration, fmtTime, t, tn, weekdayName } from "../i18n.js";
import { can, kickBroadcast, onBroadcast, serverNow } from "../store.js";
import {
  $, alertBox, bellLabel, broadcastTitle, errorState, formDialog, html, icon, on, pageHeader, raw, selectField, setHTML, statusBadge, toast, toastError,
} from "../ui.js";

const STATUS_TEXT = {
  school_day: "School day",
  override: "Special timetable",
  holiday: "Holiday — no bells",
  no_bells: "No bells today",
  not_school_day: "Not a school day",
  no_schedule: "No active schedule",
};

export function dayStatusBadge(status) {
  const cls = { school_day: "bg-green-lt", override: "bg-azure-lt", holiday: "bg-purple-lt", no_bells: "bg-secondary-lt",
    not_school_day: "bg-secondary-lt", no_schedule: "bg-yellow-lt" }[status] || "bg-secondary-lt";
  return html`<span class="badge ${cls}">${t(STATUS_TEXT[status] || status)}</span>`;
}

function isoDate(d) { return d.toISOString().slice(0, 10); }
function shiftDate(iso, days) { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return isoDate(d); }

export async function ringDialog({ preset = {} } = {}) {
  const [sounds, dests] = await Promise.all([api("/api/bells/sounds"), api("/api/system/destinations")]);
  const files = sounds.files.filter((f) => f.playable);
  if (!files.length) { toast(t("Upload a bell sound first."), "warning"); return null; }
  const zones = dests.zones;
  const body = html`
    ${selectField({ label: t("Sound"), name: "sound", value: preset.sound || files[0].name, required: true,
      options: files.map((f) => ({ value: f.name, label: `${f.name.replace(/\.wav$/i, "")} (${fmtDuration(f.duration)})` })) })}
    <div class="mb-3">
      <div class="form-label">${t("Where")}</div>
      ${zones.length ? html`<div class="form-selectgroup form-selectgroup-boxes d-flex flex-column gap-2">
        <label class="form-check"><input class="form-check-input" type="checkbox" name="all_speakers" checked>
          <span class="form-check-label">${t("All enabled speakers")}</span></label>
        ${zones.map((z) => html`<label class="form-check"><input class="form-check-input" type="checkbox" name="zone_ids" value="${z.id}" data-type="list">
          <span class="form-check-label">${z.name} <span class="text-secondary">(${tn(z.destination_ids.length, "{n} speaker", "{n} speakers")})</span></span></label>`)}
      </div><div class="form-hint">${t("Choose zones, or leave them all unticked to use every enabled speaker.")}</div>`
      : html`<p class="text-secondary mb-0">${t("All enabled speakers")}</p>`}
    </div>
    <div class="row">
      ${selectField({ label: t("Play"), name: "playback_mode", cls: "col-sm-6", value: "once",
        options: [{ value: "once", label: t("Once") }, { value: "repeat", label: t("Several times") }, { value: "duration", label: t("For a set time") }] })}
      <div class="col-sm-3 mb-3"><label class="form-label" for="ring-repeat">${t("Times")}</label>
        <input id="ring-repeat" class="form-control" type="number" name="repeat_count" min="1" max="20" value="2" data-type="int"></div>
      <div class="col-sm-3 mb-3"><label class="form-label" for="ring-dur">${t("Seconds")}</label>
        <input id="ring-dur" class="form-control" type="number" name="duration_seconds" min="1" max="600" value="10" data-type="int"></div>
    </div>
    <label class="form-check"><input class="form-check-input" type="checkbox" name="test">
      <span class="form-check-label">${t("This is a test (recorded as a test in the history)")}</span></label>`;
  return formDialog({
    title: t("Ring a bell now"),
    submitLabel: raw(`${icon("bell-ringing").s} ${t("Ring now")}`),
    body,
    onMount: (d, form) => {
      const sync = () => {
        const mode = form.playback_mode.value;
        form.repeat_count.closest(".mb-3").hidden = mode !== "repeat";
        form.duration_seconds.closest(".mb-3").hidden = mode !== "duration";
      };
      form.playback_mode.addEventListener("change", sync);
      const all = form.querySelector("[name=all_speakers]");
      form.addEventListener("change", (e) => {
        if (e.target.name === "zone_ids" && all) all.checked = !form.querySelector("[name=zone_ids]:checked");
        if (e.target === all && all.checked) form.querySelectorAll("[name=zone_ids]").forEach((c) => { c.checked = false; });
        if (e.target === all && !all.checked && !form.querySelector("[name=zone_ids]:checked")) all.checked = true;
      });
      sync();
    },
    onSubmit: (v) => submitRing({ sound: v.sound, zone_ids: v.zone_ids || [], playback_mode: v.playback_mode,
      repeat_count: v.repeat_count, duration_seconds: v.duration_seconds, test: v.test }),
  });
}

async function submitRing(body, force = false) {
  const { confirmDialog } = await import("../ui.js");
  try {
    const r = await api("/api/bells/ring", { method: "POST", body: { ...body, force } });
    toast(t("The bell is ringing."));
    kickBroadcast();
    return r;
  } catch (err) {
    if (err.code === "line_busy") {
      if (!err.detail.can_interrupt) throw new Error(t("“{title}” is playing now and you are not allowed to stop it. Try again when it has finished.", { title: broadcastTitle(err.detail.active.title) }));
      const ok = await confirmDialog({
        title: t("Something is already playing"),
        message: t("“{title}” is playing now. Stop it and ring this bell instead?", { title: broadcastTitle(err.detail.active.title) }),
        confirmLabel: t("Stop it and ring"), danger: true,
      });
      return ok ? submitRing(body, true) : false;
    }
    throw err;
  }
}

export default async function bellsToday(ctx) {
  const { root } = ctx;
  let date = ctx.query.date || null;
  let plan = null;
  let calendar = null;
  let refreshTimer = null;
  let tick = null;

  async function load() {
    clearTimeout(refreshTimer);
    try {
      const [p, cal] = await Promise.all([
        api("/api/bells/day", { query: { date } }),
        calendar && date === plan?.date ? Promise.resolve(calendar) : api("/api/bells/calendar", { query: { days: 14 } }),
      ]);
      plan = p;
      calendar = cal;
      render();
    } catch (err) {
      setHTML(root, errorState(err));
    }
    refreshTimer = setTimeout(load, 30000);
  }

  function render() {
    const today = plan.now.slice(0, 10);
    const isToday = plan.date === today;
    const actions = [];
    if (can("bells.broadcast")) actions.push(html`<button class="btn btn-primary" data-action="ring">${icon("bell-ringing")} ${t("Ring a bell now")}</button>`);
    if (can("bells.schedule.edit")) actions.push(html`<a class="btn btn-outline-secondary" href="#/bells/calendar">${icon("calendar-off")} ${t("Add a holiday")}</a>`);
    setHTML(root, html`
      ${pageHeader({ title: isToday ? t("School bells today") : t("School bells"), description: t("What rings, when and where. Times use the school's timezone ({tz}).", { tz: plan.timezone }), actions })}
      <div class="row row-cards">
        <div class="col-lg-8">
          <div class="card">
            <div class="card-header flex-wrap gap-2">
              <div class="btn-group" role="group" aria-label="${t("Choose day")}">
                <button class="btn btn-icon" data-action="day" data-shift="-1" aria-label="${t("Previous day")}">${icon("chevron-left", "flip-rtl")}</button>
                <button class="btn ${isToday ? "active" : ""}" data-action="today">${t("Today")}</button>
                <button class="btn btn-icon" data-action="day" data-shift="1" aria-label="${t("Next day")}">${icon("chevron-right", "flip-rtl")}</button>
              </div>
              <div class="ms-2">
                <div class="fw-semibold">${fmtDate(plan.date, { weekday: true })}</div>
                <div class="small text-secondary">${plan.schedule ? t("Schedule: {name}", { name: plan.schedule.name }) : ""}${plan.timetable ? " · " + t("Timetable: {name}", { name: plan.timetable.name }) : ""}</div>
              </div>
              <div class="ms-auto">${dayStatusBadge(plan.status)}</div>
            </div>
            ${dayNotices()}
            ${plan.occurrences.length ? bellList() : emptyDay()}
          </div>
        </div>
        <div class="col-lg-4">
          ${nextCard()}
          ${calendarCard()}
        </div>
      </div>`);
    updateCountdown();
  }

  function dayNotices() {
    const out = [];
    if (plan.exception && plan.exception.kind === "override") out.push(alertBox("info", t("“{name}” replaces the normal timetable on this day.", { name: plan.exception.name })));
    if (plan.overlapping?.length) out.push(alertBox("warning", t("More than one schedule covers this date. “{name}” is used because it starts latest.", { name: plan.schedule.name })));
    plan.warnings.forEach((w) => out.push(alertBox("warning", t(w.message))));
    if (plan.occurrences.some((o) => o.sound_missing)) out.push(alertBox("danger", t("A bell sound used today is missing from the library. Those bells will fail.")));
    return out.length ? html`<div class="card-body pb-0">${out}</div>` : "";
  }

  function emptyDay() {
    const reasons = {
      holiday: t("“{name}” — enjoy the day off.", { name: plan.exception?.name || "" }),
      no_bells: t("Bells are turned off for “{name}”.", { name: plan.exception?.name || "" }),
      not_school_day: t("The weekly schedule has no timetable for {day}.", { day: weekdayName(plan.weekday) }),
      no_schedule: t("No weekly schedule covers this date. Create or extend a schedule to ring bells."),
    };
    const action = plan.status === "no_schedule" && can("bells.schedule.edit")
      ? html`<a class="btn btn-primary" href="#/bells/schedules">${icon("calendar-plus")} ${t("Set up a weekly schedule")}</a>` : "";
    return html`<div class="empty">
      <div class="empty-icon">${icon(plan.status === "holiday" ? "beach" : "bell-off")}</div>
      <p class="empty-title">${t(STATUS_TEXT[plan.status] || "No bells")}</p>
      <p class="empty-subtitle text-secondary">${reasons[plan.status] || ""}</p>
      ${action ? html`<div class="empty-action">${action}</div>` : ""}
    </div>`;
  }

  function bellList() {
    const nextKey = plan.next?.key;
    return html`<ul class="timeline-list" aria-label="${t("Bells")}">
      ${plan.occurrences.map((o) => {
        const ex = o.execution;
        const conflict = (o.conflicts || []).find((c) => c.outcome !== "wins");
        return html`<li class="timeline-row ${o.past ? "past" : ""} ${o.key === nextKey ? "next" : ""}">
          <div class="timeline-time tabular">${fmtTime(o.at)}</div>
          <div>
            <div class="timeline-title">${o.labels.map(bellLabel).join(" · ")}</div>
            <div class="timeline-sub">${icon("music")} ${o.sound.replace(/\.wav$/i, "")}${o.playback.mode === "repeat" ? " × " + o.playback.repeat_count : ""}${o.playback.mode === "duration" ? " · " + fmtDuration(o.playback.duration_seconds) : ""}
              · ${icon("speakerphone")} ${zoneNames(o.zone_ids)}</div>
            ${conflict ? html`<div class="small text-warning mt-1">${icon("alert-triangle")} ${conflictText(conflict)}</div>` : ""}
            ${ex && ex.reason && ex.status !== "success" ? html`<div class="small text-secondary mt-1">${t(ex.reason)}</div>` : ""}
          </div>
          <div class="timeline-actions">
            ${ex ? statusBadge(ex.status) : o.key === nextKey ? html`<span class="badge bg-primary text-primary-fg">${t("Next")}</span>` : o.past ? "" : html`<span class="badge bg-secondary-lt">${t("Scheduled")}</span>`}
          </div>
        </li>`;
      })}
    </ul>`;
  }

  let zoneMap = {};
  function zoneNames(ids) {
    if (!ids || !ids.length) return t("All speakers");
    return ids.map((id) => zoneMap[id] || t("Unknown zone")).join(", ");
  }

  function conflictText(c) {
    if (c.outcome === "skipped") return t("Overlaps “{with}”, which has priority. This bell will probably be skipped.", { with: broadcastTitle(c.with) });
    if (c.outcome === "interrupts") return t("“{with}” starts while this bell is playing and will cut it short.", { with: broadcastTitle(c.with) });
    return t("Overlaps “{with}”. This bell may start a little late.", { with: broadcastTitle(c.with) });
  }

  function nextCard() {
    const n = plan.next;
    if (!n || plan.date !== plan.now.slice(0, 10)) return "";
    return html`<div class="card mb-3 next-card"><div class="card-body">
      <div class="label">${t("Next bell")}</div>
      <div class="d-flex align-items-end gap-3 mt-1">
        <div class="countdown tabular" data-countdown="${n.at}">—</div>
        <div class="text-secondary pb-1">${t("at {time}", { time: fmtTime(n.at) })}</div>
      </div>
      <div class="mt-2 fw-semibold">${n.labels.map(bellLabel).join(" · ")}</div>
    </div></div>`;
  }

  function calendarCard() {
    if (!calendar) return "";
    return html`<div class="card"><div class="card-header"><h2 class="card-title">${t("Next two weeks")}</h2></div>
      <div class="list-group list-group-flush">
        ${calendar.days.map((d) => html`<a href="#/bells?date=${d.date}" class="list-group-item list-group-item-action ${d.date === plan.date ? "active" : ""}" ${d.date === plan.date ? raw('aria-current="date"') : ""}>
          <div class="d-flex align-items-center gap-2">
            <div class="flex-fill"><div>${fmtDate(d.date, { weekday: true, year: false })}</div>
              <div class="small text-secondary">${d.bell_count ? tn(d.bell_count, "{n} bell", "{n} bells") + ` · ${d.first}–${d.last}` : (d.exception ? d.exception.name : "")}</div></div>
            ${dayStatusBadge(d.status)}
          </div></a>`)}
      </div></div>`;
  }

  function updateCountdown() {
    const el = $("[data-countdown]", root);
    if (!el) return;
    const secs = (new Date(el.dataset.countdown).getTime() - serverNow().getTime()) / 1000;
    el.textContent = secs > 0 ? fmtCountdown(secs) : t("now");
    if (secs < -3 && reloadedFor !== el.dataset.countdown) { reloadedFor = el.dataset.countdown; load(); }
  }
  let reloadedFor = null;

  on(root, "click", "[data-action=ring]", async () => {
    try { await ringDialog(); } catch (err) { toastError(err); }
  });
  on(root, "click", "[data-action=day]", (e, el) => { date = shiftDate(plan.date, Number(el.dataset.shift)); history.replaceState(null, "", `#/bells?date=${date}`); load(); });
  on(root, "click", "[data-action=today]", () => { date = null; history.replaceState(null, "", "#/bells"); load(); });
  on(root, "click", "[data-action=retry]", () => load());

  try {
    const dests = await api("/api/system/destinations");
    zoneMap = Object.fromEntries(dests.zones.map((z) => [z.id, z.name]));
  } catch { /* zone names are cosmetic */ }
  const unsub = onBroadcast((b) => { if (b.finished) load(); });
  tick = setInterval(updateCountdown, 1000);
  await load();
  return () => { clearTimeout(refreshTimer); clearInterval(tick); unsub(); };
}
