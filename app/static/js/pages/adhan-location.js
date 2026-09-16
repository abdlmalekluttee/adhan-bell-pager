// Adhan › Location: pick a city (offline list) or enter coordinates; the location sets the timezone too.

import { api } from "../api.js";
import { getLang, t } from "../i18n.js";
import { can, setMe } from "../store.js";
import {
  $, alertBox, debounce, errorMessage, errorState, html, icon, on, pageHeader, raw, setHTML, skeleton, toast, toastError,
  withBusy,
} from "../ui.js";
import { coordText, methodLabel, placeName } from "./adhan-today.js";

export default async function adhanLocation(ctx) {
  const { root } = ctx;
  const canEdit = can("adhan.schedule.edit");
  const canSystem = can("system.settings.manage");
  const ar = getLang() === "ar";

  let settings = null;          // GET /api/adhan/settings
  let countries = [];
  let timezones = [];
  // Editor state
  let mode = "city";            // "city" | "coords"
  let countryCode = "";
  let query = "";
  let cities = [];
  let citiesState = "idle";     // idle | loading | ready | error
  let recommended = "";
  let picked = null;            // chosen city record
  let lat = "";
  let lng = "";
  let tz = "";
  let saveError = null;
  let searchSeq = 0;

  setHTML(root, html`
    ${pageHeader({
      title: t("Location"),
      description: t("Prayer times are calculated for this place. Its timezone is also the clock that both the Adhan and the school bells follow."),
    })}
    <div data-host>${skeleton(6)}</div>`);
  const host = $("[data-host]", root);

  async function load() {
    try {
      const [s, c, tzs] = await Promise.all([
        api("/api/adhan/settings"),
        canEdit ? api("/api/geo/countries") : Promise.resolve({ countries: [] }),
        canEdit || canSystem ? api("/api/system/timezones").catch(() => ({ timezones: [] })) : Promise.resolve({ timezones: [] }),
      ]);
      settings = s;
      countries = c.countries.slice().sort((a, b) => countryLabel(a).localeCompare(countryLabel(b), ar ? "ar" : "en"));
      timezones = tzs.timezones || [];
      resetEditor();
      render();
      if (canEdit && mode === "city" && countryCode) searchCities();
    } catch (err) {
      setHTML(host, errorState(err));
    }
  }

  function resetEditor() {
    const loc = settings.location;
    mode = loc.use_manual ? "coords" : "city";
    countryCode = loc.country_code || "";
    query = "";
    cities = [];
    citiesState = "idle";
    recommended = "";
    picked = !loc.use_manual && loc.city ? {
      id: loc.city_id, name: loc.city, local_name: loc.city_local_name, region: loc.region,
      lat: loc.latitude, lng: loc.longitude, tz: loc.timezone,
    } : null;
    lat = String(loc.latitude);
    lng = String(loc.longitude);
    tz = loc.timezone;
    saveError = null;
  }

  function countryLabel(c) { return (ar && c.local_name) || c.name; }
  function cityLabel(c) { return (ar && c.local_name) || c.name; }
  function flag(code) {
    return /^[A-Za-z]{2}$/.test(code || "") ? html`<span class="fi fi-${code.toLowerCase()}" aria-hidden="true"></span>` : "";
  }

  // ------------------------------------------------------------------------
  function render() {
    setHTML(host, html`<div class="row row-cards">
      <div class="col-lg-8">${canEdit ? editorCard() : readOnlyNote()}</div>
      <div class="col-lg-4">${summaryCard()}</div>
    </div>`);
  }

  function readOnlyNote() {
    return html`<div class="card"><div class="card-body">
      ${alertBox("info", t("You can see the saved location. Changing it needs the “Edit Adhan schedule” permission."))}
      <p class="text-secondary mb-0">${t("The location decides the prayer times and the timezone used by the whole system.")}</p>
    </div></div>`;
  }

  function summaryCard() {
    const loc = settings.location;
    const place = placeName(loc);
    const manualSource = settings.prayer.source === "manual";
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Saved location")}</h2></div>
      <div class="list-group list-group-flush">
        <div class="list-group-item">
          <div class="small text-secondary">${loc.use_manual ? t("Entered as coordinates") : t("Chosen from the city list")}</div>
          <div class="fw-semibold">${place ? html`${flag(loc.country_code)} ${place}` : t("Custom coordinates")}</div>
        </div>
        <div class="list-group-item">
          <div class="small text-secondary">${t("Latitude, longitude")}</div>
          <div class="ltr tabular">${coordText(loc.latitude, loc.longitude)}</div>
        </div>
        <div class="list-group-item">
          <div class="small text-secondary">${t("Timezone")}</div>
          <div class="ltr fw-semibold">${loc.timezone}</div>
        </div>
        <div class="list-group-item">
          <div class="small text-secondary">${t("Calculation method")}</div>
          <div>${methodLabel(settings.prayer.method)}</div>
        </div>
      </div>
      ${manualSource ? html`<div class="card-body border-top">
        <p class="small text-secondary mb-0">${icon("info-circle")} ${t("Prayer times come from an uploaded timetable right now, so the location only sets the timezone.")}
          <a href="#/adhan/schedule">${t("Prayer schedule")}</a></p>
      </div>` : ""}
    </div>`;
  }

  function editorCard() {
    const option = (value, title, text, iconName) => html`<label class="form-selectgroup-item flex-fill">
      <input type="radio" name="loc-mode" value="${value}" class="form-selectgroup-input" ${mode === value ? raw("checked") : ""}>
      <div class="form-selectgroup-label d-flex align-items-center p-3 h-100">
        <div class="me-3"><span class="form-selectgroup-check"></span></div>
        <div class="text-start"><div class="fw-semibold">${icon(iconName)} ${title}</div><div class="small text-secondary">${text}</div></div>
      </div>
    </label>`;
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Change the location")}</h2></div>
      <div class="card-body">
        <div class="form-selectgroup form-selectgroup-boxes d-flex flex-column flex-md-row gap-2 mb-4" role="radiogroup" aria-label="${t("How to set the location")}">
          ${option("city", t("Choose a city"), t("Pick your country and city from the built-in list."), "building-community")}
          ${option("coords", t("Enter coordinates"), t("Type the exact latitude and longitude, for example from a map."), "map-pin")}
        </div>
        <div data-city-panel ${mode === "city" ? "" : raw("hidden")}>${cityPanel()}</div>
        <div data-coords-panel ${mode === "coords" ? "" : raw("hidden")}>${coordsPanel()}</div>
        <div data-tz-note>${tzNote()}</div>
        <div data-save-error aria-live="assertive">${saveErrorBox()}</div>
      </div>
      <div class="card-footer d-flex flex-wrap gap-2 align-items-center">
        <button type="button" class="btn btn-link link-secondary" data-action="reset">${t("Undo changes")}</button>
        <button type="button" class="btn btn-primary ms-auto" data-action="save">${icon("device-floppy")} ${t("Save location")}</button>
      </div>
    </div>`;
  }

  function cityPanel() {
    const current = countries.find((c) => c.code === countryCode);
    return html`
      <div class="row g-3">
        <div class="col-md-5">
          <label class="form-label" for="loc-country">${t("Country")}</label>
          <div class="input-group">
            <span class="input-group-text" data-country-flag>${current ? flag(current.code) : icon("world")}</span>
            <select id="loc-country" class="form-select" data-country>
              <option value="" ${countryCode ? "" : raw("selected")}>${t("Choose a country")}</option>
              ${countries.map((c) => html`<option value="${c.code}" ${c.code === countryCode ? raw("selected") : ""}>${countryLabel(c)}</option>`)}
            </select>
          </div>
        </div>
        <div class="col-md-7">
          <label class="form-label" for="loc-search">${t("City")}</label>
          <div class="input-icon">
            <span class="input-icon-addon">${icon("search")}</span>
            <input id="loc-search" class="form-control" type="search" data-city-search autocomplete="off" value="${query}"
              placeholder="${t("Search for a city")}" ${countryCode ? "" : raw("disabled")}>
          </div>
        </div>
      </div>
      <div class="mt-3" data-picked>${pickedLine()}</div>
      <div class="mt-2" data-city-results>${cityResults()}</div>
      <div class="mt-3" data-method-hint>${methodHint()}</div>`;
  }

  function pickedLine() {
    if (!picked) return "";
    return html`<div class="d-flex align-items-center gap-2 flex-wrap">
      <span class="badge bg-primary-lt">${icon("check")} ${t("Selected")}</span>
      <span class="fw-semibold">${cityLabel(picked)}</span>
      <span class="small text-secondary ltr tabular">${coordText(picked.lat, picked.lng)} · ${picked.tz}</span>
    </div>`;
  }

  function cityResults() {
    if (!countryCode) return html`<p class="text-secondary small mb-0">${t("Choose a country first, then search for your city.")}</p>`;
    if (citiesState === "loading") return html`<div class="text-secondary small py-2"><span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>${t("Searching…")}</div>`;
    if (citiesState === "error") return alertBox("danger", t("The city list could not be loaded. Try again, or enter coordinates instead."));
    if (citiesState !== "ready") return "";
    if (!cities.length) {
      return html`<p class="text-secondary small mb-0">${query ? t("No city matches “{q}”. Try another spelling, or enter coordinates instead.", { q: query }) : t("No cities are listed for this country. Enter coordinates instead.")}</p>`;
    }
    return html`<div class="small text-secondary mb-1">${query ? t("Matching cities") : t("Largest cities. Type to search for others.")}</div>
      <div class="list-group" style="max-height:18rem;overflow-y:auto" role="listbox" aria-label="${t("Cities")}">
        ${cities.map((c) => {
          const active = picked && String(picked.id) === String(c.id);
          return html`<button type="button" class="list-group-item list-group-item-action d-flex align-items-center gap-2 ${active ? "active" : ""}"
              data-action="pick-city" data-id="${c.id}" role="option" aria-selected="${active ? "true" : "false"}">
            <span class="flex-fill text-start">
              <span class="fw-semibold">${cityLabel(c)}</span>${ar && c.local_name && c.local_name !== c.name ? html` <span class="text-secondary ltr">${c.name}</span>` : ""}
              <span class="d-block small text-secondary ltr text-start">${coordText(c.lat, c.lng)} · ${c.tz}</span>
            </span>
            ${active ? icon("check") : ""}
          </button>`;
        })}
      </div>`;
  }

  function methodHint() {
    if (!recommended || !countryCode) return "";
    const country = countries.find((c) => c.code === countryCode);
    const name = country ? countryLabel(country) : countryCode;
    const current = settings.prayer.method;
    if (recommended === current) {
      return html`<p class="small text-secondary mb-0">${icon("circle-check", "text-green")} ${t("You already use the calculation method usually followed in {country}: {method}.", { country: name, method: methodLabel(current) })}</p>`;
    }
    return html`<div class="alert alert-info mb-0" role="status">
      <div class="d-flex flex-wrap gap-2 align-items-center">
        <div class="flex-fill">${t("Mosques in {country} usually follow “{method}”. You use “{current}”.", { country: name, method: methodLabel(recommended), current: methodLabel(current) })}</div>
        <button type="button" class="btn btn-sm btn-outline-primary" data-action="apply-method">${t("Use {method}", { method: methodLabel(recommended) })}</button>
      </div>
    </div>`;
  }

  function coordsPanel() {
    const tzOptions = timezones.length ? timezones : [tz];
    return html`<div class="row g-3">
      <div class="col-sm-6 col-md-4">
        <label class="form-label" for="loc-lat">${t("Latitude")}</label>
        <input id="loc-lat" class="form-control ltr tabular" type="number" step="any" min="-90" max="90" inputmode="decimal" data-lat value="${lat}">
        <div class="invalid-feedback">${t("Enter a latitude from -90 to 90.")}</div>
        <div class="form-hint">${t("North is positive, south is negative.")}</div>
      </div>
      <div class="col-sm-6 col-md-4">
        <label class="form-label" for="loc-lng">${t("Longitude")}</label>
        <input id="loc-lng" class="form-control ltr tabular" type="number" step="any" min="-180" max="180" inputmode="decimal" data-lng value="${lng}">
        <div class="invalid-feedback">${t("Enter a longitude from -180 to 180.")}</div>
        <div class="form-hint">${t("East is positive, west is negative.")}</div>
      </div>
      <div class="col-md-4">
        <label class="form-label" for="loc-tz">${t("Timezone")}</label>
        <select id="loc-tz" class="form-select ltr" data-tz>
          ${tzOptions.map((z) => html`<option value="${z}" ${z === tz ? raw("selected") : ""}>${z}</option>`)}
        </select>
      </div>
    </div>`;
  }

  function resultingTz() {
    return mode === "city" ? (picked ? picked.tz : settings.location.timezone) : tz;
  }

  function tzNote() {
    const next = resultingTz();
    const current = settings.location.timezone;
    if (!next || next === current) return "";
    const text = canSystem
      ? t("This changes the timezone from {old} to {new}. The timezone is shared by the Adhan and the school bells, so bell times will follow the new clock too.", { old: current, new: next })
      : t("This location is in a different timezone ({new}). Changing the timezone affects the Adhan and the school bells, so it needs the “System settings” permission. Ask an administrator.", { new: next });
    return html`<div class="mt-3">${alertBox("warning", text)}</div>`;
  }

  function saveErrorBox() {
    if (!saveError) return "";
    if (saveError.code === "timezone_permission") {
      return html`<div class="mt-3">${alertBox("danger",
        t("This location uses the timezone {tz}, which is different from the current one. The timezone affects both the Adhan and the school bells, so changing it needs the “System settings” permission. Ask an administrator to save this location, or choose a place in the current timezone.", { tz: saveError.detail.timezone || resultingTz() }),
        { title: t("Timezone change not allowed") })}</div>`;
    }
    return html`<div class="mt-3">${alertBox("danger", errorMessage(saveError))}</div>`;
  }

  function refresh(part) {
    const map = {
      picked: ["[data-picked]", pickedLine], results: ["[data-city-results]", cityResults], method: ["[data-method-hint]", methodHint],
      tz: ["[data-tz-note]", tzNote], error: ["[data-save-error]", saveErrorBox],
    };
    const [sel, fn] = map[part];
    const el = $(sel, host);
    if (el) setHTML(el, fn());
  }

  // ------------------------------------------------------------------------
  async function searchCities() {
    if (!countryCode) return;
    const seq = ++searchSeq;
    citiesState = "loading";
    refresh("results");
    try {
      const r = await api("/api/geo/cities", { query: { country: countryCode, q: query.trim(), limit: 50 } });
      if (seq !== searchSeq) return;
      cities = r.cities || [];
      recommended = r.recommended_method || "";
      citiesState = "ready";
    } catch {
      if (seq !== searchSeq) return;
      citiesState = "error";
    }
    refresh("results");
    refresh("method");
  }
  const debouncedSearch = debounce(searchCities, 300);

  function readCoords() {
    const latEl = $("[data-lat]", host);
    const lngEl = $("[data-lng]", host);
    const la = Number(latEl.value);
    const lo = Number(lngEl.value);
    const latOk = latEl.value.trim() !== "" && Number.isFinite(la) && la >= -90 && la <= 90;
    const lngOk = lngEl.value.trim() !== "" && Number.isFinite(lo) && lo >= -180 && lo <= 180;
    latEl.classList.toggle("is-invalid", !latOk);
    lngEl.classList.toggle("is-invalid", !lngOk);
    if (!latOk) { latEl.focus(); return null; }
    if (!lngOk) { lngEl.focus(); return null; }
    return { la, lo };
  }

  async function save(btn) {
    saveError = null;
    refresh("error");
    let body;
    if (mode === "city") {
      if (!picked) {
        saveError = new Error(t("Choose a city from the list first."));
        refresh("error");
        return;
      }
      const country = countries.find((c) => c.code === countryCode) || {};
      body = {
        use_manual: false, country_code: countryCode, country_name: country.name || "", country_local_name: country.local_name || "",
        city: picked.name, city_id: picked.id ? String(picked.id) : "", city_local_name: picked.local_name || "", region: picked.region || "",
        latitude: picked.lat, longitude: picked.lng, timezone: picked.tz,
      };
    } else {
      const c = readCoords();
      if (!c) return;
      body = {
        use_manual: true, country_code: "", country_name: "", country_local_name: "", city: "", city_id: "",
        city_local_name: "", region: "", latitude: c.la, longitude: c.lo, timezone: tz,
      };
    }
    try {
      const r = await withBusy(btn, () => api("/api/adhan/location", { method: "PUT", body }));
      const tzChanged = r.location.timezone !== settings.location.timezone;
      settings.location = r.location;
      if (tzChanged) {
        try { setMe(await api("/api/auth/me")); } catch { /* the clock catches up on the next page load */ }
      }
      if (mode === "city" && r.location.use_manual) {
        toast(t("The city could not be found in the list, so its coordinates were saved instead."), "warning");
      } else {
        toast(t("Location saved. Prayer times now use {place}.", { place: placeName(r.location) || coordText(r.location.latitude, r.location.longitude) }));
      }
      await load();
    } catch (err) {
      saveError = err;
      refresh("error");
      $("[data-save-error]", host)?.scrollIntoView({ block: "nearest" });
    }
  }

  // ------------------------------------------------------------------------
  on(root, "click", "[data-action=retry]", () => { setHTML(host, skeleton(6)); load(); });
  on(root, "change", "[name=loc-mode]", (e, el) => {
    mode = el.value;
    $("[data-city-panel]", host).hidden = mode !== "city";
    $("[data-coords-panel]", host).hidden = mode !== "coords";
    saveError = null;
    refresh("error");
    refresh("tz");
    if (mode === "city" && countryCode && citiesState === "idle") searchCities();
  });
  on(root, "change", "[data-country]", (e, el) => {
    countryCode = el.value;
    query = "";
    picked = null;
    cities = [];
    recommended = "";
    citiesState = "idle";
    const search = $("[data-city-search]", host);
    search.value = "";
    search.disabled = !countryCode;
    setHTML($("[data-country-flag]", host), countryCode ? flag(countryCode) : icon("world"));
    refresh("picked");
    refresh("results");
    refresh("method");
    refresh("tz");
    if (countryCode) { searchCities(); search.focus(); }
  });
  on(root, "input", "[data-city-search]", (e, el) => { query = el.value; debouncedSearch(); });
  on(root, "keydown", "[data-city-search]", (e) => { if (e.key === "Enter") e.preventDefault(); });
  on(root, "click", "[data-action=pick-city]", (e, el) => {
    picked = cities.find((c) => String(c.id) === el.dataset.id) || null;
    saveError = null;
    refresh("picked");
    refresh("results");
    refresh("tz");
    refresh("error");
  });
  on(root, "input", "[data-lat], [data-lng]", (e, el) => {
    el.classList.remove("is-invalid");
    if (el.matches("[data-lat]")) lat = el.value; else lng = el.value;
  });
  on(root, "change", "[data-tz]", (e, el) => { tz = el.value; refresh("tz"); });
  on(root, "click", "[data-action=save]", (e, el) => save(el));
  on(root, "click", "[data-action=reset]", () => { resetEditor(); render(); if (mode === "city" && countryCode) searchCities(); });
  on(root, "click", "[data-action=apply-method]", async (e, el) => {
    try {
      const r = await withBusy(el, () => api("/api/adhan/settings", { method: "PUT", body: { prayer: { method: recommended } } }));
      settings.prayer = r.prayer;
      toast(t("Calculation method changed to {method}.", { method: methodLabel(r.prayer.method) }));
      refresh("method");
      const summary = host.querySelector(".col-lg-4");
      if (summary) setHTML(summary, summaryCard());
    } catch (err) {
      toastError(err);
    }
  });

  await load();
  return () => { searchSeq++; };
}
