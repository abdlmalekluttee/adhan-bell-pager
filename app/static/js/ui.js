// UI building blocks on top of Tabler: safe templating, dialogs, toasts, states, badges, forms.

import { ApiError } from "./api.js";
import { fmtDateTime, fmtDuration, t } from "./i18n.js";

// --------------------------------------------------------------------------
// Safe templating. Interpolated values are escaped unless wrapped in raw() or
// produced by another html`` call. Arrays are joined.
// --------------------------------------------------------------------------
export class Raw {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
export const raw = (s) => new Raw(String(s ?? ""));

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function part(v) {
  if (v === null || v === undefined || v === false || v === true) return "";
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(part).join("");
  return esc(v);
}

export function html(strings, ...values) {
  let out = "";
  strings.forEach((s, i) => { out += s; if (i < values.length) out += part(values[i]); });
  return new Raw(out);
}

export function setHTML(el, content) { el.innerHTML = content instanceof Raw ? content.s : esc(content); }

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

/** Delegated events: on(root, "click", "[data-action=save]", (e, el) => ...) */
export function on(root, type, selector, handler) {
  const fn = (e) => {
    const el = e.target.closest(selector);
    if (el && root.contains(el)) handler(e, el);
  };
  root.addEventListener(type, fn);
  return () => root.removeEventListener(type, fn);
}

export const icon = (name, cls = "") => raw(`<i class="ti ti-${esc(name)} ${esc(cls)}" aria-hidden="true"></i>`);

// --------------------------------------------------------------------------
// Page scaffolding
// --------------------------------------------------------------------------
export function pageHeader({ title, description = "", actions = "" }) {
  return html`
    <div class="page-header d-print-none">
      <div class="row g-3 align-items-center">
        <div class="col">
          <h1 class="page-title">${title}</h1>
          ${description ? html`<p class="page-description mb-0">${description}</p>` : ""}
        </div>
        ${actions ? html`<div class="col-12 col-md-auto ms-md-auto"><div class="btn-list">${actions}</div></div>` : ""}
      </div>
    </div>`;
}

export function skeleton(lines = 4) {
  return html`<div class="card"><div class="card-body" aria-busy="true" aria-label="${t("Loading")}">
    ${Array.from({ length: lines }, (_, i) => html`<div class="skeleton-line" style="width:${90 - (i % 3) * 18}%"></div>`)}
  </div></div>`;
}

export function emptyState({ iconName = "mood-empty", title, text = "", action = "" }) {
  return html`<div class="empty">
    <div class="empty-icon">${icon(iconName)}</div>
    <p class="empty-title">${title}</p>
    ${text ? html`<p class="empty-subtitle text-secondary">${text}</p>` : ""}
    ${action ? html`<div class="empty-action">${action}</div>` : ""}
  </div>`;
}

export function errorState(err, { retry = true, bare = false } = {}) {
  const msg = errorMessage(err);
  const inner = html`<div class="empty">
    <div class="empty-icon">${icon("alert-triangle", "text-danger")}</div>
    <p class="empty-title">${t("This could not be loaded")}</p>
    <p class="empty-subtitle text-secondary">${msg}</p>
    ${retry ? html`<div class="empty-action"><button class="btn btn-primary" data-action="retry">${icon("refresh")} ${t("Try again")}</button></div>` : ""}
  </div>`;
  return bare ? inner : html`<div class="card">${inner}</div>`;
}

export function errorMessage(err) {
  if (!err) return t("Something went wrong.");
  if (err instanceof TypeError || err instanceof ReferenceError || err instanceof SyntaxError) {
    console.error(err);
    return t("This page could not load. Reload to try again.");
  }
  if (err instanceof ApiError) {
    if (err.status === 403 && err.code === "forbidden") return t("You do not have permission to do this.");
    return t(err.message);
  }
  return t(err.message || String(err));
}

export function alertBox(type, text, { title = "", iconName } = {}) {
  const icons = { success: "circle-check", danger: "alert-circle", warning: "alert-triangle", info: "info-circle" };
  return html`<div class="alert alert-${type}" role="${type === "danger" ? "alert" : "status"}">
    <div class="d-flex gap-2">
      <div>${icon(iconName || icons[type] || "info-circle", "alert-icon")}</div>
      <div>${title ? html`<h4 class="alert-title">${title}</h4>` : ""}<div>${text}</div></div>
    </div></div>`;
}

// --------------------------------------------------------------------------
// Toasts
// --------------------------------------------------------------------------
export function toast(message, type = "success", { timeout } = {}) {
  const stack = document.getElementById("toasts");
  if (!stack) return;
  const icons = { success: "circle-check", danger: "alert-circle", warning: "alert-triangle", info: "info-circle" };
  const el = document.createElement("div");
  el.className = `alert alert-${type} alert-dismissible bg-body`;
  el.setAttribute("role", type === "danger" ? "alert" : "status");
  el.innerHTML = html`<div class="d-flex gap-2 align-items-start">${icon(icons[type] || "info-circle", "alert-icon")}
    <div class="flex-fill">${message}</div></div>
    <button type="button" class="btn-close" aria-label="${t("Close")}"></button>`.s;
  el.querySelector(".btn-close").addEventListener("click", () => el.remove());
  stack.appendChild(el);
  setTimeout(() => el.remove(), timeout ?? (type === "danger" ? 9000 : 4500));
}

export function toastError(err, prefix = "") {
  toast(prefix ? `${prefix}: ${errorMessage(err)}` : errorMessage(err), "danger");
}

// --------------------------------------------------------------------------
// Dialogs (native <dialog>, Tabler modal styling)
// --------------------------------------------------------------------------
function makeDialog(size = "") {
  const d = document.createElement("dialog");
  d.className = `abp-dialog ${size}`;
  const module = document.querySelector(".page-wrapper")?.dataset.module;
  if (module) d.dataset.module = module;          // keep the module accent colour inside dialogs
  document.body.appendChild(d);
  d.addEventListener("close", () => setTimeout(() => d.remove(), 250));
  return d;
}

/** Resolves true when confirmed. */
export function confirmDialog({ title, message, confirmLabel = t("Confirm"), danger = false, details = "" }) {
  return new Promise((resolve) => {
    const d = makeDialog();
    d.innerHTML = html`<form method="dialog" class="modal-content">
      ${danger ? html`<div class="modal-status bg-danger"></div>` : ""}
      <div class="modal-body py-4">
        <div class="d-flex gap-3">
          <div>${icon(danger ? "alert-triangle" : "help-circle", `fs-1 ${danger ? "text-danger" : "text-secondary"}`)}</div>
          <div>
            <h3 class="mb-2">${title}</h3>
            <div class="text-secondary">${message}</div>
            ${details ? html`<div class="mt-2">${details}</div>` : ""}
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button value="cancel" class="btn btn-link link-secondary me-auto">${t("Cancel")}</button>
        <button value="ok" class="btn ${danger ? "btn-danger" : "btn-primary"}" autofocus>${confirmLabel}</button>
      </div></form>`.s;
    d.addEventListener("close", () => resolve(d.returnValue === "ok"));
    d.showModal();
  });
}

/**
 * formDialog({ title, body: html`...fields...`, submitLabel, size, onSubmit: async (values, form) => result })
 * Shows validation/server errors inside the dialog; resolves with onSubmit's result, or null if cancelled.
 */
export function formDialog({ title, body, submitLabel = t("Save"), size = "", onSubmit, onMount, danger = false }) {
  return new Promise((resolve) => {
    const d = makeDialog(size);
    let result = null;
    d.innerHTML = html`<form class="modal-content" novalidate>
      <div class="modal-header"><h2 class="modal-title h3">${title}</h2>
        <button type="button" class="btn-close" data-dismiss aria-label="${t("Close")}"></button></div>
      <div class="modal-body">
        <div class="dialog-error" hidden></div>
        ${body}
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-link link-secondary me-auto" data-dismiss>${t("Cancel")}</button>
        <button type="submit" class="btn ${danger ? "btn-danger" : "btn-primary"}">${submitLabel}</button>
      </div></form>`.s;
    const form = d.querySelector("form");
    const errBox = d.querySelector(".dialog-error");
    d.querySelectorAll("[data-dismiss]").forEach((b) => b.addEventListener("click", () => d.close()));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      errBox.hidden = true;
      if (!form.checkValidity()) { form.classList.add("was-validated"); form.querySelector(":invalid")?.focus(); return; }
      const btn = form.querySelector("button[type=submit]");
      try {
        result = await withBusy(btn, () => onSubmit(formValues(form), form, d));
        if (result !== false) d.close();
      } catch (err) {
        errBox.innerHTML = alertBox("danger", errorMessage(err)).s;
        errBox.hidden = false;
        errBox.scrollIntoView({ block: "nearest" });
      }
    });
    d.addEventListener("close", () => resolve(result === false ? null : result));
    d.showModal();
    if (onMount) onMount(d, form);
    const first = form.querySelector("input:not([type=hidden]):not([disabled]), select, textarea");
    if (first) first.focus();
  });
}

