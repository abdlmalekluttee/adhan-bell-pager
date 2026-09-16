"""Optional Microsoft Active Directory sign-in (LDAP over TLS).

Design
------
* Disabled by default. Local accounts, including the recovery account, always work.
* Connections use LDAPS (636) or StartTLS (389) with certificate validation.
  Plain LDAP is not offered. A CA certificate (PEM) can be pasted for internal PKI.
* A read-only **bind account** finds the user, checks the account is enabled and
  reads group membership (nested groups are expanded). The user's own password
  is then verified by binding as that user. Domain passwords are never stored.
* **Group mappings** turn AD groups into application roles. A user's permissions
  are the union of every mapped group they belong to, plus any roles assigned
  to them directly in the app.
* **Unmapped users** (no mapped group) cannot sign in.
* **Disabled / deleted accounts** cannot sign in; existing sessions are revoked at
  the next refresh.
* **Refresh policy:** membership is re-read at every sign-in and at least every
  ``refresh_minutes`` (default 15) while a session is in use. Removing someone
  from a group therefore takes effect within that interval.
* **Directory outage:** new AD sign-ins fail with a clear message. Signed-in AD
  users keep their last known permissions for ``outage_grace_minutes`` (default
  60) after the last successful check; after that their requests are refused
  until the directory is reachable again. Local accounts are unaffected.

The bind password is encrypted at rest (``security.encrypt``) and never returned
by the API or written to logs.
"""
from __future__ import annotations

import logging
import ssl
import threading
from dataclasses import dataclass, field

from . import db, rbac, security

log = logging.getLogger("directory")

SETTINGS_KEY = "directory"
DEFAULTS = {
    "enabled": False,
    "server_urls": [],                 # e.g. ["ldaps://dc1.school.local", "ldaps://dc2.school.local"]
    "security": "ldaps",               # ldaps | starttls
    "validate_cert": True,
    "ca_cert_pem": "",
    "bind_dn": "",
    "bind_password_enc": "",
    "base_dn": "",
    "user_search_base": "",            # defaults to base_dn
    "group_search_base": "",           # defaults to base_dn
    "user_filter": "(&(objectCategory=person)(objectClass=user))",
    "domain_netbios": "",              # optional, e.g. SCHOOL (for SCHOOL\\user)
    "timeout_seconds": 5,
    "refresh_minutes": 15,
    "outage_grace_minutes": 60,
    "nested_groups": True,
}

_connection_factory = None            # tests inject an ldap3 MOCK_SYNC factory
_refresh_lock = threading.Lock()
_refreshing: set[int] = set()


class DirectoryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class AuthResult:
    ok: bool
    code: str = ""
    message: str = ""
    dn: str = ""
    username: str = ""
    display_name: str = ""
    email: str = ""
    groups: list = field(default_factory=list)      # [{"dn","name"}]
    role_ids: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def get_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(db.get_setting(SETTINGS_KEY, {}) or {})
    return cfg


def public_config() -> dict:
    cfg = get_config()
    enc = cfg.pop("bind_password_enc", "")
    cfg["has_bind_password"] = bool(enc)
    return cfg


def save_config(data: dict) -> dict:
    cfg = get_config()
    for key in DEFAULTS:
        if key == "bind_password_enc" or key not in data:
            continue
        cfg[key] = data[key]
    if data.get("clear_bind_password"):
        cfg["bind_password_enc"] = ""
    elif data.get("bind_password"):
        cfg["bind_password_enc"] = security.encrypt(str(data["bind_password"]))
    cfg = _validate(cfg)
    db.set_setting(SETTINGS_KEY, cfg)
    return public_config()


