// History (Adhan or School Bells): every broadcast attempt with its result, filters and per-speaker details.

import { api } from "../api.js";
import { fmtDateTime, fmtNumber, t } from "../i18n.js";
import {
  $, EXEC_STATUS, emptyState, errorMessage, html, icon, on, pageHeader, raw, resultsTable, setHTML, skeleton, statusBadge,
} from "../ui.js";
import { broadcastTitle } from "./adhan-today.js";

const LIMIT = 50;

function statusLabel(key) {
  return {
    queued: t("Waiting"), running: t("Playing"), success: t("Played"), partial: t("Partly played"), failed: t("Failed"),
    cancelled: t("Stopped"), interrupted: t("Interrupted"), skipped: t("Skipped"), missed: t("Missed"),
  }[key] || key;
}

function isoDate(d) { return d.toISOString().slice(0, 10); }
function shiftDate(iso, days) { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return isoDate(d); }
const validDate = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v || "");

export default async function historyPage(ctx) {
  const { root } = ctx;
  const module = ctx.props.module === "bells" ? "bells" : "adhan";
  const filters = {
    status: EXEC_STATUS[ctx.query.status] ? ctx.query.status : "",
    problems: ctx.query.problems === "true" || ctx.query.problems === "1",
    from: validDate(ctx.query.from) ? ctx.query.from : "",
    to: validDate(ctx.query.to) ? ctx.query.to : "",
  };
  let offset = Math.max(0, parseInt(ctx.query.offset, 10) || 0);
  let result = null;
  let seq = 0;
  const open = new Set();

  setHTML(root, html`
    ${pageHeader({
      title: module === "adhan" ? t("Adhan history") : t("Bell history"),
      description: module === "adhan"
        ? t("Every Adhan, iqama and test, with what happened. “Played” means every speaker got the audio. “Partly played”, “Failed”, “Skipped” and “Missed” need a look.")
        : t("Every bell, “Ring now” and test, with what happened. “Played” means every speaker got the audio. “Partly played”, “Failed”, “Skipped” and “Missed” need a look."),
    })}
    <div class="card">
      <div class="card-body border-bottom" data-filters>${filtersForm()}</div>
      <div data-results>${skeleton(6)}</div>
      <div class="card-footer" data-pager hidden></div>
    </div>`);
  const results = $("[data-results]", root);
  const pager = $("[data-pager]", root);

  function filtersForm() {
    return html`<div class="row g-3 align-items-end">
      <div class="col-sm-6 col-lg-3">
        <label class="form-label" for="h-status">${t("Status")}</label>
        <select id="h-status" class="form-select" data-filter="status">
          <option value="" ${filters.status ? "" : raw("selected")}>${t("All statuses")}</option>
          ${Object.keys(EXEC_STATUS).map((k) => html`<option value="${k}" ${filters.status === k ? raw("selected") : ""}>${statusLabel(k)}</option>`)}
        </select>
      </div>
      <div class="col-6 col-sm-3 col-lg-2">
        <label class="form-label" for="h-from">${t("From")}</label>
        <input id="h-from" class="form-control" type="date" data-filter="from" value="${filters.from}">
      </div>
      <div class="col-6 col-sm-3 col-lg-2">
        <label class="form-label" for="h-to">${t("To")}</label>
        <input id="h-to" class="form-control" type="date" data-filter="to" value="${filters.to}">
      </div>
      <div class="col-sm-auto">
        <label class="form-check form-switch mb-2">
          <input class="form-check-input" type="checkbox" data-filter="problems" ${filters.problems ? raw("checked") : ""}>
          <span class="form-check-label">${t("Problems only")}</span>
        </label>
      </div>
      <div class="col-sm-auto ms-sm-auto">
        <button type="button" class="btn btn-link link-secondary mb-1" data-action="clear" ${hasFilters() ? "" : raw("hidden")}>${icon("x")} ${t("Clear filters")}</button>
      </div>
    </div>
    <div class="small text-danger mt-2" data-range-error hidden>${t("The “From” date is after the “To” date.")}</div>`;
  }

  function hasFilters() { return !!(filters.status || filters.problems || filters.from || filters.to); }

  function syncUrl() {
    const q = new URLSearchParams();
    if (filters.status) q.set("status", filters.status);
    if (filters.problems) q.set("problems", "true");
    if (filters.from) q.set("from", filters.from);
    if (filters.to) q.set("to", filters.to);
    if (offset) q.set("offset", String(offset));
    const s = q.toString();
    history.replaceState(null, "", `#/${module}/history${s ? "?" + s : ""}`);
  }

  async function load() {
    const my = ++seq;
    const rangeBad = filters.from && filters.to && filters.from > filters.to;
    const rangeErr = $("[data-range-error]", root);
    if (rangeErr) rangeErr.hidden = !rangeBad;
    const clear = $("[data-action=clear]", root);
    if (clear) clear.hidden = !hasFilters();
    if (rangeBad) return;
    results.setAttribute("aria-busy", "true");
    if (!result) setHTML(results, skeleton(6));
    else results.style.opacity = ".55";
    syncUrl();
    try {
      const r = await api(`/api/${module}/history`, {
        query: {
          status: filters.status,
          date_from: filters.from,
          date_to: filters.to ? shiftDate(filters.to, 1) : "",   // the server's upper bound is exclusive
          limit: LIMIT,
          offset,
          problems: filters.problems ? "true" : "",
        },
      });
      if (my !== seq) return;
      if (offset && !r.items.length && r.total) {   // page fell off the end (e.g. after filtering)
        offset = Math.max(0, Math.floor((r.total - 1) / LIMIT) * LIMIT);
        return load();
      }
      result = r;
      render();
    } catch (err) {
      if (my !== seq) return;
      setHTML(results, emptyState({
        iconName: "alert-triangle",
        title: t("This could not be loaded"),
        text: errorMessage(err),
        action: html`<button type="button" class="btn btn-primary" data-action="retry">${icon("refresh")} ${t("Try again")}</button>`,
      }));
      pager.hidden = true;
    } finally {
      if (my === seq) { results.removeAttribute("aria-busy"); results.style.opacity = ""; }
    }
  }

  function kindLabel(item) {
    if (item.kind === "manual") return t("Ring now");
    if (item.kind === "test") return t("Test");
    return t("Scheduled");
  }

  function render() {
    const items = result.items;
    if (!items.length) {
      setHTML(results, hasFilters()
        ? emptyState({
          iconName: "filter-off",
          title: t("Nothing matches these filters"),
          text: t("Try a wider date range or another status."),
          action: html`<button type="button" class="btn btn-outline-secondary" data-action="clear">${t("Clear filters")}</button>`,
        })
        : emptyState({
          iconName: "history",
          title: module === "adhan" ? t("No Adhan broadcasts yet") : t("No bells yet"),
          text: module === "adhan"
            ? t("Each Adhan, iqama and test appears here once it has played or been attempted.")
            : t("Each scheduled bell, “Ring now” and test appears here once it has played or been attempted."),
        }));
      pager.hidden = true;
      return;
    }
    setHTML(results, html`<div class="table-responsive">
      <table class="table card-table table-vcenter">
        <thead><tr>
          <th>${t("When")}</th><th>${t("What")}</th><th>${t("Status")}</th><th class="w-1"><span class="visually-hidden">${t("Details")}</span></th>
        </tr></thead>
        <tbody>${items.map(row)}</tbody>
      </table>
    </div>`);
    const first = result.total ? offset + 1 : 0;
    const last = offset + items.length;
    pager.hidden = false;
    setHTML(pager, html`<div class="d-flex flex-wrap align-items-center gap-2">
      <div class="text-secondary small me-auto tabular">${t("Showing {from}–{to} of {total}", { from: fmtNumber(first), to: fmtNumber(last), total: fmtNumber(result.total) })}</div>
      <div class="btn-group">
        <button type="button" class="btn btn-outline-secondary" data-action="page" data-dir="-1" ${offset > 0 ? "" : raw("disabled")}>
          ${icon("chevron-left", "flip-rtl")} ${t("Newer")}</button>
        <button type="button" class="btn btn-outline-secondary" data-action="page" data-dir="1" ${last < result.total ? "" : raw("disabled")}>
          ${t("Older")} ${icon("chevron-right", "flip-rtl")}</button>
      </div></div>`);
  }

  function row(item) {
    const expanded = open.has(item.id);
    const late = item.delay_seconds >= 1;
    return html`<tr>
      <td class="text-nowrap tabular">${fmtDateTime(item.scheduled_for || item.created_at)}</td>
      <td>
        <div class="fw-semibold">${broadcastTitle(item.title)}</div>
        <div class="small text-secondary">${kindLabel(item)}${(item.kind === "manual" || item.kind === "test") && item.actor ? html` · ${t("by {name}", { name: item.actor })}` : ""}</div>
      </td>
      <td>
        ${statusBadge(item.status)}
        ${late ? html`<div class="small text-secondary mt-1">${t("late by {n} s", { n: fmtNumber(Math.round(item.delay_seconds)) })}</div>` : ""}
      </td>
      <td>
        <button type="button" class="btn btn-sm btn-ghost-secondary text-nowrap" data-action="toggle" data-id="${item.id}"
          aria-expanded="${expanded ? "true" : "false"}" aria-controls="h-detail-${item.id}">
          ${t("Details")} ${icon(expanded ? "chevron-up" : "chevron-down")}</button>
      </td>
    </tr>
    ${expanded ? html`<tr id="h-detail-${item.id}" class="bg-body-secondary"><td colspan="4">${details(item)}</td></tr>` : ""}`;
  }

  function details(item) {
    const targets = Array.isArray(item.targets) ? item.targets : [];
    const dt = (label, value) => html`<div class="col-sm-6 col-lg-3"><div class="small text-secondary">${label}</div><div>${value}</div></div>`;
    return html`<div class="py-2">
      ${item.reason ? html`<p class="mb-3">${icon("info-circle", "text-secondary")} ${t(item.reason)}</p>` : ""}
      <div class="row g-3 mb-3">
        ${dt(t("Sound"), item.sound ? html`<span class="ltr text-break">${item.sound}</span>` : "—")}
        ${dt(t("Speakers called"), targets.length ? html`<span class="ltr text-break">${targets.map((x) => (typeof x === "string" ? x : x.uri || x.name || "")).join(", ")}</span>` : "—")}
        ${dt(t("Codec"), item.codec ? html`<span class="ltr">${item.codec}</span>` : "—")}
        ${dt(t("Started / finished"), html`<span class="tabular">${fmtDateTime(item.started_at)} / ${fmtDateTime(item.finished_at)}</span>`)}
      </div>
      ${resultsTable(item.results)}
    </div>`;
  }

  // ------------------------------------------------------------------------
  on(root, "change", "[data-filter]", (e, el) => {
    const key = el.dataset.filter;
    filters[key] = el.type === "checkbox" ? el.checked : el.value;
    offset = 0;
    open.clear();
    load();
  });
  on(root, "click", "[data-action=clear]", () => {
    filters.status = ""; filters.problems = false; filters.from = ""; filters.to = "";
    offset = 0;
    open.clear();
    setHTML($("[data-filters]", root), filtersForm());
    load();
  });
  on(root, "click", "[data-action=page]", (e, el) => {
    offset = Math.max(0, offset + Number(el.dataset.dir) * LIMIT);
    open.clear();
    load();
    root.scrollIntoView({ block: "start" });
  });
  on(root, "click", "[data-action=toggle]", (e, el) => {
    const id = Number(el.dataset.id);
    if (open.has(id)) open.delete(id); else open.add(id);
    render();
    $(`[data-action=toggle][data-id="${id}"]`, root)?.focus();
  });
  on(root, "click", "[data-action=retry]", () => { result = null; load(); });

  await load();
  return () => { seq++; };
}
