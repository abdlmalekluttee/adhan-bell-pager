// Administration › HTTPS: Let's Encrypt certificate through DNS validation (Cloudflare or cPanel), renewal status.

import { api } from "../api.js";
import { fmtAgo, fmtDate, fmtDateTime, t, tn } from "../i18n.js";
import {
  $, alertBox, confirmDialog, dirtyTracker, errorMessage, errorState, field, formValues, html, icon, on, pageHeader,
  raw, saveBar, selectField, setHTML, switchField, toast, toastError, withBusy,
} from "../ui.js";

const STATE = {
  off: { label: "Off", cls: "bg-secondary-lt", dot: "status-secondary" },
  no_certificate: { label: "No certificate", cls: "bg-yellow-lt", dot: "status-yellow" },
  active: { label: "Active", cls: "bg-green-lt", dot: "status-green" },
  expiring: { label: "Expiring soon", cls: "bg-orange-lt", dot: "status-orange" },
  expired: { label: "Expired", cls: "bg-red-lt", dot: "status-red" },
  renewal_failing: { label: "Renewal failing", cls: "bg-orange-lt", dot: "status-orange" },
  listener_error: { label: "Not serving", cls: "bg-red-lt", dot: "status-red" },
};

function dnsTip(code) {
  return {
    auth: t("Check the token. For Cloudflare it needs Zone → DNS → Edit and Zone → Zone → Read for your domain. For cPanel, create the token under Security › Manage API Tokens."),
    zone_not_found: t("The host name must be inside a domain managed by this provider account."),
    unreachable: t("This server must be able to reach the provider over the internet (outbound HTTPS, port 443 or 2083 for cPanel)."),
    tls_error: t("The provider's certificate was not trusted. For cPanel, use the server's real host name in the address."),
    dns: t("Make sure the domain's name servers are the ones of the selected provider."),
    validation_failed: t("Let's Encrypt looks the record up in public DNS. Make sure the domain's public name servers are the ones of the selected provider."),
    rate_limited: t("Use the staging service while testing; it has much higher limits."),
  }[code] || "";
}