def _validate(cfg: dict) -> dict:
    urls = cfg.get("server_urls") or []
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.replace(",", "\n").splitlines()]
    urls = [u.strip() for u in urls if u and u.strip()]
    sec = cfg.get("security")
    if sec not in ("ldaps", "starttls"):
        raise ValueError("Connection security must be LDAPS or StartTLS.")
    for u in urls:
        lu = u.lower()
        if sec == "ldaps" and not lu.startswith("ldaps://"):
            raise ValueError(f"'{u}' must start with ldaps:// when LDAPS is selected.")
        if sec == "starttls" and not lu.startswith("ldap://"):
            raise ValueError(f"'{u}' must start with ldap:// when StartTLS is selected.")
    cfg["server_urls"] = urls[:6]
    for k in ("bind_dn", "base_dn", "user_search_base", "group_search_base", "user_filter",
              "domain_netbios", "ca_cert_pem"):
        cfg[k] = str(cfg.get(k) or "").strip()
    uf = cfg["user_filter"] or DEFAULTS["user_filter"]
    if not (uf.startswith("(") and uf.endswith(")")):
        raise ValueError("User filter must be an LDAP filter in parentheses.")
    cfg["user_filter"] = uf
    if cfg["ca_cert_pem"] and "BEGIN CERTIFICATE" not in cfg["ca_cert_pem"]:
        raise ValueError("CA certificate must be PEM text (-----BEGIN CERTIFICATE-----).")
    cfg["validate_cert"] = bool(cfg.get("validate_cert", True))
    cfg["nested_groups"] = bool(cfg.get("nested_groups", True))
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["timeout_seconds"] = max(2, min(30, int(cfg.get("timeout_seconds") or 5)))
    cfg["refresh_minutes"] = max(5, min(240, int(cfg.get("refresh_minutes") or 15)))
    cfg["outage_grace_minutes"] = max(0, min(1440, int(cfg.get("outage_grace_minutes") or 0)))
    if cfg["enabled"]:
        missing = [label for key, label in (("server_urls", "server address"), ("bind_dn", "bind account"),
                                            ("base_dn", "base DN")) if not cfg[key]]
        if not cfg.get("bind_password_enc"):
            missing.append("bind password")
        if missing:
            raise ValueError("To enable directory sign-in, fill in: " + ", ".join(missing) + ".")
    return cfg


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
def set_connection_factory(fn) -> None:
    """Testing hook: ``fn(cfg, user_dn, password) -> ldap3.Connection`` (already bound)."""
    global _connection_factory
    _connection_factory = fn


def _connect(cfg: dict, user: str, password: str):
    if not password:
        # An empty password makes LDAP perform an *unauthenticated* bind that succeeds.
        raise DirectoryError("invalid_credentials", "Invalid username or password.")
    if _connection_factory is not None:
        return _connection_factory(cfg, user, password)
    import ldap3
    from ldap3.core import exceptions as lex

    tls = ldap3.Tls(
        validate=ssl.CERT_REQUIRED if cfg["validate_cert"] else ssl.CERT_NONE,
        version=ssl.PROTOCOL_TLS_CLIENT,
        ca_certs_data=cfg["ca_cert_pem"] or None,
    )
    servers = []
    for url in cfg["server_urls"]:
        servers.append(ldap3.Server(url, use_ssl=cfg["security"] == "ldaps", tls=tls,
                                    connect_timeout=cfg["timeout_seconds"], get_info=ldap3.NONE))
    if not servers:
        raise DirectoryError("not_configured", "No directory server is configured.")
    pool = ldap3.ServerPool(servers, ldap3.FIRST, active=1, exhaust=60)
    auto_bind = ldap3.AUTO_BIND_TLS_BEFORE_BIND if cfg["security"] == "starttls" else ldap3.AUTO_BIND_NO_TLS
    try:
        return ldap3.Connection(pool, user=user, password=password, auto_bind=auto_bind,
                                receive_timeout=cfg["timeout_seconds"], raise_exceptions=False,
                                read_only=True)
    except lex.LDAPBindError as e:
        raise DirectoryError("invalid_credentials", _bind_message(str(e))) from e
    except (lex.LDAPSocketOpenError, lex.LDAPServerPoolExhaustedError, lex.LDAPSocketReceiveError,
            lex.LDAPSessionTerminatedByServerError) as e:
        msg = str(e)
        if "certificate" in msg.lower() or "ssl" in msg.lower():
            raise DirectoryError("tls_error", "Could not establish a trusted TLS connection to the "
                                 "directory. Check the server name and CA certificate.") from e
        raise DirectoryError("unavailable", "The directory server could not be reached.") from e
    except lex.LDAPStartTLSError as e:
        raise DirectoryError("tls_error", "StartTLS failed. Check the CA certificate and server name.") from e
    except lex.LDAPException as e:
        raise DirectoryError("unavailable", f"Directory error: {type(e).__name__}") from e


