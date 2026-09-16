"""Sign-in, sign-out, current user, own password and preferences."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import accounts, audit, db, directory, rbac, security
from ..deps import Authz, Principal, bad_request, client_ip

log = logging.getLogger("auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field("", max_length=200)
    password: str = Field("", max_length=512)
    remember: bool = False


def _set_cookies(resp: JSONResponse, token: str, csrf: str, remember: bool) -> None:
    max_age = accounts.REMEMBER_TTL if remember else None
    resp.set_cookie(accounts.SESSION_COOKIE, token, max_age=max_age, httponly=True, samesite="strict",
                    secure=accounts.COOKIE_SECURE, path="/")
    resp.set_cookie(accounts.CSRF_COOKIE, csrf, max_age=max_age, httponly=False, samesite="strict",
                    secure=accounts.COOKIE_SECURE, path="/")


def _clear_cookies(resp: JSONResponse) -> None:
    resp.delete_cookie(accounts.SESSION_COOKIE, path="/")
    resp.delete_cookie(accounts.CSRF_COOKIE, path="/")


def profile(user: dict) -> dict:
    eff = rbac.effective_for_user(user)
    return {
        "authenticated": True,
        "user": {
            "id": user["id"], "username": user["username"], "display_name": user["display_name"],
            "source": user["source"], "language": user["language"], "theme": user["theme"],
            "must_change_password": bool(user["must_change_password"]), "is_recovery": bool(user["is_recovery"]),
            "last_login": user["last_login"],
        },
        "permissions": eff.permissions,
        "permission_sources": eff.sources,
        "roles": eff.roles,
        "modules": eff.modules(),
    }


@router.post("/login")
def login(body: LoginIn, request: Request):
    ip = client_ip(request)
    username = body.username.strip()
    locked = accounts.locked_seconds(username, ip)
    if locked:
        return JSONResponse({"detail": {"code": "locked", "message": "Too many attempts. Try again later.",
                                        "retry_after": locked}}, status_code=429)
    if not username or not body.password:
        return JSONResponse({"detail": {"code": "invalid_credentials",
                                        "message": "Enter your username and password."}}, status_code=400)
    existing = accounts.get_by_username(username)
    user = None
    method = "local"
    if existing and existing["source"] == "local":
        user = accounts.verify_local(username, body.password)
        if not user:
            return _fail(username, ip, "invalid_credentials", "Invalid username or password.", method)
    elif directory.enabled():
        method = "directory"
        res = directory.authenticate(username, body.password)
        if not res.ok:
            status = 503 if res.code == "unavailable" else 401 if res.code == "invalid_credentials" else 403
            return _fail(username, ip, res.code, res.message, method, status=status)
        try:
            user = directory.upsert_ad_user(res)
        except directory.DirectoryError as e:
            return _fail(username, ip, e.code, e.message, method, status=409)
    else:
        security.burn_password_check(body.password)
        return _fail(username, ip, "invalid_credentials", "Invalid username or password.", method)

    if user["disabled"]:
        return _fail(username, ip, "account_disabled", "This account is disabled.", method, status=403)
    eff = rbac.effective_for_user(user)
    if not eff.permissions and not user["must_change_password"]:
        return _fail(username, ip, "no_access",
                     "Your account has no access to this application. Ask an administrator.", method, status=403)
    accounts.clear_failures(username, ip)
    token, csrf = accounts.create_session(user, remember=body.remember, ip=ip,
                                          user_agent=request.headers.get("user-agent", ""))
    user = accounts.get_user(user["id"])
    audit.record("auth.login", actor=user["username"], ip=ip, details={"method": method})
    out = profile(user)
    out["server"] = server_info()
    resp = JSONResponse(out)
    _set_cookies(resp, token, csrf, body.remember)
    return resp


def _fail(username: str, ip: str, code: str, message: str, method: str, status: int = 401) -> JSONResponse:
    if code in ("invalid_credentials", "account_blocked", "not_authorized", "account_disabled", "no_access"):
        accounts.record_failure(username, ip)
    audit.record("auth.login", actor=username[:64] or "?", ip=ip, result="denied",
                 details={"method": method, "code": code})
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)


@router.post("/logout", dependencies=[Depends(Authz())])
def logout(request: Request):
    token = request.cookies.get(accounts.SESSION_COOKIE, "")
    p: Principal = request.state.principal
    accounts.revoke_session(token)
    audit.record("auth.logout", actor=p.username, ip=client_ip(request))
    resp = JSONResponse({"ok": True})
    _clear_cookies(resp)
    return resp


@router.get("/me")
def me(request: Request):
    """Public: tells the UI whether a session exists. Returns no data without one."""
    p: Principal | None = getattr(request.state, "principal", None)
    if p is None:
        return {"authenticated": False, "directory_enabled": directory.enabled()}
    out = profile(p.user)
    out["session"] = {"remember": bool(p.session["remember"]), "expires_at": p.session["expires_at"]}
    out["server"] = server_info()
    return out


def server_info() -> dict:
    from .. import __version__, clock, config, sip
    return {"now": clock.now().isoformat(), "timezone": config.timezone(), "version": __version__,
            "simulated": sip.get_engine().backend.simulated, "directory_enabled": directory.enabled()}


class PasswordIn(BaseModel):
    current_password: str = Field("", max_length=512)
    new_password: str = Field("", max_length=512)


@router.post("/change-password", dependencies=[Depends(Authz())])
def change_password(body: PasswordIn, request: Request):
    p: Principal = request.state.principal
    if p.user["source"] != "local":
        raise bad_request("Directory accounts change their password in Windows / Active Directory.", "not_local")
    if not security.verify_password(body.current_password, p.user["password_hash"]):
        audit.from_request(request, "auth.change_password", target=p.username, result="denied")
        raise bad_request("The current password is incorrect.", "wrong_password")
    if body.new_password == body.current_password:
        raise bad_request("Choose a password different from the current one.", "same_password")
    try:
        accounts.set_password(p.id, body.new_password)
    except ValueError as e:
        raise bad_request(str(e), "weak_password")
    n = accounts.revoke_sessions(p.id, keep_id=p.session["id"])
    audit.from_request(request, "auth.change_password", target=p.username, details={"other_sessions_ended": n})
    return profile(accounts.get_user(p.id))


class PrefsIn(BaseModel):
    language: str | None = None
    theme: str | None = None


@router.post("/preferences", dependencies=[Depends(Authz())])
def preferences(body: PrefsIn, request: Request):
    p: Principal = request.state.principal
    fields = {}
    if body.language in ("en", "ar"):
        fields["language"] = body.language
    if body.theme in ("auto", "light", "dark"):
        fields["theme"] = body.theme
    if fields:
        accounts.update_user(p.id, **fields)
    return profile(accounts.get_user(p.id))


@router.get("/sessions", dependencies=[Depends(Authz())])
def my_sessions(request: Request):
    p: Principal = request.state.principal
    rows = accounts.list_sessions(p.id)
    for r in rows:
        r["current"] = p.session["id"].startswith(r["id"])
    return {"sessions": rows}


@router.post("/sessions/end-others", dependencies=[Depends(Authz())])
def end_other_sessions(request: Request):
    p: Principal = request.state.principal
    n = accounts.revoke_sessions(p.id, keep_id=p.session["id"])
    audit.from_request(request, "auth.end_other_sessions", target=p.username, details={"count": n})
    return {"ended": n}


def session_principal_refresh(user: dict) -> dict | None:
    """AD refresh policy applied on each request. Returns the user, or None to refuse."""
    if user["source"] != "ad":
        return user
    state = directory.access_state(user)
    if state == "ok":
        return user
    if state == "stale":
        directory.refresh_in_background(user)
        return user
    # Beyond the outage grace period: re-check synchronously before allowing anything.
    if directory.refresh_user(user) != "ok":
        return None
    return db.one("SELECT * FROM users WHERE id = ?", (user["id"],))
