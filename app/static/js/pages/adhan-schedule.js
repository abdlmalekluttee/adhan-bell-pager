// Adhan › Prayer schedule: where times come from, calculation, which prayers play, iqama and zones.

import { api } from "../api.js";
import { fmtTime, t, tn } from "../i18n.js";
import { can } from "../store.js";
import {
  $, $$, alertBox, dirtyTracker, errorState, html, icon, on, pageHeader, raw, saveBar, setHTML, skeleton,
  toast, toastError, withBusy,
} from "../ui.js";
import { PRAYERS, methodLabel, prayerName } from "./adhan-today.js";

const HIGH_LAT_RULES = ["MIDDLE_OF_THE_NIGHT", "SEVENTH_OF_THE_NIGHT", "TWILIGHT_ANGLE"];

function highLatLabel(key) {
  return {
    MIDDLE_OF_THE_NIGHT: t("Middle of the night"),
    SEVENTH_OF_THE_NIGHT: t("One seventh of the night"),
    TWILIGHT_ANGLE: t("Twilight angle"),
  }[key] || key;
}

export default async function adhanSchedule(ctx) {
  const { root } = ctx;
  const canEdit = can("adhan.schedule.edit");
  let settings = null;
  let zones = [];
  let todayTimes = {};

  setHTML(root, html`
    ${pageHeader({
      title: t("Prayer schedule"),
      description: t("Choose how prayer times are worked out, which prayers play the Adhan, the iqama, and which speakers are used."),
    })}
    <div data-host>${skeleton(6)}</div>
    ${canEdit ? saveBar() : ""}`);
  const host = $("[data-host]", root);
  const bar = $(".save-bar", root);

  async function load() {
    try {
      const [s, d, pt] = await Promise.all([
        api("/api/adhan/settings"),
        api("/api/system/destinations").catch(() => ({ zones: [] })),
        api("/api/adhan/prayer-times").catch(() => null),
      ]);
      settings = s;
      zones = d.zones || [];
      todayTimes = pt && pt.days && pt.days[0] ? pt.days[0].times : {};
      render();
    } catch (err) {
      setHTML(host, errorState(err));
    }
  }

  // ------------------------------------------------------------------------
  function render() {
    const p = settings.prayer;
    setHTML(host, html`
      ${canEdit ? "" : alertBox("info", t("You can view these settings. Changing them needs the “Edit Adhan schedule” permission."))}
      <form data-form novalidate>
        <fieldset ${canEdit ? "" : raw("disabled")}>
          <div class="row row-cards">
            <div class="col-lg-6">${sourceCard(p)}</div>
            <div class="col-lg-6" data-calc-col>${calculationCard(p)}</div>
            <div class="col-lg-8">${prayersCard(p)}</div>
            <div class="col-lg-4">${iqamaCard(p)}</div>
            <div class="col-12">${zonesCard()}</div>
          </div>
        </fieldset>
      </form>`);
    syncUI();
  }

  function sourceCard(p) {
    const opt = (value, title, text, iconName) => html`<label class="form-selectgroup-item flex-fill">
      <input type="radio" name="source" value="${value}" class="form-selectgroup-input" ${p.source === value ? raw("checked") : ""}>
      <div class="form-selectgroup-label d-flex align-items-center p-3 h-100">
        <div class="me-3"><span class="form-selectgroup-check"></span></div>
        <div class="text-start">
          <div class="fw-semibold">${icon(iconName)} ${title}</div>
          <div class="small text-secondary">${text}</div>
        </div>
      </div>
    </label>`;
    return html`<div class="card h-100">
      <div class="card-header"><h2 class="card-title">${t("Where prayer times come from")}</h2></div>
      <div class="card-body">
        <div class="form-selectgroup form-selectgroup-boxes d-flex flex-column gap-2 mb-3">
          ${opt("calculated", t("Calculate from the location"), t("Worked out every day from the location and the calculation method. Recommended."), "calculator")}
          ${opt("manual", t("Use an uploaded timetable"), t("Exact times from a file, for example from your local mosque or ministry."), "file-upload")}
        </div>
        <div data-manual-panel data-no-dirty>${manualPanel()}</div>
      </div>
    </div>`;
  }

  function manualPanel() {
    const mt = settings.manual_timetable || { loaded: false, days: 0 };
    return html`
      ${mt.loaded
        ? alertBox("success", tn(mt.days, "A timetable with {n} day is loaded.", "A timetable with {n} days is loaded."))
        : alertBox("warning", t("No timetable is loaded yet. Until you upload one, no Adhan will play with this option."))}
      ${canEdit ? html`<div class="mb-2">
        <label class="form-label" for="tt-file">${mt.loaded ? t("Replace the timetable") : t("Upload a timetable")}</label>
        <div class="d-flex flex-wrap gap-2">
          <input id="tt-file" class="form-control flex-fill" style="min-width:0;flex-basis:12rem" type="file" accept=".txt,text/plain" data-tt-file>
          <button type="button" class="btn btn-outline-secondary" data-action="upload-tt">${icon("upload")} ${t("Upload")}</button>
        </div>
        <div class="progress progress-sm mt-2" data-tt-progress hidden><div class="progress-bar" style="width:0%"></div></div>
        <div class="form-hint mt-1">${t("A text file (up to 5 MB). Uploading switches the source to the timetable straight away.")}</div>
      </div>` : ""}
      <details class="mt-2">
        <summary class="small">${t("How the file should look")}</summary>
        <p class="small text-secondary mt-2 mb-1">${t("One block per day. Leave a time blank to skip that prayer on that day. The year is ignored, so one year's file repeats every year.")}</p>
        <pre class="ltr small mb-0">Date : 2026-01-01
Fajr : 06:25:00
Dhuhr : 13:14:00
Asr : 15:52:00
Maghrib : 18:15:00
Isha : 19:41:00

Date : 2026-01-02
Fajr :
Dhuhr : 13:14:30
...</pre>
      </details>`;
  }

  function calculationCard(p) {
    const methods = settings.methods || [];
    return html`<div class="card h-100">
      <div class="card-header"><h2 class="card-title">${t("Calculation")}</h2></div>
      <div class="card-body">
        <div class="mb-3">
          <label class="form-label" for="calc-method">${t("Calculation method")}</label>
          <select id="calc-method" class="form-select" name="method">
            ${methods.map((m) => html`<option value="${m}" ${m === p.method ? raw("selected") : ""}>${methodLabel(m)}</option>`)}
          </select>
          <div class="form-hint">${t("Use the method your local mosques follow. The Location page suggests one for your country.")}</div>
        </div>
        <div class="mb-3">
          <label class="form-label" for="calc-madhab">${t("Asr time")}</label>
          <select id="calc-madhab" class="form-select" name="madhab">
            <option value="SHAFI" ${p.madhab === "SHAFI" ? raw("selected") : ""}>${t("Standard (Shafi, Maliki, Hanbali)")}</option>
            <option value="HANAFI" ${p.madhab === "HANAFI" ? raw("selected") : ""}>${t("Hanafi")}</option>
          </select>
          <div class="form-hint">${t("Hanafi Asr is later in the afternoon.")}</div>
        </div>
        <div class="mb-0">
          <label class="form-label" for="calc-hl">${t("High-latitude rule")}</label>
          <select id="calc-hl" class="form-select" name="high_latitude_rule">
            ${HIGH_LAT_RULES.map((r) => html`<option value="${r}" ${r === p.high_latitude_rule ? raw("selected") : ""}>${highLatLabel(r)}</option>`)}
          </select>
          <div class="form-hint">${t("Only matters far from the equator, where twilight can last most of the night in summer.")}</div>
        </div>
      </div>
    </div>`;
  }

  function prayersCard(p) {
    return html`<div class="card h-100">
      <div class="card-header"><h2 class="card-title">${t("Prayers")}</h2></div>
      <div class="table-responsive">
        <table class="table card-table table-vcenter table-editor">
          <thead><tr>
            <th>${t("Prayer")}</th>
            <th>${t("Today")}</th>
            <th>${t("Adhan")}</th>
            <th>${t("Adjust (minutes)")}</th>
            <th>${t("Iqama")}</th>
          </tr></thead>
          <tbody>
            ${PRAYERS.map((name) => html`<tr>
              <td class="fw-semibold">${prayerName(name)}</td>
              <td class="tabular text-secondary text-nowrap">${todayTimes[name] ? fmtTime(todayTimes[name]) : "—"}</td>
              <td><label class="form-check form-switch mb-0">
                <input class="form-check-input" type="checkbox" data-prayer-on="${name}" ${p.enabled_prayers[name] !== false ? raw("checked") : ""}
                  aria-label="${t("Play the Adhan for {prayer}", { prayer: prayerName(name) })}">
              </label></td>
              <td><input class="form-control tabular" type="number" inputmode="numeric" min="-60" max="60" step="1" style="max-width:7rem"
                data-adjust="${name}" value="${p.adjustments[name] ?? 0}" aria-label="${t("Minutes to add to {prayer}", { prayer: prayerName(name) })}"></td>
              <td><label class="form-check form-switch mb-0">
                <input class="form-check-input" type="checkbox" data-iqama-on="${name}" ${p.iqama_prayers[name] !== false ? raw("checked") : ""}
                  aria-label="${t("Play the iqama for {prayer}", { prayer: prayerName(name) })}">
              </label></td>
            </tr>`)}
          </tbody>
        </table>
      </div>
      <div class="card-body pt-2">
        <div class="form-hint" data-adjust-hint>${t("Adjust moves a calculated time earlier (negative) or later (positive), from -60 to 60 minutes. “Today” shows the saved times.")}</div>
        <div class="form-hint mt-1" data-iqama-hint>${t("Turn on the iqama to choose which prayers have one.")}</div>
      </div>
    </div>`;
  }

  function iqamaCard(p) {
    return html`<div class="card h-100">
      <div class="card-header"><h2 class="card-title">${t("Iqama")}</h2></div>
      <div class="card-body">
        <div class="mb-3"><label class="form-check form-switch mb-0">
          <input class="form-check-input" type="checkbox" name="iqama_enabled" ${p.iqama.enabled ? raw("checked") : ""}>
          <span class="form-check-label">${t("Play the iqama after the Adhan")}</span></label>
        </div>
        <div class="mb-0">
          <label class="form-label" for="iqama-min">${t("Minutes after the Adhan")}</label>
          <input id="iqama-min" class="form-control tabular" type="number" inputmode="numeric" min="1" max="90" step="1" name="iqama_minutes" value="${p.iqama.minutes}" style="max-width:8rem">
          <div class="invalid-feedback">${t("Enter a number from 1 to 90.")}</div>
          <div class="form-hint">${t("The same gap is used for every prayer, from 1 to 90 minutes. Choose the iqama recording on the Adhan sounds page.")}</div>
        </div>
      </div>
    </div>`;
  }

  function zonesCard() {
    const selected = new Set(settings.zone_ids || []);
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Where the Adhan plays")}</h2></div>
      <div class="card-body">
        ${zones.length ? html`
          <div class="d-flex flex-wrap gap-3 mb-2">
            ${zones.map((z) => html`<label class="form-check mb-0">
              <input class="form-check-input" type="checkbox" data-zone="${z.id}" ${selected.has(z.id) ? raw("checked") : ""}>
              <span class="form-check-label">${z.name} <span class="text-secondary">(${tn(z.destination_ids.length, "{n} speaker", "{n} speakers")})</span></span>
            </label>`)}
          </div>
          <div class="form-hint" data-zone-hint></div>`
        : html`<p class="mb-1">${t("No zones are set up, so the Adhan plays on every enabled speaker.")}</p>
          ${can("system.destinations.manage") ? html`<a href="#/admin/destinations">${t("Set up speakers and zones")}</a>` : ""}`}
      </div>
    </div>`;
  }

  // ------------------------------------------------------------------------
  function syncUI() {
    const form = $("[data-form]", host);
    if (!form) return;
    const manual = form.querySelector("[name=source]:checked")?.value === "manual";
    $("[data-manual-panel]", form).hidden = !manual;
    $("[data-calc-col]", form).hidden = manual;
    $("[data-adjust-hint]", form).textContent = manual
      ? t("With an uploaded timetable the times are used exactly as written, so adjustments are ignored.")
      : t("Adjust moves a calculated time earlier (negative) or later (positive), from -60 to 60 minutes. “Today” shows the saved times.");
    $$("[data-adjust]", form).forEach((el) => { el.disabled = manual || !canEdit; });
    const iqamaOn = form.iqama_enabled.checked;
    $$("[data-iqama-on]", form).forEach((el) => { el.disabled = !iqamaOn || !canEdit; });
    form.iqama_minutes.disabled = !iqamaOn || !canEdit;
    $("[data-iqama-hint]", form).hidden = iqamaOn;
    $$("[data-prayer-on]", form).forEach((el) => {
      const iq = $(`[data-iqama-on="${el.dataset.prayerOn}"]`, form);
      if (!el.checked && iq) iq.disabled = true;
    });
    const zoneHint = $("[data-zone-hint]", form);
    if (zoneHint) {
      const picked = $$("[data-zone]:checked", form).length;
      zoneHint.textContent = picked
        ? t("The Adhan plays only on the ticked zones.")
        : t("Nothing ticked: the Adhan plays on every enabled speaker.");
    }
  }

  function collect() {
    const form = $("[data-form]", host);
    let bad = null;
    const adjustments = {};
    const enabled = {};
    const iqamaPrayers = {};
    for (const name of PRAYERS) {
      const adj = $(`[data-adjust="${name}"]`, form);
      const v = adj.value.trim() === "" ? 0 : Number(adj.value);
      const ok = Number.isInteger(v) && v >= -60 && v <= 60;
      adj.classList.toggle("is-invalid", !ok);
      if (!ok) bad = bad || adj;
      adjustments[name] = ok ? v : 0;
      enabled[name] = $(`[data-prayer-on="${name}"]`, form).checked;
      iqamaPrayers[name] = $(`[data-iqama-on="${name}"]`, form).checked;
    }
    const minutes = Number(form.iqama_minutes.value);
    const minutesOk = Number.isInteger(minutes) && minutes >= 1 && minutes <= 90;
    form.iqama_minutes.classList.toggle("is-invalid", !minutesOk);
    if (!minutesOk) bad = bad || form.iqama_minutes;
    if (bad) {
      bad.focus();
      throw new Error(t("Some values are out of range. Check the highlighted fields."));
    }
    return {
      prayer: {
        source: form.querySelector("[name=source]:checked")?.value || "calculated",
        method: $("[name=method]", form).value,
        madhab: $("[name=madhab]", form).value,
        high_latitude_rule: $("[name=high_latitude_rule]", form).value,
        adjustments,
        enabled_prayers: enabled,
        iqama_prayers: iqamaPrayers,
        iqama: { enabled: form.iqama_enabled.checked, minutes },
      },
      zone_ids: $$("[data-zone]:checked", form).map((el) => el.dataset.zone),
    };
  }

  async function uploadTimetable(btn) {
    const input = $("[data-tt-file]", host);
    const file = input?.files?.[0];
    if (!file) { toast(t("Choose a timetable file first."), "warning"); input?.focus(); return; }
    const progress = $("[data-tt-progress]", host);
    const barEl = progress.firstElementChild;
    progress.hidden = false;
    barEl.style.width = "0%";
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await withBusy(btn, () => api("/api/adhan/manual-timetable", {
        method: "POST", form, onUploadProgress: (pct) => { barEl.style.width = `${pct}%`; },
      }));
      settings.manual_timetable = { loaded: true, days: r.days };
      settings.prayer.source = "manual";
      toast(tn(r.days, "Timetable imported: {n} day.", "Timetable imported: {n} days."));
      setHTML($("[data-manual-panel]", host), manualPanel());
      const radio = $("[name=source][value=manual]", host);
      if (radio) radio.checked = true;
      syncUI();
    } catch (err) {
      progress.hidden = true;
      if (err.code === "timetable_invalid") {
        toast(t("That file has no usable days. Each day needs a line like “Date : 2026-01-01” followed by lines like “Dhuhr : 13:14:00”."), "danger");
      } else {
        toastError(err);
      }
    }
  }

  // ------------------------------------------------------------------------
  on(root, "change", "[data-form]", syncUI);
  on(root, "click", "[data-action=retry]", () => { setHTML(host, skeleton(6)); load(); });
  on(root, "click", "[data-action=upload-tt]", (e, el) => uploadTimetable(el));

  if (canEdit && bar) {
    dirtyTracker(host, bar, {
      onSave: async () => {
        await api("/api/adhan/settings", { method: "PUT", body: collect() });
        toast(t("Prayer schedule saved."));
        await load();
      },
      onDiscard: () => render(),
    });
  }

  await load();
}