_AD_CODES = {
    "525": "user not found", "52e": "invalid credentials", "530": "sign-in not permitted at this time",
    "531": "sign-in not permitted from this workstation", "532": "password expired",
    "533": "account disabled", "701": "account expired", "773": "password must be reset",
    "775": "account locked out",
}


def _bind_message(text: str) -> str:
    low = text.lower()
    for code, meaning in _AD_CODES.items():
        if f"data {code}" in low:
            return meaning
    return "invalid credentials"


def _service(cfg: dict):
    return _connect(cfg, cfg["bind_dn"], security.decrypt(cfg.get("bind_password_enc", "")))


def _escape(value: str) -> str:
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(value)


def _normalize_username(cfg: dict, username: str) -> tuple[str, str]:
    """Return (sAMAccountName, userPrincipalName-or-empty) from user / DOMAIN\\user / user@domain."""
    u = (username or "").strip()
    if "\\" in u:
        u = u.split("\\", 1)[1]
    upn = ""
    if "@" in u:
        upn = u
        u = u.split("@", 1)[0]
    return u, upn


def _attr(entry, name: str, default=""):
    try:
        v = entry["attributes"].get(name)
    except (TypeError, AttributeError):
        return default
    if isinstance(v, list):
        return v[0] if v else default
    return v if v not in (None, "") else default


def _attr_list(entry, name: str) -> list:
    v = entry["attributes"].get(name) if isinstance(entry, dict) else None
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _search(conn, base: str, flt: str, attrs: list[str], limit: int = 0) -> list[dict]:
    import ldap3
    ok = conn.search(base, flt, search_scope=ldap3.SUBTREE, attributes=attrs, size_limit=limit)
    if not ok and conn.result and conn.result.get("result") not in (0, 4, 32):
        raise DirectoryError("search_failed", f"Directory search failed: {conn.result.get('description')}")
    return [e for e in (conn.response or []) if e.get("type") == "searchResEntry"]


_USER_ATTRS = ["distinguishedName", "sAMAccountName", "userPrincipalName", "displayName", "mail",
               "userAccountControl", "memberOf", "cn"]


def _find_user(conn, cfg: dict, username: str) -> dict | None:
    sam, upn = _normalize_username(cfg, username)
    if not sam:
        return None
    ors = f"(sAMAccountName={_escape(sam)})"
    if upn:
        ors = f"(|{ors}(userPrincipalName={_escape(upn)}))"
    flt = f"(&{cfg['user_filter']}{ors})"
    entries = _search(conn, cfg["user_search_base"] or cfg["base_dn"], flt, _USER_ATTRS, limit=2)
    if len(entries) != 1:
        return None
    return entries[0]


def _find_user_by_dn(conn, cfg: dict, dn: str) -> dict | None:
    flt = f"(&{cfg['user_filter']}(distinguishedName={_escape(dn)}))"
    entries = _search(conn, cfg["user_search_base"] or cfg["base_dn"], flt, _USER_ATTRS, limit=2)
    return entries[0] if len(entries) == 1 else None


def _is_disabled(entry) -> bool:
    try:
        return bool(int(_attr(entry, "userAccountControl", 0) or 0) & 0x2)
    except (TypeError, ValueError):
        return False


def _cn(dn: str) -> str:
    first = dn.split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else dn