// --------------------------------------------------------------------------
// Forms
// --------------------------------------------------------------------------
/** Read named controls. data-type="number|int|bool|json|list" coerces; checkboxes give booleans;
 *  several checkboxes with the same name and data-type="list" give an array of checked values. */
export function formValues(form) {
  const out = {};
  const els = Array.from(form.elements).filter((el) => el.name && !el.disabled);
  for (const el of els) {
    const type = el.dataset.type || "";
    if (el.type === "checkbox" && type === "list") {
      out[el.name] = out[el.name] || [];
      if (el.checked) out[el.name].push(el.value);
      continue;
    }
    if (el.type === "radio") { if (el.checked) out[el.name] = coerce(el.value, type); continue; }
    if (el.type === "checkbox") { out[el.name] = el.checked; continue; }
    if (el.tagName === "SELECT" && el.multiple) { out[el.name] = Array.from(el.selectedOptions).map((o) => o.value); continue; }
    out[el.name] = coerce(el.value, type);
  }
  return out;
}

function coerce(v, type) {
  if (type === "number") return v === "" ? null : Number(v);
  if (type === "int") return v === "" ? null : parseInt(v, 10);
  if (type === "bool") return v === "true" || v === "1";
  if (type === "nullable") return v === "" ? null : v;
  if (type === "json") { try { return JSON.parse(v); } catch { return null; } }
  return v;
}

