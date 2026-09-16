// School Bells › Bell sounds: the sound library, browser preview, play on speakers and uploads.

import { api } from "../api.js";
import { fmtDuration, fmtNumber, t, tn } from "../i18n.js";
import { can } from "../store.js";
import {
  $, $$, alertBox, confirmDialog, emptyState, errorState, html, icon, on, pageHeader, raw, setHTML, skeleton, toast, toastError,
  withBusy,
} from "../ui.js";
import { ringDialog } from "./bells-today.js";

const ACCEPT = [".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus"];

function lengthText(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return t("{s} s", { s: fmtNumber(seconds, { maximumFractionDigits: 1 }) });
  return fmtDuration(seconds);
}

function sizeText(bytes) {
  if (bytes >= 1024 * 1024) return t("{n} MB", { n: fmtNumber(bytes / (1024 * 1024), { maximumFractionDigits: 1 }) });
  return t("{n} KB", { n: fmtNumber(Math.max(1, Math.round(bytes / 1024))) });
}

function usedByItem(text) {
  const m = /^(schedule|timetable) '(.*)'$/.exec(text || "");
  if (!m) return html`<span>${text}</span>`;
  return m[1] === "schedule"
    ? html`<a href="#/bells/schedules" class="text-reset text-nowrap">${icon("calendar-week", "text-secondary")} ${m[2]}</a>`
    : html`<span class="text-nowrap">${icon("list-details", "text-secondary")} ${m[2]}</span>`;
}

