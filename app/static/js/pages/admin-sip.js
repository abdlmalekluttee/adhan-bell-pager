// Administration › Phone system: SIP registration status, account, codecs and call behaviour.

import { api } from "../api.js";
import { fmtAgo, fmtCountdown, fmtDuration, t, tn } from "../i18n.js";
import {
  $, alertBox, confirmDialog, errorState, field, formValues, html, icon, on, pageHeader, raw, selectField,
  setHTML, switchField, toast, toastError, withBusy,
} from "../ui.js";

/** Friendly codec names; the technical id is shown next to them. */
function codecName(id) {
  const base = String(id).split("/")[0].toLowerCase();
  const names = {
    g722: t("G.722 wideband (16 kHz)"),
    pcma: t("G.711 A-law"),
    pcmu: t("G.711 µ-law"),
    opus: t("Opus"),
    speex: t("Speex"),
    ilbc: t("iLBC"),
    gsm: t("GSM"),
    g729: t("G.729"),
  };
  return names[base] || id;
}

function codecHint(id) {
  const base = String(id).split("/")[0].toLowerCase();
  return {
    g722: t("Best sound quality for the Adhan when your phone system supports it."),
    pcma: t("Standard phone quality. Works almost everywhere (common outside North America)."),
    pcmu: t("Standard phone quality. Works almost everywhere (common in North America)."),
  }[base] || "";
}