export async function withBusy(button, fn) {
  if (!button) return fn();
  const prev = button.innerHTML;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.innerHTML = `<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>${prev}`;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.innerHTML = prev;
  }
}

/** Track unsaved changes in a form; shows a sticky save bar. Returns controller. */
export function dirtyTracker(root, bar, { onSave, onDiscard }) {
  let dirty = false;
  const set = (v) => { dirty = v; bar.hidden = !v; };
  const mark = (e) => { if (!e.target.closest("[data-no-dirty]")) set(true); };
  root.addEventListener("input", mark);
  root.addEventListener("change", mark);
  bar.querySelector("[data-action=save]")?.addEventListener("click", async (e) => {
    try { await withBusy(e.currentTarget, onSave); set(false); } catch (err) { toastError(err); }
  });
  bar.querySelector("[data-action=discard]")?.addEventListener("click", () => { set(false); onDiscard?.(); });
  set(false);
  return { isDirty: () => dirty, set };
}

export function saveBar() {
  return html`<div class="save-bar" hidden role="region" aria-label="${t("Unsaved changes")}">
    <span class="msg">${icon("point-filled", "text-warning")} ${t("You have unsaved changes")}</span>
    <button type="button" class="btn btn-link link-secondary" data-action="discard">${t("Discard")}</button>
    <button type="button" class="btn btn-primary" data-action="save">${icon("device-floppy")} ${t("Save changes")}</button>
  </div>`;
}

