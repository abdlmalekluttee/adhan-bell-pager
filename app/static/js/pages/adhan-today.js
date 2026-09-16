// Adhan › Today: the day's prayer times, what will play where, live status, tests and the next Adhan.

import { api } from "../api.js";
import { fmtCountdown, fmtDate, fmtDuration, fmtTime, getLang, t } from "../i18n.js";
import { can, kickBroadcast, onBroadcast, serverNow } from "../store.js";
import {
  $, alertBox, broadcastTitle, confirmDialog, errorState, html, icon, occurrenceTitle, on, pageHeader, prayerName, setHTML,
  statusBadge, toast, toastError, withBusy,
} from "../ui.js";

// --------------------------------------------------------------------------
// Shared Adhan helpers (also used by the schedule, location, sounds and history pages)
// --------------------------------------------------------------------------
export const PRAYERS = ["fajr", "dhuhr", "asr", "maghrib", "isha"];

// Shared title helpers live in ui.js; re-exported for pages that import them from here.
export { broadcastTitle, occurrenceTitle, prayerName };

export function methodLabel(key) {
  return {
    UMM_AL_QURA: t("Umm al-Qura (Makkah)"),
    EGYPTIAN: t("Egyptian General Authority"),
    MUSLIM_WORLD_LEAGUE: t("Muslim World League"),
    KARACHI: t("University of Islamic Sciences, Karachi"),
    DUBAI: t("Dubai"),
    MOON_SIGHTING_COMMITTEE: t("Moonsighting Committee"),
    NORTH_AMERICA: t("ISNA (North America)"),
    KUWAIT: t("Kuwait"),
    QATAR: t("Qatar"),
    SINGAPORE: t("Singapore"),
    UOIF: t("UOIF (France)"),
  }[key] || key;
}

/** Short place name for a saved LocationConfig. */
export function placeName(loc) {
  if (!loc) return "";
  const ar = getLang() === "ar";
  if (!loc.use_manual && loc.city) {
    const city = (ar && loc.city_local_name) || loc.city;
    const country = (ar && loc.country_local_name) || loc.country_name;
    return country ? t("{city}, {country}", { city, country }) : city;
  }
  return "";
}

export function coordText(lat, lng) {
  const n = (v) => (typeof v === "number" ? v.toFixed(4) : v);
  return `${n(lat)}, ${n(lng)}`;
}

/**
 * POST a manual Adhan broadcast. Handles a busy paging line like the bells page:
 * asks to interrupt when allowed, explains otherwise. Resolves false when the user declines.
 */
export async function playAdhan(path, body, force = false) {
  try {
    const r = await api(path, { method: "POST", body: { ...body, force } });
    kickBroadcast();
    return r;
  } catch (err) {
    if (err.code === "line_busy") {
      const title = broadcastTitle(err.detail.active?.title);
      if (!err.detail.can_interrupt) {
        throw new Error(t("“{title}” is playing now and you are not allowed to stop it. Try again when it has finished.", { title }));
      }
      const ok = await confirmDialog({
        title: t("Something is already playing"),
        message: t("“{title}” is playing now. Stop it and play this instead?", { title }),
        confirmLabel: t("Stop it and play"), danger: true,
      });
      return ok ? playAdhan(path, body, true) : false;
    }
    throw err;
  }
}

