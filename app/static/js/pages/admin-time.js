// Administration › Time & priorities: timezone, network time sync, and the broadcast conflict policy.

import { api } from "../api.js";
import { fmtAgo, fmtDate, fmtDateTime, fmtDuration, fmtTime, setTimeZone, t } from "../i18n.js";
import { serverNow, state } from "../store.js";
import {
  $, alertBox, confirmDialog, errorState, field, formValues, html, icon, on, pageHeader, raw, setHTML, switchField,
  toast, toastError, withBusy,
} from "../ui.js";

export default async function adminTime(ctx) {
  const { root } = ctx;
  let data = null;
  let zones = [];
  let clock = null;

  async function load() {
    try {
      const [d, z] = await Promise.all([api("/api/system/time"), api("/api/system/timezones")]);
      data = d;
      zones = z.timezones || [];
      render();
    } catch (err) {
      setHTML(root, errorState(err));
    }
  }

  function render() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Time & priorities"),
        description: t("The clock every schedule follows, and what happens when two broadcasts want the speakers at the same time."),
      })}
      <div class="row row-cards">
        <div class="col-lg-6" data-region="tz">${timezoneCard()}</div>
        <div class="col-lg-6" data-region="ntp">${ntpCard()}</div>
        <div class="col-12" data-region="policy">${policyCard()}</div>
      </div>`);
    tickClock();
  }

  const dirtyBadge = () => html`<span class="text-warning small me-auto" data-dirty hidden>${icon("point-filled")} ${t("Unsaved changes")}</span>`;

  // ---- timezone ------------------------------------------------------------
  function timezoneCard() {
    return html`<form class="card h-100" data-form="tz" novalidate>
      <div class="card-header"><h2 class="card-title">${t("Timezone")}</h2></div>
      <div class="card-body">
        <div class="mb-3">
          <div class="text-secondary small">${t("Server time now")}</div>
          <div class="d-flex flex-wrap align-items-baseline gap-2">
            <span class="h1 mb-0 tabular" data-clock>—</span>
            <span class="text-secondary" data-clock-date></span>
          </div>
          <div class="small text-secondary">${t("Timezone in use: {tz}", { tz: data.timezone })}</div>
        </div>
        <div class="mb-2">
          <label class="form-label" for="tz-filter">${t("Choose timezone")}</label>
          <div class="input-icon mb-2">
            <span class="input-icon-addon">${icon("search")}</span>
            <input id="tz-filter" class="form-control" type="search" data-tz-filter placeholder="${t("Search, for example Tripoli or London")}" autocomplete="off"
              aria-controls="tz-select">
          </div>
          <select id="tz-select" class="form-select ltr" name="timezone" size="7" aria-label="${t("Timezone")}">${tzOptions("")}</select>
          <div class="form-hint" data-tz-count></div>
        </div>
        ${alertBox("warning", t("Changing the timezone moves every schedule in both modules: prayer times and school bells follow the new local time. It is the same timezone as the Adhan location settings."))}
      </div>
      <div class="card-footer d-flex align-items-center gap-2 flex-wrap">
        ${dirtyBadge()}
        <button type="submit" class="btn btn-primary ms-auto">${icon("device-floppy")} ${t("Save timezone")}</button>
      </div>
    </form>`;
  }

  function tzOptions(filter) {
    const f = filter.toLowerCase().replace(/\s+/g, "_");
    const current = data.timezone;
    const list = zones.filter((z) => !f || z.toLowerCase().includes(f) || z === current);
    if (!list.includes(current)) list.unshift(current);
    return html`${list.map((z) => html`<option value="${z}" ${z === current ? raw("selected") : ""}>${z.replace(/_/g, " ")}</option>`)}`;
  }

  on(root, "input", "[data-tz-filter]", (e, el) => {
    const form = el.form;
    const sel = form.timezone;
    const chosen = sel.value;
    setHTML(sel, tzOptions(el.value.trim()));
    if ([...sel.options].some((o) => o.value === chosen)) sel.value = chosen;
    const n = sel.options.length;
    $("[data-tz-count]", form).textContent = el.value.trim() ? t("{n} matching", { n }) : "";
  });

  function tickClock() {
    const el = $("[data-clock]", root);
    if (!el) return;
    const now = serverNow();
    el.textContent = fmtTime(now, { seconds: true });
    const d = $("[data-clock-date]", root);
    if (d) d.textContent = fmtDate(now, { weekday: true });
  }

  on(root, "submit", "form[data-form=tz]", async (e, form) => {
    e.preventDefault();
    const tz = form.timezone.value;
    if (!tz || tz === data.timezone) { toast(t("The timezone is already {tz}.", { tz: data.timezone }), "info"); return; }
    const ok = await confirmDialog({
      title: t("Change the timezone to {tz}?", { tz }),
      message: t("Every schedule in both modules follows the new timezone straight away. A bell set for 08:00 rings at 08:00 in {tz}, and prayer times are recalculated.", { tz }),
      confirmLabel: t("Change timezone"), danger: true,
    });
    if (!ok) return;
    try {
      const r = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/system/time", { method: "PUT", body: { timezone: tz } }));
      data = r;
      // Keep the rest of the app's times in step with the new zone.
      setTimeZone(r.timezone);
      if (state.me?.server) state.me.server.timezone = r.timezone;
      setHTML($("[data-region=tz]", root), timezoneCard());
      setHTML($("[data-region=ntp]", root), ntpCard());
      tickClock();
      toast(t("Timezone changed to {tz}.", { tz: r.timezone }));
    } catch (err) {
      toastError(err);
    }
  });

  // ---- time sync -----------------------------------------------------------
  function ntpStatus() {
    const s = data.ntp_status || {};
    if (!s.enabled) {
      return html`<div class="d-flex align-items-center gap-2"><span class="status-dot status-secondary"></span><span class="fw-semibold">${t("Sync is off")}</span></div>
        <div class="small text-secondary">${t("The server trusts its own clock. Turn sync on so bells and the Adhan stay on time.")}</div>`;
    }
    const synced = !!s.synced;
    const rows = [
      [t("Offset"), s.last_sync ? t("{ms} ms", { ms: Math.round(s.offset_ms ?? 0) }) : "—"],
      [t("Last sync"), s.last_sync ? html`<span title="${fmtDateTime(s.last_sync)}">${fmtAgo(s.last_sync)}</span>` : t("never")],
      [t("Next sync"), s.next_sync ? fmtDateTime(s.next_sync) : "—"],
    ];
    if (s.stratum !== undefined && s.stratum !== null) rows.push([t("Stratum"), s.stratum]);
    if (s.round_trip_ms !== undefined && s.round_trip_ms !== null) rows.push([t("Round trip"), t("{ms} ms", { ms: Math.round(s.round_trip_ms) })]);
    return html`
      <div class="d-flex align-items-center gap-2">
        <span class="status-dot ${synced ? "status-green" : "status-red"}"></span>
        <span class="fw-semibold">${synced ? t("In sync") : t("Not synced")}</span>
        <span class="text-secondary small ltr">${s.server}</span>
      </div>
      ${s.error ? html`<div class="mt-2">${alertBox("danger", t(s.error), { title: t("Last sync failed") })}</div>` : ""}
      <dl class="row mb-0 mt-2 small">${rows.map(([k, v]) => html`<dt class="col-5 fw-normal text-secondary">${k}</dt><dd class="col-7 mb-1 tabular">${v}</dd>`)}</dl>`;
  }

  function ntpCard() {
    const n = data.ntp;
    return html`<form class="card h-100" data-form="ntp" novalidate>
      <div class="card-header">
        <h2 class="card-title">${t("Time sync")}</h2>
        <div class="card-actions">
          <button type="button" class="btn btn-outline-secondary btn-sm" data-action="sync" ${n.enabled ? "" : raw("disabled")}
            title="${n.enabled ? t("Check the clock against the time server now") : t("Turn time sync on and save first")}">${icon("refresh")} ${t("Sync now")}</button>
        </div>
      </div>
      <div class="card-body border-bottom" data-region="ntp-status" aria-live="polite">${ntpStatus()}</div>
      <div class="card-body">
        ${switchField({ label: t("Keep the clock in sync with a time server"), name: "enabled", checked: n.enabled })}
        <div class="row">
          ${field({ label: t("Time server"), name: "server", value: n.server, ltr: true, required: true, cls: "col-md-7",
            hint: t("Your domain controller or pool.ntp.org."), attrs: 'spellcheck="false" autocapitalize="off" maxlength="200"' })}
          <div class="col-md-5 mb-3">
            <label class="form-label" for="ntp-interval">${t("Check every")}</label>
            <div class="input-group">
              <input id="ntp-interval" class="form-control tabular" type="number" name="sync_interval_min" value="${n.sync_interval_min}" min="5" max="1440" required data-type="int">
              <span class="input-group-text">${t("minutes")}</span>
            </div>
            <div class="form-hint">${t("5–1440")}</div>
          </div>
        </div>
        ${switchField({ label: t("Also set the computer's own clock"), name: "set_system_clock", checked: n.set_system_clock,
          hint: t("Usually leave off: schedules use the corrected time anyway. Setting the system clock only works when the container has extra privileges (SYS_TIME).") })}
      </div>
      <div class="card-footer d-flex align-items-center gap-2 flex-wrap">
        ${dirtyBadge()}
        <button type="submit" class="btn btn-primary ms-auto">${icon("device-floppy")} ${t("Save time sync")}</button>
      </div>
    </form>`;
  }

  on(root, "click", "[data-action=sync]", async (e, el) => {
    try {
      const s = await withBusy(el, () => api("/api/system/ntp/sync", { method: "POST", body: {}, timeout: 30000 }));
      data.ntp_status = s;
      setHTML($("[data-region=ntp-status]", root), ntpStatus());
      if (s.error) toast(t("Time sync failed: {error}", { error: t(s.error) }), "danger");
      else toast(t("Clock checked. Offset {ms} ms.", { ms: Math.round(s.offset_ms ?? 0) }));
    } catch (err) {
      toastError(err);
    }
  });

  on(root, "submit", "form[data-form=ntp]", async (e, form) => {
    e.preventDefault();
    if (invalid(form)) return;
    const v = formValues(form);
    try {
      const r = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/system/time", { method: "PUT", body: {
        ntp: { enabled: v.enabled, server: v.server.trim(), sync_interval_min: v.sync_interval_min, set_system_clock: v.set_system_clock } } }));
      data = r;
      setHTML($("[data-region=ntp]", root), ntpCard());
      toast(t("Time sync settings saved."));
    } catch (err) {
      toastError(err);
    }
  });

  // ---- policy --------------------------------------------------------------
  function secondsInput(name, label, value, min, max, hint) {
    const id = `policy-${name}`;
    return html`<div class="col-md-6 mb-3">
      <label class="form-label" for="${id}">${label}</label>
      <div class="input-group">
        <input id="${id}" class="form-control tabular" type="number" name="${name}" value="${value}" min="${min}" max="${max}" step="1" required data-type="int" data-duration>
        <span class="input-group-text">${t("seconds")}</span>
      </div>
      <div class="form-hint"><span data-duration-text>${fmtDuration(value)}</span> · ${hint}</div>
    </div>`;
  }

  function policyCard() {
    const p = data.policy;
    return html`<form class="card" data-form="policy" novalidate>
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Broadcast priorities")}</h2>
          <p class="card-subtitle mb-0">${t("Only one thing plays at a time. When two broadcasts meet, this order decides.")}</p>
        </div>
      </div>
      <div class="card-body">
        <div class="row g-4">
          <div class="col-lg-5">
            <ol class="list-group list-group-numbered">
              <li class="list-group-item d-flex gap-2">
                <div class="ms-1"><div class="fw-semibold">${t("Someone rings or tests by hand")}</div>
                  <div class="small text-secondary">${t("Never interrupted automatically. A scheduled broadcast waits until it has finished.")}</div></div>
              </li>
              <li class="list-group-item d-flex gap-2">
                <div class="ms-1"><div class="fw-semibold">${icon("building-mosque", "text-green")} ${t("Adhan")}</div>
                  <div class="small text-secondary">${t("Interrupts a school bell that is ringing.")}</div></div>
              </li>
              <li class="list-group-item d-flex gap-2">
                <div class="ms-1"><div class="fw-semibold">${icon("bell", "text-orange")} ${t("School bells")}</div>
                  <div class="small text-secondary">${t("Wait for anything above them to finish.")}</div></div>
              </li>
            </ol>
          </div>
          <div class="col-lg-7">
            <h3 class="h4">${t("When the line is busy")}</h3>
            <div class="row">
              ${secondsInput("adhan_max_delay_seconds", t("An Adhan may start late by up to"), p.adhan_max_delay_seconds, 0, 1800, t("0–1800"))}
              ${secondsInput("bells_max_delay_seconds", t("A bell may start late by up to"), p.bells_max_delay_seconds, 0, 600, t("0–600"))}
            </div>
            <h3 class="h4 mt-2">${t("After a restart or power cut")}</h3>
            <div class="row">
              ${secondsInput("adhan_late_grace_seconds", t("Still play an Adhan that was due up to … ago"), p.adhan_late_grace_seconds, 10, 900, t("10–900"))}
              ${secondsInput("bells_late_grace_seconds", t("Still play a bell that was due up to … ago"), p.bells_late_grace_seconds, 10, 600, t("10–600"))}
            </div>
            <p class="text-secondary small mb-0">${icon("info-circle")} ${t("Anything later than these limits is recorded as skipped or missed in the history. It never plays unexpectedly afterwards.")}</p>
          </div>
        </div>
      </div>
      <div class="card-footer d-flex align-items-center gap-2 flex-wrap">
        ${dirtyBadge()}
        <button type="submit" class="btn btn-primary ms-auto">${icon("device-floppy")} ${t("Save priorities")}</button>
      </div>
    </form>`;
  }

  on(root, "input", "[data-duration]", (e, el) => {
    const out = el.closest(".mb-3")?.querySelector("[data-duration-text]");
    if (out) out.textContent = el.value === "" ? "—" : fmtDuration(Number(el.value));
  });

  on(root, "submit", "form[data-form=policy]", async (e, form) => {
    e.preventDefault();
    if (invalid(form)) return;
    const v = formValues(form);
    try {
      const r = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/system/time", { method: "PUT", body: {
        policy: { adhan_max_delay_seconds: v.adhan_max_delay_seconds, bells_max_delay_seconds: v.bells_max_delay_seconds,
          adhan_late_grace_seconds: v.adhan_late_grace_seconds, bells_late_grace_seconds: v.bells_late_grace_seconds } } }));
      data = r;
      setHTML($("[data-region=policy]", root), policyCard());
      toast(t("Broadcast priorities saved."));
    } catch (err) {
      toastError(err);
    }
  });

  // ---- shared --------------------------------------------------------------
  function invalid(form) {
    if (form.checkValidity()) return false;
    form.classList.add("was-validated");
    form.querySelector(":invalid")?.focus();
    toast(t("Some fields need attention."), "warning");
    return true;
  }

  const markDirty = (e) => {
    if (e.target.matches("[data-tz-filter]")) return;
    const badge = e.target.closest("form[data-form]")?.querySelector("[data-dirty]");
    if (badge) badge.hidden = false;
  };
  on(root, "input", "form[data-form] input", markDirty);
  on(root, "change", "form[data-form] input, form[data-form] select", markDirty);
  on(root, "click", "[data-action=retry]", load);

  clock = setInterval(tickClock, 1000);
  await load();
  return () => clearInterval(clock);
}