export function field({ label, name, value = "", type = "text", hint = "", required = false, attrs = "", cls = "", dataType = "", placeholder = "", ltr = false }) {
  const id = `f-${name}-${Math.random().toString(36).slice(2, 7)}`;
  return html`<div class="mb-3 ${cls}">
    <label class="form-label ${required ? "form-required" : ""}" for="${id}">${label}</label>
    <input id="${id}" class="form-control ${ltr ? "ltr" : ""}" type="${type}" name="${name}" value="${value ?? ""}"
      ${required ? raw("required") : ""} ${dataType ? raw(`data-type="${esc(dataType)}"`) : ""}
      ${placeholder ? raw(`placeholder="${esc(placeholder)}"`) : ""} ${raw(attrs)}>
    ${hint ? html`<div class="form-hint">${hint}</div>` : ""}
    <div class="invalid-feedback">${t("Please check this field.")}</div>
  </div>`;
}

export function selectField({ label, name, value = "", options = [], hint = "", required = false, cls = "", dataType = "", attrs = "" }) {
  const id = `f-${name}-${Math.random().toString(36).slice(2, 7)}`;
  return html`<div class="mb-3 ${cls}">
    <label class="form-label ${required ? "form-required" : ""}" for="${id}">${label}</label>
    <select id="${id}" class="form-select" name="${name}" ${required ? raw("required") : ""} ${dataType ? raw(`data-type="${esc(dataType)}"`) : ""} ${raw(attrs)}>
      ${options.map((o) => html`<option value="${o.value}" ${String(o.value) === String(value ?? "") ? raw("selected") : ""} ${o.disabled ? raw("disabled") : ""}>${o.label}</option>`)}
    </select>
    ${hint ? html`<div class="form-hint">${hint}</div>` : ""}
  </div>`;
}

export function switchField({ label, name, checked = false, hint = "", cls = "", attrs = "" }) {
  return html`<div class="mb-3 ${cls}"><label class="form-check form-switch mb-0">
    <input class="form-check-input" type="checkbox" name="${name}" ${checked ? raw("checked") : ""} ${raw(attrs)}>
    <span class="form-check-label">${label}</span></label>
    ${hint ? html`<div class="form-hint ms-5">${hint}</div>` : ""}</div>`;
}

// --------------------------------------------------------------------------
// Status vocabulary
// --------------------------------------------------------------------------
export const EXEC_STATUS = {
  queued: { label: "Waiting", cls: "bg-blue-lt", icon: "clock" },
  running: { label: "Playing", cls: "bg-blue text-blue-fg", icon: "volume" },
  success: { label: "Played", cls: "bg-green-lt", icon: "check" },
  partial: { label: "Partly played", cls: "bg-yellow-lt", icon: "alert-triangle" },
  failed: { label: "Failed", cls: "bg-red-lt", icon: "x" },
  cancelled: { label: "Stopped", cls: "bg-secondary-lt", icon: "player-stop" },
  interrupted: { label: "Interrupted", cls: "bg-orange-lt", icon: "cut" },
  skipped: { label: "Skipped", cls: "bg-yellow-lt", icon: "player-skip-forward" },
  missed: { label: "Missed", cls: "bg-red-lt", icon: "clock-off" },
};

export function statusBadge(status) {
  const s = EXEC_STATUS[status] || { label: status, cls: "bg-secondary-lt", icon: "point" };
  return html`<span class="badge ${s.cls}">${icon(s.icon)} ${t(s.label)}</span>`;
}

export const OUTCOME_LABEL = {
  played: "Played to the end", stopped: "Stopped", interrupted: "Interrupted", remote_hangup: "Hung up early",
  no_answer: "No answer", busy: "Busy", rejected: "Rejected by PBX", failed: "Failed", no_media: "No audio path",
  timeout: "Too long, ended",
};

/** Per-destination details of one execution (history rows, dashboard). */
export function resultsTable(results) {
  if (!results || !results.length) return html`<p class="text-secondary mb-0">${t("No call details.")}</p>`;
  return html`<div class="table-responsive"><table class="table table-sm mb-0">
    <thead><tr><th>${t("Destination")}</th><th>${t("Result")}</th><th>${t("Codec")}</th><th>${t("Played")}</th><th>${t("Audio sent")}</th></tr></thead>
    <tbody>${results.map((r) => html`<tr>
      <td class="ltr">${r.uri}</td>
      <td>${t(OUTCOME_LABEL[r.outcome] || r.outcome)}${r.sip_code && r.outcome !== "played" ? html` <span class="text-secondary">(${r.sip_code})</span>` : ""}</td>
      <td class="ltr">${r.codec || "—"}</td>
      <td class="num">${r.played_seconds ? fmtDuration(r.played_seconds) : "—"}</td>
      <td class="num">${r.rtp && r.rtp.tx_packets ? t("{n} packets", { n: r.rtp.tx_packets }) : "—"}</td>
    </tr>`)}</tbody></table></div>
    <p class="form-hint mt-2 mb-0">${t("“Audio sent” confirms the audio stream left this server. Whether it was heard depends on the speaker or phone.")}</p>`;
}