function isoDate(d) { return d.toISOString().slice(0, 10); }
function shiftDate(iso, days) { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return isoDate(d); }

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default async function adhanToday(ctx) {
  const { root } = ctx;
  let date = ctx.query.date || null;
  let data = null;
  let zoneMap = {};
  let refreshTimer = null;
  let tick = null;
  let reloadedFor = null;

  async function load() {
    clearTimeout(refreshTimer);
    try {
      data = await api("/api/adhan/overview", { query: { date } });
      render();
    } catch (err) {
      if (!data) setHTML(root, errorState(err));
      else toastError(err);
    }
    refreshTimer = setTimeout(load, 60000);
  }

  // Build the day's rows: Adhan/iqama occurrences, sunrise, prayers that are off or have no time.
  function buildRows() {
    const s = data.settings.prayer;
    const times = data.times || {};
    const rows = [];
    for (const p of PRAYERS) {
      const occ = data.occurrences.filter((o) => o.prayer === p);
      const adhan = occ.find((o) => o.kind === "adhan");
      if (adhan) rows.push({ type: "occ", o: adhan, at: adhan.at });
      else if (s.enabled_prayers[p] === false) rows.push({ type: "off", prayer: p, at: times[p] || null });
      else rows.push({ type: "notime", prayer: p, at: null });
      const iqama = occ.find((o) => o.kind === "iqama");
      if (iqama) rows.push({ type: "occ", o: iqama, at: iqama.at });
      if (p === "fajr" && times.sunrise) rows.push({ type: "sunrise", prayer: "sunrise", at: times.sunrise });
    }
    // Keep prayer order for rows without a time; otherwise order by time (stable sort).
    let last = "";
    rows.forEach((r) => { r.sortKey = r.at ? new Date(r.at).getTime() : last; last = r.sortKey; });
    return rows.sort((a, b) => (a.sortKey || 0) - (b.sortKey || 0));
  }

  function nextOccurrence() {
    const now = serverNow().getTime();
    return data.occurrences.find((o) => new Date(o.at).getTime() > now && !(o.execution && o.execution.status !== "queued")) || null;
  }

  function render() {
    const today = data.now.slice(0, 10);
    const isToday = data.date === today;
    const actions = [];
    if (can("adhan.schedule.edit")) actions.push(html`<a class="btn btn-outline-secondary" href="#/adhan/schedule">${icon("calendar-time")} ${t("Prayer schedule")}</a>`);
    setHTML(root, html`
      ${pageHeader({
        title: isToday ? t("Adhan today") : t("Adhan"),
        description: t("Prayer times for the day, where the Adhan plays and whether it played. Times use the server's timezone ({tz}).", { tz: data.timezone }),
        actions,
      })}
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
                <div class="fw-semibold">${fmtDate(data.date, { weekday: true })}</div>
                <div class="small text-secondary">${data.source === "manual" ? t("From the uploaded timetable") : t("Calculated: {method}", { method: methodLabel(data.settings.prayer.method) })}</div>
              </div>
            </div>
            ${dayNotices()}
            ${prayerList(isToday)}
          </div>
        </div>
        <div class="col-lg-4">
          ${isToday ? nextCard() : ""}
          ${sourceCard()}
        </div>
      </div>`);
    updateCountdown();
  }

  function dayNotices() {
    const out = [];
    if (data.source === "manual" && !data.manual_timetable.loaded) {
      out.push(alertBox("danger", html`${t("Prayer times are set to come from an uploaded timetable, but no timetable has been uploaded. No Adhan will play.")}
        ${can("adhan.schedule.edit") ? html` <a href="#/adhan/schedule">${t("Upload a timetable")}</a>` : ""}`));
    } else if (!data.occurrences.length && PRAYERS.some((p) => data.settings.prayer.enabled_prayers[p] !== false)) {
      out.push(alertBox("warning", t("There are no prayer times for this day, so no Adhan will play.")));
    }
    if (PRAYERS.every((p) => data.settings.prayer.enabled_prayers[p] === false)) {
      out.push(alertBox("info", t("Every prayer is turned off in the prayer schedule. Nothing will play.")));
    }
    if (data.occurrences.some((o) => o.sound_missing)) {
      out.push(alertBox("danger", html`${t("Some broadcasts below have no recording assigned, so they will fail.")}
        ${can("adhan.sounds.manage") ? html` <a href="#/adhan/sounds">${t("Choose recordings")}</a>` : ""}`));
    }
    return out.length ? html`<div class="card-body pb-0">${out}</div>` : "";
  }

  function prayerList(isToday) {
    const next = isToday ? nextOccurrence() : null;
    const now = serverNow().getTime();
    return html`<ul class="timeline-list" aria-label="${t("Prayer times")}">
      ${buildRows().map((r) => {
        if (r.type === "sunrise") {
          return html`<li class="timeline-row past">
            <div class="timeline-time tabular">${fmtTime(r.at)}</div>
            <div><div class="timeline-title">${icon("sunrise")} ${t("Sunrise")}</div>
              <div class="timeline-sub">${t("For reference. No Adhan at sunrise.")}</div></div>
            <div class="timeline-actions"></div>
          </li>`;
        }
        if (r.type === "off" || r.type === "notime") {
          return html`<li class="timeline-row past">
            <div class="timeline-time tabular">${r.at ? fmtTime(r.at) : "—"}</div>
            <div><div class="timeline-title">${occurrenceTitle(r.prayer, "adhan")}</div>
              <div class="timeline-sub">${r.type === "off" ? t("Turned off in the prayer schedule.") : t("No time for this prayer in the timetable on this day.")}</div></div>
            <div class="timeline-actions"><span class="badge bg-secondary-lt">${r.type === "off" ? t("Off") : t("No time")}</span></div>
          </li>`;
        }
        const o = r.o;
        const ex = o.execution;
        const past = new Date(o.at).getTime() <= now;
        const isNext = next && next.key === o.key;
        const conflict = (o.conflicts || []).find((c) => c.outcome !== "wins");
        return html`<li class="timeline-row ${past && !isNext ? "past" : ""} ${isNext ? "next" : ""}">
          <div class="timeline-time tabular">${fmtTime(o.at)}</div>
          <div>
            <div class="timeline-title">${occurrenceTitle(o.prayer, o.kind)}</div>
            <div class="timeline-sub">
              ${o.sound_missing
                ? html`<span class="text-danger">${icon("music-off")} ${t("No recording assigned")}</span>`
                : html`${icon("music")} <span class="ltr">${o.sound}</span>${o.est_seconds ? html` · ${fmtDuration(o.est_seconds)}` : ""}`}
              · ${icon("speakerphone")} ${zoneNames(o.zone_ids)}
            </div>
            ${conflict ? html`<div class="small text-warning mt-1">${icon("alert-triangle")} ${conflictText(conflict)}</div>` : ""}
            ${ex && ex.reason && ex.status !== "success" ? html`<div class="small text-secondary mt-1">${t(ex.reason)}</div>` : ""}
          </div>
          <div class="timeline-actions">
            ${ex ? statusBadge(ex.status) : isNext ? html`<span class="badge bg-primary text-primary-fg">${t("Next")}</span>` : past ? "" : html`<span class="badge bg-secondary-lt">${t("Scheduled")}</span>`}
            ${can("adhan.broadcast") && !o.sound_missing ? html`<button type="button" class="btn btn-sm btn-ghost-secondary" data-action="test" data-prayer="${o.prayer}" data-kind="${o.kind}"
                aria-label="${t("Play a test of {title} now", { title: occurrenceTitle(o.prayer, o.kind) })}">${icon("player-play")} ${t("Test")}</button>` : ""}
          </div>
        </li>`;
      })}
    </ul>`;
  }

  function zoneNames(ids) {
    if (!ids || !ids.length) return t("All speakers");
    return ids.map((id) => zoneMap[id] || t("Unknown zone")).join(", ");
  }

  function conflictText(c) {
    const other = broadcastTitle(c.with);
    if (c.outcome === "skipped") return t("Overlaps “{with}”, which has priority. This will probably be skipped.", { with: other });
    if (c.outcome === "interrupts") return t("“{with}” starts while this is playing and will cut it short.", { with: other });
    return t("Overlaps “{with}”. This may start a little late.", { with: other });
  }

  function nextCard() {
    const n = nextOccurrence();
    if (!n) {
      return html`<div class="card mb-3 next-card"><div class="card-body">
        <div class="label">${t("Next Adhan")}</div>
        <div class="fw-semibold mt-1">${t("Nothing more today")}</div>
        <button type="button" class="btn btn-link px-0 mt-1" data-action="day" data-shift="1">${t("See tomorrow's times")} ${icon("chevron-right", "flip-rtl")}</button>
      </div></div>`;
    }
    return html`<div class="card mb-3 next-card"><div class="card-body">
      <div class="label">${n.kind === "iqama" ? t("Next iqama") : t("Next Adhan")}</div>
      <div class="d-flex align-items-end gap-3 mt-1">
        <div class="countdown tabular" data-countdown="${n.at}">—</div>
        <div class="text-secondary pb-1">${t("at {time}", { time: fmtTime(n.at) })}</div>
      </div>
      <div class="mt-2 fw-semibold">${occurrenceTitle(n.prayer, n.kind)}</div>
      <div class="small text-secondary">${icon("speakerphone")} ${zoneNames(n.zone_ids)}</div>
    </div></div>`;
  }

  function sourceCard() {
    const s = data.settings;
    const loc = s.location;
    const place = placeName(loc);
    const flag = !loc.use_manual && /^[A-Za-z]{2}$/.test(loc.country_code || "") ? html`<span class="fi fi-${loc.country_code.toLowerCase()} me-1" aria-hidden="true"></span>` : "";
    const manual = data.source === "manual";
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Where the times come from")}</h2></div>
      <div class="list-group list-group-flush">
        <div class="list-group-item">
          <div class="small text-secondary">${t("Source")}</div>
          <div class="fw-semibold">${manual ? t("Uploaded timetable") : t("Calculated from the location")}</div>
          <div class="small text-secondary">${manual
            ? (data.manual_timetable.loaded ? t("{n} days in the timetable", { n: data.manual_timetable.days }) : t("No timetable uploaded yet"))
            : methodLabel(s.prayer.method)}</div>
        </div>
        <div class="list-group-item">
          <div class="small text-secondary">${t("Location")}</div>
          <div class="fw-semibold">${place ? html`${flag}${place}` : html`<span class="ltr tabular">${coordText(loc.latitude, loc.longitude)}</span>`}</div>
          <div class="small text-secondary">${t("Timezone")}: <span class="ltr">${data.timezone}</span></div>
        </div>
        <div class="list-group-item">
          <div class="small text-secondary">${t("Plays on")}</div>
          <div class="fw-semibold">${zoneNames(s.zone_ids)}</div>
        </div>
      </div>
      <div class="card-footer d-flex flex-wrap gap-2">
        <a class="btn btn-outline-secondary btn-sm" href="#/adhan/schedule">${icon("calendar-time")} ${t("Prayer schedule")}</a>
        <a class="btn btn-outline-secondary btn-sm" href="#/adhan/location">${icon("map-pin")} ${t("Location")}</a>
      </div>
    </div>`;
  }

  function updateCountdown() {
    const el = $("[data-countdown]", root);
    if (!el) return;
    const secs = (new Date(el.dataset.countdown).getTime() - serverNow().getTime()) / 1000;
    el.textContent = secs > 0 ? fmtCountdown(secs) : t("now");
    if (secs < -3 && reloadedFor !== el.dataset.countdown) { reloadedFor = el.dataset.countdown; load(); }
  }

  on(root, "click", "[data-action=retry]", () => { setHTML(root, ""); load(); });
  on(root, "click", "[data-action=day]", (e, el) => {
    date = shiftDate(data.date, Number(el.dataset.shift));
    history.replaceState(null, "", `#/adhan?date=${date}`);
    load();
  });
  on(root, "click", "[data-action=today]", () => { date = null; history.replaceState(null, "", "#/adhan"); load(); });
  on(root, "click", "[data-action=test]", async (e, el) => {
    const { prayer, kind } = el.dataset;
    const title = occurrenceTitle(prayer, kind);
    const ok = await confirmDialog({
      title: t("Play a test now?"),
      message: t("“{title}” will play right away on {where}.", { title, where: zoneNames(data.settings.zone_ids) }),
      confirmLabel: t("Play test"),
    });
    if (!ok) return;
    try {
      const r = await withBusy(el, () => playAdhan("/api/adhan/test", { prayer, kind }));
      if (r !== false) toast(t("Test started: {title}", { title }));
    } catch (err) {
      toastError(err);
    }
  });

  try {
    const dests = await api("/api/system/destinations");
    zoneMap = Object.fromEntries(dests.zones.map((z) => [z.id, z.name]));
  } catch { /* zone names are cosmetic */ }
  const unsub = onBroadcast((b) => { if (b.finished && data) load(); });
  tick = setInterval(updateCountdown, 1000);
  await load();
  return () => { clearTimeout(refreshTimer); clearInterval(tick); unsub(); };
}