def _groups(conn, cfg: dict, entry) -> list[dict]:
    """Direct groups from memberOf, expanded through nested groups (bounded)."""
    seen: dict[str, str] = {}
    frontier = [g for g in _attr_list(entry, "memberOf")]
    depth = 0
    while frontier and depth < 10 and len(seen) < 1000:
        nxt = []
        for gdn in frontier:
            key = gdn.lower()
            if key in seen:
                continue
            seen[key] = gdn
            if cfg.get("nested_groups"):
                res = _search(conn, cfg["group_search_base"] or cfg["base_dn"],
                              f"(&(objectClass=group)(distinguishedName={_escape(gdn)}))",
                              ["memberOf", "cn"], limit=1)
                for e in res:
                    nxt.extend(_attr_list(e, "memberOf"))
        frontier = nxt
        depth += 1
    return [{"dn": dn, "name": _cn(dn)} for dn in seen.values()]


def mapped_role_ids(groups: list[dict]) -> list[int]:
    dns = {g["dn"].lower() for g in groups}
    ids = []
    for m in db.query("SELECT group_dn, role_id FROM ad_group_mappings"):
        if m["group_dn"].lower() in dns and m["role_id"] not in ids:
            ids.append(m["role_id"])
    return ids


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    return bool(get_config().get("enabled"))


def authenticate(username: str, password: str) -> AuthResult:
    cfg = get_config()
    if not cfg["enabled"]:
        return AuthResult(False, "disabled", "Directory sign-in is not enabled.")
    if not password:
        return AuthResult(False, "invalid_credentials", "Invalid username or password.")
    try:
        svc = _service(cfg)
    except DirectoryError as e:
        log.error("directory: service bind failed (%s): %s", e.code, e.message)
        return AuthResult(False, "unavailable", "The directory is unavailable. Try again later or "
                          "use a local account.")
    try:
        entry = _find_user(svc, cfg, username)
        if not entry:
            return AuthResult(False, "invalid_credentials", "Invalid username or password.")
        dn = _attr(entry, "distinguishedName") or entry.get("dn", "")
        try:
            uconn = _connect(cfg, dn, password)
            try:
                uconn.unbind()
            except Exception:  # noqa: BLE001
                pass
        except DirectoryError as e:
            if e.code == "invalid_credentials":
                if e.message in ("account disabled", "account locked out", "account expired",
                                 "password expired", "password must be reset"):
                    return AuthResult(False, "account_blocked", f"Your directory account cannot sign in: "
                                      f"{e.message}.")
                return AuthResult(False, "invalid_credentials", "Invalid username or password.")
            return AuthResult(False, "unavailable", "The directory is unavailable. Try again later.")
        if _is_disabled(entry):
            return AuthResult(False, "account_blocked", "Your directory account is disabled.")
        groups = _groups(svc, cfg, entry)
        roles = mapped_role_ids(groups)
        res = AuthResult(
            True, dn=dn, username=str(_attr(entry, "sAMAccountName") or username).lower(),
            display_name=str(_attr(entry, "displayName") or _attr(entry, "cn") or ""),
            email=str(_attr(entry, "mail") or ""), groups=groups, role_ids=roles)
        if not roles:
            res.ok = False
            res.code = "not_authorized"
            res.message = ("Your account is valid but is not a member of any group that has access to "
                           "this application. Ask an administrator.")
        return res
    finally:
        try:
            svc.unbind()
        except Exception:  # noqa: BLE001
            pass


def upsert_ad_user(res: AuthResult) -> dict:
    from . import accounts
    now = db.now()
    existing = accounts.get_by_username(res.username)
    if existing and existing["source"] != "ad":
        raise DirectoryError("conflict", "A local account with this username already exists.")
    if existing:
        db.execute(
            "UPDATE users SET display_name = ?, email = ?, ad_dn = ?, ad_groups = ?, ad_role_ids = ?,"
            " ad_synced_at = ?, ad_sync_error = NULL, updated_at = ? WHERE id = ?",
            (res.display_name, res.email, res.dn, db.dumps([g["dn"] for g in res.groups]),
             db.dumps(res.role_ids), now, now, existing["id"]))
        return accounts.get_user(existing["id"])
    cur = db.execute(
        "INSERT INTO users (username, display_name, email, source, ad_dn, ad_groups, ad_role_ids,"
        " ad_synced_at, created_at, updated_at) VALUES (?, ?, ?, 'ad', ?, ?, ?, ?, ?, ?)",
        (res.username, res.display_name, res.email, res.dn, db.dumps([g["dn"] for g in res.groups]),
         db.dumps(res.role_ids), now, now, now))
    return accounts.get_user(cur.lastrowid)


