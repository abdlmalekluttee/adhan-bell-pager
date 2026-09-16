// School Bells › Timetable editor: the rows of one day plan, with live checks and a period generator.

import { api } from "../api.js";
import { fmtDuration, t, tn } from "../i18n.js";
import { can, navigate } from "../store.js";
import {
  $, $$, alertBox, confirmDialog, debounce, dirtyTracker, emptyState, errorState, formDialog, html, icon, on, pageHeader, raw,
  saveBar, setHTML, skeleton, toast, toastError, TIME_INPUT_ATTRS } from "../ui.js";
import { usageChips } from "./bells-timetables.js";

const KINDS = [
  { value: "start_of_day", label: () => t("Start of day"), icon: "sunrise" },
  { value: "period", label: () => t("Period"), icon: "school" },
  { value: "break", label: () => t("Break"), icon: "coffee" },
  { value: "end_of_day", label: () => t("End of day"), icon: "sunset" },
  { value: "custom", label: () => t("Single bell"), icon: "bell" },
];
const KIND = Object.fromEntries(KINDS.map((k) => [k.value, k]));
const SPANS = new Set(["period", "break"]);        // row types that have an end time
const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;

const toMin = (hhmm) => (TIME_RE.test(hhmm || "") ? Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3)) : null);
const fromMin = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const soundLabel = (name) => (name || "").replace(/\.wav$/i, "");

let keySeq = 0;
const newKey = () => `r${++keySeq}`;

function defaultLabel(kind, rows) {
  if (kind === "period") return t("Period {n}", { n: rows.filter((r) => r.kind === "period").length + 1 });
  if (kind === "break") return t("Break");
  if (kind === "start_of_day") return t("Start of day");
  if (kind === "end_of_day") return t("End of day");
  return t("Bell");
}