export default async function bellsSounds(ctx) {
  const { root } = ctx;
  const canManage = can("bells.sounds.manage");
  const canRing = can("bells.broadcast");
  let data = null;
  let uploading = false;
  const player = new Audio();
  let playingName = null;

  function renderFrame() {
    setHTML(root, html`
      ${pageHeader({
        title: t("Bell sounds"),
        description: t("The sounds your bells can play. Listen here in the browser, or play one on the speakers to check the volume. Timetables and weekly schedules choose from this list."),
      })}
      <div class="row row-cards">
        <div class="${canManage ? "col-lg-8" : "col-12"}" data-table>${skeleton(4)}</div>
        ${canManage ? html`<div class="col-lg-4">${uploadCard()}</div>` : ""}
      </div>`);
  }

  function uploadCard() {
    return html`<div class="card">
      <div class="card-header"><h2 class="card-title">${t("Add a bell sound")}</h2></div>
      <div class="card-body">
        <div data-ffmpeg></div>
        <div class="dropzone-lite" data-dropzone>
          <div class="mb-2">${icon("upload", "fs-1 text-secondary")}</div>
          <div class="fw-semibold">${t("Drop a sound file here")}</div>
          <div class="text-secondary small mb-3">${t("or")}</div>
          <button type="button" class="btn btn-outline-secondary" data-action="pick">${icon("file-music")} ${t("Choose a file")}</button>
          <input type="file" class="d-none" data-file accept="${ACCEPT.join(",")}" multiple>
          <div class="small text-secondary mt-3"><span class="ltr">WAV, MP3, M4A, AAC, OGG, FLAC, Opus</span> · ${t("up to 50 MB")}</div>
        </div>
        <div data-upload-status class="mt-3" aria-live="polite"></div>
        <ul class="form-hint ps-3 mt-3 mb-0">
          <li>${t("Keep bells short: 2 to 5 seconds works best. A schedule can repeat a sound or play it for a set time.")}</li>
          <li>${t("Files are converted automatically to the format the phone system needs.")}</li>
          <li>${t("A file with the same name replaces the old one after you confirm.")}</li>
        </ul>
      </div>
    </div>`;
  }

  async function load() {
    try {
      data = await api("/api/bells/sounds");
      renderTable();
    } catch (err) {
      const host = $("[data-table]", root);
      if (host) setHTML(host, errorState(err));
    }
  }

  function renderTable() {
    const host = $("[data-table]", root);
    if (!host) return;
    const ff = $("[data-ffmpeg]", root);
    if (ff) setHTML(ff, data.ffmpeg ? "" : alertBox("warning", t("The audio converter (ffmpeg) is not installed on the server, so only WAV files can be uploaded. Ask your administrator to install it to upload MP3 and other formats.")));
    const files = data.files || [];
    if (!files.length) {
      setHTML(host, html`<div class="card">${emptyState({
        iconName: "music",
        title: t("No bell sounds yet"),
        text: canManage ? t("Upload a short bell or chime to use in your timetables and schedules.") : t("Ask a bells administrator to upload a bell sound."),
      })}</div>`);
      return;
    }
    setHTML(host, html`<div class="card">
      <div class="card-header"><h2 class="card-title">${tn(files.length, "{n} sound", "{n} sounds")}</h2></div>
      <div class="table-responsive">
        <table class="table table-vcenter card-table mb-0">
          <thead><tr>
            <th>${t("Name")}</th><th>${t("Length")}</th><th class="d-none d-md-table-cell">${t("Format")}</th><th>${t("Used by")}</th>
            <th><span class="visually-hidden">${t("Actions")}</span></th>
          </tr></thead>
          <tbody>${files.map(row)}</tbody>
        </table>
      </div>
    </div>`);
    syncPreviewButtons();
  }

  function row(f) {
    const playing = playingName === f.name && !player.paused;
    const long = f.duration && f.duration > 15;
    return html`<tr>
      <td style="min-width:13rem">
        <div class="d-flex flex-wrap align-items-center gap-2">
          <span class="ltr fw-semibold">${f.name}</span>
          ${f.builtin ? html`<span class="badge bg-secondary-lt">${t("Built-in")}</span>` : ""}
          ${f.playable ? "" : html`<span class="badge bg-red-lt">${t("Cannot be played")}</span>`}
        </div>
      </td>
      <td class="text-nowrap tabular">${lengthText(f.duration)}
        ${long ? html`<div class="small text-warning">${t("Long for a bell")}</div>` : ""}</td>
      <td class="d-none d-md-table-cell small text-secondary text-nowrap">
        ${f.rate ? html`${t("{rate} kHz", { rate: fmtNumber(f.rate / 1000, { maximumFractionDigits: 1 }) })} ${f.channels === 1 ? t("mono") : t("stereo")} · ` : ""}${sizeText(f.size)}
      </td>
      <td class="small">${f.used_by?.length
        ? html`<div class="d-flex flex-column gap-1">${f.used_by.map(usedByItem)}</div>`
        : html`<span class="text-secondary">${t("Not used")}</span>`}</td>
      <td class="text-end">
        <div class="btn-list flex-nowrap justify-content-end">
          <button type="button" class="btn btn-ghost-secondary btn-icon" data-action="preview" data-name="${f.name}"
            aria-pressed="${playing ? "true" : "false"}" aria-label="${t("Listen to {name} in the browser", { name: f.name })}" title="${t("Listen in the browser")}"
            ${f.playable ? "" : raw("disabled")}>${icon(playing ? "player-pause" : "player-play")}</button>
          ${canRing ? html`<button type="button" class="btn btn-outline-secondary" data-action="ring" data-name="${f.name}" ${f.playable ? "" : raw("disabled")}
            aria-label="${t("Play {name} on the speakers", { name: f.name })}">${icon("speakerphone")}<span class="d-none d-xl-inline ms-1">${t("Play on speakers")}</span></button>` : ""}
          ${canManage ? html`<button type="button" class="btn btn-ghost-danger btn-icon" data-action="delete" data-name="${f.name}"
            aria-label="${t("Delete {name}", { name: f.name })}" title="${t("Delete")}">${icon("trash")}</button>` : ""}
        </div>
      </td>
    </tr>`;
  }

  // ------------------------------------------------------------------ browser preview (one shared player)
  function syncPreviewButtons() {
    $$("[data-action=preview]", root).forEach((b) => {
      const on_ = b.dataset.name === playingName && !player.paused;
      b.setAttribute("aria-pressed", on_ ? "true" : "false");
      b.innerHTML = icon(on_ ? "player-pause" : "player-play").s;
    });
  }
  player.addEventListener("play", syncPreviewButtons);
  player.addEventListener("pause", syncPreviewButtons);
  player.addEventListener("ended", () => { playingName = null; syncPreviewButtons(); });
  player.addEventListener("error", () => {
    if (!player.getAttribute("src")) return;
    playingName = null;
    syncPreviewButtons();
    toast(t("This sound could not be played in the browser."), "warning");
  });

  function togglePreview(name) {
    if (playingName === name && !player.paused) { player.pause(); return; }
    playingName = name;
    player.src = `/api/bells/sounds/${encodeURIComponent(name)}/file`;
    player.play().catch(() => { playingName = null; syncPreviewButtons(); });
    syncPreviewButtons();
  }

  // ------------------------------------------------------------------ uploads
  function statusHost() { return $("[data-upload-status]", root); }

  function showProgress(name, pct, converting) {
    const host = statusHost();
    if (!host) return;
    setHTML(host, html`<div class="small mb-1 d-flex gap-2">
        <span class="ltr text-truncate">${name}</span>
        <span class="ms-auto text-secondary text-nowrap">${converting ? t("Converting…") : t("{pct}%", { pct })}</span>
      </div>
      <div class="progress progress-sm" role="progressbar" aria-label="${t("Upload progress")}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
        <div class="progress-bar ${converting ? "progress-bar-indeterminate" : ""}" style="width:${converting ? 100 : pct}%"></div>
      </div>`);
  }

  async function uploadOne(file, replace = false) {
    const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    if (!ACCEPT.includes(ext)) {
      toast(t("“{name}” is not a supported sound file. Use WAV, MP3, M4A, AAC, OGG, FLAC or Opus.", { name: file.name }), "warning");
      return false;
    }
    if (data && data.ffmpeg === false && ext !== ".wav") {
      toast(t("“{name}” cannot be converted because ffmpeg is not installed. Upload a WAV file instead.", { name: file.name }), "warning");
      return false;
    }
    const form = new FormData();
    form.append("file", file);
    showProgress(file.name, 0, false);
    try {
      const res = await api(`/api/bells/sounds${replace ? "?replace=true" : ""}`, {
        method: "POST", form, onUploadProgress: (pct) => showProgress(file.name, pct, pct >= 100),
      });
      toast(replace ? t("“{name}” replaced.", { name: res.name }) : t("“{name}” added to the bell sounds.", { name: res.name }));
      return true;
    } catch (err) {
      if (err.status === 409 && err.code === "exists") {
        const name = err.detail?.name || file.name;
        const ok = await confirmDialog({
          title: t("Replace “{name}”?", { name }),
          message: t("A bell sound with this name already exists. Everything that uses it will play the new file."),
          confirmLabel: t("Replace"),
        });
        return ok ? uploadOne(file, true) : false;
      }
      toastError(err, t("Upload failed"));
      return false;
    } finally {
      const host = statusHost();
      if (host) host.innerHTML = "";
    }
  }

  async function uploadFiles(list) {
    const filesToSend = Array.from(list || []);
    if (!filesToSend.length || uploading) return;
    uploading = true;
    const pick = $("[data-action=pick]", root);
    if (pick) pick.disabled = true;
    let any = false;
    try {
      for (const file of filesToSend) {
        // eslint-disable-next-line no-await-in-loop
        if (await uploadOne(file)) any = true;
      }
    } finally {
      uploading = false;
      if (pick) pick.disabled = false;
      const input = $("[data-file]", root);
      if (input) input.value = "";
    }
    if (any) await load();
  }

  // ------------------------------------------------------------------ events
  on(root, "click", "[data-action=retry]", () => { setHTML($("[data-table]", root), skeleton(4)); load(); });
  on(root, "click", "[data-action=preview]", (e, el) => togglePreview(el.dataset.name));
  on(root, "click", "[data-action=ring]", async (e, el) => {
    player.pause();
    try { await ringDialog({ preset: { sound: el.dataset.name } }); } catch (err) { toastError(err); }
  });
  on(root, "click", "[data-action=delete]", async (e, el) => {
    const f = (data?.files || []).find((x) => x.name === el.dataset.name);
    if (!f) return;
    const notes = [];
    if (f.used_by?.length) notes.push(html`<div class="small text-secondary mb-1">${t("It is still used by:")}</div><div class="small d-flex flex-column gap-1 mb-2">${f.used_by.map(usedByItem)}</div>`);
    if (f.builtin) notes.push(html`<div class="small text-secondary">${t("This is a built-in sound. It will not come back on its own.")}</div>`);
    const ok = await confirmDialog({
      title: t("Delete “{name}”?", { name: f.name }),
      message: t("The sound file will be removed from the server. This cannot be undone."),
      details: notes.length ? html`${notes}` : "",
      confirmLabel: t("Delete sound"),
      danger: true,
    });
    if (!ok) return;
    if (playingName === f.name) player.pause();
    try {
      await withBusy(el, () => api(`/api/bells/sounds/${encodeURIComponent(f.name)}`, { method: "DELETE" }));
      toast(t("“{name}” deleted.", { name: f.name }));
      await load();
    } catch (err) {
      toastError(err);
    }
  });

  if (canManage) {
    on(root, "click", "[data-action=pick]", () => $("[data-file]", root)?.click());
    on(root, "change", "[data-file]", (e, el) => uploadFiles(el.files));
    on(root, "dragenter", "[data-dropzone]", (e, el) => { e.preventDefault(); el.classList.add("drag"); });
    on(root, "dragover", "[data-dropzone]", (e, el) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy"; el.classList.add("drag"); });
    on(root, "dragleave", "[data-dropzone]", (e, el) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drag"); });
    on(root, "drop", "[data-dropzone]", (e, el) => {
      e.preventDefault();
      el.classList.remove("drag");
      uploadFiles(e.dataTransfer?.files);
    });
  }

  renderFrame();
  await load();

  return () => {
    player.pause();
    player.removeAttribute("src");
    player.load();
  };
}
