// Adhan › Sounds: the recording library, uploads, previews, and which recording plays for each prayer.

import { api } from "../api.js";
import { fmtDuration, fmtNumber, t, tn } from "../i18n.js";
import { can } from "../store.js";
import {
  $, $$, alertBox, confirmDialog, dirtyTracker, emptyState, errorState, html, icon, on, pageHeader, raw, saveBar, setHTML,
  skeleton, toast, toastError, withBusy,
} from "../ui.js";
import { PRAYERS, occurrenceTitle, playAdhan, prayerName } from "./adhan-today.js";

const ACCEPT = ".wav,.mp3,.m4a,.aac,.ogg,.flac,.opus";
const RATES = [8000, 16000, 32000, 44100, 48000];

function rateLabel(r) {
  if (r === 8000) return t("8 kHz (G.711 only)");
  if (r === 16000) return t("16 kHz (recommended)");
  return t("{rate} kHz", { rate: fmtNumber(r / 1000) });
}

/** "fajr Adhan" → "Fajr Adhan"; "default iqama" → "Default iqama". */
function usageLabel(u) {
  if (u === "default Adhan") return t("Default Adhan");
  if (u === "default iqama") return t("Default iqama");
  const m = String(u).match(/^(fajr|dhuhr|asr|maghrib|isha) (Adhan|iqama)$/);
  return m ? occurrenceTitle(m[1], m[2]) : u;
}

function formatLabel(f) {
  if (!f.rate) return "—";
  const rate = fmtNumber(f.rate / 1000);
  if (f.channels === 1) return t("{rate} kHz mono", { rate });
  if (f.channels === 2) return t("{rate} kHz stereo", { rate });
  return t("{rate} kHz", { rate });
}

