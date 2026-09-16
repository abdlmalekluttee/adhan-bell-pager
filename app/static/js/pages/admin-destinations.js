// Administration › Speakers & zones: the extensions both modules page, and named groups of them.

import { api } from "../api.js";
import { t, tn } from "../i18n.js";
import {
  $, $$, alertBox, confirmDialog, dirtyTracker, emptyState, errorMessage, errorState, html, icon, on, pageHeader, raw,
  saveBar, setHTML, toast,
} from "../ui.js";

/** Short client-side id. crypto.randomUUID needs a secure context, which a plain-HTTP LAN install is not. */
function newId() {
  if (window.crypto?.randomUUID) return crypto.randomUUID().replace(/-/g, "").slice(0, 10);
  const bytes = new Uint8Array(5);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

const clone = (v) => JSON.parse(JSON.stringify(v));

export default async function adminDestinations(ctx) {
  const { root } = ctx;
  let saved = null;       // last server copy
  let dests = [];         // working copies
  let zones = [];
  let callMode = "sequential";
  let tracker = null;

  async function load() {
    try {
      const data = await api("/api/system/destinations");
      adopt(data);
      renderPage();
    } catch (err) {
      setHTML(root, errorState(err));
    }
  }

  function adopt(data) {
    saved = data;
    dests = clone(data.destinations);
    zones = clone(data.zones);
    callMode = data.call_mode || "sequential";
  }

  function renderPage() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Speakers & zones"),
        description: t("The phones, speakers and paging groups that play the Adhan and school bells, and the zones you choose them by."),
      })}
      <div data-region="errors" aria-live="polite"></div>
      <div data-region="editor"></div>
      ${saveBar()}`);
    renderEditor();
    tracker = dirtyTracker($("[data-region=editor]", root), $(".save-bar", root), {
      onSave: save,
      onDiscard: () => { adopt(saved); clearErrors(); renderEditor(); },
    });
  }

  function renderEditor() {
    setHTML($("[data-region=editor]", root), html`
      <div class="row row-cards">
        <div class="col-12">${speakersCard()}</div>
        <div class="col-12">${zonesCard()}</div>
      </div>`);
  }

  // ---- speakers ------------------------------------------------------------
  function zonesUsing(id) {
    return zones.filter((z) => z.destination_ids.includes(id)).map((z) => z.name || t("Unnamed zone"));
  }

  function speakersCard() {
    return html`<div class="card">
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Speakers and paging groups")}</h2>
          <p class="card-subtitle mb-0">${t("Each line is one number the system calls. It can be a single phone or speaker, or a paging group on your phone system that rings many speakers at once.")}</p>
        </div>
        <div class="card-actions">
          <button type="button" class="btn btn-primary" data-action="add-dest">${icon("plus")} ${t("Add speaker")}</button>
        </div>
      </div>
      ${dests.length ? html`<div class="table-responsive">
        <table class="table card-table table-vcenter table-editor">
          <thead><tr>
            <th>${t("Name")}</th>
            <th>${t("Extension or SIP address")}</th>
            <th class="w-1">${t("On")}</th>
            <th class="w-1"><span class="visually-hidden">${t("Actions")}</span></th>
          </tr></thead>
          <tbody>${dests.map((d, i) => speakerRow(d, i))}</tbody>
        </table>
      </div>
      <div class="card-footer text-secondary small">
        ${icon("info-circle")} ${t("Speakers that are turned off are never called, even when a zone includes them.")}
      </div>` : emptyState({
        iconName: "speakerphone",
        title: t("No speakers yet"),
        text: t("Add the extension of a speaker, phone or paging group. Nothing can play until at least one is added."),
        action: html`<button type="button" class="btn btn-primary" data-action="add-dest">${icon("plus")} ${t("Add speaker")}</button>`,
      })}
    </div>`;
  }

  function speakerRow(d, i) {
    const used = zonesUsing(d.id);
    const nameId = `dest-name-${d.id}`;
    const uriId = `dest-uri-${d.id}`;
    return html`<tr data-dest="${d.id}">
      <td style="min-width:11rem">
        <label class="visually-hidden" for="${nameId}">${t("Name")}</label>
        <input id="${nameId}" class="form-control" data-field="name" value="${d.name}" maxlength="80"
          placeholder="${t("For example: Block A classrooms")}" autocomplete="off">
        <div class="invalid-feedback">${t("Enter a name.")}</div>
        ${used.length ? html`<div class="form-hint mt-1">${t("In zones: {names}", { names: used.join(", ") })}</div>` : ""}
      </td>
      <td style="min-width:9rem">
        <label class="visually-hidden" for="${uriId}">${t("Extension or SIP address")}</label>
        <input id="${uriId}" class="form-control ltr" data-field="uri" value="${d.uri}" maxlength="200"
          placeholder="601" autocomplete="off" autocapitalize="off" spellcheck="false">
        <div class="invalid-feedback" data-feedback="uri">${t("Enter an extension without spaces.")}</div>
      </td>
      <td>
        <label class="form-check form-switch mb-0">
          <input class="form-check-input" type="checkbox" data-field="enabled" ${d.enabled ? raw("checked") : ""}
            aria-label="${t("{name} is turned on", { name: d.name || t("Speaker {n}", { n: i + 1 }) })}">
        </label>
      </td>
      <td>
        <button type="button" class="btn btn-icon btn-ghost-danger" data-action="del-dest"
          aria-label="${t("Delete {name}", { name: d.name || t("Speaker {n}", { n: i + 1 }) })}">${icon("trash")}</button>
      </td>
    </tr>`;
  }

  // ---- zones ---------------------------------------------------------------
  function modeName(mode) {
    return mode === "parallel" ? t("All at once") : t("One after another");
  }

  function zonesCard() {
    return html`<div class="card">
      <div class="card-header">
        <div>
          <h2 class="card-title">${t("Zones")}</h2>
          <p class="card-subtitle mb-0">${t("A zone is a named set of speakers, such as “Classrooms” or “Prayer hall”. You pick zones to decide where bells and the Adhan play. Wherever no zone is picked, every speaker that is turned on is used.")}</p>
        </div>
        <div class="card-actions">
          <button type="button" class="btn btn-outline-secondary" data-action="add-zone" ${dests.length ? "" : raw("disabled")}>${icon("plus")} ${t("Add zone")}</button>
        </div>
      </div>
      ${zones.length ? html`<div class="list-group list-group-flush">${zones.map(zoneItem)}</div>` : emptyState({
        iconName: "layout-grid",
        title: t("No zones yet"),
        text: dests.length
          ? t("Without zones, every broadcast plays on all speakers that are turned on. Add a zone to play some things only in certain places.")
          : t("Add speakers first, then group them into zones."),
      })}
    </div>`;
  }

  function zoneItem(z, i) {
    const nameId = `zone-name-${z.id}`;
    const modeId = `zone-mode-${z.id}`;
    const label = z.name || t("Zone {n}", { n: i + 1 });
    return html`<div class="list-group-item py-3" data-zone="${z.id}">
      <div class="row g-2 align-items-end">
        <div class="col-12 col-md">
          <label class="form-label" for="${nameId}">${t("Zone name")}</label>
          <input id="${nameId}" class="form-control" data-zfield="name" value="${z.name}" maxlength="80"
            placeholder="${t("For example: Classrooms")}" autocomplete="off">
          <div class="invalid-feedback" data-feedback="zone-name">${t("Enter a name.")}</div>
        </div>
        <div class="col-8 col-md-4">
          <label class="form-label" for="${modeId}">${t("Call order")}</label>
          <select id="${modeId}" class="form-select" data-zfield="mode">
            <option value="" ${!z.mode ? raw("selected") : ""}>${t("Use default ({mode})", { mode: modeName(callMode) })}</option>
            <option value="sequential" ${z.mode === "sequential" ? raw("selected") : ""}>${t("One after another")}</option>
            <option value="parallel" ${z.mode === "parallel" ? raw("selected") : ""}>${t("All at once")}</option>
          </select>
        </div>
        <div class="col-4 col-md-auto text-end">
          <button type="button" class="btn btn-ghost-danger" data-action="del-zone" aria-label="${t("Delete zone {name}", { name: label })}">
            ${icon("trash")} <span class="d-none d-sm-inline">${t("Delete")}</span></button>
        </div>
      </div>
      <fieldset class="mt-3">
        <legend class="form-label">${t("Speakers in this zone")}</legend>
        <div class="row g-1">
          ${dests.map((d) => html`<div class="col-12 col-sm-6 col-lg-4">
            <label class="form-check mb-1">
              <input class="form-check-input" type="checkbox" data-member="${d.id}" ${z.destination_ids.includes(d.id) ? raw("checked") : ""}>
              <span class="form-check-label" data-member-label="${d.id}">${d.name || t("Unnamed speaker")}<span class="text-secondary small ltr ms-1">${d.uri}</span>${d.enabled ? "" : html`<span class="badge bg-secondary-lt ms-1">${t("Off")}</span>`}</span>
            </label></div>`)}
        </div>
        <div class="form-hint text-warning mt-1" data-empty-zone ${z.destination_ids.length ? raw("hidden") : ""}>
          ${icon("alert-triangle")} ${t("No speakers picked. Anything sent to this zone will not play anywhere.")}
        </div>
      </fieldset>
    </div>`;
  }

  // ---- editing -------------------------------------------------------------
  const destOf = (el) => dests.find((d) => d.id === el.closest("[data-dest]")?.dataset.dest);
  const zoneOf = (el) => zones.find((z) => z.id === el.closest("[data-zone]")?.dataset.zone);

  on(root, "input", "[data-field]", (e, el) => {
    const d = destOf(el);
    if (!d) return;
    if (el.dataset.field === "enabled") d.enabled = el.checked;
    else d[el.dataset.field] = el.value;
    el.classList.remove("is-invalid");
    syncSpeakerLabels(d);
  });
  on(root, "change", "[data-field=enabled]", (e, el) => { const d = destOf(el); if (d) { d.enabled = el.checked; syncSpeakerLabels(d); } });

  // Speaker names appear in every zone's member list; update those labels in place while typing.
  function syncSpeakerLabels(d) {
    $$(`[data-member-label="${d.id}"]`, root).forEach((el) => {
      setHTML(el, html`${d.name || t("Unnamed speaker")}<span class="text-secondary small ltr ms-1">${d.uri}</span>${d.enabled ? "" : html`<span class="badge bg-secondary-lt ms-1">${t("Off")}</span>`}`);
    });
  }
  on(root, "input", "[data-zfield=name]", (e, el) => { const z = zoneOf(el); if (z) z.name = el.value; el.classList.remove("is-invalid"); });
  on(root, "change", "[data-zfield=mode]", (e, el) => { const z = zoneOf(el); if (z) z.mode = el.value || null; });
  on(root, "change", "[data-member]", (e, el) => {
    const z = zoneOf(el);
    if (!z) return;
    const id = el.dataset.member;
    z.destination_ids = el.checked ? [...new Set([...z.destination_ids, id])] : z.destination_ids.filter((x) => x !== id);
    const warn = el.closest("[data-zone]").querySelector("[data-empty-zone]");
    if (warn) warn.hidden = z.destination_ids.length > 0;
  });
  function rerender(focusSelector) {
    renderEditor();
    $(focusSelector || "[data-action=add-dest]", root)?.focus();
  }

  on(root, "click", "[data-action=add-dest]", () => {
    const d = { id: newId(), name: "", uri: "", enabled: true };
    dests.push(d);
    tracker?.set(true);
    rerender(`#dest-name-${d.id}`);
  });

  on(root, "click", "[data-action=del-dest]", async (e, el) => {
    const d = destOf(el);
    if (!d) return;
    const used = zonesUsing(d.id);
    if (used.length) {
      const ok = await confirmDialog({
        title: t("Remove “{name}”?", { name: d.name || d.uri || t("this speaker") }),
        message: t("It is also taken out of these zones: {names}. Nothing is saved until you click Save changes.", { names: used.join(", ") }),
        confirmLabel: t("Remove"), danger: true,
      });
      if (!ok) return;
    }
    dests = dests.filter((x) => x.id !== d.id);
    zones.forEach((z) => { z.destination_ids = z.destination_ids.filter((x) => x !== d.id); });
    tracker?.set(true);
    rerender();
  });

  on(root, "click", "[data-action=add-zone]", () => {
    const z = { id: newId(), name: "", destination_ids: [], mode: null };
    zones.push(z);
    tracker?.set(true);
    rerender(`#zone-name-${z.id}`);
  });

  on(root, "click", "[data-action=del-zone]", async (e, el) => {
    const z = zoneOf(el);
    if (!z) return;
    const ok = await confirmDialog({
      title: t("Delete the zone “{name}”?", { name: z.name || t("Unnamed zone") }),
      message: t("The speakers stay. If a bell schedule uses this zone, saving is refused until you change that schedule. If the Adhan uses only this zone, it will play on every speaker afterwards."),
      confirmLabel: t("Delete zone"), danger: true,
    });
    if (!ok) return;
    zones = zones.filter((x) => x.id !== z.id);
    tracker?.set(true);
    rerender();
  });

  on(root, "click", "[data-action=retry]", load);

  // ---- save ----------------------------------------------------------------
  function clearErrors() { setHTML($("[data-region=errors]", root), ""); }

  function showErrors(messages) {
    setHTML($("[data-region=errors]", root), alertBox("danger",
      messages.length === 1 ? messages[0] : html`<ul class="mb-0 ps-3">${messages.map((m) => html`<li>${m}</li>`)}</ul>`,
      { title: t("Changes not saved") }));
    $("[data-region=errors]", root).scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function validate() {
    const problems = [];
    let first = null;
    const mark = (el) => { if (el) { el.classList.add("is-invalid"); first = first || el; } };
    $$(".is-invalid", root).forEach((el) => el.classList.remove("is-invalid"));
    dests.forEach((d, i) => {
      const row = root.querySelector(`[data-dest="${d.id}"]`);
      const label = d.name.trim() || t("Speaker {n}", { n: i + 1 });
      if (!d.name.trim()) { problems.push(t("Speaker {n} needs a name.", { n: i + 1 })); mark(row?.querySelector("[data-field=name]")); }
      const uri = d.uri.trim();
      if (!uri) { problems.push(t("“{name}” needs an extension or SIP address.", { name: label })); mark(row?.querySelector("[data-field=uri]")); }
      else if (/\s/.test(uri)) { problems.push(t("The extension of “{name}” contains spaces.", { name: label })); mark(row?.querySelector("[data-field=uri]")); }
      else if (/[<>"']/.test(uri)) { problems.push(t("The extension of “{name}” contains characters that are not allowed.", { name: label })); mark(row?.querySelector("[data-field=uri]")); }
    });
    const seen = new Set();
    zones.forEach((z, i) => {
      const item = root.querySelector(`[data-zone="${z.id}"]`);
      const input = item?.querySelector("[data-zfield=name]");
      const name = z.name.trim();
      if (!name) { problems.push(t("Zone {n} needs a name.", { n: i + 1 })); mark(input); return; }
      if (seen.has(name.toLowerCase())) { problems.push(t("Two zones are called “{name}”. Zone names must be different.", { name })); mark(input); }
      seen.add(name.toLowerCase());
    });
    return { problems, first };
  }

  async function save() {
    clearErrors();
    const { problems, first } = validate();
    if (problems.length) {
      showErrors(problems);
      first?.focus();
      throw new Error(t("Some fields need attention."));
    }
    const body = {
      destinations: dests.map((d) => ({ id: d.id, name: d.name.trim(), uri: d.uri.trim(), enabled: !!d.enabled })),
      zones: zones.map((z) => ({ id: z.id, name: z.name.trim(), destination_ids: z.destination_ids.filter((id) => dests.some((d) => d.id === id)), mode: z.mode || null })),
    };
    try {
      const data = await api("/api/system/destinations", { method: "PUT", body });
      adopt(data);
      renderEditor();
      toast(t("Speakers and zones saved. {speakers} and {zones}.", {
        speakers: tn(data.destinations.length, "{n} speaker", "{n} speakers"),
        zones: tn(data.zones.length, "{n} zone", "{n} zones"),
      }));
    } catch (err) {
      const msg = err.code === "zone_in_use"
        ? t("A zone you removed is still used by a bell schedule. Change those schedules to another zone first, then save again.")
        : errorMessage(err);
      showErrors(err.code === "zone_in_use" ? [msg, errorMessage(err)] : [msg]);
      throw err;
    }
  }

  await load();
}
