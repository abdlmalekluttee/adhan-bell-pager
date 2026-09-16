// Translation and locale formatting.
// Strings are written in English in the code: t("Ring now"). The Arabic dictionary
// maps the same English text to Arabic. Missing entries fall back to English, and
// tests/test_i18n.py fails the build if any t("...") string lacks an Arabic entry.

import { AR } from "./locales/ar.js";

let lang = document.documentElement.lang === "ar" ? "ar" : "en";
let timeZone = undefined;

export function getLang() { return lang; }
export function isRTL() { return lang === "ar"; }
export function setTimeZone(tz) { timeZone = tz || undefined; }
export function getTimeZone() { return timeZone; }

export function t(text, vars) {
  let s = lang === "ar" ? (AR[text] ?? text) : text;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? String(vars[k]) : m));
  return s;
}

/** Plural helper: tn(n, "{n} bell", "{n} bells") */
export function tn(n, one, many, vars = {}) {
  return t(n === 1 ? one : many, { n: fmtNumber(n), ...vars });
}

export function applyLanguage(next) {
  lang = next === "ar" ? "ar" : "en";
  const root = document.documentElement;
  root.lang = lang;
  root.dir = lang === "ar" ? "rtl" : "ltr";
  const link = document.getElementById("tabler-css");
  const href = lang === "ar" ? "/static/vendor/tabler/tabler.rtl.min.css" : "/static/vendor/tabler/tabler.min.css";
  if (link && !link.href.endsWith(href)) link.href = href;
  try { localStorage.setItem("abp.lang", lang); } catch { /* ignore */ }
}

export function applyTheme(theme) {
  const mode = ["light", "dark", "auto"].includes(theme) ? theme : "auto";
  const dark = mode === "dark" || (mode === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
  document.documentElement.dataset.themeMode = mode;
  try { localStorage.setItem("abp.theme", mode); } catch { /* ignore */ }
}

function locale() { return lang === "ar" ? "ar-u-nu-latn" : "en-GB"; }

function toDate(v) { return v instanceof Date ? v : new Date(typeof v === "number" ? v * 1000 : v); }

export function fmtTime(v, { seconds = false } = {}) {
  if (v === null || v === undefined || v === "") return "—";
  return new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit", second: seconds ? "2-digit" : undefined,
    hourCycle: "h23", timeZone }).format(toDate(v));
}

export function fmtDate(v, { weekday = false, year = true } = {}) {
  if (!v) return "—";
  const d = typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(v + "T12:00:00Z") : toDate(v);
  const tz = typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? "UTC" : timeZone;
  return new Intl.DateTimeFormat(locale(), { weekday: weekday ? "long" : undefined, day: "numeric", month: "short",
    year: year ? "numeric" : undefined, timeZone: tz }).format(d);
}

export function fmtDateTime(v) {
  if (!v) return "—";
  return new Intl.DateTimeFormat(locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    hourCycle: "h23", timeZone }).format(toDate(v));
}

export function fmtNumber(n, opts) { return new Intl.NumberFormat(locale(), opts).format(n); }

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return t("{h} h {m} min", { h: fmtNumber(h), m: fmtNumber(m) });
  if (m) return sec && m < 10 ? t("{m} min {s} s", { m: fmtNumber(m), s: fmtNumber(sec) }) : t("{m} min", { m: fmtNumber(m) });
  return t("{s} s", { s: fmtNumber(sec) });
}

export function fmtCountdown(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (x) => String(x).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

export function fmtAgo(v) {
  if (!v) return t("never");
  const diff = (Date.now() - toDate(v).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
  if (Math.abs(diff) < 60) return rtf.format(-Math.round(diff), "second");
  if (Math.abs(diff) < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (Math.abs(diff) < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
  return rtf.format(-Math.round(diff / 86400), "day");
}

const WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
export function weekdayName(key, style = "long") {
  const idx = WEEKDAY_KEYS.indexOf(key);
  const d = new Date(Date.UTC(2024, 0, 1 + idx, 12));      // 2024-01-01 was a Monday
  return new Intl.DateTimeFormat(locale(), { weekday: style, timeZone: "UTC" }).format(d);
}
export const WEEKDAYS = WEEKDAY_KEYS;
