// Shared session state, permission checks, navigation and live broadcast updates.

import { api } from "./api.js";
import { setTimeZone } from "./i18n.js";

export const state = {
  me: null,              // /api/auth/me payload
  perms: new Set(),
  serverOffsetMs: 0,     // server clock minus browser clock
  broadcast: null,       // last /api/broadcast payload
};

export function setMe(me) {
  state.me = me;
  state.perms = new Set(me?.permissions || []);
  if (me?.server) {
    setTimeZone(me.server.timezone);
    state.serverOffsetMs = new Date(me.server.now).getTime() - Date.now();
  }
}

export const can = (perm) => state.perms.has(perm);
export const canAny = (list) => !list || list.length === 0 || list.some((p) => state.perms.has(p));
export const serverNow = () => new Date(Date.now() + state.serverOffsetMs);

export function navigate(path) {
  if (location.hash !== "#" + path) location.hash = "#" + path;
  else window.dispatchEvent(new HashChangeEvent("hashchange"));
}

// ---- live broadcast polling -------------------------------------------------
const subs = new Set();
let timer = null;
let lastActiveKey = null;

export function onBroadcast(fn) { subs.add(fn); if (state.broadcast) fn(state.broadcast); return () => subs.delete(fn); }

export async function pollBroadcast() {
  clearTimeout(timer);
  if (!canAny(["adhan.view", "bells.view"])) return;
  let next = 10000;
  try {
    const b = await api("/api/broadcast", { timeout: 8000 });
    const key = b.active ? b.active.key : null;
    const finished = lastActiveKey && lastActiveKey !== key;
    lastActiveKey = key;
    state.broadcast = { ...b, finished };
    subs.forEach((fn) => fn(state.broadcast));
    next = b.line_busy || b.queue?.length ? 1500 : 8000;
  } catch {
    next = 15000;
  }
  if (!document.hidden) timer = setTimeout(pollBroadcast, next);
}

export function kickBroadcast() { setTimeout(pollBroadcast, 400); }

document.addEventListener("visibilitychange", () => { if (!document.hidden && state.me) pollBroadcast(); });
