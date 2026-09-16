"""Users, sessions and sign-in.

* Local users have a bcrypt password hash. Directory (AD) users never have a
  stored password: they are verified against the directory at every sign-in.
* Sessions are random server-side tokens (only a SHA-256 of the token is
  stored), so they can be listed and revoked individually.
* The **recovery account** is a local Full Administrator that cannot be
  deleted, disabled or demoted. On upgrade it is the v1 ``admin`` account.
  Reset its password on the host with ``python -m app.cli reset-password``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import config, db, rbac, security

log = logging.getLogger("accounts")

SESSION_COOKIE = "abp_session"
CSRF_COOKIE = "abp_csrf"
SESSION_TTL = 8 * 3600            # browser session sign-in
REMEMBER_TTL = 7 * 24 * 3600      # "keep me signed in"
IDLE_TIMEOUT = 2 * 3600           # sessions without "remember" expire after 2h idle
COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")

_MAX_FAILS = 5
_LOCK_SECONDS = 5 * 60
_attempts: dict[str, list[float]] = {}
_att_lock = threading.Lock()


def legacy_users_path() -> str:
    return os.environ.get("USERS_PATH") or os.path.join(
        os.path.dirname(config.CONFIG_PATH), "users.json")


# --------------------------------------------------------------------------- #
# Bootstrap / migration
# --------------------------------------------------------------------------- #
def bootstrap() -> None:
    """Seed roles, migrate v1 users.json, and guarantee a recovery account."""
    rbac.seed_builtin_roles()
    if db.one("SELECT id FROM users LIMIT 1"):
        _ensure_recovery_flag()
        return
    path = legacy_users_path()
    if os.path.exists(path):
        _migrate_legacy(path)
    else:
        create_user("admin", password="admin", display_name="Administrator",
                    must_change_password=True, is_recovery=True,
                    role_keys=["full_admin"], _allow_weak=True)
        log.info("accounts: created recovery account 'admin' (password must be changed at first sign-in)")


def _migrate_legacy(path: str) -> None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        users = data.get("users") or {}
        if not isinstance(users, dict) or not users:
            raise ValueError("no users")
    except Exception as e:  # noqa: BLE001
        # Never fall back to default credentials when an existing store is unreadable.
        raise RuntimeError(
            f"{path} exists but could not be migrated ({e}). Fix or remove it, then restart.") from e
    first = True
    for name, u in users.items():
        now = db.now()
        with db.tx():
            cur = db.execute(
                "INSERT INTO users (username, display_name, source, password_hash, must_change_password,"
                " is_recovery, language, theme, created_at, updated_at, last_login)"
                " VALUES (?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?)",
                (u.get("username") or name, "Administrator", u.get("password_hash"),
                 1 if u.get("must_change_password") else 0, 1 if first else 0,
                 u.get("language") if u.get("language") in ("en", "ar") else "en",
                 u.get("theme") if u.get("theme") in ("auto", "light", "dark") else "auto",
                 now, now, u.get("last_login")),
            )
            rid = rbac.role_id_by_key("full_admin")
            db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (cur.lastrowid, rid))
        first = False
    os.replace(path, path + ".migrated")
    log.info("accounts: migrated %d user(s) from %s (kept as users.json.migrated)", len(users), path)


def _ensure_recovery_flag() -> None:
    if db.one("SELECT id FROM users WHERE is_recovery = 1"):
        return
    fa = rbac.role_id_by_key("full_admin")
    row = db.one(
        "SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id "
        "WHERE u.source = 'local' AND ur.role_id = ? ORDER BY u.id LIMIT 1", (fa,))
    if row:
        db.execute("UPDATE users SET is_recovery = 1 WHERE id = ?", (row["id"],))


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def get_user(user_id: int) -> dict | None:
    return db.one("SELECT * FROM users WHERE id = ?", (user_id,))


def get_by_username(username: str) -> dict | None:
    return db.one("SELECT * FROM users WHERE username = ?", (username,))


def direct_role_ids(user_id: int) -> list[int]:
    return [r["role_id"] for r in db.query("SELECT role_id FROM user_roles WHERE user_id = ?", (user_id,))]


def public_user(u: dict, *, with_effective: bool = False) -> dict:
    out = {
        "id": u["id"], "username": u["username"], "display_name": u["display_name"],
        "email": u["email"], "source": u["source"], "disabled": bool(u["disabled"]),
        "must_change_password": bool(u["must_change_password"]), "is_recovery": bool(u["is_recovery"]),
        "language": u["language"], "theme": u["theme"], "last_login": u["last_login"],
        "created_at": u["created_at"], "role_ids": direct_role_ids(u["id"]),
        "ad_groups": db.loads(u.get("ad_groups"), []) if u["source"] == "ad" else [],
        "ad_synced_at": u.get("ad_synced_at"), "ad_sync_error": u.get("ad_sync_error"),
    }
    if with_effective:
        eff = rbac.effective_for_user(u)
        out["permissions"] = eff.permissions
        out["permission_sources"] = eff.sources
        out["roles"] = eff.roles
    return out


def list_users() -> list[dict]:
    return [public_user(u, with_effective=True) for u in db.query("SELECT * FROM users ORDER BY username")]


def create_user(username: str, *, password: str | None = None, display_name: str = "",
                email: str = "", source: str = "local", must_change_password: bool = True,
                is_recovery: bool = False, role_ids: list[int] | None = None,
                role_keys: list[str] | None = None, _allow_weak: bool = False) -> dict:
    err = security.validate_username(username)
    if err:
        raise ValueError(err)
    if get_by_username(username):
        raise ValueError("That username is already taken.")
    pw_hash = None
    if source == "local":
        if not _allow_weak:
            err = security.validate_password(password or "", username)
            if err:
                raise ValueError(err)
        pw_hash = security.hash_password(password or "")
    ids = list(role_ids or [])
    for k in role_keys or []:
        rid = rbac.role_id_by_key(k)
        if rid:
            ids.append(rid)
    now = db.now()
    with db.tx():
        cur = db.execute(
            "INSERT INTO users (username, display_name, email, source, password_hash, must_change_password,"
            " is_recovery, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (username, (display_name or "").strip()[:120], (email or "").strip()[:200], source, pw_hash,
             1 if (must_change_password and source == "local") else 0, 1 if is_recovery else 0, now, now),
        )
        uid = cur.lastrowid
        for rid in dict.fromkeys(ids):
            if db.one("SELECT id FROM roles WHERE id = ?", (rid,)):
                db.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (uid, rid))
    return get_user(uid)


def set_roles(user_id: int, role_ids: list[int]) -> None:
    with db.tx():
        db.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        for rid in dict.fromkeys(role_ids):
            if db.one("SELECT id FROM roles WHERE id = ?", (rid,)):
                db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, rid))


def update_user(user_id: int, **fields) -> dict:
    allowed = {"display_name", "email", "disabled", "must_change_password", "language", "theme"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = ?")
            args.append(int(v) if isinstance(v, bool) else v)
    if sets:
        sets.append("updated_at = ?")
        args.append(db.now())
        db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", [*args, user_id])
    if fields.get("disabled"):
        revoke_sessions(user_id)
    return get_user(user_id)


def set_password(user_id: int, new_password: str, *, must_change: bool = False) -> None:
    u = get_user(user_id)
    err = security.validate_password(new_password, u["username"] if u else "")
    if err:
        raise ValueError(err)
    db.execute("UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
               (security.hash_password(new_password), 1 if must_change else 0, db.now(), user_id))


def delete_user(user_id: int) -> None:
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))


def full_admin_count(exclude_user_id: int | None = None) -> int:
    n = 0
    for u in db.query("SELECT * FROM users WHERE disabled = 0"):
        if u["id"] == exclude_user_id:
            continue
        if rbac.effective_for_user(u).is_full_admin:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Sign-in throttling
# --------------------------------------------------------------------------- #
def _keys(username: str, ip: str) -> list[str]:
    return [f"u:{(username or '').lower()}|{ip}", f"ip:{ip}"]


def locked_seconds(username: str, ip: str) -> int:
    now = time.time()
    with _att_lock:
        worst = 0
        for k in _keys(username, ip):
            rec = _attempts.get(k)
            if rec and rec[1] > now:
                worst = max(worst, int(rec[1] - now) + 1)
        return worst


def record_failure(username: str, ip: str) -> None:
    now = time.time()
    with _att_lock:
        for i, k in enumerate(_keys(username, ip)):
            limit = _MAX_FAILS if i == 0 else _MAX_FAILS * 4
            rec = _attempts.get(k, [0, 0])
            rec[0] += 1
            if rec[0] >= limit:
                rec[1] = now + _LOCK_SECONDS
                rec[0] = 0
            _attempts[k] = rec


def clear_failures(username: str, ip: str) -> None:
    with _att_lock:
        _attempts.pop(_keys(username, ip)[0], None)


def reset_throttle() -> None:
    with _att_lock:
        _attempts.clear()


def verify_local(username: str, password: str) -> dict | None:
    u = get_by_username(username)
    if not u or u["source"] != "local":
        security.burn_password_check(password)
        return None
    if not security.verify_password(password, u["password_hash"]):
        return None
    return u


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def create_session(user: dict, *, remember: bool, ip: str, user_agent: str) -> tuple[str, str]:
    token = security.new_token()
    csrf = security.new_token()
    now = db.now()
    ttl = REMEMBER_TTL if remember else SESSION_TTL
    db.execute(
        "INSERT INTO sessions (id, user_id, csrf, created_at, last_seen, expires_at, remember, ip, user_agent)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (security.token_id(token), user["id"], csrf, now, now, now + ttl, 1 if remember else 0,
         ip[:64], (user_agent or "")[:200]),
    )
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user["id"]))
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token, csrf


def resolve_session(token: str) -> tuple[dict, dict] | None:
    """Return (session, user) for a valid, unexpired session token."""
    if not token or len(token) > 200:
        return None
    sid = security.token_id(token)
    s = db.one("SELECT * FROM sessions WHERE id = ?", (sid,))
    if not s:
        return None
    now = db.now()
    if s["expires_at"] < now or (not s["remember"] and now - s["last_seen"] > IDLE_TIMEOUT):
        db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return None
    u = get_user(s["user_id"])
    if not u or u["disabled"]:
        db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return None
    if now - s["last_seen"] > 60:
        db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (now, sid))
    return s, u


def revoke_session(token_or_id: str, *, is_id: bool = False) -> None:
    sid = token_or_id if is_id else security.token_id(token_or_id)
    db.execute("DELETE FROM sessions WHERE id = ?", (sid,))


def revoke_sessions(user_id: int, *, keep_id: str | None = None) -> int:
    if keep_id:
        cur = db.execute("DELETE FROM sessions WHERE user_id = ? AND id <> ?", (user_id, keep_id))
    else:
        cur = db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount or 0


def list_sessions(user_id: int) -> list[dict]:
    rows = db.query("SELECT id, created_at, last_seen, expires_at, remember, ip, user_agent FROM sessions "
                    "WHERE user_id = ? ORDER BY last_seen DESC", (user_id,))
    for r in rows:
        r["id"] = r["id"][:16]   # short handle, never the token
    return rows
