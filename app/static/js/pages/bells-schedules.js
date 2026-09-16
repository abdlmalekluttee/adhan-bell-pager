// School Bells › Weekly schedules: which timetable runs on each weekday between two dates.

import { api } from "../api.js";
import { fmtDate, fmtDuration, getTimeZone, t, tn, weekdayName } from "../i18n.js";
import { can, serverNow } from "../store.js";
import {
  alertBox, confirmDialog, emptyState, errorState, field, formDialog, html, icon, on, pageHeader, raw, selectField, setHTML,
  skeleton, switchField, toast, toastError, withBusy,
} from "../ui.js";

const WEEK_ORDER = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const soundLabel = (name) => (name || "").replace(/\.wav$/i, "");

function serverToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: getTimeZone() }).format(serverNow());
}

export function scheduleStatus(s, today = serverToday()) {
  if (!s.enabled) return { key: "off", cls: "bg-secondary-lt", text: t("Turned off") };
  if (s.effective_from > today) return { key: "future", cls: "bg-azure-lt", text: t("Starts {date}", { date: fmtDate(s.effective_from) }) };
  if (s.effective_to && s.effective_to < today) return { key: "ended", cls: "bg-secondary-lt", text: t("Ended") };
  return { key: "active", cls: "bg-green-lt", text: t("Active now") };
}

export function playbackText(s) {
  if (s.playback_mode === "repeat") return tn(s.repeat_count, "{n} time", "{n} times");
  if (s.playback_mode === "duration") return t("for {time}", { time: fmtDuration(s.duration_seconds) });
  return t("once");
}

