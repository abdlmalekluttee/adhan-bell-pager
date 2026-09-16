"""FastAPI application: API, session middleware, background services, and the web UI."""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, accounts, audio, audit, config, db, dispatcher, logbuf, ntp, rbac, scheduler, sip
from .deps import Principal
from .routers import adhan as adhan_router
from .routers import admin as admin_router
from .routers import auth as auth_router
from .routers import bells as bells_router
from .routers import system as system_router

logbuf.setup(logging.INFO)
log = logging.getLogger("main")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_ntp_stop = None


def start_services(*, start_sip: bool = True) -> None:
    global _ntp_stop
    config.load()
    db.open_db()
    accounts.bootstrap()
    try:
        created = audio.ensure_builtin_bells()
        if created:
            log.info("created built-in bell sounds: %s", ", ".join(created))
    except Exception:  # noqa: BLE001
        log.exception("could not create built-in bell sounds")
    config.on_change(lambda section: scheduler.get().mark_dirty())
    _ntp_stop = ntp.start()
    if start_sip:
        threading.Thread(target=sip.get_engine().start, name="sip-start", daemon=True).start()
    dispatcher.get().start()
    scheduler.get().start()
    audit.record("service.start", details={"version": __version__})
    log.info("Adhan & Bell Pager %s started (timezone %s)", __version__, config.timezone())


def stop_services() -> None:
    scheduler.get().shutdown()
    dispatcher.get().shutdown()
    sip.get_engine().shutdown()
    if _ntp_stop:
        _ntp_stop.set()
    audit.record("service.stop")
    log.info("service stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_services()
    yield
    stop_services()


app = FastAPI(title="Adhan & Bell Pager", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

PUBLIC_API = {"/api/auth/login", "/api/auth/me", "/api/version", "/api/health"}
PASSWORD_CHANGE_ALLOWED = {"/api/auth/me", "/api/auth/change-password", "/api/auth/logout", "/api/auth/preferences"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def _deny(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def session_guard(request: Request, call_next):
    path = request.url.path
    request.state.principal = None
    token = request.cookies.get(accounts.SESSION_COOKIE, "")
    if token:
        found = accounts.resolve_session(token)
        if found:
            sess, user = found
            user = auth_router.session_principal_refresh(user)
            if user is None:
                if path.startswith("/api/") and path not in PUBLIC_API:
                    return _deny(401, "directory_unavailable",
                                 "Your directory access could not be re-checked. Sign in again when the "
                                 "directory is available, or use a local account.")
            else:
                request.state.principal = Principal(user=user, session=sess,
                                                    effective=rbac.effective_for_user(user))
    p: Principal | None = request.state.principal
    if path.startswith("/api/"):
        if path not in PUBLIC_API:
            if p is None:
                return _deny(401, "not_authenticated", "Please sign in.")
            if p.user["must_change_password"] and path not in PASSWORD_CHANGE_ALLOWED:
                return _deny(403, "password_change_required", "Change your password to continue.")
            if request.method in UNSAFE and request.headers.get("x-csrf-token", "") != p.session["csrf"]:
                return _deny(403, "csrf", "Your session token is missing or out of date. Reload the page.")
        elif request.method in UNSAFE and path == "/api/auth/login":
            if "application/json" not in request.headers.get("content-type", ""):
                return _deny(415, "json_required", "Sign-in requests must be JSON.")
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = CSP
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith(("/static/vendor/fonts/", "/static/vendor/tabler-icons/fonts/", "/static/fi/")):
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif path.startswith("/static/") or path == "/":
        # Revalidate app code on every load so an upgrade never leaves browsers on old JS/CSS.
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    err = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
    msg = (err.get("msg") or "Invalid request").replace("Value error, ", "")
    return JSONResponse({"detail": {"code": "invalid", "message": f"{loc}: {msg}" if loc else msg}}, status_code=422)


@app.exception_handler(HTTPException)
async def http_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": {"code": "server_error",
                                    "message": "Something went wrong on the server. The error was logged."}},
                        status_code=500)


for r in (auth_router.router, admin_router.router, system_router.router, adhan_router.router,
          adhan_router.geo_router, bells_router.router):
    app.include_router(r)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str):
    raise HTTPException(404, detail={"code": "not_found", "message": "Unknown API endpoint."})


# --------------------------------------------------------------------------- #
# Web UI
# --------------------------------------------------------------------------- #
def _index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index():
    return _index()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "img", "logo.svg"), media_type="image/svg+xml")


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