export default async function adminHttps(ctx) {
  const { root } = ctx;
  let data = null;
  let tracker = null;
  let poll = null;
  let alive = true;

  async function load() {
    try {
      data = await api("/api/system/https");
      renderPage();
      schedulePoll();
    } catch (err) {
      setHTML(root, errorState(err));
    }
  }

  function renderPage() {
    setHTML(root, html`
      ${pageHeader({
        title: t("HTTPS"),
        description: t("Serve this app on a secure https:// address with a free Let's Encrypt certificate that renews itself."),
      })}
      <div class="row row-cards">
        <div class="col-lg-4 order-lg-2">${howItWorksCard()}</div>
        <div class="col-lg-8 order-lg-1">
          <div class="row row-cards">
            <div class="col-12" data-region="status">${statusCard()}</div>
            <div class="col-12">
              <form class="card" data-form="settings" novalidate autocomplete="off">
                <div class="card-header"><h2 class="card-title">${t("Settings")}</h2></div>
                <div class="card-body" data-region="settings">${settingsFields()}</div>
                <div data-region="test-result" aria-live="polite"></div>
              </form>
            </div>
          </div>
        </div>
      </div>
      ${saveBar()}`);
    tracker = dirtyTracker($("form[data-form=settings] [data-region=settings]", root), $(".save-bar", root), {
      onSave: save,
      onDiscard: () => { setHTML($("[data-region=settings]", root), settingsFields()); setHTML($("[data-region=test-result]", root), ""); },
    });
  }

  // ---- status ----------------------------------------------------------------
  function statusCard() {
    const s = data.status;
    const c = data.config;
    const cert = s.certificate;
    const job = s.job;
    const running = job && job.running;
    const st = STATE[s.state] || STATE.off;
    const url = cert && c.hostnames.length ? `https://${c.hostnames.find((h) => !h.startsWith("*.")) || ""}${data.https_port === 443 ? "" : ":" + data.https_port}/` : "";
    const actions = [];
    if (!running) {
      actions.push(html`<button type="button" class="btn btn-primary" data-action="request" ${s.ready_problems.length ? raw("disabled") : ""}>
        ${icon(cert ? "refresh" : "certificate")} ${cert ? t("Renew now") : t("Request certificate")}</button>`);
      if (cert) actions.push(html`<button type="button" class="btn btn-ghost-danger" data-action="remove">${icon("trash")} ${t("Remove certificate")}</button>`);
    }
    return html`<div class="card">
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Certificate")}</h2>
          <p class="card-subtitle mb-0"><span class="status-dot ${st.dot} me-1"></span>${stateText(s)}</p>
        </div>
        <div class="card-actions"><span class="badge ${st.cls}">${t(st.label)}</span></div>
      </div>
      <div class="card-body">
        ${cert ? certDetails(cert, url, s) : html`<div class="d-flex gap-3 align-items-start">
          <div>${icon("lock-off", "fs-1 text-secondary")}</div>
          <div><div class="fw-semibold">${t("No certificate yet")}</div>
          <div class="text-secondary">${t("Fill in the settings below, save them, then request a certificate. It usually takes one to three minutes.")}</div></div>
        </div>`}
        ${s.listener.error ? html`<div class="mt-3">${alertBox("danger", t(s.listener.error), { title: t("HTTPS is not being served") })}</div>` : ""}
        ${renewalNote(s)}
        ${job ? jobProgress(job) : ""}
        ${!running && s.ready_problems.length ? html`<div class="mt-3 small text-secondary">${icon("info-circle")} ${s.ready_problems.map((p) => t(p)).join(" ")}</div>` : ""}
      </div>
      ${actions.length ? html`<div class="card-footer d-flex flex-wrap gap-2">${actions}</div>` : ""}
    </div>`;
  }

  function stateText(s) {
    const cert = s.certificate;
    return {
      off: cert ? t("HTTPS is turned off. The certificate is kept, but it is not renewed while HTTPS is off.") : t("HTTPS is turned off."),
      no_certificate: t("HTTPS is on, but there is no certificate yet."),
      active: t("Serving HTTPS on port {port}.", { port: s.listener.port }),
      expiring: t("The certificate expires in {n} days and has not been renewed.", { n: cert?.days_left ?? 0 }),
      expired: t("The certificate has expired. Browsers will show a warning."),
      renewal_failing: t("Renewal keeps failing. The current certificate expires in {n} days.", { n: cert?.days_left ?? 0 }),
      listener_error: t("A certificate is installed, but HTTPS could not be started."),
    }[s.state] || "";
  }

  function certDetails(cert, url, s) {
    return html`
      ${cert.test_ca ? alertBox("warning", t("This certificate is from a test service. Browsers will not trust it. Switch to Let's Encrypt and request a new certificate when your tests are done."), { title: t("Test certificate") }) : ""}
      ${!cert.covers_hostnames ? alertBox("warning", t("The host names in the settings are not all covered by this certificate. Request a new certificate to include them.")) : ""}
      <dl class="row mb-0">
        <dt class="col-sm-4">${t("Names")}</dt>
        <dd class="col-sm-8">${cert.names.map((n) => html`<span class="badge bg-secondary-lt ltr me-1 mb-1">${n}</span>`)}</dd>
        <dt class="col-sm-4">${t("Valid until")}</dt>
        <dd class="col-sm-8 tabular">${fmtDate(cert.not_after)} <span class="text-secondary">· ${cert.expired ? t("expired") : tn(cert.days_left, "{n} day left", "{n} days left")}</span></dd>
        <dt class="col-sm-4">${t("Renews automatically after")}</dt>
        <dd class="col-sm-8 tabular">${fmtDate(cert.renew_at)}</dd>
        <dt class="col-sm-4">${t("Issued by")}</dt>
        <dd class="col-sm-8"><span class="ltr">${cert.issuer}</span></dd>
        ${s.listener.running && url ? html`<dt class="col-sm-4">${t("Secure address")}</dt>
          <dd class="col-sm-8"><a class="ltr" href="${url}" target="_blank" rel="noopener">${url}</a></dd>` : ""}
      </dl>`;
  }

  function renewalNote(s) {
    const r = s.renewal;
    if (!r.last_error || (s.job && s.job.running)) return "";
    const next = r.next_attempt_at ? t("Next attempt {when}.", { when: fmtDateTime(r.next_attempt_at) }) : "";
    const tip = dnsTip(r.last_error.code);
    return html`<div class="mt-3">${alertBox("warning", html`<div>${t(r.last_error.message)}</div>
      ${tip ? html`<div class="mt-1">${tip}</div>` : ""}
      <div class="mt-1 small text-secondary">${tn(r.failures, "Failed {n} time in a row, last {ago}.", "Failed {n} times in a row, last {ago}.", { ago: fmtAgo(r.last_error.at) })} ${next}</div>`,
      { title: t("The last certificate request failed") })}</div>`;
  }

  function jobProgress(job) {
    const stepIcon = (st) => st === "running" ? html`<span class="spinner-border spinner-border-sm text-primary" aria-hidden="true"></span>`
      : st === "failed" ? icon("circle-x", "text-danger") : icon("circle-check", "text-green");
    return html`<div class="mt-3 border rounded p-3" aria-live="polite">
      <div class="fw-semibold mb-2">${job.running ? t("Requesting a certificate…") : job.ok ? t("Certificate installed") : t("The request did not finish")}
        <span class="text-secondary fw-normal small ms-1">${job.reason === "renewal" ? t("automatic renewal") : t("started by {name}", { name: job.actor })} · ${fmtDateTime(job.started_at)}</span></div>
      <ul class="list-unstyled mb-0">
        ${job.steps.map((s) => html`<li class="d-flex gap-2 align-items-center py-1">${stepIcon(s.state)}<span>${t(s.text, s.params)}</span></li>`)}
      </ul>
      ${!job.running && job.error ? html`<div class="mt-2">${alertBox("danger", html`<div>${t(job.error.message)}</div>${dnsTip(job.error.code) ? html`<div class="mt-1">${dnsTip(job.error.code)}</div>` : ""}`)}</div>` : ""}
    </div>`;
  }

  function refreshStatus() {
    const region = $("[data-region=status]", root);
    if (region) setHTML(region, statusCard());
  }

  function schedulePoll() {
    clearTimeout(poll);
    if (!alive) return;
    const running = data?.status?.job?.running;
    poll = setTimeout(async () => {
      try {
        const wasRunning = data.status.job?.running;
        data.status = await api("/api/system/https/status");
        refreshStatus();
        if (wasRunning && !data.status.job?.running) {
          if (data.status.job?.ok) toast(t("The certificate is installed."));
          else toast(t("The certificate request failed."), "danger");
        }
      } catch { /* keep the last state; try again */ }
      schedulePoll();
    }, running ? 2000 : 30000);
  }

  on(root, "click", "[data-action=request]", async (e, el) => {
    if (tracker?.isDirty()) {
      toast(t("Save your changes before requesting a certificate."), "warning");
      return;
    }
    const c = data.config;
    const staging = c.ca !== "letsencrypt";
    const ok = await confirmDialog({
      title: data.status.certificate ? t("Renew the certificate now?") : t("Request a certificate?"),
      message: html`${t("The app adds temporary DNS records for {names} through {provider}, waits for them to appear, and removes them afterwards.",
        { names: c.hostnames.join(", "), provider: providerLabel(c.provider) })}
        ${staging ? "" : html`<div class="mt-2">${t("Let's Encrypt limits how many certificates you can get for the same names each week. Use the staging service for repeated tests.")}</div>`}`,
      confirmLabel: data.status.certificate ? t("Renew now") : t("Request certificate"),
    });
    if (!ok) return;
    try {
      const r = await withBusy(el, () => api("/api/system/https/request", { method: "POST" }));
      data.status.job = r.job;
      refreshStatus();
      schedulePoll();
    } catch (err) {
      toastError(err);
    }
  });

  on(root, "click", "[data-action=remove]", async () => {
    const ok = await confirmDialog({
      title: t("Remove the certificate?"),
      message: t("HTTPS stops right away and people must use the http:// address until you request a new certificate. Automatic renewal stops too."),
      confirmLabel: t("Remove certificate"), danger: true,
    });
    if (!ok) return;
    try {
      data = await api("/api/system/https/certificate", { method: "DELETE" });
      refreshStatus();
      toast(t("The certificate was removed."));
    } catch (err) {
      toastError(err);
    }
  });

  // ---- settings --------------------------------------------------------------
  function providerLabel(key) {
    return (data.providers.find((p) => p.key === key) || { label: key }).label;
  }

  function settingsFields() {
    const c = data.config;
    return html`
      <h3 class="mb-2">${t("Address")}</h3>
      <div class="mb-3">
        <label class="form-label form-required" for="https-hosts">${t("Host names")}</label>
        <textarea id="https-hosts" class="form-control ltr" name="hostnames" rows="2" spellcheck="false" autocapitalize="off"
          placeholder="pager.yourschool.com">${c.hostnames.join("\n")}</textarea>
        <div class="form-hint">${t("One per line. Use a name in a public domain you own, for example pager.yourschool.com. People must open the app by this name, so add it to your internal DNS pointing to this server.")}</div>
      </div>

      <h3 class="mb-2 mt-4">${t("DNS provider")}</h3>
      <p class="text-secondary">${t("Let's Encrypt checks that you control the domain by looking for a temporary DNS record. The app creates and removes that record through your DNS provider's API.")}</p>
      ${selectField({ label: t("Provider"), name: "provider", value: c.provider,
        options: data.providers.map((p) => ({ value: p.key, label: p.label })),
        hint: t("Choose where the domain's DNS zone is hosted (where its name servers point).") })}
      <div data-region="provider-fields">${providerFields(c.provider)}</div>
      <div class="d-flex flex-wrap align-items-center gap-3 mb-2">
        <button type="button" class="btn btn-outline-secondary" data-action="test">${icon("plug-connected")} ${t("Test DNS provider")}</button>
        <label class="form-check mb-0" data-no-dirty>
          <input class="form-check-input" type="checkbox" name="write_test" data-no-dirty>
          <span class="form-check-label">${t("Also add and remove a test record")}</span>
        </label>
      </div>
      <div class="form-hint mb-3">${t("Uses the values above, even before you save them.")}</div>

      <h3 class="mb-2 mt-4">${t("Certificate")}</h3>
      <div class="row">
        ${selectField({ label: t("Certificate authority"), name: "ca", value: c.ca, cls: "col-md-6",
          options: data.ca_options.map((o) => ({ value: o.key, label: t(o.label) })),
          hint: t("Use staging while you test: its certificates are not trusted by browsers, but the limits are much higher.") })}
        ${field({ label: t("Email (optional)"), name: "email", value: c.email, type: "email", ltr: true, cls: "col-md-6",
          placeholder: "it@yourschool.com", hint: t("Registered with the certificate authority for account notices.") })}
      </div>
      <div data-region="custom-ca" ${c.ca === "custom" ? "" : raw("hidden")}>
        ${field({ label: t("ACME directory address"), name: "directory_url", value: c.directory_url, ltr: true,
          placeholder: "https://acme.example.com/directory", hint: t("Only for another ACME certificate authority."), attrs: 'spellcheck="false" autocapitalize="off"' })}
      </div>
      <label class="form-check mb-3">
        <input class="form-check-input" type="checkbox" name="agree_tos" ${c.agree_tos ? raw("checked") : ""}>
        <span class="form-check-label">${t("I accept the Let's Encrypt Subscriber Agreement")}
          <a href="${data.tos_url}" target="_blank" rel="noopener" class="ms-1">${t("Read it")} ${icon("external-link")}</a></span>
      </label>

      <h3 class="mb-2 mt-4">${t("Serving")}</h3>
      ${switchField({ label: t("Serve the app over HTTPS on port {port}", { port: data.https_port }), name: "enabled", checked: c.enabled,
        hint: t("The http:// address keeps working as well, so you can always get back in. Allow this port in the server's firewall.") })}
      ${switchField({ label: t("Send people who open the http:// address to https://"), name: "redirect_http", checked: c.redirect_http,
        hint: t("Turn this on only after you have opened the secure address successfully from another computer.") })}
      <div data-region="redirect-warning">${redirectWarning(c.redirect_http)}</div>

      <details>
        <summary class="fw-semibold" style="cursor:pointer">${t("Advanced")}</summary>
        <div class="pt-3 row">
          ${selectField({ label: t("Key type"), name: "key_type", value: c.key_type, cls: "col-md-6",
            options: [{ value: "ecdsa", label: t("ECDSA P-256 (recommended)") }, { value: "rsa", label: t("RSA 2048 (older devices)") }] })}
          ${field({ label: t("Wait for DNS up to (seconds)"), name: "dns_timeout_seconds", type: "number", value: c.dns_timeout_seconds,
            dataType: "int", cls: "col-md-6", required: true, attrs: 'min="30" max="1800"', hint: t("30–1800. Increase it if your provider is slow to publish changes.") })}
        </div>
      </details>`;
  }

  function providerFields(provider) {
    const c = data.config;
    if (provider === "cloudflare") {
      return html`<div class="mb-3">
        <label class="form-label" for="cf-token">${t("Cloudflare API token")}
          ${c.has_cloudflare_token ? html`<span class="badge bg-green-lt ms-2">${icon("lock")} ${t("Saved")}</span>` : ""}</label>
        <input id="cf-token" class="form-control ltr" type="password" name="cloudflare_token" value="" autocomplete="new-password"
          placeholder="${c.has_cloudflare_token ? t("Unchanged") : ""}">
        <div class="form-hint">${t("In Cloudflare: My Profile › API Tokens › Create Token › “Edit zone DNS” template, limited to this domain. Leave blank to keep the saved token.")}</div>
      </div>`;
    }
    if (provider === "cpanel") {
      return html`
        <div class="row">
          ${field({ label: t("cPanel address"), name: "cpanel_url", value: c.cpanel_url, ltr: true, cls: "col-md-7",
            placeholder: "https://cpanel.yourhost.com:2083", hint: t("The address you use to open cPanel, usually on port 2083."), attrs: 'spellcheck="false" autocapitalize="off"' })}
          ${field({ label: t("cPanel username"), name: "cpanel_username", value: c.cpanel_username, ltr: true, cls: "col-md-5",
            attrs: 'spellcheck="false" autocapitalize="off"' })}
        </div>
        <div class="mb-3">
          <label class="form-label" for="cp-token">${t("cPanel API token")}
            ${c.has_cpanel_token ? html`<span class="badge bg-green-lt ms-2">${icon("lock")} ${t("Saved")}</span>` : ""}</label>
          <input id="cp-token" class="form-control ltr" type="password" name="cpanel_token" value="" autocomplete="new-password"
            placeholder="${c.has_cpanel_token ? t("Unchanged") : ""}">
          <div class="form-hint">${t("In cPanel: Security › Manage API Tokens › Create. Leave blank to keep the saved token.")}</div>
        </div>
        ${switchField({ label: t("Check the cPanel server's certificate"), name: "cpanel_verify_tls", checked: c.cpanel_verify_tls,
          hint: t("Keep this on. Turn it off only if the cPanel address has no valid certificate; the token could then be intercepted.") })}`;
    }
    return html`<p class="text-secondary small">${t("No settings are needed for this provider.")}</p>`;
  }

  function redirectWarning(on) {
    return on ? alertBox("warning", t("Everyone who opens the http:// address will be sent to the secure address. If that name does not resolve on your network, they cannot reach the app. To undo from the server, run: python -m app.cli disable-https")) : "";
  }

  on(root, "change", "[data-form=settings] [name=provider]", (e, el) => {
    setHTML($("[data-region=provider-fields]", root), providerFields(el.value));
    setHTML($("[data-region=test-result]", root), "");
  });
  on(root, "change", "[data-form=settings] [name=ca]", (e, el) => {
    $("[data-region=custom-ca]", root).hidden = el.value !== "custom";
  });
  on(root, "change", "[data-form=settings] [name=redirect_http]", (e, el) => {
    setHTML($("[data-region=redirect-warning]", root), redirectWarning(el.checked));
  });

  function readSettings({ forTest = false } = {}) {
    const form = $("form[data-form=settings]", root);
    const v = formValues(form);
    const hostnames = String(v.hostnames || "").split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter(Boolean);
    const problems = [];
    hostnames.forEach((h) => {
      if (/^[\d.]+$/.test(h) || h.includes(":")) problems.push(t("“{name}” is an IP address. Let's Encrypt certificates need a domain name.", { name: h }));
      else if (!/^(\*\.)?([a-z0-9¡-￿]([a-z0-9¡-￿-]{0,61}[a-z0-9¡-￿])?\.)+[a-z¡-￿0-9-]{2,63}$/.test(h)) problems.push(t("“{name}” is not a valid host name.", { name: h }));
    });
    if (!forTest && v.enabled && !hostnames.length) problems.push(t("Add at least one host name to turn HTTPS on."));
    if (forTest && !hostnames.length) problems.push(t("Add at least one host name to test."));
    if (v.provider === "cpanel" && v.cpanel_url && !/^https:\/\//i.test(v.cpanel_url.trim()) && /^[a-z]+:\/\//i.test(v.cpanel_url.trim())) {
      problems.push(t("The cPanel address must use https:// so the API token is never sent unencrypted."));
    }
    if (!form.checkValidity()) {
      form.classList.add("was-validated");
      const bad = form.querySelector(":invalid");
      const details = bad?.closest("details");
      if (details) details.open = true;
      bad?.focus();
      problems.push(t("Some fields need attention."));
    }
    const body = {
      hostnames, provider: v.provider, email: (v.email || "").trim(), ca: v.ca, directory_url: (v.directory_url || "").trim(),
      agree_tos: v.agree_tos, enabled: v.enabled, redirect_http: v.redirect_http, key_type: v.key_type,
      dns_timeout_seconds: v.dns_timeout_seconds,
    };
    if (v.provider === "cpanel") {
      Object.assign(body, { cpanel_url: (v.cpanel_url || "").trim(), cpanel_username: (v.cpanel_username || "").trim(), cpanel_verify_tls: v.cpanel_verify_tls });
      if (v.cpanel_token) body.cpanel_token = v.cpanel_token.trim();
    }
    if (v.provider === "cloudflare" && v.cloudflare_token) body.cloudflare_token = v.cloudflare_token.trim();
    return { body, problems, writeTest: v.write_test };
  }

  async function save() {
    const { body, problems } = readSettings();
    const result = $("[data-region=test-result]", root);
    if (problems.length) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("danger", html`<ul class="mb-0 ps-3">${problems.map((p) => html`<li>${p}</li>`)}</ul>`, { title: t("Settings not saved") })}</div>`);
      throw new Error(problems[0]);
    }
    try {
      data = await api("/api/system/https", { method: "PUT", body });
      setHTML($("[data-region=settings]", root), settingsFields());
      refreshStatus();
      setHTML(result, "");
      toast(t("HTTPS settings saved."));
    } catch (err) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("danger", t(errorMessage(err)), { title: t("Settings not saved") })}</div>`);
      throw err;
    }
  }

  on(root, "click", "[data-action=test]", async (e, el) => {
    const result = $("[data-region=test-result]", root);
    const { body, problems, writeTest } = readSettings({ forTest: true });
    if (problems.length) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("warning", problems.join(" "))}</div>`);
      return;
    }
    setHTML(result, "");
    try {
      const r = await withBusy(el, () => api("/api/system/https/test", { method: "POST", body: { ...body, write_test: writeTest }, timeout: 90000 }));
      if (r.ok) {
        setHTML(result, html`<div class="card-body border-top">${alertBox("success", html`<div>${t(r.message)}</div>
          ${r.zones.length ? html`<ul class="mb-0 mt-1 ps-3">${r.zones.map((z) => html`<li>${t("{name} is in the zone {zone}", { name: z.hostname, zone: z.zone })}</li>`)}</ul>` : ""}`,
          { title: t("DNS provider works") })}</div>`);
      } else {
        const tip = dnsTip(r.code);
        setHTML(result, html`<div class="card-body border-top">${alertBox("danger",
          html`<div>${t(r.message)}</div>${tip ? html`<div class="mt-1">${tip}</div>` : ""}`, { title: t("DNS provider test failed") })}</div>`);
      }
    } catch (err) {
      setHTML(result, html`<div class="card-body border-top">${alertBox("danger", errorMessage(err), { title: t("DNS provider test failed") })}</div>`);
    }
  });

  // ---- explanation -----------------------------------------------------------
  function howItWorksCard() {
    const rules = [
      { i: "world-www", text: t("You need a domain name you own, such as yourschool.com, with its DNS at Cloudflare or in cPanel. Certificates cannot be issued for IP addresses or internal-only names like .local.") },
      { i: "server", text: t("Point the host name to this server in your internal DNS. It does not need to exist in public DNS, and nothing has to be opened to the internet.") },
      { i: "cloud-lock", text: t("This server needs outbound internet access (HTTPS) to reach Let's Encrypt and the DNS provider.") },
      { i: "refresh", text: t("Certificates last up to 90 days. The app renews them automatically after two thirds of that time and retries if something fails. Problems appear on the dashboard.") },
      { i: "key", text: t("API tokens are stored encrypted and never shown again. Give the token access to this one domain's DNS only.") },
      { i: "door-exit", text: t("If you get locked out, the http:// address still works unless you turned on the redirect. Run python -m app.cli disable-https on the server to turn HTTPS off.") },
    ];
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${icon("info-circle", "me-2")}${t("How HTTPS works here")}</h2></div>
      <ul class="list-group list-group-flush">
        ${rules.map((r) => html`<li class="list-group-item d-flex gap-2">${icon(r.i, "text-secondary mt-1")}<span>${r.text}</span></li>`)}
      </ul>
    </div>`;
  }

  await load();
  return () => { alive = false; clearTimeout(poll); };
}