export default async function bellsSchedules(ctx) {
  const { root } = ctx;
  const canEdit = can("bells.schedule.edit");
  let schedules = null;
  let timetables = [];
  let files = [];
  let zones = [];

  async function load() {
    try {
      const [s, tts, snd, dests] = await Promise.all([
        api("/api/bells/schedules"),
        api("/api/bells/timetables"),
        api("/api/bells/sounds").catch(() => ({ files: null })),
        api("/api/system/destinations").catch(() => ({ zones: [] })),
      ]);
      schedules = Array.isArray(s) ? s : (s.schedules || []);
      timetables = Array.isArray(tts) ? tts : (tts.timetables || []);
      files = snd.files;            // null when the sound list could not be loaded
      zones = dests.zones || [];
      render();
    } catch (err) {
      setHTML(root, html`${header()}${errorState(err)}`);
    }
  }

  function header() {
    return pageHeader({
      title: t("Weekly schedules"),
      description: t("A weekly schedule says which timetable runs on each weekday between two dates, where the bells ring and which sound they use. If two schedules cover the same date, the one that starts latest is used."),
      actions: canEdit && schedules?.length && timetables.length
        ? html`<button type="button" class="btn btn-primary" data-action="new">${icon("plus")} ${t("New schedule")}</button>` : "",
    });
  }

  function render() {
    if (!schedules.length) {
      let body;
      if (!timetables.length) {
        body = emptyState({
          iconName: "list-details",
          title: t("Create a timetable first"),
          text: t("A weekly schedule picks a timetable for each school day. Set up your day plan (periods and breaks) under Timetables, then come back here."),
          action: html`<a class="btn btn-primary" href="#/bells/timetables">${icon("list-details")} ${t("Go to Timetables")}</a>`,
        });
      } else {
        body = emptyState({
          iconName: "calendar-week",
          title: t("Create your first weekly schedule"),
          text: canEdit
            ? t("Choose the school days, the timetable for each day and the dates of the school year. Bells start ringing on the first date.")
            : t("No weekly schedule exists yet, so no bells ring. Ask a bells administrator to set one up."),
          action: canEdit ? html`<button type="button" class="btn btn-primary" data-action="new">${icon("plus")} ${t("Create a weekly schedule")}</button>` : "",
        });
      }
      setHTML(root, html`${header()}<div class="card">${body}</div>`);
      return;
    }
    const today = serverToday();
    setHTML(root, html`${header()}
      <div class="row row-cards">
        ${schedules.map((s) => html`<div class="col-12">${card(s, today)}</div>`)}
      </div>`);
  }

  function zoneNames(ids) {
    if (!ids || !ids.length) return t("All speakers");
    const map = Object.fromEntries(zones.map((z) => [z.id, z.name]));
    return ids.map((z) => map[z] || t("Unknown zone")).join(", ");
  }

  function card(s, today) {
    const st = scheduleStatus(s, today);
    const ttMap = Object.fromEntries(timetables.map((x) => [x.id, x]));
    const range = s.effective_to
      ? t("From {from} to {to}", { from: fmtDate(s.effective_from), to: fmtDate(s.effective_to) })
      : t("From {from} (no end date)", { from: fmtDate(s.effective_from) });
    const soundMissing = files !== null && !files.some((f) => f.name === s.sound);
    return html`<div class="card">
      <div class="card-header flex-wrap gap-2">
        <div class="me-auto">
          <h2 class="card-title d-flex flex-wrap align-items-center gap-2">${s.name} <span class="badge ${st.cls}">${st.text}</span></h2>
          <div class="small text-secondary mt-1">${icon("calendar")} ${range}</div>
        </div>
        ${canEdit ? html`<div class="btn-list">
          <button type="button" class="btn btn-outline-secondary" data-action="edit" data-id="${s.id}" aria-label="${t("Edit {name}", { name: s.name })}">${icon("pencil")} ${t("Edit")}</button>
          <button type="button" class="btn btn-ghost-danger btn-icon" data-action="delete" data-id="${s.id}" aria-label="${t("Delete {name}", { name: s.name })}" title="${t("Delete")}">${icon("trash")}</button>
        </div>` : ""}
      </div>
      <div class="card-body">
        ${s.description ? html`<p class="text-secondary">${s.description}</p>` : ""}
        ${(s.warnings || []).map((w) => alertBox("warning", t(w.message)))}
        ${!soundMissing ? "" : alertBox("danger", t("The sound “{name}” is missing from the bell sound library. Choose another sound, or these bells will fail.", { name: soundLabel(s.sound) }))}
        <div class="d-grid gap-2 mb-3" style="grid-template-columns:repeat(auto-fit,minmax(6.5rem,1fr))" role="list" aria-label="${t("Timetable for each weekday")}">
          ${WEEK_ORDER.map((d) => {
            const tt = s.days?.[d] ? ttMap[s.days[d]] : null;
            return html`<div role="listitem" class="border rounded px-2 py-2 ${tt ? "" : "bg-surface-secondary"}">
              <div class="small text-secondary">${weekdayName(d, "short")}</div>
              ${tt
                ? html`<a href="#/bells/timetables/${tt.id}" class="fw-semibold text-reset d-block text-truncate" title="${tt.name}">${tt.name}</a>`
                : html`<div class="text-secondary">${s.days?.[d] ? t("Missing timetable") : t("No school")}</div>`}
            </div>`;
          })}
        </div>
        <div class="row g-2 small">
          <div class="col-sm-6">${icon("speakerphone", "text-secondary")} <span class="text-secondary">${t("Rings on")}:</span> ${zoneNames(s.zone_ids)}</div>
          <div class="col-sm-6">${icon("music", "text-secondary")} <span class="text-secondary">${t("Sound")}:</span> <span class="ltr">${soundLabel(s.sound)}</span>, ${playbackText(s)}</div>
        </div>
      </div>
    </div>`;
  }

  async function editDialog(s = null) {
    const playable = (files || []).filter((f) => f.playable);
    if (!timetables.length) { toast(t("Create a timetable first."), "warning"); return; }
    const today = serverToday();
    const v = s || {
      name: "", description: "", enabled: true, effective_from: today, effective_to: null,
      days: {}, zone_ids: [], sound: playable[0]?.name || "", playback_mode: "once", repeat_count: 2, duration_seconds: 10,
    };
    const ttOptions = (value) => [{ value: "", label: t("No school") }, ...timetables.map((x) => ({ value: x.id, label: x.name }))]
      .map((o) => html`<option value="${o.value}" ${String(o.value) === String(value ?? "") ? raw("selected") : ""}>${o.label}</option>`);
    const soundOpts = playable.map((f) => ({ value: f.name, label: soundLabel(f.name) }));
    if (v.sound && !playable.some((f) => f.name === v.sound)) soundOpts.unshift({ value: v.sound, label: t("{name} (missing)", { name: soundLabel(v.sound) }) });
    const body = html`
      <div class="row">
        ${field({ label: t("Name"), name: "name", value: v.name, required: true, cls: "col-md-6", attrs: 'maxlength="80" autocomplete="off"', placeholder: t("For example: School year 2026–2027") })}
        ${field({ label: t("Description (optional)"), name: "description", value: v.description || "", cls: "col-md-6", attrs: 'maxlength="300" autocomplete="off"' })}
      </div>
      ${switchField({ label: t("Schedule is on"), name: "enabled", checked: v.enabled, hint: t("Turn it off to stop these bells without deleting the schedule.") })}
      <div class="row">
        ${field({ label: t("From"), name: "effective_from", type: "date", value: v.effective_from, required: true, cls: "col-sm-6" })}
        ${field({ label: t("To (optional)"), name: "effective_to", type: "date", value: v.effective_to || "", cls: "col-sm-6", dataType: "nullable", hint: t("Leave empty to keep ringing with no end date.") })}
      </div>
      <fieldset class="mb-3">
        <legend class="form-label">${t("Timetable for each day")}</legend>
        <div class="btn-list mb-2">
          <button type="button" class="btn btn-sm btn-outline-secondary" data-preset="sun,mon,tue,wed,thu">${t("Sunday–Thursday")}</button>
          <button type="button" class="btn btn-sm btn-outline-secondary" data-preset="mon,tue,wed,thu,fri">${t("Monday–Friday")}</button>
        </div>
        <div class="form-hint mb-2">${t("These buttons copy the first timetable you chose to those days and set the other days to “No school”.")}</div>
        <div class="row g-2">
          ${WEEK_ORDER.map((d) => html`<div class="col-sm-6 col-lg-3">
            <label class="form-label small mb-1" for="day-${d}">${weekdayName(d)}</label>
            <select id="day-${d}" class="form-select" name="day_${d}">${ttOptions(v.days?.[d])}</select>
          </div>`)}
        </div>
      </fieldset>
      <fieldset class="mb-3">
        <legend class="form-label">${t("Where the bells ring")}</legend>
        ${zones.length ? html`<div class="d-flex flex-wrap gap-3">
          ${zones.map((z) => html`<label class="form-check m-0"><input class="form-check-input" type="checkbox" name="zone_ids" value="${z.id}" data-type="list" ${(v.zone_ids || []).includes(z.id) ? raw("checked") : ""}>
            <span class="form-check-label">${z.name} <span class="text-secondary">(${tn(z.destination_ids.length, "{n} speaker", "{n} speakers")})</span></span></label>`)}
        </div>
        <div class="form-hint">${t("Leave all unticked to ring on every enabled speaker.")}</div>`
        : html`<p class="text-secondary mb-0">${t("All enabled speakers")}</p>`}
      </fieldset>
      <div class="row align-items-end">
        <div class="col-md-6 mb-3">
          <label class="form-label form-required" for="sch-sound">${t("Bell sound")}</label>
          <div class="input-group">
            <select id="sch-sound" class="form-select" name="sound" required>
              ${soundOpts.length ? "" : html`<option value="">${t("Upload a bell sound first")}</option>`}
              ${soundOpts.map((o) => html`<option value="${o.value}" ${o.value === v.sound ? raw("selected") : ""}>${o.label}</option>`)}
            </select>
            <button type="button" class="btn btn-outline-secondary" data-preview aria-pressed="false" aria-label="${t("Listen to this sound")}" title="${t("Listen to this sound")}" ${soundOpts.length ? "" : raw("disabled")}>${icon("player-play")}</button>
          </div>
          <div class="form-hint">${t("Rows in a timetable can use their own sound. Otherwise this one plays.")}</div>
        </div>
        <div class="col-md-6 mb-3">
          ${selectField({ label: t("How it plays"), name: "playback_mode", value: v.playback_mode, cls: "mb-0",
            options: [{ value: "once", label: t("Once") }, { value: "repeat", label: t("Several times") }, { value: "duration", label: t("For a set time") }] })}
        </div>
      </div>
      <div class="row">
        <div class="col-sm-6 mb-3" data-mode="repeat">
          <label class="form-label" for="sch-repeat">${t("Number of times")}</label>
          <input id="sch-repeat" class="form-control" type="number" name="repeat_count" min="1" max="20" value="${v.repeat_count || 2}" data-type="int" required>
          <div class="form-hint">${t("Between 1 and 20.")}</div>
        </div>
        <div class="col-sm-6 mb-3" data-mode="duration">
          <label class="form-label" for="sch-dur">${t("Seconds")}</label>
          <input id="sch-dur" class="form-control" type="number" name="duration_seconds" min="1" max="600" value="${v.duration_seconds || 10}" data-type="int" required>
          <div class="form-hint">${t("The sound repeats until this time is up (1 to 600 seconds).")}</div>
        </div>
      </div>`;

    let audio = null;
    const saved = await formDialog({
      title: s ? t("Edit weekly schedule") : t("New weekly schedule"),
      submitLabel: s ? t("Save schedule") : t("Create schedule"),
      size: "lg",
      body,
      onMount: (d, form) => {
        const syncMode = () => {
          form.querySelectorAll("[data-mode]").forEach((el) => {
            const show = el.dataset.mode === form.playback_mode.value;
            el.hidden = !show;
            el.querySelectorAll("input").forEach((i) => { i.disabled = !show; });
          });
        };
        const syncDates = () => { form.effective_to.min = form.effective_from.value || ""; };
        const previewBtn = form.querySelector("[data-preview]");
        const setPlaying = (on_) => {
          previewBtn.setAttribute("aria-pressed", on_ ? "true" : "false");
          previewBtn.innerHTML = icon(on_ ? "player-pause" : "player-play").s;
        };
        previewBtn.addEventListener("click", () => {
          const name = form.sound.value;
          if (!name) return;
          if (audio && !audio.paused) { audio.pause(); setPlaying(false); return; }
          if (!audio) {
            audio = new Audio();
            audio.addEventListener("ended", () => setPlaying(false));
            audio.addEventListener("pause", () => setPlaying(false));
            audio.addEventListener("error", () => { setPlaying(false); toast(t("This sound could not be played in the browser."), "warning"); });
          }
          audio.src = `/api/bells/sounds/${encodeURIComponent(name)}/file`;
          audio.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
        });
        form.sound.addEventListener("change", () => { if (audio) audio.pause(); });
        form.querySelectorAll("[data-preset]").forEach((btn) => btn.addEventListener("click", () => {
          const days = btn.dataset.preset.split(",");
          const chosen = WEEK_ORDER.map((d) => form[`day_${d}`].value).find((x) => x) || String(timetables[0].id);
          WEEK_ORDER.forEach((d) => { form[`day_${d}`].value = days.includes(d) ? chosen : ""; });
        }));
        form.playback_mode.addEventListener("change", syncMode);
        form.effective_from.addEventListener("change", syncDates);
        d.addEventListener("close", () => { if (audio) { audio.pause(); audio.removeAttribute("src"); } });
        syncMode();
        syncDates();
      },
      onSubmit: async (val) => {
        const days = {};
        WEEK_ORDER.forEach((d) => { days[d] = val[`day_${d}`] ? Number(val[`day_${d}`]) : null; });
        if (!Object.values(days).some(Boolean)) {
          const ok = await confirmDialog({
            title: t("No school days chosen"),
            message: t("Every day is set to “No school”, so this schedule will not ring any bells. Save it anyway?"),
            confirmLabel: t("Save anyway"),
          });
          if (!ok) return false;
        }
        const payload = {
          name: (val.name || "").trim(),
          description: (val.description || "").trim(),
          enabled: !!val.enabled,
          effective_from: val.effective_from,
          effective_to: val.effective_to || null,
          days,
          zone_ids: val.zone_ids || [],
          sound: val.sound,
          playback_mode: val.playback_mode,
          repeat_count: val.playback_mode === "repeat" ? val.repeat_count : (s?.repeat_count || 1),
          duration_seconds: val.playback_mode === "duration" ? val.duration_seconds : (s?.duration_seconds || 10),
        };
        return s
          ? api(`/api/bells/schedules/${s.id}`, { method: "PUT", body: payload })
          : api("/api/bells/schedules", { method: "POST", body: payload });
      },
    });
    if (!saved) return;
    toast(s ? t("Schedule “{name}” saved.", { name: saved.name }) : t("Schedule “{name}” created.", { name: saved.name }));
    await load();
  }

  on(root, "click", "[data-action=retry]", () => { setHTML(root, skeleton(5)); load(); });
  on(root, "click", "[data-action=new]", () => editDialog().catch(toastError));
  on(root, "click", "[data-action=edit]", (e, el) => {
    const s = schedules.find((x) => String(x.id) === el.dataset.id);
    if (s) editDialog(s).catch(toastError);
  });
  on(root, "click", "[data-action=delete]", async (e, el) => {
    const s = schedules.find((x) => String(x.id) === el.dataset.id);
    if (!s) return;
    const ok = await confirmDialog({
      title: t("Delete “{name}”?", { name: s.name }),
      message: t("Bells from this schedule will stop. Its timetables are kept. This cannot be undone."),
      confirmLabel: t("Delete schedule"),
      danger: true,
    });
    if (!ok) return;
    try {
      await withBusy(el, () => api(`/api/bells/schedules/${s.id}`, { method: "DELETE" }));
      toast(t("Schedule “{name}” deleted.", { name: s.name }));
      await load();
    } catch (err) {
      toastError(err);
    }
  });

  await load();
}