export default async function bellsTimetableEdit(ctx) {
  const { root } = ctx;
  const id = ctx.params.id;
  const canEdit = can("bells.schedule.edit");
  const mq = window.matchMedia("(max-width: 767.98px)");

  let tt = null;
  let rows = [];
  let files = [];
  let zones = [];
  let validation = { ok: true, error: "", warnings: [] };
  let valSeq = 0;
  let tracker = null;
  const cleanups = [];

  // ------------------------------------------------------------------ load
  async function load() {
    try {
      const [timetable, sounds, dests] = await Promise.all([
        api(`/api/bells/timetables/${encodeURIComponent(id)}`),
        api("/api/bells/sounds").catch(() => ({ files: [] })),
        api("/api/system/destinations").catch(() => ({ zones: [] })),
      ]);
      tt = timetable;
      files = sounds.files || [];
      zones = dests.zones || [];
      setModel(tt);
      document.title = `${tt.name} · ${t("Adhan & Bell Pager")}`;
      if (canEdit) renderEditor(); else renderReadOnly();
    } catch (err) {
      setHTML(root, html`${pageHeader({ title: t("Timetable"), actions: backLink() })}${errorState(err)}`);
    }
  }

  function setModel(data) {
    rows = sortRows(data.events.map((e) => ({ ...e, zone_ids: e.zone_ids && e.zone_ids.length ? [...e.zone_ids] : null, _k: newKey(), _open: false })));
    validation = { ok: true, error: "", warnings: data.warnings || [] };
  }

  function sortRows(list) {
    return list
      .map((r, i) => ({ r, i }))
      .sort((a, b) => (toMin(a.r.start_time) ?? 1e9) - (toMin(b.r.start_time) ?? 1e9) || a.i - b.i)
      .map((x) => x.r);
  }
  const isSorted = () => sortRows(rows).every((r, i) => r === rows[i]);

  function backLink() {
    return html`<a href="#/bells/timetables" class="btn btn-ghost-secondary">${icon("chevron-left", "flip-rtl")} ${t("All timetables")}</a>`;
  }

  // ------------------------------------------------------------------ shared bits
  function summary() {
    // Bells at the same minute ring together (the server merges them), so count distinct minutes.
    const rings = new Set();
    let off = 0;
    for (const r of rows) {
      if (!r.enabled) { off += 1; continue; }
      if (r.ring_start && toMin(r.start_time) !== null) rings.add(r.start_time);
      if (SPANS.has(r.kind) && r.ring_end && toMin(r.end_time) !== null) rings.add(r.end_time);
    }
    const times = [...rings].sort();
    const parts = [];
    if (times.length) {
      parts.push(tn(times.length, "{n} bell", "{n} bells"));
      parts.push(t("first {time}", { time: times[0] }));
      parts.push(t("last {time}", { time: times[times.length - 1] }));
    } else {
      parts.push(t("No bells will ring"));
    }
    if (off) parts.push(tn(off, "{n} row turned off", "{n} rows turned off"));
    return parts.join(" · ");
  }

  function zoneNames(ids) {
    const map = Object.fromEntries(zones.map((z) => [z.id, z.name]));
    return ids.map((z) => map[z] || t("Unknown zone")).join(", ");
  }

  function kindBadge(kind) {
    const k = KIND[kind] || KIND.custom;
    return html`<span class="text-nowrap">${icon(k.icon, "text-secondary")} ${k.label()}</span>`;
  }

  // ------------------------------------------------------------------ read-only view
  function renderReadOnly() {
    setHTML(root, html`
      ${pageHeader({ title: tt.name, description: tt.description || t("A reusable day plan. Each row rings at its start time and, when shown, at its end time."), actions: backLink() })}
      <div class="row row-cards">
        <div class="col-lg-8">
          <div class="card">
            <div class="card-header"><h2 class="card-title">${t("Bells")}</h2><div class="card-actions small text-secondary tabular">${summary()}</div></div>
            ${warningsBody()}
            ${rows.length ? html`<div class="table-responsive"><table class="table table-vcenter card-table mb-0">
              <thead><tr><th>${t("Time")}</th><th>${t("Name")}</th><th>${t("Type")}</th><th>${t("Rings")}</th></tr></thead>
              <tbody>${rows.map((r) => {
                const spans = SPANS.has(r.kind) && r.end_time;
                const ringText = [r.ring_start ? t("at the start") : "", spans && r.ring_end ? t("at the end") : ""].filter(Boolean).join(" · ") || t("Does not ring");
                const extras = [];
                if (r.sound_start || (spans && r.sound_end)) extras.push(html`${icon("music")} ${[r.sound_start, spans ? r.sound_end : ""].filter(Boolean).map(soundLabel).join(" / ")}`);
                if (r.zone_ids && r.zone_ids.length) extras.push(html`${icon("speakerphone")} ${zoneNames(r.zone_ids)}`);
                return html`<tr class="${r.enabled ? "" : "text-secondary"}">
                  <td class="tabular text-nowrap fw-semibold"><span class="ltr">${spans ? `${r.start_time}–${r.end_time}` : r.start_time}</span></td>
                  <td>${r.label}${r.enabled ? "" : html` <span class="badge bg-secondary-lt ms-1">${t("Turned off")}</span>`}
                    ${extras.length ? html`<div class="small text-secondary">${extras.map((x, i) => html`${i ? " · " : ""}${x}`)}</div>` : ""}</td>
                  <td>${kindBadge(r.kind)}</td>
                  <td class="small">${ringText}</td>
                </tr>`;
              })}</tbody></table></div>`
              : emptyState({ iconName: "bell-off", title: t("This timetable has no rows yet") })}
          </div>
        </div>
        <div class="col-lg-4">${usageCard()}</div>
      </div>`);
  }

  function warningsBody() {
    return validation.warnings?.length ? html`<div class="card-body pb-0">${alertBox("warning",
      html`<ul class="mb-0 ps-3">${validation.warnings.map((w) => html`<li>${t(w.message)}</li>`)}</ul>`,
      { title: t("Please check these times") })}</div>` : "";
  }

  function usageCard() {
    const used = (tt.usage?.schedules?.length || 0) + (tt.usage?.exceptions?.length || 0);
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Used by")}</h2></div>
      <div class="card-body">
        ${usageChips(tt.usage)}
        <p class="form-hint mt-3 mb-0">${used
          ? t("Saving changes here changes the bells on every day these use.")
          : t("Choose this timetable for some weekdays in a weekly schedule, or use it for special dates under Holidays & changes.")}</p>
      </div>
    </div>`;
  }

  // ------------------------------------------------------------------ editor
  function renderEditor() {
    setHTML(root, html`
      ${pageHeader({
        title: tt.name,
        description: t("Each row rings at its start time and, if you tick it, at its end time. Bells at the same minute with the same sound and speakers ring once, so the end of one period and the start of the next do not ring twice."),
        actions: backLink(),
      })}
      <div data-editor>
        <div class="row row-cards">
          <div class="col-lg-8">
            <div class="card">
              <div class="card-header"><h2 class="card-title">${t("Details")}</h2></div>
              <div class="card-body">
                <div class="row">
                  <div class="col-md-5 mb-3 mb-md-0">
                    <label class="form-label form-required" for="tt-name">${t("Name")}</label>
                    <input id="tt-name" class="form-control" name="name" maxlength="80" autocomplete="off" required value="${tt.name}">
                    <div class="invalid-feedback">${t("Enter a timetable name.")}</div>
                  </div>
                  <div class="col-md-7">
                    <label class="form-label" for="tt-desc">${t("Description (optional)")}</label>
                    <input id="tt-desc" class="form-control" name="description" maxlength="300" autocomplete="off" value="${tt.description || ""}" placeholder="${t("For example: Sunday to Wednesday")}">
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="col-lg-4" data-usage>${usageCard()}</div>
          <div class="col-12">
            <div class="card">
              <div class="card-header flex-wrap gap-2">
                <div class="me-auto">
                  <h2 class="card-title">${t("Bells")}</h2>
                  <div class="small text-secondary tabular mt-1" data-summary aria-live="polite">${summary()}</div>
                </div>
                <button type="button" class="btn btn-outline-secondary" data-action="generate">${icon("wand")} ${t("Generate periods")}</button>
              </div>
              <div data-alerts aria-live="polite"></div>
              <div data-rows></div>
              <div class="card-footer d-flex flex-wrap gap-2 align-items-center">
                <button type="button" class="btn btn-outline-secondary" data-action="add" data-kind="period">${icon("plus")} ${t("Add period")}</button>
                <button type="button" class="btn btn-outline-secondary" data-action="add" data-kind="break">${icon("coffee")} ${t("Add break")}</button>
                <button type="button" class="btn btn-ghost-secondary" data-action="add" data-kind="custom">${icon("bell-plus")} ${t("Add single bell")}</button>
                <button type="button" class="btn btn-ghost-secondary ms-md-auto" data-action="sort" hidden>${icon("sort-ascending")} ${t("Sort by time")}</button>
              </div>
            </div>
          </div>
        </div>
      </div>
      ${saveBar()}`);
    const editor = $("[data-editor]", root);
    tracker = dirtyTracker(editor, $(".save-bar", root), { onSave: save, onDiscard: discard });
    renderRows();
    renderAlerts();
  }

  function renderRows({ focusKey, focusField } = {}) {
    const host = $("[data-rows]", root);
    if (!host) return;
    const active = document.activeElement;
    const restoreKey = focusKey ?? active?.closest?.("[data-k]")?.dataset.k;
    const restoreField = focusField ?? (active?.dataset?.f || (active?.dataset?.action ? `action:${active.dataset.action}` : null));
    if (!rows.length) {
      setHTML(host, html`<div class="card-body border-top">${emptyState({
        iconName: "list-details",
        title: t("No rows yet"),
        text: t("Generate the periods in one step, or add periods and breaks one at a time."),
        action: html`<button type="button" class="btn btn-primary" data-action="generate">${icon("wand")} ${t("Generate periods")}</button>`,
      })}</div>`);
    } else {
      setHTML(host, mq.matches ? mobileRows() : desktopRows());
    }
    const sortBtn = $("[data-action=sort]", root);
    if (sortBtn) sortBtn.hidden = isSorted();
    if (restoreKey && restoreField) {
      const sel = restoreField.startsWith("action:")
        ? `[data-k="${restoreKey}"] [data-action="${restoreField.slice(7)}"], [data-action="${restoreField.slice(7)}"][data-k="${restoreKey}"]`
        : `[data-k="${restoreKey}"][data-f="${restoreField}"]`;
      host.querySelector(sel)?.focus();
    }
    markInvalidRow();
  }

  function soundOptions(value) {
    const playable = files.filter((f) => f.playable);
    const known = playable.some((f) => f.name === value);
    return html`<option value="" ${!value ? raw("selected") : ""}>${t("Schedule default")}</option>
      ${playable.map((f) => html`<option value="${f.name}" ${f.name === value ? raw("selected") : ""}>${soundLabel(f.name)}</option>`)}
      ${value && !known ? html`<option value="${value}" selected>${t("{name} (missing)", { name: soundLabel(value) })}</option>` : ""}`;
  }

  function kindOptions(value) {
    return KINDS.map((k) => html`<option value="${k.value}" ${k.value === value ? raw("selected") : ""}>${k.label()}</option>`);
  }

  function moreButton(r) {
    const custom = !!(r.sound_start || r.sound_end || (r.zone_ids && r.zone_ids.length));
    return html`<button type="button" class="btn btn-ghost-secondary btn-icon ${custom ? "text-primary" : ""}" data-action="more" data-k="${r._k}"
      aria-expanded="${r._open ? "true" : "false"}" aria-controls="more-${r._k}"
      aria-label="${custom ? t("More options (this row has its own sound or speakers)") : t("More options")}" title="${t("Sound and speakers for this row")}">
      ${icon(r._open ? "chevron-up" : "adjustments-horizontal")}</button>`;
  }

  function rowButtons(r) {
    return html`${moreButton(r)}
      <button type="button" class="btn btn-ghost-secondary btn-icon" data-action="dup-row" data-k="${r._k}" aria-label="${t("Duplicate row")}" title="${t("Duplicate row")}">${icon("copy")}</button>
      <button type="button" class="btn btn-ghost-danger btn-icon" data-action="del-row" data-k="${r._k}" aria-label="${t("Delete row")}" title="${t("Delete row")}">${icon("trash")}</button>`;
  }

  function morePanel(r) {
    const spans = SPANS.has(r.kind);
    return html`<div class="row g-3" id="more-${r._k}" data-k="${r._k}">
      <div class="col-md-4">
        <label class="form-label" for="ss-${r._k}">${t("Sound at start")}</label>
        <select id="ss-${r._k}" class="form-select" data-k="${r._k}" data-f="sound_start">${soundOptions(r.sound_start)}</select>
      </div>
      ${spans ? html`<div class="col-md-4">
        <label class="form-label" for="se-${r._k}">${t("Sound at end")}</label>
        <select id="se-${r._k}" class="form-select" data-k="${r._k}" data-f="sound_end">${soundOptions(r.sound_end)}</select>
      </div>` : ""}
      <div class="col-md-4">
        <div class="form-label">${t("Speakers")}</div>
        ${zones.length ? zones.map((z) => html`<label class="form-check mb-1">
          <input class="form-check-input" type="checkbox" data-k="${r._k}" data-f="zone" value="${z.id}" ${(r.zone_ids || []).includes(z.id) ? raw("checked") : ""}>
          <span class="form-check-label">${z.name}</span></label>`)
          : html`<div class="text-secondary small">${t("No zones are set up. The schedule's speakers are used.")}</div>`}
      </div>
      <div class="col-12"><div class="form-hint">${t("Leave these on “Schedule default” and untick all speakers to use the sound and speakers chosen in the weekly schedule.")}</div></div>
    </div>`;
  }

  function desktopRows() {
    return html`<div class="table-responsive">
      <table class="table table-vcenter card-table table-editor mb-0">
        <thead><tr>
          <th>${t("Type")}</th><th>${t("Name")}</th><th>${t("Start")}</th><th>${t("End")}</th>
          <th class="text-center">${t("Ring at start")}</th><th class="text-center">${t("Ring at end")}</th>
          <th class="text-center">${t("On")}</th><th><span class="visually-hidden">${t("Actions")}</span></th>
        </tr></thead>
        <tbody>${rows.map((r) => {
          const spans = SPANS.has(r.kind);
          return html`<tr data-row="${r._k}" class="${r.enabled ? "" : "text-secondary"}">
            <td><select class="form-select" data-k="${r._k}" data-f="kind" aria-label="${t("Type")}">${kindOptions(r.kind)}</select></td>
            <td><input class="form-control" data-k="${r._k}" data-f="label" value="${r.label}" maxlength="80" aria-label="${t("Name")}" required>
              ${r.enabled ? "" : html`<div class="small text-secondary mt-1">${t("Turned off: this row does not ring")}</div>`}</td>
            <td><input class="form-control tabular" ${raw(TIME_INPUT_ATTRS)} data-k="${r._k}" data-f="start_time" value="${r.start_time}" aria-label="${t("Start time")}" required></td>
            <td>${spans
              ? html`<input class="form-control tabular" ${raw(TIME_INPUT_ATTRS)} data-k="${r._k}" data-f="end_time" value="${r.end_time || ""}" aria-label="${t("End time")}">`
              : html`<span class="text-secondary" title="${t("This type of row rings once and has no end time")}">—<span class="visually-hidden">${t("No end time")}</span></span>`}</td>
            <td class="text-center"><input class="form-check-input m-0" type="checkbox" data-k="${r._k}" data-f="ring_start" ${r.ring_start ? raw("checked") : ""} aria-label="${t("Ring at start")}"></td>
            <td class="text-center">${spans
              ? html`<input class="form-check-input m-0" type="checkbox" data-k="${r._k}" data-f="ring_end" ${r.ring_end ? raw("checked") : ""} ${r.end_time ? "" : raw("disabled")} aria-label="${t("Ring at end")}">`
              : html`<span class="text-secondary">—</span>`}</td>
            <td class="text-center"><label class="form-check form-switch d-inline-block m-0"><input class="form-check-input" type="checkbox" data-k="${r._k}" data-f="enabled" ${r.enabled ? raw("checked") : ""} aria-label="${t("Row is on")}"></label></td>
            <td class="text-end text-nowrap">${rowButtons(r)}</td>
          </tr>
          ${r._open ? html`<tr data-row-more="${r._k}"><td colspan="8" class="bg-surface-secondary">${morePanel(r)}</td></tr>` : ""}`;
        })}</tbody>
      </table>
    </div>`;
  }

  function mobileRows() {
    return html`<div class="list-group list-group-flush border-top">${rows.map((r) => {
      const spans = SPANS.has(r.kind);
      const k = r._k;
      return html`<div class="list-group-item" data-row="${k}">
        <div class="row g-2">
          <div class="col-7">
            <label class="form-label small mb-1" for="m-l-${k}">${t("Name")}</label>
            <input id="m-l-${k}" class="form-control" data-k="${k}" data-f="label" value="${r.label}" maxlength="80" required>
          </div>
          <div class="col-5">
            <label class="form-label small mb-1" for="m-k-${k}">${t("Type")}</label>
            <select id="m-k-${k}" class="form-select" data-k="${k}" data-f="kind">${kindOptions(r.kind)}</select>
          </div>
          <div class="col-6">
            <label class="form-label small mb-1" for="m-s-${k}">${t("Start")}</label>
            <input id="m-s-${k}" class="form-control tabular" ${raw(TIME_INPUT_ATTRS)} data-k="${k}" data-f="start_time" value="${r.start_time}" required>
          </div>
          <div class="col-6">
            ${spans ? html`<label class="form-label small mb-1" for="m-e-${k}">${t("End")}</label>
              <input id="m-e-${k}" class="form-control tabular" ${raw(TIME_INPUT_ATTRS)} data-k="${k}" data-f="end_time" value="${r.end_time || ""}">`
              : html`<div class="form-label small mb-1">${t("End")}</div><div class="small text-secondary pt-2">${t("Rings once")}</div>`}
          </div>
          <div class="col-12 d-flex flex-wrap align-items-center gap-3 pt-1">
            <label class="form-check m-0"><input class="form-check-input" type="checkbox" data-k="${k}" data-f="ring_start" ${r.ring_start ? raw("checked") : ""}>
              <span class="form-check-label">${t("Ring at start")}</span></label>
            ${spans ? html`<label class="form-check m-0"><input class="form-check-input" type="checkbox" data-k="${k}" data-f="ring_end" ${r.ring_end ? raw("checked") : ""} ${r.end_time ? "" : raw("disabled")}>
              <span class="form-check-label">${t("Ring at end")}</span></label>` : ""}
            <label class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" data-k="${k}" data-f="enabled" ${r.enabled ? raw("checked") : ""}>
              <span class="form-check-label">${t("On")}</span></label>
            <div class="ms-auto text-nowrap">${rowButtons(r)}</div>
          </div>
          ${r._open ? html`<div class="col-12"><div class="bg-surface-secondary rounded p-3 mt-1">${morePanel(r)}</div></div>` : ""}
        </div>
      </div>`;
    })}</div>`;
  }

  function renderAlerts() {
    const host = $("[data-alerts]", root);
    if (!host) return;
    const out = [];
    if (!validation.ok && validation.error) {
      out.push(alertBox("danger", t(validation.error), { title: t("This timetable cannot be saved yet") }));
    }
    if (validation.warnings?.length) {
      out.push(alertBox("warning", html`<ul class="mb-0 ps-3">${validation.warnings.map((w) => html`<li>${t(w.message)}</li>`)}</ul>`,
        { title: t("Please check these times") }));
    }
    setHTML(host, out.length ? html`<div class="card-body pb-0 border-top">${out}</div>` : "");
    markInvalidRow();
  }

  function markInvalidRow() {
    $$("[data-row]", root).forEach((el) => el.classList.remove("bg-red-lt", "table-danger"));
    $$("[data-f].is-invalid", root).forEach((el) => el.classList.remove("is-invalid"));
    if (validation.ok || !validation.error) return;
    const m = /^Row (\d+)/.exec(validation.error);
    const r = m ? rows[Number(m[1]) - 1] : null;
    if (!r) return;
    const rowEl = $(`[data-row="${r._k}"]`, root);
    rowEl?.classList.add(rowEl.tagName === "TR" ? "table-danger" : "bg-red-lt");
    const msg = validation.error.toLowerCase();
    const f = msg.includes("enter a name") ? "label" : msg.includes(" end") ? "end_time" : msg.includes(" start") ? "start_time" : null;
    if (f) $(`[data-k="${r._k}"][data-f="${f}"]`, root)?.classList.add("is-invalid");
  }

  function refreshLive() {
    const s = $("[data-summary]", root);
    if (s) s.textContent = summary();
    const sortBtn = $("[data-action=sort]", root);
    if (sortBtn) sortBtn.hidden = isSorted();
    validateSoon();
  }

  // ------------------------------------------------------------------ validation & save
  function payloadRows() {
    return rows.map((r) => {
      const spans = SPANS.has(r.kind);
      const end = spans && r.end_time ? r.end_time : null;
      return {
        label: (r.label || "").trim(),
        kind: r.kind,
        start_time: r.start_time || "",
        end_time: end,
        ring_start: !!r.ring_start,
        ring_end: !!(end && r.ring_end),
        sound_start: r.sound_start || "",
        sound_end: end ? (r.sound_end || "") : "",
        zone_ids: r.zone_ids && r.zone_ids.length ? r.zone_ids : null,
        enabled: !!r.enabled,
      };
    });
  }

  const validateSoon = debounce(async () => {
    const seq = ++valSeq;
    try {
      const res = await api("/api/bells/timetables/validate", { method: "POST", body: { events: payloadRows() } });
      if (seq !== valSeq) return;
      validation = { ok: !!res.ok, error: res.error || "", warnings: res.warnings || [] };
    } catch {
      if (seq !== valSeq) return;
      validation = { ok: true, error: "", warnings: [] };      // the server checks again on save
    }
    renderAlerts();
  }, 500);

  async function save() {
    const nameEl = $("#tt-name", root);
    const name = nameEl.value.trim();
    nameEl.classList.toggle("is-invalid", !name);
    if (!name) { nameEl.focus(); throw new Error(t("Enter a timetable name.")); }
    const body = { name, description: $("#tt-desc", root).value.trim(), events: payloadRows() };
    valSeq += 1;                                                 // ignore validation replies still in flight
    try {
      const res = await api(`/api/bells/timetables/${encodeURIComponent(id)}`, { method: "PUT", body });
      tt = res;
      setModel(res);
      $("#tt-name", root).value = res.name;
      $("#tt-desc", root).value = res.description || "";
      const h1 = $(".page-title", root);
      if (h1) h1.textContent = res.name;
      document.title = `${res.name} · ${t("Adhan & Bell Pager")}`;
      setHTML($("[data-usage]", root), usageCard());
      renderRows();
      renderAlerts();
      refreshSummaryOnly();
      toast(t("Timetable “{name}” saved.", { name: res.name }));
    } catch (err) {
      if (err.status === 400) {
        validation = { ok: false, error: err.message, warnings: validation.warnings };
        renderAlerts();
      }
      throw err;
    }
  }

  function refreshSummaryOnly() {
    const s = $("[data-summary]", root);
    if (s) s.textContent = summary();
  }

  function discard() {
    setModel(tt);
    $("#tt-name", root).value = tt.name;
    $("#tt-name", root).classList.remove("is-invalid");
    $("#tt-desc", root).value = tt.description || "";
    renderRows();
    renderAlerts();
    refreshSummaryOnly();
  }

  function changed() {
    tracker?.set(true);
    refreshLive();
  }

  // ------------------------------------------------------------------ row operations
  const rowByKey = (k) => rows.find((r) => r._k === k);

  function addRow(kind) {
    const ends = rows.map((r) => toMin(SPANS.has(r.kind) && r.end_time ? r.end_time : r.start_time)).filter((m) => m !== null);
    const start = Math.min(ends.length ? Math.max(...ends) : 8 * 60, 23 * 60 + 58);
    const length = kind === "break" ? 20 : 45;
    const r = {
      _k: newKey(), _open: false, id: null, kind, label: defaultLabel(kind, rows), start_time: fromMin(start),
      end_time: SPANS.has(kind) ? fromMin(Math.min(start + length, 23 * 60 + 59)) : null,
      ring_start: true, ring_end: SPANS.has(kind), sound_start: "", sound_end: "", zone_ids: null, enabled: true,
    };
    rows.push(r);
    rows = sortRows(rows);
    renderRows({ focusKey: r._k, focusField: "label" });
    const input = $(`[data-k="${r._k}"][data-f="label"]`, root);
    input?.scrollIntoView({ block: "center", behavior: "smooth" });
    input?.select();
    changed();
  }

  function updateField(el) {
    const r = rowByKey(el.dataset.k);
    if (!r) return false;
    const f = el.dataset.f;
    if (f === "label" || f === "start_time" || f === "end_time") {
      r[f] = el.value;
      if (f === "end_time") {
        const ringEnd = $(`[data-k="${r._k}"][data-f="ring_end"]`, root);
        if (ringEnd) ringEnd.disabled = !el.value;
      }
    } else if (f === "ring_start" || f === "ring_end" || f === "enabled") {
      r[f] = el.checked;
    } else if (f === "sound_start" || f === "sound_end") {
      r[f] = el.value;
    } else if (f === "zone") {
      const set = new Set(r.zone_ids || []);
      if (el.checked) set.add(el.value); else set.delete(el.value);
      r.zone_ids = zones.map((z) => z.id).filter((z) => set.has(z));
      if (!r.zone_ids.length) r.zone_ids = null;
    } else if (f === "kind") {
      const oldKind = r.kind;
      const oldDefault = defaultLabel(oldKind, rows.filter((x) => x !== r && x.kind === oldKind && rows.indexOf(x) < rows.indexOf(r)));
      r.kind = el.value;
      if (!r.label.trim() || r.label === oldDefault || r.label === t("Bell") || KINDS.some((k) => k.label() === r.label)) {
        r.label = defaultLabel(r.kind, rows.filter((x) => x !== r && rows.indexOf(x) < rows.indexOf(r)));
      }
      if (SPANS.has(r.kind) && !r.end_time && toMin(r.start_time) !== null) {
        r.end_time = fromMin(Math.min(toMin(r.start_time) + (r.kind === "break" ? 20 : 45), 23 * 60 + 59));
        r.ring_end = true;
      }
      return true;                                                // structure changed: re-render
    }
    if (f === "sound_start" || f === "sound_end" || f === "zone") {
      const custom = !!(r.sound_start || r.sound_end || (r.zone_ids && r.zone_ids.length));
      $$(`[data-action=more][data-k="${r._k}"]`, root).forEach((b) => b.classList.toggle("text-primary", custom));
    }
    return f === "enabled";
  }

  // ------------------------------------------------------------------ generator
  async function generateDialog() {
    const existing = rows.length;
    const firstStart = rows.find((r) => r.kind === "period")?.start_time || "07:45";
    const body = html`
      <p class="text-secondary">${t("Enter how your day is built. The rows are added to the table, where you can still change any time before saving.")}</p>
      <div class="row">
        <div class="col-sm-6 mb-3">
          <label class="form-label form-required" for="g-start">${t("First period starts at")}</label>
          <input id="g-start" class="form-control tabular" ${raw(TIME_INPUT_ATTRS)} name="start" value="${firstStart}" required>
        </div>
        <div class="col-sm-6 mb-3">
          <label class="form-label form-required" for="g-count">${t("Number of periods")}</label>
          <input id="g-count" class="form-control" type="number" name="count" min="1" max="20" value="7" data-type="int" required>
        </div>
        <div class="col-sm-6 mb-3">
          <label class="form-label form-required" for="g-len">${t("Period length (minutes)")}</label>
          <input id="g-len" class="form-control" type="number" name="period_length" min="5" max="240" value="45" data-type="int" required>
        </div>
        <div class="col-sm-6 mb-3">
          <label class="form-label" for="g-gap">${t("Minutes between periods")}</label>
          <input id="g-gap" class="form-control" type="number" name="gap" min="0" max="60" value="0" data-type="int">
          <div class="form-hint">${t("Time to change rooms. Use 0 if the next period starts straight away.")}</div>
        </div>
        <div class="col-sm-6 mb-3">
          <label class="form-label" for="g-break-after">${t("Break after period")}</label>
          <select id="g-break-after" class="form-select" name="break_after" data-type="int"></select>
        </div>
        <div class="col-sm-6 mb-3">
          <label class="form-label" for="g-break-len">${t("Break length (minutes)")}</label>
          <input id="g-break-len" class="form-control" type="number" name="break_length" min="5" max="120" value="20" data-type="int">
        </div>
      </div>
      <label class="form-check mb-2"><input class="form-check-input" type="checkbox" name="ring_end" checked>
        <span class="form-check-label">${t("Ring at the end of each period")}</span></label>
      <div class="form-hint ms-4 mb-3">${t("When a period ends at the same minute the next one starts, the bell rings only once.")}</div>
      <div class="row align-items-end">
        <div class="col-sm-6 mb-3">
          <label class="form-check mb-2"><input class="form-check-input" type="checkbox" name="with_sod">
            <span class="form-check-label">${t("Add a start-of-day bell")}</span></label>
          <input class="form-control tabular" ${raw(TIME_INPUT_ATTRS)} name="sod" value="07:30" aria-label="${t("Start-of-day bell time")}" disabled>
        </div>
        <div class="col-sm-6 mb-3">
          <label class="form-check mb-2"><input class="form-check-input" type="checkbox" name="with_eod">
            <span class="form-check-label">${t("Add an end-of-day bell when the last period ends")}</span></label>
        </div>
      </div>
      ${existing ? html`<div class="mb-3">
        <div class="form-label">${tn(existing, "The table already has {n} row", "The table already has {n} rows")}</div>
        <label class="form-check"><input class="form-check-input" type="radio" name="mode" value="replace" checked>
          <span class="form-check-label">${t("Replace them with the generated rows")}</span></label>
        <label class="form-check"><input class="form-check-input" type="radio" name="mode" value="append">
          <span class="form-check-label">${t("Keep them and add the generated rows")}</span></label>
      </div>` : ""}
      <div class="alert alert-info mb-0" data-preview role="status"></div>`;
    const result = await formDialog({
      title: t("Generate periods"),
      submitLabel: raw(`${icon("wand").s} ${esc(t("Add these rows"))}`),
      size: "lg",
      body,
      onMount: (d, form) => {
        const fillBreaks = () => {
          const count = Math.max(1, Math.min(20, parseInt(form.count.value, 10) || 1));
          const cur = form.break_after.value || "0";
          const opts = [html`<option value="0">${t("No break")}</option>`];
          for (let i = 1; i < count; i += 1) opts.push(html`<option value="${i}">${t("After period {n}", { n: i })}</option>`);
          setHTML(form.break_after, html`${opts}`);
          form.break_after.value = Number(cur) < count ? cur : "0";
          form.break_length.disabled = form.break_after.value === "0";
        };
        const preview = () => {
          const box = form.querySelector("[data-preview]");
          form.sod.disabled = !form.with_sod.checked;
          form.break_length.disabled = form.break_after.value === "0";
          try {
            const gen = generate(readGen(form));
            const periods = gen.filter((r) => r.kind === "period");
            const endMin = Math.max(...gen.map((r) => toMin(r.end_time || r.start_time)));
            box.className = "alert alert-info mb-0";
            setHTML(box, html`${icon("info-circle")} ${tn(periods.length, "{n} period from {from} to {to}", "{n} periods from {from} to {to}", {
              from: periods[0].start_time, to: fromMin(endMin) })} · ${t("school day of {length}", { length: fmtDuration((endMin - toMin(gen[0].start_time)) * 60) })}`);
          } catch (err) {
            box.className = "alert alert-warning mb-0";
            setHTML(box, html`${icon("alert-triangle")} ${err.message}`);
          }
        };
        form.count.addEventListener("input", fillBreaks);
        form.addEventListener("input", preview);
        form.addEventListener("change", preview);
        fillBreaks();
        preview();
      },
      onSubmit: (v, form) => ({ rows: generate(readGen(form)), mode: v.mode || "replace" }),
    });
    if (!result) return;
    if (result.mode === "append") rows = sortRows([...rows, ...result.rows]);
    else rows = result.rows;
    renderRows();
    changed();
    toast(tn(result.rows.length, "{n} row added. Check the times, then save.", "{n} rows added. Check the times, then save."), "info");
  }

  function readGen(form) {
    const int = (el, def) => { const n = parseInt(el.value, 10); return Number.isFinite(n) ? n : def; };
    return {
      start: form.start.value, count: int(form.count, 0), length: int(form.period_length, 0), gap: int(form.gap, 0),
      breakAfter: int(form.break_after, 0), breakLength: int(form.break_length, 0), ringEnd: form.ring_end.checked,
      sod: form.with_sod.checked ? form.sod.value : null, eod: form.with_eod.checked,
    };
  }

  function generate(o) {
    const start = toMin(o.start);
    if (start === null) throw new Error(t("Enter the time the first period starts."));
    if (o.count < 1 || o.count > 20) throw new Error(t("Choose between 1 and 20 periods."));
    if (o.length < 5 || o.length > 240) throw new Error(t("A period must be between 5 and 240 minutes."));
    if (o.gap < 0 || o.gap > 60) throw new Error(t("The time between periods must be between 0 and 60 minutes."));
    if (o.breakAfter && (o.breakLength < 5 || o.breakLength > 120)) throw new Error(t("A break must be between 5 and 120 minutes."));
    const out = [];
    const mk = (kind, label, s, e, extra = {}) => ({
      _k: newKey(), _open: false, id: null, kind, label, start_time: fromMin(s), end_time: e === null ? null : fromMin(e),
      ring_start: true, ring_end: false, sound_start: "", sound_end: "", zone_ids: null, enabled: true, ...extra,
    });
    if (o.sod) {
      const sod = toMin(o.sod);
      if (sod === null) throw new Error(t("Enter the time of the start-of-day bell."));
      if (sod >= start) throw new Error(t("The start-of-day bell must be before the first period."));
      out.push(mk("start_of_day", t("Start of day"), sod, null));
    }
    let now = start;
    for (let i = 1; i <= o.count; i += 1) {
      const end = now + o.length;
      if (end > 23 * 60 + 59) throw new Error(t("The periods would run past midnight. Use fewer or shorter periods."));
      out.push(mk("period", t("Period {n}", { n: i }), now, end, { ring_end: o.ringEnd }));
      now = end;
      if (i < o.count && o.breakAfter === i) {
        const bEnd = now + o.breakLength;
        if (bEnd > 23 * 60 + 59) throw new Error(t("The periods would run past midnight. Use fewer or shorter periods."));
        out.push(mk("break", t("Break"), now, bEnd, { ring_end: true }));
        now = bEnd;
      } else if (i < o.count) {
        now += o.gap;
      }
    }
    if (o.eod) out.push(mk("end_of_day", t("End of day"), now, null));
    return out;
  }

  function esc(s) { return html`${s}`.s; }

  // ------------------------------------------------------------------ events (bound once)
  if (canEdit) {
    on(root, "input", "input[data-f=label], input[data-f=start_time], input[data-f=end_time]", (e, el) => {
      updateField(el);
      if (el.dataset.f === "label") el.classList.remove("is-invalid");
      refreshLive();
    });
    on(root, "change", "[data-f]", (e, el) => {
      if (el.dataset.f === "label" || el.dataset.f === "start_time" || el.dataset.f === "end_time") return;
      const rerender = updateField(el);
      if (rerender) renderRows();
      refreshLive();
    });
    on(root, "input", "#tt-name", (e, el) => el.classList.remove("is-invalid"));
    on(root, "click", "[data-action=add]", (e, el) => addRow(el.dataset.kind));
    on(root, "click", "[data-action=sort]", () => { rows = sortRows(rows); renderRows(); changed(); $("[data-action=add]", root)?.focus(); });
    on(root, "click", "[data-action=generate]", () => generateDialog().catch(toastError));
    on(root, "click", "[data-action=more]", (e, el) => {
      const r = rowByKey(el.dataset.k);
      if (!r) return;
      r._open = !r._open;
      renderRows({ focusKey: r._k, focusField: "action:more" });
    });
    on(root, "click", "[data-action=dup-row]", (e, el) => {
      const idx = rows.findIndex((r) => r._k === el.dataset.k);
      if (idx < 0) return;
      const copy = { ...rows[idx], _k: newKey(), id: null, _open: false, zone_ids: rows[idx].zone_ids ? [...rows[idx].zone_ids] : null };
      rows.splice(idx + 1, 0, copy);
      renderRows({ focusKey: copy._k, focusField: "label" });
      changed();
    });
    on(root, "click", "[data-action=del-row]", (e, el) => {
      const idx = rows.findIndex((r) => r._k === el.dataset.k);
      if (idx < 0) return;
      const [removed] = rows.splice(idx, 1);
      const next = rows[Math.min(idx, rows.length - 1)];
      renderRows(next ? { focusKey: next._k, focusField: "action:del-row" } : {});
      if (!next) $("[data-action=add]", root)?.focus();
      changed();
      toast(t("Row “{name}” removed. Use Discard to undo all unsaved changes.", { name: removed.label || "—" }), "info");
    });
    // Leaving through a link on this page with unsaved changes asks first.
    on(root, "click", "a[href^='#/']", async (e, el) => {
      if (!tracker?.isDirty()) return;
      e.preventDefault();
      const ok = await confirmDialog({
        title: t("Leave without saving?"),
        message: t("Your changes to this timetable will be lost."),
        confirmLabel: t("Leave without saving"),
        danger: true,
      });
      if (ok) { tracker.set(false); navigate(el.getAttribute("href").slice(1)); }
    });
    const beforeUnload = (e) => { if (tracker?.isDirty()) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", beforeUnload);
    cleanups.push(() => window.removeEventListener("beforeunload", beforeUnload));
  }
  on(root, "click", "[data-action=retry]", () => { setHTML(root, skeleton(6)); load(); });

  const onMedia = () => { if (tt && canEdit && $("[data-rows]", root)) renderRows(); };
  mq.addEventListener("change", onMedia);
  cleanups.push(() => mq.removeEventListener("change", onMedia));

  await load();
  if (tt && canEdit && ctx.query.generate === "1") {
    history.replaceState(null, "", `#/bells/timetables/${encodeURIComponent(id)}`);
    generateDialog().catch(toastError);
  }

  return () => cleanups.forEach((fn) => fn());
}