export default async function adhanSounds(ctx) {
  const { root } = ctx;
  const canManage = can("adhan.sounds.manage");
  const canPlay = can("adhan.broadcast");
  let data = null;
  let tracker = null;
  let uploading = false;

  // One preview player for the whole page.
  const audio = new Audio();
  audio.preload = "none";
  let playingName = null;

  setHTML(root, html`
    ${pageHeader({
      title: t("Adhan sounds"),
      description: t("Recordings for the Adhan and the iqama. Uploads are converted automatically for the phone system."),
    })}
    <div data-notices></div>
    <div class="row row-cards">
      <div class="col-12" data-library>${skeleton(5)}</div>
      <div class="col-12" data-assign-host></div>
    </div>
    ${canManage ? saveBar() : ""}`);
  const libHost = $("[data-library]", root);
  const assignHost = $("[data-assign-host]", root);
  const bar = $(".save-bar", root);

  async function load({ keepPending = false } = {}) {
    const pending = keepPending && tracker && tracker.isDirty() && $("[data-assign-form]", assignHost) ? readAssignments() : null;
    try {
      data = await api("/api/adhan/sounds");
      if (pending) data.assignments = { ...data.assignments, ...pending };
      render();
      if (pending) tracker.set(true);
    } catch (err) {
      if (!data) { setHTML(libHost, errorState(err)); setHTML(assignHost, ""); } else toastError(err);
    }
  }

  function render() {
    setHTML($("[data-notices]", root), data.ffmpeg ? "" : html`<div class="mb-3">${alertBox("warning",
      t("Only WAV files can be uploaded: the audio converter (ffmpeg) is not installed on the server. Convert MP3 and other files to WAV first."))}</div>`);
    setHTML(libHost, libraryCard());
    setHTML(assignHost, assignmentsCard());
    syncAssignUI();
    syncPlayButtons();
  }

  // ------------------------------------------------------------------------
  // Library
  // ------------------------------------------------------------------------
  function libraryCard() {
    const files = data.files;
    return html`<div class="card">
      <div class="card-header">
        <h2 class="card-title">${t("Library")}</h2>
        <div class="card-actions small text-secondary">${tn(files.length, "{n} recording", "{n} recordings")}</div>
      </div>
      ${canManage ? html`<div class="card-body border-bottom" data-no-dirty>${uploadArea()}</div>` : ""}
      ${files.length ? html`<div class="table-responsive">
        <table class="table card-table table-vcenter">
          <thead><tr>
            <th>${t("Name")}</th><th>${t("Length")}</th><th>${t("Format")}</th><th>${t("Used for")}</th><th class="w-1"><span class="visually-hidden">${t("Actions")}</span></th>
          </tr></thead>
          <tbody>${files.map(fileRow)}</tbody>
        </table>
      </div>` : emptyState({
        iconName: "music",
        title: t("No Adhan recordings yet"),
        text: canManage
          ? t("Upload a recording of the Adhan above (MP3 or WAV work well). Then choose below which prayers use it.")
          : t("Ask someone who manages Adhan sounds to upload a recording. Until then the Adhan cannot play."),
      })}
    </div>`;
  }

  function uploadArea() {
    return html`<div class="dropzone-lite" data-dropzone>
      <div class="mb-2">${icon("cloud-upload", "fs-1 text-secondary")}</div>
      <div class="fw-semibold">${t("Drag recordings here")}</div>
      <div class="text-secondary small mb-3">${data.ffmpeg
        ? t("WAV, MP3, M4A, AAC, OGG, FLAC or Opus, up to 50 MB each.")
        : t("WAV files only, up to 50 MB each.")}</div>
      <label class="btn btn-outline-secondary">
        ${icon("upload")} ${t("Choose files")}
        <input type="file" class="visually-hidden" multiple accept="${data.ffmpeg ? ACCEPT : ".wav"}" data-upload-input>
      </label>
      <div data-upload-status class="mt-3 text-start" aria-live="polite" hidden>
        <div class="d-flex justify-content-between small mb-1"><span data-upload-name class="ltr text-truncate"></span><span data-upload-pct class="tabular"></span></div>
        <div class="progress progress-sm"><div class="progress-bar" data-upload-bar style="width:0%"></div></div>
      </div>
    </div>`;
  }

  function fileRow(f) {
    const used = f.used_by || [];
    return html`<tr>
      <td>
        <div class="ltr text-break fw-semibold">${f.name}</div>
        ${f.playable ? "" : html`<div class="small text-danger">${icon("alert-triangle")} ${t("This file cannot be played. Upload it again.")}</div>`}
      </td>
      <td class="num text-nowrap">${fmtDuration(f.duration)}</td>
      <td class="text-nowrap text-secondary">${formatLabel(f)}</td>
      <td>${used.length ? used.map((u) => html`<span class="badge bg-primary-lt me-1 mb-1">${usageLabel(u)}</span>`) : html`<span class="text-secondary">${t("Not used")}</span>`}</td>
      <td>
        <div class="btn-list flex-nowrap justify-content-end">
          ${f.playable ? html`<button type="button" class="btn btn-icon btn-ghost-secondary" data-action="preview" data-name="${f.name}"
            aria-label="${t("Listen to {name} in this browser", { name: f.name })}" title="${t("Listen here")}">${icon("player-play")}</button>` : ""}
          ${canPlay && f.playable ? html`<button type="button" class="btn btn-ghost-secondary text-nowrap" data-action="play-speakers" data-name="${f.name}"
            aria-label="${t("Play {name} on the speakers", { name: f.name })}">${icon("speakerphone")} <span class="d-none d-md-inline">${t("Play on speakers")}</span></button>` : ""}
          ${canManage ? html`<button type="button" class="btn btn-icon btn-ghost-danger" data-action="delete" data-name="${f.name}"
            aria-label="${t("Delete {name}", { name: f.name })}" title="${t("Delete")}">${icon("trash")}</button>` : ""}
        </div>
      </td>
    </tr>`;
  }

  function syncPlayButtons() {
    $$("[data-action=preview]", root).forEach((b) => {
      const active = b.dataset.name === playingName && !audio.paused;
      b.innerHTML = icon(active ? "player-pause" : "player-play").s;
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function preview(name) {
    if (playingName === name && !audio.paused) { audio.pause(); return; }
    if (playingName !== name) {
      audio.src = `/api/adhan/sounds/${encodeURIComponent(name)}/file`;
      playingName = name;
    }
    audio.play().catch(() => { toast(t("The browser could not play this recording."), "warning"); playingName = null; syncPlayButtons(); });
  }
  ["play", "pause", "ended"].forEach((ev) => audio.addEventListener(ev, syncPlayButtons));

  async function uploadFiles(fileList) {
    if (uploading) { toast(t("Wait for the current upload to finish."), "warning"); return; }
    const files = Array.from(fileList || []);
    if (!files.length) return;
    uploading = true;
    let added = 0;
    try {
      for (const file of files) {
        const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
        const allowed = data.ffmpeg ? ACCEPT.split(",") : [".wav"];
        if (!allowed.includes(ext)) {
          toast(t("“{name}” was skipped: this type of file is not supported.", { name: file.name }), "danger");
          continue;
        }
        if (await uploadOne(file)) added++;
      }
    } finally {
      uploading = false;
      const status = $("[data-upload-status]", root);
      if (status) status.hidden = true;
    }
    if (added) await load({ keepPending: true });
  }

  async function uploadOne(file, replace = false) {
    const status = $("[data-upload-status]", root);
    const setProgress = (pct, label) => {
      if (!status) return;
      status.hidden = false;
      $("[data-upload-name]", status).textContent = file.name;
      $("[data-upload-pct]", status).textContent = label;
      $("[data-upload-bar]", status).style.width = `${pct}%`;
    };
    setProgress(0, "0%");
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await api("/api/adhan/sounds", {
        method: "POST", form, query: replace ? { replace: "true" } : undefined, timeout: 300000,
        onUploadProgress: (pct) => setProgress(pct, pct < 100 ? `${pct}%` : t("Converting…")),
      });
      toast(t("“{name}” was added to the library.", { name: r.name }));
      return true;
    } catch (err) {
      if (err.code === "exists") {
        const name = err.detail.name || file.name;
        const ok = await confirmDialog({
          title: t("Replace the existing recording?"),
          message: t("“{name}” is already in the library. Replace it with the file you chose? Prayers that use it will play the new recording.", { name }),
          confirmLabel: t("Replace"),
        });
        return ok ? uploadOne(file, true) : false;
      }
      if (err.code === "audio_invalid") toast(t("“{name}” could not be used: it is not a supported audio file or it is damaged.", { name: file.name }), "danger");
      else toastError(err, file.name);
      return false;
    }
  }

  // ------------------------------------------------------------------------
  // Assignments
  // ------------------------------------------------------------------------
  function fileOptions(current, emptyLabel) {
    const names = data.files.filter((f) => f.playable).map((f) => f.name);
    const opts = [html`<option value="" ${current ? "" : raw("selected")}>${emptyLabel}</option>`];
    if (current && !names.includes(current)) {
      opts.push(html`<option value="${current}" selected>${t("{name} (missing)", { name: current })}</option>`);
    }
    names.forEach((n) => opts.push(html`<option value="${n}" ${n === current ? raw("selected") : ""}>${n}</option>`));
    return opts;
  }

  function assignmentsCard() {
    const a = data.assignments;
    return html`<form data-assign-form novalidate>
      <fieldset ${canManage ? "" : raw("disabled")}>
        <div class="row row-cards">
          <div class="col-lg-8">
            <div class="card h-100">
              <div class="card-header"><h2 class="card-title">${t("Which recording plays")}</h2></div>
              <div class="card-body">
                ${canManage ? "" : alertBox("info", t("You can see the assignments. Changing them needs the “Manage Adhan sounds” permission."))}
                <div data-missing></div>
                <div class="row g-3">
                  <div class="col-md-6">
                    <label class="form-label" for="as-default">${t("Adhan recording")}</label>
                    <select id="as-default" class="form-select" data-field="default_file">${fileOptions(a.default_file, t("None chosen"))}</select>
                    <div class="form-hint">${t("Used for every prayer that has no recording of its own.")}</div>
                  </div>
                  <div class="col-md-6">
                    <label class="form-label" for="as-iqama">${t("Iqama recording")}</label>
                    <select id="as-iqama" class="form-select" data-field="iqama_file">${fileOptions(a.iqama_file, t("Same as the Adhan"))}</select>
                    <div class="form-hint">${t("Only plays when the iqama is turned on in the prayer schedule.")}</div>
                  </div>
                  <div class="col-12">
                    <label class="form-check form-switch mb-0">
                      <input class="form-check-input" type="checkbox" data-field="use_global_for_all" ${a.use_global_for_all ? raw("checked") : ""}>
                      <span class="form-check-label">${t("Use one recording for every Adhan")}</span>
                    </label>
                    <div class="form-hint ms-5">${t("Turn off to choose a different Adhan recording for some prayers.")}</div>
                  </div>
                </div>
              </div>
              <div class="table-responsive">
                <table class="table card-table table-vcenter table-editor mb-0">
                  <thead><tr><th>${t("Prayer")}</th><th>${t("Adhan")}</th><th>${t("Iqama")}</th></tr></thead>
                  <tbody>
                    ${PRAYERS.map((p) => html`<tr>
                      <td class="fw-semibold text-nowrap">${prayerName(p)}</td>
                      <td><select class="form-select" data-per-prayer="${p}" aria-label="${t("Adhan recording for {prayer}", { prayer: prayerName(p) })}">
                        ${fileOptions(a.per_prayer[p] || "", t("Same as above"))}</select></td>
                      <td><select class="form-select" data-iqama-prayer="${p}" aria-label="${t("Iqama recording for {prayer}", { prayer: prayerName(p) })}">
                        ${fileOptions(a.iqama_per_prayer[p] || "", t("Same as above"))}</select></td>
                    </tr>`)}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          <div class="col-lg-4">
            <div class="card h-100">
              <div class="card-header"><h2 class="card-title">${t("Converting new uploads")}</h2></div>
              <div class="card-body">
                <div class="mb-3">
                  <label class="form-label" for="as-rate">${t("Sample rate")}</label>
                  <select id="as-rate" class="form-select" data-field="target_rate">
                    ${RATES.map((r) => html`<option value="${r}" ${r === a.target_rate ? raw("selected") : ""}>${rateLabel(r)}</option>`)}
                  </select>
                  <div class="form-hint">${t("16 kHz suits most phone systems (G.722). Use 8 kHz only if your phones support nothing better.")}</div>
                </div>
                <div class="mb-3">
                  <label class="form-label" for="as-gain">${t("Volume change (dB)")}</label>
                  <input id="as-gain" class="form-control tabular" type="number" min="-30" max="30" step="0.5" inputmode="decimal" data-field="gain_db" value="${a.gain_db}" style="max-width:8rem">
                  <div class="invalid-feedback">${t("Enter a number from -30 to 30.")}</div>
                  <div class="form-hint">${t("Positive makes uploads louder, negative quieter. 0 leaves them as they are.")}</div>
                </div>
                <p class="small text-secondary mb-0">${icon("info-circle")} ${t("These settings apply to recordings you upload from now on. Files already in the library are not changed; upload them again to convert them.")}</p>
              </div>
            </div>
          </div>
        </div>
      </fieldset>
    </form>`;
  }

  function readAssignments() {
    const form = $("[data-assign-form]", assignHost);
    const val = (sel) => $(sel, form).value;
    const per = {};
    const iq = {};
    PRAYERS.forEach((p) => { per[p] = val(`[data-per-prayer="${p}"]`); iq[p] = val(`[data-iqama-prayer="${p}"]`); });
    return {
      default_file: val("[data-field=default_file]"),
      use_global_for_all: $("[data-field=use_global_for_all]", form).checked,
      per_prayer: per,
      iqama_file: val("[data-field=iqama_file]"),
      iqama_per_prayer: iq,
      target_rate: Number(val("[data-field=target_rate]")),
      gain_db: val("[data-field=gain_db]").trim() === "" ? 0 : Number(val("[data-field=gain_db]")),
    };
  }

  function syncAssignUI() {
    const form = $("[data-assign-form]", assignHost);
    if (!form) return;
    const a = readAssignments();
    $$("[data-per-prayer]", form).forEach((el) => { el.disabled = a.use_global_for_all || !canManage; });
    const missing = PRAYERS.filter((p) => !(a.use_global_for_all ? a.default_file : (a.per_prayer[p] || a.default_file)));
    const box = $("[data-missing]", form);
    if (box) {
      setHTML(box, missing.length ? html`<div class="mb-3">${alertBox("warning",
        t("No recording is chosen for: {prayers}. The Adhan will fail for these prayers.", { prayers: missing.map(prayerName).join(", ") }))}</div>` : "");
    }
  }

  // ------------------------------------------------------------------------
  // Events
  // ------------------------------------------------------------------------
  on(root, "click", "[data-action=retry]", () => { setHTML(libHost, skeleton(5)); load(); });
  on(root, "change", "[data-assign-form]", syncAssignUI);
  on(root, "click", "[data-action=preview]", (e, el) => preview(el.dataset.name));
  on(root, "click", "[data-action=play-speakers]", async (e, el) => {
    const name = el.dataset.name;
    const ok = await confirmDialog({
      title: t("Play on the speakers now?"),
      message: t("“{name}” will play right away on the speakers used for the Adhan.", { name }),
      confirmLabel: t("Play now"),
    });
    if (!ok) return;
    try {
      const r = await withBusy(el, () => playAdhan("/api/adhan/play-file", { file: name }));
      if (r !== false) toast(t("“{name}” is playing on the speakers.", { name }));
    } catch (err) {
      toastError(err);
    }
  });
  on(root, "click", "[data-action=delete]", async (e, el) => {
    const name = el.dataset.name;
    const file = data.files.find((f) => f.name === name);
    const used = file?.used_by || [];
    const ok = await confirmDialog({
      title: t("Delete this recording?"),
      message: t("“{name}” will be removed from the library. This cannot be undone.", { name }),
      details: used.length ? alertBox("warning", t("It is still used for: {uses}. Choose another recording there first.", { uses: used.map(usageLabel).join(", ") })) : "",
      confirmLabel: t("Delete"), danger: true,
    });
    if (!ok) return;
    try {
      await withBusy(el, () => api(`/api/adhan/sounds/${encodeURIComponent(name)}`, { method: "DELETE" }));
      if (playingName === name) { audio.pause(); playingName = null; }
      toast(t("“{name}” was deleted.", { name }));
      await load({ keepPending: true });
    } catch (err) {
      if (err.code === "in_use") {
        toast(t("“{name}” cannot be deleted because it is still used for: {uses}. Choose another recording there first.", { name, uses: used.map(usageLabel).join(", ") || t("a prayer") }), "danger");
      } else {
        toastError(err);
      }
    }
  });
  on(root, "change", "[data-upload-input]", (e, el) => { const files = el.files; uploadFiles(files).finally(() => { el.value = ""; }); });
  on(root, "dragover", "[data-dropzone]", (e, el) => { e.preventDefault(); el.classList.add("drag"); });
  on(root, "dragleave", "[data-dropzone]", (e, el) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drag"); });
  on(root, "drop", "[data-dropzone]", (e, el) => { e.preventDefault(); el.classList.remove("drag"); uploadFiles(e.dataTransfer?.files); });

  if (canManage && bar) {
    tracker = dirtyTracker(assignHost, bar, {
      onSave: async () => {
        const body = readAssignments();
        const gain = $("[data-field=gain_db]", assignHost);
        const gainOk = Number.isFinite(body.gain_db) && body.gain_db >= -30 && body.gain_db <= 30;
        gain.classList.toggle("is-invalid", !gainOk);
        if (!gainOk) { gain.focus(); throw new Error(t("Enter a volume change from -30 to 30 dB.")); }
        await api("/api/adhan/sounds/assignments", { method: "PUT", body });
        toast(t("Recording choices saved."));
        await load();
      },
      onDiscard: () => render(),
    });
  }

  await load();
  return () => { audio.pause(); audio.removeAttribute("src"); audio.load(); };
}