def refresh_user(user: dict) -> str:
    """Re-read an AD user's status and groups. Returns ok | revoked | unavailable."""
    from . import accounts, audit
    cfg = get_config()
    if not cfg["enabled"]:
        db.execute("UPDATE users SET ad_sync_error = ? WHERE id = ?",
                   ("Directory sign-in is disabled", user["id"]))
        accounts.revoke_sessions(user["id"])
        return "revoked"
    try:
        svc = _service(cfg)
    except DirectoryError as e:
        db.execute("UPDATE users SET ad_sync_error = ? WHERE id = ?", (e.message, user["id"]))
        log.warning("directory: refresh of %s deferred, directory unavailable (%s)", user["username"], e.code)
        return "unavailable"
    try:
        entry = _find_user_by_dn(svc, cfg, user.get("ad_dn") or "") or _find_user(svc, cfg, user["username"])
        reason = ""
        if not entry:
            reason = "account no longer found in the directory"
        elif _is_disabled(entry):
            reason = "account disabled in the directory"
        groups = _groups(svc, cfg, entry) if entry else []
        roles = mapped_role_ids(groups) if entry else []
        if entry and not roles:
            reason = "no longer a member of any mapped group"
        now = db.now()
        db.execute("UPDATE users SET ad_groups = ?, ad_role_ids = ?, ad_synced_at = ?, ad_sync_error = ?,"
                   " updated_at = ? WHERE id = ?",
                   (db.dumps([g["dn"] for g in groups]), db.dumps(roles), now, reason or None, now, user["id"]))
        if reason:
            n = accounts.revoke_sessions(user["id"])
            audit.record("directory.access_revoked", target=user["username"], result="ok",
                         details={"reason": reason, "sessions_revoked": n})
            return "revoked"
        return "ok"
    except DirectoryError as e:
        db.execute("UPDATE users SET ad_sync_error = ? WHERE id = ?", (e.message, user["id"]))
        return "unavailable"
    finally:
        try:
            svc.unbind()
        except Exception:  # noqa: BLE001
            pass


def access_state(user: dict) -> str:
    """For an AD user in a live session: ok | stale (refresh due) | expired (outage grace over)."""
    cfg = get_config()
    synced = user.get("ad_synced_at") or 0
    age = db.now() - synced
    if age <= cfg["refresh_minutes"] * 60:
        return "ok"
    if age <= (cfg["refresh_minutes"] + cfg["outage_grace_minutes"]) * 60:
        return "stale"
    return "expired"


def refresh_in_background(user: dict) -> None:
    with _refresh_lock:
        if user["id"] in _refreshing:
            return
        _refreshing.add(user["id"])

    def run():
        try:
            refresh_user(user)
        except Exception:  # noqa: BLE001
            log.exception("directory: background refresh failed")
        finally:
            with _refresh_lock:
                _refreshing.discard(user["id"])

    threading.Thread(target=run, daemon=True, name="ad-refresh").start()


def test_connection(overrides: dict | None = None) -> dict:
    cfg = get_config()
    if overrides:
        tmp = dict(cfg)
        for k in DEFAULTS:
            if k in overrides and k != "bind_password_enc":
                tmp[k] = overrides[k]
        if overrides.get("bind_password"):
            tmp["bind_password_enc"] = security.encrypt(str(overrides["bind_password"]))
        tmp["enabled"] = False
        cfg = _validate(tmp)
    try:
        svc = _service(cfg)
    except DirectoryError as e:
        return {"ok": False, "code": e.code, "message": e.message}
    try:
        base = _search(svc, cfg["base_dn"], "(objectClass=*)", ["distinguishedName"], limit=1)
        return {"ok": True, "message": "Connected and signed in with the bind account.",
                "base_dn_found": bool(base), "server": str(getattr(svc, "server", "") or "")}
    except DirectoryError as e:
        return {"ok": False, "code": e.code, "message": e.message}
    finally:
        try:
            svc.unbind()
        except Exception:  # noqa: BLE001
            pass


