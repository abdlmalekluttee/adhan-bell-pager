---
name: Adhan & Bell Pager
description: Operate-mode control panel for broadcasting the Adhan and school bells, built on Tabler.
colors:
  prayer-hall-green: "#0f6e64"
  prayer-hall-green-strong: "#0b564e"
  prayer-hall-green-dark: "#2a9d8f"
  school-bell-amber: "#97570a"
  school-bell-amber-strong: "#7a4606"
  school-bell-amber-dark: "#d08a2c"
  administration-slate: "#3d5a80"
  administration-slate-strong: "#2f4766"
  administration-slate-dark: "#7ea3d1"
  brass: "#e3b04b"
  parchment: "#f4efe2"
typography:
  body-en:
    fontFamily: "Noto Sans Variable, system-ui, Segoe UI, Roboto, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.4285714
  body-ar:
    fontFamily: "Cairo Variable, Segoe UI, Tahoma, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
  page-title:
    fontFamily: "{typography.body-en.fontFamily}"
    fontSize: "1.375rem"
    fontWeight: 650
    letterSpacing: "-0.01em"
  timeline-time:
    fontSize: "1.25rem"
    fontWeight: 650
    fontFeature: "tnum"
  countdown:
    fontSize: "2.25rem"
    fontWeight: 700
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
rounded:
  control: "8px"
  card: "10px"
  pill: "999px"
spacing:
  row-y: "0.8rem"
  row-x: "1rem"
  header-gap: "2.25rem"
components:
  button-primary-adhan:
    backgroundColor: "{colors.prayer-hall-green}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
  button-primary-bells:
    backgroundColor: "{colors.school-bell-amber}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
  button-primary-system:
    backgroundColor: "{colors.administration-slate}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
  module-chip-adhan:
    textColor: "{colors.prayer-hall-green-strong}"
    rounded: "{rounded.pill}"
  module-chip-bells:
    textColor: "{colors.school-bell-amber-strong}"
    rounded: "{rounded.pill}"
---

# Design System: Adhan & Bell Pager

> Documented from the built interface (scan mode). The visual framework (Tabler 1.5) and the Arabic typeface
> (Cairo) are owner-pinned; module colours carry the v1 prayer-hall green and brass forward. Qualitative language
> below was written without an interview and should be confirmed.

## Overview

**North star: the school office noticeboard, kept tidy.** A calm, familiar Tabler control panel where the one thing
that moves is what is actually on the speakers. Staff should trust it at a glance: today's broadcasts as a readable
timeline, the next one as a large countdown, problems in plain words. Colour identifies *which part of the building's
life* you are in (Adhan, School Bells, Administration) rather than decorating.

This is an Operate-mode product surface: no display type, no hero patterns, no decorative motion. Brand lives in the
logo (a dome that reads as a bell, with brass sound arcs), the sign-in panel's arch pattern, and the module colours.

## Colors

* **Module accent** is applied by `data-module` on the page wrapper and on dialogs; it redefines Tabler's
  `--tblr-primary*` variables so every primary button, link, focus ring, selection tint and "next" highlight follows the
  module: prayer-hall green (Adhan and home), school-bell amber (School Bells), administration slate.
* Dark theme swaps each accent for its lighter `-dark` variant and uses a dark foreground on primary buttons.
* Semantic states use Tabler's palette through badges: green *Played*, yellow *Partly played / Skipped*, red
  *Failed / Missed*, orange *Interrupted*, secondary *Stopped*, azure *simulation*.
* Neutrals are Tabler's tinted slate greys; there is no pure black or pure grey.
* Brass (`#e3b04b`) and parchment (`#f4efe2`) appear only in the logo and the sign-in art panel.

## Typography

* English: Noto Sans Variable. Arabic: Cairo Variable (owner-pinned), switched with `html[lang="ar"]`. Both are bundled
  for offline use.
* One family per language, fixed rem scale (Tabler), weights 400/600/650/700.
* Numbers that update or align (clock, times, countdowns, table numbers, 24-hour time inputs) use tabular figures.
* Extensions, URIs, DNs, file names and logs are wrapped in `.ltr` / `<bdi>` so they stay left-to-right inside Arabic.

## Layout

* Tabler vertical layout: sidebar navigation grouped by module (group titles carry a small module-colour dot), top bar
  with the server clock and timezone, phone-system status, language, theme and account menu.
* A sticky **live broadcast bar** appears under the top bar only while something plays: what, where, progress, time
  left and Stop.
* Pages start with a title and one plain-language description; actions sit at the end of the header row.
* Content uses Tabler `row-cards` grids: 8/4 or 7/5 splits on desktop, single column below 992px. Timelines and tables
  collapse to stacked rows under 576px. Editors switch to stacked row blocks under 768px.
* Settings pages use a sticky save bar that appears only when there are unsaved changes.

## Elevation & Depth

Flat Tabler surfaces with hairline borders. Depth is reserved for things above the page: dialogs (soft 24px/60px
shadow and a blurred backdrop), toasts, the live bar (soft bottom shadow) and the save bar.

## Shapes

8px controls, 10px cards and panels, pill-shaped module chips and status badges. Icons are Tabler Icons (outline,
one stroke weight) always paired with text for everyday actions; icon-only buttons carry `aria-label` and `title`.

## Components

* **Timeline row** (today views, dashboard): time, title, secondary line (sound, zones), status badge. Past rows are
  muted; the next row gets a module-tinted background and module-coloured time — no side stripe.
* **Status cell grid** (dashboard health): label with icon, value with a status dot (green OK, red problem, azure
  simulated, grey turned off), one line of detail.
* **Dialogs**: native `<dialog>` with Tabler modal styling; errors appear inside the dialog; destructive confirmations
  use the danger status bar and name the item.
* **Forms**: Tabler labels, hints and switches; write-only secrets are never prefilled and say "leave blank to keep".
* **Empty states** teach the next step and offer the action; **errors** explain and offer *Try again*; loading uses
  skeleton lines.

## Do's and Don'ts

* Do let the module colour mark the module; don't introduce new accent colours for decoration.
* Do keep Tabler components stock; don't invent custom controls for standard tasks.
* Do use plain, action-named copy ("Ring a bell now", "Add a holiday"); keep SIP codes and DNs in secondary text.
* Don't use coloured side stripes, decorative pulsing dots, gradient text or repeating-stripe backgrounds.
* Don't animate layout properties; motion is 150–250 ms and only for state changes (dialogs, toasts, save bar).
* Do test every screen in English and Arabic, light and dark, at 390px and 1440px.