export function destState(s) {
  return { waiting: "waiting", calling: "ringing", connected: "answered", playing: "playing", played: "done",
    no_answer: "no answer", busy: "busy", rejected: "rejected", failed: "failed", stopped: "stopped",
    interrupted: "interrupted", remote_hangup: "hung up", no_media: "no audio", timeout: "ended" }[s] || s;
}

const PRAYER_KEYS = { Fajr: "fajr", Dhuhr: "dhuhr", Asr: "asr", Maghrib: "maghrib", Isha: "isha" };

export function prayerName(p) {
  return { fajr: t("Fajr"), dhuhr: t("Dhuhr"), asr: t("Asr"), maghrib: t("Maghrib"), isha: t("Isha"),
    sunrise: t("Sunrise") }[p] || p;
}

/** "Fajr Adhan" / "Fajr iqama" in the current language. */
export function occurrenceTitle(prayer, kind) {
  return kind === "iqama" ? t("{prayer} iqama", { prayer: prayerName(prayer) }) : t("{prayer} Adhan", { prayer: prayerName(prayer) });
}

/** A bell label from a timetable row: user text stays as typed, generated "End of …" is translated. */
export function bellLabel(label) {
  const m = String(label || "").match(/^End of (.+)$/);
  return m ? t("End of {name}", { name: m[1] }) : label;
}

/**
 * Translate a broadcast title generated by the server, including titles stored in the history:
 * "Fajr Adhan", "Test: Asr iqama", "Ring now: Classic school bell", "End of Period 1 · Period 2", "Play: file.wav".
 */
export function broadcastTitle(title) {
  const s = String(title || "");
  let m = s.match(/^(Fajr|Dhuhr|Asr|Maghrib|Isha) (Adhan|iqama)$/);
  if (m) return occurrenceTitle(PRAYER_KEYS[m[1]], m[2]);
  m = s.match(/^Test: (.+)$/);
  if (m) return t("Test: {title}", { title: broadcastTitle(m[1]) });
  m = s.match(/^Ring now: (.+)$/);
  if (m) return t("Ring now: {name}", { name: m[1] });
  m = s.match(/^Play: (.+)$/);
  if (m) return t("Play: {file}", { file: m[1] });
  if (s.includes(" · ") || s.startsWith("End of ")) return s.split(" · ").map(bellLabel).join(" · ");
  return t(s);
}

export function moduleChip(module) {
  const label = module === "adhan" ? t("Adhan") : module === "bells" ? t("School Bells") : t(module);
  return html`<span class="module-chip ${module}">${icon(module === "adhan" ? "building-mosque" : "bell")} ${label}</span>`;
}

export function whenText(ts) { return fmtDateTime(ts); }

/** Attributes for a 24-hour time text input (native type="time" follows the browser's 12/24-hour locale). */
export const TIME_INPUT_ATTRS = 'type="text" inputmode="numeric" pattern="([01][0-9]|2[0-3]):[0-5][0-9]" maxlength="5" placeholder="08:00" autocomplete="off" data-time24';

/** "8", "800", "0800", "8:0" → "08:00"; leaves anything else unchanged. */
export function normalizeTime(value) {
  const v = String(value || "").trim();
  let m = v.match(/^(\d{1,2})[:.h]?(\d{2})$/);
  if (!m) m = v.match(/^(\d{1,2})$/) ? [v, v, "00"] : null;
  if (!m) return v;
  const h = Number(m[1]), min = Number(m[2]);
  if (h > 23 || min > 59) return v;
  return `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
}

export function debounce(fn, ms = 300) {
  let h;
  return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); };
}