def lookup_user(username: str, password: str = "") -> dict:
    """Admin diagnostic: what would happen if this user signed in?"""
    cfg = get_config()
    try:
        svc = _service(cfg)
    except DirectoryError as e:
        return {"ok": False, "code": e.code, "message": e.message}
    try:
        entry = _find_user(svc, cfg, username)
        if not entry:
            return {"ok": False, "code": "not_found", "message": "No matching user was found."}
        groups = _groups(svc, cfg, entry)
        roles = mapped_role_ids(groups)
        role_names = [r["name"] for r in rbac.list_roles() if r["id"] in roles]
        out = {"ok": True, "dn": _attr(entry, "distinguishedName"),
               "username": _attr(entry, "sAMAccountName"), "display_name": _attr(entry, "displayName"),
               "email": _attr(entry, "mail"), "disabled": _is_disabled(entry), "groups": groups,
               "mapped_role_ids": roles, "mapped_roles": role_names,
               "would_sign_in": bool(roles) and not _is_disabled(entry)}
        if password:
            try:
                c = _connect(cfg, out["dn"], password)
                c.unbind()
                out["password_ok"] = True
            except DirectoryError as e:
                out["password_ok"] = False
                out["password_message"] = e.message
        return out
    except DirectoryError as e:
        return {"ok": False, "code": e.code, "message": e.message}
    finally:
        try:
            svc.unbind()
        except Exception:  # noqa: BLE001
            pass


def search_groups(q: str) -> dict:
    cfg = get_config()
    q = (q or "").strip()
    if len(q) < 2:
        return {"ok": True, "groups": []}
    try:
        svc = _service(cfg)
    except DirectoryError as e:
        return {"ok": False, "code": e.code, "message": e.message, "groups": []}
    try:
        term = _escape(q)
        flt = f"(&(objectClass=group)(|(cn=*{term}*)(sAMAccountName=*{term}*)))"
        entries = _search(svc, cfg["group_search_base"] or cfg["base_dn"], flt,
                          ["distinguishedName", "cn", "description"], limit=25)
        return {"ok": True, "groups": [
            {"dn": _attr(e, "distinguishedName") or e.get("dn"), "name": _attr(e, "cn"),
             "description": _attr(e, "description")} for e in entries]}
    except DirectoryError as e:
        return {"ok": False, "code": e.code, "message": e.message, "groups": []}
    finally:
        try:
            svc.unbind()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Group mappings
# --------------------------------------------------------------------------- #
def list_mappings() -> list[dict]:
    return db.query(
        "SELECT m.id, m.group_dn, m.group_name, m.role_id, r.name AS role_name, m.created_at "
        "FROM ad_group_mappings m JOIN roles r ON r.id = m.role_id ORDER BY m.group_name, r.name")


def add_mapping(group_dn: str, group_name: str, role_id: int) -> dict:
    group_dn = (group_dn or "").strip()
    if "=" not in group_dn or len(group_dn) > 500:
        raise ValueError("Enter the group's distinguished name, e.g. CN=Bell Admins,OU=Groups,DC=school,DC=local.")
    if not db.one("SELECT id FROM roles WHERE id = ?", (role_id,)):
        raise ValueError("Role not found.")
    try:
        cur = db.execute("INSERT INTO ad_group_mappings (group_dn, group_name, role_id, created_at) "
                         "VALUES (?, ?, ?, ?)", (group_dn, (group_name or _cn(group_dn))[:120], role_id, db.now()))
    except Exception as e:  # noqa: BLE001
        if "UNIQUE" in str(e):
            raise ValueError("This group is already mapped to that role.") from e
        raise
    return db.one("SELECT * FROM ad_group_mappings WHERE id = ?", (cur.lastrowid,))


def delete_mapping(mapping_id: int) -> dict | None:
    m = db.one("SELECT * FROM ad_group_mappings WHERE id = ?", (mapping_id,))
    if m:
        db.execute("DELETE FROM ad_group_mappings WHERE id = ?", (mapping_id,))
    return m
