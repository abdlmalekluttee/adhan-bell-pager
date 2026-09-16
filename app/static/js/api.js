// API client: JSON by default, CSRF header on writes, readable errors.

export class ApiError extends Error {
  constructor(status, detail) {
    const d = detail && typeof detail === "object" ? detail : { message: String(detail || "") };
    super(d.message || `Request failed (${status})`);
    this.status = status;
    this.code = d.code || "";
    this.detail = d;
  }
}

function cookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : "";
}

const listeners = new Set();
export function onAuthProblem(fn) { listeners.add(fn); return () => listeners.delete(fn); }

/**
 * api("/api/bells/day", { query: {date}, method, body, form, timeout })
 * - body: JSON-serialised; form: FormData (uploads)
 * - resolves with parsed JSON (or text for non-JSON responses)
 * - rejects with ApiError; 401/403 password-change notify the app shell
 */
export async function api(path, opts = {}) {
  const { method = "GET", body, form, query, timeout = 20000, onUploadProgress, raw = false } = opts;
  let url = path;
  if (query) {
    const qs = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") qs.set(k, v); });
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRF-Token"] = cookie("abp_csrf");
  if (form && onUploadProgress) return xhrUpload(url, method, form, headers, onUploadProgress);
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(url, { method, headers, body: payload, credentials: "same-origin", signal: ctrl.signal });
  } catch (e) {
    throw new ApiError(0, { code: e.name === "AbortError" ? "timeout" : "network",
      message: e.name === "AbortError" ? "The server took too long to respond." : "Cannot reach the server. Check the network connection." });
  } finally {
    clearTimeout(timer);
  }
  if (raw) return res;
  return handle(res, path);
}

async function handle(res, path) {
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json().catch(() => ({})) : await res.text();
  if (res.ok) return data;
  const detail = data && data.detail !== undefined ? data.detail : data;
  const err = new ApiError(res.status, detail);
  if (!path.startsWith("/api/auth/login")) {
    if (res.status === 401 || (res.status === 403 && err.code === "password_change_required")) {
      listeners.forEach((fn) => fn(err));
    }
  }
  throw err;
}

function xhrUpload(url, method, form, headers, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    xhr.onerror = () => reject(new ApiError(0, { code: "network", message: "Upload failed: cannot reach the server." }));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch { data = { detail: xhr.responseText }; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, data.detail !== undefined ? data.detail : data));
    };
    xhr.send(form);
  });
}
