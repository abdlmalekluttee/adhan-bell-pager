# UI conventions

The web UI is a dependency-free ES-module single-page app on top of **Tabler 1.5** (vendored under
`app/static/vendor`, so it works on an offline LAN). There is no build step.

## Files

| File | Purpose |
|---|---|
| `app/static/index.html` | Shell document. Loads Tabler CSS/JS, icons, `css/app.css`, `js/boot.js`, `js/app.js`. |
| `app/static/js/boot.js` | Applies saved language (RTL) and theme before first paint. |
| `app/static/js/app.js` | Sign-in, forced password change, sidebar, top bar, live broadcast bar, router. **Route table lives here.** |
| `app/static/js/api.js` | `api(path, {method, body, form, query, onUploadProgress})` → JSON or throws `ApiError {status, code, message, detail}`. Adds the CSRF header on writes. |
| `app/static/js/i18n.js` | `t("English text", {vars})`, `tn(n, one, many)`, `fmtTime/fmtDate/fmtDateTime/fmtDuration/fmtCountdown/fmtAgo/weekdayName`, `WEEKDAYS`. Formats in the **server timezone**. |
| `app/static/js/store.js` | `state.me`, `can(perm)`, `canAny([...])`, `serverNow()`, `navigate(path)`, `onBroadcast(fn)`, `kickBroadcast()`. |
| `app/static/js/ui.js` | `html\`\`` (auto-escaping), `raw()`, `setHTML`, `$`, `$$`, `on(root, event, selector, fn)`, `icon(name)`, `pageHeader`, `skeleton`, `emptyState`, `errorState`, `errorMessage`, `alertBox`, `toast`, `toastError`, `confirmDialog`, `formDialog`, `formValues`, `withBusy`, `dirtyTracker` + `saveBar`, `field`, `selectField`, `switchField`, `statusBadge`, `resultsTable`, `moduleChip`, `destState`, `debounce`. |
| `app/static/js/pages/*.js` | One module per route. |
| `app/static/js/locales/ar.js` | Arabic dictionary keyed by the exact English text. |
| `app/static/css/app.css` | Tokens (fonts, module colours) and shared patterns. |

Reference implementations: `pages/dashboard.js` and `pages/bells-today.js`.

## Page contract

```js
export default async function myPage(ctx) {
  // ctx.root   – empty container element for this page (fresh element per navigation)
  // ctx.params – route params (e.g. {id}); ctx.query – hash query (?date=…); ctx.props – static route props
  // ctx.reload – re-run the router for the current route
  ...render into ctx.root...
  return () => { /* clear timers, unsubscribe */ };   // optional cleanup
}
```

* Bind events with delegation on `ctx.root` (`on(root, "click", "[data-action=x]", fn)`). The router gives every
  page a fresh root, so listeners never leak between pages. Bind once, then re-render inner HTML freely.
* Show `skeleton()` while loading (the router already shows one before the page resolves), `errorState(err)` with a
  `data-action="retry"` button when loading fails, and `emptyState()` that teaches what to do next.
* Always start with `pageHeader({title, description, actions})`. No eyebrow/kicker text above titles.

## Security and permissions

* **Never** build HTML by string concatenation with user or server data. Use `html\`\`` which escapes everything
  except nested `html` results and `raw()` (use `raw()` only for trusted static markup such as `icon()`).
* Hide or disable controls the user cannot use (`can("bells.schedule.edit")`), but remember the server enforces every
  permission anyway; handle 403 with `toastError(err)`.
* Write-only secrets (SIP password, directory bind password): never prefill; show "leave blank to keep" hints and a
  `has_password`/`has_bind_password` indicator.

## Language, RTL, numbers

* Every user-visible string goes through `t("…")` with plain English text, including button labels, `aria-label`,
  placeholders, titles and toasts. Use `{name}` placeholders instead of concatenation so Arabic word order works.
* Backend messages are English; pass them through `t(err.message)` / `errorMessage(err)` so known ones translate.
* Use logical CSS (`ms-*`/`me-*`, `text-start`/`text-end`, `inset-inline-*`). Tabler's RTL stylesheet flips the
  rest. Add class `flip-rtl` to directional icons (chevrons, arrows). Wrap extensions, URIs, DNs, IPs, file names and
  code in `class="ltr"` so they stay left-to-right inside Arabic text.
* Times: `fmtTime(iso)`, dates `fmtDate("YYYY-MM-DD", {weekday:true})`, durations `fmtDuration(seconds)`. Add class
  `tabular` to numbers that update or align in columns.

## Visual language (Impeccable, Operate mode)

* Tabler components only; keep one vocabulary: primary action = `btn btn-primary` (the module colour applies
  automatically), secondary = `btn btn-outline-secondary`, destructive = `btn btn-danger` behind `confirmDialog({danger:true})`,
  tertiary = `btn btn-ghost-secondary` / `btn-link`.
* Icons: Tabler Icons webfont, `icon("bell")`. Every icon button needs an `aria-label`; prefer icon + text for
  everyday actions.
* Cards group related content; never nest cards. Use `card-table` tables, `list-group-flush`, `timeline-list` rows.
* Forms: `form-label`, `form-control`, `form-select`, `form-check`/`form-switch`, `form-hint`, `invalid-feedback`.
  Group long forms into titled cards; sticky `saveBar()` with `dirtyTracker()` for settings pages, `formDialog()` for
  create/edit of single records.
* States: loading (skeleton), empty (teaches the next step), error (message + retry), success (toast), progress
  (`withBusy(button, fn)` for actions; upload progress bars).
* Copy is plain, action-named language for non-technical staff ("Ring a bell now", "Add a holiday"). Hide technical
  details (SIP codes, DNs) behind "Advanced" `<details>` or secondary text.
* Responsive: design at 390px, 768px and 1440px. Tables wrap in `.table-responsive`; editors stack on mobile.
* Motion: 150–250 ms, state changes only. Respect `prefers-reduced-motion` (already handled for shared patterns).

## Status vocabulary

Execution statuses (history, today views): `queued` Waiting, `running` Playing, `success` Played, `partial` Partly
played, `failed` Failed, `cancelled` Stopped, `interrupted` Interrupted, `skipped` Skipped, `missed` Missed. Use
`statusBadge(status)`; per-destination details with `resultsTable(results)`.

## Local preview

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt     # or .venv/bin/pip
CONFIG_PATH=$PWD/demo/config/config.yaml AUDIO_DIR=$PWD/demo/audio python scripts/seed_demo.py
CONFIG_PATH=$PWD/demo/config/config.yaml AUDIO_DIR=$PWD/demo/audio SIP_SIMULATE=1 WEB_PORT=8931 WEB_BIND=127.0.0.1 python -m app
```

Sign in as `admin` / `Demo-Admin-2026` (demo data only). `SIP_SIMULATE=1` fakes calls so the whole UI can be exercised.