export default async function adminSip(ctx) {
  const { root } = ctx;
  let data = null;
  let codecs = [];              // [{id, enabled}] in priority order
  let statusTimer = null;
  let tick = null;
  let retryAt = null;
  let disposed = false;

  async function load() {
    try {
      data = await api("/api/system/sip");
      codecs = buildCodecList(data);
      renderPage();
      scheduleStatus();
    } catch (err) {
      setHTML(root, errorState(err));
    }
  }

  function buildCodecList(d) {
    const pri = d.codecs?.priorities || {};
    const ids = [...new Set([...(d.available_codecs || []).map((c) => c.id), ...Object.keys(pri)])];
    const order = new Map(ids.map((id, i) => [id, i]));
    return ids
      .map((id) => ({ id, prio: Number(pri[id] ?? 0) }))
      .sort((a, b) => (b.prio > 0) - (a.prio > 0) || b.prio - a.prio || order.get(a.id) - order.get(b.id))
      .map((c) => ({ id: c.id, enabled: c.prio > 0 }));
  }

  function renderPage() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Phone system"),
        description: t("How this server signs in to your phone system (PBX) to call the speakers. Changes here affect both the Adhan and school bells."),
      })}
      <div class="row row-cards">
        <div class="col-12" data-region="status">${statusCard()}</div>
        <div class="col-lg-7" data-region="account">${accountCard()}</div>
        <div class="col-lg-5">
          <div class="row row-cards">
            <div class="col-12" data-region="codecs">${codecsCard()}</div>
            <div class="col-12" data-region="call">${callCard()}</div>
          </div>
        </div>
      </div>`);
    updateTick();
  }

  // ---- status --------------------------------------------------------------
  function stateOf(s) {
    if (s.simulated) return { key: "simulated", label: t("Simulation"), dot: "status-azure", icon: "flask",
      text: t("No real calls are made. Broadcasts are simulated so the system can be tried safely.") };
    if (!s.available) return { key: "unavailable", label: t("Not installed"), dot: "status-red", icon: "plug-off",
      text: s.detail ? t(s.detail) : t("The phone system component (PJSIP) is not installed on this server.") };
    if (!s.enabled) return { key: "disabled", label: t("Turned off"), dot: "status-secondary", icon: "phone-off",
      text: t("The connection to the phone system is turned off. Nothing will play until it is turned on.") };
    if (s.registered) return { key: "registered", label: t("Connected"), dot: "status-green", icon: "phone-check",
      text: t("Signed in to the phone system. Broadcasts can go out.") };
    return { key: "error", label: t("Not connected"), dot: "status-red", icon: "phone-x", text: t(s.message || "") };
  }

  function statusCard() {
    const s = data.status;
    const st = stateOf(s);
    const failing = !s.registered && s.enabled && s.available && !s.simulated;
    const cells = [
      { label: t("Signed in as"), value: html`<span class="ltr">${s.identity}</span>`,
        detail: html`<span class="text-uppercase">${s.transport}</span> · ${t("port {port}", { port: s.port })}` },
      { label: t("Last connected"), value: s.last_registered_at ? fmtAgo(s.last_registered_at) : t("never"),
        detail: s.registered && s.expires ? t("Renews every {time}", { time: fmtDuration(s.expires) }) : (s.last_attempt_at ? t("Last attempt {ago}", { ago: fmtAgo(s.last_attempt_at) }) : "") },
      failing ? { label: t("Next attempt"), value: html`<span class="tabular" data-retry>${s.next_retry_in !== null && s.next_retry_in !== undefined ? fmtCountdown(s.next_retry_in) : "—"}</span>`,
        detail: s.consecutive_failures ? tn(s.consecutive_failures, "{n} failed attempt in a row", "{n} failed attempts in a row") : "" }
        : { label: t("Calls in progress"), value: html`<span class="tabular">${s.active_calls}</span>`, detail: "" },
    ];
    return html`<div class="card">
      <div class="card-body">
        <div class="d-flex flex-wrap align-items-center gap-3">
          <span class="avatar avatar-md ${st.key === "registered" ? "bg-green-lt" : st.key === "simulated" ? "bg-azure-lt" : st.key === "disabled" ? "bg-secondary-lt" : "bg-red-lt"}">${icon(st.icon)}</span>
          <div class="me-auto">
            <div class="h2 mb-0 d-flex align-items-center"><span class="status-dot ${st.dot} ${failing ? "status-dot-animated" : ""} me-2"></span>${st.label}</div>
            <div class="text-secondary" role="status" aria-live="polite">${st.text}</div>
            ${failing && s.reg_code ? html`<div class="small text-secondary mt-1">${t("Phone system reply: {code} {reason}", { code: s.reg_code, reason: s.reg_reason || "" })}</div>` : ""}
          </div>
          <button type="button" class="btn btn-outline-secondary" data-action="reconnect" ${s.available ? "" : raw("disabled")}>${icon("refresh")} ${t("Reconnect now")}</button>
        </div>
      </div>
      <div class="status-grid border-top">
        ${cells.map((c) => html`<div class="status-cell">
          <div class="stat-label">${c.label}</div>
          <div class="stat-value">${c.value}</div>
          ${c.detail ? html`<div class="stat-detail">${c.detail}</div>` : ""}
        </div>`)}
      </div>
    </div>`;
  }

  function applyStatus(s) {
    const unchanged = JSON.stringify(s) === JSON.stringify(data.status);
    data.status = s;
    retryAt = s.next_retry_in !== null && s.next_retry_in !== undefined ? Date.now() + s.next_retry_in * 1000 : null;
    const region = $("[data-region=status]", root);
    if (region && !unchanged) setHTML(region, statusCard());
  }

  function scheduleStatus() {
    clearTimeout(statusTimer);
    if (disposed) return;
    statusTimer = setTimeout(refreshStatus, 5000);
  }

  async function refreshStatus() {
    if (!document.hidden) {
      try {
        const d = await api("/api/system/sip", { timeout: 8000 });
        applyStatus(d.status);
      } catch { /* keep the last known status; the next poll retries */ }
    }
    scheduleStatus();
  }

  function updateTick() {
    const el = $("[data-retry]", root);
    if (el && retryAt) el.textContent = fmtCountdown(Math.max(0, (retryAt - Date.now()) / 1000));
  }

  on(root, "click", "[data-action=reconnect]", async (e, el) => {
    if (data.status.active_calls > 0) {
      const ok = await confirmDialog({
        title: t("Reconnect now?"),
        message: t("A broadcast is playing. Reconnecting ends it on every speaker."),
        confirmLabel: t("Reconnect"), danger: true,
      });
      if (!ok) return;
    }
    try {
      const r = await withBusy(el, () => api("/api/system/sip/reconnect", { method: "POST", body: {} }));
      applyStatus(r.status);
      toast(r.status.registered ? t("Reconnected to the phone system.") : t("Reconnecting. The status updates in a few seconds."), r.status.registered ? "success" : "info");
      scheduleStatus();
    } catch (err) {
      toastError(err);
    }
  });

  // ---- account -------------------------------------------------------------
  function accountCard() {
    const s = data.sip;
    return html`<form class="card" data-form="account" novalidate autocomplete="off">
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Phone system account")}</h2>
          <p class="card-subtitle mb-0">${t("Use the extension your phone system administrator created for this server.")}</p>
        </div>
      </div>
      <div class="card-body">
        ${switchField({ label: t("Connect to the phone system"), name: "enabled", checked: s.enabled,
          hint: t("Turn off to stop all calls, for example during PBX maintenance.") })}
        <div class="row">
          ${field({ label: t("Server address"), name: "registrar", value: s.registrar, required: true, ltr: true, cls: "col-md-6",
            hint: t("The phone system's name or IP address, for example pbx.school.local."), attrs: 'pattern="\\S+" autocapitalize="off" spellcheck="false"' })}
          ${field({ label: t("Port"), name: "port", type: "number", value: s.port, required: true, dataType: "int", cls: "col-6 col-md-3",
            attrs: 'min="1" max="65535"' })}
          ${selectField({ label: t("Transport"), name: "transport", value: s.transport, cls: "col-6 col-md-3",
            options: [{ value: "udp", label: "UDP" }, { value: "tcp", label: "TCP" }, { value: "tls", label: "TLS" }] })}
        </div>
        <div class="row">
          ${field({ label: t("Extension (username)"), name: "username", value: s.username, required: true, ltr: true, cls: "col-md-6",
            attrs: 'pattern="\\S+" autocapitalize="off" spellcheck="false"' })}
          ${field({ label: t("Authentication ID"), name: "auth_user", value: s.auth_user, ltr: true, cls: "col-md-6",
            hint: t("Usually the same as the extension."), attrs: 'autocapitalize="off" spellcheck="false"' })}
        </div>
        <div class="row">
          <div class="col-md-6 mb-3">
            <label class="form-label" for="sip-password">${t("Password")}
              ${s.has_password ? html`<span class="badge bg-green-lt ms-2">${icon("lock")} ${t("Saved")}</span>` : ""}</label>
            <input id="sip-password" class="form-control" type="password" name="password" value="" autocomplete="new-password"
              placeholder="${s.has_password ? t("Unchanged") : ""}">
            <div class="form-hint">${s.has_password ? t("Leave blank to keep the current password.") : t("No password is saved yet.")}</div>
            ${s.has_password ? html`<label class="form-check mt-2 mb-0">
              <input class="form-check-input" type="checkbox" name="clear_password" data-action="clear-password">
              <span class="form-check-label">${t("Remove saved password")}</span></label>` : ""}
          </div>
          ${field({ label: t("Caller name"), name: "display_name", value: s.display_name, cls: "col-md-6",
            hint: t("Shown on phones that display who is calling.") })}
        </div>
        <details class="mt-2">
          <summary class="fw-semibold" style="cursor:pointer">${t("Advanced")}</summary>
          <div class="pt-3">
            <div class="row">
              ${field({ label: t("Outbound proxy"), name: "proxy", value: s.proxy, ltr: true, cls: "col-md-6",
                hint: t("Only if your provider asks for one, as host or host:port."), attrs: 'autocapitalize="off" spellcheck="false"' })}
              ${field({ label: t("Registration expiry (seconds)"), name: "register_expires", type: "number", value: s.register_expires, dataType: "int",
                cls: "col-md-6", required: true, attrs: 'min="60" max="3600"', hint: t("How often the server renews its sign-in (60–3600).") })}
            </div>
            <div class="row">
              ${field({ label: t("Local SIP port"), name: "local_port", type: "number", value: s.local_port, dataType: "int", cls: "col-md-4",
                required: true, attrs: 'min="0" max="65535"', hint: t("0 lets the server pick a free port.") })}
              ${field({ label: t("First audio (RTP) port"), name: "rtp_port_start", type: "number", value: s.rtp_port_start, dataType: "int",
                cls: "col-6 col-md-4", required: true, attrs: 'min="1024" max="65000"' })}
              ${field({ label: t("Number of audio ports"), name: "rtp_port_range", type: "number", value: s.rtp_port_range, dataType: "int",
                cls: "col-6 col-md-4", required: true, attrs: 'min="10" max="10000"' })}
            </div>
            <p class="form-hint mt-n2" data-rtp-hint>${rtpHint(s.rtp_port_start, s.rtp_port_range)}</p>
            ${switchField({ label: t("Verify the phone system's TLS certificate"), name: "tls_verify_server", checked: s.tls_verify_server,
              hint: t("Only used with TLS. Turn on when the PBX has a trusted certificate.") })}
          </div>
        </details>
      </div>
      <div class="card-footer d-flex align-items-center gap-2 flex-wrap">
        <span class="text-warning small me-auto" data-dirty hidden>${icon("point-filled")} ${t("Unsaved changes")}</span>
        <button type="submit" class="btn btn-primary ms-auto">${icon("device-floppy")} ${t("Save and reconnect")}</button>
      </div>
    </form>`;
  }

  function rtpHint(start, range) {
    const s = Number(start) || 0;
    const n = Number(range) || 0;
    return t("Audio uses UDP ports {first}–{last}. Allow them through any firewall between this server and the phone system.", { first: s, last: s + Math.max(0, n - 1) });
  }

  on(root, "input", "[data-form=account] [name=rtp_port_start], [data-form=account] [name=rtp_port_range]", () => {
    const f = $("[data-form=account]", root);
    const hint = $("[data-rtp-hint]", f);
    if (hint) hint.textContent = rtpHint(f.rtp_port_start.value, f.rtp_port_range.value);
  });

  on(root, "change", "[data-action=clear-password]", (e, el) => {
    const input = el.form.password;
    input.disabled = el.checked;
    if (el.checked) input.value = "";
  });

  // Dirty markers for the three forms.
  const markDirty = (e) => {
    const form = e.target.closest("form[data-form]");
    const badge = form && $("[data-dirty]", form);
    if (badge) badge.hidden = false;
  };
  on(root, "input", "form[data-form] input, form[data-form] select, form[data-form] textarea", markDirty);
  on(root, "change", "form[data-form] input, form[data-form] select", markDirty);

  function invalid(form) {
    if (form.checkValidity()) return false;
    form.classList.add("was-validated");
    const bad = form.querySelector(":invalid");
    const details = bad?.closest("details");
    if (details) details.open = true;
    bad?.focus();
    toast(t("Some fields need attention."), "warning");
    return true;
  }

  on(root, "submit", "form[data-form=account]", async (e, form) => {
    e.preventDefault();
    if (invalid(form)) return;
    const v = formValues(form);
    const ok = await confirmDialog({
      title: t("Save and reconnect?"),
      message: t("The server signs in to the phone system again with these settings. Any broadcast in progress ends, and nothing can play for a few seconds."),
      confirmLabel: t("Save and reconnect"),
    });
    if (!ok) return;
    const body = {
      enabled: v.enabled, registrar: v.registrar.trim(), port: v.port, transport: v.transport, username: v.username.trim(),
      auth_user: (v.auth_user || "").trim() || v.username.trim(), display_name: v.display_name, proxy: (v.proxy || "").trim(),
      register_expires: v.register_expires, local_port: v.local_port, rtp_port_start: v.rtp_port_start,
      rtp_port_range: v.rtp_port_range, tls_verify_server: v.tls_verify_server,
    };
    if (v.clear_password) body.clear_password = true;
    else if (v.password) body.password = v.password;
    try {
      const r = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/system/sip", { method: "PUT", body }));
      data.sip = r.sip;
      setHTML($("[data-region=account]", root), accountCard());
      applyStatus(r.status);
      toast(t("Phone system settings saved. Reconnecting…"));
      scheduleStatus();
    } catch (err) {
      toastError(err);
    }
  });

  // ---- codecs --------------------------------------------------------------
  function codecsCard() {
    const known = new Set((data.available_codecs || []).map((c) => c.id));
    const enabledCount = codecs.filter((c) => c.enabled).length;
    let pos = 0;
    return html`<form class="card" data-form="codecs" novalidate>
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Audio codecs")}</h2>
          <p class="card-subtitle mb-0">${t("The phone system picks the first codec in this list that it also supports. Put the best quality first.")}</p>
        </div>
      </div>
      <ul class="list-group list-group-flush" aria-label="${t("Codecs in order of preference")}">
        ${codecs.map((c, i) => {
          if (c.enabled) pos += 1;
          const name = codecName(c.id);
          const hint = codecHint(c.id);
          return html`<li class="list-group-item py-2" data-codec="${c.id}">
            <div class="d-flex align-items-center gap-2">
              <span class="badge ${c.enabled ? "bg-primary-lt" : "bg-secondary-lt"} tabular" style="min-width:1.75rem">${c.enabled ? pos : "–"}</span>
              <div class="flex-fill ${c.enabled ? "" : "text-secondary"}">
                <div class="fw-semibold">${name}</div>
                <div class="small text-secondary"><span class="ltr">${c.id}</span>${known.size && !known.has(c.id) ? html` · <span class="text-warning">${t("Not available on this server")}</span>` : ""}</div>
                ${hint && c.enabled ? html`<div class="small text-secondary">${hint}</div>` : ""}
              </div>
              <div class="btn-group" role="group">
                <button type="button" class="btn btn-icon btn-ghost-secondary" data-action="codec-up" ${i === 0 ? raw("disabled") : ""}
                  aria-label="${t("Move {name} up", { name })}">${icon("chevron-up")}</button>
                <button type="button" class="btn btn-icon btn-ghost-secondary" data-action="codec-down" ${i === codecs.length - 1 ? raw("disabled") : ""}
                  aria-label="${t("Move {name} down", { name })}">${icon("chevron-down")}</button>
              </div>
              <label class="form-check form-switch mb-0 ms-1">
                <input class="form-check-input" type="checkbox" data-action="codec-toggle" ${c.enabled ? raw("checked") : ""}
                  aria-label="${t("Use {name}", { name })}">
              </label>
            </div>
          </li>`;
        })}
      </ul>
      <div class="card-body border-top py-3">
        ${!enabledCount ? alertBox("warning", t("Turn on at least one codec.")) : ""}
        <p class="form-hint mb-0">${icon("info-circle")} ${t("G.729 cannot be used to send audio from this server. If your phones only support G.729, let the phone system convert the audio (pass-through only).")}</p>
      </div>
      <div class="card-footer d-flex align-items-center gap-2 flex-wrap">
        <span class="text-warning small me-auto" data-dirty ${raw(codecsDirty ? "" : "hidden")}>${icon("point-filled")} ${t("Unsaved changes")}</span>
        <button type="submit" class="btn btn-primary ms-auto" ${enabledCount ? "" : raw("disabled")}>${icon("device-floppy")} ${t("Save codecs")}</button>
      </div>
    </form>`;
  }

  let codecsDirty = false;
  function rerenderCodecs(focusSel) {
    codecsDirty = true;
    setHTML($("[data-region=codecs]", root), codecsCard());
    if (focusSel) {
      const el = $(focusSel, root);
      const row = el?.closest("[data-codec]");
      (el && !el.disabled ? el : row?.querySelector("button:not([disabled])"))?.focus();
    }
  }

  on(root, "click", "[data-action=codec-up], [data-action=codec-down]", (e, el) => {
    const id = el.closest("[data-codec]").dataset.codec;
    const i = codecs.findIndex((c) => c.id === id);
    const j = el.dataset.action === "codec-up" ? i - 1 : i + 1;
    if (j < 0 || j >= codecs.length) return;
    [codecs[i], codecs[j]] = [codecs[j], codecs[i]];
    rerenderCodecs(`[data-codec="${CSS.escape(id)}"] [data-action=${el.dataset.action}]`);
  });

  on(root, "change", "[data-action=codec-toggle]", (e, el) => {
    const id = el.closest("[data-codec]").dataset.codec;
    const c = codecs.find((x) => x.id === id);
    if (!c) return;
    c.enabled = el.checked;
    rerenderCodecs(`[data-codec="${CSS.escape(id)}"] [data-action=codec-toggle]`);
  });

  on(root, "submit", "form[data-form=codecs]", async (e, form) => {
    e.preventDefault();
    const enabled = codecs.filter((c) => c.enabled);
    if (!enabled.length) { toast(t("Turn on at least one codec."), "warning"); return; }
    const priorities = {};
    let p = 250;
    codecs.forEach((c) => {
      if (c.enabled) { priorities[c.id] = Math.max(1, p); p -= 10; } else priorities[c.id] = 0;
    });
    const ok = await confirmDialog({
      title: t("Save codecs and reconnect?"),
      message: t("The server reconnects to the phone system to use the new codec order. Any broadcast in progress ends."),
      confirmLabel: t("Save and reconnect"),
    });
    if (!ok) return;
    try {
      const r = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/system/codecs", { method: "PUT", body: { priorities } }));
      data.codecs = r.codecs;
      codecs = buildCodecList(data);
      codecsDirty = false;
      setHTML($("[data-region=codecs]", root), codecsCard());
      applyStatus(r.status);
      toast(t("Codec order saved."));
      scheduleStatus();
    } catch (err) {
      toastError(err);
    }
  });

  // ---- call behaviour ------------------------------------------------------
  function callCard() {
    const c = data.call;
    return html`<form class="card" data-form="call" novalidate>
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Call behaviour")}</h2>
          <p class="card-subtitle mb-0">${t("Defaults for every broadcast. Zones can choose their own call order.")}</p>
        </div>
      </div>
      <div class="card-body">
        <div class="mb-3">
          <div class="form-label">${t("Call order")}</div>
          <label class="form-check">
            <input class="form-check-input" type="radio" name="mode" value="sequential" ${c.mode === "sequential" ? raw("checked") : ""}>
            <span class="form-check-label">${t("One after another")}</span>
            <span class="form-check-description">${t("Calls each speaker in turn. Gentle on small phone systems.")}</span>
          </label>
          <label class="form-check mb-0">
            <input class="form-check-input" type="radio" name="mode" value="parallel" ${c.mode === "parallel" ? raw("checked") : ""}>
            <span class="form-check-label">${t("All at once")}</span>
            <span class="form-check-description">${t("Calls every speaker together so they play in step. Needs enough lines on the phone system.")}</span>
          </label>
        </div>
        ${numberField("ring_timeout", t("Ring for up to"), c.ring_timeout, 5, 120, t("seconds"), t("How long to wait for a speaker to answer (5–120)."))}
        ${numberField("answer_delay_ms", t("Wait after answer before audio"), c.answer_delay_ms, 0, 10000, t("ms"), t("Auto-answer speakers often need a moment before they play sound. 1000–2000 ms is typical (0–10000)."))}
        ${numberField("hangup_after_eof_ms", t("Pause after audio before hanging up"), c.hangup_after_eof_ms, 0, 10000, t("ms"), t("Stops the end of the sound being cut off (0–10000)."))}
        ${numberField("max_call_seconds", t("Maximum call length"), c.max_call_seconds, 30, 3600, t("seconds"), t("A safety limit: calls are ended after this long (30–3600)."))}
      </div>
      <div class="card-footer d-flex align-items-center gap-2 flex-wrap">
        <span class="text-warning small me-auto" data-dirty hidden>${icon("point-filled")} ${t("Unsaved changes")}</span>
        <button type="submit" class="btn btn-primary ms-auto">${icon("device-floppy")} ${t("Save call behaviour")}</button>
      </div>
    </form>`;
  }

  function numberField(name, label, value, min, max, unit, hint) {
    const id = `call-${name}`;
    return html`<div class="mb-3">
      <label class="form-label" for="${id}">${label}</label>
      <div class="input-group">
        <input id="${id}" class="form-control tabular" type="number" name="${name}" value="${value}" min="${min}" max="${max}" step="1" required data-type="int">
        <span class="input-group-text">${unit}</span>
      </div>
      <div class="form-hint">${hint}</div>
    </div>`;
  }

  on(root, "submit", "form[data-form=call]", async (e, form) => {
    e.preventDefault();
    if (invalid(form)) return;
    const v = formValues(form);
    try {
      const r = await withBusy(form.querySelector("button[type=submit]"), () => api("/api/system/call", { method: "PUT", body: {
        mode: v.mode, ring_timeout: v.ring_timeout, answer_delay_ms: v.answer_delay_ms,
        hangup_after_eof_ms: v.hangup_after_eof_ms, max_call_seconds: v.max_call_seconds } }));
      data.call = r.call;
      setHTML($("[data-region=call]", root), callCard());
      toast(t("Call behaviour saved. It applies from the next broadcast."));
    } catch (err) {
      toastError(err);
    }
  });

  on(root, "click", "[data-action=retry]", load);

  tick = setInterval(updateTick, 1000);
  await load();
  return () => { disposed = true; clearTimeout(statusTimer); clearInterval(tick); };
}
