// Dashboard: system health, what is playing, what is next, and recent problems.

import { api } from "../api.js";
import { fmtAgo, fmtCountdown, fmtDate, fmtDateTime, fmtTime, t, tn } from "../i18n.js";
import { can, kickBroadcast, onBroadcast, serverNow, state } from "../store.js";
import { $, broadcastTitle, errorState, html, icon, moduleChip, on, pageHeader, resultsTable, setHTML, statusBadge } from "../ui.js";

export default async function dashboard(ctx) {
  const { root } = ctx;
  let data = null;
  let timer = null;
  let tick = null;

  async function load() {
    try {
      data = await api("/api/dashboard");
      render();
    } catch (err) {
      if (!data) {
        setHTML(root, errorState(err));
      }
    }
    timer = setTimeout(load, 15000);
  }

  function render() {
    const d = data;
    const name = state.me.user.display_name || state.me.user.username;
    const actions = [];
    if (d.can.broadcast.includes("bells")) actions.push(html`<a href="#/bells" class="btn btn-outline-secondary">${icon("bell-ringing")} ${t("Ring a bell")}</a>`);
    if (d.can.broadcast.includes("adhan")) actions.push(html`<a href="#/adhan" class="btn btn-outline-secondary">${icon("building-mosque")} ${t("Test the Adhan")}</a>`);
    setHTML(root, html`
      ${pageHeader({ title: t("Dashboard"), description: t("Signed in as {name}. Everything below updates on its own.", { name }), actions })}
      <div class="row row-deck row-cards">
        <div class="col-12">${healthCard(d)}</div>
        <div class="col-lg-7">${upcomingCard(d)}</div>
        <div class="col-lg-5">${problemsCard(d)}</div>
      </div>`);
    updateCountdown();
  }

  function healthCard(d) {
    const h = d.health;
    const sip = h.sip;
    const sipText = {
      registered: t("Connected"), simulated: t("Simulation"), disabled: t("Turned off"),
      unavailable: t("Not installed"), error: t("Not connected"),
    }[sip.state];
    const cells = [
      { ok: sip.ok || sip.state === "simulated", dot: sip.state === "simulated" ? "status-azure" : sip.state === "disabled" ? "status-secondary" : null, iconName: "phone-call", label: t("Phone system"), value: sipText,
        detail: sip.ok ? html`${t("Registered as")} <bdi class="ltr">${sip.identity}</bdi>` : t(sip.message || ""),
        link: d.can.sip ? "#/admin/sip" : null },
      { ok: h.time.ok, dot: h.time.enabled ? null : "status-secondary", iconName: "clock-check", label: t("Clock"), value: h.time.enabled ? (h.time.ok ? t("In sync") : t("Not synced")) : t("Sync off"),
        detail: h.time.enabled && h.time.last_sync ? t("Offset {ms} ms · checked {ago}", { ms: Math.round(h.time.offset_ms), ago: fmtAgo(h.time.last_sync) }) : (h.time.error ? t(h.time.error) : "") },
      { ok: h.scheduler.ok, iconName: "calendar-bolt", label: t("Scheduler"), value: h.scheduler.ok ? t("Running") : t("Stopped"),
        detail: h.scheduler.ok ? t("Broadcasts will go out on time.") : t("Scheduled broadcasts will not play. Restart the service.") },
      { ok: h.storage.ok, iconName: "database", label: t("Storage"), value: h.storage.ok ? t("OK") : t("Almost full"),
        detail: t("{mb} MB free", { mb: h.storage.free_mb.toLocaleString() }) },
    ];
    if (h.https) {
      const hs = h.https;
      const value = { active: t("Certificate valid"), no_certificate: t("No certificate"), expiring: t("Expiring soon"), expired: t("Expired"),
        renewal_failing: t("Renewal failing"), listener_error: t("Not serving") }[hs.state] || hs.state;
      const detail = hs.error ? t(hs.error)
        : hs.not_after ? t("Valid until {date} · {days}", { date: fmtDate(hs.not_after), days: tn(hs.days_left, "{n} day left", "{n} days left") }) : "";
      cells.push({ ok: hs.ok, iconName: "lock", label: t("HTTPS"), value, detail, link: d.can.https ? "#/admin/https" : null });
    }
    const allOk = cells.every((c) => c.ok);
    return html`<div class="card">
      <div class="card-header">
        <h2 class="card-title">${allOk ? html`${icon("circle-check", "text-green me-2")}${t("All systems working")}` : html`${icon("alert-triangle", "text-warning me-2")}${t("Something needs attention")}`}</h2>
      </div>
      <div class="status-grid">
        ${cells.map((c) => html`<div class="status-cell">
          <div class="stat-label">${icon(c.iconName)} ${c.label}</div>
          <div class="stat-value"><span class="status-dot ${c.dot || (c.ok ? "status-green" : "status-red")} me-2"></span>${c.link ? html`<a href="${c.link}" class="text-reset">${c.value}</a>` : c.value}</div>
          <div class="stat-detail">${c.detail}</div>
        </div>`)}
      </div>
    </div>`;
  }

  function upcomingCard(d) {
    const items = d.upcoming || [];
    const next = items[0];
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Coming up")}</h2></div>
      ${next ? html`<div class="card-body border-bottom next-card">
        <div class="d-flex flex-wrap align-items-end gap-3">
          <div class="me-auto">
            <div class="label">${t("Next broadcast")}</div>
            <div class="h2 mb-1">${broadcastTitle(next.title)}</div>
            <div>${moduleChip(next.module)} <span class="text-secondary ms-2 tabular">${fmtDateTime(next.at)}</span></div>
          </div>
          <div class="text-end"><div class="label">${t("Starts in")}</div><div class="countdown tabular" data-countdown="${next.at}">—</div></div>
        </div>
      </div>` : ""}
      ${items.length > 1 ? html`<ul class="timeline-list">
        ${items.slice(1).map((o) => html`<li class="timeline-row">
          <div class="timeline-time tabular">${fmtTime(o.at)}${dayLabel(o.at)}</div>
          <div><div class="timeline-title">${broadcastTitle(o.title)}</div><div class="timeline-sub">${moduleChip(o.module)}</div></div>
          <div class="timeline-actions"><a class="btn btn-sm btn-ghost-secondary" href="#/${o.module}" aria-label="${o.module === "adhan" ? t("View in Adhan") : t("View in School Bells")}">${icon("chevron-right", "flip-rtl")}<span class="d-none d-md-inline">${o.module === "adhan" ? t("View in Adhan") : t("View in School Bells")}</span></a></div>
        </li>`)}
      </ul>` : ""}
      ${!items.length ? html`<div class="empty"><div class="empty-icon">${icon("calendar-check")}</div>
        <p class="empty-title">${t("Nothing scheduled in the next day")}</p>
        <p class="empty-subtitle text-secondary">${d.modules.length ? t("Check the prayer settings or the school timetable if you expected something.") : t("You do not have access to a module yet.")}</p></div>` : ""}
    </div>`;
  }

  function dayLabel(at) {
    const day = (iso) => new Intl.DateTimeFormat("en-CA", { timeZone: state.me.server.timezone }).format(new Date(iso));
    if (day(at) === day(serverNow().toISOString())) return "";
    return html`<div class="small fw-normal text-secondary">${t("Tomorrow")}</div>`;
  }

  function problemsCard(d) {
    const rows = d.recent_problems || [];
    const counts = d.today_counts || {};
    const played = counts.success || 0;
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Recent problems")}</h2>
        <div class="card-actions text-secondary small">${tn(played, "{n} broadcast played today", "{n} broadcasts played today")}</div></div>
      ${rows.length ? html`<div class="list-group list-group-flush">
        ${rows.map((r) => html`<details class="list-group-item">
          <summary class="d-flex gap-2 align-items-start" style="cursor:pointer;list-style:none">
            <div class="flex-fill">
              <div class="fw-semibold">${broadcastTitle(r.title)}</div>
              <div class="small text-secondary">${moduleChip(r.module)} <span class="tabular ms-1">${fmtDateTime(r.scheduled_for || r.created_at)}</span></div>
              <div class="small mt-1">${t(r.reason)}</div>
            </div>
            <div>${statusBadge(r.status)}</div>
          </summary>
          <div class="mt-2">${resultsTable(r.results)}</div>
        </details>`)}
      </div>` : html`<div class="empty"><div class="empty-icon">${icon("mood-happy")}</div>
        <p class="empty-title">${t("No problems in the last 48 hours")}</p>
        <p class="empty-subtitle text-secondary">${t("Failed, skipped, missed or interrupted broadcasts will appear here.")}</p></div>`}
    </div>`;
  }

  function updateCountdown() {
    const el = $("[data-countdown]", root);
    if (!el) return;
    const secs = (new Date(el.dataset.countdown).getTime() - serverNow().getTime()) / 1000;
    el.textContent = secs > 0 ? fmtCountdown(secs) : t("now");
    if (secs <= -5) { clearTimeout(timer); load(); }
  }

  on(root, "click", "[data-action=retry]", () => { clearTimeout(timer); load(); });
  const unsub = onBroadcast((b) => { if (b.finished && data) { clearTimeout(timer); load(); } });
  tick = setInterval(updateCountdown, 1000);
  await load();
  return () => { clearTimeout(timer); clearInterval(tick); unsub(); };
}
